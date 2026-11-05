"""Google-Datumsrotation: jeden Abflugtag einzeln prüfen (Alarm-Option ``rotation``).

Der normale Google-Scan fragt zwei feste Termine je Monat ab. Auf der Kurzstrecke ZRH⇄HAM
schwanken die Preise aber von Tag zu Tag stark (82–174 € one-way über 10 Stichproben) - ein
einzelner billiger Eurowings-Tag fiele durchs Raster. Die Rotation prüft deshalb reihum jeden
Tag der nächsten ``HORIZON_DAYS`` Tage einzeln als One-way, pro Lauf ein Stück davon:

* Die Einträge sind (Strecke, Tag). Jeder Tag hat einen festen Platz im Kreis (nach dem
  Kalenderdatum), der Lauf-Index ``n = int(epoch / Takt)`` wählt ``per_run`` Plätze ab
  ``(n * per_run) % Länge``. Das kommt ohne Datenbankzustand aus; ein neuer Tag am Ende des
  Horizonts übernimmt den Platz des vergangenen.
* Reicht das Budget für mehr als einen Durchgang pro ``CYCLE_HOURS``, kommen die nächsten 14
  Tage sowie Freitage und Sonntage ein zweites Mal dran.
* ``per_run`` ist der Google-Anteil des Alarms (``GoogleFlightsSource.begin_run``) - die
  Rotation verdrängt also keine anderen Alarme. Kleines Budget = längerer Durchgang.

Zwischen zwei Prüfungen desselben Tages gilt der zuletzt gesehene Preis (:class:`Memory`, im
Store als Quellen-Zustand). Daraus kombiniert :func:`combine` Hin- und Rückflug aus zwei
One-ways; solche Angebote tragen ``Offer.legs`` und werden als "kombiniert aus 2 One-way"
gekennzeichnet (:func:`note`).
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta

log = logging.getLogger(__name__)

#: so viele Tage voraus wird rotiert
HORIZON_DAYS = 90
#: höchstens so viele Abfragen je Lauf (der tatsächliche Anteil kommt aus dem Google-Budget)
PER_RUN = 23
#: Takt, in dem der Lauf-Index weiterzählt (Standard-Prüfintervall)
PERIOD_HOURS = 3.0
#: in dieser Zeit soll ein Durchgang fertig sein; Überschuss geht an die Zweitbesuche
CYCLE_HOURS = 24
#: die nächsten so viele Tage sowie diese Wochentage (Fr, So) zählen doppelt
NEAR_DAYS = 14
EXTRA_WEEKDAYS = frozenset({4, 6})
#: Name des Quellen-Zustands im Store
STATE_NAME = "google-rotation"
#: Preise im Gedächtnis gelten 1,5 Durchgänge lang, aber mindestens/höchstens so lange
MIN_KEEP_HOURS = 48
MAX_KEEP_HOURS = 240
#: höchstens so viele Zeilen (je Airline/Umstiege die günstigste) je Strecke und Tag
MAX_ROWS = 6
COMBO_LABEL = "kombiniert aus 2 One-way"


# -- Planung --------------------------------------------------------------------------
def routes_for(alert) -> list[tuple[str, str]]:
    """Strecken der Rotation: bei one-way die des Alarms, bei Hin/Rück beide Richtungen."""
    out: list[tuple[str, str]] = []
    for o, d in alert.pairs:
        for route in ((o, d), (d, o)) if alert.roundtrip else ((o, d),):
            if route not in out:
                out.append(route)
    return out


def horizon_for(alert, scan_months: int) -> int:
    """Tage voraus: ``HORIZON_DAYS``, höchstens so weit wie ``months_ahead`` reicht."""
    months = alert.months_ahead or scan_months or 1
    return max(min(HORIZON_DAYS, months * 30), 1)


def run_index(now: datetime, period_hours: float = PERIOD_HOURS) -> int:
    return int(now.timestamp() // (max(period_hours, 0.1) * 3600))


def _is_extra(day: date, today: date) -> bool:
    return (day - today).days <= NEAR_DAYS or day.weekday() in EXTRA_WEEKDAYS


def items(routes: list[tuple[str, str]], today: date, horizon_days: int = HORIZON_DAYS, *,
          capacity: int | None = None) -> list[tuple[str, str, date]]:
    """Der Kreis der Rotation: erst jeder (Strecke, Tag) einmal - Platz nach Kalenderdatum,
    damit ein Tageswechsel nichts verschiebt -, dann die Zweitbesuche (nahe Tage, Fr/So),
    soweit ``capacity`` (Abfragen je Durchgang) über einen Durchgang hinaus reicht."""
    routes = list(dict.fromkeys(routes))
    if not routes or horizon_days <= 0:
        return []
    days = [today + timedelta(days=i) for i in range(1, horizon_days + 1)]
    base = sorted(((o, d, day) for day in days for o, d in routes),
                  key=lambda x: (x[2].toordinal() % horizon_days, routes.index(x[:2])))
    extra = [x for x in sorted(base, key=lambda x: x[2]) if _is_extra(x[2], today)]
    if capacity is not None:
        extra = extra[:max(capacity - len(base), 0)]
    return base + extra


def slice_for_run(now: datetime, routes: list[tuple[str, str]], horizon_days: int = HORIZON_DAYS,
                  per_run: int = PER_RUN, *, today: date | None = None,
                  period_hours: float = PERIOD_HOURS) -> list[tuple[str, str, date]]:
    """Die Abfragen dieses Laufs: ``per_run`` Einträge ``(origin, dest, tag)`` des Kreises
    ab ``(n * per_run) % Länge`` - ohne Doppelte, nach Datum sortiert."""
    if per_run <= 0:
        return []
    today = today or now.date()
    per_cycle = max(int(CYCLE_HOURS / max(period_hours, 0.1)), 1)
    ring = items(routes, today, horizon_days, capacity=per_run * per_cycle)
    if not ring:
        return []
    if len(set(ring)) <= per_run:
        picked = list(dict.fromkeys(ring))
    else:
        start = (run_index(now, period_hours) * per_run) % len(ring)
        picked = list(dict.fromkeys(ring[(start + i) % len(ring)] for i in range(per_run)))
    return sorted(picked, key=lambda x: (x[2], routes.index(x[:2])))


def cycle_hours(routes, horizon_days: int, per_run: int,
                period_hours: float = PERIOD_HOURS) -> float:
    """So lange dauert ein Durchgang (jeder Tag einmal) bei ``per_run`` Abfragen je Lauf."""
    if per_run <= 0:
        return float("inf")
    return math.ceil(len(set(routes)) * horizon_days / per_run) * period_hours


# -- Kombination ----------------------------------------------------------------------
def combine(outbound: dict[date, dict], inbound: dict[date, dict], min_nights: int | None = None,
            max_nights: int | None = None) -> list[tuple[date, date, dict, dict]]:
    """Hin/Rück aus zwei One-ways: je Hinflug-Tag der günstigste Rückflug im Abstand
    ``min_nights..max_nights`` (Standard 1..30). Eingabe: Tag -> Zeile mit ``price``.
    Rückgabe: ``[(hin_tag, rück_tag, hin, rück)]`` nach Gesamtpreis sortiert."""
    lo = 1 if min_nights is None else max(min_nights, 0)
    hi = 30 if max_nights is None else max_nights
    out = []
    for dep, leg in outbound.items():
        best = None
        for nights in range(lo, hi + 1):
            back = inbound.get(dep + timedelta(days=nights))
            if back is not None and (best is None or back["price"] < best[1]["price"]):
                best = (dep + timedelta(days=nights), back)
        if best:
            out.append((dep, best[0], leg, best[1]))
    out.sort(key=lambda x: (x[2]["price"] + x[3]["price"], x[0]))
    return out


def note(offer) -> str | None:
    """Hinweis für Meldung und Oberfläche bei kombinierten One-ways (sonst ``None``):
    "kombiniert aus 2 One-way: hin 82 € (Eurowings), zurück 70 € (Eurowings)"."""
    legs = getattr(offer, "legs", None)
    if not legs or len(legs) != 2:
        return None
    from .principles import euro
    parts = []
    for word, leg in zip(("hin", "zurück"), legs):
        try:
            text = f"{word} {euro(float(leg['price']))}"
        except (KeyError, TypeError, ValueError):
            return COMBO_LABEL
        if leg.get("airline"):
            text += f" ({leg['airline']})"
        parts.append(text)
    return f"{COMBO_LABEL}: " + ", ".join(parts)


# -- Gedächtnis -----------------------------------------------------------------------
def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x


def compact_rows(rows: list[dict]) -> list[list]:
    """Ergebniszeilen platzsparend: je Airline/Umstiege die günstigste, höchstens ``MAX_ROWS``."""
    out, seen = [], set()
    for r in sorted(rows, key=lambda r: r.get("price") or 0):
        key = (r.get("airline") or r.get("airline_code"), r.get("stops"))
        if key in seen or not _num(r.get("price")) or r["price"] <= 0:
            continue
        seen.add(key)
        out.append([float(r["price"]), r.get("airline"), r.get("airline_code"), r.get("stops"),
                    r.get("duration_min")])
        if len(out) >= MAX_ROWS:
            break
    return out


def expand_rows(rows) -> list[dict]:
    """Gegenstück zu :func:`compact_rows`; Unbrauchbares wird still übergangen."""
    out = []
    for r in rows if isinstance(rows, list) else ():
        if not (isinstance(r, list) and len(r) == 5 and _num(r[0]) and r[0] > 0):
            continue
        price, airline, code, stops, minutes = r
        out.append({"price": float(r[0]),
                    "airline": airline if isinstance(airline, str) else None,
                    "airline_code": code if isinstance(code, str) else None,
                    "stops": stops if isinstance(stops, int) and not isinstance(stops, bool) else None,
                    "duration_min": minutes if _num(minutes) else None,
                    "roundtrip": False})
    return out


#: aus dem Preisniveau behalten (ohne den langen Verlauf)
INSIGHT_KEYS = ("level", "level_code", "lowest", "typical", "saving", "typical_low",
                "typical_high")


class Memory:
    """Zuletzt gesehene One-way-Preise je Strecke, Kabine und Tag (JSON im Store).

    ``{"keep_h": 48, "legs": {"ZRH-HAM-economy": {"2027-01-14": {"at": ISO, "rows": [...],
    "insight": {...}}}}}`` - kaputte oder fremde Inhalte werden ignoriert, nie ein Absturz.
    """

    def __init__(self, data: dict | None = None) -> None:
        data = data if isinstance(data, dict) else {}
        legs = data.get("legs")
        self.legs: dict = legs if isinstance(legs, dict) else {}
        keep = data.get("keep_h")
        self.keep_hours: float = float(keep) if _num(keep) and keep > 0 else MIN_KEEP_HOURS
        self.changed = False

    @staticmethod
    def key(origin: str, dest: str, cabin: str) -> str:
        return f"{origin.upper()}-{dest.upper()}-{cabin}"

    def put(self, origin: str, dest: str, cabin: str, day: date, rows: list[dict],
            insight: dict | None, now: datetime) -> None:
        entry = {"at": now.isoformat(timespec="minutes"), "rows": compact_rows(rows)}
        if insight:
            entry["insight"] = {k: insight.get(k) for k in INSIGHT_KEYS if insight.get(k) is not None}
        self.legs.setdefault(self.key(origin, dest, cabin), {})[day.isoformat()] = entry
        self.changed = True

    def get(self, origin: str, dest: str, cabin: str, today: date,
            now: datetime) -> dict[date, dict]:
        """Gültige Einträge: Tag nach ``today``, nicht älter als ``keep_hours``.
        Tag -> ``{"at": datetime, "rows": [zeilen], "insight": dict|None}``."""
        out: dict[date, dict] = {}
        days = self.legs.get(self.key(origin, dest, cabin))
        if not isinstance(days, dict):
            return out
        for raw_day, entry in days.items():
            try:
                day = date.fromisoformat(raw_day)
                at = datetime.fromisoformat(entry["at"])
            except (TypeError, ValueError, KeyError):
                continue
            if day <= today or not self._fresh(at, now):
                continue
            rows = expand_rows(entry.get("rows"))
            if rows:
                if at.tzinfo is not None:       # wie die übrigen Offers: naive Ortszeit
                    at = at.astimezone().replace(tzinfo=None)
                insight = entry.get("insight")
                out[day] = {"at": at, "rows": rows,
                            "insight": dict(insight) if isinstance(insight, dict) else None}
        return out

    def _fresh(self, at: datetime, now: datetime) -> bool:
        if (at.tzinfo is None) != (now.tzinfo is None):
            at, now = at.replace(tzinfo=None), now.replace(tzinfo=None)
        return now - at <= timedelta(hours=self.keep_hours)

    def prune(self, today: date, now: datetime) -> None:
        """Vergangene und veraltete Tage entfernen."""
        for key in list(self.legs):
            days = self.legs[key]
            if not isinstance(days, dict):
                del self.legs[key]
                self.changed = True
                continue
            for raw_day in list(days):
                entry = days[raw_day]
                try:
                    ok = (date.fromisoformat(raw_day) > today
                          and self._fresh(datetime.fromisoformat(entry["at"]), now))
                except (TypeError, ValueError, KeyError):
                    ok = False
                if not ok:
                    del days[raw_day]
                    self.changed = True
            if not days:
                del self.legs[key]

    def dump(self) -> dict:
        return {"keep_h": self.keep_hours, "legs": self.legs}
