"""Derselbe Deal in zwei Artikeln? Ein gemeinsamer Abgleich für Radar und Erstmeldung.

Radar (``radar.same_deal``, im selben Lauf) und Erstmeldung (``sightings.first_report``,
über Läufe hinweg) fragen beide :func:`match`. Zwei Artikel sind derselbe Deal, wenn

* Kabine und Reiseart gleich sind,
* sie ein gemeinsames Ziel haben (Stadt- oder Flughafencode; ein unbekanntes Wort nach
  "nach"/"to" zählt auch, ein Land ohne Code nicht: "Antalya / Türkei" ≠ "Izmir / Türkei"),
* der Abflug gleich ist oder bei einer Seite unbekannt,
* höchstens 7 Tage zwischen den Veröffentlichungen liegen,
* es nicht zwei verschiedene Artikel derselben Seite sind (Host gleich, Link verschieden),
* und die Preise passen: Nennen beide eine Airline und sind sie verschieden, ist es nie
  derselbe Deal (Hongkong mit China Eastern 484 € ≠ Air China 469 €). Nennen beide dieselbe
  Airline, dürfen die Preise 15 % auseinanderliegen (Turkmenistan Airlines Frankfurt–Bangkok
  Business: Travel-Dealz 1.208 €, ErrorFareAlerts eine Woche später 1.288 €), sonst 5 %.

Bewusst ohne Titel-Ähnlichkeit: die Airline erklärt jede Zusammenführung, Wortlisten nicht.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import NamedTuple
from urllib.parse import urlsplit

from . import regions
from .sources import feed

WINDOW = timedelta(days=7)
#: Preis-Toleranz ohne (gemeinsame) Airline-Angabe
TOLERANCE = 0.05
#: Preis-Toleranz, wenn beide Artikel dieselbe Airline nennen
SAME_AIRLINE_TOLERANCE = 0.15

#: erstes Ziel nach "nach"/"to": ein oder zwei großgeschriebene Wörter ("New York", "Phuket")
DEST_WORD_RE = re.compile(r"\b(?:nach|to)\s+([A-ZÄÖÜ][\w'’.-]+(?:\s+[A-ZÄÖÜ][\w'’.-]+)?)")

# -- Airlines ----------------------------------------------------------------------------------

#: Airline-Namen der Deal-Seiten; "Eurowings Discover" vor "Eurowings" (eigene Airline)
_AIRLINE_RE = re.compile(r"\beurowings[\s-]+discover\b|" + feed.AIRLINE_NAMES_RE.pattern, re.I)
#: kurze Namen, die klein geschrieben gewöhnliche Wörter sind ("a lot of", "tap", "play")
_SHORT_NAMES = frozenset({"lot", "tap", "sas", "ana", "jal", "play", "buzz", "delta", "united"})
#: "United States/Kingdom/Arab Emirates" ist keine Airline, "Doha, Qatar"/"Sanya, Hainan" ein Ort
_NOT_AIRLINE_AFTER = re.compile(r"\s+(?:states|kingdom|arab)\b", re.I)
_NOT_AIRLINE_BEFORE = re.compile(r"(?:\barab\s+|,\s*)$", re.I)
_PLACE_LIKE = frozenset({"emirates", "qatar", "hainan"})
#: gleiche Airline, andere Schreibweise
_CANONICAL = {"eurowings discover": "discover", "discover airlines": "discover",
              "wizz air": "wizz", "wizzair": "wizz", "tui fly": "tuifly", "airbaltic": "air baltic"}
_SUFFIX_RE = re.compile(r"\s+(?:airlines|airways|air\s+lines)$")


def _canonical(name: str) -> str:
    name = re.sub(r"[\s-]+", " ", name.strip().lower())
    name = _CANONICAL.get(name, name)
    name = _SUFFIX_RE.sub("", name)          # "Turkish Airlines" = "Turkish", "Qatar Airways" = "Qatar"
    return _CANONICAL.get(name, name)


def airlines(title: str) -> frozenset[str]:
    """Airlines, die eine Überschrift nennt, in einheitlicher Schreibweise.

    >>> sorted(airlines("Turkmenistan Airlines: Business Class von Frankfurt nach Bangkok"))
    ['turkmenistan']
    """
    out: set[str] = set()
    text = title or ""
    for m in _AIRLINE_RE.finditer(text):
        raw = m.group(0)
        name = _canonical(raw)
        if name in _SHORT_NAMES and raw.islower():
            continue
        if name == "united" and _NOT_AIRLINE_AFTER.match(text, m.end()):
            continue
        if name in _PLACE_LIKE and _NOT_AIRLINE_BEFORE.search(text, 0, m.start()):
            continue
        out.add(name)
    return frozenset(out)


# -- Ziel und Abflug ---------------------------------------------------------------------------

def expand(codes) -> set[str]:
    """Codes samt Stadt-/Flughafen-Gruppe ("PEK" -> {"BJS", "PEK", "PKX"})."""
    out: set[str] = set()
    for c in codes or ():
        c = str(c).strip().upper()
        if not c:
            continue
        out.add(c)
        out |= regions.codes_for(regions.name(c))
    return out


def dest_word(title: str) -> str | None:
    """Ziel nach "nach"/"to" als Schlüssel-Wort, auch wenn der Ort unbekannt ist ("Frankfurt,
    Germany to Phuket, Thailand" und "Von Frankfurt nach Phuket ab 496 €" -> "~phuket")."""
    m = DEST_WORD_RE.search(title or "")
    if not m:
        return None
    word = m.group(1)
    if regions.place_kind(word) == "home":
        return None
    return "~" + regions.normalize_name(word)


def _route_dest(text: str) -> set[str]:
    """Codes des Ziels einer Strecke "A – B"/"A → B" (erste Strecke im Titel), sonst leer."""
    m = feed.ROUTE_RE.search(text)
    if not m:
        return set()
    return regions.codes_for(m.group("dest").strip(" .,")) or \
        feed._lookup(m.group("dest").strip(" .,"))[1]


def route(title: str, origins=(), dests=(), destination: str = "") -> tuple[set[str], set[str]] | None:
    """``(Ziele, Abflug-Codes)`` einer Überschrift - ``None`` ohne erkennbares Ziel.

    Ziel = Codes der Quelle (``dests``), Orte im Titel und das Wort nach "nach"/"to", jeweils
    ohne den Abflug; so finden "SEL" (Titel "Seoul") und "ICN" (Loader) zusammen. Orte in
    Airline-Namen zählen nicht ("China Eastern Airlines: Von Wien nach Singapur" hat das Ziel
    Singapur, nicht China; "Singapore Airlines nach Bali" nicht Singapur)."""
    text = feed.AIRLINE_NOISE_RE.sub(" ", title or "")
    origin_set = expand(origins)
    origin_set |= expand(feed.origin_codes(text, "de") | feed.origin_codes(text, "en"))
    dest_set = expand(dests)
    # Ziel in DACH ("Zürich nach Hamburg"): ein Heimat-Ort ist Ziel, wenn die Quelle ihn als
    # Ziel nennt oder er nach "nach"/"to" steht - sonst gilt er als Abflug
    m = DEST_WORD_RE.search(text)
    hints = dest_set | (regions.codes_for(m.group(1)) if m else set())
    if not hints:
        hints = _route_dest(text)    # "Hamburg – Zürich": der Ort hinter dem Strich ist das Ziel
    home_dest: set[str] = set()
    for place in regions.places_in(text):
        codes = regions.codes_for(place)
        if regions.place_kind(place) != "home":
            dest_set |= codes
        elif codes and codes <= hints:
            home_dest |= codes
        else:
            origin_set |= codes
    origin_set -= home_dest
    dest_set = (dest_set - origin_set) | home_dest
    if (word := dest_word(text)):
        dest_set |= regions.codes_for(word[1:]) - origin_set or {word}
    if not dest_set and destination:     # Alarm mit genau einem Ziel
        dest_set = expand({destination})
    if not dest_set:
        return None
    return dest_set, origin_set


# -- Abgleich ----------------------------------------------------------------------------------

class DealKey(NamedTuple):
    """Was :func:`match` von einem Artikel braucht (``radar.deal_key``, ``sightings``)."""

    cabin: str
    oneway: bool
    dests: frozenset[str]        # Codes und "~wort"
    price: float
    origins: frozenset[str]      # leer = unbekannt
    airlines: frozenset[str] = frozenset()   # leer = keine genannt
    published: datetime | None = None
    link: str = ""


def site(link: str | None) -> str | None:
    """Host ohne "www." ("travel-dealz.de") - ``None`` ohne Link."""
    host = urlsplit(link or "").hostname or ""
    return host.removeprefix("www.") or None


def naive(when: datetime | None) -> datetime | None:
    """Zeitpunkt als lokale Zeit ohne Zeitzone (Artikel haben UTC, die Datenbank lokal)."""
    if when is not None and when.tzinfo is not None:
        return when.astimezone().replace(tzinfo=None)
    return when


def _specific(dest: str) -> bool:
    """Ein Code oder ein unbekanntes Ziel-Wort - kein Land ohne Flughafen ("~tuerkei")."""
    return not dest.startswith("~") or regions.place_kind(dest[1:]) == "unknown"


def tolerance(a: frozenset[str], b: frozenset[str]) -> float | None:
    """Erlaubter Preisabstand für zwei Airline-Angaben - ``None``: verschiedene Airlines."""
    if a and b:
        if a == b:
            return SAME_AIRLINE_TOLERANCE
        if not a & b:
            return None
    return TOLERANCE


def match(a: DealKey | None, b: DealKey | None) -> bool:
    """Sind ``a`` und ``b`` derselbe Deal? (Regeln siehe Moduldoku)"""
    if a is None or b is None or (a.cabin, a.oneway) != (b.cabin, b.oneway):
        return False
    if not any(_specific(d) for d in a.dests & b.dests):
        return False
    if a.origins and b.origins and not a.origins & b.origins:
        return False
    ta, tb = naive(a.published), naive(b.published)
    if ta is not None and tb is not None and abs(ta - tb) > WINDOW:
        return False
    if a.link and b.link and site(a.link) == site(b.link) \
            and feed.normalize_link(a.link) != feed.normalize_link(b.link):
        return False            # zwei Artikel derselben Seite sind zwei Deals
    tol = tolerance(a.airlines, b.airlines)
    if tol is None or not a.price or not b.price or a.price <= 0 or b.price <= 0:
        return False
    lo, hi = sorted((a.price, b.price))
    return hi <= lo * (1 + tol)
