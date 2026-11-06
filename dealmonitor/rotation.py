"""Google-Datumsrotation: jeden Abflugtag einzeln prüfen (Alarm-Option ``rotation``).

Der normale Google-Scan fragt zwei feste Termine je Monat ab. Auf der Kurzstrecke ZRH⇄HAM
schwanken die Preise aber von Tag zu Tag stark (82–174 € one-way über 10 Stichproben) - ein
einzelner billiger Eurowings-Tag fiele durchs Raster. Die Rotation prüft deshalb reihum jeden
Tag der nächsten ``HORIZON_DAYS`` Tage einzeln als One-way, pro Lauf ein Stück davon:

* Die Einträge sind (Strecke, Tag). Jeder Tag hat einen festen Platz im Grundkreis (nach dem
  Kalenderdatum, feste Länge Strecken × Horizont), der Lauf-Index ``n`` wählt ``b`` Plätze ab
  ``(n * b) % Länge``; ein neuer Tag am Ende des Horizonts übernimmt den Platz des vergangenen.
  ``n`` zählt je vollem Lauf um eins weiter (:meth:`Memory.run_number`) - aus der Uhrzeit
  allein (``epoch / Takt``) übersprängen Läufe nahe einer Taktgrenze ganze Fenster; Sofort-
  und Einzelprüfungen zählen nicht weiter (``advance=False``).
* Reicht das Budget für mehr als einen Durchgang pro ``CYCLE_HOURS``, kommen die nächsten 14
  Tage sowie Freitage und Sonntage in einem eigenen Kreis ein zweites Mal dran - der
  Grundkreis bleibt davon unberührt (sonst verschöbe die täglich schwankende Zahl der
  Zweitbesuche den Startpunkt und ließe Tage aus).
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
#: Preise im Gedächtnis gelten einen Durchgang (+ einen Takt), aber mindestens/höchstens so
#: lange (``report_hours``) - ältere Preise werden weder gemeldet noch kombiniert
MIN_KEEP_HOURS = 36
MAX_KEEP_HOURS = 48
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


def _rings(routes: list[tuple[str, str]], today: date,
           horizon_days: int) -> tuple[list[tuple[str, str, date]], list[tuple[str, str, date]]]:
    """(Grundkreis, Zweitbesuche): der Grundkreis enthält jeden (Strecke, Tag) genau einmal,
    Platz nach Kalenderdatum - seine Länge ist fest (Strecken × Horizont), ein Tageswechsel
    verschiebt nichts. Zweitbesuche (nahe Tage, Fr/So) nach Datum; ihre Zahl schwankt täglich."""
    routes = list(dict.fromkeys(routes))
    if not routes or horizon_days <= 0:
        return [], []
    days = [today + timedelta(days=i) for i in range(1, horizon_days + 1)]
    base = sorted(((o, d, day) for day in days for o, d in routes),
                  key=lambda x: (x[2].toordinal() % horizon_days, routes.index(x[:2])))
    extra = [x for x in sorted(base, key=lambda x: (x[2], routes.index(x[:2])))
             if _is_extra(x[2], today)]
    return base, extra


def items(routes: list[tuple[str, str]], today: date, horizon_days: int = HORIZON_DAYS, *,
          capacity: int | None = None) -> list[tuple[str, str, date]]:
    """Der Kreis der Rotation: erst jeder (Strecke, Tag) einmal - Platz nach Kalenderdatum,
    damit ein Tageswechsel nichts verschiebt -, dann die Zweitbesuche (nahe Tage, Fr/So),
    soweit ``capacity`` (Abfragen je Durchgang) über einen Durchgang hinaus reicht."""
    base, extra = _rings(routes, today, horizon_days)
    if capacity is not None:
        extra = extra[:max(capacity - len(base), 0)]
    return base + extra


def _per_cycle(period_hours: float) -> int:
    return max(int(CYCLE_HOURS / max(period_hours, 0.1)), 1)


def base_share(size: int, per_run: int, period_hours: float = PERIOD_HOURS) -> int:
    """Abfragen je Lauf für den Grundkreis (``size`` Einträge): so viele, dass er in
    ``CYCLE_HOURS`` durch ist, höchstens ``per_run`` - der Rest geht an die Zweitbesuche."""
    if per_run <= 0 or size <= 0:
        return 0
    return min(per_run, math.ceil(size / _per_cycle(period_hours)))


def _take(picked: list, ring: list, start: int, count: int) -> int:
    """Bis zu ``count`` Einträge aus ``ring`` ab ``start`` an ``picked`` anhängen, die dort noch
    fehlen (Zweitbesuch = schon im Grundfenster -> nächster). Rückgabe: so viele angehängt."""
    added, have = 0, set(picked)
    for i in range(len(ring)):
        if added >= count:
            break
        item = ring[(start + i) % len(ring)]
        if item not in have:
            picked.append(item)
            have.add(item)
            added += 1
    return added


def slice_for_run(now: datetime, routes: list[tuple[str, str]], horizon_days: int = HORIZON_DAYS,
                  per_run: int = PER_RUN, *, today: date | None = None,
                  period_hours: float = PERIOD_HOURS,
                  index: int | None = None) -> list[tuple[str, str, date]]:
    """Die Abfragen dieses Laufs (ohne Doppelte, nach Datum sortiert). ``n`` = ``index``, ohne
    Angabe aus der Uhrzeit (:func:`run_index`).

    Der Grundkreis (feste Länge, :func:`_rings`) bekommt ``b`` = :func:`base_share` Plätze ab
    ``(n * b) % Länge`` - aufeinanderfolgende Läufe schließen also lückenlos aneinander an, auch
    über Mitternacht. Übriges Budget geht an die Zweitbesuche (eigener Kreis, höchstens so viele,
    wie in einen Durchgang passen) und danach an die nächsten Plätze des Grundkreises."""
    if per_run <= 0:
        return []
    today = today or now.date()
    base, extra = _rings(routes, today, horizon_days)
    if not base:
        return []
    order = list(dict.fromkeys(routes))
    if len(base) <= per_run:                # alles in einem Lauf (Zweitbesuche = dieselben)
        picked = list(base)
    else:
        n = run_index(now, period_hours) if index is None else index
        b = base_share(len(base), per_run, period_hours)
        start = (n * b) % len(base)
        picked = [base[(start + i) % len(base)] for i in range(b)]
        rest = per_run - b
        extra = extra[:rest * _per_cycle(period_hours)]
        if rest and extra:
            e = min(rest, len(extra))
            rest -= _take(picked, extra, n * e, e)
        _take(picked, base, start + b, rest)        # übriges Budget: die nächsten Grundplätze
    return sorted(picked, key=lambda x: (x[2], order.index(x[:2])))


def cycle_hours(routes, horizon_days: int, per_run: int,
                period_hours: float = PERIOD_HOURS) -> float:
    """So lange dauert ein Durchgang (jeder Tag einmal) bei ``per_run`` Abfragen je Lauf."""
    size = len(set(routes)) * max(horizon_days, 0)
    b = base_share(size, per_run, period_hours)
    if b <= 0:
        return float("inf")
    return math.ceil(size / b) * period_hours


def report_hours(cycle: float, period_hours: float = PERIOD_HOURS) -> float:
    """So lange gilt ein gemerkter Preis (Meldung, Kombination, Bestpreis): ein Durchgang plus
    ein Takt Puffer, mindestens ``MIN_KEEP_HOURS``, höchstens ``MAX_KEEP_HOURS`` - bei kleinem
    Budget (langer Durchgang) fallen ältere Preise weg, statt tagealt als Deal zu gelten."""
    return min(max(MIN_KEEP_HOURS, cycle + max(period_hours, 0)), MAX_KEEP_HOURS)


# -- Kombination ----------------------------------------------------------------------
def combine(outbound: dict[date, dict], inbound: dict[date, dict], min_nights: int | None = None,
            max_nights: int | None = None) -> list[tuple[date, date, dict, dict]]:
    """Hin/Rück aus zwei One-ways: je Hinflug-Tag der günstigste Rückflug im Abstand
    ``min_nights..max_nights`` (Standard 1..30). Eingabe: Tag -> Zeile mit ``price``.
    Rückgabe: ``[(hin_tag, rück_tag, hin, rück)]`` nach Gesamtpreis sortiert."""
    # mindestens 1 Nacht: Uhrzeiten kennt das Gedächtnis nicht (Rückflug vor dem Hinflug?)
    lo = 1 if min_nights is None else max(min_nights, 1)
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
    """Ergebniszeilen platzsparend: je Airline/Umstiege die günstigste und jede teurere, die
    schneller ist (sonst fiele bei einer Dauer-Grenze der passende Flug weg), höchstens
    ``MAX_ROWS``."""
    out, fastest = [], {}
    for r in sorted(rows, key=lambda r: r.get("price") or 0):
        key = (r.get("airline") or r.get("airline_code"), r.get("stops"))
        if not _num(r.get("price")) or r["price"] <= 0:
            continue
        minutes = r.get("duration_min") if _num(r.get("duration_min")) else None
        if key in fastest and (minutes is None or fastest[key] is None
                               or minutes >= fastest[key]):
            continue
        fastest[key] = minutes
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
    "insight": {...}}}}, "runs": {alarm_id: {"n": 5, "at": ISO, "cycle_h": 24.0}}}`` - kaputte oder fremde
    Inhalte werden ignoriert, nie ein Absturz.
    """

    def __init__(self, data: dict | None = None) -> None:
        data = data if isinstance(data, dict) else {}
        legs = data.get("legs")
        self.legs: dict = legs if isinstance(legs, dict) else {}
        keep = data.get("keep_h")
        self.keep_hours: float = (min(float(keep), MAX_KEEP_HOURS) if _num(keep) and keep > 0
                                  else MIN_KEEP_HOURS)
        runs = data.get("runs")
        self.runs: dict = runs if isinstance(runs, dict) else {}
        self.changed = False

    def run_number(self, name: str, now: datetime, period_hours: float = PERIOD_HOURS, *,
                   advance: bool = True) -> int:
        """Lauf-Index der Rotation ``name`` (Alarm): je Lauf genau eins weiter - auch wenn
        sich der Laufbeginn um eine Taktgrenze verschiebt oder ein Lauf ausfiel. Ein Lauf
        weniger als einen halben Takt nach dem letzten bekommt denselben Index; ohne
        gespeicherten Stand zählt die Uhrzeit (:func:`run_index`). ``advance=False``
        (Sofort-Prüfung, "Nur diesen prüfen"): nur nachsehen, nichts weiterzählen."""
        entry = self.runs.get(name)
        try:
            n, at = int(entry["n"]), datetime.fromisoformat(entry["at"])
        except (TypeError, ValueError, KeyError):
            n, at = None, None
        if not advance:
            return run_index(now, period_hours) if n is None else n
        if n is not None:
            if (at.tzinfo is None) != (now.tzinfo is None):
                at, now = at.replace(tzinfo=None), now.replace(tzinfo=None)
            if abs((now - at).total_seconds()) < max(period_hours, 0.1) * 1800:
                return n
            n += 1
        else:
            n = run_index(now, period_hours)
        self.runs[name] = {**(entry if isinstance(entry, dict) else {}),
                           "n": n, "at": now.isoformat(timespec="minutes")}
        self.changed = True
        return n

    def set_cycle(self, name: str, hours: float) -> None:
        """Dauer eines Durchgangs der Rotation ``name`` beim letzten vollen Lauf merken."""
        entry = self.runs.get(name)
        entry = entry if isinstance(entry, dict) else {}
        value = round(hours, 1) if math.isfinite(hours) else None
        if entry.get("cycle_h") != value:
            self.runs[name] = {**entry, "cycle_h": value}
            self.changed = True

    def cycle_hours(self, name: str) -> float | None:
        """So lange dauerte ein Durchgang (jeder Tag einmal) zuletzt - None = unbekannt bzw.
        kein Budget. Für die Oberfläche: "täglich geprüft" nur, wenn höchstens ``CYCLE_HOURS``."""
        entry = self.runs.get(name)
        value = entry.get("cycle_h") if isinstance(entry, dict) else None
        return float(value) if _num(value) and value > 0 else None

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
        return {"keep_h": self.keep_hours, "legs": self.legs, "runs": self.runs}
