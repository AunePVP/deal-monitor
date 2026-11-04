"""Quelle: RSS-Feeds von Deal-Seiten (Standard: Travel-Dealz).

Hier wird nichts gescrapt - es wird nur der öffentliche WordPress-Feed
gelesen, die Artikel werden per Stichwort den Alarmen zugeordnet und der
"ab"-Preis aus der Überschrift gelesen. Der Originaltitel und der Link zum
Artikel bleiben erhalten (Prinzip: Quelle nennen).
"""

from __future__ import annotations

import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .. import http
from ..models import Alert, Offer
from . import SourceError

log = logging.getLogger(__name__)

DEFAULT_FEEDS = ["https://travel-dealz.de/feed/"]

# "ab 399€", "für 1.299 €", "399 Euro", "ab 89,- €", "ab 39,99€"
PRICE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{2}|-))?\s*(?:€|EUR\b|Euro\b)", re.I)
# Beträge direkt nach diesen Wörtern sind sehr wahrscheinlich der Flugpreis
PRICE_LEAD_RE = re.compile(r"\b(?:ab|für|fuer|nur|von)\s*$", re.I)
# ... und diese sind Rabatte/Gutscheine, kein Flugpreis ("30€ Rabatt", "100€ Gutschein")
DISCOUNT_AFTER_RE = re.compile(
    r"^[^\d€]{0,12}?\b(?:rabatt|gutschein|cashback|guthaben|sparen|bonus|meilen|nachlass|"
    r"ersparnis|discount|voucher)", re.I)
DISCOUNT_BEFORE_RE = re.compile(
    r"\b(?:spar\w*|rabatt|gutschein|cashback|guthaben|bonus|nachlass)\s*"
    r"(?:von|über|ueber|in\s+höhe\s+von)?\s*(?:bis\s+zu\s*)?$", re.I)
ONEWAY_RE = re.compile(r"\bone[\s-]?way\b|\beinzelflug|\bnur hinflug|\bpro (?:strecke|richtung)\b", re.I)
EXPIRED_RE = re.compile(r"abgelaufen|expired", re.I)
CABIN_RE = {
    "first": re.compile(r"\bfirst\b", re.I),
    "business": re.compile(r"\bbusiness\b", re.I),
    "premium": re.compile(r"premium\s*(?:eco|class)", re.I),
}

# Travel-Dealz-Kategorien (echter Feed): Flug-Deals vs. alles andere
CATEGORY_CABIN = [("first", "first class"), ("business", "business class"),
                  ("premium", "premium economy"), ("economy", "economy class")]
FLIGHT_CAT_RE = re.compile(r"fl(?:ü|ue)g|class\b|airline|error ?fare|fehlerpreis|gabelfl|"
                           r"stopover|round the world|langstrecke|kurzstrecke", re.I)
NON_FLIGHT_CAT_RE = re.compile(r"kreuzfahrt|pauschalreise|\bbahn\b|\bzug\b|hotel|mietwagen|"
                               r"kreditkarte|ferienwohnung|\bmietauto|lounge", re.I)
NON_FLIGHT_TITLE_RE = re.compile(r"kreuzfahrt|pauschalreise|all[\s-]?inclusive|\bhotel\b|"
                                 r"\bresort\b|\bbahn\b|\bzug\b|\btgv\b|\bice\b|kreditkarte", re.I)
# Airline-Namen, die ein Ziel-Stichwort enthalten: "China Eastern: Amsterdam – Tokio"
# ist kein China-Deal. Vor dem Stichwort-Abgleich entfernen.
AIRLINE_NOISE_RE = re.compile(
    r"\b(?:air china|china (?:eastern|southern|airlines|express)|hainan airlines|"
    r"sichuan airlines|xiamen ?air(?:lines)?|shenzhen airlines|shanghai airlines|"
    r"beijing capital airlines|tianjin airlines|hong kong airlines|swiss(?: international)?"
    r"(?: air lines)?|austrian(?: airlines)?)\b", re.I)


def is_flight_item(title: str, categories: list[str]) -> bool:
    """Nur Flug-Deals - keine Kreuzfahrten, Pauschalreisen, Bahn, Hotels, Kreditkarten."""
    cats = [c for c in categories if c]
    if any(NON_FLIGHT_CAT_RE.search(c) for c in cats):
        return False
    if any(FLIGHT_CAT_RE.search(c) for c in cats):
        return True
    if NON_FLIGHT_TITLE_RE.search(title):
        return False
    # Kategorien vorhanden, aber keine Flug-Kategorie (z. B. "Allgemein") -> keine Deals
    return not cats


def normalize(text: str) -> str:
    """Kleinbuchstaben, Umlaute vereinfacht: "Zürich" == "zurich" == "zuerich"."""
    text = text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text


def _kw(word: str) -> re.Pattern:
    w = normalize(word)
    # Umlaut-Varianten tolerant machen: "zuerich" soll auch "zurich" treffen
    w = re.escape(w).replace("ue", "u?e?").replace("oe", "o?e?").replace("ae", "a?e?")
    return re.compile(r"\b" + w, re.I)


def detect_cabin(title: str, categories: list[str] | None = None) -> str:
    cats = " | ".join(categories or []).lower()
    for cabin, needle in CATEGORY_CABIN:
        if needle in cats:
            return cabin
    for cabin, rx in CABIN_RE.items():
        if rx.search(title):
            return cabin
    return "economy"


def parse_price(title: str) -> float | None:
    """Flugpreis aus der Überschrift; Rabatt-/Gutscheinbeträge werden übersprungen."""
    candidates = []
    for m in PRICE_RE.finditer(title):
        before, after = title[:m.start()], title[m.end():]
        if DISCOUNT_AFTER_RE.search(after) or DISCOUNT_BEFORE_RE.search(before):
            continue
        value = float(m.group(1).replace(".", ""))
        if m.group(2) and m.group(2) != "-":
            value += int(m.group(2)) / 100
        candidates.append((0 if PRICE_LEAD_RE.search(before) else 1, value))
    if not candidates:
        return None
    return min(candidates, key=lambda c: c[0])[1]   # erster Betrag nach "ab/für/nur/von"


def matches(alert: Alert, text: str) -> bool:
    """Mindestens eine Stichwort-Gruppe muss vollständig vorkommen."""
    norm = normalize(AIRLINE_NOISE_RE.sub(" ", text))
    return any(all(_kw(w).search(norm) for w in group) for group in alert.feed_keywords if group)


class FeedSource:
    name = "travel-dealz-feed"
    records_history = False

    def __init__(self, feeds: list[str] | None = None, *, max_age_days: int = 14) -> None:
        self.feeds = feeds or DEFAULT_FEEDS
        self.max_age_days = max_age_days
        self._items: list[dict] | None = None   # einmal pro Lauf laden
        self._error: str | None = None
        self._partial: str | None = None        # einige (nicht alle) Feeds fehlgeschlagen
        #: nach dem letzten ``fetch``: None = alle Feeds gelesen, sonst {"message", "routes": None}
        self.incomplete: dict | None = None

    def load(self) -> list[dict]:
        if self._items is not None:
            if self._error:
                raise SourceError(self._error)
            return self._items
        items: list[dict] = []
        errors = []
        for url in self.feeds:
            try:
                items += self.parse(http.request(url, timeout=20), url)
            except (http.HttpError, ET.ParseError) as e:
                errors.append(f"{url}: {e}")
                log.warning("Feed %s nicht lesbar: %s", url, e)
            except Exception as e:  # noqa: BLE001 - kaputter Feed darf den Lauf nicht stoppen
                errors.append(f"{url}: unerwarteter Fehler: {e}")
                log.warning("Feed %s nicht lesbar: %s", url, e)
        self._items = items  # auch bei Fehlern merken: nicht für jeden Alarm neu versuchen
        if errors and len(errors) == len(self.feeds):
            self._error = "; ".join(errors)
        elif errors:
            self._partial = (f"{len(errors)} von {len(self.feeds)} Feeds nicht lesbar: "
                             + "; ".join(errors))
        if self._error:
            raise SourceError(self._error)
        return items

    def parse(self, xml: bytes, url: str = "") -> list[dict]:
        root = ET.fromstring(xml)
        out = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_age_days)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = item.findtext("pubDate")
            categories = [c.text or "" for c in item.findall("category")]
            published = None
            if pub:
                try:
                    published = parsedate_to_datetime(pub)
                except (TypeError, ValueError, IndexError):
                    pass
            if published is not None and published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)   # "-0000" = UTC
            if published and published < cutoff:
                continue
            if EXPIRED_RE.search(title) or any(EXPIRED_RE.search(c) for c in categories):
                continue
            out.append({"title": title, "link": link, "published": published,
                        "categories": categories, "feed": url,
                        "description": item.findtext("description") or ""})
        return out

    def fetch(self, alert: Alert) -> list[Offer]:
        self.incomplete = None
        if not alert.feed_keywords:
            return []
        offers = []
        items = self.load()
        if self._partial:
            self.incomplete = {"message": self._partial, "routes": None}
        for it in items:
            if not is_flight_item(it["title"], it["categories"]):
                continue
            if not matches(alert, it["title"] + " " + " ".join(it["categories"])):
                continue
            price = parse_price(it["title"])
            if price is None:
                continue  # ohne Preis kein vergleichbarer Deal
            offers.append(Offer(
                source=self.name,
                origin=alert.origins[0] if len(alert.origins) == 1 else "",
                destination=alert.destinations[0] if len(alert.destinations) == 1 else "",
                price=price,
                roundtrip=not ONEWAY_RE.search(it["title"]),
                cabin=detect_cabin(it["title"], it["categories"]),
                link=it["link"],
                title=it["title"],
                found_at=(it["published"].astimezone().replace(tzinfo=None)
                          if it["published"] else datetime.now()),
            ))
        return offers
