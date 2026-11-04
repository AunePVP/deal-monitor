"""Preisquelle: Travelpayouts / Aviasales Data API.

Kostenloser API-Token: https://www.travelpayouts.com/ -> Konto anlegen ->
"Tools" -> "API" (Token kopieren) und als Umgebungsvariable
``TRAVELPAYOUTS_TOKEN`` setzen.

Wichtig: Die Data API liefert *gecachte* Preise aus Suchen der letzten
Tage, keine Live-Verfügbarkeit. Genau deshalb hängt der Monitor an jeden
Deal einen Vergleichslink (Google Flights) - Prinzip "selbst nachprüfen".

Economy: ``/aviasales/v3/prices_for_dates`` (pro Abflugmonat, inkl. Dauer
und Umstiegen). Premium-Kabinen: ``/v2/prices/latest`` mit ``trip_class``;
die Datenlage ist dort deutlich dünner.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime

from .. import http
from ..models import Alert, Offer
from . import SourceError

log = logging.getLogger(__name__)

BASE = "https://api.travelpayouts.com"
TRIP_CLASS = {"economy": 0, "business": 1, "first": 2}


def _months(start: date, count: int) -> list[str]:
    out, y, m = [], start.year, start.month
    for _ in range(count):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


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
    ) -> None:
        self.token = token or os.environ.get("TRAVELPAYOUTS_TOKEN")
        self.currency = currency.lower()
        self.scan_months = scan_months
        self.request_delay = request_delay
        self.marker = marker or os.environ.get("TRAVELPAYOUTS_MARKER")
        self.today = today
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
        for origin, dest in alert.pairs:
            for month in months:
                try:
                    if alert.cabin == "economy":
                        offers += self._prices_for_dates(alert, origin, dest, month)
                    else:
                        offers += self._latest(alert, origin, dest, month)
                    consecutive = 0
                except http.HttpError as e:
                    if e.status in (401, 403):
                        # Token falsch/gesperrt: jede weitere Anfrage scheitert genauso
                        self._down = ("Travelpayouts lehnt den Token ab (HTTP "
                                      f"{e.status}) – bitte in den Einstellungen prüfen.")
                        raise SourceError(self._down) from None
                    errors += 1
                    failed.add((origin, dest))
                    log.warning("Travelpayouts %s-%s %s: %s", origin, dest, month, e)
                    # Netzwerk-/Serverfehler zählen für den Schutzschalter, 4xx nicht
                    if e.network or (e.status is not None and e.status >= 500):
                        consecutive += 1
                        if consecutive >= self.MAX_CONSECUTIVE_FAILURES:
                            self._down = (f"Travelpayouts nicht erreichbar ({consecutive} Fehler "
                                          f"hintereinander, Prüfung abgebrochen): {e}")
                            raise SourceError(self._down) from None
                    else:
                        consecutive = 0
                time.sleep(self.request_delay)
        total = len(alert.pairs) * len(months)
        if errors and errors == total:
            raise SourceError(f"Alle {total} Travelpayouts-Anfragen fehlgeschlagen")
        if errors:
            self.incomplete = {"message": f"{errors} von {total} Anfragen fehlgeschlagen",
                               "routes": failed}
        return offers

    # -- Economy ----------------------------------------------------------
    def _prices_for_dates(self, alert: Alert, origin: str, dest: str, month: str) -> list[Offer]:
        data = http.get_json(
            f"{BASE}/aviasales/v3/prices_for_dates",
            params={
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
                "token": self.token,
            },
            headers={"Accept-Encoding": "identity"},
            retries=1,
        )
        if not isinstance(data, dict):
            raise http.HttpError("Unerwartete Antwort der Travelpayouts-API")
        if not data.get("success", True):
            raise http.HttpError(str(data.get("error") or data))
        return [o for o in (self.parse_v3(row, alert) for row in data.get("data") or []) if o]

    def parse_v3(self, row: dict, alert: Alert) -> Offer | None:
        price = row.get("price")
        if price is None:
            return None
        transfers = row.get("transfers")
        back = row.get("return_transfers")
        stops = max(x for x in (transfers, back, 0) if x is not None)
        link = row.get("link")
        if link:
            link = "https://www.aviasales.com" + link
            if self.marker:
                link += ("&" if "?" in link else "?") + f"marker={self.marker}"
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
            link=link,
        )

    # -- Business / First -------------------------------------------------
    def _latest(self, alert: Alert, origin: str, dest: str, month: str) -> list[Offer]:
        wanted = TRIP_CLASS[alert.cabin]
        data = http.get_json(
            f"{BASE}/v2/prices/latest",
            params={
                "origin": origin,
                "destination": dest,
                "beginning_of_period": f"{month}-01",
                "period_type": "month",
                "one_way": "false" if alert.roundtrip else "true",
                "trip_class": wanted,
                "currency": self.currency,
                "sorting": "price",
                "limit": 30,
                "page": 1,
                "show_to_affiliates": "true",
                "token": self.token,
            },
            retries=1,
        )
        if not isinstance(data, dict):
            raise http.HttpError("Unerwartete Antwort der Travelpayouts-API")
        if not data.get("success", True):
            raise http.HttpError(str(data.get("error") or data))
        offers = []
        for row in data.get("data") or []:
            # Sicherheitsnetz: nie einen Economy-Preis als Business melden
            if row.get("trip_class") is not None and int(row["trip_class"]) != wanted:
                continue
            price = row.get("value")
            if price is None:
                continue
            offers.append(Offer(
                source=self.name,
                origin=(row.get("origin") or origin).upper(),
                destination=(row.get("destination") or dest).upper(),
                price=float(price),
                currency=self.currency.upper(),
                roundtrip=alert.roundtrip,
                cabin=alert.cabin,
                departure=_date(row.get("depart_date")),
                return_date=_date(row.get("return_date")) if alert.roundtrip else None,
                stops=row.get("number_of_changes"),
                link=None,
            ))
        return offers
