"""Quelle: ErrorFareAlerts.com (deutschsprachige Flug-Deals, Abflug immer DACH).

Die Seite hat keinen Feed (``/rss`` und ``/feed`` leiten auf die Startseite um), die Liste
``/deals/flights`` ist aber schlichtes HTML mit 10 Karten je Seite. Sie ist langsamer als
Secret Flying und travel-dealz (keine Frühwarnung), bringt aber Business-Preise ab DACH nach.

Eine Karte liefert: Titel (``<h3 class="mb-1">``, ohne "✈️"), Link ``/deals/flights/<slug>``
(Slug mit Emojis -> kodiert), Zeit "05.11.2026 05:46", Abflug/Ziel als IATA-Code in
Klammern ("Frankfurt Flughafen (FRA)"; weitere Abflüge aus dem Titel) und den Preis: aus
dem Titel ("ab 1.959 €"), sonst aus dem Preiskasten (``</sup> 1059``). Den Preis nie aus dem
Slug lesen.

Seiten zählen ab 1 (``?page=0`` liefert HTTP 500, live geprüft 05.11.2026). Historie je
Strecke: ``route_url("FRA", "PEK")``.
"""

from __future__ import annotations

import html as html_mod
import logging
import re
import unicodedata
import urllib.parse
from datetime import datetime, timedelta, timezone

from .. import http
from . import SourceError, feed

log = logging.getLogger(__name__)

BASE = "https://errorfarealerts.com"
LIST_URL = BASE + "/deals/flights"
MAX_BYTES = 2_000_000
#: so viele Deals merkt sich die Quelle (``state["deals"]``), höchstens so alt
KEEP_DEALS = 60
KEEP_DAYS = 14
#: Plausible Preisspanne in € (alles andere ist ein Lesefehler)
MIN_PRICE, MAX_PRICE = 10.0, 30_000.0

CARD_RE = re.compile(r'class="[^"]*\bstrip_all_tour_list\b')
TITLE_RE = re.compile(r'<h3 class="mb-1">([^<]*)</h3>')
LINK_RE = re.compile(r'href="((?:https?://(?:www\.)?errorfarealerts\.com)?/deals/flights/[^"?#]+)"')
DATE_RE = re.compile(r"(\d\d)\.(\d\d)\.(\d{4}) (\d\d):(\d\d)")
DESC_RE = re.compile(r'<p class="text-small">([^<]*)</p>')
FROM_RE = re.compile(r"<strong>\s*Von\s*</strong>([^<]*)")
TO_RE = re.compile(r"<strong>\s*nach\s*</strong>([^<]*)")
IATA_RE = re.compile(r"\(([A-Z]{3})\)")
TITLE_PRICE_RE = re.compile(r"\bab\s+(\d[\d.]*)\s*€")
BOX_PRICE_RE = re.compile(r"</sup>\s*(\d[\d.]*)")
ERROR_FARE_RE = re.compile(r"error[\s-]*fare|fehlertarif|preisfehler", re.I)


def page_url(page: int = 1) -> str:
    """Listenseite (ab 1). Seite 1 ohne Parameter - ``?page=0`` antwortet mit HTTP 500."""
    return LIST_URL if page <= 1 else f"{LIST_URL}?page={page}"


def route_url(from_iata: str, to_iata: str, page: int = 1) -> str:
    """Alle Deals einer Strecke (auch abgelaufene) - zum Nachladen der Historie."""
    q = {"fromIata": from_iata.upper(), "toIata": to_iata.upper()}
    if page > 1:
        q["page"] = page
    return f"{LIST_URL}?{urllib.parse.urlencode(q)}"


# -- Zeit ------------------------------------------------------------------------------------

#: Ortszeit Europe/Berlin -> UTC (liegt in ``feed``, auch für das Buchungsende der Infobox)
berlin_to_utc = feed.berlin_to_utc


def parse_time(text: str, now: datetime | None = None) -> datetime | None:
    """"04.11.2026 07:46" -> datetime (UTC).

    UNBESTÄTIGT, welche Zeitzone die Liste meint: Die Detailseite nennt dieselbe Uhrzeit als
    UTC (``article:published_time`` "…T07:46:40Z"), die Nachrecherche hielt sie für Ortszeit.
    Wir lesen sie als Ortszeit Europe/Berlin - das ist die vorsichtige Wahl: Stimmt UTC, wirkt
    der Deal nur 1-2 h älter; umgekehrt läge er bis zu 2 h in der Zukunft. Zeiten in der Zukunft
    werden zusätzlich auf ``now`` gekappt."""
    m = DATE_RE.search(text or "")
    if not m:
        return None
    d, mo, y, h, mi = (int(x) for x in m.groups())
    try:
        utc = berlin_to_utc(datetime(y, mo, d, h, mi))
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    return min(utc, now)


# -- Karten ----------------------------------------------------------------------------------

def _text(raw: str) -> str:
    return re.sub(r"\s+", " ", html_mod.unescape(raw)).strip()


def clean_title(raw: str) -> str:
    """Titel ohne führende Emojis/Flaggen ("✈️ ", "🇨🇳 ")."""
    title = _text(raw)
    i = 0
    while i < len(title) and (unicodedata.category(title[i]) in ("So", "Sk", "Cf", "Mn", "Zs")):
        i += 1
    return title[i:].strip(" -–:")


def _amount(text: str) -> float | None:
    """"1.959" -> 1959.0, "471" -> 471.0; außerhalb der Spanne -> None."""
    digits = text.rstrip(".")
    if not re.fullmatch(r"\d{1,3}(?:\.\d{3})+|\d+", digits):
        return None
    value = float(digits.replace(".", ""))
    return value if MIN_PRICE <= value <= MAX_PRICE else None


def _price(title: str, card: str) -> float | None:
    m = TITLE_PRICE_RE.search(title)
    price = _amount(m.group(1)) if m else None
    if price is None:
        m = BOX_PRICE_RE.search(card)
        price = _amount(m.group(1)) if m else None
    return price


def _link(href: str) -> str:
    """Absoluter Link; Emojis und Sonderzeichen im Slug prozentkodiert."""
    url = urllib.parse.urljoin(BASE + "/", html_mod.unescape(href))
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(parts._replace(path=urllib.parse.quote(parts.path, safe="/%")))


def _codes(rx: re.Pattern, card: str) -> list[str]:
    out: list[str] = []
    for line in rx.findall(card):
        for code in IATA_RE.findall(line):
            if code not in out:
                out.append(code)
    return out


def parse_card(card: str, now: datetime | None = None) -> dict | None:
    """Eine Karte -> Artikel (Format wie ``FeedSource.parse`` plus ``price_eur``,
    ``origin_codes``, ``dest_codes``, ``hot``); ohne Titel oder Link -> None."""
    m_title, m_link = TITLE_RE.search(card), LINK_RE.search(card)
    if not m_title or not m_link:
        return None
    title = clean_title(m_title.group(1))
    if not title:
        return None
    m_desc = DESC_RE.search(card)
    desc = _text(m_desc.group(1)) if m_desc else ""
    item = {
        "title": title,
        "link": _link(m_link.group(1)),
        "published": parse_time(card, now),
        "categories": [],
        "description": desc,
        "description_text": desc,
        "merchant_price": None,
    }
    price = _price(title, card)
    if price is not None:
        item["price_eur"] = price
    origins, dests = _codes(FROM_RE, card), _codes(TO_RE, card)
    # Die Karte nennt nur den ersten Abflug ("Von Hamburg und Berlin" -> BER): Titel ergänzen
    if feed.detect_origin(title, "de") == "home":
        origins += sorted(set(feed.origin_codes(title, "de")) - set(origins))
    if origins:
        item["origin_codes"] = origins
    if dests:
        item["dest_codes"] = dests
    if ERROR_FARE_RE.search(title):
        item["hot"] = "ERROR FARE"
    return item


def parse(page: str, now: datetime | None = None) -> list[dict]:
    """Listenseite -> Artikel. Kaputte Karten fallen still weg."""
    items, seen = [], set()
    for card in CARD_RE.split(page or "")[1:]:
        try:
            item = parse_card(card, now)
        except Exception as e:          # nie wegen einer Karte die ganze Quelle verlieren
            log.debug("ErrorFareAlerts: Karte übersprungen (%s)", e)
            continue
        if item and item["link"] not in seen:
            seen.add(item["link"])
            items.append(item)
    return items


# -- Abruf -----------------------------------------------------------------------------------

def _get(url: str, timeout: float) -> str:
    try:
        raw = http.request(url, timeout=timeout, retries=1, max_bytes=MAX_BYTES,
                           error_body=False)
    except http.HttpError as e:
        raise SourceError(f"ErrorFareAlerts nicht erreichbar: {e}") from None
    return raw.decode("utf-8", errors="replace")


def fetch_page(url: str, *, timeout: float = 20.0) -> list[dict]:
    """Eine Listenseite laden und lesen. Keine Karte gefunden -> ``SourceError``
    (Seitenaufbau geändert), damit die Quelle in der Gesundheit auffällt."""
    page = _get(url, timeout)
    items = parse(page)
    if not items:
        raise SourceError("ErrorFareAlerts: keine Deals auf der Seite (Aufbau geändert?)")
    return items


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _remembered(state: dict, cutoff: datetime) -> list[dict]:
    """Gemerkte Deals aus ``state["deals"]`` (Kaputtes und zu Altes fällt still weg)."""
    out = []
    raw = state.get("deals")
    for d in raw if isinstance(raw, list) else []:
        if not isinstance(d, dict) or not isinstance(d.get("title"), str) \
                or not isinstance(d.get("link"), str):
            continue
        published = feed._aware(d.get("published"))
        if published is None or published < cutoff:
            continue
        out.append(dict(d, published=published))
    return out


def fetch_items(*, timeout: float = 20.0, state: dict | None = None) -> list[dict]:
    """Loader für ``FeedSource``: die neuesten 10 Deals (Seite 1) plus die gemerkten Deals
    der letzten ``KEEP_DAYS`` Tage (``state["deals"]``). Seite 1 reicht nur 2-3 Tage zurück;
    ohne das Gedächtnis gälten ältere, noch buchbare Deals beim nächsten Lauf als abgelaufen."""
    state = state if state is not None else {}
    fresh = fetch_page(page_url(1), timeout=timeout)
    cutoff = _now() - timedelta(days=KEEP_DAYS)
    merged = {d["link"]: d for d in _remembered(state, cutoff)}
    merged.update((d["link"], d) for d in fresh)          # frisch gelesen gewinnt
    items = sorted(merged.values(), reverse=True,
                   key=lambda d: d.get("published") or datetime.min.replace(tzinfo=timezone.utc))
    items = items[:KEEP_DEALS]
    state["deals"] = [dict(d, published=d["published"].isoformat() if d.get("published")
                           else None) for d in items]
    return items
