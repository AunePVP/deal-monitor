"""Preisquelle: Travelpayouts / Aviasales Data API.

Kostenloser API-Token: https://www.travelpayouts.com/ -> Konto anlegen ->
"Tools" -> "API" (Token kopieren) und als Umgebungsvariable
``TRAVELPAYOUTS_TOKEN`` setzen. Der Token geht im Header ``X-Access-Token`` mit
(nie in der URL). HTTP 401 = Token ungültig -> Quelle stoppt für den Lauf.

Wichtig: Die Data API liefert *gecachte* Preise aus Suchen der letzten
Tage, keine Live-Verfügbarkeit. Genau deshalb hängt der Monitor an jeden
Deal einen Vergleichslink (Google Flights) - Prinzip "selbst nachprüfen".

Endpunkte:

* Economy: ``/aviasales/v3/prices_for_dates`` (pro Abflugmonat, inkl. Dauer und
  Umstiegen). Für Strecken innerhalb DACH (ZRH⇄HAM) zusätzlich ``/v1/prices/calendar``:
  günstigster Preis **je Tag** des Monats (``data`` = {"JJJJ-MM-TT": {...}}).
* Business/First: ``/v2/prices/latest`` mit ``trip_class``, ``period_type=year`` (alle
  Preise der letzten 48 h), ``limit=1000``, ``show_to_affiliates=false`` (alle Preise, nicht
  nur die mit Partner-Marker). Liegen alle Abflüge in einem Land (z. B. @DE) und alle
  Ziele in einem Land (@CN_MAJOR), genügt eine Anfrage mit Ländercodes ``DE``/``CN``.

**Stand: ohne Token nicht live prüfbar.** ``calendar`` und ``latest`` sind streng nach Doku
gebaut (Tests mit Fixtures in Doku-Struktur); UNBESTÄTIGT sind Ländercodes bei ``latest``
und der Parametername ``depart_date`` beim Kalender (Doku-Tabelle: ``departure_date``,
alle Doku-Beispiele: ``depart_date``). Unerwartete Antworten werden übersprungen, nie Absturz.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timezone

from .. import http, regions
from ..models import Alert, Offer
from . import SourceError

log = logging.getLogger(__name__)

BASE = "https://api.travelpayouts.com"
V3_PATH = "/aviasales/v3/prices_for_dates"
CALENDAR_PATH = "/v1/prices/calendar"
LATEST_PATH = "/v2/prices/latest"
TRIP_CLASS = {"economy": 0, "business": 1, "first": 2}
#: Höchstwert von ``limit`` bei ``v2/prices/latest`` laut Doku
LATEST_LIMIT = 1000
#: Ländercode -> Codes aus ``regions``, für die er stehen darf (Hongkong/Macau sind eigene Länder)
COUNTRIES = {
    "DE": frozenset(regions.DE_AIRPORTS), "CH": frozenset(regions.CH_AIRPORTS),
    "AT": frozenset(regions.AT_AIRPORTS), "CN": frozenset(regions.CN_AIRPORTS) - {"HKG", "MFM"},
}

TOKEN_INVALID = "Travelpayouts: Token ungültig (HTTP 401) – bitte in den Einstellungen prüfen."
TOKEN_BLOCKED = ("Travelpayouts: Token gesperrt oder ohne Zugriff (HTTP 403) – bitte in den "
                 "Einstellungen prüfen.")


def _months(start: date, count: int) -> list[str]:
    out, y, m = [], start.year, start.month
    for _ in range(count):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _date(value) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def _when(value) -> datetime | None:
    """ISO-Zeit (``expires_at``, UTC) -> aware datetime; ungültig -> None."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _price(value) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def country_code(codes: list[str]) -> str | None:
    """Ländercode, wenn alle (mind. 2) Codes in einem Land liegen - sonst None."""
    if len(codes) < 2:
        return None
    for cc, members in COUNTRIES.items():
        if all(c in members for c in codes):
            return cc
    return None


def home_route(origin: str, dest: str) -> bool:
    """Strecke innerhalb DACH (ZRH⇄HAM): dort lohnt der Tageskalender."""
    return regions.is_home(origin) and regions.is_home(dest)


class TravelpayoutsSource:
    name = "travelpayouts"
    records_history = True

    #: so viele Netzwerkfehler hintereinander -> API gilt für diesen Lauf als nicht erreichbar
    MAX_CONSECUTIVE_FAILURES = 5

    def __init__(
        self,
        token: str | None = None,
        *,
        currency: str = "eur",
        scan_months: int = 6,
        request_delay: float = 0.35,
        marker: str | None = None,
        today: date | None = None,
        calendar: bool = True,
    ) -> None:
        self.token = token or os.environ.get("TRAVELPAYOUTS_TOKEN")
        self.currency = currency.lower()
        self.scan_months = scan_months
        self.request_delay = request_delay
        self.marker = marker or os.environ.get("TRAVELPAYOUTS_MARKER")
        self.today = today
        #: Tageskalender (``v1/prices/calendar``) für Economy-Strecken innerhalb DACH
        self.calendar = calendar
        #: nach dem letzten ``fetch``: None = alles abgefragt, sonst
        #: {"message": str, "routes": {(origin, dest), ...}} der fehlgeschlagenen Strecken
        self.incomplete: dict | None = None
        self._down: str | None = None   # Schutzschalter: API in diesem Lauf nicht erreichbar

    # -- öffentliche Schnittstelle ---------------------------------------
    def fetch(self, alert: Alert) -> list[Offer]:
        self.incomplete = None
        if not self.token:
            raise SourceError(
                "Travelpayouts-Token fehlt - kostenlos unter travelpayouts.com anlegen "
                "und in den Einstellungen eintragen (oder TRAVELPAYOUTS_TOKEN setzen)."
            )
        if alert.cabin == "premium":
            raise SourceError("Premium Economy wird von der Data API nicht unterstützt")
        if self._down:
            raise SourceError(self._down)

        months = _months(self.today or date.today(), alert.months_ahead or self.scan_months)
        offers: list[Offer] = []
        errors = 0
        consecutive = 0
        failed: set[tuple[str, str]] = set()
        plan = self._plan(alert, months)
        # Eine Abfrage = eine Strecke und ein Monat (Economy, ggf. mit Kalender) bzw. eine
        # Strecke oder ein Länderpaar (Business/First); scheitert sie, gilt ihre Strecke als offen
        for routes, label, run in plan:
            try:
                offers += run()
                consecutive = 0
            except http.HttpError as e:
                if e.status in (401, 403):
                    # Token falsch/gesperrt: jede weitere Anfrage scheitert genauso
                    self._down = TOKEN_INVALID if e.status == 401 else TOKEN_BLOCKED
                    raise SourceError(self._down) from None
                errors += 1
                failed |= routes
                log.warning("Travelpayouts %s: %s", label, e)
                # Netzwerk-/Serverfehler zählen für den Schutzschalter, 4xx nicht
                if e.network or (e.status is not None and e.status >= 500):
                    consecutive += 1
                    if consecutive >= self.MAX_CONSECUTIVE_FAILURES:
                        self._down = (f"Travelpayouts nicht erreichbar ({consecutive} Fehler "
                                      f"hintereinander, Prüfung abgebrochen): {e}")
                        raise SourceError(self._down) from None
                else:
                    consecutive = 0
        total = len(plan)
        if errors and errors == total:
            raise SourceError(f"Alle {total} Travelpayouts-Anfragen fehlgeschlagen")
        if errors:
            self.incomplete = {"message": f"{errors} von {total} Anfragen fehlgeschlagen",
                               "routes": failed}
        return offers

    def _plan(self, alert: Alert, months: list[str]) -> list:
        """Liste von (Strecken, Bezeichnung für Logs, Funktion -> list[Offer])."""
        if alert.cabin == "economy":
            plan = []
            for o, d in alert.pairs:
                for month in months:
                    plan.append(({(o, d)}, f"{o}-{d} {month}",
                                 lambda o=o, d=d, m=month: self._economy(alert, o, d, m)))
            return plan
        # Ländercode je Seite, sonst der einzelne Code; mindestens eine Seite muss ein Land sein
        q_origin = country_code(alert.origins) or (
            alert.origins[0] if len(alert.origins) == 1 else None)
        q_dest = country_code(alert.destinations) or (
            alert.destinations[0] if len(alert.destinations) == 1 else None)
        if q_origin and q_dest and 2 in (len(q_origin), len(q_dest)):
            return [(set(alert.pairs), f"{q_origin}-{q_dest}",
                     lambda: self._latest_country(alert, q_origin, q_dest, months))]
        return [({(o, d)}, f"{o}-{d}", lambda o=o, d=d: self._latest(alert, o, d, months))
                for o, d in alert.pairs]

    # -- Anfragen ---------------------------------------------------------
    def _get(self, path: str, params: dict) -> dict:
        """Eine Anfrage mit Token im Header; prüft Erfolg und Währung der Antwort."""
        try:
            data = http.get_json(
                BASE + path, params=params,
                headers={"X-Access-Token": self.token, "Accept-Encoding": "identity"},
                retries=1,
            )
        finally:
            if self.request_delay:
                time.sleep(self.request_delay)
        if not isinstance(data, dict):
            raise http.HttpError("Unerwartete Antwort der Travelpayouts-API")
        if not data.get("success", True):
            error = str(data.get("error") or data)
            if any(w in error.lower() for w in ("unauthorized", "token")):
                raise http.HttpError(error, 401)
            raise http.HttpError(error)
        currency = data.get("currency")
        if isinstance(currency, str) and currency and currency.lower() != self.currency:
            raise http.HttpError(f"Antwort in {currency.upper()} statt {self.currency.upper()}")
        return data

    def v3_params(self, alert: Alert, origin: str, dest: str, month: str) -> dict:
        return {
            "origin": origin,
            "destination": dest,
            "departure_at": month,
            "one_way": "false" if alert.roundtrip else "true",
            "direct": "true" if alert.max_stops == 0 else "false",
            "currency": self.currency,
            "sorting": "price",
            "unique": "false",
            "limit": 30,
            "page": 1,
        }

    def calendar_params(self, alert: Alert, origin: str, dest: str, month: str) -> dict:
        """``v1/prices/calendar``: ohne ``return_date`` liefert die API one-way-Preise, darum
        bei Hin/Rück den Rückflug im selben Monat (UNBESTÄTIGT, ob spätere Rückflüge fehlen)."""
        params = {"origin": origin, "destination": dest, "depart_date": month,
                  "calendar_type": "departure_date", "currency": self.currency}
        if alert.roundtrip:
            params["return_date"] = month
        return params

    def latest_params(self, alert: Alert, origin: str, dest: str) -> dict:
        return {
            "origin": origin,
            "destination": dest,
            "period_type": "year",
            "one_way": "false" if alert.roundtrip else "true",
            "trip_class": TRIP_CLASS[alert.cabin],
            "currency": self.currency,
            "sorting": "price",
            "limit": LATEST_LIMIT,
            "page": 1,
            "show_to_affiliates": "false",
        }

    # -- Economy ----------------------------------------------------------
    def _economy(self, alert: Alert, origin: str, dest: str, month: str) -> list[Offer]:
        offers = self._prices_for_dates(alert, origin, dest, month)
        if self.calendar and home_route(origin, dest):
            data = self._get(CALENDAR_PATH, self.calendar_params(alert, origin, dest, month))
            offers += self.parse_calendar(data, alert, origin, dest)
        return offers

    def _prices_for_dates(self, alert: Alert, origin: str, dest: str, month: str) -> list[Offer]:
        data = self._get(V3_PATH, self.v3_params(alert, origin, dest, month))
        rows = data.get("data")
        if not isinstance(rows, list):
            return []
        return [o for o in (self.parse_v3(row, alert) for row in rows if isinstance(row, dict))
                if o]

    def _link(self, path: str | None) -> str | None:
        if not path:
            return None
        link = "https://www.aviasales.com" + path
        if self.marker:
            link += ("&" if "?" in link else "?") + f"marker={self.marker}"
        return link

    def parse_v3(self, row: dict, alert: Alert) -> Offer | None:
        price = row.get("price")
        if price is None:
            return None
        transfers = row.get("transfers")
        back = row.get("return_transfers")
        stops = max(x for x in (transfers, back, 0) if x is not None)
        return Offer(
            source=self.name,
            origin=(row.get("origin") or "").upper(),
            destination=(row.get("destination") or "").upper(),
            price=float(price),
            currency=self.currency.upper(),
            roundtrip=alert.roundtrip,
            cabin="economy",
            departure=_date(row.get("departure_at")),
            return_date=_date(row.get("return_at")) if alert.roundtrip else None,
            airline=row.get("airline"),
            stops=stops,
            duration_out_min=row.get("duration_to") or (None if alert.roundtrip else row.get("duration")),
            duration_back_min=row.get("duration_back"),
            link=self._link(row.get("link")),
        )

    def parse_calendar(self, data: dict, alert: Alert, origin: str, dest: str,
                       now: datetime | None = None) -> list[Offer]:
        """Antwort von ``v1/prices/calendar``: ``data`` = {"JJJJ-MM-TT": {origin, destination,
        price, transfers, airline, flight_number, departure_at, return_at, expires_at}}.
        Abgelaufene Preise (``expires_at`` vorbei) und Hin/Rück ohne Rückflug fallen weg."""
        rows = data.get("data") if isinstance(data, dict) else None
        if isinstance(rows, dict):
            items = list(rows.items())
        elif isinstance(rows, list):
            items = [(None, r) for r in rows]
        else:
            return []
        now = now or datetime.now(timezone.utc)
        offers = []
        for day, row in items:
            if not isinstance(row, dict) or (price := _price(row.get("price"))) is None:
                continue
            departure = _date(row.get("departure_at")) or _date(day)
            back = _date(row.get("return_at")) if alert.roundtrip else None
            expires = _when(row.get("expires_at"))
            if departure is None or (alert.roundtrip and back is None) \
                    or (expires is not None and expires <= now):
                continue
            o = str(row.get("origin") or origin).upper()
            d = str(row.get("destination") or dest).upper()
            path = f"/search/{o}{departure:%d%m}{d}{f'{back:%d%m}' if back else ''}1"
            offers.append(Offer(
                source=self.name, origin=o, destination=d, price=price,
                currency=self.currency.upper(), roundtrip=alert.roundtrip, cabin="economy",
                departure=departure, return_date=back,
                airline=str(row["airline"]) if row.get("airline") else None,
                stops=_int(row.get("transfers")), link=self._link(path), expires=expires,
            ))
        return offers

    # -- Business / First -------------------------------------------------
    def _latest(self, alert: Alert, origin: str, dest: str, months: list[str]) -> list[Offer]:
        data = self._get(LATEST_PATH, self.latest_params(alert, origin, dest))
        return self.parse_latest(data, alert, months, origin, dest)

    def _latest_country(self, alert: Alert, origin: str, dest: str,
                        months: list[str]) -> list[Offer]:
        """Eine Anfrage mit Ländercodes; lehnt die API sie ab (HTTP 400), je Strecke einzeln."""
        try:
            data = self._get(LATEST_PATH, self.latest_params(alert, origin, dest))
        except http.HttpError as e:
            if e.status != 400:
                raise
            log.info("Travelpayouts: Ländercodes %s-%s abgelehnt (%s) - frage je Strecke ab",
                     origin, dest, e)
            return [o for a, b in alert.pairs for o in self._latest(alert, a, b, months)]
        return self.parse_latest(data, alert, months)

    def parse_latest(self, data: dict, alert: Alert, months: list[str],
                     origin: str | None = None, dest: str | None = None) -> list[Offer]:
        """Antwort von ``v2/prices/latest``: ``data`` = [{trip_class, origin, destination,
        depart_date, return_date, number_of_changes, value, found_at, actual, …}].

        Nur Zeilen der gewünschten Klasse (nie Economy als Business), mit Abflug im
        Suchzeitraum, nicht ``actual: false`` und - bei Hin/Rück - mit Rückflugdatum.
        Ohne ``origin``/``dest`` (Länderabfrage) nur Strecken des Alarms."""
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []
        wanted = TRIP_CLASS[alert.cabin]
        pairs = set(alert.pairs)
        offers = []
        for row in rows:
            if not isinstance(row, dict) or row.get("actual") is False:
                continue
            if row.get("trip_class") is not None and _int(row["trip_class"]) != wanted:
                continue
            price = _price(row.get("value"))
            o = str(row.get("origin") or origin or "").upper()
            d = str(row.get("destination") or dest or "").upper()
            departure = _date(row.get("depart_date"))
            back = _date(row.get("return_date")) if alert.roundtrip else None
            if price is None or departure is None or f"{departure:%Y-%m}" not in months:
                continue
            if (alert.roundtrip and back is None) or (origin is None and (o, d) not in pairs):
                continue
            offers.append(Offer(
                source=self.name, origin=o, destination=d, price=price,
                currency=self.currency.upper(), roundtrip=alert.roundtrip, cabin=alert.cabin,
                departure=departure, return_date=back, stops=_int(row.get("number_of_changes")),
                link=None,
            ))
        return offers
