#!/usr/bin/env python3
"""Serveur local et proxy PISTE pour auditer les renvois d'un article LEGI.
Dépendances : bibliothèque standard Python 3.10+ uniquement.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import html
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
ARTICLE_RE = re.compile(r"LEGIARTI\d{12}", re.IGNORECASE)
OTHER_RE = re.compile(r"(?:JORFARTI|KALIARTI)\d{12}", re.IGNORECASE)
MAX_WORKERS = 4


def load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


load_dotenv()
MAX_REFERENCES = max(1, min(int(os.environ.get("MAX_REFERENCES", "100")), 500))


class APIError(Exception):
    def __init__(self, message: str, status: int = 502):
        self.message = message
        self.status = status
        super().__init__(message)


class PisteClient:
    def __init__(self):
        mode = os.getenv("PISTE_ENV", "production").lower()
        if mode not in {"production", "sandbox"}:
            raise APIError("PISTE_ENV doit être 'production' ou 'sandbox'.", 500)
        prefix = "sandbox-" if mode == "sandbox" else ""
        self.token_url = f"https://{prefix}oauth.piste.gouv.fr/api/oauth/token"
        self.base = f"https://{prefix}api.piste.gouv.fr/dila/legifrance/lf-engine-app"
        self.client_id = os.getenv("PISTE_CLIENT_ID", "")
        self.client_secret = os.getenv("PISTE_CLIENT_SECRET", "")
        self._token = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()
        self._article_cache: dict[str, dict] = {}
        self._cache_lock = threading.Lock()

    def token(self, force: bool = False) -> str:
        if not self.client_id or not self.client_secret:
            raise APIError("Identifiants PISTE absents. Renseignez PISTE_CLIENT_ID et PISTE_CLIENT_SECRET dans .env.", 503)
        with self._lock:
            if not force and self._token and time.monotonic() < self._expires_at:
                return self._token
            payload = urlencode({
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "openid",
            }).encode("utf-8")
            req = Request(self.token_url, data=payload, headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            }, method="POST")
            try:
                with urlopen(req, timeout=20) as resp:
                    data = json.load(resp)
            except HTTPError as exc:
                raise APIError(f"Authentification PISTE refusée (HTTP {exc.code}). Vérifiez les clés, l'environnement et les CGU.", 502) from exc
            except (URLError, TimeoutError, ValueError) as exc:
                raise APIError(f"Connexion au serveur d'authentification impossible : {exc}", 502) from exc
            self._token = data.get("access_token", "")
            if not self._token:
                raise APIError("Réponse OAuth sans access_token.", 502)
            self._expires_at = time.monotonic() + max(30, int(data.get("expires_in", 3600)) - 75)
            return self._token

    def call(self, endpoint: str, body: dict) -> dict:
        # Endpoints figés : aucune URL fournie par l'utilisateur ne sera appelée.
        if endpoint not in {
            "consult/getArticle", "consult/relatedLinksArticle",
            "consult/getArticleByCid", "consult/concordanceLinksArticle",
        }:
            raise APIError("Endpoint refusé.", 500)
        token = self.token()
        for attempt in range(2):
            req = Request(f"{self.base}/{endpoint}", data=json.dumps(body).encode("utf-8"), headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }, method="POST")
            try:
                with urlopen(req, timeout=24) as resp:
                    return json.load(resp)
            except HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    token = self.token(force=True)
                    continue
                if exc.code == 403:
                    raise APIError("Accès API refusé (403). Vérifiez l'abonnement Légifrance et l'acceptation des CGU sur PISTE.", 502) from exc
                if exc.code == 429:
                    raise APIError("Quota d'appels PISTE atteint (429). Réessayez ultérieurement.", 429) from exc
                raise APIError(f"PISTE a renvoyé HTTP {exc.code} pour {endpoint}.", 502) from exc
            except (URLError, TimeoutError, ValueError) as exc:
                raise APIError(f"Appel {endpoint} impossible : {exc}", 502) from exc
        raise APIError("Échec d'authentification.", 502)

    def versions(self, cid: str) -> list[dict]:
        payload = self.call("consult/getArticleByCid", {"cid": cid})
        return flatten_versions(payload)

    def article(self, article_id: str) -> dict:
        with self._cache_lock:
            cached = self._article_cache.get(article_id)
        if cached is not None:
            return cached
        res = self.call("consult/getArticle", {"id": article_id})
        art = res.get("article")
        if not isinstance(art, dict) or not art.get("id"):
            raise APIError(f"Article {article_id} absent de la réponse PISTE.", 502)
        with self._cache_lock:
            if len(self._article_cache) > 300:
                self._article_cache.clear()
            self._article_cache[article_id] = art
        return art


class AnchorCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.href: str | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() == "a":
            self.href = dict(attrs).get("href", "")
            self.parts = []

    def handle_data(self, data: str):
        if self.href is not None:
            self.parts.append(data)

    def handle_endtag(self, tag: str):
        if tag.lower() == "a" and self.href is not None:
            self.links.append((self.href, " ".join(" ".join(self.parts).split())))
            self.href = None
            self.parts = []


class TextCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str):
        self.parts.append(data)


def clean_text(value: str) -> str:
    parser = TextCollector()
    parser.feed(value or "")
    return " ".join(html.unescape(" ".join(parser.parts)).split())


def parse_article_id(value: str) -> str:
    m = ARTICLE_RE.search(value or "")
    if not m:
        raise APIError("Collez une URL d'article ou un identifiant LEGIARTI (ex. LEGIARTI000006307920).", 400)
    return m.group(0).upper()


def extract_references(article: dict, links: dict) -> tuple[list[dict], list[str]]:
    indexed: dict[str, dict] = {}
    for e in links.get("liensCite", []) or []:
        if not isinstance(e, dict):
            continue
        ident = str(e.get("id", "")).upper()
        if not (ARTICLE_RE.fullmatch(ident) or OTHER_RE.fullmatch(ident)):
            continue
        indexed[ident] = {
            "id": ident, "titre": str(e.get("name") or ident),
            "sources": ["indexation"], "evidence": "Lien répertorié par Légifrance",
            "nature": str(e.get("nature") or ""),
        }
    source_html = str(article.get("texteHtml") or article.get("texte") or "")
    parser = AnchorCollector()
    parser.feed(source_html)
    for href, label in parser.links:
        found = ARTICLE_RE.search(href) or OTHER_RE.search(href)
        if not found:
            continue
        ident = found.group(0).upper()
        entry = indexed.setdefault(ident, {
            "id": ident, "titre": label or ident,
            "sources": [], "evidence": "", "nature": "",
        })
        if "texte" not in entry["sources"]:
            entry["sources"].insert(0, "texte")
        entry["evidence"] = label or "Lien hypertexte dans le corps de l'article"
        if not entry.get("titre") or entry["titre"] == ident:
            entry["titre"] = label or ident
        if "/codes/" in href and not entry.get("nature"):
            entry["nature"] = "CODE"
        elif "/loda/" in href and not entry.get("nature"):
            entry["nature"] = "LODA"

    # Mentions textuelles non appariées à un identifiant : remontées à part, jamais déclarées vérifiées.
    plain = clean_text(source_html)
    mentions = re.findall(
        r"\barticles?\s+(?:L\.?|R\.?|D\.?|LO\.?|[A-Z]{1,3}\.?)\s*\d{1,5}(?:-\d{1,5}){0,4}",
        plain, flags=re.IGNORECASE,
    )
    unresolved = []
    anchor_labels = " ".join(label for _, label in parser.links).lower().replace(" ", "")
    for term in dict.fromkeys(mentions):
        normalized = term.lower().replace(" ", "")
        if normalized not in anchor_labels:
            unresolved.append(term)
    # L'ID source n'est jamais considéré comme un renvoi sortant.
    indexed.pop(str(article.get("id") or "").upper(), None)
    return list(indexed.values()), unresolved[:80]


def parsed_date(value) -> dt.date | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, int):
            return dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc).date()
        return dt.date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError, OverflowError):
        return None


def active_on(version: dict, day: dt.date) -> bool:
    begin, end = parsed_date(version.get("dateDebut")), parsed_date(version.get("dateFin"))
    if not begin and not end:
        return False
    return (begin is None or begin <= day) and (end is None or day < end)


def normalize_state(article: dict) -> str:
    return str(article.get("etat") or "").upper()


def source_title(article: dict) -> str:
    ctx = article.get("context") or {}
    titles = article.get("textTitles") or []
    if titles and isinstance(titles, list):
        first = titles[0] or {}
        prefix = first.get("titre") or first.get("titreLong")
    else:
        prefix = ""
    num = article.get("num") or ""
    return (f"{prefix} — article {num}" if prefix else f"Article {num}").strip()


def article_summary(a: dict) -> dict:
    return {"id": a.get("id"), "numero": a.get("num"), "etat": a.get("etat"),
            "dateDebut": a.get("dateDebut"), "dateFin": a.get("dateFin"),
            "cid": a.get("cid"), "titre": source_title(a)}




def flatten_versions(payload: dict) -> list[dict]:
    """Accepte aussi bien listArticle (articles complets) que articleVersions (résumés)."""
    versions: dict[str, dict] = {}
    for article in payload.get("listArticle", []) or []:
        if not isinstance(article, dict):
            continue
        candidates = [article] + list(article.get("articleVersions") or [])
        for item in candidates:
            if not isinstance(item, dict):
                continue
            ident = str(item.get("id") or "").upper()
            if not ARTICLE_RE.fullmatch(ident):
                continue
            # Une version complète a davantage de métadonnées qu'un résumé.
            if ident not in versions or len(item) > len(versions[ident]):
                versions[ident] = item
    return list(versions.values())


def live_version(article: dict, day: dt.date) -> bool:
    state = normalize_state(article)
    return active_on(article, day) and (state.startswith("VIGUEUR") or state == "MODIFIE")


def choose_source_version(client: PisteClient, input_id: str, day: dt.date, mode: str) -> tuple[dict, dict, list[str]]:
    """Ne jamais retomber silencieusement sur une version ancienne en mode actuel."""
    if mode not in {"actuel", "historique"}:
        raise APIError("Mode invalide : choisissez 'actuel' ou 'historique'.", 400)
    originally = client.article(input_id)
    notes = []
    if mode == "historique":
        notes.append("Mode version fournie : les renvois de cette rédaction sont vérifiés par rapport à la date d'analyse, pas à leur date de rédaction.")
        return originally, originally, notes
    if live_version(originally, day):
        return originally, originally, notes
    cid = str(originally.get("cid") or input_id).upper()
    if not ARTICLE_RE.fullmatch(cid):
        raise APIError("Impossible de retrouver l'identifiant chronologique (CID) de cet article. Essayez le mode « Version fournie ». ", 422)
    try:
        versions = client.versions(cid) if callable(getattr(client, "versions", None)) else flatten_versions(client.call("consult/getArticleByCid", {"cid": cid}))
    except APIError as exc:
        raise APIError("La recherche de la version actuelle a échoué : " + exc.message, exc.status) from exc
    versions.append(originally)
    current = [v for v in versions if live_version(v, day)]
    current.sort(key=lambda v: parsed_date(v.get("dateDebut")) or dt.date.min, reverse=True)
    if not current:
        raise APIError("Aucune version en vigueur aujourd'hui n'a été identifiée par Légifrance pour cet article. Il peut avoir été abrogé ou renuméroté. Passez en mode « Version fournie » pour analyser sa rédaction historique.", 422)
    winning = current[0]
    if winning["id"] != input_id:
        winning = client.article(winning["id"])
        if not live_version(winning, day):
            raise APIError("La version courante proposée n'a pas pu être confirmée. Analyse interrompue pour éviter un faux résultat.", 422)
        notes.append(f"L'identifiant fourni ({input_id}) désigne une ancienne version. Analyse effectuée sur la version en vigueur ({winning['id']}).")
    return winning, originally, notes


class SafeArticleRenderer(HTMLParser):
    """Reconstitue le HTML juridique en conservant seulement une liste de balises sûres.

    Les références sont coloriées selon les contrôles, sans injecter le HTML brut de PISTE.
    """
    SAFE = {"p", "div", "span", "br", "ul", "ol", "li", "b", "strong", "i", "em", "u", "sup", "sub", "blockquote", "h2", "h3", "h4", "table", "thead", "tbody", "tr", "td", "th", "a"}
    VOID = {"br"}
    BLOCKED = {"script", "style", "iframe", "object", "embed", "svg", "math", "template", "form", "textarea", "button", "input"}
    UNRESOLVED = re.compile(r"\barticles?\s+(?:L\.?|R\.?|D\.?|LO\.?|[A-Z]{1,3}\.?)\s*\d{1,5}(?:-\d{1,5}){0,4}", re.IGNORECASE)

    def __init__(self, results: list[dict]):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, str | None]] = []
        self.blocked_depth = 0
        self.link_depth = 0
        self.by_id = {x["id"]: x for x in results}
        self.count_highlighted = 0
        self.mentioned_ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if self.blocked_depth:
            if tag in self.BLOCKED:
                self.blocked_depth += 1
            return
        if tag in self.BLOCKED:
            self.blocked_depth = 1
            return
        if tag not in self.SAFE:
            return
        if tag == "a":
            href = str(dict(attrs).get("href") or "")
            hit = ARTICLE_RE.search(href) or OTHER_RE.search(href)
            entry = self.by_id.get(hit.group(0).upper()) if hit else None
            # Seuls les identifiants effectivement vérifiés sont codés en couleur.
            if entry:
                status = entry.get("statut", "indetermine")
                if status not in {"vigoureux", "alerte", "ancienne_version", "indetermine"}:
                    status = "indetermine"
                uid = entry["id"]
                dest = legal_article_url(uid, entry.get("nature", ""))
                self.parts.append(f'<a class="legal-ref status-{status}" href="{html.escape(dest, quote=True)}" target="_blank" rel="noopener noreferrer" data-ref="{uid}" title="{html.escape(entry.get("raison", "Voir cette référence sur Légifrance"), quote=True)}">')
                self.mentioned_ids.add(uid)
                self.count_highlighted += 1
            else:
                # On garde la typographie, mais aucun attribut non vérifié.
                self.parts.append('<span class="reference-unchecked" title="Lien non vérifié">')
            self.stack.append(("a", "a" if entry else "span"))
            self.link_depth += 1
            return
        self.parts.append(f"<{tag}>")
        if tag not in self.VOID:
            self.stack.append((tag, tag))

    def handle_startendtag(self, tag: str, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if self.blocked_depth:
            if tag in self.BLOCKED:
                self.blocked_depth -= 1
            return
        # Ignorer une fermeture inattendue plutôt que casser la structure.
        for idx in range(len(self.stack) - 1, -1, -1):
            if self.stack[idx][0] == tag:
                for original, rendered in reversed(self.stack[idx:]):
                    self.parts.append(f"</{rendered}>")
                    if original == "a":
                        self.link_depth = max(0, self.link_depth - 1)
                del self.stack[idx:]
                return

    def handle_data(self, data: str):
        if self.blocked_depth:
            return
        if self.link_depth:
            self.parts.append(html.escape(data))
            return
        last = 0
        for m in self.UNRESOLVED.finditer(data):
            self.parts.append(html.escape(data[last:m.start()]))
            self.parts.append('<mark class="legal-ref status-nonresolu" title="Mention textuelle sans identifiant résolu : vérification manuelle nécessaire">' + html.escape(m.group(0)) + '</mark>')
            last = m.end()
        self.parts.append(html.escape(data[last:]))

    def render(self, markup: str) -> str:
        self.feed(markup)
        self.close()
        for original, rendered in reversed(self.stack):
            self.parts.append(f"</{rendered}>")
        return "".join(self.parts)


def legal_article_url(ident: str, nature: str = "") -> str:
    if ident.startswith("JORFARTI"):
        return f"https://www.legifrance.gouv.fr/jorf/id/{ident}"
    if ident.startswith("LEGIARTI"):
        group = "codes" if nature.upper() == "CODE" else "loda"
        return f"https://www.legifrance.gouv.fr/{group}/article_lc/{ident}"
    return "https://www.legifrance.gouv.fr/"


def render_annotated_html(article: dict, checked: list[dict]) -> tuple[str, int, set[str]]:
    raw = str(article.get("texteHtml") or article.get("texte") or "")
    if not raw:
        return "", 0, set()
    if "<" not in raw:
        raw = "<p>" + html.escape(raw).replace("\n", "<br>") + "</p>"
    parser = SafeArticleRenderer(checked)
    result = parser.render(raw[:300_000])
    return result, parser.count_highlighted, parser.mentioned_ids

def candidates_from_concordance(data: dict, old_id: str) -> list[dict]:
    seen: set[str] = set()
    result = []
    for entry in data.get("newTexts", []) or []:
        if not isinstance(entry, dict):
            continue
        ident = str(entry.get("id") or "").upper()
        if not ARTICLE_RE.fullmatch(ident) or ident == old_id or ident in seen:
            continue
        seen.add(ident)
        result.append({"id": ident, "titre": entry.get("name") or ident,
                       "type": ", ".join(str(x.get("type", "")) for x in entry.get("modifications", []) if isinstance(x, dict))})
    return result[:12]


def inspect_reference(client: PisteClient, entry: dict, today: dt.date) -> dict:
    res = dict(entry)
    res.update({"etat": "", "statut": "indetermine", "raison": "État non vérifié.",
                "dateFin": None, "dateDebut": None, "versionActuelle": None,
                "concordances": []})
    ident = entry["id"]
    if not ARTICLE_RE.fullmatch(ident):
        res["raison"] = "L'identifiant ne désigne pas un article LEGI : contrôle automatique indisponible."
        return res
    try:
        target = client.article(ident)
        state = normalize_state(target)
        res.update({"etat": state, "dateFin": target.get("dateFin"),
                    "dateDebut": target.get("dateDebut"), "numero": target.get("num")})
        if state.startswith("VIGUEUR") and active_on(target, today):
            res["statut"] = "vigoureux"
            res["raison"] = "Version citée en vigueur à la date de l'analyse."
            return res
        cid = target.get("cid")
        versions = []
        if cid and ARTICLE_RE.fullmatch(str(cid).upper()):
            try:
                versions = client.versions(cid) if callable(getattr(client, "versions", None)) else flatten_versions(client.call("consult/getArticleByCid", {"cid": cid}))
            except APIError as exc:
                res["notesVersions"] = exc.message
        versions.extend(x for x in (target.get("articleVersions") or []) if isinstance(x, dict))
        current = [x for x in versions if live_version(x, today)]
        # Certaines réponses sont des résumés incluant un CID mais pas le numéro d'article.
        current.sort(key=lambda x: parsed_date(x.get("dateDebut")) or dt.date.min, reverse=True)
        if current:
            winning = current[0]
            res["versionActuelle"] = {"id": winning.get("id"), "etat": winning.get("etat"),
                                       "dateDebut": winning.get("dateDebut"), "numero": winning.get("num") or winning.get("numero")}
            if winning.get("id") != ident:
                res["statut"] = "ancienne_version"
                res["raison"] = "L'identifiant cité désigne une version antérieure, mais l'article possède une version actuellement en vigueur."
            else:
                res["statut"] = "vigoureux"
                res["raison"] = "Article actuellement en vigueur, confirmé par son historique."
            return res
        end = parsed_date(target.get("dateFin"))
        if state.startswith(("ABROGE", "REMPLACE", "PERIME", "TRANSFERE")) and (end is None or end <= today):
            res["statut"] = "alerte"
            res["raison"] = "Article cité indiqué comme abrogé, remplacé ou transféré ; aucune version courante confirmée. Vérifier le renvoi et son contexte."
        elif end and end <= today and state.startswith("MODIFIE"):
            res["statut"] = "indetermine"
            res["raison"] = "Version modifiée et terminée, mais la continuité de l'article n'a pas pu être établie."
        else:
            res["statut"] = "indetermine"
            res["raison"] = "Statut non concluant : date d'entrée en vigueur future, historique incomplet ou état non reconnu."
        if res["statut"] != "vigoureux":
            try:
                concordance = client.call("consult/concordanceLinksArticle", {"articleId": ident})
                res["concordances"] = candidates_from_concordance(concordance, ident)
                if res["concordances"]:
                    res["raison"] += " Une correspondance possible est renseignée par Légifrance (non validée juridiquement)."
            except APIError as exc:
                res["notesConcordance"] = exc.message
    except APIError as exc:
        res["raison"] = exc.message
    return res


def analyze(client: PisteClient, value: str, today: dt.date | None = None, mode: str = "actuel") -> dict:
    today = today or dt.date.today()
    source_id = parse_article_id(value)
    original, supplied, warnings = choose_source_version(client, source_id, today, mode)
    selected_id = str(original["id"])
    try:
        links = client.call("consult/relatedLinksArticle", {"articleId": selected_id})
    except APIError as exc:
        links = {}
        warnings.append("Citations indexées indisponibles : " + exc.message)
    entries, unresolved = extract_references(original, links)
    total = len(entries)
    if total > MAX_REFERENCES:
        warnings.append(f"Seuls les {MAX_REFERENCES} premiers renvois identifiés ont été vérifiés (sur {total}). Modifiez MAX_REFERENCES pour augmenter cette limite.")
        entries = entries[:MAX_REFERENCES]
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        checked = list(pool.map(lambda entry: inspect_reference(client, entry, today), entries))
    checked.sort(key=lambda x: {"alerte": 0, "indetermine": 1, "ancienne_version": 2, "vigoureux": 3}.get(x["statut"], 4))
    counts = {key: sum(x["statut"] == key for x in checked) for key in ("alerte", "indetermine", "ancienne_version", "vigoureux")}
    decorated, colored, actual_ids = render_annotated_html(original, checked)
    if not any("texte" in e["sources"] for e in entries):
        warnings.append("Aucun hyperlien LEGI/JORF n'a été trouvé dans le HTML : les citations indexées ne garantissent pas que les renvois figurent dans la rédaction.")
    if any(x["id"] not in actual_ids for x in checked):
        warnings.append("Certaines références ne sont visibles que dans l'indexation Légifrance : elles ne sont pas surlignées dans le corps de l'article.")
    source_nature = "CODE" if "/codes/" in value.lower() else ("LODA" if "/loda/" in value.lower() else "CODE")
    return {"article": article_summary(original), "articleDemandee": article_summary(supplied),
            "sourceNature": source_nature, "versionChangee": selected_id != source_id, "mode": mode,
            "sourceHtmlAnnote": decorated, "renvoisSurlignes": colored,
            "dateAnalyse": today.isoformat(), "renvois": checked, "totalIdentifies": total, "comptes": counts,
            "mentionsNonResolues": unresolved, "avertissements": warnings,
            "limite": MAX_REFERENCES}


CLIENT = PisteClient()


class Handler(BaseHTTPRequestHandler):
    def _respond(self, status: int, payload: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, obj: dict):
        self._respond(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        paths = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                 "/style.css": ("style.css", "text/css; charset=utf-8")}
        if self.path == "/api/health":
            return self._json(200, {"status": "ok", "configured": bool(CLIENT.client_id and CLIENT.client_secret),
                                    "environment": os.getenv("PISTE_ENV", "production")})
        if self.path not in paths:
            return self._json(404, {"erreur": "Page inconnue."})
        filename, mime = paths[self.path]
        base = ROOT / "static"
        content = (base / filename).read_bytes()
        self._respond(200, content, mime)

    def do_POST(self):
        if self.path != "/api/analyze":
            return self._json(404, {"erreur": "Endpoint inconnu."})
        try:
            count = int(self.headers.get("Content-Length", "0"))
            if count < 2 or count > 8192:
                raise APIError("Requête vide ou trop volumineuse.", 400)
            body = json.loads(self.rfile.read(count))
            if not isinstance(body, dict) or not isinstance(body.get("article"), str):
                raise APIError("Fournir un champ article (URL ou identifiant).", 400)
            result = analyze(CLIENT, body["article"], mode=body.get("mode", "actuel"))
            self._json(200, result)
        except APIError as exc:
            self._json(exc.status, {"erreur": exc.message})
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"erreur": "JSON invalide."})
        except Exception as exc:
            print("Erreur interne:", repr(exc))
            self._json(500, {"erreur": "Erreur interne du serveur. Consultez la console Python."})


def main():
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"\nAudit des renvois Légifrance : http://{host}:{port}")
    print("PISTE :", os.getenv("PISTE_ENV", "production"), "| identifiants :", "configurés" if CLIENT.client_id and CLIENT.client_secret else "À CONFIGURER (.env)")
    print("Ctrl+C pour arrêter.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
