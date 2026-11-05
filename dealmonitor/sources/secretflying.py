"""Quelle: Secret Flying über Bluesky (Erstmelder für Error Fares und Langstrecken-Deals).

secretflying.com selbst sitzt hinter Cloudflare. Der Bluesky-Kanal des Anbieters ist
öffentlich (ohne Login) und meldet die Deals meist Stunden vor allen anderen Quellen.

* Primärweg: ``app.bsky.feed.getAuthorFeed`` (JSON). Der volle Deal-Link steht in den
  ``facets`` (im Text ist er gekürzt), ``post.uri`` ist die stabile ID (``uid``).
* Fallback: der RSS-Feed des Profils (30 Posts, ohne ``<title>``, Text in ``<description>``).
* Erster Lauf bzw. Lücke seit dem letzten Lauf: bis zu ``MAX_PAGES`` Seiten nachladen
  (``state["newest"]`` = jüngster gesehener Post).

Aus "#Zurich, Switzerland to Mauritius for only €471 roundtrip #Travel" wird ein Artikel im
Format von ``FeedSource.parse`` mit ``price_eur``, ``origin_codes``, ``dest_codes``, ``oneway``,
``hot`` und ``uid`` (siehe ``docs/handoff/RUNDE2-SCHNITTSTELLEN.md``). Gefiltert wird so:

* Hotels ("per night", "5*") und abgelaufene Error Fares fallen weg.
* Abflug DACH oder übriges Europa (Nachbar-Hub): behalten, mit ``origin_codes``. Ein ganzes
  DACH-Land ("#Germany", "Austrian cities") wird zur Region und bekommt ``origin_region``
  (passt dann nur zu Alarmen ab einer Region, nicht zu Zürich/Hamburg). Europa ohne Ort
  ("UK cities", "European cities") bekommt ``origin_foreign`` - nie "Abflug unbekannt".
* Abflug außerhalb Europas: nur mit Marker (ERROR FARE, CRAZY HOT, PRICE DROP) und
  bekanntem Abflug-Code - sonst ginge der Artikel als "Abflug unbekannt" durch jeden Alarm.
* Teaser ("A new Business Class deal from Europe has just been posted"): Artikel ohne
  Preis mit Kategorie ``Business-Teaser``.

Preise in $, £ usw. rechnet ``currency.to_eur`` um. Den Preis nie aus dem Link-Slug lesen
(der Slug nennt oft einen alten Preis).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .. import currency, http, regions
from . import SourceError

log = logging.getLogger(__name__)

ACTOR = "secretflying.bsky.social"
DID = "did:plc:ffurylltuiy7da64xuf5lgne"
API_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
#: DID-Adresse direkt (der Handle-Link leitet per 302 hierher)
RSS_URL = f"https://bsky.app/profile/{DID}/rss"
POST_URL = "https://bsky.app/profile/" + ACTOR + "/post/{rkey}"
PAGE_SIZE = 50
#: so viele Seiten höchstens pro Lauf (150 Posts = ca. 6 Tage)
MAX_PAGES = 3
MAX_BYTES = 2_000_000
#: so viele Deal-Posts merkt sich die Quelle (``state["posts"]``), höchstens so alt
KEEP_POSTS = 100
KEEP_DAYS = 14

HOT_MARKERS = [
    (re.compile(r"error\s*fare", re.I), "ERROR FARE"),
    (re.compile(r"crazy\s*hot", re.I), "CRAZY HOT"),
    (re.compile(r"price\s*drop", re.I), "PRICE DROP"),
]
_HOT_PREFIX = r"(?:error\s*fare|crazy\s*hot|price\s*drop)"
#: Vorspann vor der Strecke: Marker, Reisezeit ("SUMMER:"), "Non-stop from", "We've found …"
PREFIX_RE = re.compile(
    rf"^(?:(?-i:[A-Z]+\s+){{0,3}}{_HOT_PREFIX}[\s!]*:?\s*"     # "BUSINESS CLASS ERROR FARE"
    r"|(?-i:[A-Z][A-Z&\s]{2,30}):\s*"                            # "XMAS & NEW YEAR:"
    r"|non-stop\s+from\s+|we[’']ve\s+found\s+an\s+error\s+fare\s+from\s+)", re.I)
EXPIRED_RE = re.compile(r"\bexpired\b|\bhas\s+(?:finally\s+)?died\b", re.I)
HOTEL_RE = re.compile(r"\bper\s+night\b|(?:^|\s)\d\*\s|\bhotel\b", re.I)
TEASER_RE = re.compile(r"has just been posted|premium members|paid members|"
                       r"just found another error fare", re.I)
TEASER_REGION_RE = re.compile(
    r"\b(?:deal|departing|departs?|flying)\s+from\s+(?:the\s+)?"
    r"(?P<region>[A-Z][\w-]*(?:\s+[A-Z][\w-]*)*)")
CABIN_RE = re.compile(r"\b(business|first|premium\s+economy|economy)\s+class\b", re.I)

#: "Zurich, Switzerland to Mauritius for only €471 roundtrip with Swiss"
DEAL_RE = re.compile(
    r"^(?P<orig>.+?)\s+to\s+(?P<dest>.+?)\s+(?:for|from)(?:\s+only)?\s+"
    r"(?P<sym>(?:US|CA|AU|NZ|HK|S|A|C)?\$|€|£|CHF\s?|EUR\s?|Fr\.\s?)?"
    r"(?P<amt>\d[\d.,]*\d|\d)"
    r"(?:\s?(?P<code>USD|CAD|AUD|NZD|HKD|SGD|EUR|GBP|CHF)\b|\s?(?P<sym2>€))?"
    r"(?P<rest>.*)$")
ONEWAY_RE = re.compile(r"\bone[\s-]?way\b", re.I)
ROUNDTRIP_RE = re.compile(r"\bround[\s-]?trip\b|\breturn\b", re.I)
AIRLINE_RE = re.compile(r"\bwith\s+(?P<airline>.+?)\s*[.!]?\s*$")
URL_RE = re.compile(r"(?:https?://|www\.)\S+")
SF_POST_RE = re.compile(r"^https://(?:www\.)?secretflying\.com/posts/[\w%-]+/?$")
RKEY_RE = re.compile(r"^[\w.~:-]+$")

#: Länder/Gegenden in Europa (englisch, wie im Kanal), DACH zählt über ``regions`` als Heimat
EUROPE_NAMES = frozenset("""
    europe uk england scotland wales ireland france spain portugal italy netherlands belgium
    luxembourg denmark sweden norway finland iceland poland czechia slovakia hungary greece
    turkey croatia slovenia serbia romania bulgaria lithuania latvia estonia malta cyprus albania
    montenegro bosnia moldova scandinavia baltics
""".split()) | {"united kingdom", "czech republic", "north macedonia", "great britain"}
#: europäische Stadtcodes aus ``regions.WORLD_AIRPORTS`` (ohne DACH)
EUROPE_CODES = frozenset("""
    AMS LIS MAD PAR LON MIL ROM ATH BUD WAW PRG BRU CPH STO OSL DUB BCN OPO IST LUX MAN HEL VCE
    LYS NCE KRK GDN EDI RIX VNO TLL SOF BUH BEG ZAG LJU BTS KEF GOT
""".split())
#: ganze DACH-Länder als Abflug ("#Germany to Moroccan cities", "Austrian cities") -> Region
COUNTRY_REGIONS = {"germany": "DE", "deutschland": "DE", "german": "DE",
                   "switzerland": "CH", "schweiz": "CH", "swiss": "CH",
                   "austria": "AT", "oesterreich": "AT", "austrian": "AT", "dach": "DACH"}


# -- Text ------------------------------------------------------------------------------------

def _split_camel(tag: str) -> str:
    """"NewYork" -> "New York", "WashingtonDC" -> "Washington DC"."""
    tag = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", tag)
    return re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", tag)


def _clean(text: str) -> str:
    """Emojis und Hashtag-Zeichen weg, Schlusstags ("#Travel") weg, Leerraum vereinfachen."""
    text = "".join(c for c in text if unicodedata.category(c) not in ("So", "Sk", "Cf", "Cs")
                   and c not in "\ufe0e\ufe0f")
    text = re.sub(r"(?:\s+#\w+)+\s*$", "", " " + text)
    text = re.sub(r"#(\w+)", lambda m: _split_camel(m.group(1)), text)
    return re.sub(r"\s+", " ", text).strip(" -–:")


def _body_lines(text: str) -> list[str]:
    """Textzeilen ohne Links (im API-Text sind die Links gekürzt, im RSS vollständig)."""
    out = []
    for line in (text or "").splitlines():
        line = URL_RE.sub("", line)
        if _clean(line):
            out.append(line.strip())
    return out


def _hot(text: str) -> str | None:
    for rx, label in HOT_MARKERS:
        if rx.search(text):
            return label
    return None


def _strip_prefixes(text: str) -> str:
    while True:
        new = PREFIX_RE.sub("", text, count=1).strip()
        if new == text:
            return text
        text = new


def _amount(text: str) -> float | None:
    """"471" -> 471, "1,134" / "1.134" -> 1134, "29.99" -> 29.99."""
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", text):
        return float(re.sub(r"[.,]", "", text))
    if re.fullmatch(r"\d+(?:[.,]\d{1,2})?", text):
        return float(text.replace(",", "."))
    return None


# -- Orte ------------------------------------------------------------------------------------

def _places(part: str) -> tuple[list[str], str]:
    """"Stockholm or Gothenburg, Sweden" -> (["Stockholm", "Gothenburg"], "Sweden")."""
    part = re.sub(r"\(.*?\)", " ", part)                 # "(& vice versa)"
    part = re.split(r"\s+returning\s+to\s+", part, maxsplit=1)[0]   # Gabelflug
    names, _, country = part.rpartition(",") if "," in part else (part, "", "")
    out = []
    for n in re.split(r"\s+(?:or|and|&)\s+|/|,", names):
        n = re.sub(r"^the\s+", "", n.strip(), flags=re.I)
        n = re.sub(r"\s+(?:cities|airports)$", "", n, flags=re.I)
        if n:
            out.append(n)
    return out, country.strip()


def _codes(name: str) -> set[str]:
    """Codes eines Ortsnamens; kurze Wörter nur als echter Code ("Sao" ist nicht SAO)."""
    for cand in [name] + ([name.split()[0]] if " " in name else []):   # "Washington DC"
        if len(cand) <= 3 and not cand.isupper():
            continue
        codes = regions.codes_for(cand)
        if codes:
            return codes
    return set()


def _is_europe(name: str) -> bool:
    key = regions.normalize_name(name)
    return key in EUROPE_NAMES or key.startswith("europ")


def _classify(part: str) -> tuple[str, set[str], bool]:
    """Wie :func:`classify_origin`, dazu ``True``, wenn die Codes ein ganzes Land sind."""
    names, country = _places(part)
    codes: set[str] = set()
    for n in names:
        codes |= _codes(n)
    places = names + ([country] if country else [])
    countries = [r for n in places if (r := COUNTRY_REGIONS.get(regions.normalize_name(n)))]
    if any(regions.is_home(c) for c in codes) or countries or \
            any(regions.place_kind(n) == "home" for n in places):
        if any(regions.is_home(c) for c in codes):
            return "home", codes, False
        for region in countries:
            codes |= set(regions.REGIONS[region])
        return "home", codes, bool(countries)
    if codes & EUROPE_CODES or any(_is_europe(n) for n in places):
        return "europe", codes, False
    return "far", codes, False


def classify_origin(part: str) -> tuple[str, set[str]]:
    """Abflug -> ("home" | "europe" | "far", IATA-Codes). Ganze DACH-Länder werden zur Region
    ("#Germany", "Austrian cities", auch "Lugano, Switzerland" mit unbekannter Stadt)."""
    kind, codes, _ = _classify(part)
    return kind, codes


def dest_codes(part: str) -> set[str]:
    names, _ = _places(part)
    out: set[str] = set()
    for n in names:
        out |= _codes(n)
    return out


# -- Posts -> Artikel ------------------------------------------------------------------------

def _post_link(uri: str) -> str:
    rkey = uri.rsplit("/", 1)[-1] if uri.startswith("at://") else ""
    return POST_URL.format(rkey=rkey) if rkey and RKEY_RE.match(rkey) else ""


def _deal_link(links: list[str]) -> str:
    for u in links:
        u = u.strip().rstrip(".,)")
        if u.startswith("www."):
            u = "https://" + u
        u = u.replace("http://", "https://", 1)
        if SF_POST_RE.match(u):
            return u
    return ""


def _title(headline: str, hot: str | None) -> str:
    title = re.sub(rf"^(?:{_HOT_PREFIX}\s*:?\s*)+", "", _clean(headline), flags=re.I)
    if hot and hot.lower() not in title.lower():
        title = f"{hot}: {title}"
    return title


def _base(post: dict, title: str, hot: str | None, lines: list[str]) -> dict:
    uri = post.get("uri") or ""
    body = "\n".join(lines)
    item = {
        "title": title,
        "link": _deal_link(post.get("links") or []) or _post_link(uri),
        "published": post.get("created"),
        "categories": ["Flights"],
        "description": body,
        "description_text": body,
        "merchant_price": None,
        "uid": uri or None,
    }
    if hot:
        item["hot"] = hot
    return item


def _teaser(post: dict, lines: list[str], hot: str | None) -> dict | None:
    """Premium-Hinweis ohne Preis: nur, wenn der Deal ab Europa/DACH geht."""
    cleaned = [_clean(line) for line in lines]
    found = next(((line, m) for line in cleaned for m in [TEASER_REGION_RE.search(line)] if m),
                 None)
    if found is None:
        return None
    line, m = found
    kind, codes, country = _classify(m.group("region"))
    if kind == "far":
        return None
    cabin_m = CABIN_RE.search(" ".join(cleaned))
    cabin = cabin_m.group(1).lower().split()[0] if cabin_m else None
    title = cleaned[0] if line == cleaned[0] else f"{cleaned[0]} – {line}"
    item = _base(post, _title(title, hot), hot, lines)
    item["link"] = _post_link(post.get("uri") or "") or item["link"]   # kein Deal-Link
    item["categories"].append("Business-Teaser" if cabin == "business" else "Teaser")
    if cabin:
        item["cabin"] = cabin
    if kind == "home" and codes:
        item["origin_codes"] = codes
        if country:
            item["origin_region"] = True
    return item


def post_item(post: dict) -> dict | None:
    """Ein Post (``{"text", "created", "uri", "links"}``) -> Artikel, ``None`` = kein Deal."""
    lines = _body_lines(post.get("text") or "")
    if not lines:
        return None
    headline = lines[0]
    hot = _hot(headline)
    if EXPIRED_RE.search(headline):
        return None
    text = _strip_prefixes(_clean(headline))
    m = DEAL_RE.match(text)
    if m is None:
        return _teaser(post, lines, hot) if TEASER_RE.search(" ".join(lines)) else None
    if HOTEL_RE.search(text):
        return None
    kind, codes, country = _classify(m.group("orig"))
    if kind == "far" and not (hot and codes):
        return None
    if kind == "home" and not codes:
        return None     # DACH genannt, aber kein Ort erkannt: sonst "Abflug unbekannt"
    item = _base(post, _title(headline, hot), hot, lines)
    if codes:
        item["origin_codes"] = codes
    else:
        item["origin_foreign"] = True    # "UK cities", "European cities": Europa, nicht DACH
    if country:
        item["origin_region"] = True     # ganzes Land: passt nur zu Alarmen ab einer Region
    dest = dest_codes(m.group("dest"))
    if dest:
        item["dest_codes"] = dest
    cur = m.group("code") or (m.group("sym") or m.group("sym2") or "").strip()
    amount = _amount(m.group("amt"))
    price = currency.to_eur(amount, cur) if amount is not None and cur else None
    if price:
        item["price_eur"] = price
    rest = m.group("rest")
    if ONEWAY_RE.search(rest):
        item["oneway"] = True
    elif ROUNDTRIP_RE.search(rest) or re.search(r"\breturning\s+to\b", m.group("dest")):
        item["oneway"] = False
    cabin_m = CABIN_RE.search(_clean(headline))
    if cabin_m and cabin_m.group(1).lower().startswith(("business", "first", "premium")):
        item["cabin"] = cabin_m.group(1).lower().split()[0]
    airline = AIRLINE_RE.search(rest)
    if airline:
        item["airline"] = airline.group("airline")
    return item


# -- Bluesky-Antworten lesen -----------------------------------------------------------------

def _aware(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _dicts(value) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def parse_api(data) -> tuple[list[dict], str | None]:
    """getAuthorFeed-Antwort -> (Posts, Cursor). Kaputte Einträge werden übersprungen."""
    feed = data.get("feed") if isinstance(data, dict) else None
    if not isinstance(feed, list):
        raise SourceError("Bluesky-API: unerwartete Antwort (kein 'feed')")
    posts = []
    for entry in feed:
        try:
            post = entry["post"]
            record = post["record"]
            reason = str((entry.get("reason") or {}).get("$type") or "")
            if reason.endswith("reasonRepost"):
                continue                                   # fremder Inhalt
            links = [f["uri"] for fc in _dicts(record.get("facets"))
                     for f in _dicts(fc.get("features"))
                     if str(f.get("$type", "")).endswith("#link") and isinstance(f.get("uri"), str)]
            posts.append({
                "text": str(record.get("text") or ""),
                "created": _aware(record.get("createdAt")) or _aware(post.get("indexedAt")),
                "uri": str(post.get("uri") or ""),
                "links": links,
                "pinned": reason.endswith("reasonPin"),
            })
        except (AttributeError, KeyError, TypeError):
            continue
    cursor = data.get("cursor")
    return posts, cursor if isinstance(cursor, str) and cursor else None


def _rss_date(text: str | None) -> datetime | None:
    """"05 Nov 2026 10:27 +0000" (ohne Wochentag und Sekunden)."""
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.strptime(text.strip(), "%d %b %Y %H:%M %z")
        except ValueError:
            return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def parse_rss(xml: bytes) -> list[dict]:
    """Bluesky-Profil-RSS -> Posts (wie :func:`parse_api`)."""
    root = ET.fromstring(xml)
    if root.find("channel") is None:
        raise SourceError("Bluesky-RSS: kein RSS-Kanal")
    posts = []
    for item in root.iter("item"):
        text = item.findtext("description") or ""
        uri = (item.findtext("guid") or "").strip()
        if not uri.startswith("at://"):
            rkey = (item.findtext("link") or "").rstrip("/").rsplit("/", 1)[-1]
            uri = f"at://{DID}/app.bsky.feed.post/{rkey}" if RKEY_RE.match(rkey or "") else ""
        posts.append({"text": text, "created": _rss_date(item.findtext("pubDate")), "uri": uri,
                      "links": URL_RE.findall(text), "pinned": False})
    return posts


# -- Abruf -----------------------------------------------------------------------------------

def _fetch_api(timeout: float, since: datetime | None) -> tuple[list[dict], bool]:
    """Seite 1, bei Lücke (oder ohne ``since``) weitere Seiten. -> (Posts, vollständig?)."""
    posts: list[dict] = []
    cursor = None
    for page in range(MAX_PAGES):
        params = {"actor": ACTOR, "limit": PAGE_SIZE, "filter": "posts_no_replies",
                  "cursor": cursor}
        try:
            raw = http.request(API_URL, params=params, timeout=timeout, error_body=False,
                               max_bytes=MAX_BYTES)
            try:
                data = json.loads(raw)
            except ValueError as e:
                raise SourceError(f"Bluesky-API: ungültiges JSON ({e})") from None
            batch, cursor = parse_api(data)
        except (http.HttpError, SourceError) as e:
            if page == 0:
                raise
            log.warning("Secret Flying: Seite %d nicht lesbar: %s", page + 1, e)
            return posts, False
        posts += batch
        dated = [p["created"] for p in batch if p["created"] and not p["pinned"]]
        if not cursor or not dated:
            break
        if since is not None and min(dated) <= since:
            break                                   # Anschluss an den letzten Lauf erreicht
    return posts, True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cached(state: dict) -> list[dict]:
    """Gemerkte Posts aus ``state["posts"]`` (Kaputtes wird still verworfen)."""
    out = []
    raw = state.get("posts")
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict) or not isinstance(p.get("uri"), str) \
                or not isinstance(p.get("text"), str):
            continue
        links = p.get("links") if isinstance(p.get("links"), list) else []
        out.append({"text": p["text"], "created": _aware(p.get("created")), "uri": p["uri"],
                    "links": [u for u in links if isinstance(u, str)], "pinned": False})
    return out


def _item(post: dict) -> dict | None:
    try:
        return post_item(post)
    except Exception as e:  # noqa: BLE001 - ein seltsamer Post darf den Abruf nicht stoppen
        log.warning("Secret Flying: Post %s übersprungen: %s", post.get("uri"), e)
        return None


def fetch_items(*, timeout: float = 20.0, state: dict | None = None) -> list[dict]:
    """Loader für ``FeedSource`` (Preset ``secretflying``). Fehler -> ``SourceError``.

    ``state``: ``newest`` = jüngster gelesener Post (ISO), ``via`` = "api" oder "rss",
    ``posts`` = die letzten Deal-Posts (bis ``KEEP_POSTS``/``KEEP_DAYS``). Die Seite 1 der API
    reicht nur ca. 2 Tage zurück; ohne das Gedächtnis würden ältere, noch gültige Deals beim
    nächsten Lauf als "abgelaufen" gelten."""
    state = state if state is not None else {}
    complete = False
    try:
        posts, complete = _fetch_api(timeout, _aware(state.get("newest")))
        state["via"] = "api"
    except (http.HttpError, SourceError) as e:
        log.warning("Secret Flying: Bluesky-API nicht lesbar (%s), nehme RSS", e)
        try:
            posts = parse_rss(http.request(RSS_URL, timeout=timeout, error_body=False,
                                           max_bytes=MAX_BYTES))
        except (http.HttpError, ET.ParseError, SourceError) as e2:
            raise SourceError(f"Bluesky-API: {e}; RSS: {e2}") from None
        state["via"] = "rss"
    newest = max((p["created"] for p in posts if p["created"] and not p["pinned"]), default=None)
    cutoff = _now() - timedelta(days=KEEP_DAYS)
    merged: dict[str, dict] = {p["uri"]: p for p in _cached(state)
                               if p["created"] is not None and p["created"] >= cutoff}
    merged.update((p["uri"], p) for p in posts if p["uri"])     # frisch gelesen gewinnt
    kept: list[tuple[dict, dict]] = []
    for post in sorted(merged.values(), key=lambda p: p["created"] or cutoff, reverse=True):
        item = _item(post)
        if item:
            kept.append((post, item))
    kept = kept[:KEEP_POSTS]
    state["posts"] = [{"uri": p["uri"], "text": p["text"], "links": p["links"],
                       "created": p["created"].isoformat() if p["created"] else None}
                      for p, _ in kept]
    if complete and newest is not None:     # bei RSS/Teilabruf: nächstes Mal nachladen
        old = _aware(state.get("newest"))
        if old is None or newest > old:
            state["newest"] = newest.isoformat()
    return [item for _, item in kept]
