"""Preisquelle: Amadeus Self-Service APIs (Flight Cheapest Date Search, Flight Offers Search).

**Status: abgeschaltet seit 17.07.2026** (``STATUS``). Der Amadeus Self-Service
(developers.amadeus.com, kostenlose Keys) ist eingestellt, ``test.api.amadeus.com`` antwortet
nicht mehr (CONNECT 502, geprüft 05.11.2026), ``api.amadeus.com`` nur noch für Enterprise-Kunden
(HTTP 401). Recherche: docs/handoff/research/preis-apis/NOTES.md. Die Quelle ist standardmäßig
aus und in der Oberfläche als abgeschaltet markiert; der Code bleibt für einen evtl. noch
gültigen Enterprise-Key (und weil Tests ihn nutzen). Test-Umgebung ``test.api.amadeus.com``, Produktion ``api.amadeus.com``; die Umgebung
wählt die Einstellung ``amadeus_env`` ("test"|"production").

**Stand: strikt nach Doku umgesetzt (OpenAPI-Spezifikation und Developer-Guides von
amadeus4dev auf GitHub), mit Fixture-Tests - aber ohne Key noch nicht live geprüft.**

Ablauf pro Anfrage:

* OAuth2 Client-Credentials: ``POST /v1/security/oauth2/token`` (x-www-form-urlencoded:
  ``grant_type=client_credentials&client_id=…&client_secret=…``) -> ``access_token`` +
  ``expires_in`` (Sekunden, i. d. R. 1799). Der Token wird im Client gecacht und kurz vor
  Ablauf erneuert; jede Daten-Anfrage trägt ``Authorization: Bearer <token>``.
* Economy, Monats-Scan (Quelle ``amadeus-dates``): ``GET /v1/shopping/flight-dates`` mit
  ``departureDate=<erster>,<letzter Tag>``, ``oneWay``, ``duration=<min>,<max Nächte>``
  (nur Hin/Rück), ``nonStop``, ``viewBy=DATE`` -> je Abflugtag der günstigste Preis
  (``data[].departureDate/returnDate/price.total``). Der Endpunkt kennt keine Kabine und
  keine Währungswahl: ``meta.currency`` muss ``EUR`` sein, sonst wird die Antwort verworfen.
* Premium/Business/First (Quelle ``amadeus-offers``): ``GET /v2/shopping/flight-offers``
  mit ``travelClass``, ``currencyCode=EUR``, ``max=10`` für zwei Datumspaare je Monat
  (Freitag und Dienstag um die Monatsmitte, Aufenthalt wie bei Google Flights) ->
  ``data[].price.grandTotal``, ``itineraries[].duration`` ("PT12H30M"), ``segments[]``
  (``carrierCode``, ``numberOfStops``, ``departure.at``).

Fehlerbehandlung: HTTP 401 -> ``SourceError("Amadeus lehnt den Key ab …")`` sofort (bei
einem abgelaufenen Token wird genau einmal ein neuer geholt); 429 -> Schutzschalter für den
Lauf; 400/5xx/Netz -> nur diese Anfrage scheitert (``errors[].detail`` in der Meldung),
404 -> keine Daten für diese Strecke. Pro Lauf höchstens ``max_requests`` Daten-Anfragen
(Setting ``amadeus_max_requests``); nicht abgefragte Strecken stehen in ``incomplete``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from .. import http
from ..models import Alert, Offer
from . import SourceError
from .googleflights import scan_dates, stay_nights

log = logging.getLogger(__name__)

BASE_URLS = {"test": "https://test.api.amadeus.com", "production": "https://api.amadeus.com"}
TOKEN_PATH = "/v1/security/oauth2/token"
DATES_PATH = "/v1/shopping/flight-dates"
OFFERS_PATH = "/v2/shopping/flight-offers"
ACCEPT = "application/json, application/vnd.amadeus+json"
TRAVEL_CLASS = {"economy": "ECONOMY", "premium": "PREMIUM_ECONOMY",
                "business": "BUSINESS", "first": "FIRST"}
CABIN_RANK = {"ECONOMY": 0, "PREMIUM_ECONOMY": 1, "BUSINESS": 2, "FIRST": 3}
#: Angebote je Abfrage (Parameter ``max`` der Flight Offers Search)
MAX_OFFERS_PER_QUERY = 10
#: Sekunden vor Ablauf, ab denen der Token vorsorglich erneuert wird (wie im Amadeus-SDK)
TOKEN_BUFFER = 10
#: Aufenthalt für den Monats-Scan, wenn der Alarm keine Nächte vorgibt
DEFAULT_DURATION = (1, 30)
#: frühester Abflugtag = heute + so viele Tage (heute selbst wird nicht mehr abgefragt)
MIN_LEAD_DAYS = 1

#: Self-Service eingestellt - für Meldungen, Einstellungen und Oberfläche
SHUTDOWN_DATE = "17.07.2026"
STATUS = f"abgeschaltet seit {SHUTDOWN_DATE}"
SHUTDOWN_HINT = f"Amadeus Self-Service ist {STATUS}"

KEY_REJECTED = "Amadeus lehnt den Key ab"
KEY_HINT = "bitte API Key und Secret in den Einstellungen prüfen"
MISSING_KEY = (f"Amadeus-Key/Secret fehlen - {SHUTDOWN_HINT} (nur noch Enterprise). Mit einem "
               "gültigen Key in den Einstellungen eintragen (oder AMADEUS_API_KEY/"
               "AMADEUS_API_SECRET setzen), sonst Amadeus ausschalten.")
RATE_LIMITED = "Amadeus-Limit erreicht (HTTP 429{detail}) - später erneut"

#: JSON-Felder aus Fehlerantworten, die in der Meldung landen (Reihenfolge = Vorrang)
_DETAIL_RE = re.compile(r'"(?:detail|error_description)"\s*:\s*"((?:[^"\\]|\\.)*)')
_TITLE_RE = re.compile(r'"(?:title|error)"\s*:\s*"((?:[^"\\]|\\.)*)')
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


class AmadeusApiError(RuntimeError):
    """Eine einzelne Anfrage ist gescheitert (400, 5xx, Netzwerk, kaputte Antwort).

    Andere Anfragen desselben Laufs können trotzdem funktionieren - anders als bei
    ``SourceError`` (Key abgelehnt, Limit, Schutzschalter), die die Quelle für den Lauf stoppen.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class BudgetExhausted(SourceError):
    """``max_requests`` für diesen Lauf aufgebraucht."""


# -- Helfer ---------------------------------------------------------------------------------
def parse_duration(text: str | None) -> int | None:
    """ISO-8601-Dauer der Amadeus-Antworten ("PT12H30M", "P1DT2H") -> Minuten."""
    if not text or not (m := _DURATION_RE.match(text.strip().upper())):
        return None
    days, hours, minutes, seconds = m.groups()
    if not any((days, hours, minutes, seconds)):
        return None
    total = int(days or 0) * 1440 + int(hours or 0) * 60 + int(minutes or 0)
    if seconds and float(seconds) >= 30:
        total += 1
    return total


def error_detail(e: Exception) -> str:
    """``errors[].detail`` bzw. ``error_description`` aus einer Fehlerantwort.

    ``http.HttpError`` trägt bei 4xx die ersten 300 Zeichen des Bodys in der Meldung - also
    das JSON der Amadeus-Fehlerantwort, ggf. abgeschnitten. Darum per Regex statt json.loads.
    """
    text = str(e)
    m = _DETAIL_RE.search(text) or _TITLE_RE.search(text)
    if not m:
        return ""
    try:
        return json.loads(f'"{m.group(1)}"')
    except ValueError:
        return m.group(1)


def api_message(e: http.HttpError) -> str:
    detail = error_detail(e)
    if e.status is None:
        return f"nicht erreichbar ({e})"
    return f"HTTP {e.status}" + (f": {detail}" if detail else "")


def _months(start: date, count: int) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    for _ in range(max(count, 0)):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def month_range(year: int, month: int, today: date) -> tuple[date, date] | None:
    """Abfragezeitraum eines Monats (erster abfragbarer Tag, letzter Tag) - None, wenn vorbei."""
    first = date(year, month, 1)
    last = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
    start = max(first, today + timedelta(days=MIN_LEAD_DAYS))
    if start > last:
        return None
    return start, last


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# -- Client: Token, Zähler, Schutzschalter --------------------------------------------------
class AmadeusClient:
    """Gemeinsamer Unterbau beider Amadeus-Quellen: Token-Cache, Request-Zähler, Schutzschalter.

    Ein Client pro Lauf; ``AmadeusDatesSource`` und ``AmadeusOffersSource`` teilen sich damit
    Token und Budget (``max_requests`` zählt nur Daten-Anfragen, nicht den Token-Abruf).
    """

    #: so viele Netzwerk-/Serverfehler hintereinander -> API gilt für diesen Lauf als nicht erreichbar
    MAX_CONSECUTIVE_FAILURES = 5

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        env: str = "test",
        max_requests: int = 100,
        request_delay: float = 0.35,
        timeout: float = 20,
    ) -> None:
        env = (env or "test").strip().lower()
        if env not in BASE_URLS:
            raise ValueError(f"amadeus_env muss 'test' oder 'production' sein, nicht '{env}'")
        self.env = env
        self.base = BASE_URLS[env]
        self.api_key = (api_key or os.environ.get("AMADEUS_API_KEY") or "").strip()
        self.api_secret = (api_secret or os.environ.get("AMADEUS_API_SECRET") or "").strip()
        self.max_requests = max(int(max_requests), 0)
        self.request_delay = max(float(request_delay), 0.0)
        self.timeout = timeout
        self._token: str | None = None
        self._token_expires_at = 0.0
        self.reset()

    # -- Zustand pro Lauf ---------------------------------------------------
    def reset(self) -> None:
        """Zähler und Schutzschalter für einen neuen Lauf zurücksetzen (Token bleibt gültig)."""
        self.requests_made = 0
        self.token_requests = 0
        self._down: str | None = None
        self._consecutive_failures = 0
        self._last_request_at: float | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def remaining(self) -> int:
        return max(self.max_requests - self.requests_made, 0)

    @property
    def down(self) -> str | None:
        return self._down

    def _pause(self) -> None:
        if self._last_request_at is None or self.request_delay <= 0:
            return
        wait = self.request_delay - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)

    # -- OAuth2 ----------------------------------------------------------------
    def token(self) -> str:
        """Gültiger Access-Token (gecacht, kurz vor Ablauf erneuert)."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        if not self.configured:
            raise SourceError(MISSING_KEY)
        if self._down:
            raise SourceError(self._down)
        body = urlencode({"grant_type": "client_credentials", "client_id": self.api_key,
                          "client_secret": self.api_secret}).encode()
        self.token_requests += 1
        try:
            raw = http.request(
                self.base + TOKEN_PATH, data=body, method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "Accept": "application/json"},
                timeout=self.timeout, retries=1,
            )
        except http.HttpError as e:
            if e.status in (400, 401, 403):
                # invalid_client / unsupported_grant_type: jede weitere Anfrage scheitert genauso
                self._down = f"{KEY_REJECTED} ({api_message(e)}) - {KEY_HINT}."
            elif e.status == 429:
                self._down = RATE_LIMITED.format(detail="")
            else:
                self._down = f"Amadeus nicht erreichbar (Token-Anfrage): {e} - {SHUTDOWN_HINT}."
            raise SourceError(self._down) from None
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        token = data.get("access_token") if isinstance(data, dict) else None
        if not token:
            self._down = "Amadeus: Token-Antwort ohne access_token - API geändert?"
            raise SourceError(self._down)
        expires_in = _float(data.get("expires_in")) or 1799.0
        self._token = str(token)
        self._token_expires_at = time.monotonic() + max(expires_in - TOKEN_BUFFER, 1.0)
        log.debug("Amadeus: neuer Token (%s s gültig)", int(expires_in))
        return self._token

    # -- Daten-Anfragen --------------------------------------------------------
    def get(self, path: str, params: dict, _retry: bool = True) -> dict:
        """GET ``path`` mit Bearer-Token. Zählt auf ``max_requests``.

        Wirft ``SourceError`` (Key fehlt/abgelehnt, 429, Schutzschalter, Budget) bzw.
        ``AmadeusApiError`` (nur diese Anfrage gescheitert). 404 = keine Daten -> ``{"data": []}``.
        """
        if self._down:
            raise SourceError(self._down)
        if self.requests_made >= self.max_requests:
            raise BudgetExhausted(
                f"Amadeus: Limit von {self.max_requests} Abfragen pro Lauf erreicht")
        token = self.token()
        self._pause()
        self.requests_made += 1
        try:
            raw = http.request(
                self.base + path, params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": ACCEPT},
                timeout=self.timeout, retries=1,
            )
        except http.HttpError as e:
            self._last_request_at = time.monotonic()
            self._handle_error(e)
            if e.status == 401 and _retry:
                # Token abgelaufen/widerrufen (38190/38192/38193)? Genau einmal neu holen.
                log.info("Amadeus: HTTP 401 (%s) - hole neuen Token", error_detail(e) or "?")
                self._token = None
                return self.get(path, params, _retry=False)
            if e.status == 401:
                self._down = f"{KEY_REJECTED} ({api_message(e)}) - {KEY_HINT}."
                raise SourceError(self._down) from None
            if e.status == 404:
                log.info("Amadeus %s: keine Daten (%s)", path, error_detail(e) or "HTTP 404")
                return {"data": [], "meta": {}}
            raise AmadeusApiError(api_message(e), e.status) from e
        self._last_request_at = time.monotonic()
        self._consecutive_failures = 0
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise AmadeusApiError(f"Ungültige JSON-Antwort: {e}") from e
        if not isinstance(data, dict):
            raise AmadeusApiError("Unerwartete Antwort der Amadeus-API")
        for w in data.get("warnings") or []:
            if isinstance(w, dict):
                log.info("Amadeus-Hinweis: %s %s", w.get("title") or "", w.get("detail") or "")
        return data

    def _handle_error(self, e: http.HttpError) -> None:
        """Schutzschalter für 429/403 und für Serien von Netzwerk-/Serverfehlern."""
        if e.status == 429:
            detail = error_detail(e)
            self._down = RATE_LIMITED.format(detail=f": {detail}" if detail else "")
            raise SourceError(self._down) from None
        if e.status == 403:
            self._down = f"Amadeus verweigert den Zugriff ({api_message(e)}) - {KEY_HINT}."
            raise SourceError(self._down) from None
        if e.network or (e.status is not None and e.status >= 500):
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                self._down = (f"Amadeus nicht erreichbar ({self._consecutive_failures} Fehler "
                              f"hintereinander, Prüfung abgebrochen): {e} - {SHUTDOWN_HINT}.")
                raise SourceError(self._down) from None
        else:
            self._consecutive_failures = 0


# -- Quellen ---------------------------------------------------------------------------------
class _AmadeusSource:
    """Gemeinsamer Ablauf: Plan abarbeiten, Budget/Fehler zählen, ``incomplete`` setzen."""

    name = "amadeus"
    path = ""
    records_history = True

    def __init__(self, client: AmadeusClient | None = None, *, scan_months: int = 6,
                 today: date | None = None, **client_kw) -> None:
        self.client = client or AmadeusClient(**client_kw)
        self.scan_months = scan_months
        self.today = today
        #: nach ``fetch``: None = alles abgefragt, sonst {"message": str, "routes": {(o, d), ...}}
        self.incomplete: dict | None = None

    def handles(self, alert: Alert) -> bool:   # pragma: no cover - überschrieben
        return True

    def plan(self, alert: Alert) -> list[tuple[str, str, dict]]:   # pragma: no cover
        return []

    def parse(self, data: dict, alert: Alert, origin: str, dest: str,
              params: dict) -> list[Offer]:   # pragma: no cover
        return []

    def fetch(self, alert: Alert) -> list[Offer]:
        self.incomplete = None
        if not self.handles(alert):
            return []
        if not self.client.configured:
            raise SourceError(MISSING_KEY)
        if self.client.down:
            raise SourceError(self.client.down)
        plan = self.plan(alert)
        total = len(plan)
        offers: list[Offer] = []
        errors = skipped = 0
        affected: set[tuple[str, str]] = set()
        for origin, dest, params in plan:
            if self.client.remaining <= 0:
                skipped += 1
                affected.add((origin, dest))
                continue
            try:
                data = self.client.get(self.path, params)
            except BudgetExhausted:
                skipped += 1
                affected.add((origin, dest))
                continue
            except AmadeusApiError as e:
                errors += 1
                affected.add((origin, dest))
                log.warning("Amadeus %s-%s %s: %s", origin, dest,
                            params.get("departureDate"), e)
                continue
            offers += self.parse(data, alert, origin, dest, params)
        if total and errors == total:
            raise SourceError(f"Alle {total} Amadeus-Anfragen fehlgeschlagen")
        parts = []
        if skipped:
            parts.append(f"Amadeus: {total - skipped} von {total} Abfragen (Limit)")
        if errors:
            parts.append(f"{errors} von {total} Anfragen fehlgeschlagen")
        if parts:
            self.incomplete = {"message": "; ".join(parts), "routes": affected}
        return offers


class AmadeusDatesSource(_AmadeusSource):
    """Economy-Monatsscan über Flight Cheapest Date Search (``/v1/shopping/flight-dates``)."""

    name = "amadeus-dates"
    path = DATES_PATH

    def handles(self, alert: Alert) -> bool:
        return alert.cabin == "economy"

    def plan(self, alert: Alert) -> list[tuple[str, str, dict]]:
        today = self.today or date.today()
        lo, hi = DEFAULT_DURATION
        lo = alert.min_nights if alert.min_nights is not None else lo
        hi = alert.max_nights if alert.max_nights is not None else max(hi, lo)
        lo, hi = max(lo, 1), max(hi, max(lo, 1))
        out = []
        for origin, dest in alert.pairs:
            for y, m in _months(today, alert.months_ahead or self.scan_months):
                rng = month_range(y, m, today)
                if rng is None:
                    continue
                start, end = rng
                out.append((origin, dest, {
                    "origin": origin,
                    "destination": dest,
                    "departureDate": f"{start.isoformat()},{end.isoformat()}",
                    "oneWay": "false" if alert.roundtrip else "true",
                    # duration darf bei oneWay nicht gesetzt sein (http.request lässt None weg)
                    "duration": f"{lo},{hi}" if alert.roundtrip else None,
                    "nonStop": "true" if alert.max_stops == 0 else "false",
                    "viewBy": "DATE",
                }))
        return out

    def parse(self, data: dict, alert: Alert, origin: str, dest: str,
              params: dict) -> list[Offer]:
        rows = data.get("data") or []
        meta = data.get("meta") or {}
        currency = str(meta.get("currency") or "").upper()
        if rows and currency != "EUR":
            log.warning("Amadeus %s-%s: Preise in %s statt EUR - verworfen", origin, dest,
                        currency or "unbekannter Währung")
            return []
        nonstop = params.get("nonStop") == "true"
        offers: list[Offer] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = _float((row.get("price") or {}).get("total"))
            departure = _date(row.get("departureDate"))
            if price is None or price <= 0 or departure is None:
                continue
            return_date = _date(row.get("returnDate")) if alert.roundtrip else None
            if alert.roundtrip and return_date is None:
                continue
            offers.append(Offer(
                source=self.name,
                origin=(row.get("origin") or origin).upper(),
                destination=(row.get("destination") or dest).upper(),
                price=price,
                currency="EUR",
                roundtrip=alert.roundtrip,
                cabin="economy",
                departure=departure,
                return_date=return_date,
                airline=None,                  # der Endpunkt nennt keine Airline
                stops=0 if nonstop else None,  # ... und keine Umstiege (nur bei nonStop sicher)
                link=None,                     # Gegenprüfen-Link (Google) ergänzt der Monitor
            ))
        return offers


class AmadeusOffersSource(_AmadeusSource):
    """Premium/Business/First über Flight Offers Search (``/v2/shopping/flight-offers``)."""

    name = "amadeus-offers"
    path = OFFERS_PATH

    def handles(self, alert: Alert) -> bool:
        return alert.cabin in TRAVEL_CLASS and alert.cabin != "economy"

    def plan(self, alert: Alert) -> list[tuple[str, str, dict]]:
        today = self.today or date.today()
        months = _months(today, alert.months_ahead or self.scan_months)
        # Nur IATA-Codes an die API geben; alles andere filtert principles.quality_problems
        excluded = ",".join(a for a in alert.exclude_airlines if re.fullmatch(r"[A-Z0-9]{2}", a))
        out = []
        for origin, dest in alert.pairs:
            nights = stay_nights(alert, origin, dest)
            for y, m in months:
                for dep in scan_dates(y, m, today):
                    ret = dep + timedelta(days=nights) if alert.roundtrip else None
                    out.append((origin, dest, {
                        "originLocationCode": origin,
                        "destinationLocationCode": dest,
                        "departureDate": dep.isoformat(),
                        "returnDate": ret.isoformat() if ret else None,
                        "adults": 1,
                        "travelClass": TRAVEL_CLASS[alert.cabin],
                        "nonStop": "true" if alert.max_stops == 0 else "false",
                        "currencyCode": "EUR",
                        "max": MAX_OFFERS_PER_QUERY,
                        "excludedAirlineCodes": excluded or None,
                    }))
        return out

    def parse(self, data: dict, alert: Alert, origin: str, dest: str,
              params: dict) -> list[Offer]:
        offers = []
        for row in data.get("data") or []:
            if isinstance(row, dict) and (o := self.parse_offer(row, alert, origin, dest)):
                offers.append(o)
        return offers

    def parse_offer(self, row: dict, alert: Alert, origin: str, dest: str) -> Offer | None:
        price = row.get("price") or {}
        currency = str(price.get("currency") or "").upper()
        if currency != "EUR":
            log.warning("Amadeus-Angebot %s-%s in %s statt EUR - verworfen", origin, dest,
                        currency or "unbekannter Währung")
            return None
        total = _float(price.get("grandTotal") or price.get("total"))
        itineraries = [i for i in row.get("itineraries") or [] if isinstance(i, dict)]
        if total is None or total <= 0 or not itineraries:
            return None
        if alert.roundtrip and len(itineraries) < 2:
            return None
        if not alert.roundtrip and len(itineraries) != 1:
            return None
        out = itineraries[0]
        back = itineraries[1] if alert.roundtrip else None
        out_segments = [s for s in out.get("segments") or [] if isinstance(s, dict)]
        if not out_segments:
            return None
        departure = _date((out_segments[0].get("departure") or {}).get("at"))
        if departure is None:
            return None
        return_date = None
        if back is not None:
            back_segments = [s for s in back.get("segments") or [] if isinstance(s, dict)]
            if not back_segments:
                return None
            return_date = _date((back_segments[0].get("departure") or {}).get("at"))
            if return_date is None:
                return None
        # Airlines aller Segmente (Marketing-Carrier) gegen die Ausschlussliste prüfen
        carriers = [str(s.get("carrierCode") or "").upper()
                    for itin in itineraries for s in itin.get("segments") or []]
        if any(c and c in alert.exclude_airlines for c in carriers):
            return None
        airline = (out_segments[0].get("carrierCode")
                   or (row.get("validatingAirlineCodes") or [None])[0])
        # Sicherheitsnetz: nie einen Economy-Preis als Business melden. travelClass heißt
        # "überwiegend diese Kabine oder höher" - mindestens ein Segment muss sie erreichen.
        wanted = CABIN_RANK[TRAVEL_CLASS[alert.cabin]]
        cabins = [str(fd.get("cabin") or "").upper()
                  for tp in row.get("travelerPricings") or []
                  for fd in (tp.get("fareDetailsBySegment") or []) if isinstance(fd, dict)]
        if cabins and not any(CABIN_RANK.get(c, -1) >= wanted for c in cabins):
            return None
        return Offer(
            source=self.name,
            origin=origin.upper(),
            destination=dest.upper(),
            price=total,
            currency="EUR",
            roundtrip=alert.roundtrip,
            cabin=alert.cabin,
            departure=departure,
            return_date=return_date,
            airline=str(airline).upper() if airline else None,
            stops=max(self.stops(itin) for itin in itineraries),
            duration_out_min=parse_duration(out.get("duration")),
            duration_back_min=parse_duration(back.get("duration")) if back else None,
            link=None,
        )

    @staticmethod
    def stops(itinerary: dict) -> int:
        """Umstiege einer Richtung: Segmente - 1, plus technische Zwischenlandungen."""
        segments = [s for s in itinerary.get("segments") or [] if isinstance(s, dict)]
        technical = sum(int(_float(s.get("numberOfStops")) or 0) for s in segments)
        return max(len(segments) - 1, 0) + technical


def build_sources(api_key: str | None = None, api_secret: str | None = None, *,
                  env: str = "test", max_requests: int = 100, scan_months: int = 6,
                  request_delay: float = 0.35, today: date | None = None,
                  timeout: float = 20) -> list:
    """Beide Amadeus-Quellen mit gemeinsamem Client (Token, Budget) - für ``Context.sources``."""
    client = AmadeusClient(api_key, api_secret, env=env, max_requests=max_requests,
                           request_delay=request_delay, timeout=timeout)
    return [AmadeusDatesSource(client, scan_months=scan_months, today=today),
            AmadeusOffersSource(client, scan_months=scan_months, today=today)]
