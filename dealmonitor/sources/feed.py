"""Quelle: RSS-Feeds von Deal-Seiten (Travel-Dealz, mydealz, fly4free, premium-flights, …).

Hier wird nichts gescrapt - es werden nur die öffentlichen Feeds gelesen. Die
Artikel werden per Stichwort den Alarmen zugeordnet, der "ab"-Preis wird aus
der Überschrift (bzw. bei mydealz aus der Beschreibung) gelesen. Der
Originaltitel und der Link zum Artikel bleiben erhalten (Prinzip: Quelle nennen).

Aufbau
------
* ``FEED_PRESETS``: fertige Deal-Seiten mit URL(s), Sprache und Parser-Profil.
* ``Profile``: wie ein Feed zu lesen ist (Preisquelle, Sprache, Abflug-Annahme, …).
* ``FeedSource(presets=[...], extra_feeds=[...], search_enabled=True)``: lädt alle
  Feeds einmal pro Lauf, dedupliziert per normalisiertem Link und liefert pro Alarm
  passende ``Offer``s (``source = "feed:<preset-id>"``).
* ``analyze(item)``: Flug-Artikel? Preis, Kabine, Abflugort - wird auch vom Radar
  (``dealmonitor.radar``) genutzt, das *alle* Flug-Artikel prüft.
* Abwärtskompatibel: ``FeedSource(["https://…/feed/"])`` liest eine URL-Liste wie
  bisher (Name ``travel-dealz-feed``).
"""

from __future__ import annotations

import html as html_mod
import logging
import re
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .. import http, regions
from ..models import Alert, Offer
from . import SourceError

log = logging.getLogger(__name__)

DEFAULT_FEEDS = ["https://travel-dealz.de/feed/"]
TRAVEL_DEALZ_SEARCH = "https://travel-dealz.de/?s={word}&feed=rss2"
#: Grenzen je Feed-Abruf: Größe (Bytes) und Gesamtdauer (Sekunden)
FEED_MAX_BYTES = 5_000_000
FEED_MAX_TIME = 60.0
SAFE_LINK_RE = re.compile(r"^https?://", re.I)


# -- Parser-Profile und Presets ------------------------------------------------------

@dataclass(frozen=True)
class Profile:
    """Wie ein Feed gelesen wird."""

    lang: str = "de"                    # "de" | "en"
    price_from: str = "title"           # "title" | "title+description" | "description+title"
    default_origin: str = "unknown"     # "home": ohne Angabe = Abflug DACH (Travel-Dealz-Prinzip)
    use_categories: bool = True         # Kategorien für die Flug-Erkennung nutzen
    flight_words_required: bool = False  # Titel muss "Flug"/"Flights"/Airline nennen (mydealz)
    cabins: tuple[str, ...] | None = None   # nur diese Kabinen übernehmen (premium-flights)
    description_chars: int = 600         # so viele Zeichen der Beschreibung nach Preisen durchsuchen


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    urls: tuple[str, ...]
    language: str
    enabled: bool
    profile: Profile
    homepage: str
    description: str = ""
    search: bool = False                # Suchfeed: URLs entstehen pro Alarm aus Stichwörtern

    @property
    def source(self) -> str:
        return f"feed:{self.id}"


TRAVEL_DEALZ_PROFILE = Profile(lang="de", price_from="title", default_origin="home")
# mydealz: Deal-Preis steht am Anfang der Beschreibung ("297€ - SkyExplorer … Schon ab 298€");
# tiefer im Text folgen Teilpreise ("Frankfurt – Paris für 40€") -> nur den Anfang lesen
MYDEALZ_PROFILE = Profile(lang="de", price_from="description+title", use_categories=False,
                          flight_words_required=True, description_chars=240)
GENERIC_PROFILE = Profile(lang="de", price_from="title")

FEED_PRESETS: list[Preset] = [
    Preset("travel-dealz", "Travel-Dealz", (
        "https://travel-dealz.de/feed/",
        "https://travel-dealz.de/feed/?paged=2",
        "https://travel-dealz.de/feed/?paged=3",
        "https://travel-dealz.de/ticker/feed/",
        "https://travel-dealz.de/kategorie/business-class/feed/",
    ), "de", True, TRAVEL_DEALZ_PROFILE, "https://travel-dealz.de/",
        "Artikel, Ticker und Business-Class-Kategorie"),
    Preset("travel-dealz-suche", "Travel-Dealz-Suche", (TRAVEL_DEALZ_SEARCH,), "de", True,
           TRAVEL_DEALZ_PROFILE, "https://travel-dealz.de/",
           "Suche nach den Stichwörtern deiner Alarme (wie der Schlagwortalarm)", search=True),
    Preset("mydealz-fluege", "mydealz Flüge", ("https://www.mydealz.de/rss/gruppe/fluege",), "de",
           True, MYDEALZ_PROFILE, "https://www.mydealz.de/gruppe/fluege",
           "Preis steht in der Beschreibung, nur Flug-Deals"),
    Preset("fly4free", "fly4free", ("https://www.fly4free.com/feed/",), "en", True,
           Profile(lang="en", price_from="title"), "https://www.fly4free.com/",
           "Englisch, Abflug oft nicht aus DACH"),
    Preset("premium-flights", "premium-flights", ("https://www.premium-flights.com/feed/",), "en",
           True, Profile(lang="en", price_from="title", cabins=("business", "first")),
           "https://www.premium-flights.com/", "Business/First Class, nur €-Preise"),
    Preset("urlaubspiraten", "Urlaubspiraten", ("https://www.urlaubspiraten.de/feed",), "de",
           False, Profile(lang="de", price_from="title+description", use_categories=False,
                          flight_words_required=True),
           "https://www.urlaubspiraten.de/", "meist Pauschal/Hotel, Preise selten im Titel"),
    Preset("reisetopia", "reisetopia", ("https://reisetopia.de/feed/",), "de", False,
           Profile(lang="de", price_from="title"), "https://reisetopia.de/",
           "meist Hotels/Meilen, Flug-Deals selten"),
]
PRESETS_BY_ID: dict[str, Preset] = {p.id: p for p in FEED_PRESETS}
CUSTOM_SOURCE = "feed:custom"

#: Anzeigenamen für ``Offer.source`` (UI, Report, Meldungen)
SOURCE_LABELS: dict[str, str] = {
    "travelpayouts": "Travelpayouts (Preis-API)",
    "travel-dealz-feed": "Travel-Dealz-Artikel",
    "feed:travel-dealz": "Travel-Dealz-Artikel",
    "feed:travel-dealz-suche": "Travel-Dealz-Suche",
    "feed:mydealz-fluege": "mydealz",
    "feed:fly4free": "fly4free",
    "feed:premium-flights": "premium-flights",
    "feed:urlaubspiraten": "Urlaubspiraten",
    "feed:reisetopia": "reisetopia",
    CUSTOM_SOURCE: "Eigener Feed",
    "google_flights": "Google Flights",
    "amadeus-dates": "Amadeus (Monatsscan)",
    "amadeus-offers": "Amadeus (Angebote)",
}


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def default_preset_ids() -> list[str]:
    return [p.id for p in FEED_PRESETS if p.enabled]


def preset_for_url(url: str) -> Preset | None:
    """Preset, zu dessen Seite eine (eigene) Feed-URL gehört - für Profil und Quelle."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")
    for p in FEED_PRESETS:
        if p.search:
            continue
        for u in p.urls:
            if (urllib.parse.urlsplit(u).hostname or "").lower().removeprefix("www.") == host:
                return p
    return None


# -- Preise ---------------------------------------------------------------------------

# "ab 399€", "für 1.299 €", "399 Euro", "ab 89,- €", "ab 39,99€", "€599", "from €1,299"
PRICE_RE = re.compile(
    r"(?:(?P<cur1>€|EUR\b|Euro\b)\s*(?P<amt1>\d[\d.,]*\d|\d))"
    r"|(?:(?P<amt2>\d{1,3}(?:[.,]\d{3})+|\d+)(?:,(?P<dec2>\d{2}|-|–))?\s*(?P<cur2>€|EUR\b|Euro\b))",
    re.I)
# Fremdwährung direkt vor einem Betrag: "$3,650", "US$830", "£1610", "CHF 29", "29 CHF"
FOREIGN_CURRENCY_RE = re.compile(r"(?:US|CA|AU|HK|S)?\$|£|CHF|USD|GBP|PLN|zł|Kč|kr\b", re.I)
# Beträge direkt nach diesen Wörtern sind sehr wahrscheinlich der Flugpreis
PRICE_LEAD_RE = re.compile(r"\b(?:ab|für|fuer|nur|von|from|for|at|just)\s*$", re.I)
# ... und diese sind Rabatte/Gutscheine, kein Flugpreis ("30€ Rabatt", "100€ Gutschein")
DISCOUNT_AFTER_RE = re.compile(
    r"^[^\d€]{0,12}?\b(?:rabatt|gutschein|cashback|guthaben|sparen|bonus|meilen|nachlass|"
    r"ersparnis|discount|voucher|off\b|credit|bordguthaben)", re.I)
DISCOUNT_BEFORE_RE = re.compile(
    r"\b(?:spar\w*(?:\s+(?:sie|ihr|du|wir|euch))?|rabatt|gutschein|cashback|guthaben|bonus|"
    r"nachlass|save|saving|discount)\s*"
    r"(?:von|über|ueber|in\s+höhe\s+von|of)?\s*(?:bis\s+zu\s*|up\s+to\s*)?$", re.I)
ONEWAY_RE = re.compile(r"\bone[\s-]?way\b|\beinzelflug|\bnur hinflug|\bpro (?:strecke|richtung|weg)\b"
                       r"|\bper (?:leg|direction)\b|\beinfache fahrt", re.I)
# "inkl. Rückflug (Jan - Mär) | oneway ab 19€": der zuerst genannte Reisetyp gehört zum Hauptpreis
ROUNDTRIP_RE = re.compile(r"\binkl(?:\.|usive)?\s*r(?:ü|ue|u)ckflug|\bhin[\s-]*und[\s-]*r(?:ü|ue|u)ckflug"
                          r"|\bround[\s-]?trip\b|\breturn\b|\bretour\b", re.I)
# Kabine direkt vor ihrem eigenen Preis: "… ab 99€ (Business Class ab 229€)", "(Economy ab 233€)"
# "First" allein ist meist keine Kabine ("First Minute", "for the first time") - nur
# "First Class", "First-Class", "First Suite", "in der First" oder "First ab 2.499€"
CABIN_WORD_RE = re.compile(r"\b(?:first[\s-]?class|first\s+suite|(?<=der )first|first(?=\s+ab\b)|"
                           r"business(?:[\s-]?class)?|biz(?:class)?|"
                           r"premium[\s-]*(?:economy|eco|class)|economy(?:[\s-]?class)?)\b", re.I)
_CABIN_GAP_RE = re.compile(r"^[\s„“\"'”‚‘’:()–-]*(?:(?:ab|from|für|fuer|for|nur|just|at)\s*)?$", re.I)
EXPIRED_RE = re.compile(r"abgelaufen|expired", re.I)
CABIN_RE = {
    "first": re.compile(r"\bfirst[\s-]?class\b|\bfirst\s+suite|\bin der first\b|"
                        r"\bfirst(?=\s+ab\b)", re.I),
    "business": re.compile(r"\bbusiness(?:[\s-]?class)?\b|\bbiz(?:class)?\b", re.I),
    "premium": re.compile(r"\bpremium[\s-]*(?:eco\w*|class)\b", re.I),
}

# Kategorien (Travel-Dealz, fly4free, premium-flights, reisetopia): Flug-Deals vs. alles andere
CATEGORY_CABIN = [("first", "first class"), ("business", "business class"),
                  ("premium", "premium economy"), ("economy", "economy class")]
FLIGHT_CAT_RE = re.compile(r"fl(?:ü|ue)g|\bflights?\b|class\b|airline|error ?fare|fehlerpreis|"
                           r"gabelfl|stopover|round the world|langstrecke|kurzstrecke|air ?fare",
                           re.I)
NON_FLIGHT_CAT_RE = re.compile(r"kreuzfahrt|cruise|pauschalreise|package|\bbahn\b|\bzug\b|\btrain\b|"
                               r"hotel|mietwagen|kreditkarte|credit card|ferienwohnung|\bmietauto|"
                               r"lounge|\bnews\b|\btips?\b|guides?\b|stories|bewertung|review|"
                               r"meilen|miles|punkte|award|finanzen|\bbank\b|status|shows? &|"
                               r"musical|reisezubehör|versicherung", re.I)
# Titel, die sicher kein Flug-Deal sind (Kreuzfahrt, Hotel, Bahn, Pauschalreise "inkl. Flug" …)
NON_FLIGHT_TITLE_RE = re.compile(
    r"kreuzfahrt|cruise|pauschalreise|all[\s-]?inclusive|\bhotels?\b|\bresort\b|\bbahn\b|\bzug\b|"
    r"\btgv\b|\bice\b|kreditkarte|credit card|"
    r"\baida\b|\bmsc\b|\bcosta (?:toscana|smeralda|serena|diadema|deliziosa|fascinosa|favolosa|"
    r"fortuna|pacifica|firenze|crociere|kreuzfahrt)|/night\b|"
    r"/double\b|rundreise|hostel|apartment|ferienhaus|skipass|therme|musical|\btrain\b|"
    r"\bfähre|\bferry\b|mietwagen|gutscheinkarte|geschenkkarte|"
    r"\binkl(?:\.|usive)?\s+fl(?:ug|üge)\b", re.I)
# ... und Wörter, die nur ohne Flug-/Kabinenwort gegen einen Flug-Deal sprechen
# ("Lufthansa Business Class … + 5.000 Meilen", "Flüge … & 14 Nächte frei wählbar")
SOFT_NON_FLIGHT_TITLE_RE = re.compile(
    r"\bmeilen\b|\bmiles\b|\bavios\b|\bpunkte\b|\bstatus\b|\bbus\b|"
    r"\d+\s*(?:nächte|naechte|nights?|ün|übernachtung)", re.I)
# Name des Vielfliegerprogramms - kein Hinweis auf einen Meilen-Deal
PROGRAM_NAME_RE = re.compile(r"miles\s*(?:&|and|und|\+)\s*more", re.I)


def non_flight_title(title: str) -> bool:
    """Überschrift spricht gegen einen Flug-Deal (harte Wörter immer, weiche nur ohne
    Flug-/Kabinenwort)."""
    if NON_FLIGHT_TITLE_RE.search(title):
        return True
    text = PROGRAM_NAME_RE.sub(" ", title)
    return bool(SOFT_NON_FLIGHT_TITLE_RE.search(text)) and not (
        FLIGHT_WORD_RE.search(text) or CABIN_WORD_RE.search(text))


# Airline-Namen, die ein Ziel-Stichwort enthalten: "China Eastern: Amsterdam – Tokio"
# ist kein China-Deal. Vor dem Stichwort-Abgleich entfernen.
AIRLINE_NOISE_RE = re.compile(
    r"\b(?:air china|china (?:eastern|southern|airlines|express)|hainan airlines|"
    r"sichuan airlines|xiamen ?air(?:lines)?|shenzhen airlines|shanghai airlines|"
    r"beijing capital airlines|tianjin airlines|hong kong airlines|cathay pacific|"
    r"swiss(?: international)?(?: air lines)?|austrian(?: airlines)?|singapore airlines|"
    r"japan airlines|korean air|thai airways|malaysia airlines|vietnam airlines|air india|"
    r"turkish airlines|air france|british airways|brussels airlines|air europa|"
    r"norwegian(?: air)?|icelandair|air canada|american airlines|air new zealand|"
    r"ethiopian airlines|kenya airways|south african airways|egyptair|qatar airways|"
    r"etihad airways|emirates|air serbia|air baltic|air malta|malta air|air dolomiti|"
    r"philippine airlines|china airlines|eva air|starlux|air macau|air busan)\b", re.I)
# Airlines, deren Name in einem Titel einen Flug-Deal verrät (mydealz ohne Kategorien)
AIRLINE_NAMES_RE = re.compile(
    r"\b(?:lufthansa|ryanair|eurowings|condor|easyjet|wizz ?air|emirates|qatar|etihad|swiss|"
    r"austrian|klm|air france|british airways|turkish|icelandair|norwegian|vueling|tui ?fly|"
    r"discover airlines|finnair|\bsas\b|\blot\b|\btap\b|iberia|air china|china (?:eastern|southern)|"
    r"hainan|cathay|singapore airlines|\bana\b|\bjal\b|korean air|asiana|thai airways|"
    r"malaysia airlines|vietnam airlines|eva air|starlux|air india|oman air|saudia|gulf air|"
    r"royal jordanian|ethiopian|kenya airways|delta|united|american airlines|air canada|westjet|"
    r"latam|avianca|aer lingus|transavia|pegasus|sunexpress|corendon|malta air|\bbuzz\b|volotea|"
    r"air ?baltic|luxair|edelweiss|helvetic|brussels airlines|aegean|air serbia|air europa|"
    r"jetblue|play\b|wizz|air dolomiti|eurowings discover|marabu|aeroflot|air astana|"
    r"turkmenistan airlines|miat|uzbekistan airways|azerbaijan airlines|air arabia|flydubai|"
    r"skyexplorer)\b", re.I)
FLIGHT_WORD_RE = re.compile(r"fl(?:u|ü|ue)g|\bflights?\b|\bfly\b|\bairfare|\berror ?fare|"
                            r"fehlerpreis|\bnonstop\b|\bgabelfl", re.I)
NON_DEAL_LINK_RE = re.compile(r"/gutscheine?/|/coupons?/|/vouchers?/|/hotels?/|/pauschalreisen/|"
                              r"/cruises?/|/kreuzfahrt|/news/|/reviews?/|/guides?/|/bewertung", re.I)
FLIGHT_LINK_RE = re.compile(r"/fluege/|/flights?/|/flight-deals?/|/fl%C3%BCge/", re.I)
UTM_RE = re.compile(r"^(?:utm_|fbclid|gclid|mc_|ref$)", re.I)
TAG_RE = re.compile(r"<[^>]+>")
IATA_RE = re.compile(r"^[A-Z]{3}$")


def is_flight_item(title: str, categories: list[str], link: str = "") -> bool:
    """Nur Flug-Deals - keine Kreuzfahrten, Pauschalreisen, Bahn, Hotels, Kreditkarten."""
    cats = [c for c in categories if c]
    if any(NON_FLIGHT_CAT_RE.search(c) for c in cats):
        return False
    if any(FLIGHT_CAT_RE.search(c) for c in cats):
        return True
    if link and NON_DEAL_LINK_RE.search(link):
        return False
    if non_flight_title(title):
        return False
    if link and FLIGHT_LINK_RE.search(link):
        return True
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


def strip_html(text: str) -> str:
    text = html_mod.unescape(TAG_RE.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def normalize_link(link: str) -> str:
    """Link ohne utm_*-Parameter, Fragment und Schrägstrich am Ende - zum Deduplizieren."""
    link = (link or "").strip()
    if not link:
        return ""
    try:
        parts = urllib.parse.urlsplit(link)
    except ValueError:
        return link
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if not UTM_RE.match(k)]
    path = parts.path.rstrip("/") if parts.path != "/" else ""
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path,
                                    urllib.parse.urlencode(query), ""))


def detect_cabin(title: str, categories: list[str] | None = None) -> str:
    cats = " | ".join(categories or []).lower()
    for cabin, needle in CATEGORY_CABIN:
        if needle in cats:
            return cabin
    for cabin, rx in CABIN_RE.items():
        if rx.search(title):
            return cabin
    return "economy"


def is_oneway(title: str) -> bool:
    """one-way laut Überschrift - nennt sie beides ("inkl. Rückflug … | oneway ab 19€"),
    zählt der zuerst genannte Reisetyp (der gehört zum Hauptpreis)."""
    ow = ONEWAY_RE.search(title)
    if not ow:
        return False
    rt = ROUNDTRIP_RE.search(title)
    return rt is None or ow.start() < rt.start()


def _cabin_of(word: str) -> str:
    w = word.lower()
    if w.startswith("first"):
        return "first"
    if w.startswith("premium"):
        return "premium"
    if w.startswith(("business", "biz")):
        return "business"
    return "economy"


def cabin_prices(title: str, lang: str = "de") -> list[tuple[str, float]]:
    """Kabinen, denen die Überschrift einen eigenen Preis gibt.

    "Europaflüge von Wien ab 99€ roundtrip (Business Class ab 229€)" -> [("business", 229.0)]
    """
    out: list[tuple[str, float]] = []
    for m in CABIN_WORD_RE.finditer(title):
        rest = title[m.end():m.end() + 30]
        pm = PRICE_RE.search(rest)
        if not pm or not _CABIN_GAP_RE.match(rest[:pm.start()]):
            continue
        value = parse_price(title[m.end():m.end() + pm.end() + 15], lang)
        if value is not None:
            out.append((_cabin_of(m.group(0)), value))
    return out


def price_cabin(title: str, categories: list[str] | None, price: float | None,
                pairs: list[tuple[str, float]] | None = None, lang: str = "de") -> str:
    """Kabine des Hauptpreises. Wie :func:`detect_cabin`, aber eine Kabine, die in der
    Überschrift einen *anderen* Preis hat ("ab 99€ (Business Class ab 229€)"), ist es nicht."""
    pairs = cabin_prices(title, lang) if pairs is None else pairs
    if price is not None:
        for cabin, p in pairs:
            if abs(p - price) < 0.01:
                return cabin
    other = {cabin for cabin, _ in pairs}
    cats = " | ".join(categories or []).lower()
    for cabin, needle in CATEGORY_CABIN:
        if needle in cats and cabin not in other:
            return cabin
    for cabin, rx in CABIN_RE.items():
        if cabin not in other and rx.search(title):
            return cabin
    return "economy"


def _amount(text: str, lang: str) -> float | None:
    """"1.299" -> 1299 (de), "1,299" -> 1299 (en), "39,99" -> 39.99 (de), "599.99" -> 599.99 (en)."""
    t = text.strip()
    if lang == "en":
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", t):
            t = t.replace(",", "")
        elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", t):   # "from €1.299" (deutsche Schreibweise)
            t = t.replace(".", "")
        elif re.fullmatch(r"\d+(?:\.\d{1,2})?", t):
            pass
        elif re.fullmatch(r"\d+,\d{2}", t):     # "39,99" auch in englischen Titeln
            t = t.replace(",", ".")
        else:
            return None
    else:
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", t):
            t = t.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"\d+(?:,\d{1,2})?", t):
            t = t.replace(",", ".")
        elif re.fullmatch(r"\d{1,3}(?:,\d{3})+", t):   # "1,299€" in deutschen Feeds selten
            t = t.replace(",", "")
        else:
            return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_price(text: str, lang: str = "de") -> float | None:
    """Flugpreis in Euro; Rabatt-/Gutscheinbeträge und Fremdwährungen ($, £, CHF) werden übergangen."""
    candidates = []
    for m in PRICE_RE.finditer(text):
        before, after = text[:m.start()], text[m.end():]
        if DISCOUNT_AFTER_RE.search(after) or DISCOUNT_BEFORE_RE.search(before):
            continue
        if m.group("amt1") is not None:            # "€599", "from €1,299"
            value = _amount(m.group("amt1"), lang)
        else:                                      # "599€", "1.299 €", "39,99€", "89,- Euro"
            value = _amount(m.group("amt2"), "de" if lang == "de" else lang)
            if value is None and lang == "en":
                value = _amount(m.group("amt2"), "de")
            dec = m.group("dec2")
            if value is not None and dec and dec not in ("-", "–"):
                value += int(dec) / 100
        if value is None or value <= 0:
            continue
        candidates.append((0 if PRICE_LEAD_RE.search(before) else 1, value))
    if not candidates:
        return None
    return min(candidates, key=lambda c: c[0])[1]   # erster Betrag nach "ab/für/nur/von"


# -- Abflugort ---------------------------------------------------------------------------

_NAME = r"[A-ZÄÖÜ][\w'’.-]*(?:\s+(?:am\s+Main|del\s+\w+|de\s+\w+))?(?:\s+[A-ZÄÖÜ][\w'’.-]*)?"
_SEP = r"\s*(?:,|&|\+|/|\||\bund\b|\boder\b|\band\b|\bor\b)\s*"
_LIST = rf"{_NAME}(?:{_SEP}{_NAME})*"
ORIGIN_RE = {
    "de": re.compile(rf"\b(?:[Vv]on|[Aa]b|[Aa]us)\s+(?P<names>{_LIST})(?![\w$£])"),
    "en": re.compile(rf"\b[Ff]rom\s+(?:(?:several|many|various|multiple|different|all|the)\s+)?"
                     rf"(?P<names>{_LIST})(?![\w$£])"),
}
ROUTE_RE = re.compile(rf"(?P<name>{_NAME})\s*(?P<sep>→|->|–|—|↔|⇄|⇆|<->|↔︎|\s-\s)\s*(?P<dest>{_NAME})")
#: Pfeile verbinden immer Orte; ein Gedankenstrich auch "Shanghai - Flüge inkl. Rückflug"
ARROWS = ("→", "->", "↔", "⇄", "⇆", "<->", "↔︎")
#: Airline-Namen sind kein Abflugort ("von Air China", "Singapore Airlines – Business Class")
_AIRLINE_CAND_RE = re.compile(r"^Air\s|\s(?:Airlines|Airways|Air\s+Lines|Air)$", re.I)
_SPLIT_RE = re.compile(_SEP)
# Wörter, die nach "ab/von/from" kein Ort sind (Monate, Zeitangaben, ...)
_NOT_PLACE_RE = re.compile(
    r"^(?:januar|februar|märz|maerz|april|mai|juni|juli|august|september|oktober|november|"
    r"dezember|jan|feb|mar|apr|jun|jul|aug|sep|sept|okt|oct|nov|dez|dec|january|february|march|"
    r"may|june|july|october|december|sofort|mitte|ende|anfang|heute|morgen|now|only|just|"
    r"economy|business|first|premium|us|ca|au|hk|s|eur|the)$", re.I)


def origin_candidates(title: str, lang: str = "de") -> list[str]:
    """Ortsnamen, die der Titel als Abflugort nennt ("von Frankfurt", "from Prague", "ZRH – HAM")."""
    found: list[str] = []
    rxs = [ORIGIN_RE.get(lang, ORIGIN_RE["de"])]
    if lang != "de":
        rxs.append(ORIGIN_RE["de"])
    for rx in rxs:
        for m in rx.finditer(title):
            for raw in _SPLIT_RE.split(m.group("names")):
                raw = raw.strip(" .,")
                if raw and not _NOT_PLACE_RE.match(raw) and not _is_airline(raw):
                    found.append(raw)
    for m in ROUTE_RE.finditer(title):  # "A – B – C": nur A ist der Abflugort
        raw = m.group("name").strip(" .,")
        dest = m.group("dest").strip(" .,")
        if not raw or _NOT_PLACE_RE.match(raw) or _is_airline(raw):
            continue
        # "New York - Flüge inkl. Rückflug": hinter dem Strich steht kein Ort -> keine Strecke
        if m.group("sep") not in ARROWS and (_is_airline(dest) or _lookup(dest)[0] == "unknown"):
            continue
        found.append(raw)
        break
    out: list[str] = []
    for f in found:
        if f not in out:
            out.append(f)
    return out


def _is_airline(name: str) -> bool:
    """"Air China", "Singapore Airlines", "China Eastern" (mehrteilig) - keine Orte."""
    return bool(_AIRLINE_CAND_RE.search(name)) or (
        " " in name.strip() and bool(AIRLINE_NOISE_RE.fullmatch(name.strip())))


def _lookup(place: str) -> tuple[str, set[str]]:
    """Art + Codes für einen Kandidaten; probiert auch das letzte/erste Wort allein
    ("Gabelflüge Amsterdam", "Berlin Business")."""
    words = place.split()
    for cand in ([place] + ([words[-1], words[0]] if len(words) > 1 else [])):
        kind = regions.place_kind(cand)
        if kind != "unknown":
            return kind, regions.codes_for(cand)
    return "unknown", set()


def detect_origin(title: str, lang: str = "de") -> str:
    """"home" (Abflug DACH), "foreign" (bekannter fremder Ort) oder "unknown" (keine Angabe)."""
    kinds = {_lookup(p)[0] for p in origin_candidates(title, lang)}
    if "home" in kinds:
        return "home"
    if "foreign" in kinds:
        return "foreign"
    return "unknown"


def origin_codes(title: str, lang: str = "de") -> set[str]:
    """IATA-Codes der im Titel genannten Abflugorte (für die Alarm-Ausnahme bei fremdem Abflug)."""
    out: set[str] = set()
    for p in origin_candidates(title, lang):
        out |= _lookup(p)[1]
    return out


# -- Stichwörter -----------------------------------------------------------------------------

def auto_keywords(alert: Alert, *, origin_required: bool = True) -> list[list[str]]:
    """Stichwort-Gruppen aus den Von/Nach-Codes eines Alarms ohne eigene ``feed_keywords``.

    Jede Gruppe = ein Von-Wort + ein Nach-Wort (Stadtname oder Regionsname wie
    "Deutschland"); bei Regionen reicht so ein Mitglied. ``origin_required=False``
    liefert nur Nach-Wörter (Travel-Dealz-Prinzip: ohne Angabe = Abflug Deutschland).
    """
    dests = regions.place_words(alert.destinations)
    if not dests:
        return []
    if not origin_required:
        return [[d] for d in dests]
    origins = regions.place_words(alert.origins)
    return [[o, d] for o in origins for d in dests if normalize(o) != normalize(d)]


#: "Kanton" für Guangzhou - aber "Kanton Zürich", "Canton of Geneva" sind Schweizer Kantone
CANTON_RE = re.compile(r"\b(?i:kanton|canton)\b(?!\s+(?:of\b|[A-ZÄÖÜ]))")


def _word_matches(word: str, norm: str, raw: str) -> bool:
    for variant in regions.synonyms(word):
        if IATA_RE.match(variant):
            if variant in regions.AMBIGUOUS_CODES:   # "YES YOU CAN" -> nur "FRA-CAN", "CAN →"
                if re.search(rf"\b[A-Z]{{3}}\s*(?:-|–|→|->|/)\s*{variant}\b|"
                             rf"\b{variant}\s*(?:-|–|→|->|/)\s*[A-Z]{{3}}\b", raw):
                    return True
            elif re.search(rf"\b{variant}\b", raw):      # Codes nur in Großschreibung
                return True
        elif normalize(variant) in ("kanton", "canton"):
            if CANTON_RE.search(raw):
                return True
        elif _kw(variant).search(norm):
            return True
    return False


def matches(alert: Alert, text: str, keywords: list[list[str]] | None = None, *,
            title: str | None = None, lang: str = "de") -> bool:
    """Mindestens eine Stichwort-Gruppe muss vollständig vorkommen (inkl. Synonyme/IATA-Codes).

    Eine Gruppe, deren Wörter *alle* als Abflugorte genannt sind ("Bangkok … von Frankfurt,
    Hamburg, Zürich" für die Gruppe Zürich+Hamburg), beschreibt kein Ziel und zählt nicht.
    """
    groups = keywords if keywords is not None else (alert.feed_keywords or auto_keywords(alert))
    raw = AIRLINE_NOISE_RE.sub(" ", text)
    norm = normalize(raw)
    departures = origin_candidates(title if title is not None else text, lang)
    for group in groups:
        if not group or not all(_word_matches(w, norm, raw) for w in group):
            continue
        if departures and all(any(_word_matches(w, normalize(d), d) for d in departures)
                              for w in group):
            continue
        return True
    return False


def search_terms(alert: Alert, min_len: int = 4) -> list[str]:
    """Suchwörter für den Travel-Dealz-Suchfeed: erstes Wort jeder Stichwort-Gruppe,
    ohne eigene Stichwörter die Zielnamen des Alarms."""
    words = ([g[0] for g in alert.feed_keywords if g] if alert.feed_keywords
             else regions.place_words(alert.destinations))
    out: list[str] = []
    taken: set[str] = set()      # Synonyme ("Peking"/"Beijing") nur einmal suchen - Suchen sind begrenzt
    for w in words:
        w = w.strip()
        if len(w) >= min_len and not IATA_RE.match(w) and regions.normalize_name(w) not in taken:
            out.append(w)
            taken |= {regions.normalize_name(s) for s in regions.synonyms(w)}
    return out


# -- Artikel bewerten ------------------------------------------------------------------------------

@dataclass
class ItemInfo:
    """Ergebnis von :func:`analyze` - alles, was Alarm-Abgleich und Radar brauchen."""

    flight: bool
    price: float | None
    cabin: str
    oneway: bool
    origin: str                      # "home" | "foreign" | "unknown"
    origin_codes: set[str] = field(default_factory=set)
    source: str = CUSTOM_SOURCE
    link: str = ""                   # normalisiert
    foreign_currency: bool = False   # Preis nur in $/£/CHF
    #: weitere Kabinen mit eigenem Preis in der Überschrift ("(Business Class ab 229€)")
    alt_prices: dict[str, float] = field(default_factory=dict)


def _is_flight(item: dict, profile: Profile) -> bool:
    title, cats, link = item["title"], item.get("categories") or [], item.get("link") or ""
    if profile.use_categories:
        if not is_flight_item(title, cats, link):
            return False
    else:
        if non_flight_title(title) or NON_DEAL_LINK_RE.search(link):
            return False
    if profile.flight_words_required:
        desc = item.get("description_text") or strip_html(item.get("description") or "")
        if not (FLIGHT_WORD_RE.search(title) or AIRLINE_NAMES_RE.search(title)
                or FLIGHT_LINK_RE.search(link) or FLIGHT_WORD_RE.search(desc[:600])):
            return False
    return True


def _price(item: dict, profile: Profile) -> float | None:
    title = item["title"]
    desc = item.get("description_text")
    if desc is None:
        desc = strip_html(item.get("description") or "")
    order = profile.price_from.split("+")
    for part in order:
        text = title if part == "title" else desc[:profile.description_chars]
        p = parse_price(text, profile.lang)
        if p is not None:
            return p
    if item.get("merchant_price"):
        return parse_price(item["merchant_price"], profile.lang)
    return None


def analyze(item: dict, profile: Profile | None = None) -> ItemInfo:
    """Flug-Artikel? Preis, Kabine, one-way, Abflugort. ``item`` wie aus :meth:`FeedSource.parse`."""
    profile = profile or item.get("profile") or GENERIC_PROFILE
    title = item["title"]
    cats = item.get("categories") or []
    link = normalize_link(item.get("link") or "")
    source = item.get("source") or CUSTOM_SOURCE
    flight = _is_flight(item, profile)
    price = _price(item, profile) if flight else None
    pairs = cabin_prices(title, profile.lang) if flight else []
    cabin = price_cabin(title, cats, price, pairs, profile.lang)
    alt: dict[str, float] = {}
    for c, p in pairs:
        if c != cabin and c not in alt and (not profile.cabins or c in profile.cabins):
            alt[c] = p
    if flight and profile.cabins and cabin not in profile.cabins:
        if alt:      # Hauptpreis in fremder Kabine, aber z. B. "(Business ab 1.499€)" genannt
            cabin, price = next(iter(alt.items()))
            del alt[cabin]
        else:
            flight, price = False, None
    foreign_currency = flight and price is None and bool(FOREIGN_CURRENCY_RE.search(title))
    origin = detect_origin(title, profile.lang)
    codes = origin_codes(title, profile.lang) if origin != "unknown" else set()
    if origin == "unknown" and profile.default_origin == "home":
        origin = "home"
    return ItemInfo(flight=flight, price=price, cabin=cabin, oneway=is_oneway(title),
                    origin=origin, origin_codes=codes, source=source, link=link,
                    foreign_currency=foreign_currency, alt_prices=alt if flight else {})


def origin_ok(alert: Alert, info: ItemInfo) -> bool:
    """Fremder Abflugort passt nur, wenn der Alarm diesen Ort selbst als Von hat. Ein
    DACH-Abflug aus einem Land, aus dem der Alarm gar nicht abfliegt ("von Zürich" beim
    Alarm ab Deutschland), passt auch nicht."""
    if info.origin == "foreign":
        return bool(info.origin_codes & set(alert.origins))
    if info.origin == "home" and info.origin_codes:
        if info.origin_codes & set(alert.origins):
            return True
        countries = {regions.country(c) for c in info.origin_codes} - {None}
        alert_countries = {regions.country(c) for c in alert.origins} - {None}
        if countries and alert_countries and not countries & alert_countries:
            return False
    return True


# -- Die Quelle ---------------------------------------------------------------------------------------

class FeedSource:
    """Alle aktiven Deal-Feeds als eine Quelle.

    ``FeedSource(presets=["travel-dealz", "mydealz-fluege"], extra_feeds=[...],
    search_enabled=True, max_age_days=14)`` - oder abwärtskompatibel
    ``FeedSource(["https://…/feed/"])`` (nur URL-Liste, Name ``travel-dealz-feed``).
    """

    records_history = False

    def __init__(self, feeds: list[str] | None = None, *, presets: list[str] | None = None,
                 extra_feeds: list[str] | None = None, search_enabled: bool | None = None,
                 max_age_days: int = 14, max_searches: int = 8) -> None:
        self.max_age_days = max_age_days
        self.max_searches = max_searches
        if presets is None and extra_feeds is None and search_enabled is None:
            # Alte Schnittstelle: reine URL-Liste (Standard: Travel-Dealz-Hauptfeed)
            self.legacy = True
            self.name = "travel-dealz-feed"
            self.presets: list[Preset] = []
            self.extra_feeds = list(feeds or DEFAULT_FEEDS)
            self.search_enabled = False
        else:
            self.legacy = False
            self.name = "feeds"
            ids = list(presets or [])
            unknown = [i for i in ids if i not in PRESETS_BY_ID]
            if unknown:
                raise ValueError(f"Unbekannte Feed-Presets: {', '.join(unknown)}")
            self.presets = [PRESETS_BY_ID[i] for i in ids if not PRESETS_BY_ID[i].search]
            self.extra_feeds = list(extra_feeds or []) + list(feeds or [])
            self.search_enabled = ("travel-dealz-suche" in ids) if search_enabled is None \
                else bool(search_enabled)
        self.feeds: list[str] = [u for p in self.presets for u in p.urls] + self.extra_feeds
        self._items: list[dict] | None = None   # einmal pro Lauf laden
        self._error: str | None = None
        self._partial: str | None = None        # einige (nicht alle) Feeds fehlgeschlagen
        self._failed_sources: set[str] = set()  # Offer.source der fehlgeschlagenen Feeds
        self._search_cache: dict[str, list[dict]] = {}
        self._search_errors: list[str] = []
        self._search_failed: set[str] = set()   # Suchwörter (klein), deren Suche fehlschlug
        #: Probleme der Suche für den zuletzt geprüften Alarm (Fehler, Limit)
        self._alert_search_problems: list[str] = []
        #: nach dem letzten ``fetch``: None = alle Feeds gelesen, sonst {"message", "routes": None}
        self.incomplete: dict | None = None
        #: normalisierte Links aller Artikel, die in diesem Lauf einem Alarm zugeordnet wurden
        #: (das Radar meldet diese nicht noch einmal)
        self.matched_links: set[str] = set()

    @property
    def load_errors(self) -> str | None:
        """Hinweis, wenn beim Laden einige (nicht alle) Feeds oder Suchen fehlgeschlagen sind -
        dann dürfen deren Radar-Deals nicht ablaufen (siehe :meth:`unreliable_sources`)."""
        parts = [m for m in (self._partial, *self._search_errors) if m]
        return "; ".join(parts) or None

    @property
    def search_source(self) -> str:
        return PRESETS_BY_ID["travel-dealz-suche"].source

    def unreliable_sources(self) -> set[str]:
        """``Offer.source``-Werte, deren Artikel in diesem Lauf nicht vollständig gelesen
        wurden (Feed nicht lesbar, Suche fehlgeschlagen) - deren Deals nicht ablaufen lassen."""
        out = set(self._failed_sources)
        if self._search_errors:
            out.add(self.search_source)
        return out

    @property
    def offer_sources(self) -> list[str]:
        """Alle ``Offer.source``-Werte, die diese Quelle liefern kann (für ``expire_missing``)."""
        if self.legacy:
            return [self.name]
        out = [p.source for p in self.presets]
        if self.search_enabled:
            out.append(PRESETS_BY_ID["travel-dealz-suche"].source)
        for url in self.extra_feeds:
            p = preset_for_url(url)
            s = p.source if p else CUSTOM_SOURCE
            if s not in out:
                out.append(s)
        return out

    # -- Laden ------------------------------------------------------------------------------------
    def _feed_jobs(self) -> list[tuple[str, Preset | None]]:
        jobs: list[tuple[str, Preset | None]] = []
        taken: set[str] = set()       # gleiche URL nicht doppelt laden (Preset + eigener Feed)
        candidates = [(u, p) for p in self.presets for u in p.urls] + \
            [(u, preset_for_url(u)) for u in self.extra_feeds]
        for url, preset in candidates:
            key = normalize_link(url) or url
            if key in taken:
                continue
            taken.add(key)
            jobs.append((url, preset))
        return jobs

    def _job_source(self, preset: Preset | None) -> str:
        if self.legacy:
            return self.name
        return preset.source if preset else CUSTOM_SOURCE

    def _tag(self, items: list[dict], preset: Preset | None) -> list[dict]:
        """Quelle, Profil und Dedup-Schlüssel an Artikel hängen (idempotent)."""
        for it in items:
            if "key" in it:
                continue
            if self.legacy:
                it["source"], it["profile"] = self.name, (preset.profile if preset else GENERIC_PROFILE)
            else:
                it["source"] = preset.source if preset else CUSTOM_SOURCE
                it["profile"] = preset.profile if preset else GENERIC_PROFILE
            it["preset"] = preset.id if preset else None
            it["key"] = normalize_link(it["link"]) or it["title"]
        return items

    def _fetch_one(self, url: str) -> list[dict]:
        # Fremde Server: kein Antworttext in Fehlermeldungen, Größe/Dauer begrenzt;
        # selbst eingetragene Feeds nur auf öffentliche Adressen (auch nach Weiterleitungen)
        raw = http.request(url, timeout=20, error_body=False, max_bytes=FEED_MAX_BYTES,
                           max_time=FEED_MAX_TIME, public_only=url in self.extra_feeds)
        return self.parse(raw, url)

    def load(self) -> list[dict]:
        if self._items is not None:
            if self._error:
                raise SourceError(self._error)
            return self._items
        items: list[dict] = []
        seen: set[str] = set()
        errors = []
        jobs = self._feed_jobs()
        for url, preset in jobs:
            shown = http.redact(url)            # keine Zugangsdaten in Meldungen/Log
            try:
                fetched = self._tag(self._fetch_one(url), preset)
            except (http.HttpError, ET.ParseError) as e:
                errors.append(f"{shown}: {e}")
                self._failed_sources.add(self._job_source(preset))
                log.warning("Feed %s nicht lesbar: %s", shown, e)
                continue
            except Exception as e:  # noqa: BLE001 - kaputter Feed darf den Lauf nicht stoppen
                errors.append(f"{shown}: unerwarteter Fehler: {e}")
                self._failed_sources.add(self._job_source(preset))
                log.warning("Feed %s nicht lesbar: %s", shown, e)
                continue
            for it in fetched:                      # Dedup über alle Feeds (erster gewinnt)
                if it["key"] in seen:
                    continue
                seen.add(it["key"])
                items.append(it)
        self._items = items  # auch bei Fehlern merken: nicht für jeden Alarm neu versuchen
        if errors and len(errors) == len(jobs):
            self._error = "; ".join(errors)
        elif errors:
            self._partial = (f"{len(errors)} von {len(jobs)} Feeds nicht lesbar: "
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
            if link and not SAFE_LINK_RE.match(link):
                link = ""                  # nur http(s) - kein "javascript:" in Bericht/Meldungen
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
            description = item.findtext("description") or ""
            merchant_price = None
            for child in item:                       # mydealz: <pepper:merchant price="297€"/>
                if child.tag.endswith("}merchant") or child.tag == "merchant":
                    merchant_price = child.get("price")
            out.append({"title": title, "link": link, "published": published,
                        "categories": categories, "feed": url, "description": description,
                        "description_text": strip_html(description)[:1000],
                        "merchant_price": merchant_price})
        return out

    # -- Suche (Travel-Dealz-Schlagwortalarm) --------------------------------------------------
    def search_items(self, alert: Alert) -> list[dict]:
        """Artikel aus der Travel-Dealz-Suche zu den Stichwörtern des Alarms (gecacht, ≤ max_searches)."""
        if not self.search_enabled:
            return []
        preset = PRESETS_BY_ID["travel-dealz-suche"]
        out: list[dict] = []
        skipped: list[str] = []
        failed: list[str] = []
        for word in search_terms(alert):
            key = word.lower()
            if key not in self._search_cache:
                if len(self._search_cache) >= self.max_searches:
                    log.info("Travel-Dealz-Suche: Limit von %d Suchen erreicht, '%s' übersprungen",
                             self.max_searches, word)
                    skipped.append(word)
                    continue
                url = TRAVEL_DEALZ_SEARCH.format(word=urllib.parse.quote(word))
                try:
                    self._search_cache[key] = self._tag(self._fetch_one(url), preset)
                except Exception as e:  # noqa: BLE001 - Suche ist Zugabe, nie fatal
                    self._search_cache[key] = []
                    self._search_failed.add(key)
                    self._search_errors.append(f"Suche '{word}': {e}")
                    log.warning("Travel-Dealz-Suche '%s' fehlgeschlagen: %s", word, e)
            if key in self._search_failed:
                failed.append(word)
            out += self._search_cache.get(key, [])
        self._alert_search_problems = [f"Suche '{w}' fehlgeschlagen" for w in failed]
        if skipped:
            self._alert_search_problems.append(
                f"Travel-Dealz-Suche: Limit von {self.max_searches} Suchen pro Lauf, "
                f"nicht gesucht: {', '.join(skipped)}")
        return out

    def all_items(self) -> list[dict]:
        """Alle geladenen Artikel (Feeds + bisherige Suchergebnisse), dedupliziert - für das Radar."""
        items = self._tag(list(self.load()), preset_for_url(self.feeds[0]) if self.feeds else None)
        seen = {it["key"] for it in items}
        for found in self._search_cache.values():
            for it in found:
                if it["key"] not in seen:
                    seen.add(it["key"])
                    items.append(it)
        return items

    # -- Abgleich ------------------------------------------------------------------------------------
    def fetch(self, alert: Alert) -> list[Offer]:
        self.incomplete = None
        if self.legacy and not alert.feed_keywords:
            return []
        items = self._tag(self.load(), preset_for_url(self.feeds[0]) if self.feeds else None)
        self._alert_search_problems = []
        if self.search_enabled:
            seen = {it["key"] for it in items}
            items = items + [it for it in self.search_items(alert) if it["key"] not in seen]
        messages = [m for m in (self._partial, *self._alert_search_problems) if m]
        if messages:
            # nur die betroffenen Quellen gelten als unvollständig (deren Deals laufen nicht ab)
            affected = set(self._failed_sources)
            if self._alert_search_problems:
                affected.add(self.search_source)
            self.incomplete = {"message": "; ".join(messages), "routes": None,
                               "sources": affected}
        keywords = alert.feed_keywords
        offers = []
        for it in items:
            info = analyze(it)
            if not info.flight:
                continue
            text = it["title"] + " " + " ".join(it["categories"])
            kw_args = {"title": it["title"], "lang": it["profile"].lang}
            if keywords:
                hit = matches(alert, text, keywords, **kw_args)
            else:
                # Ohne eigene Stichwörter: Von- und Nach-Wort; bei Travel-Dealz-Artikeln ohne
                # Abflugangabe (= Deutschland) reicht das Nach-Wort, wenn der Alarm ab DACH geht
                # (nur für Alarme ab einer ganzen Region wie @DE - bei einzelnen Städten wie
                # Zürich/Hamburg muss die Stadt im Titel stehen)
                implied = (it["profile"].default_origin == "home" and not origin_candidates(
                    it["title"], it["profile"].lang) and all(regions.is_home(o) for o in alert.origins)
                    and bool(regions.region_names(alert.origins)))
                hit = matches(alert, text, auto_keywords(alert, origin_required=not implied),
                              **kw_args)
            if not hit:
                continue
            if not origin_ok(alert, info):
                continue
            cabin, price = info.cabin, info.price
            if alert.cabin != cabin and alert.cabin in info.alt_prices:
                # "Tokio ab 650€ (Business Class ab 1.199€)" -> für den Business-Alarm 1.199 €
                cabin, price = alert.cabin, info.alt_prices[alert.cabin]
            if price is None:
                continue  # ohne €-Preis kein vergleichbarer Deal
            offers.append(Offer(
                source=info.source,
                origin=alert.origins[0] if len(alert.origins) == 1 else "",
                destination=alert.destinations[0] if len(alert.destinations) == 1 else "",
                price=price,
                roundtrip=not info.oneway,
                cabin=cabin,
                link=it["link"],
                title=it["title"],
                found_at=(it["published"].astimezone().replace(tzinfo=None)
                          if it["published"] else datetime.now()),
            ))
            self.matched_links.add(info.link or it["key"])
        return offers
