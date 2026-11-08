"""Preisquelle: Google Flights (inoffiziell - serverseitig gerenderte Ergebnisseite).

Google Flights hat keine freie API. Die Ergebnisseite

    https://www.google.com/travel/flights?hl=de&curr=EUR&q=Flights%20from%20ZRH%20to%20HAM
        %20on%202027-01-15%20through%202027-01-19

enthält die Ergebniszeilen aber bereits im HTML (kein JavaScript nötig): pro Flug ein
``aria-label`` wie

    "Ab 183 Euro für Hin- und Rückflug. Nonstop-Flug mit SWISS. Abflug von Flughafen Zürich
     am Freitag, Januar 15, 07:00 und Ankunft an Flughafen Hamburg am Freitag, Januar 15, 08:35.
     Gesamtdauer: 1 Std. 35 Min.."

sowie Preis-Spans ``<span aria-label="183 Euro" role="text">183 €</span>``. Ein schlichter
HTTP-GET mit Browser-User-Agent und ``Accept-Language: de-DE`` genügt. Dieselben Verbindungen
stehen auch im Datenblock ``ds:1`` (``ds1_results``) - Rückfall für Seiten, die nur die Daten
mitbringen (live gesehen, Abgleich mit den HTML-Zeilen auf 20 echten Seiten deckungsgleich).

Das ist **inoffiziell**: Google kann die Seite jederzeit umbauen oder mit einer Consent-/
Captcha-Seite bzw. HTTP 429/403 antworten. Deshalb wird defensiv geparst, zwischen Anfragen
2-4 s (zufällig) gewartet, pro Lauf höchstens ``max_requests`` Mal angefragt und bei einer
Blockade sofort mit ``SourceError`` abgebrochen (Schutzschalter ``_down`` für den Lauf).

Weil die Preise echte Live-Preise sind, fließen sie in die Preis-Historie ein
(``records_history = True``) und dienen zur Gegenprüfung gecachter Quellen (``verify``).

Jede Seite bringt außerdem Googles Preisniveau mit (``google_insights``: Tiefst-/Normalpreis,
typische Spanne, Verlauf); es hängt an jeder Offer (``Offer.insight``) und jeder Gegenprüfung.
Seiten ohne Ergebnisse werden eingeordnet (``empty_kind``, gemessen 05.11.2026, 42 Abrufe) und
je Fall höchstens ein zweites Mal abgerufen (zählt aufs Budget):

* ``error`` - ``ds:1`` ist eine ErrorResponse (Code 13), vorübergehend: dieselbe URL nach 4-6 s.
* ``unparsed`` - Google hat den Suchtext nicht verstanden und zeigt die allgemeine Startseite
  (kommt für denselben Text jedes Mal, betraf "… business class"): die Suche als tfs-Link
  (``build_tfs_url``, dieselben Angaben als Protobuf). Business/Premium/First fragen deshalb
  gleich per tfs an, Economy per Suchtext; der andere Weg ist jeweils der Rückfall.
* ``no_itineraries`` - gültige Antwort ohne Verbindungen (seltene Langstrecken-Paare mit langem
  Aufenthalt, z. B. HAJ-HGH 12/17 Nächte; mit 7 Nächten oder one-way gab es Flüge): bei
  Hin/Rück einmal mit 7 Nächten, wenn die Alarm-Spanne das erlaubt. Sonst "keine Flüge"
  (``no_results``), kein Fehler.

"Abruf leer" (``empty_fetches``, kein Netzfehler) bleibt nur, wenn Google auch beim zweiten Abruf
einen Fehler meldet bzw. die Suche weder als Text noch als tfs-Link versteht.

Alarme mit ``rotation = true`` fragen statt zwei Terminen je Monat reihum jeden Abflugtag
einzeln als One-way ab (``dealmonitor.rotation``), im Rahmen ihres Budget-Anteils. Zwischen zwei
Prüfungen eines Tages gilt der zuletzt gesehene Preis (Quellen-Zustand ``google-rotation``);
Hin/Rück-Alarme bekommen daraus zusätzlich Angebote "kombiniert aus 2 One-way".
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import html as html_mod
import logging
import random
import re
import time
import zlib
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlsplit

from .. import http, regions, rotation
from ..models import Alert, Offer
from . import SourceError, google_insights

log = logging.getLogger(__name__)

BASE = "https://www.google.com/travel/flights"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9",
    "Accept-Encoding": "gzip",
}
TIMEOUT = 30
BLOCKED_MESSAGE = "Google Flights blockiert gerade – später erneut"
#: Obergrenze für eine Ergebnisseite (komprimiert; normal ~0,3 MB gzip / 2,4 MB entpackt)
MAX_PAGE_BYTES = 8_000_000
#: günstigste Ergebnisse je Abfrage, die als Offers weitergegeben werden
MAX_RESULTS_PER_QUERY = 10
CABIN_QUERY = {"economy": "", "premium": " premium economy class",
               "business": " business class", "first": " first class"}

#: Ziele innerhalb Europas -> Kurzstrecke (7 Nächte Standardaufenthalt), sonst 12 Nächte
EUROPE_CODES: frozenset[str] = frozenset(
    code for key in ("DE", "CH", "AT", "DACH") for code in regions.REGIONS[key]
) | frozenset("""
    LON LHR LGW STN LTN LCY MAN EDI GLA BHX BRS DUB ORK PAR CDG ORY NCE LYS MRS TLS BOD
    AMS RTM EIN BRU CRL LUX MAD BCN AGP PMI IBZ VLC SVQ ALC BIO LIS OPO FAO ROM FCO CIA
    MIL MXP LIN BGY VCE NAP FLO BLQ PSA CTA PMO CAG OLB ATH SKG HER RHO JTR CFU CPH AAR
    BLL OSL BGO TRD SVG STO ARN BMA GOT MMX HEL TKU TLL RIX VNO WAW KRK GDN WRO POZ KTW
    PRG BRQ BUD BTS LJU ZAG SPU DBV BEG SOF VAR BOJ OTP CLJ TIA SKP TGD PRN IST SAW ADB
    AYT DLM BJV ESB KEF REK TIV LCA PFO MLA EFL ZTH KGS CHQ GRO REU SDR XRY MAH LPA TFS
    TFN ACE FUE SPC EMA LPL NCL LBA ABZ BFS BHD SNN NOC KIR CWL SOU EXT INV JER GCI
""".split())

#: Kennzeichen von Consent-/Captcha-/Sperrseiten (Titel bzw. Formular/Text im Seitenkopf)
BLOCK_TITLE_RE = re.compile(
    r"<title>[^<]*(?:Bevor (?:du|Sie) zu Google weiterge|Before you continue to Google|"
    r"sorry/index|Zugriff verweigert|Access denied)[^<]*</title>", re.I)
BLOCK_BODY_RE = re.compile(
    r"<form[^>]+consent\.google\.com|ungewöhnlichen Datenverkehr|unusual traffic|"
    r"www\.google\.com/sorry/|id=\"recaptcha\"|class=\"g-recaptcha\"", re.I)
NO_FLIGHTS_RE = re.compile(r"keine (?:Flüge|Ergebnisse|passenden Flüge)|no flights found", re.I)
#: Notbehelf, wenn die Strukturmerkmale fehlen: leere Seiten 1,91-1,99 MB, Ergebnisseiten
#: 2,29-2,66 MB (entpackt, gemessen 05.11.2026)
EMPTY_PAGE_MAX_CHARS = 2_100_000
#: Kennzeichen einer echten Google-Flights-Seite (sonst: anderes Layout -> kein zweiter Versuch)
GOOGLE_PAGE_RE = re.compile(r"AF_initDataCallback|Google Fl(?:ü|ue)ge|Google Flights")
#: Titel der allgemeinen Startseite (Suchtext nicht verstanden)
UNPARSED_TITLE_RE = re.compile(r"<title>\s*Günstige Flüge weltweit finden")
#: Wartezeit vor dem zweiten Abruf derselben URL (Sekunden, zufällig aus der Spanne)
RETRY_WAIT = {"error": (4.0, 6.0), "empty": (4.0, 6.0)}
#: Aufenthalt für den zweiten Abruf, wenn Google für den langen Aufenthalt keine Hin/Rück-Flüge hat
SHORT_STAY_NIGHTS = 7
#: so viele Hinweise je Lauf für die Seite "Quellen"
MAX_NOTES = 8
TFS_BASE = BASE + "/search"
#: Kabine im tfs-Protobuf (Feld 9)
TFS_CABIN = {"economy": 1, "premium": 2, "business": 3, "first": 4}
CABIN_LABEL = {"economy": "", "premium": " Premium Economy", "business": " Business",
               "first": " First"}

# -- Parser-Regexe ----------------------------------------------------------------------
LABEL_RE = re.compile(r"""aria-label=(["'])(Ab\s+\d[\d.]*(?:,\d+)?\s+Euro\b.*?)\1""", re.S)
PRICE_SPAN_RE = re.compile(r"""<span[^>]*aria-label=(["'])(\d[\d.]*(?:,\d+)?)\s+Euro\1[^>]*>""")
PRICE_RE = re.compile(r"Ab\s+(\d[\d.]*)(?:,(\d+))?\s+Euro")
AIRLINE_RE = re.compile(
    r"(?:Nonstop-)?Flug mit (.+?)"
    r"(?: mit (?:einer|zwei|drei|\d+) Zwischenlandung(?:en)?)?\.(?:\s|$)")
STOPS_WORDS = {"einer": 1, "eine": 1, "zwei": 2, "drei": 3}
STOPS_RE = re.compile(r"mit (einer|zwei|drei|\d+) Zwischenlandung")
STOPS_OF_RE = re.compile(r"Zwischenstopp \(\d+ von (\d+)\)")
STOPS_N_RE = re.compile(r"(\d+) Zwischenstopps?")
DURATION_RE = re.compile(r"Gesamtdauer:\s*(?:(\d+)\s*Std\.?)?\s*(?:(\d+)\s*Min\.?)?")
DEPARTURE_RE = re.compile(
    r"Abflug von (?:Flughafen )?(.+?) am ([^,]+), ([^\s,]+) (\d{1,2}), (\d{1,2}:\d{2})")
LOGO_RE = re.compile(r"airline_logos/\d+px/(?:dark/)?([A-Z0-9]{2})\.png")
#: Preisphrase: "Ab 183 Euro für Hin- und Rückflug." (Hin/Rück) bzw. "Ab 697 Euro." (one-way)
TRIP_PHRASE_RE = re.compile(r"Ab\s+\d[\d.]*(?:,\d+)?\s+Euro(\s+für Hin- und Rückflug)?\s*\.")
#: Ausgewählte Optionen der Suchmaske (Reiseart, Kabine) - Attributname ist eine Zufalls-ID
SELECTED_OPTION_RE = re.compile(
    r"""role=["']option["'][^>]*aria-selected=["']true["'][^>]*=["']"""
    r"""(Economy Class|Premium Economy|Business Class|First Class|"""
    r"""Hin- und Rückreise|Nur Hinreise|Mehrere Städte)["']""")
OPTION_VALUES = {"Economy Class": ("cabin", "economy"), "Premium Economy": ("cabin", "premium"),
                 "Business Class": ("cabin", "business"), "First Class": ("cabin", "first"),
                 "Hin- und Rückreise": ("trip", "roundtrip"), "Nur Hinreise": ("trip", "oneway"),
                 "Mehrere Städte": ("trip", "multi")}


class GoogleBlocked(SourceError):
    """Google antwortet mit Consent-/Captcha-Seite oder HTTP 429/403."""


class ParseError(RuntimeError):
    """Seite geladen, aber keine Ergebnisse erkennbar (Seitenaufbau geändert?)."""


class EmptyPage(ParseError):
    """Abruf leer: ErrorResponse bzw. nicht verstandene Suche auch beim zweiten Versuch."""


# -- URL und Abruf ----------------------------------------------------------------------
def build_url(origin: str, dest: str, departure: date, return_date: date | None = None,
              cabin: str = "economy", roundtrip: bool = True) -> str:
    """Google-Flights-Suche als URL (dient auch als Buchen-/Gegenprüfen-Link)."""
    q = f"Flights from {origin.upper()} to {dest.upper()} on {departure.isoformat()}"
    if roundtrip and return_date:
        q += f" through {return_date.isoformat()}"
    else:
        q += " one way"
    q += CABIN_QUERY.get(cabin, "")
    return f"{BASE}?hl=de&curr=EUR&q={quote(q)}"


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        if not n:
            out.append(byte)
            return bytes(out)
        out.append(byte | 0x80)


def _pb(num: int, value: int | bytes) -> bytes:
    """Ein Protobuf-Feld: Zahl (Varint) oder Bytes/eingebettete Nachricht (Länge + Inhalt)."""
    if isinstance(value, int):
        return _varint(num << 3) + _varint(value)
    return _varint(num << 3 | 2) + _varint(len(value)) + value


def build_tfs_url(origin: str, dest: str, departure: date, return_date: date | None = None,
                  cabin: str = "economy", roundtrip: bool = True) -> str:
    """Dieselbe Suche als ``tfs``-Link (so baut Google seine eigenen Such-URLs, "CBwQAh…").

    Protobuf: 1 = 28, 2 = 2, je Strecke 3 = {2: Datum, 13: {1: 1, 2: Abflug}, 14: {1: 1, 2: Ziel}},
    8 = 1 (ein Erwachsener), 9 = Kabine (1-4), 19 = Reiseart (1 Hin/Rück, 2 one-way).
    """
    def airport(code: str) -> bytes:
        return _pb(1, 1) + _pb(2, code.upper().encode())

    legs = [(origin, dest, departure)]
    if roundtrip and return_date:
        legs.append((dest, origin, return_date))
    msg = _pb(1, 28) + _pb(2, 2)
    for o, d, day in legs:
        msg += _pb(3, _pb(2, day.isoformat().encode()) + _pb(13, airport(o)) + _pb(14, airport(d)))
    msg += _pb(8, 1) + _pb(9, TFS_CABIN.get(cabin, 1)) + _pb(19, 1 if len(legs) == 2 else 2)
    tfs = base64.urlsafe_b64encode(msg).decode().rstrip("=")
    return f"{TFS_BASE}?tfs={tfs}&hl=de&curr=EUR"


def search_urls(origin: str, dest: str, departure: date, return_date: date | None = None,
                cabin: str = "economy", roundtrip: bool = True) -> tuple[str, str]:
    """(erster Abruf, Rückfall bei nicht verstandener Suche): Economy per Suchtext, sonst per
    tfs-Link - "… business class" verstand Google bei 3 von 5 Texten nicht, tfs 2 von 2."""
    q = build_url(origin, dest, departure, return_date, cabin, roundtrip)
    tfs = build_tfs_url(origin, dest, departure, return_date, cabin, roundtrip)
    return (q, tfs) if cabin == "economy" else (tfs, q)


def blocked_reason(url: str, html: str) -> str | None:
    """Erkennt Consent-/Captcha-/Sperrseiten (None = normale Ergebnisseite)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = None
    if parts is not None:
        host = parts.netloc.lower()
        if host.startswith("consent.") or "/sorry" in parts.path:
            return f"Weiterleitung auf {host}{parts.path}"
    head = html[:200_000]
    if BLOCK_TITLE_RE.search(head):
        return "Consent-/Sperrseite statt Ergebnissen"
    if BLOCK_BODY_RE.search(head):
        return "Captcha-/Consent-Formular statt Ergebnissen"
    return None


def fetch_html(url: str, timeout: float = TIMEOUT) -> str:
    """Lädt eine Google-Flights-Seite. ``GoogleBlocked`` bei Sperre, ``HttpError`` sonst."""
    try:
        resp = http.request(url, headers=HEADERS, timeout=timeout, retries=0, full=True,
                            max_bytes=MAX_PAGE_BYTES)
    except http.HttpError as e:
        if e.status in (403, 429):
            raise GoogleBlocked(f"HTTP {e.status}") from e
        raise
    body = resp.body
    encoding = resp.header("Content-Encoding").lower()
    try:
        if "gzip" in encoding:
            body = gzip.decompress(body)
        elif "deflate" in encoding:
            body = zlib.decompress(body)
    except (OSError, zlib.error, EOFError) as e:
        log.warning("Google Flights: %s-Antwort nicht dekodierbar (%s), nutze Rohdaten",
                    encoding, e)
    text = body.decode("utf-8", errors="replace")
    reason = blocked_reason(resp.url, text)
    if reason:
        raise GoogleBlocked(reason)
    return text


# -- Parser -----------------------------------------------------------------------------
def _int(value: str) -> int:
    return int(value.replace(".", ""))


def parse_label(label: str) -> dict | None:
    """Zerlegt einen Ergebnis-``aria-label``-Text ("Ab 183 Euro für Hin- und Rückflug. ...")."""
    text = html_mod.unescape(label).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    m = PRICE_RE.search(text)
    if not m:
        return None
    price = float(_int(m.group(1)))
    if m.group(2):
        price += float("0." + m.group(2))
    roundtrip: bool | None = None
    if (t := TRIP_PHRASE_RE.search(text)):
        roundtrip = bool(t.group(1))
    elif "Hin- und Rückflug" in text:
        roundtrip = True
    stops: int | None = None
    if re.search(r"\bNonstop\b", text):
        stops = 0
    elif (m := STOPS_RE.search(text)):
        word = m.group(1)
        stops = STOPS_WORDS.get(word) or int(word)
    elif (m := STOPS_OF_RE.search(text)):
        stops = int(m.group(1))
    elif (m := STOPS_N_RE.search(text)):
        stops = int(m.group(1))
    airline = None
    if (m := AIRLINE_RE.search(text)):
        airline = m.group(1).strip()
    duration = None
    if (m := DURATION_RE.search(text)) and (m.group(1) or m.group(2)):
        duration = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    departure_airport = departure_time = None
    if (m := DEPARTURE_RE.search(text)):
        departure_airport, departure_time = m.group(1), m.group(5)
    return {
        "price": price,
        "airline": airline,
        "airline_code": None,
        "stops": stops,
        "duration_min": duration,
        "departure_time": departure_time,
        "departure_airport": departure_airport,
        "roundtrip": roundtrip,
        "label": text,
    }


def _names(names: list[str]) -> str | None:
    """["Lufthansa", "SWISS", "Air China"] -> "Lufthansa, SWISS und Air China" (wie im Label)."""
    names = [n for n in names if isinstance(n, str) and n]
    if len(names) < 2:
        return names[0] if names else None
    return ", ".join(names[:-1]) + " und " + names[-1]


def _ds1_row(item) -> dict | None:
    """Eine Verbindung aus ``ds:1`` (Index 2/3): ``[[code, [namen], [strecken…], abflug, [jahr,
    monat, tag], [h, m], …, dauer_min (9)], [[None, preis], token]]`` - Form wie in Googles
    eigener Antwort, mit den HTML-Zeilen von 26 echten Seiten abgeglichen (05.11.2026)."""
    info, fare = item[0], item[1]
    price = fare[0][1]
    if not isinstance(price, (int, float)) or isinstance(price, bool) or price <= 0:
        return None
    names = info[1] if isinstance(info[1], list) else []
    legs = info[2] if isinstance(info[2], list) else []
    clock = info[5] if len(info) > 5 and isinstance(info[5], list) and info[5] else None
    first = legs[0] if legs and isinstance(legs[0], list) else []
    airport = first[4] if len(first) > 4 and isinstance(first[4], str) else None
    duration = info[9] if len(info) > 9 and isinstance(info[9], int) else None
    return {
        "price": float(price),
        "airline": _names(names),
        "airline_code": info[0] if len(names) == 1 and isinstance(info[0], str) else None,
        "stops": len(legs) - 1 if legs else None,
        "duration_min": duration,
        "departure_time": (f"{clock[0]:02d}:{(clock[1] if len(clock) > 1 and clock[1] else 0):02d}"
                           if clock and isinstance(clock[0], int) else None),
        "departure_airport": re.sub(r"^Flughafen\s+", "", airport) if airport else None,
        "roundtrip": None,
        "label": f"{price:g} Euro (aus ds:1)",
    }


def ds1_results(html: str) -> list[dict]:
    """Verbindungen aus dem ``ds:1``-Datenblock - für Seiten, die die Daten mitbringen, aber
    keine Ergebniszeilen im HTML (live gesehen 05.11.2026: FRA-PEK Business, 2,0 MB)."""
    data = google_insights.ds1_data(html)
    if not isinstance(data, list) or len(data) < 4 or data[0] == google_insights.ERROR_CODE:
        return []
    rows = []
    for block in (data[2], data[3]):
        if not (isinstance(block, list) and block and isinstance(block[0], list)):
            continue
        for item in block[0]:
            try:
                row = _ds1_row(item)
            except (TypeError, IndexError, KeyError, ValueError):
                row = None
            if row:
                rows.append(row)
    return rows


def parse_results(html: str) -> list[dict]:
    """Alle Flugergebnisse einer Google-Flights-Seite, dedupliziert und nach Preis sortiert.

    Jedes Ergebnis: ``price`` (EUR), ``airline`` (Name, z. B. "SWISS"), ``airline_code``
    (IATA aus dem Logo, falls eindeutig), ``stops``, ``duration_min``, ``departure_time``
    ("07:00"), ``roundtrip`` (True/False/None), ``label`` (Originaltext).
    Findet der Parser keine ``aria-label``-Zeilen, gelten die Verbindungen aus ``ds:1``
    (``ds1_results``), danach als letzte Rückfallebene der Preis aus den
    ``<span aria-label="183 Euro">``-Elementen (ohne Airline/Umstiege).
    """
    matches = list(LABEL_RE.finditer(html))
    results: list[dict] = []
    seen: set[tuple] = set()
    for i, m in enumerate(matches):
        row = parse_label(m.group(2))
        if not row:
            continue
        # Der Rest des Listeneintrags enthält Logo (IATA-Code) und Preis-Span
        end = matches[i + 1].start() if i + 1 < len(matches) else min(len(html), m.end() + 40_000)
        li_end = html.find("</li>", m.end(), end)
        segment = html[m.end():li_end if li_end != -1 else end]
        codes = {c for c in LOGO_RE.findall(segment)}
        if len(codes) == 1:
            row["airline_code"] = codes.pop()
        key = (row["price"], row["airline"], row["stops"], row["departure_time"],
               row["duration_min"])
        if key in seen:
            continue
        seen.add(key)
        results.append(row)
    if not results:
        for row in ds1_results(html):
            key = (row["price"], row["airline"], row["stops"], row["departure_time"],
                   row["duration_min"])
            if key not in seen:
                seen.add(key)
                results.append(row)
    if not results:
        prices: list[float] = []
        for m in PRICE_SPAN_RE.finditer(html):
            value = float(_int(m.group(2).split(",")[0]))
            if value not in prices:
                prices.append(value)
        results = [{"price": p, "airline": None, "airline_code": None, "stops": None,
                    "duration_min": None, "departure_time": None, "departure_airport": None,
                    "roundtrip": None, "label": f"{p:g} Euro"} for p in prices]
    results.sort(key=lambda r: (r["price"], r["stops"] if r["stops"] is not None else 9,
                                r["duration_min"] or 0))
    return results


def _route_understood(page: str) -> bool | None:
    """Hat Google Abflug und Ziel der Suche erkannt? (``ds:0``: je Strecke ``[[[["FRA", 0]]],
    [[["PEK", 0]]], …]``, nicht verstanden: ``[[], [], None, 0]``; None = nicht lesbar)."""
    data = google_insights.ds0_data(page)
    try:
        legs = data[1][1][13]
    except (TypeError, IndexError, KeyError):
        return None
    if not isinstance(legs, list):
        return None
    return any(isinstance(leg, list) and len(leg) >= 2 and leg[0] and leg[1] for leg in legs)


def _answer_without_itineraries(page: str) -> bool:
    """``ds:1`` ist eine gültige Antwort (Kennung ``[[1]]``), aber ohne Ergebnislisten
    (Index 2 und 3 = null; mit Flügen stehen dort die Listen)."""
    data = google_insights.ds1_data(page)
    if not (isinstance(data, list) and len(data) > 3 and isinstance(data[0], list)):
        return False
    head = data[0]
    valid = len(head) > 1 and isinstance(head[1], list) and [[1]] in head[1]
    return valid and data[2] is None and data[3] is None


def empty_kind(page: str, results: list[dict]) -> str | None:
    """Warum eine Google-Seite keine Ergebnisse hat (gemessen 05.11.2026):

    * ``"error"`` - ``ds:1`` ist eine ErrorResponse (vorübergehend; auch mit Zeilen gemeldet,
      dann fehlt nur das Preisniveau),
    * ``"unparsed"`` - Suche nicht verstanden: ``ds:0`` ohne Strecke bzw. Titel der Startseite,
    * ``"no_itineraries"`` - gültige Antwort ohne Verbindungen,
    * ``"empty"`` - Notbehelf ohne lesbare Merkmale: kleine Google-Seite ohne Ergebnisse,
    * ``None`` - Ergebnisse da, "keine Flüge" sichtbar oder keine Google-Seite.
    """
    if google_insights.is_error_response(page):
        return "error"
    if results or not GOOGLE_PAGE_RE.search(page):
        return None
    if _route_understood(page) is False or UNPARSED_TITLE_RE.search(page[:5000]):
        return "unparsed"
    if _answer_without_itineraries(page):
        return "no_itineraries"
    if not NO_FLIGHTS_RE.search(page) and len(page) < EMPTY_PAGE_MAX_CHARS:
        return "empty"
    return None


def short_stay(nights: int, min_nights: int | None, max_nights: int | None) -> int | None:
    """Kürzerer Aufenthalt für den zweiten Abruf: 7 Nächte (bzw. ``min_nights``, falls größer),
    wenn das in die Alarm-Spanne passt und kürzer als ``nights`` ist - sonst None."""
    short = max(SHORT_STAY_NIGHTS, min_nights or 0, 1)
    if (max_nights is not None and short > max_nights) or short >= nights:
        return None
    return short


def page_settings(html: str) -> dict[str, str | None]:
    """Reiseart und Kabine, die Google tatsächlich angewendet hat (aus der Suchmaske).

    ``{"trip": "roundtrip"|"oneway"|"multi"|None, "cabin": "economy"|...|None}`` - None,
    wenn die Angabe nicht gefunden wurde (Seitenaufbau geändert).
    """
    out: dict[str, str | None] = {"trip": None, "cabin": None}
    for m in SELECTED_OPTION_RE.finditer(html):
        key, value = OPTION_VALUES[m.group(1)]
        if out[key] is None:
            out[key] = value
    return out


def row_problem(row: dict, *, max_stops: int | None = None, max_minutes: float | None = None,
                exclude_airlines=()) -> str | None:
    """Warum eine Ergebniszeile nicht zu den Alarm-Regeln passt (None = passt)."""
    stops, minutes = row.get("stops"), row.get("duration_min")
    if max_stops is not None and stops is not None and stops > max_stops:
        return f"{stops} Umstiege"
    if max_minutes is not None and minutes is not None and minutes > max_minutes:
        return f"{minutes / 60:.1f} h"
    excluded = {a.upper() for a in exclude_airlines or ()}
    if excluded:
        code = (row.get("airline_code") or "").upper()
        name = (row.get("airline") or "").upper()
        if code in excluded or name in excluded:
            return f"Airline {row.get('airline') or code} ausgeschlossen"
    return None


def alert_criteria(alert: Alert) -> dict:
    """Umstiege, Dauer und ausgeschlossene Airlines eines Alarms (für ``row_problem``)."""
    return {"max_stops": alert.max_stops,
            "max_minutes": alert.max_duration_hours * 60 if alert.max_duration_hours else None,
            "exclude_airlines": alert.exclude_airlines}


# -- Datumsplanung ----------------------------------------------------------------------
def _months(start: date, count: int) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    for _ in range(max(count, 0)):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def is_longhaul(origin: str, dest: str) -> bool:
    return origin.upper() not in EUROPE_CODES or dest.upper() not in EUROPE_CODES


def stay_nights(alert: Alert, origin: str, dest: str) -> int:
    """Aufenthalt für den Scan: 7 (Europa) bzw. 12 Nächte, begrenzt auf min/max_nights.

    Nicht die Mitte der Spanne: bei 5-30 Nächten wären das 17 - für seltene Langstrecken-Paare
    liefert Google bei so langen Aufenthalten keine Hin/Rück-Flüge (gemessen 05.11.2026)."""
    nights = 12 if is_longhaul(origin, dest) else 7
    if alert.min_nights is not None:
        nights = max(nights, alert.min_nights)
    if alert.max_nights is not None:
        nights = min(nights, alert.max_nights)
    return max(nights, 1)


def scan_dates(year: int, month: int, today: date, *, min_lead_days: int = 2) -> list[date]:
    """Zwei Abflugtage je Monat: der Freitag und der Dienstag um die Monatsmitte (12.-18.).

    Liegt der Tag schon (fast) in der Vergangenheit, wird um eine Woche verschoben, solange
    der Monat reicht - sonst entfällt der Termin.
    """
    out: list[date] = []
    anchor = date(year, month, 12)
    for weekday in (4, 1):   # Freitag, Dienstag
        day = anchor + timedelta(days=(weekday - anchor.weekday()) % 7)
        while day.month == month and day < today + timedelta(days=min_lead_days):
            day += timedelta(days=7)
        if day.month == month and day.year == year:
            out.append(day)
    return out


# -- Quelle -----------------------------------------------------------------------------
class GoogleFlightsSource:
    name = "google_flights"
    records_history = True

    #: so viele nicht auswertbare Seiten hintereinander -> Lauf abbrechen (Seitenaufbau geändert?)
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(
        self,
        max_requests: int = 40,
        delay: float = 3.0,
        verify_only: bool = False,
        *,
        scan_months: int = 6,
        today: date | None = None,
        timeout: float = TIMEOUT,
        fetcher=None,
        seed: int | None = None,
        verify_reserve: int = 0,
        state_store=None,
        period_hours: float = rotation.PERIOD_HOURS,
        now: datetime | None = None,
    ) -> None:
        self.max_requests = max(int(max_requests), 0)
        #: so viele Abfragen lässt der Scan für die Gegenprüfung übrig
        self.verify_reserve = min(max(int(verify_reserve), 0), self.max_requests)
        self.delay = max(float(delay), 0.0)
        self.verify_only = verify_only
        self.scan_months = scan_months
        self.today = today
        self.timeout = timeout
        #: ``fetcher(url) -> html`` - für Tests/Demo austauschbar (Standard: ``fetch_html``)
        self.fetcher = fetcher
        self.rng = random.Random(seed)
        #: Store für das Rotations-Gedächtnis (None = gilt nur für dieses Objekt)
        self.state_store = state_store
        #: Takt der vollen Läufe - danach zählt der Lauf-Index der Rotation weiter
        self.period_hours = period_hours
        #: nur volle geplante Läufe zählen den Lauf-Index der Rotation weiter; Sofort-Prüfung
        #: und "Nur diesen prüfen" setzen False (``scheduler.execute``) - sonst übersprängen
        #: die vollen Läufe danach ganze Fenster
        self.advance_rotation = True
        self.now = now
        self._memory: rotation.Memory | None = None
        #: nach ``fetch``: None = alles abgefragt, sonst {"message": str, "routes": {(o, d), ...}}
        self.incomplete: dict | None = None
        #: Grund des letzten gescheiterten ``verify`` (None = ok)
        self.last_error: str | None = None
        self.reset()

    # -- Zustand pro Lauf ----------------------------------------------------
    def reset(self) -> None:
        """Zähler, Cache und Schutzschalter für einen neuen Lauf zurücksetzen."""
        self.requests_made = 0
        self.scans_made = 0          # echte Scan-Abfragen (ohne Gegenprüfungen)
        self.verifications = 0
        self.retries = 0             # zweite Abrufe (ErrorResponse, tfs-Link, 7 Nächte)
        self.empty_fetches = 0       # "Abruf leer": Fehler bzw. Suche nicht verstanden
        self.no_results = 0          # Google antwortete gültig, aber ohne Flüge
        self.rescued = 0             # zweiter Abruf (tfs-Link bzw. 7 Nächte) brachte Flüge
        #: Hinweise für die Seite "Quellen" (``_note``)
        self.notes: list[str] = []
        #: Scan-Abfragen, die den noch nicht geprüften Alarmen zustehen (``begin_run``) -
        #: so viel lässt die Gegenprüfung frei, damit spätere Alarme nicht leer ausgehen
        self._scan_pending = 0
        self.incomplete = None
        self.last_error = None
        self._down: str | None = None
        self._cache: dict[str, list[dict]] = {}
        #: Preisniveau je abgefragter URL (``google_insights.parse``; None = keins)
        self._insights: dict[str, dict | None] = {}
        #: je Abfrage-URL: (geladene URL, tatsächlicher Rückflug), wenn es Ergebnisse gab
        self._loaded: dict[str, tuple[str, date | None]] = {}
        self._last_request_at: float | None = None
        self._consecutive_failures = 0
        self._consecutive_empty = 0
        #: Scan-Budget je Alarm (``begin_run``); ohne Eintrag gilt das ganze Scan-Budget
        self._shares: dict[str, int] = {}
        #: so lange gelten Preise im Rotations-Gedächtnis (längster Durchgang dieses Laufs)
        self._keep_hours = 0.0

    @property
    def scan_budget(self) -> int:
        """Abfragen, die der Scan insgesamt verbrauchen darf (Rest bleibt der Gegenprüfung)."""
        return max(self.max_requests - self.verify_reserve, 0)

    def begin_run(self, alerts: list[Alert]) -> None:
        """Scan-Budget fair auf die Alarme des Laufs verteilen: jeder bekommt gleich viel,
        wer weniger braucht, gibt den Rest an die anderen weiter. Sonst verbrauchen die ersten
        Alarme alles und die letzten (z. B. China mit hunderten Strecken) bekommen nie Google."""
        self._shares = {}
        if self.verify_only:
            return
        needs = sorted((len(self.plan(a)), a.id) for a in alerts)
        budget = self.scan_budget
        for k, (need, alert_id) in enumerate(needs):
            given = min(need, budget // (len(needs) - k))   # kleinster Bedarf zuerst
            self._shares[alert_id] = given
            budget -= given
        self._scan_pending = sum(self._shares.values())

    @property
    def remaining(self) -> int:
        return max(self.max_requests - self.requests_made, 0)

    @property
    def verify_limit(self) -> int:
        """Höchstens die Hälfte des Budgets geht in Gegenprüfungen."""
        return self.max_requests // 2

    @property
    def down(self) -> str | None:
        return self._down

    # -- Abruf ----------------------------------------------------------------
    def _pause(self, minimum: tuple | float = 0.0) -> None:
        if self._last_request_at is None or self.delay <= 0:
            return
        if isinstance(minimum, tuple):
            minimum = self.rng.uniform(*minimum)
        wanted = max(self.rng.uniform(max(self.delay - 1.0, 0.0), self.delay + 1.0), minimum)
        wait = wanted - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)

    def query(self, origin: str, dest: str, departure: date, return_date: date | None,
              cabin: str = "economy", roundtrip: bool = True, *, retry: bool = True,
              nights: tuple[int | None, int | None] | None = None) -> list[dict]:
        """Eine Google-Abfrage (gecacht pro Lauf, zählt auf ``max_requests``).

        Wirft ``GoogleBlocked`` (und setzt den Schutzschalter), ``http.HttpError`` bei
        Netzwerkfehlern, ``ParseError`` wenn keine Ergebnisse erkennbar sind (``EmptyPage``:
        Abruf leer) und ``SourceError`` wenn das Budget erschöpft ist.
        Seite ohne Ergebnisse -> je nach ``empty_kind`` höchstens ein zweiter Abruf (zählt aufs
        Budget; ``retry=False``: keiner, weil der Anteil des Alarms schon aufgebraucht ist):
        ErrorResponse -> dieselbe URL, Suche nicht verstanden -> anderer URL-Stil (Suchtext/tfs),
        Hin/Rück ohne Verbindungen -> mit 7 Nächten, wenn ``nights`` = (min, max) das erlaubt.
        Schlüssel für Cache und ``insight`` ist immer ``build_url`` der angefragten Daten; welche
        Seite und welcher Rückflug tatsächlich galten, steht in ``loaded(url)``.
        """
        key = build_url(origin, dest, departure, return_date, cabin, roundtrip)
        if key in self._cache:
            return self._cache[key]
        if self._down:
            raise GoogleBlocked(self._down)
        if self.requests_made >= self.max_requests:
            raise SourceError(f"Google Flights: Limit von {self.max_requests} Abfragen erreicht")
        url, alternate = search_urls(origin, dest, departure, return_date, cabin, roundtrip)
        ret = return_date
        page = self._load(url)
        results = parse_results(page)
        first = kind = empty_kind(page, results)
        second = None
        if kind and retry and self.requests_made < self.max_requests:
            second = self._second_fetch(kind, url, alternate, origin, dest, departure,
                                        return_date, cabin, roundtrip, nights)
        if second:
            # ErrorResponse mit Flügen: die erste Seite gilt, falls der zweite Abruf nichts
            # Besseres bringt (Fehlerseite ohne Flüge oder Netzfehler) - nur das Preisniveau fehlt
            kept = (page, results, url, ret) if results else None
            url, ret, wait = second
            log.info("Google Flights: %s, zweiter Abruf %s", kind, url)
            self.retries += 1
            try:
                page = self._load(url, wait=wait)
            except http.HttpError:
                if kept is None:
                    raise
                page = None
            results = parse_results(page) if page is not None else []
            kind = empty_kind(page, results) if page is not None else None
            if kept and not results:
                log.info("Google Flights: zweiter Abruf ohne Flüge, erste Seite gilt")
                page, results, url, ret = kept
                kind, second = None, None
                self._consecutive_failures = 0
        label = self._label(origin, dest, departure, return_date, cabin)
        if not results:
            if "no_itineraries" in (first, kind) or (kind is None and NO_FLIGHTS_RE.search(page)):
                return self._no_results(key, label, first, kind, roundtrip, return_date,
                                        departure, ret if second else None)
            if kind or first in ("error", "unparsed", "empty"):
                self._empty_fetch(self._empty_reason(first, kind, bool(second)))
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                self._down = ("Google Flights liefert keine auswertbaren Ergebnisse "
                              f"({self._consecutive_failures} Seiten hintereinander) – "
                              "Seitenaufbau geändert oder Sperre?")
                raise GoogleBlocked(self._down)
            raise ParseError("keine Ergebnisse auf der Seite erkannt")
        self._consecutive_failures = 0
        self._consecutive_empty = 0
        # Sicherheitsnetz: nie Economy-Preise als Business melden oder One-way als Hin/Rück -
        # falls Google die Angaben aus der Suchanfrage ignoriert hat
        applied = page_settings(page)
        if applied["cabin"] and applied["cabin"] != cabin:
            raise ParseError(f"Google zeigt {applied['cabin']} statt {cabin}")
        wanted_trip = "roundtrip" if roundtrip else "oneway"
        if applied["trip"] and applied["trip"] != wanted_trip:
            raise ParseError(f"Google zeigt {applied['trip']} statt {wanted_trip}")
        results = [r for r in results if r["roundtrip"] is None or r["roundtrip"] == roundtrip]
        if second and first in ("unparsed", "no_itineraries"):
            self.rescued += 1
            tfs = url.startswith(TFS_BASE)
            what = (f"Google ohne Hin/Rück-Flüge für {(return_date - departure).days} Nächte, "
                    f"mit {(ret - departure).days} Nächten geprüft" if first == "no_itineraries"
                    else "Google verstand den " + ("Suchtext nicht, per tfs-Link geprüft" if tfs
                                                   else "tfs-Link nicht, per Suchtext geprüft"))
            best = f" ab {results[0]['price']:,.0f} €".replace(",", ".") if results else ""
            self._note(f"{label}: {what} – {len(results)} Flüge{best}")
        self._insights[key] = google_insights.parse(page)
        self._cache[key] = results
        self._loaded[key] = (url, ret)
        return results

    def _second_fetch(self, kind: str, url: str, alternate: str, origin: str, dest: str,
                      departure: date, return_date: date | None, cabin: str, roundtrip: bool,
                      nights) -> tuple[str, date | None, tuple | float] | None:
        """(URL, Rückflug, Mindestpause) für den zweiten Abruf - None = keiner lohnt."""
        if kind in RETRY_WAIT:          # vorübergehend: dieselbe URL, etwas später
            return url, return_date, RETRY_WAIT[kind]
        if kind == "unparsed":          # kommt für denselben Text jedes Mal: anderer URL-Stil
            return alternate, return_date, 0.0
        if kind == "no_itineraries" and roundtrip and return_date and nights:
            short = short_stay((return_date - departure).days, *nights)
            if short:
                ret = departure + timedelta(days=short)
                return search_urls(origin, dest, departure, ret, cabin, True)[0], ret, 0.0
        return None

    def _no_results(self, key: str, label: str, first, kind, roundtrip: bool,
                    return_date: date | None, departure: date, tried: date | None) -> list:
        """Google hat gültig geantwortet, aber ohne Flüge - kein Fehler, kein "Abruf leer"."""
        self.no_results += 1
        self._consecutive_failures = 0
        self._consecutive_empty = 0
        if "no_itineraries" in (first, kind) and roundtrip and return_date:
            text = f"{label}: Google ohne Hin/Rück-Flüge für {(return_date - departure).days} Nächte"
            if tried and tried != return_date:
                short = (tried - departure).days
                text += {"no_itineraries": f", auch mit {short} Nächten keine",
                         "error": f", mit {short} Nächten meldete Google einen Fehler"}.get(
                    kind, f", mit {short} Nächten nicht auswertbar")
            self._note(text)
        log.info("Google Flights: keine Flüge für %s", label)
        self._insights[key] = None
        self._cache[key] = []
        return []

    @staticmethod
    def _empty_reason(first, kind, retried: bool) -> str:
        if "unparsed" in (first, kind) and kind != "error":
            return ("Abruf leer – Google hat die Suche nicht verstanden"
                    + (" (weder als Suchtext noch als tfs-Link)" if retried else ""))
        if "error" in (first, kind):
            return ("Abruf leer – Google meldete einen Fehler (ErrorResponse)"
                    + (" auch beim zweiten Abruf" if retried else ""))
        return ("Abruf leer – Google zeigte keine Flüge"
                + (" auch beim zweiten Versuch" if retried else ""))

    @staticmethod
    def _label(origin: str, dest: str, departure: date, return_date: date | None,
               cabin: str) -> str:
        days = (f"{departure:%d.%m.}–{return_date:%d.%m.%Y}" if return_date
                else f"{departure:%d.%m.%Y}")
        return f"{origin.upper()}–{dest.upper()}{CABIN_LABEL.get(cabin, '')} {days}"

    def _note(self, text: str) -> None:
        """Hinweis für die Seite "Quellen" (nur Gemessenes, höchstens ``MAX_NOTES`` je Lauf)."""
        if len(self.notes) < MAX_NOTES:
            self.notes.append(text)

    def loaded(self, url: str) -> tuple[str, date | None] | None:
        """(tatsächlich geladene URL, tatsächlicher Rückflug) zur Abfrage ``url`` (``build_url``)
        - None, wenn sie in diesem Lauf nicht mit Ergebnissen geladen wurde."""
        return self._loaded.get(url)

    def _load(self, url: str, wait: tuple | float = 0.0) -> str:
        """Ein Abruf (zählt auf ``max_requests``) mit Pause, Sperre und Netzfehler-Zähler."""
        self._pause(wait)
        self.requests_made += 1
        try:
            return self.fetcher(url) if self.fetcher else fetch_html(url, self.timeout)
        except GoogleBlocked as e:
            self._down = f"{BLOCKED_MESSAGE} ({e})"
            raise
        except http.HttpError as e:
            if e.network:   # Netz weg/Zeitüberschreitung: nicht alle Abfragen durchprobieren
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    self._down = ("Google Flights nicht erreichbar "
                                  f"({self._consecutive_failures} Abfragen hintereinander) – "
                                  "pausiert bis zum nächsten Lauf")
                    raise GoogleBlocked(self._down) from None
            raise
        finally:
            self._last_request_at = time.monotonic()

    def _empty_fetch(self, reason: str) -> None:
        """Abruf leer: eigener Zähler, kein Netzfehler (die Seite kam ja an); nur wenn es
        sich häuft, ist für diesen Lauf Pause."""
        self.empty_fetches += 1
        self._consecutive_failures = 0
        self._consecutive_empty += 1
        if self._consecutive_empty >= self.MAX_CONSECUTIVE_FAILURES:
            self._down = ("Google Flights liefert nur leere Seiten "
                          f"({self._consecutive_empty} Abfragen hintereinander) – "
                          "pausiert bis zum nächsten Lauf")
            raise GoogleBlocked(self._down)
        raise EmptyPage(reason)

    def insight(self, url: str) -> dict | None:
        """Preisniveau der (in diesem Lauf geladenen) Seite ``url`` - None, wenn keins."""
        return self._insights.get(url)

    # -- Scan (Source-Protokoll) --------------------------------------------
    def plan(self, alert: Alert) -> list[tuple[str, str, date, date | None]]:
        """Abfragen für einen Alarm: Strecken zufällig, Monate nah -> fern, 2 Termine je Monat.
        Mit ``alert.rotation``: das Stück der Tagesrotation für diesen Lauf (One-ways).
        Wochentage des Alarms: Abflug nur an ``depart_weekdays``, Rückflug auf einem Tag aus
        ``return_weekdays`` innerhalb min/max_nights (``rotation.scan_dates_on``/``return_on``)."""
        if alert.rotation:
            return self._rotation_plan(alert)
        today = self.today or date.today()
        pairs = list(alert.pairs)
        self.rng.shuffle(pairs)
        months = _months(today, alert.months_ahead or self.scan_months)
        out = []
        for origin, dest in pairs:
            nights = stay_nights(alert, origin, dest)
            out_days = alert.weekdays_for(origin, dest)
            back_days = alert.weekdays_for(dest, origin)
            for y, m in months:
                days = (rotation.scan_dates_on(y, m, today, out_days) if out_days
                        else scan_dates(y, m, today))
                for dep in days:
                    ret = None
                    if alert.roundtrip:
                        ret = rotation.return_on(dep, nights, back_days, alert.min_nights,
                                                 alert.max_nights)
                        if ret is None:
                            continue
                    out.append((origin, dest, dep, ret))
        return out

    def fetch(self, alert: Alert) -> list[Offer]:
        self.incomplete = None
        if self.verify_only:
            return []
        if self._down:
            raise SourceError(self._down)
        plan = self.plan(alert)
        total = len(plan)
        offers: list[Offer] = []
        errors = 0
        empty = 0
        skipped = 0
        affected: set[tuple[str, str]] = set()
        share = self._shares.get(alert.id)
        used = 0
        roundtrip = alert.roundtrip and not alert.rotation      # Rotation: immer One-ways
        fresh: set[tuple[str, str, date]] = set()
        for origin, dest, dep, ret in plan:
            url = build_url(origin, dest, dep, ret, alert.cabin, roundtrip)
            cached = url in self._cache
            if not cached and (self.scans_made >= self.scan_budget
                               or self.requests_made >= self.max_requests
                               or (share is not None and used >= share)):
                skipped += 1
                affected.add((origin, dest))
                continue
            if not cached:
                used += 1
                self.scans_made += 1
            before = self.requests_made
            # zweiter Abruf nur aus dem eigenen Anteil, nie aus Reserve oder fremdem Anteil
            retry = self.scans_made < self.scan_budget and (share is None or used < share)
            try:
                results = self.query(origin, dest, dep, ret, alert.cabin, roundtrip,
                                     retry=retry, nights=(alert.min_nights, alert.max_nights))
            except GoogleBlocked:
                self._release_share(share)
                if alert.rotation:      # schon abgefragte Tage nicht verlieren
                    self._save_memory(alert, total)
                raise SourceError(self._down or BLOCKED_MESSAGE) from None
            except EmptyPage as e:
                errors += 1
                empty += 1
                affected.add((origin, dest))
                log.warning("Google Flights %s-%s %s: %s", origin, dest, dep, e)
                continue
            except (http.HttpError, ParseError) as e:
                errors += 1
                affected.add((origin, dest))
                log.warning("Google Flights %s-%s %s: %s", origin, dest, dep, e)
                continue
            finally:
                # ein zweiter Abruf (Fehler, tfs-Link, 7 Nächte) geht vom Scan-Anteil dieses Alarms ab
                retried = max(self.requests_made - before - 1, 0)
                used += retried
                self.scans_made += retried
            if alert.rotation:
                self.memory.put(origin, dest, alert.cabin, dep, results, self.insight(url),
                                self._now())
                fresh.add((origin, dest, dep))
            if roundtrip == alert.roundtrip:      # Rotation bei Hin/Rück: nur kombiniert
                # Link und Rückflug der tatsächlich geladenen Seite (tfs-Link, 7 Nächte)
                link, real_ret = self.loaded(url) or (url, ret)
                offers += self.to_offers(results, alert, origin, dest, dep, real_ret, link,
                                         insight=self.insight(url))
        self._release_share(share)
        if alert.rotation:
            offers += self._remembered(alert, fresh)
            self._save_memory(alert, total)
        if alert.roundtrip:
            offers += self.combined(alert)
        if total and errors == total:
            raise SourceError(f"Alle {total} Google-Flights-Abfragen fehlgeschlagen")
        if skipped or errors:
            parts = []
            if skipped:
                parts.append(f"Google Flights: {total - skipped} von {total} Abfragen (Limit)")
            if errors:
                parts.append(f"{errors} von {total} Abfragen fehlgeschlagen"
                             + (f" (davon {empty} leer)" if empty else ""))
            self.incomplete = {"message": "; ".join(parts), "routes": affected}
        return offers

    # -- Datumsrotation (``dealmonitor.rotation``) -----------------------------
    def _now(self) -> datetime:
        return self.now or datetime.now()

    def _rotation_plan(self, alert: Alert) -> list[tuple[str, str, date, None]]:
        """So viele (Strecke, Tag)-Abfragen, wie der Budget-Anteil des Alarms hergibt (ohne
        ``begin_run``: ``rotation.PER_RUN``) - ein fester Anteil hält den Kreis lückenlos."""
        share = self._shares.get(alert.id)
        picked = rotation.slice_for_run(
            self._now(), rotation.routes_for(alert), rotation.horizon_for(alert, self.scan_months),
            rotation.PER_RUN if share is None else share, today=self.today or date.today(),
            period_hours=self.period_hours,
            index=self.memory.run_number(alert.id, self._now(), self.period_hours,
                                         advance=self.advance_rotation),
            weekdays=rotation.route_weekdays(alert))
        return [(o, d, day, None) for o, d, day in picked]

    @property
    def memory(self) -> rotation.Memory:
        """Rotations-Gedächtnis (beim ersten Zugriff aus dem Store geladen)."""
        if self._memory is None:
            data = None
            if self.state_store is not None:
                try:
                    data = self.state_store.source_state(rotation.STATE_NAME)
                except Exception:  # noqa: BLE001 - Zusatzinfo, nie fatal
                    log.exception("Google-Rotation: gespeicherte Preise nicht lesbar")
            self._memory = rotation.Memory(data)
        return self._memory

    def _save_memory(self, alert: Alert, per_run: int) -> None:
        """Veraltetes entfernen und speichern. Ein Preis gilt einen Durchgang plus einen Takt
        (``rotation.report_hours``: mindestens ``MIN_KEEP_HOURS``, höchstens ``MAX_KEEP_HOURS``).
        Durchgangsdauer und Geltung setzen nur volle Läufe (``advance_rotation``) - eine
        Sofort-Prüfung mit 5 Abfragen sagt nichts über den Takt der Rotation."""
        mem = self.memory
        if self.advance_rotation:
            cycle = rotation.cycle_hours(rotation.routes_for(alert),
                                         rotation.horizon_for(alert, self.scan_months), per_run,
                                         self.period_hours, rotation.route_weekdays(alert))
            mem.set_cycle(alert.id, cycle)
            self._keep_hours = max(self._keep_hours,
                                   rotation.report_hours(cycle, self.period_hours))
            if mem.keep_hours != self._keep_hours:
                mem.keep_hours, mem.changed = self._keep_hours, True
        mem.prune(self.today or date.today(), self._now())
        if mem.changed and self.state_store is not None:
            try:
                self.state_store.set_source_state(rotation.STATE_NAME, mem.dump())
                mem.changed = False
            except Exception:  # noqa: BLE001
                log.exception("Google-Rotation: Preise nicht gespeichert")

    def _remembered(self, alert: Alert, fresh: set[tuple[str, str, date]]) -> list[Offer]:
        """One-way-Alarm: die zuletzt gesehenen Preise der Tage, die dieser Lauf nicht abgefragt
        hat - so laufen ihre Deals nicht ab und die Bestpreise decken den ganzen Horizont ab."""
        if alert.roundtrip:
            return []
        today, now = self.today or date.today(), self._now()
        last = today + timedelta(days=rotation.horizon_for(alert, self.scan_months))
        out: list[Offer] = []
        for o, d in alert.pairs:
            allowed = alert.weekdays_for(o, d)
            for day, entry in sorted(self.memory.get(o, d, alert.cabin, today, now).items()):
                if (o, d, day) in fresh or day > last or (allowed and day.weekday() not in allowed):
                    continue
                recalled = self.to_offers(entry["rows"], alert, o, d, day, None,
                                          search_urls(o, d, day, None, alert.cabin, False)[0],
                                          insight=entry["insight"])
                for offer in recalled:      # gesehen, als der Tag abgefragt wurde
                    offer.found_at = entry["at"]
                out += recalled
        return out

    def _legs(self, origin: str, dest: str, alert: Alert, today: date,
              now: datetime) -> dict[date, dict]:
        """Je Tag die günstigste One-way-Zeile im Gedächtnis, die zu den Alarm-Regeln passt."""
        criteria = alert_criteria(alert)
        out = {}
        for day, entry in self.memory.get(origin, dest, alert.cabin, today, now).items():
            row = next((r for r in entry["rows"] if row_problem(r, **criteria) is None), None)
            if row:
                out[day] = {**row, "at": entry["at"],
                            "link": search_urls(origin, dest, day, None, alert.cabin, False)[0]}
        return out

    def combined(self, alert: Alert) -> list[Offer]:
        """Hin/Rück aus zwei One-ways der Rotation: je Hinflug-Tag der günstigste Rückflug
        innerhalb ``min_nights..max_nights``; gekennzeichnet über ``Offer.legs``."""
        if not alert.roundtrip:
            return []
        today, now = self.today or date.today(), self._now()
        out: list[Offer] = []
        for o, d in alert.pairs:
            there = self._legs(o, d, alert, today, now)
            back = self._legs(d, o, alert, today, now) if there else {}
            for dep, ret, a, b in rotation.combine(there, back, alert.min_nights,
                                                   alert.max_nights,
                                                   depart_weekdays=alert.weekdays_for(o, d),
                                                   return_weekdays=alert.weekdays_for(d, o)):
                out.append(self._combo(alert, o, d, dep, ret, a, b))
        return out

    def _combo(self, alert: Alert, origin: str, dest: str, dep: date, ret: date,
               there: dict, back: dict) -> Offer:
        def leg(o: str, d: str, day: date, row: dict) -> dict:
            return {"origin": o, "destination": d, "departure": day.isoformat(),
                    "price": row["price"], "airline": row.get("airline") or row.get("airline_code"),
                    "stops": row.get("stops"), "link": row["link"],
                    "seen": row["at"].isoformat(timespec="minutes")}
        legs = [leg(origin, dest, dep, there), leg(dest, origin, ret, back)]
        airline = " / ".join(dict.fromkeys(x["airline"] for x in legs if x["airline"])) or None
        stops = [x["stops"] for x in legs if x["stops"] is not None]
        # eigene Identität: nie derselbe Deal wie ein echter Hin/Rück-Flug mit gleichen Daten
        ident = hashlib.sha1(f"{self.name}|2xOW|{origin}|{dest}|{dep}|{ret}|{airline}"
                             .encode()).hexdigest()[:16]
        return Offer(source=self.name, origin=origin.upper(), destination=dest.upper(),
                     price=round(there["price"] + back["price"], 2), currency="EUR",
                     roundtrip=True, cabin=alert.cabin, departure=dep, return_date=ret,
                     airline=airline, stops=max(stops) if stops else None,
                     duration_out_min=there.get("duration_min"),
                     duration_back_min=back.get("duration_min"),
                     link=there["link"], ident=ident, legs=legs,
                     found_at=max(there["at"], back["at"]))

    def _release_share(self, share: int | None) -> None:
        """Alarm fertig gescannt: sein (ggf. unverbrauchter) Anteil ist nicht mehr reserviert."""
        if share:
            self._scan_pending = max(self._scan_pending - share, 0)

    def to_offers(self, results: list[dict], alert: Alert, origin: str, dest: str,
                  departure: date, return_date: date | None, link: str,
                  insight: dict | None = None) -> list[Offer]:
        """Ergebniszeilen -> Offers (passend zum Alarm, höchstens ``MAX_RESULTS_PER_QUERY``);
        jede trägt das Preisniveau der Seite (``insight``)."""
        offers: list[Offer] = []
        # erst nach den Alarm-Regeln filtern, dann kürzen - sonst verdrängen 10 billigere
        # Umsteigeflüge einen passenden Nonstop-Flug
        criteria = alert_criteria(alert)
        fitting = [r for r in results if row_problem(r, **criteria) is None]
        for row in fitting[:MAX_RESULTS_PER_QUERY]:
            code = row.get("airline_code")
            offers.append(Offer(
                source=self.name,
                origin=origin.upper(),
                destination=dest.upper(),
                price=float(row["price"]),
                currency="EUR",
                roundtrip=alert.roundtrip,
                cabin=alert.cabin,
                departure=departure,
                return_date=return_date if alert.roundtrip else None,
                airline=row.get("airline") or code,
                stops=row.get("stops"),
                duration_out_min=row.get("duration_min"),
                duration_back_min=None,
                link=link,
                insight=dict(insight) if insight else None,
            ))
        return offers

    # -- Gegenprüfung ---------------------------------------------------------
    def verify(self, origin: str, dest: str, departure: date, return_date: date | None,
               cabin: str = "economy", roundtrip: bool = True, *, max_stops: int | None = None,
               max_minutes: float | None = None, exclude_airlines=()) -> dict | None:
        """Günstigster *passender* Google-Flug für genau diese Daten:
        ``{price, airline, airline_code, stops, duration_min, link}`` - plus ``insight``
        (Preisniveau der Seite), wenn Google eins zeigt.

        Passend heißt: höchstens ``max_stops`` Umstiege, Hinflug höchstens ``max_minutes``,
        keine Airline aus ``exclude_airlines`` (die Regeln des Alarms). Zeigt Google Flüge,
        aber keiner passt, kommt ``{"price": None, "cheapest": …, "reason": …}`` zurück.
        ``None`` heißt "nicht gegenprüfbar" (Sperre, Netzwerk, Budget erschöpft, keine
        Ergebnisse) - der Grund steht danach in ``last_error``. Zählt auf ``max_requests``;
        pro Lauf höchstens ``verify_limit`` echte Gegenprüfungen.
        """
        self.last_error = None
        url = build_url(origin, dest, departure, return_date, cabin, roundtrip)
        cached = url in self._cache
        if not cached:
            if self._down:
                self.last_error = self._down
                return None
            if self.verifications >= self.verify_limit:
                self.last_error = (f"Google Flights: Gegenprüf-Limit ({self.verify_limit}) "
                                   "für diesen Lauf erreicht")
                return None
            if self.requests_made >= self.max_requests:
                self.last_error = f"Google Flights: Limit von {self.max_requests} Abfragen erreicht"
                return None
            if self.requests_made >= self.max_requests - self._scan_pending:
                self.last_error = ("Google Flights: Rest-Budget für die Preis-Suche der übrigen "
                                   "Alarme reserviert")
                return None
        try:
            results = self.query(origin, dest, departure, return_date, cabin, roundtrip)
        except EmptyPage as e:
            self.last_error = self._down or f"Google Flights: {e}"
            log.warning("Google Flights Gegenprüfung %s-%s %s: %s", origin, dest, departure, e)
            return None
        except (SourceError, http.HttpError, ParseError) as e:
            self.last_error = self._down or f"Google Flights nicht erreichbar: {e}"
            log.warning("Google Flights Gegenprüfung %s-%s %s: %s", origin, dest, departure, e)
            return None
        finally:
            if not cached:
                self.verifications += 1
        if not results:
            self.last_error = "Google Flights: keine Flüge für diese Daten gefunden"
            return None
        criteria = {"max_stops": max_stops, "max_minutes": max_minutes,
                    "exclude_airlines": exclude_airlines}
        best = next((r for r in results if row_problem(r, **criteria) is None), None)
        insight = self.insight(url)
        extra = {"insight": dict(insight)} if insight else {}
        url = (self.loaded(url) or (url,))[0]     # Link der tatsächlich geladenen Seite
        if best is None:
            first = results[0]
            return {"price": None, "cheapest": float(first["price"]), "link": url,
                    "reason": f"kein passender Flug (günstigster: {row_problem(first, **criteria)})",
                    **extra}
        return {"price": float(best["price"]),
                "airline": best.get("airline") or best.get("airline_code"),
                "airline_code": best.get("airline_code"), "stops": best.get("stops"),
                "duration_min": best.get("duration_min"), "link": url, **extra}
