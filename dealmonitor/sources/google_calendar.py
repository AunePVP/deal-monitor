"""Google Flights: Preiskalender (günstigster Preis je Abflugtag) als sparsame Schnellsuche.

Das Datumsfeld von Google Flights zeigt pro Tag den günstigsten Preis. Die Seite lädt ihn per
RPC ``FlightsFrontendService/GetCalendarGraph`` - ein schlichter POST ohne Cookies/Token
genügt, Aufbau wie ``GetBookingResults`` (``f.req`` = ``[null, "<JSON>"]``, gemessen 08.11.2026)::

    [null, <Suchfilter wie GetShoppingResults>, ["JJJJ-MM-TT", "JJJJ-MM-TT"]]          One-way
    [null, <Suchfilter>, ["von", "bis"], null, [Nächte, Nächte]]                       Hin/Rück

Antwort (``wrb.fr``): ``[[Kopf], [[Abflug, Rückflug|null, [[null, Preis], token], 1], …]]``.
Ein Abruf deckt bis zu ``MAX_DAYS`` Abflugtage ab (180 gemessen ok, 362 -> ErrorResponse 3).
Der Zeitraum gilt für den Abflug; bei Hin/Rück ist der Rückflug Abflug + Nächte (fest).
Kabine (Feld 6: Business FRA-PEK ~2.800-3.300 € statt ~700 €) und Umstiege (Feld 4 je
Strecke: 0 = egal, 1 = nonstop, 2 = höchstens 1, 3 = höchstens 2) wirken. Stadtcodes nicht:
``["BJS", 0]`` liefert eine leere Antwort - deshalb alle Flughäfen der Stadt (``CITY_AIRPORTS``).

Abgleich mit der normalen Suche (08.11.2026, Kalender 11:03, Suche 11:10-11:12): HAM-ZRH
one-way 12./15./28.01.2027 Kalender 60/70/90 €, Suche 60/70/90 € (Buchungsoptionen nicht
günstiger); FRA-PEK Hin/Rück 12 Nächte ab 10./15./22.01. Kalender 710/735/802 €, Suche
710/735/802 €. Portal-Preise (OTA) kennt der Kalender nicht - die holt erst die Bestätigung.

Kalenderpreise sind ein Vorfilter: Sie haben keine Airline, Zeiten oder Buchungsoptionen und
können veraltet sein. ``GoogleFlightsSource`` bestätigt deshalb nur die günstigsten Tage per
normaler Abfrage - gemeldet werden ausschließlich bestätigte Angebote.
"""

from __future__ import annotations

import gzip
import json
import math
from datetime import date, timedelta
from urllib.parse import quote

from .. import http

RPC = ("https://www.google.com/_/FlightsFrontendUi/data/travel.frontend.flights."
       "FlightsFrontendService/GetCalendarGraph?hl=de&gl=DE&curr=EUR")
#: Abflugtage je Abruf (gemessen: 180 ok, 362 -> ErrorResponse)
MAX_DAYS = 180
MAX_BYTES = 2_000_000
#: so viele Tage je Strecke werden höchstens per normaler Abfrage bestätigt (das Budget des
#: Alarms kürzt weiter; die Planung verteilt Runde für Runde über alle Strecken)
CONFIRM_PER_ROUTE = 8
#: je Monat werden die günstigsten 20 % der Kalendertage Kandidaten (mindestens einer)
MONTH_SHARE = 0.2
#: Tage bis Grenze × Faktor sind Kandidaten (sonst: günstigster Tag je Monat)
LIMIT_FACTOR = 1.3
#: Kennzeichnung unbestätigter Kalenderpreise (Hinweise, nie Deals)
LABEL = "Kalenderpreis, unbestätigt"
CABIN = {"economy": 1, "premium": 2, "business": 3, "first": 4}
#: Stadtcodes -> Flughäfen: der Kalender versteht nur Flughafen-Codes (``["BJS", 0]`` ist ein
#: unbekannter Flughafen -> leere Antwort), aber mehrere je Ort (gemessen: PEK+PKX ok)
CITY_AIRPORTS = {
    "BJS": ("PEK", "PKX"), "SHA": ("PVG", "SHA"), "TYO": ("HND", "NRT"), "OSA": ("KIX", "ITM"),
    "SEL": ("ICN", "GMP"), "TPE": ("TPE", "TSA"), "BKK": ("BKK", "DMK"), "KUL": ("KUL", "SZB"),
    "JKT": ("CGK", "HLP"), "NYC": ("JFK", "EWR", "LGA"), "WAS": ("IAD", "DCA", "BWI"),
    "CHI": ("ORD", "MDW"), "YTO": ("YYZ", "YTZ"), "SAO": ("GRU", "CGH", "VCP"),
    "RIO": ("GIG", "SDU"), "BUE": ("EZE", "AEP"), "LON": ("LHR", "LGW", "STN", "LTN", "LCY", "SEN"),
    "PAR": ("CDG", "ORY", "BVA"), "MIL": ("MXP", "LIN", "BGY"), "ROM": ("FCO", "CIA"),
    "STO": ("ARN", "BMA", "NYO"), "OSL": ("OSL", "TRF"), "MOW": ("SVO", "DME", "VKO"),
    "IST": ("IST", "SAW"), "BUH": ("OTP",), "REK": ("KEF",), "DXB": ("DXB", "DWC"),
    "EAP": ("BSL",), "BER": ("BER",),
}
#: ``max_stops`` des Alarms -> Umstiegsfilter je Strecke (wie in der normalen Suche)
STOPS = {0: 1, 1: 2, 2: 3}


class CalendarError(RuntimeError):
    """Kalender nicht lesbar (ErrorResponse, anderer Aufbau) - Rückfall auf die alte Planung."""


def place(code: str) -> list:
    """Ort im Suchfilter: alle Flughäfen eines Stadtcodes (``CITY_AIRPORTS``)."""
    code = code.upper()
    return [[c, 0] for c in CITY_AIRPORTS.get(code, (code,))]


def request_body(origin: str, dest: str, start: date, end: date, cabin: str = "economy",
                 nights: int | None = None, max_stops: int | None = None) -> bytes:
    """POST-Body für ``GetCalendarGraph``: Abflug ``start..end``; ``nights`` = Hin/Rück mit
    fester Aufenthaltsdauer (None = One-way)."""
    stops = STOPS.get(max_stops, 0) if max_stops is not None else 0
    legs = [(origin.upper(), dest.upper(), start)]
    if nights is not None:
        legs.append((dest.upper(), origin.upper(), start + timedelta(days=nights)))
    segs = [[[place(o)], [place(d)], None, stops, None, None, day.isoformat(), None, None, None,
             None, None, None, None, 3] for o, d, day in legs]
    filt = [None, None, 1 if nights is not None else 2, None, [], CABIN.get(cabin, 1),
            [1, 0, 0, 0], None, None, None, None, None, None, segs, None, None, None, 1]
    inner = [None, filt, [start.isoformat(), end.isoformat()]]
    if nights is not None:
        inner += [None, [nights, nights]]
    payload = json.dumps([None, json.dumps(inner, separators=(",", ":"))], separators=(",", ":"))
    return ("f.req=" + quote(payload, safe="")).encode()


def fetch(body: bytes, timeout: float, headers: dict, blocked) -> str:
    """Ein Kalender-Abruf. ``blocked`` (Ausnahme-Klasse) bei 403/429, ``HttpError`` sonst."""
    try:
        resp = http.request(RPC, data=body, headers=headers, method="POST", timeout=timeout,
                            retries=0, full=True, max_bytes=MAX_BYTES)
    except http.HttpError as e:
        if e.status in (403, 429):
            raise blocked(f"HTTP {e.status}") from e
        raise
    raw = resp.body
    if "gzip" in resp.header("Content-Encoding").lower():
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def parse(text: str, nights: int | None = None) -> dict[date, float]:
    """``{Abflugtag: Preis}`` aus einer ``GetCalendarGraph``-Antwort. Tage ohne Preis fehlen;
    bei Hin/Rück nur Tage mit Rückflug = Abflug + ``nights``. ``CalendarError``, wenn Google
    einen Fehler meldet oder die Antwort nicht lesbar ist."""
    found = False
    out: dict[date, float] = {}
    for line in (text or "").splitlines():
        if not line.startswith('[["wrb.fr"'):
            continue
        try:
            frames = json.loads(line)
        except ValueError:
            continue
        for fr in frames:
            if not (isinstance(fr, list) and len(fr) > 2 and fr[0] == "wrb.fr"):
                continue
            if not isinstance(fr[2], str):
                code = fr[5][0] if len(fr) > 5 and isinstance(fr[5], list) and fr[5] else None
                raise CalendarError(f"Google-Kalender meldet Fehler {code}")
            try:
                data = json.loads(fr[2])
            except ValueError:
                raise CalendarError("Google-Kalender: Antwort nicht lesbar") from None
            if not isinstance(data, list):
                raise CalendarError("Google-Kalender: Antwort nicht lesbar")
            # nur Kopf ohne Tage: gültige Antwort ohne Flüge (z. B. unbekannter Ort)
            rows = data[1] if len(data) > 1 else []
            if not isinstance(rows, list):
                raise CalendarError("Google-Kalender: Antwort nicht lesbar")
            found = True
            for row in rows or []:
                try:
                    dep = date.fromisoformat(row[0])
                    price = row[2][0][1]
                    ret = date.fromisoformat(row[1]) if row[1] else None
                except (TypeError, ValueError, IndexError, KeyError):
                    continue
                if not (isinstance(price, (int, float)) and not isinstance(price, bool)
                        and price > 0):
                    continue
                if nights is not None and ret != dep + timedelta(days=nights):
                    continue
                if nights is None and ret is not None:
                    continue
                out[dep] = float(price)
    if not found:
        raise CalendarError("Google-Kalender: keine Daten in der Antwort")
    return out


def windows(start: date, end: date, max_days: int = MAX_DAYS) -> list[tuple[date, date]]:
    """``start..end`` (einschließlich) in Stücke zu höchstens ``max_days`` Tagen."""
    out = []
    while start <= end:
        stop = min(start + timedelta(days=max_days - 1), end)
        out.append((start, stop))
        start = stop + timedelta(days=1)
    return out


def candidates(prices: dict[date, float], limit: float | None, k: int = CONFIRM_PER_ROUTE
               ) -> list[date]:
    """Tage, die eine Bestätigung lohnen, nach Vorrang (höchstens ``k``; das Budget kürzt
    weiter): erst alle bis Grenze × ``LIMIT_FACTOR`` (günstigste zuerst), dann je Monat die
    günstigsten ``MONTH_SHARE`` der Tage (mindestens einer) - Runde für Runde, also erst der
    günstigste jedes Monats, dann der zweitgünstigste … Großzügig, weil der Kalender einen
    Tag zu teuer zeigen kann: ein knapp teurerer Nachbartag wird trotzdem bestätigt."""
    order = sorted(prices, key=lambda d: (prices[d], d))
    hot = [d for d in order if limit and prices[d] <= limit * LIMIT_FACTOR]
    months: dict[tuple[int, int], list[date]] = {}
    for d in order:
        months.setdefault((d.year, d.month), []).append(d)
    per_month = {m: days[:max(1, math.ceil(len(days) * MONTH_SHARE))]
                 for m, days in months.items()}
    rounds: list[date] = []
    for rank in range(max((len(v) for v in per_month.values()), default=0)):
        rounds += sorted((v[rank] for v in per_month.values() if len(v) > rank),
                         key=lambda d: (prices[d], d))
    out: list[date] = []
    for d in hot + rounds:
        if len(out) >= k:
            break
        if d not in out:
            out.append(d)
    return out
