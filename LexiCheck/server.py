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
                result = client.call("consult/getArticleByCid", {"cid": cid})
                for x in result.get("listArticle", []) or []:
                    if isinstance(x, dict):
                        versions.append(x)
                        versions.extend(v for v in (x.get("articleVersions") or []) if isinstance(v, dict))
            except APIError as exc:
                res["notesVersions"] = exc.message
        versions.extend(x for x in (target.get("articleVersions") or []) if isinstance(x, dict))
        current = [x for x in versions if active_on(x, today) and normalize_state(x).startswith("VIGUEUR")]
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


def analyze(client: PisteClient, value: str, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    source_id = parse_article_id(value)
    original = client.article(source_id)
    warnings = []
    try:
        links = client.call("consult/relatedLinksArticle", {"articleId": source_id})
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
    if not any("texte" in e["sources"] for e in entries):
        warnings.append("Aucun hyperlien LEGI/JORF n'a été trouvé dans le HTML fourni : les citations indexées ne garantissent pas des renvois présents dans la rédaction.")
    return {"article": article_summary(original), "sourceTexte": clean_text(str(original.get("texteHtml") or original.get("texte") or ""))[:14000],
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
            result = analyze(CLIENT, body["article"])
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
