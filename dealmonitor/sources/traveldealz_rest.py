"""Quelle: Travel-Dealz über die WordPress-Schnittstelle (WP-REST) statt über RSS.

Zwei Endpunkte, beide nur Flüge (Kategorie 24 mit Unterkategorien):

* ``/wp-json/wp/v2/deal``   - die Artikel
* ``/wp-json/wp/v2/ticker`` - Kurzmeldungen (stehen *nicht* in ``/deal``)

Statt bis zu 2 MB RSS kommen je Abruf wenige KB JSON (``_fields``). Nachgeholt wird über
``modified_after``; das Ablaufdatum steht in ``meta.suspend_date``, Abflug und Ziel als
Term-IDs (``origins``/``destinations``), deren Namen die IATA-Codes tragen
("Zürich [ZRH]", "Peking [PEK,BJS]"). Einen Preis gibt es nur im Titel.

Loader für ``FeedSource`` (siehe ``feed.py``, Preset ``travel-dealz-rest``)::

    fetch_items(timeout=20.0, state=store.source_state("travel-dealz-rest")) -> list[dict]

``FeedSource`` lässt Deals ablaufen, die in einem Lauf fehlen. Deshalb liefert der Loader
jedes Mal *alle* aktuellen Beiträge, holt aber nur die Änderungen ab. Zustand (``state``,
JSON-tauglich):

* ``cursor``: ``{"deal": ISO, "ticker": ISO}`` - jüngstes ``modified_gmt`` minus 1 min (UTC)
* ``full_at``: letzter vollständiger Abruf (alle 24 h: Fenster ``WINDOW``, ersetzt den Bestand -
  so verschwinden gelöschte Beiträge)
* ``posts``: ``{"deal:123": {...}}`` - der gekürzte Bestand
* ``terms``: ``{"origins": {"3015": ["DUB"]}, "destinations": {...}}`` - nachgeschlagene Term-IDs

Fehler (403/429, Netz, kaputtes JSON) -> ``SourceError``; dann bleibt der Zustand unverändert
und der RSS-Preset ``travel-dealz`` deckt die Seite weiter ab.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from .. import currency, http
from . import SourceError

log = logging.getLogger(__name__)

BASE = "https://travel-dealz.de/wp-json/wp/v2/"
KINDS = ("deal", "ticker")
FLIGHTS = 24
FIELDS = ("id,date_gmt,modified_gmt,date,modified,link,title,categories,origins,destinations,"
          "tags,meta.suspend_date")
#: mehr als 100 je Seite -> HTTP 400; eine Seite über X-WP-TotalPages ebenfalls
PER_PAGE = 100
MAX_PAGES = 5
MAX_BYTES = 2_000_000
#: so weit zurück wird beim vollständigen Abruf geholt (wie ``feed_max_age_days``)
WINDOW = timedelta(days=14)
FULL_EVERY = timedelta(hours=24)
OVERLAP = timedelta(minutes=1)
MAX_POSTS = 1000
MAX_TERMS = 5000

#: Kategorie-IDs -> Namen wie im RSS-Feed (für ``is_flight_item``/``detect_cabin``)
CABIN_CATEGORIES = {1757: "Economy Class", 884: "Business Class", 3225: "Premium Economy",
                    1047: "First Class"}
OTHER_CATEGORIES = {12: "Meilen, Punkte & Status", 13: "Pauschalreisen", 2480: "Kreuzfahrten",
                    41: "Hotels", 2466: "Bahn"}
#: Schlagwörter, die einen Deal als dringend markieren (``hot``)
HOT_TAGS = {94: "ERROR FARE", 3091: "EILMELDUNG"}
#: Bekannte Term-IDs (spart das Nachschlagen); alle anderen über ``/origins``/``/destinations``
KNOWN_TERMS: dict[str, dict[int, tuple[str, ...]]] = {
    "origins": {
        2971: ("ZRH",), 2972: ("BSL", "MLH"), 2973: ("GVA",), 2945: ("HAM",), 2940: ("FRA",),
        2941: ("MUC",), 2942: ("BER",), 2939: ("DUS",), 2965: ("VIE",), 2946: ("STR",),
        2938: ("CGN",), 2957: ("HAJ",), 2959: ("BRE",), 2947: ("NUE",), 2966: ("SZG",),
        2967: ("GRZ",), 2968: ("INN",), 2948: ("LEJ",), 2949: ("DRS",),
    },
    "destinations": {
        112: ("PEK", "BJS"), 292: ("SHA", "PVG"), 2808: ("CAN",), 2537: ("CTU", "TFU"),
        3097: ("CKG",), 3195: ("SZX",), 3169: ("XIY",), 3193: ("HGH",), 14472: ("DLC",),
        14291: ("KMG",), 3017: ("NKG",), 3749: ("URC",), 293: ("HKG",), 371: ("HAM",),
        1828: ("ZRH",),
    },
}
TAXONOMIES = tuple(KNOWN_TERMS)
TERM_CODES_RE = re.compile(r"\[([A-Za-z0-9,\s]+)\]")
IATA_RE = re.compile(r"^[A-Z]{3}$")
#: "ab 745€", "für 1.299 €", "nur 450 CHF" - bei mehreren Preisen zählt der erste
PRICE_RE = re.compile(r"(?:ab|für|nur)\s*(\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+)\s*(€|CHF|\$)", re.I)

try:                                # suspend_date ist Berliner Ortszeit
    from zoneinfo import ZoneInfo
    BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # noqa: BLE001 - ohne tzdata: Winterzeit reicht (höchstens 1 h daneben)
    BERLIN = timezone(timedelta(hours=1))


# -- Hilfen ---------------------------------------------------------------------------------

def _utc(value, tz=timezone.utc) -> datetime | None:
    """ISO-Text ("2026-11-05T10:19:39", auch mit Leerzeichen) -> datetime in UTC; ohne
    Zeitzone gilt ``tz``. Unbrauchbar -> None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    """UTC ohne Zeitzone, wie WordPress es in ``*_gmt`` und ``modified_after`` erwartet."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _ids(value) -> list[int]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, int) and not isinstance(v, bool)]


def title_price(title: str) -> tuple[float, str] | None:
    """Erster Preis der Überschrift als (Betrag, Währung): "ab 1.123€" -> (1123.0, "€")."""
    m = PRICE_RE.search(title or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(".", "").replace(",", ".")), m.group(2).upper()
    except ValueError:
        return None


def suspend_date(post: dict) -> datetime | None:
    """``meta.suspend_date`` ("2026-11-16 23:59:00" oder mit "T", Berliner Zeit) in UTC;
    leer/unbrauchbar = kein Ablaufdatum (Evergreen)."""
    meta = post.get("meta")
    raw = meta.get("suspend_date") if isinstance(meta, dict) else None
    if not isinstance(raw, str):
        return None
    return _utc(raw.replace("T", " ")[:16], BERLIN)


def term_codes(name: str) -> list[str]:
    """IATA-Codes aus einem Term-Namen: "Basel [BSL,MLH,EAP]" -> ["BSL", "MLH", "EAP"]."""
    m = TERM_CODES_RE.search(html.unescape(name or ""))
    if not m:
        return []
    return [c for c in (p.strip().upper() for p in m.group(1).split(",")) if IATA_RE.match(c)]


# -- Abruf ----------------------------------------------------------------------------------

def _get(path: str, params: dict, timeout: float) -> tuple[list, int]:
    """Eine Seite JSON + ``X-WP-TotalPages``. Fehler -> ``SourceError``."""
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE}{path}?{query}"
    try:
        resp = http.request(url, timeout=timeout, retries=1, full=True, error_body=False,
                            max_bytes=MAX_BYTES, max_time=2 * timeout)
    except http.HttpError as e:
        if e.status in (403, 429):
            raise SourceError(f"Travel-Dealz-Schnittstelle gesperrt (HTTP {e.status})") from None
        raise SourceError(f"Travel-Dealz-Schnittstelle nicht erreichbar: {e}") from None
    try:
        data = json.loads(resp.body)
    except ValueError:
        raise SourceError("Travel-Dealz-Schnittstelle: keine gültige JSON-Antwort") from None
    if not isinstance(data, list):
        raise SourceError("Travel-Dealz-Schnittstelle: unerwartete Antwort")
    try:
        pages = int(resp.header("X-WP-TotalPages", "1") or 1)
    except ValueError:
        pages = 1
    return data, max(pages, 1)


def fetch_posts(kind: str, after: datetime, timeout: float = 20.0) -> list[dict]:
    """Flug-Beiträge (``deal``/``ticker``), geändert nach ``after`` - neueste zuerst. Blättert
    höchstens bis ``X-WP-TotalPages`` (darüber antwortet WordPress mit 400) bzw. ``MAX_PAGES``."""
    params = {"categories": FLIGHTS, "per_page": PER_PAGE, "orderby": "modified",
              "order": "desc", "modified_after": _iso(after), "_fields": FIELDS}
    out: list[dict] = []
    page, pages = 1, 1
    while page <= min(pages, MAX_PAGES):
        rows, pages = _get(kind, {**params, "page": page} if page > 1 else params, timeout)
        out += [r for r in rows if isinstance(r, dict)]
        if len(rows) < PER_PAGE:
            break
        page += 1
    return out


def _slim(post: dict, kind: str) -> dict | None:
    """Nur was der Loader braucht (der Bestand liegt im Zustand). Ohne ID/Titel -> None."""
    pid = post.get("id")
    title = post.get("title")
    title = title.get("rendered") if isinstance(title, dict) else title
    if not isinstance(pid, int) or isinstance(pid, bool) or not isinstance(title, str) \
            or not title.strip():
        return None
    # *_gmt ist UTC; fehlt es (ältere Abrufe), gilt date/modified als Berliner Zeit
    published = _utc(post.get("date_gmt")) or _utc(post.get("date"), BERLIN)
    modified = _utc(post.get("modified_gmt")) or _utc(post.get("modified"), BERLIN) or published
    expires = suspend_date(post)
    link = post.get("link")
    return {
        "key": f"{kind}:{pid}", "id": pid, "kind": kind,
        "title": html.unescape(title).strip(),
        "link": link if isinstance(link, str) else "",
        "date": _iso(published) if published else None,
        "modified": _iso(modified) if modified else None,
        "expires": _iso(expires) if expires else None,
        "categories": _ids(post.get("categories")),
        "origins": _ids(post.get("origins")),
        "destinations": _ids(post.get("destinations")),
        "tags": _ids(post.get("tags")),
    }


def _usable(p) -> bool:
    """Gemerkter Beitrag noch brauchbar (Zustand von Hand geändert/alte Form)?"""
    return (isinstance(p, dict) and p.get("kind") in KINDS and isinstance(p.get("id"), int)
            and isinstance(p.get("title"), str) and p.get("key") == f"{p['kind']}:{p['id']}"
            and all(isinstance(p.get(k), list) and p[k] == _ids(p[k])
                    for k in ("categories", "origins", "destinations", "tags"))
            and all(p.get(k) is None or isinstance(p[k], str)
                    for k in ("link", "date", "modified", "expires")))


def resolve_terms(posts, cache: dict, timeout: float = 20.0) -> None:
    """Unbekannte Abflug-/Ziel-IDs über ``/origins``/``/destinations?include=…`` nachschlagen
    und in ``cache`` (``state["terms"]``) merken - höchstens ein Abruf je Taxonomie. Fehler
    sind nicht schlimm: dann zählt der Titel, beim nächsten Lauf wird es erneut versucht."""
    for tax in TAXONOMIES:
        known = cache.get(tax) if isinstance(cache.get(tax), dict) else {}
        if len(known) > MAX_TERMS:
            known = {}
        cache[tax] = known
        missing = sorted({i for p in posts for i in p.get(tax) or ()
                          if i not in KNOWN_TERMS[tax] and str(i) not in known})[:PER_PAGE]
        if not missing:
            continue
        try:
            rows, _ = _get(tax, {"include": ",".join(map(str, missing)), "per_page": PER_PAGE,
                                 "_fields": "id,name"}, timeout)
        except SourceError as e:
            log.info("Travel-Dealz: %s nicht nachgeschlagen: %s", tax, e)
            continue
        found = {r.get("id"): term_codes(str(r.get("name") or "")) for r in rows
                 if isinstance(r, dict)}
        for i in missing:                     # nicht gefunden = keine Codes (nicht neu fragen)
            known[str(i)] = found.get(i, [])


def _codes(ids: list[int], tax: str, cache: dict, *, strict: bool = False) -> list[str]:
    """Codes der IDs. ``strict``: leer, sobald eine ID unbekannt ist oder keinen Code hat
    ("Schweiz", "New York") - ein halber Abflug wäre schlechter als der aus dem Titel."""
    out: list[str] = []
    known = cache.get(tax) if isinstance(cache.get(tax), dict) else {}
    for i in ids:
        codes = KNOWN_TERMS[tax].get(i)
        if codes is None:
            codes = known.get(str(i))
        if not isinstance(codes, (list, tuple)) or not codes:
            if strict:
                return []
            continue
        out += [c for c in codes if isinstance(c, str) and IATA_RE.match(c) and c not in out]
    return out


def to_item(post: dict, terms: dict | None = None) -> dict:
    """Gekürzter Beitrag -> Artikel im Format von ``FeedSource.parse`` plus optionale Schlüssel."""
    terms = terms or {}
    cats = post.get("categories") or []
    cabins = [CABIN_CATEGORIES[c] for c in cats if c in CABIN_CATEGORIES]
    others = [OTHER_CATEGORIES[c] for c in cats if c in OTHER_CATEGORIES]
    # Nur "Flüge" ohne Klasse sind meist Rabatte, Versicherungen, Programme ("AirHelp+ ab
    # 36€ / Jahr") - wie die RSS-Kategorie "Allgemein" kein Flug-Deal
    categories = (["Flüge", *cabins] if cabins else []) + others
    item = {
        "title": post["title"], "link": post.get("link") or "",
        # Geänderte Beiträge sind aktiv (abgelaufene verschwinden aus der Schnittstelle) -
        # also zählt die letzte Änderung, nicht das (oft Monate alte) Erscheinen
        "published": _utc(post.get("modified") or post.get("date")),
        "categories": categories or ["Allgemein"], "description": "", "merchant_price": None,
        "uid": f"{post['id']}:{post.get('modified') or ''}",
    }
    if post.get("expires"):
        item["expires"] = _utc(post["expires"])
    price = title_price(post["title"])
    if price and price[1] != "€":        # €-Preise liest feed.analyze (inkl. Kabinen-Nebenpreisen)
        eur = currency.to_eur(*price)
        if eur:
            item["price_eur"] = eur
    origins = _codes(post.get("origins") or [], "origins", terms, strict=True)
    if origins:                          # leer oder unklar: Abflug aus dem Titel
        item["origin_codes"] = origins
    dests = _codes(post.get("destinations") or [], "destinations", terms)
    if dests:
        item["dest_codes"] = dests
    hot = next((HOT_TAGS[t] for t in post.get("tags") or () if t in HOT_TAGS), None)
    if hot:
        item["hot"] = hot
    return item


# -- Loader ---------------------------------------------------------------------------------

def fetch_items(*, timeout: float = 20.0, state: dict | None = None,
                now: datetime | None = None) -> list[dict]:
    """Alle aktuellen Flug-Beiträge von Travel-Dealz (Deals + Ticker); ``state`` wird
    fortgeschrieben (siehe Moduldoku)."""
    state = state if state is not None else {}
    now = now or datetime.now(timezone.utc)
    posts = state.get("posts") if isinstance(state.get("posts"), dict) else {}
    cursor = state.get("cursor") if isinstance(state.get("cursor"), dict) else {}
    full_at = _utc(state.get("full_at"))
    full = full_at is None or not (now - FULL_EVERY < full_at <= now)
    oldest = now - WINDOW

    fresh: dict[str, dict] = {}
    for kind in KINDS:
        after = None if full else _utc(cursor.get(kind))
        after = max(after, oldest) if after else oldest
        for raw in fetch_posts(kind, after, timeout):
            p = _slim(raw, kind)
            if p:
                fresh[p["key"]] = p
    if full:
        posts = fresh
    else:
        posts = {k: v for k, v in posts.items() if _usable(v)}
        posts.update(fresh)

    def alive(p: dict) -> bool:
        expires, modified = _utc(p.get("expires")), _utc(p.get("modified"))
        return (expires is None or expires >= now) and (modified is None or modified >= oldest)

    kept = sorted((p for p in posts.values() if alive(p)),
                  key=lambda p: p.get("modified") or "", reverse=True)[:MAX_POSTS]
    terms = state.get("terms") if isinstance(state.get("terms"), dict) else {}
    resolve_terms(kept, terms, timeout)

    new_cursor = dict(cursor)
    for kind in KINDS:
        stamps = [m for m in (_utc(p.get("modified")) for p in kept if p["kind"] == kind) if m]
        if stamps:
            new_cursor[kind] = _iso(max(stamps) - OVERLAP)
    state["cursor"] = new_cursor
    state["posts"] = {p["key"]: p for p in kept}
    state["terms"] = terms
    if full:
        state["full_at"] = _iso(now)
    return [to_item(p, terms) for p in kept]
