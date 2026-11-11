#!/usr/bin/env python3
"""Serveur local et proxy PISTE pour auditer les renvois d'un article LEGI.
Dépendances : bibliothèque standard Python 3.10+ uniquement.
"""
from __future__ import annotations

import concurrent.futures
import difflib
import uuid
import unicodedata
import datetime as dt
import html
import hmac
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

APP_VERSION = "3.7.0"
ROOT = Path(__file__).resolve().parent
ARTICLE_RE = re.compile(r"LEGIARTI\d{12}", re.IGNORECASE)
OTHER_RE = re.compile(r"(?:JORFARTI|KALIARTI)\d{12}", re.IGNORECASE)
MAX_WORKERS = 3
TEXT_RE = re.compile(r"LEGITEXT\d{12}", re.I)
SECTION_RE = re.compile(r"LEGISCTA\d{12}", re.I)
JOB_BATCH_MAX = max(5, min(int(os.environ.get("JOB_BATCH_MAX", "40")), 100))
BULK_REF_LIMIT = max(1, min(int(os.environ.get("BULK_REF_LIMIT", "25")), 100))
AUTO_RESOLVE_LIMIT = max(0, min(int(os.environ.get("AUTO_RESOLVE_LIMIT", "20")), 60))
AUTO_RESOLVE_BULK_LIMIT = max(0, min(int(os.environ.get("AUTO_RESOLVE_BULK_LIMIT", "6")), 20))
CONCORDANCE_MAX_CALLS = max(1, min(int(os.environ.get("CONCORDANCE_MAX_CALLS", "9")), 20))
CONCORDANCE_MAX_HOPS = max(1, min(int(os.environ.get("CONCORDANCE_MAX_HOPS", "3")), 4))
CONCORDANCE_VERSION_PROBES = max(1, min(int(os.environ.get("CONCORDANCE_VERSION_PROBES", "5")), 10))


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
        self._lookup_cache: dict[tuple[str, str, str], list[str]] = {}

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
            "consult/legi/tableMatieres", "consult/code", "search",
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


    def search_article_number(self, number: str, code_name: str, day: dt.date) -> list[dict]:
        """Retrouve le numéro dans un code, y compris ses versions abrogées.

        IMPORTANT : les versions 'MODIFIE' peuvent être mort-nées (début > fin).
        Une recherche CODE_ETAT ne garantit pas de retourner la version terminale.
        On vérifie les articles et on cherche une version historique réellement
        applicable avant de conclure. Les résultats ambigus restent ambigus.
        """
        number = normalize_article_number(number)
        if not number or not code_name:
            return []
        cache_key = (number, code_name.casefold(), day.isoformat())
        with self._cache_lock:
            cached_ids = self._lookup_cache.get(cache_key)

        def verified(ident: str) -> dict | None:
            try:
                obj = self.article(ident)
            except APIError:
                return None
            if normalize_article_number(str(obj.get("num") or "")) != number:
                return None
            titles = article_code_titles(obj)
            if titles and not any(normalize_code_title(code_name) == normalize_code_title(t) for t in titles):
                return None
            return obj

        if cached_ids is None:
            found: dict[str, dict] = {}
            timestamp = int(dt.datetime.combine(day, dt.time(12), tzinfo=dt.timezone.utc).timestamp() * 1000)

            def search(fond: str, at_date: int | None):
                filters = [{"facette": "NOM_CODE", "valeurs": [code_name]}]
                if at_date is not None:
                    filters.append({"facette": "DATE_VERSION", "singleDate": at_date})
                body = {"fond": fond, "recherche": {
                    "champs": [{"typeChamp": "NUM_ARTICLE", "criteres": [{
                        "typeRecherche": "EXACTE", "valeur": number, "operateur": "ET"
                    }], "operateur": "ET"}],
                    "filtres": filters, "pageNumber": 1, "pageSize": 30,
                    "operateur": "ET", "sort": "PERTINENCE", "typePagination": "ARTICLE"
                }}
                for ident in ids_in_search_response(self.call("search", body)):
                    if ident in found or len(found) >= 40:
                        continue
                    obj = verified(ident)
                    if obj is not None:
                        found[ident] = obj

            # 1. Contrôle d'abord la situation actuelle, sans choisir un ID historique.
            search("CODE_DATE", timestamp)
            if not any(live_version(a, day) for a in found.values()):
                # 2. Index des états, y compris les anciennes versions.
                search("CODE_ETAT", None)
                # 3. Si cet index ne prouve pas une abrogation, rechercher une
                # version effectivement applicable avant l'abrogation. Cela évite
                # notamment la version L212-36 'MODIFIE' aux dates inversées.
                if not any(terminal_abrogation(a, day) for a in found.values()):
                    for year in (2020, 2017, 2010, 2005):
                        if year >= day.year:
                            continue
                        old_at = int(dt.datetime(year, 1, 1, 12, tzinfo=dt.timezone.utc).timestamp() * 1000)
                        search("CODE_DATE", old_at)
                        if any(terminal_abrogation(a, day) for a in found.values()):
                            break
            ids = list(found)
            with self._cache_lock:
                if len(self._lookup_cache) > 500:
                    self._lookup_cache.clear()
                self._lookup_cache[cache_key] = ids
            return list(found.values())

        return [obj for ident in cached_ids if (obj := verified(ident)) is not None]


    def search_loda_article_number(self, number: str, text_number: str, day: dt.date, kind: str = "loi") -> list[dict]:
        """Recherche un article d'une loi/ordonnance/décret par NUM et NUM_ARTICLE.

        Aucun numéro d'article n'est considéré unique sans son numéro de texte.
        La recherche à une date historique permet de retrouver les abrogations.
        """
        number = re.sub(r"\s+", " ", str(number or "")).strip()
        text_number = normalize_text_number(text_number)
        if not re.fullmatch(r"\d+(?:-\d+){0,3}(?: (?:bis|ter|quater))?", number, re.I):
            return []
        if not re.fullmatch(r"\d{2,4}-\d{1,6}", text_number):
            return []
        cache_key = ("LODA:" + kind + ":" + number, text_number, day.isoformat())
        with self._cache_lock:
            cached = self._lookup_cache.get(cache_key)
        if cached is not None:
            return [self.article(i) for i in cached]

        found: dict[str, dict] = {}
        def fetch(fond: str, at_date: dt.date | None):
            filters = []
            if at_date is not None:
                millis = int(dt.datetime.combine(at_date, dt.time(12), tzinfo=dt.timezone.utc).timestamp() * 1000)
                filters.append({"facette": "DATE_VERSION", "singleDate": millis})
            body = {"fond": fond, "recherche": {
                "champs": [
                    {"typeChamp": "NUM", "criteres": [{"typeRecherche": "EXACTE", "valeur": text_number,
                                                          "operateur": "ET"}], "operateur": "ET"},
                    {"typeChamp": "NUM_ARTICLE", "criteres": [{"typeRecherche": "EXACTE", "valeur": number,
                                                                  "operateur": "ET"}], "operateur": "ET"},
                ], "filtres": filters, "pageNumber": 1, "pageSize": 30,
                "operateur": "ET", "sort": "PERTINENCE", "typePagination": "DEFAUT"
            }}
            for ident in ids_in_search_response(self.call("search", body)):
                if ident in found or len(found) >= 40:
                    continue
                try:
                    article = self.article(ident)
                except APIError:
                    continue
                if str(article.get("num") or "").strip().casefold() != number.casefold():
                    continue
                titles = article_code_titles(article)
                # Contrôle anti-homonymie : ne jamais accepter un article de CODE,
                # ni d'une autre loi ayant le même numéro d'article.
                if not titles:
                    continue
                title = " ".join(titles).lower()
                if not re.search(rf"(?<!\d){re.escape(text_number)}(?!\d)", title):
                    continue
                if kind not in title:
                    continue
                found[ident] = article

        fetch("LODA_DATE", day)
        if not any(live_version(a, day) for a in found.values()):
            # Recherche d'état juridique sur l'ensemble du fonds LODA,
            # puis exploration historique bornée. Aucune année de réforme spéciale.
            try:
                fetch("LODA_ETAT", None)
            except APIError:
                pass  # Certains filtres sont refusés : le balayage par dates reste possible.
            if not any(terminal_abrogation(a, day) for a in found.values()):
                # Années génériques : années récentes, puis pas de 3/5 ans jusqu'à
                # l'année de l'acte (si déterminable depuis son numéro).
                digits = int(text_number.split("-", 1)[0])
                year_of_text = digits if digits >= 1000 else (2000 + digits if digits <= day.year % 100 else 1900 + digits)
                earliest = max(1800, min(year_of_text, day.year))
                samples = [day.year - 1, day.year - 3, day.year - 5]
                samples.extend(range(day.year - 8, earliest - 1, -5))
                samples.append(earliest)
                seen_years = set()
                for year in samples[:14]:
                    if year < earliest or year in seen_years:
                        continue
                    seen_years.add(year)
                    past = dt.date(year, 7, 1)
                    if past >= day:
                        continue
                    try:
                        fetch("LODA_DATE", past)
                    except APIError:
                        continue
                    if any(terminal_abrogation(a, day) for a in found.values()):
                        break
        with self._cache_lock:
            if len(self._lookup_cache) > 500:
                self._lookup_cache.clear()
            self._lookup_cache[cache_key] = list(found)
        return list(found.values())


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


class FlatTextCollector(HTMLParser):
    """Assemble le texte en conservant l'adjacence des éléments HTML inline.

    Un renvoi scindé par <span>/<a> est traité comme une seule expression.
    Les paragraphes restent séparés pour ne pas créer de faux renvois.
    """
    BLOCKS = {"p", "div", "li", "blockquote", "tr", "h2", "h3", "h4", "br"}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.blocked = 0
    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "iframe", "template"}:
            self.blocked += 1
        elif tag.lower() == "br" and not self.blocked:
            self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "iframe", "template"}:
            self.blocked = max(0, self.blocked-1)
        elif tag.lower() in self.BLOCKS and not self.blocked:
            self.parts.append("\n")
    def handle_data(self, data):
        if not self.blocked:
            self.parts.append(data)


def cohesive_text(value: str) -> str:
    if "<" not in str(value or ""):
        return html.unescape(str(value or ""))
    parser = FlatTextCollector()
    parser.feed(value or "")
    return html.unescape("".join(parser.parts))


def clean_text(value: str) -> str:
    parser = TextCollector()
    parser.feed(value or "")
    return " ".join(html.unescape(" ".join(parser.parts)).split())


def parse_article_id(value: str) -> str:
    m = ARTICLE_RE.search(value or "")
    if not m:
        raise APIError("Collez une URL d'article ou un identifiant LEGIARTI (ex. LEGIARTI000006307920).", 400)
    return m.group(0).upper()


# Une citation n'est pas forcément un lien HTML : Légifrance fournit souvent du texte brut.
# Le suffixe est borné pour ne pas confondre L. 212-4 et L. 212-4-1.
LEGAL_NUM_RE = re.compile(r"(?<![\w.-])(?:LO|L|R|D)\s*\.?\s*\d{1,5}(?:\s*-\s*\d{1,5}){1,4}(?![\w-])", re.I)
ARTICLE_PREFIX_RE = re.compile(r"(?:articles?|art\.)\s*$", re.I)

# Les plages sont des renvois vers plusieurs articles, pas un simple lien vers
# le premier numéro. Expansion prudente : mêmes préfixe et sous-niveaux, avec
# une borne haute pour éviter d'effectuer des centaines d'appels non voulus.
RANGE_RE = re.compile(
    r"(?<![\w.-])(?P<first>(?:LO|L|R|D)\s*\.?\s*\d{1,5}(?:\s*-\s*\d{1,5}){1,4})"
    r"\s+(?:à|au|jusqu['’]à)\s+"
    r"(?P<last>(?:LO|L|R|D)\s*\.?\s*\d{1,5}(?:\s*-\s*\d{1,5}){1,4})(?![\w-])",
    re.I,
)
MAX_RANGE_ARTICLES = max(2, min(int(os.environ.get("MAX_RANGE_ARTICLES", "40")), 80))

# Renvois vers les textes autonomes : « l'article 7 de la loi n° 90-111 ».
# Les subdivisions 1° et 2° qui précèdent l'article sont conservées dans le
# texte affiché, mais ne doivent PAS être interprétées comme les articles 1 et 2.
NORMATIVE_RE = re.compile(
    r"(?<![\w.-])(?P<reference>(?:l['’])?articles?\s+"
    r"(?P<article>\d+(?:\s*-\s*\d+){0,3}(?:\s+(?:bis|ter|quater))?)"
    r"\s+de\s+(?:la|l['’])\s*(?P<type>loi|ordonnance|décret)\s+"
    r"(?:n[°ºo]\s*|numéro\s+)(?P<texte>\d{2,4}\s*[-–‑]\s*\d{1,6}))",
    re.IGNORECASE,
)


def normalize_text_number(value: str) -> str:
    return re.sub(r"\s*[-–‑]\s*", "-", str(value or "")).strip()


def normative_mentions(text: str) -> list[dict]:
    return [{"start": m.start(), "end": m.end(), "raw": m.group("reference"),
             "numero": re.sub(r"\s+", " ", m.group("article")).strip(),
             "texteNumero": normalize_text_number(m.group("texte")),
             "typeTexte": m.group("type").lower()}
            for m in NORMATIVE_RE.finditer(text)]



def expand_article_range(first: str, last: str) -> list[str] | None:
    """Énumère une plage simple, ou renvoie None si la numérotation est ambiguë.

    L. 212-34 à L. 212-36 -> L212-34, L212-35, L212-36.
    L. 212-4-1 à L. 212-10 -> None : structures différentes, pas de
    fabrication arbitraire de numéros intermédiaires.
    """
    left = normalize_article_number(first)
    right = normalize_article_number(last)
    m1 = re.fullmatch(r"(LO|L|R|D)(\d+(?:-\d+)*)", left)
    m2 = re.fullmatch(r"(LO|L|R|D)(\d+(?:-\d+)*)", right)
    if not m1 or not m2 or m1.group(1) != m2.group(1):
        return None
    a, b = m1.group(2).split("-"), m2.group(2).split("-")
    if len(a) != len(b) or a[:-1] != b[:-1]:
        return None
    start, end = int(a[-1]), int(b[-1])
    if start > end or end - start + 1 > MAX_RANGE_ARTICLES:
        return None
    stem = m1.group(1) + ("-".join(a[:-1]) + "-" if len(a) > 1 else "")
    return [stem + str(n) for n in range(start, end+1)]


def textual_mentions(plain_text: str) -> tuple[list[dict], list[str]]:
    """Identifie les renvois explicites et développe les plages non ambiguës.

    Chaque occurrence contient les numéros à résoudre, avec le même contexte
    grammatical. Conserve un avertissement quand une plage ne peut pas être
    développée sans risquer de fabriquer de fausses références.
    """
    out: list[dict] = []
    warnings: list[str] = []
    ranges = list(RANGE_RE.finditer(plain_text))
    for m in ranges:
        numbers = expand_article_range(m.group("first"), m.group("last"))
        if numbers is None:
            # Aux frontières des plages complexes on ne vérifie que les deux
            # références explicites, sans prétendre à l'exhaustivité.
            numbers = [normalize_article_number(m.group("first")), normalize_article_number(m.group("last"))]
            warnings.append(f"Plage complexe « {m.group()} » : seuls les deux articles extrêmes sont contrôlables automatiquement.")
        out.append({"start": m.start(), "end": m.end(), "raw": m.group(),
                    "numbers": list(dict.fromkeys(numbers)), "plage": True})
    for m in LEGAL_NUM_RE.finditer(plain_text):
        if any(group.start() <= m.start() < group.end() for group in ranges):
            continue
        out.append({"start": m.start(), "end": m.end(), "raw": m.group(),
                    "numbers": [normalize_article_number(m.group())], "plage": False})
    out.sort(key=lambda x: x["start"])
    return out, warnings


def pretty_article_number(number: str) -> str:
    return re.sub(r"^(LO|L|R|D)(?=\d)", r"\1. ", number)


def normalize_article_number(value: str) -> str:
    m = LEGAL_NUM_RE.search(value or "")
    return re.sub(r"[\s.]", "", m.group()).upper() if m else ""


def indexed_article_numbers(entry: dict) -> set[str]:
    # L'API fournit parfois « Code du patrimoine - art. L212-33 », parfois seulement
    # un numéro dans le titre. Ne pas assimiler un titre sans numéro à une citation.
    output = set()
    for field in (entry.get("numero"), entry.get("titre"), entry.get("evidence")):
        if not isinstance(field, str):
            continue
        # Un hyperlien peut englober « L212-34 à L212-36 » tout en pointant
        # vers un seul LEGIARTI. Ne JAMAIS attribuer son ID à tous les numéros
        # contenus dans le libellé du lien.
        numbers = {normalize_article_number(m.group()) for m in LEGAL_NUM_RE.finditer(field)}
        if len(numbers) == 1:
            output.update(numbers)
    return output - {""}



def ids_in_search_response(payload: dict) -> list[str]:
    """Les versions de l'API peuvent ranger les articles dans sections/extracts."""
    ids: list[str] = []
    def walk(node, depth: int = 0):
        if depth > 8 or len(ids) >= 30:
            return
        if isinstance(node, dict):
            ident = str(node.get("id") or "").upper()
            if ARTICLE_RE.fullmatch(ident) and ident not in ids:
                ids.append(ident)
            for key in ("results", "articles", "sections", "extracts", "article", "articleVersions"):
                if key in node:
                    walk(node[key], depth+1)
        elif isinstance(node, list):
            for x in node[:60]:
                walk(x, depth+1)
    walk(payload)
    return ids


def normalize_code_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in value if not unicodedata.combining(c)).lower())


def article_code_titles(article: dict) -> list[str]:
    titles = []
    for entry in article.get("textTitles") or []:
        if isinstance(entry, dict):
            value = entry.get("titre") or entry.get("titreLong") or entry.get("title")
            if isinstance(value, str) and value.strip():
                titles.append(value)
    return titles


# Noms complets, pour éviter de chercher « code du patrimoine et des articles ... ».
# Les autres noms peuvent être ajoutés sans changer le moteur d'analyse.
KNOWN_CODE_NAMES = (
    "Code général des collectivités territoriales", "Code de la sécurité sociale",
    "Code du patrimoine", "Code général des impôts", "Code civil",
    "Code pénal", "Code de procédure pénale", "Code du travail",
    "Code de l'éducation", "Code de la santé publique", "Code de l'environnement",
    "Code de l'urbanisme", "Code de la construction et de l'habitation",
    "Code de la commande publique", "Code des relations entre le public et l'administration",
    "Code de la fonction publique", "Code général de la fonction publique",
    "Code de commerce", "Code des transports", "Code de l'énergie",
    "Code de justice administrative", "Code de procédure civile",
    "Code rural et de la pêche maritime", "Code monétaire et financier",
    "Code des assurances", "Code de la consommation", "Code du sport",
    "Code des postes et des communications électroniques", "Code de la propriété intellectuelle",
    "Code de la défense", "Code de l'action sociale et des familles",
    "Code de la route", "Code du tourisme", "Code électoral",
    "Code minier", "Code forestier", "Code des douanes",
)
CODE_NAMES_PATTERN = re.compile(r"(?<!\w)(?:" + "|".join(
    re.escape(name).replace("\'", "['’]") for name in sorted(KNOWN_CODE_NAMES, key=len, reverse=True)
) + r")(?!\w)", re.IGNORECASE)


def code_for_mention(full_text: str, position: int, source_article: dict) -> str | None:
    """Ne résout un renvoi que si son code est explicite dans son contexte.

    Cas classique : « articles L. ... et L. ... du code du patrimoine ».
    On ne devine jamais le code sur la seule base du numéro.
    """
    matches = list(CODE_NAMES_PATTERN.finditer(full_text[max(0, position-600):position+360]))
    if not matches:
        tail = full_text[max(0, position-75):position+45].lower()
        if re.search(r"\b(?:du présent|du même|de ce) code\b", tail):
            titles = article_code_titles(source_article)
            return titles[0] if len(titles) == 1 and titles[0].lower().startswith("code ") else None
        return None
    start = max(0, position-600)
    # Préférence au nom situé après le renvoi : « L. 212-33 du code du patrimoine ».
    nearby = []
    for m in matches:
        absolute = start + m.start()
        dist = absolute - position
        if 0 <= dist <= 270:
            rank = dist
        elif -240 <= dist < 0:
            rank = 300 + abs(dist)
        else:
            continue
        nearby.append((rank, m.group()))
    if not nearby:
        return None
    nearby.sort(key=lambda item: item[0])
    return next((name for name in KNOWN_CODE_NAMES if normalize_code_title(name) == normalize_code_title(nearby[0][1])), None)


def resolve_textual_references(client: PisteClient, article: dict, entries: list[dict],
                               unresolved: list[str], day: dt.date, bulk: bool) -> tuple[list[dict], list[str], list[str]]:
    """Résout individuellement tous les articles d'une plage via numéro + code."""
    raw = str(article.get("texteHtml") or article.get("texte") or article.get("content") or "")
    text = cohesive_text(raw)
    mentions, range_notes = textual_mentions(text)
    todo_numbers = {normalize_article_number(label) for label in unresolved}
    todo_numbers.discard("")
    remaining = set(todo_numbers)
    warnings: list[str] = list(range_notes)
    limit = AUTO_RESOLVE_BULK_LIMIT if bulk else AUTO_RESOLVE_LIMIT
    if not callable(getattr(client, "search_article_number", None)):
        return entries, unresolved, warnings
    done: set[tuple[str, str]] = set()
    by_num = {n for entry in entries for n in indexed_article_numbers(entry)}
    count = 0
    for mention in mentions:
        # Dans une plage, le code figure fréquemment après le dernier numéro.
        code = code_for_mention(text, mention["end"], article)
        if not code:
            code = code_for_mention(text, mention["start"], article)
        for number in mention["numbers"]:
            if number not in remaining or number in by_num or not code or (number, code) in done:
                continue
            done.add((number, code))
            if count >= limit:
                warnings.append(f"Recherche par numéro limitée à {limit} référence(s) par article : résultat partiel.")
                return entries, [label for label in unresolved if normalize_article_number(label) in remaining], warnings
            count += 1
            try:
                candidates = client.search_article_number(number, code, day)
            except APIError as exc:
                warnings.append(f"Recherche de {number} ({code}) indisponible : {exc.message}")
                continue
            cids = {str(x.get("cid") or x.get("id")) for x in candidates}
            if len(cids) != 1 or not candidates:
                continue
            # Ne jamais choisir une version « MODIFIE » mort-née avant la
            # véritable version terminale « ABROGE » du même article.
            candidates.sort(key=lambda x: reference_version_rank(x, day), reverse=True)
            chosen = candidates[0]
            ident = str(chosen["id"]).upper()
            if ident not in {e["id"] for e in entries}:
                entries.append({"id": ident, "numero": number,
                                "titre": f"{code} - art. {pretty_article_number(number)}",
                                "sources": ["texte_litteral", "recherche_numero"],
                                "evidence": f"Numéro {number} et code vérifiés par la recherche Légifrance",
                                "nature": "CODE"})
            remaining.discard(number)
            by_num.add(number)
    return entries, [label for label in unresolved if normalize_article_number(label) in remaining], warnings


def resolve_normative_references(client: PisteClient, article: dict, entries: list[dict],
                                 unresolved: list[str], day: dt.date, bulk: bool
                                 ) -> tuple[list[dict], list[str], list[str]]:
    """Résout les numéros d’articles de lois/décrets sans les confondre avec des codes."""
    plain = cohesive_text(str(article.get("texteHtml") or article.get("texte") or ""))
    mentions = normative_mentions(plain)
    if not mentions:
        return entries, unresolved, []
    warnings = []
    limit = AUTO_RESOLVE_BULK_LIMIT if bulk else AUTO_RESOLVE_LIMIT
    search = getattr(client, "search_loda_article_number", None)
    for index, mention in enumerate(mentions):
        key = (mention["numero"], mention["texteNumero"], mention["typeTexte"])
        existing = [e for e in entries if (e.get("numero"), e.get("texteNumero"), e.get("typeTexte")) == key]
        if existing:
            continue
        if index >= limit or not callable(search):
            if mention["raw"] not in unresolved:
                unresolved.append(mention["raw"])
            warnings.append("Références à des textes non codifiés partiellement vérifiées.")
            continue
        try:
            candidates = search(*key[:2], day, key[2])
        except APIError as exc:
            candidates = []
            warnings.append("Recherche d'un texte non codifié indisponible : " + exc.message)
        cids = {str(x.get("cid") or x.get("id")) for x in candidates}
        if len(cids) != 1 or not candidates:
            if mention["raw"] not in unresolved:
                unresolved.append(mention["raw"])
            continue
        candidates.sort(key=lambda x: reference_version_rank(x, day), reverse=True)
        chosen = candidates[0]
        ident = str(chosen.get("id") or "").upper()
        if not ARTICLE_RE.fullmatch(ident):
            unresolved.append(mention["raw"])
            continue
        entry = next((e for e in entries if e["id"] == ident), None)
        if entry is None:
            entry = {"id": ident, "nature": "LODA", "sources": ["texte_litteral", "recherche_numero"],
                     "evidence": mention["raw"], "titre": f"{key[2].capitalize()} n° {key[1]} – art. {key[0]}"}
            entries.append(entry)
        entry.update({"numero": key[0], "texteNumero": key[1], "typeTexte": key[2], "nature": "LODA"})
    return entries, unresolved, list(dict.fromkeys(warnings))

def extract_references(article: dict, links: dict) -> tuple[list[dict], list[str]]:
    indexed: dict[str, dict] = {}
    for e in links.get("liensCite", []) or []:
        if not isinstance(e, dict):
            continue
        ident = str(e.get("id", "")).upper()
        if not (ARTICLE_RE.fullmatch(ident) or OTHER_RE.fullmatch(ident)):
            continue
        indexed[ident] = {
            "id": ident, "titre": re.sub(r"\s*\([Vv]\)$", "", str(e.get("name") or ident)),
            "sources": ["indexation"], "evidence": "Lien répertorié par Légifrance",
            "nature": str(e.get("nature") or ""),
        }
    source_html = str(article.get("texteHtml") or article.get("texte") or article.get("content") or "")
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

    # Rapprochement par numéro. Une plage représente plusieurs articles, y
    # compris ceux non écrits explicitement (L. 212-35 dans 34 à 36).
    plain = cohesive_text(source_html)
    mentions, _ = textual_mentions(plain)
    by_number: dict[str, list[dict]] = {}
    for entry in indexed.values():
        for key in indexed_article_numbers(entry):
            by_number.setdefault(key, []).append(entry)
    unresolved: list[str] = []
    for mention in mentions:
        for number in mention["numbers"]:
            matches = by_number.get(number, [])
            if len(matches) == 1:
                entry = matches[0]
                if "texte_litteral" not in entry["sources"]:
                    entry["sources"].append("texte_litteral")
            else:
                if mention["plage"]:
                    label = pretty_article_number(number)
                else:
                    prefix = ARTICLE_PREFIX_RE.search(plain[max(0, mention["start"]-18):mention["start"]])
                    label = (prefix.group().strip() + " " if prefix else "") + mention["raw"].strip()
                if label not in unresolved:
                    unresolved.append(label)
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


def effective_period(article: dict) -> bool:
    """Écarte les versions mort-nées/incohérentes dont début >= fin."""
    start = parsed_date(article.get("dateDebut"))
    end = parsed_date(article.get("dateFin"))
    return not (start and end and start >= end)


def terminal_abrogation(article: dict, day: dt.date) -> bool:
    """Abrogation affirmée par l'état d'une version chronologiquement cohérente."""
    end = parsed_date(article.get("dateFin"))
    return (effective_period(article) and normalize_state(article).startswith("ABROGE")
            and (end is None or end <= day))


def reference_version_rank(article: dict, day: dt.date) -> tuple:
    """Version courante, puis abrogation attestée, puis ancienne version valide."""
    start = parsed_date(article.get("dateDebut")) or dt.date.min
    end = parsed_date(article.get("dateFin")) or dt.date.min
    return (int(live_version(article, day) and effective_period(article)),
            int(terminal_abrogation(article, day)),
            int(effective_period(article)), end, start)


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
    """Surligne les expressions dans le FLUX TEXTUEL ENTIER, pas nœud par nœud.

    Légifrance découpe parfois une référence entre plusieurs balises inline.
    On reconstruit des tokens sûrs, repère les références par offsets globaux,
    puis on annote les segments visibles. Une indexation sans citation textuelle
    n'est PAS coloriée et sera affichée séparément dans l'interface.
    """
    SAFE = {"p", "div", "span", "br", "ul", "ol", "li", "b", "strong",
            "i", "em", "u", "sup", "sub", "blockquote", "h2", "h3", "h4",
            "table", "thead", "tbody", "tr", "td", "th"}
    VOID = {"br"}
    BLOCKS = {"p", "div", "li", "blockquote", "tr", "h2", "h3", "h4"}
    BLOCKED = {"script", "style", "iframe", "object", "embed", "svg", "math",
               "template", "form", "textarea", "button", "input"}

    def __init__(self, results: list[dict]):
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple[str, str]] = []
        self.stack: list[str] = []
        self.blocked_depth = 0
        self.by_number = {}
        self.by_normative = {}
        self.by_id = {r["id"]: r for r in results}
        self.text_offset = 0
        self.open_anchors: list[tuple[str, int]] = []
        self.anchors: list[dict] = []
        for r in results:
            for n in indexed_article_numbers(r):
                self.by_number.setdefault(n, []).append(r)
            if r.get("texteNumero") and r.get("numero"):
                key = (str(r["numero"]), str(r["texteNumero"]), str(r.get("typeTexte") or "loi"))
                self.by_normative.setdefault(key, []).append(r)
        self.count_highlighted = 0
        self.mentioned_ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.blocked_depth:
            if tag in self.BLOCKED:
                self.blocked_depth += 1
            return
        if tag in self.BLOCKED:
            self.blocked_depth = 1
            return
        # Supprime les href de Légifrance (ils ne prouvent pas que toute une
        # plage pointe vers une seule cible), mais conserve leur texte.
        if tag == "a":
            self.open_anchors.append((str(dict(attrs).get("href") or ""), self.text_offset))
            return
        if tag not in self.SAFE:
            return
        self.tokens.append(("tag", f"<{tag}>"))
        if tag == "br":
            self.tokens.append(("virtual", "\n"))
            self.text_offset += 1
        else:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.blocked_depth:
            if tag in self.BLOCKED:
                self.blocked_depth -= 1
            return
        if tag == "a":
            if self.open_anchors:
                href, start = self.open_anchors.pop()
                allowed = href.startswith(("/codes/", "/loda/", "/jorf/", "https://www.legifrance.gouv.fr/"))
                found = ARTICLE_RE.search(href) or OTHER_RE.search(href)
                ident = found.group().upper() if allowed and found else None
                if ident in self.by_id and self.text_offset > start:
                    self.anchors.append({"start": start, "end": self.text_offset, "entry": self.by_id[ident]})
            return
        if tag not in self.stack:
            return
        while self.stack:
            opened = self.stack.pop()
            self.tokens.append(("tag", f"</{opened}>"))
            if opened in self.BLOCKS:
                self.tokens.append(("virtual", "\n"))
                self.text_offset += 1
            if opened == tag:
                break

    def handle_data(self, data):
        if not self.blocked_depth:
            self.tokens.append(("text", data))
            self.text_offset += len(data)

    def _badge(self, value: str, match: dict) -> str:
        status = match["status"]
        title = html.escape(match["title"], quote=True)
        content = html.escape(value)
        entry = match.get("entry")
        if entry and not match.get("range"):
            url = html.escape(legal_article_url(entry["id"], entry.get("nature", "")), quote=True)
            return (f'<a class="legal-ref status-{status}" href="{url}" target="_blank" '
                    f'rel="noopener noreferrer" title="{title}" data-ref="{entry["id"]}">{content}</a>')
        extra = " legal-range" if match.get("range") else ""
        return f'<mark class="legal-ref{extra} status-{status}" title="{title}">{content}</mark>'

    def _annotation(self, mention: dict) -> dict:
        if mention.get("anchor"):
            refs = [mention["anchor"]]
        elif mention.get("normative"):
            m = mention["normative"]
            key = (m["numero"], m["texteNumero"], m["typeTexte"])
            matches = self.by_normative.get(key, [])
            refs = matches if len(matches) == 1 else []
        else:
            refs = []
            for number in mention["numbers"]:
                candidates = self.by_number.get(number, [])
                if len(candidates) == 1:
                    refs.append(candidates[0])
                else:
                    anchors = [a["entry"] for a in self.anchors
                               if a["start"] <= mention["start"] and mention["end"] <= a["end"]]
                    refs.append(anchors[0] if len(anchors) == 1 and not mention.get("plage") else None)
        found = [r for r in refs if r]
        for ref in found:
            self.mentioned_ids.add(ref["id"])
        if found or mention.get("plage"):
            self.count_highlighted += 1
        if len(found) != len(refs) or not refs:
            status = "nonresolu"
        else:
            statuses = {r.get("statut", "indetermine") for r in found}
            status = next((x for x in ("alerte", "indetermine", "ancienne_version", "vigoureux") if x in statuses), "indetermine")
        if mention.get("plage"):
            labels = []
            for n, ref in zip(mention["numbers"], refs):
                value = ({"vigoureux":"en vigueur", "alerte":"à vérifier / abrogé",
                          "indetermine":"indéterminé", "ancienne_version":"ancienne version"}
                         .get(ref.get("statut"), "indéterminé") if ref else "non résolu")
                labels.append(f"{pretty_article_number(n)} : {value}")
            title = f"Plage de {len(refs)} articles : " + " ; ".join(labels)
        else:
            title = (found[0].get("raison") or "Voir sur Légifrance") if found else "Référence non résolue : statut inconnu"
        return {"start": mention["start"], "end": mention["end"], "status": status,
                "title": title, "entry": found[0] if len(refs) == 1 and len(found) == 1 else None,
                "range": bool(mention.get("plage"))}

    def render(self, markup: str) -> str:
        self.feed(markup)
        self.close()
        while self.stack:
            self.tokens.append(("tag", f"</{self.stack.pop()}>"))
        flat = "".join(v for kind, v in self.tokens if kind in {"text", "virtual"})
        mentions, _ = textual_mentions(flat)
        for m in normative_mentions(flat):
            mentions.append({"start": m["start"], "end": m["end"], "raw": m["raw"],
                             "normative": m, "plage": False})
        mentions.sort(key=lambda x: (x["start"], -(x["end"]-x["start"])))
        # Un lien ancré dans le HTML est une citation textuelle même si son
        # libellé n'est pas une référence numérotée. Il ne supplante JAMAIS
        # une plage ou un renvoi textuel explicitement reconnu.
        for anchor in self.anchors:
            mentions.append({"start": anchor["start"], "end": anchor["end"],
                             "anchor": anchor["entry"], "plage": False})
        mentions.sort(key=lambda x: (0 if x.get("normative") else (1 if x.get("plage") else (3 if x.get("anchor") else 2)), x["start"]))
        selected = []
        for mention in mentions:
            if any(not (mention["end"] <= m["start"] or mention["start"] >= m["end"]) for m in selected):
                continue
            selected.append(mention)
        decorations = [self._annotation(m) for m in selected]
        decorations.sort(key=lambda x: x["start"])
        result = []
        pos = 0
        for kind, value in self.tokens:
            if kind == "tag":
                result.append(value)
                continue
            end = pos + len(value)
            if kind == "virtual":
                pos = end
                continue
            points = {pos, end}
            overlaps = [m for m in decorations if m["start"] < end and m["end"] > pos]
            for m in overlaps:
                points.update((max(pos, m["start"]), min(end, m["end"])))
            cuts = sorted(points)
            for a, b in zip(cuts, cuts[1:]):
                part = value[a-pos:b-pos]
                current = next((m for m in overlaps if m["start"] <= a < m["end"]), None)
                result.append(self._badge(part, current) if current else html.escape(part))
            pos = end
        return "".join(result)


def legal_article_url(ident: str, nature: str = "") -> str:
    if ident.startswith("JORFARTI"):
        return f"https://www.legifrance.gouv.fr/jorf/id/{ident}"
    if ident.startswith("LEGIARTI"):
        group = "codes" if nature.upper() == "CODE" else "loda"
        return f"https://www.legifrance.gouv.fr/{group}/article_lc/{ident}"
    return "https://www.legifrance.gouv.fr/"


def render_annotated_html(article: dict, checked: list[dict]) -> tuple[str, int, set[str]]:
    raw = str(article.get("texteHtml") or article.get("texte") or article.get("content") or "")
    if not raw:
        return "", 0, set()
    if "<" not in raw:
        raw = "<p>" + html.escape(raw).replace("\n", "<br>") + "</p>"
    parser = SafeArticleRenderer(checked)
    result = parser.render(raw[:300_000])
    return result, parser.count_highlighted, parser.mentioned_ids

def candidates_from_concordance(data: dict, old_id: str) -> list[dict]:
    """Liens sortants `newTexts`, jamais `oldTexts` (sens inverse).

    On ne prend que des identifiants ARTICLE LEGI : un lien vers un texte
    entier ou un modificateur ne constitue pas une équivalence d'article.
    """
    seen: set[str] = set()
    result = []
    for entry in data.get("newTexts", []) or []:
        if not isinstance(entry, dict):
            continue
        ident = str(entry.get("id") or entry.get("articleId") or "").upper()
        if not ARTICLE_RE.fullmatch(ident) or ident == old_id or ident in seen:
            continue
        seen.add(ident)
        types = [str(x.get("type") or "") for x in entry.get("modifications", []) or [] if isinstance(x, dict)]
        result.append({"id": ident, "titre": entry.get("name") or ident,
                       "type": ", ".join(types), "nature": entry.get("nature") or ""})
    return result


def discover_successors(client: PisteClient, old_article: dict, day: dt.date,
                        history: list[dict] | None = None) -> dict:
    """Explore génériquement les concordances officielles d'un article.

    Recherche sur plusieurs versions du même CID, puis suit les transferts
    successifs. Budget de requêtes limité pour maîtriser le quota PISTE.
    Un résultat ambigu ou partiel ne devient JAMAIS une correction certaine.
    """
    from collections import deque
    ident = str(old_article.get("id") or "").upper()
    cid = str(old_article.get("cid") or ident).upper()
    # Original d'abord, puis versions historiques terminales les plus récentes.
    records = list(history or []) + list(old_article.get("articleVersions") or [])
    ordered = sorted((v for v in records if isinstance(v, dict)
                      and ARTICLE_RE.fullmatch(str(v.get("id") or "").upper())
                      and (parsed_date(v.get("dateDebut")) or dt.date.min) <= day),
                     key=lambda v: parsed_date(v.get("dateFin")) or dt.date.min, reverse=True)
    seeds = [ident]
    for version in ordered:
        vid = str(version["id"]).upper()
        if vid not in seeds and len(seeds) < CONCORDANCE_VERSION_PROBES:
            seeds.append(vid)
    more_versions = bool(len({str(v.get("id")).upper() for v in ordered}) > len(seeds))
    queue = deque((sid, [sid], 0) for sid in seeds)
    visited: set[str] = set()
    destinations: dict[str, dict] = {}
    failures: list[str] = []
    calls = 0
    truncated = more_versions
    while queue:
        if calls >= CONCORDANCE_MAX_CALLS:
            truncated = True
            break
        source_id, path, depth = queue.popleft()
        if source_id in visited:
            continue
        visited.add(source_id)
        calls += 1
        try:
            data = client.call("consult/concordanceLinksArticle", {"articleId": source_id})
        except APIError as exc:
            failures.append(f"{source_id}: {exc.message}")
            continue
        outgoing = candidates_from_concordance(data, source_id)
        for item in outgoing:
            next_id = item["id"]
            if next_id in path:
                continue
            try:
                candidate = client.article(next_id)
            except (APIError, KeyError):
                failures.append(f"Identifiant non consultable : {next_id}")
                continue
            # Un nouveau LEGIARTI du même CID est une autre VERSION du même
            # article. Ce n'est pas un renvoi à modifier juridiquement.
            new_cid = str(candidate.get("cid") or next_id).upper()
            if new_cid == cid:
                continue
            current = candidate
            if not live_version(current, day):
                try:
                    versions = client.versions(new_cid) if callable(getattr(client, "versions", None)) else flatten_versions(client.call("consult/getArticleByCid", {"cid": new_cid}))
                    live = [v for v in versions if live_version(v, day)]
                    if live:
                        live.sort(key=lambda v: parsed_date(v.get("dateDebut")) or dt.date.min, reverse=True)
                        current = client.article(str(live[0]["id"]).upper())
                except (APIError, KeyError):
                    pass
            new_path = path + [next_id]
            if live_version(current, day):
                dest_id = str(current.get("id") or "").upper()
                if ARTICLE_RE.fullmatch(dest_id):
                    # Déduplication par CID juridique, non par LEGIARTI/version.
                    k = str(current.get("cid") or dest_id).upper()
                    if k not in destinations or len(new_path) < len(destinations[k]["parcours"]):
                        destinations[k] = {"id": dest_id, "titre": item.get("titre"),
                                           "type": item.get("type"), "nature": item.get("nature"),
                                           "parcours": new_path,
                                           "source": "Concordances Légifrance (API)"}
            elif depth + 1 < CONCORDANCE_MAX_HOPS and next_id not in visited:
                queue.append((next_id, new_path, depth + 1))
            elif depth + 1 >= CONCORDANCE_MAX_HOPS:
                truncated = True
    return {"candidats": list(destinations.values()), "versionsExaminees": seeds,
            "appels": calls, "partiel": truncated or bool(failures), "erreurs": failures[:3]}


def comparable_content(article: dict) -> str:
    """Texte de la disposition (hors métadonnées), normalisé sans corriger sa rédaction."""
    raw = str(article.get("texteHtml") or article.get("texte") or article.get("content") or "")
    if not raw:
        return ""
    txt = clean_text(raw)
    return " ".join(unicodedata.normalize("NFKC", txt).replace("\u00a0", " ").split())


def propose_replacement(client: PisteClient, old_article: dict, candidates: list[dict], day: dt.date) -> dict:
    """Comparaison lexicale stricte, sans inférence juridique ni choix arbitraire."""
    output = {"statut": "aucune", "libelle": "Aucun successeur en vigueur confirmé", "candidats": [],
              "source": "Concordances Légifrance (API)"}
    if not candidates:
        return output
    old_text = comparable_content(old_article)
    results = []
    for item in candidates[:5]:
        ident = item.get("id", "")
        if not ARTICLE_RE.fullmatch(ident):
            continue
        try:
            article = client.article(ident)
            # Les concordances renvoient parfois une ancienne version du nouveau numéro.
            if not live_version(article, day):
                try:
                    cid = str(article.get("cid") or ident)
                    versions = client.versions(cid) if callable(getattr(client, "versions", None)) else flatten_versions(client.call("consult/getArticleByCid", {"cid": cid}))
                    live = [v for v in versions if live_version(v, day)]
                    if live:
                        article = client.article(live[0]["id"])
                except APIError:
                    pass
            current = live_version(article, day)
            new_text = comparable_content(article)
            identity = bool(current and old_text and new_text and old_text == new_text)
            similarity, excerpt_old, excerpt_new = None, "", ""
            if old_text and new_text:
                if old_text == new_text:
                    similarity = 100.0
                else:
                    # Comparaison indicative, plafonnée pour rester rapide sur les articles très longs.
                    matcher = difflib.SequenceMatcher(None, old_text[:5000], new_text[:5000], autojunk=True)
                    similarity = round(matcher.ratio()*100, 1)
                    for operation, i1, i2, j1, j2 in matcher.get_opcodes():
                        if operation != "equal":
                            excerpt_old = old_text[max(0,i1-75):min(len(old_text),max(i2,i1+1)+75)]
                            excerpt_new = new_text[max(0,j1-75):min(len(new_text),max(j2,j1+1)+75)]
                            break
                    if not excerpt_old and not excerpt_new:
                        excerpt_old = old_text[:200]
                        excerpt_new = new_text[:200]
            results.append({"id": article.get("id") or ident, "numero": article.get("num"),
                            "nature": item.get("nature") or ("CODE" if article_code_titles(article) and any("code" in t.lower() for t in article_code_titles(article)) else "LODA"),
                            "parcours": item.get("parcours", []),
                            "enVigueur": current, "identique": identity, "similarite": similarity,
                            "texteDisponible": bool(old_text and new_text),
                            "extraitAncien": excerpt_old, "extraitNouveau": excerpt_new})
        except (APIError, KeyError):
            results.append({"id": ident, "enVigueur": False, "identique": False,
                            "similarite": None, "texteDisponible": False})
    # Déduplique les identifiants *courants* ; il peut exister plusieurs concordances vers le même article.
    results = list({x["id"]: x for x in results}.values())
    output["candidats"] = results
    live = [x for x in results if x["enVigueur"]]
    if len(live) == 1 and len(results) == 1 and live[0]["identique"]:
        output.update({"statut": "proposition", "libelle": "Correction proposée · rédaction identique",
                       "id": live[0]["id"], "nature": live[0].get("nature", ""), "identique": True})
    elif len(live) == 1 and len(results) == 1:
        output.update({"statut": "verification", "libelle": "Correction à vérifier · rédaction différente ou indisponible",
                       "id": live[0]["id"], "nature": live[0].get("nature", ""), "identique": False})
    elif live:
        output.update({"statut": "ambigu", "libelle": "Plusieurs concordances · pas de correction automatique"})
    return output


def inspect_reference(client: PisteClient, entry: dict, today: dt.date) -> dict:
    res = dict(entry)
    res.update({"etat": "", "statut": "indetermine", "raison": "État non vérifié.",
                "dateFin": None, "dateDebut": None, "versionActuelle": None,
                "concordances": [], "suggestion": {"statut": "aucune", "libelle": "Aucune correction proposée"}})
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
                version_info = propose_replacement(client, target, [{"id": winning["id"]}], today)
                version_info.update({"statut": "version", "libelle": "Version actuelle du même article · aucun changement de référence nécessaire", "id": winning["id"], "source": "Historique des versions Légifrance"})
                res["suggestion"] = version_info
            else:
                res["statut"] = "vigoureux"
                res["raison"] = "Article actuellement en vigueur, confirmé par son historique."
            return res
        end = parsed_date(target.get("dateFin"))
        if state.startswith(("ABROGE", "REMPLACE", "PERIME", "TRANSFERE")) and (end is None or end <= today) and effective_period(target):
            res["statut"] = "alerte"
            if state.startswith("ABROGE"):
                res["raison"] = ("Article abrogé" + (f" depuis le {end.strftime('%d/%m/%Y')}" if end else "") +
                                  "; aucune version courante confirmée. Renvoi à examiner juridiquement.")
            else:
                res["raison"] = "Article cité indiqué comme remplacé ou transféré ; aucune version courante confirmée. Vérifier le renvoi et son contexte."
        elif end and end <= today and state.startswith("MODIFIE"):
            res["statut"] = "indetermine"
            res["raison"] = "Version modifiée et terminée, mais la continuité de l'article n'a pas pu être établie."
        else:
            res["statut"] = "indetermine"
            res["raison"] = "Statut non concluant : date d'entrée en vigueur future, historique incomplet ou état non reconnu."
        if res["statut"] != "vigoureux":
            trace = discover_successors(client, target, today, versions)
            res["rechercheSuccesseur"] = {k: trace[k] for k in ("appels", "partiel", "versionsExaminees", "erreurs")}
            res["concordances"] = trace["candidats"]
            if trace["candidats"]:
                res["raison"] += " Concordance officielle vers un article en vigueur trouvée."
                suggestion = propose_replacement(client, target, trace["candidats"], today)
                # Une recherche tronquée ou échouée peut masquer d'autres
                # successeurs : jamais une substitution unique dans ce cas.
                if trace["partiel"]:
                    suggestion.pop("id", None)
                    suggestion.update({"statut": "ambigu", "libelle":
                                      "Correspondance(s) trouvée(s), recherche incomplète · à vérifier"})
                res["suggestion"] = suggestion
            elif trace["partiel"]:
                res["raison"] += " Recherche de concordances incomplète (quota, erreur ou limite)."
            else:
                res["raison"] += " Aucune correspondance officielle vers un article en vigueur trouvée."
    except APIError as exc:
        res["raison"] = exc.message
    return res


def analyze(client: PisteClient, value: str, today: dt.date | None = None, mode: str = "actuel", *, bulk: bool = False) -> dict:
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
    raw_source = str(original.get("texteHtml") or original.get("texte") or original.get("content") or "")
    _, range_warnings = textual_mentions(cohesive_text(raw_source))
    warnings.extend(range_warnings)
    entries, unresolved, lookup_warnings = resolve_textual_references(client, original, entries, unresolved, today, bulk)
    warnings.extend(x for x in lookup_warnings if x not in warnings)
    entries, unresolved, loda_warnings = resolve_normative_references(client, original, entries, unresolved, today, bulk)
    warnings.extend(x for x in loda_warnings if x not in warnings)
    total = len(entries)
    ref_limit = min(MAX_REFERENCES, BULK_REF_LIMIT) if bulk else MAX_REFERENCES
    if total > ref_limit:
        warnings.append(f"Seuls les {ref_limit} premiers renvois identifiés ont été vérifiés (sur {total}). Résultat partiel.")
        entries = entries[:ref_limit]
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        checked = list(pool.map(lambda entry: inspect_reference(client, entry, today), entries))
    checked.sort(key=lambda x: {"alerte": 0, "indetermine": 1, "ancienne_version": 2, "vigoureux": 3}.get(x["statut"], 4))
    # Même critère pour un article isolé ET pour les lots : un lien indexé
    # sans preuve dans la rédaction ne gonfle jamais les statistiques.
    decorated, colored, actual_ids = render_annotated_html(original, checked)
    if bulk:
        decorated = ""  # On ne renvoie pas le HTML lourd dans les lots.
    index_only = [x for x in checked if x["id"] not in actual_ids]
    in_text = [x for x in checked if x["id"] in actual_ids]
    # Même extraction que le surlignage, sur le texte global : signale aussi
    # une loi citée au travers de plusieurs <span>/<a>.
    if not bulk:
        flat = cohesive_text(raw_source)
        present_keys = {(str(x.get("numero")), str(x.get("texteNumero")), str(x.get("typeTexte")))
                        for x in in_text if x.get("texteNumero")}
        for m in normative_mentions(flat):
            key = (m["numero"], m["texteNumero"], m["typeTexte"])
            if key not in present_keys and m["raw"] not in unresolved:
                unresolved.append(m["raw"])
        if index_only:
            warnings.append(f"{len(index_only)} lien(s) de l'indexation Légifrance sans citation textuelle confirmée : présentés séparément, non comptés comme anomalies du texte.")
        if unresolved:
            warnings.append(f"{len(unresolved)} référence(s) textuelle(s) non résolue(s) : affichées en violet (statut inconnu).")
    counts = {key: sum(x["statut"] == key for x in in_text) for key in ("alerte", "indetermine", "ancienne_version", "vigoureux")}
    source_nature = "CODE" if "/codes/" in value.lower() else ("LODA" if "/loda/" in value.lower() else "CODE")
    return {"article": article_summary(original), "articleDemandee": article_summary(supplied),
            "sourceNature": source_nature, "versionChangee": selected_id != source_id, "mode": mode,
            "sourceHtmlAnnote": decorated, "renvoisSurlignes": colored,
            "dateAnalyse": today.isoformat(), "versionMoteur": APP_VERSION,
            "renvois": in_text, "liensIndexationSeule": index_only,
            "correctionsDocumentees": [],
            "totalIdentifies": total, "comptes": counts,
            "mentionsNonResolues": unresolved, "avertissements": warnings,
            "limite": ref_limit}


def parse_scope(value: str, kind: str) -> tuple[str, str | None]:
    """Scope via URL Légifrance : titre = LEGITEXT + LEGISCTA."""
    text_id = TEXT_RE.search(value or "")
    section_id = SECTION_RE.search(value or "")
    if not text_id:
        raise APIError("Collez l'URL d'un code contenant son identifiant LEGITEXT.", 400)
    if kind == "section" and not section_id:
        raise APIError("Pour un titre ou une section, collez l'URL Légifrance contenant LEGITEXT et LEGISCTA.", 400)
    return text_id.group().upper(), section_id.group().upper() if section_id else None


def list_articles_in_tree(root: dict, day: dt.date, section_id: str | None = None) -> dict:
    """Construit une liste déterministe depuis l'arborescence API, sans inventer des articles."""
    seen: set[str] = set()
    articles: list[dict] = []
    sections: list[dict] = []
    matched = section_id is None
    found_section = False

    def record_article(a: dict, path: list[str]):
        ident = str(a.get("id") or "").upper()
        if not ARTICLE_RE.fullmatch(ident) or ident in seen:
            return
        state = normalize_state(a)
        if state.startswith(("ABROGE", "PERIME", "TRANSFERE", "REMPLACE")):
            return
        if (a.get("dateDebut") or a.get("dateFin")) and not active_on(a, day):
            return
        seen.add(ident)
        articles.append({"id": ident, "numero": str(a.get("num") or ""), "chemin": " / ".join(path)})

    def walk(node: dict, path: list[str], within: bool, depth: int):
        nonlocal matched, found_section
        if depth > 35 or not isinstance(node, dict):
            return
        ident = str(node.get("cid") or node.get("id") or "").upper()
        title = str(node.get("title") or node.get("titre") or "Section")
        is_match = bool(section_id and (section_id == ident or section_id == str(node.get("id") or "").upper()))
        if is_match:
            found_section = True
        take = within or is_match or section_id is None
        children = node.get("sections") or []
        nested_path = path + ([title] if title else [])
        if section_id is None or take or depth == 0:
            sections.append({"id": ident, "titre": title, "niveau": depth, "actif": take})
        if take:
            for a in node.get("articles") or []:
                if isinstance(a, dict):
                    record_article(a, nested_path)
        for child in children:
            walk(child, nested_path, take, depth + 1)

    # Root contains first-level sections and possibly articles.
    if section_id is None:
        for a in root.get("articles") or []:
            if isinstance(a, dict):
                record_article(a, [])
    for child in root.get("sections") or []:
        walk(child, [], section_id is None, 0)
    if section_id is not None and not found_section:
        raise APIError("Section LEGISCTA introuvable dans le sommaire de ce code à la date du jour.", 404)
    return {"articles": articles, "sections": sections, "nombre": len(articles), "titre": root.get("title") or "Code"}


def get_outline(client: PisteClient, value: str, kind: str, day: dt.date | None = None) -> dict:
    day = day or dt.date.today()
    text_id, section_id = parse_scope(value, kind)
    req = {"date": day.isoformat() + "T00:00:00.000Z", "textId": text_id, "nature": "CODE"}
    tree = client.call("consult/legi/tableMatieres", req)
    if not isinstance(tree, dict):
        raise APIError("Le sommaire renvoyé par PISTE est inexploitable.", 502)
    outline = list_articles_in_tree(tree, day, section_id)
    # Ne pas afficher un total rassurant si le sommaire est incomplet.
    if not outline["nombre"]:
        raise APIError("Aucun article en vigueur n'a pu être énuméré dans ce périmètre. Vérifiez l'URL et le sommaire Légifrance.", 422)
    return {**outline, "textId": text_id, "sectionId": section_id, "date": day.isoformat(), "type": kind}


class AuditJob:
    def __init__(self, ids: list[str], offset: int, count: int):
        self.id = uuid.uuid4().hex
        self.ids = ids[offset:offset+count]
        self.offset = offset
        self.total_scope = len(ids)
        self.next_offset = min(len(ids), offset+count)
        self.progress = 0
        self.results: list[dict] = []
        self.errors: list[dict] = []
        self.done = False
        self.started = time.monotonic()
        self.lock = threading.Lock()

    def status(self) -> dict:
        with self.lock:
            return {"jobId": self.id, "totalLot": len(self.ids), "traites": self.progress,
                    "totalScope": self.total_scope, "offset": self.offset, "nextOffset": self.next_offset,
                    "termine": self.done, "resultats": list(self.results), "erreurs": list(self.errors)}

    def run(self):
        for article_id in self.ids:
            try:
                full = analyze(CLIENT, article_id, mode="actuel", bulk=True)
                relevant = [r for r in full["renvois"] if r["statut"] != "vigoureux"]
                row = {"id": full["article"]["id"], "titre": full["article"]["titre"],
                       "numero": full["article"]["numero"], "comptes": full["comptes"],
                       "totalIdentifies": full["totalIdentifies"], "limite": full["limite"],
                       "renvois": relevant, "mentionsNonResolues": full["mentionsNonResolues"],
                       "avertissements": full["avertissements"]}
                with self.lock:
                    self.results.append(row)
            except Exception as exc:
                with self.lock:
                    # Pas de secret ni d'URL privée dans l'erreur.
                    self.errors.append({"id": article_id, "erreur": exc.message if isinstance(exc, APIError) else "Erreur interne d'analyse"})
            finally:
                with self.lock:
                    self.progress += 1
            time.sleep(0.12)
        with self.lock:
            self.done = True


JOBS: dict[str, AuditJob] = {}
JOBS_LOCK = threading.Lock()


def create_job(ids: list[str], offset: int, count: int) -> AuditJob:
    ids = list(dict.fromkeys(ids))
    if not ids or len(ids) > 50000 or not all(ARTICLE_RE.fullmatch(x) for x in ids):
        raise APIError("Liste d'articles invalide.", 400)
    if offset < 0 or offset >= len(ids):
        raise APIError("Curseur hors de portée.", 400)
    count = min(max(1, count), JOB_BATCH_MAX)
    with JOBS_LOCK:
        for key, job in list(JOBS.items()):
            if job.done and time.monotonic()-job.started > 3600:
                del JOBS[key]
        if any(not job.done for job in JOBS.values()):
            raise APIError("Une analyse de lot est déjà en cours sur cette instance. Réessayez après sa fin.", 409)
        job = AuditJob(ids, offset, count)
        JOBS[job.id] = job
    threading.Thread(target=job.run, daemon=True, name="lexicheck-job").start()
    return job


CLIENT = PisteClient()


class Handler(BaseHTTPRequestHandler):
    def _authorized(self) -> bool:
        expected = os.getenv("APP_ACCESS_PASSWORD", "")
        if not expected:
            return True
        return hmac.compare_digest(self.headers.get("X-LexiCheck-Access", ""), expected)

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
        path = urlsplit(self.path).path
        paths = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                 "/style.css": ("style.css", "text/css; charset=utf-8"),
                 "/mentions-legales": ("mentions-legales.html", "text/html; charset=utf-8")}
        if path == "/api/health":
            return self._json(200, {"status": "ok", "version": APP_VERSION, "configured": bool(CLIENT.client_id and CLIENT.client_secret),
                                    "environment": os.getenv("PISTE_ENV", "production"),
                                    "protected": bool(os.getenv("APP_ACCESS_PASSWORD", ""))})
        if path.startswith("/api/jobs/"):
            if not self._authorized():
                return self._json(401, {"erreur": "Code d'accès invalide."})
            job_id = path[len("/api/jobs/"):]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                return self._json(404, {"erreur": "Analyse introuvable ou perdue après redémarrage du serveur."})
            return self._json(200, job.status())
        if path == "/api/legal":
            return self._json(200, {"editor": os.getenv("SITE_EDITOR_NAME", "À renseigner"),
                     "email": os.getenv("SITE_EDITOR_EMAIL", "À renseigner"),
                     "address": os.getenv("SITE_EDITOR_ADDRESS", "À renseigner"),
                     "phone": os.getenv("SITE_EDITOR_PHONE", "À renseigner")})
        if path not in paths:
            return self._json(404, {"erreur": "Page inconnue."})
        filename, mime = paths[path]
        base = ROOT / "static"
        content = (base / filename).read_bytes()
        self._respond(200, content, mime)

    def do_POST(self):
        if self.path not in {"/api/analyze", "/api/outline", "/api/jobs"}:
            return self._json(404, {"erreur": "Endpoint inconnu."})
        if not self._authorized():
            return self._json(401, {"erreur": "Code d'accès invalide."})
        try:
            count = int(self.headers.get("Content-Length", "0"))
            if count < 2 or count > 3500000:
                raise APIError("Requête vide ou trop volumineuse.", 400)
            body = json.loads(self.rfile.read(count))
            if not isinstance(body, dict):
                raise APIError("Corps JSON attendu.", 400)
            if self.path == "/api/outline":
                if not isinstance(body.get("value"), str) or body.get("type") not in {"code", "section"}:
                    raise APIError("Fournir l'URL d'un code ou d'une section.", 400)
                return self._json(200, get_outline(CLIENT, body["value"], body["type"]))
            if self.path == "/api/jobs":
                ids = body.get("ids")
                if not isinstance(ids, list):
                    raise APIError("Liste d'articles manquante.", 400)
                try:
                    job = create_job(ids, int(body.get("offset", 0)), int(body.get("count", 25)))
                except (TypeError, ValueError):
                    raise APIError("Paramètre de lot invalide.", 400)
                return self._json(202, job.status())
            if not isinstance(body.get("article"), str):
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
