"""Zeitplan: prüft automatisch und startet manuelle Läufe.

Drei Takte (nachts wird nicht gedrosselt - Error Fares kommen auch nachts):

* **voller Lauf** alle ``interval_hours`` - alle aktiven Quellen (Preis-APIs, Google Flights,
  Deal-Seiten) plus Radar (``trigger="schedule"``),
* **Feed-Lauf** alle ``feed_interval_minutes`` - nur die Deal-Seiten (Feeds) plus Radar
  (``trigger="feeds"``), damit ein Error Fare nicht erst Stunden später auffällt,
* **Schnell-Lauf** alle ``fast_interval_minutes`` - nur die leichten Deal-Seiten mit
  ``Preset.lane == "fast"`` plus Radar (``trigger="fast"``); nur wenn eine gewählt ist.

Dazu die **Sofort-Prüfung** (``trigger="hot"``): Meldet ein Feed- oder Schnell-Lauf einen
Artikel-Deal mit Hot-Marker (``ERROR FARE`` …) oder unter der Preisgrenze eines Alarms, prüft
der Zeitplan diesen Alarm gleich danach bei Google Flights - als eigener kleiner Lauf mit
höchstens ``HOT_GOOGLE_REQUESTS`` Abfragen; das Budget der vollen Läufe bleibt unberührt.

Nach jedem Lauf schreibt :mod:`dealmonitor.health` die Quellen-Gesundheit fort.

Es läuft immer höchstens ein Lauf gleichzeitig (Lock). Jeder Lauf - egal ob
geplant, manuell oder per CLI - landet als Zeile in der Tabelle ``runs``.
Fehler beenden den Zeitplan-Thread nie.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta

from . import currency, health, radar
from .appsettings import Context
from .monitor import RunResult, run
from .sources.feed import PRESETS_BY_ID
from .store import FAST_TRIGGERS, HOT_TRIGGER

log = logging.getLogger(__name__)

POLL_SECONDS = 30   # so oft wird geprüft, ob ein Lauf fällig ist (Intervall-Änderungen greifen)
MODES = ("full", "feeds", "fast", "hot")
#: Google-Budget einer Sofort-Prüfung (eigener kleiner Lauf, nicht vom vollen Lauf abgezogen)
HOT_GOOGLE_REQUESTS = 5
#: derselbe Alarm wird höchstens so oft sofort geprüft
HOT_COOLDOWN = timedelta(minutes=30)


_ALERT_PREFIX = re.compile(r"^\[([^\]]+)\] (.*)$", re.S)


def merge_errors(errors: list[str]) -> list[str]:
    """Gleiche Fehler mehrerer Alarme zusammenfassen: "[a, b] quelle: fehlt"."""
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for e in errors:
        m = _ALERT_PREFIX.match(e)
        ids, msg = ([m.group(1)], m.group(2)) if m else ([], e)
        if msg not in groups:
            groups[msg] = []
            order.append(msg)
        groups[msg] += [i for i in ids if i not in groups[msg]]
    return [f"[{', '.join(groups[m])}] {m}" if groups[m] else m for m in order]


def feed_trigger(trigger: str) -> str:
    """Auslöser-Name für einen reinen Feed-Lauf ("schedule" -> "feeds", sonst "manual-feeds")."""
    if trigger in ("feeds", "manual-feeds"):
        return trigger
    return "feeds" if trigger == "schedule" else "manual-feeds"


def fast_trigger(trigger: str) -> str:
    """Auslöser-Name für einen Schnell-Lauf ("schedule" -> "fast", sonst "manual-fast")."""
    if trigger in FAST_TRIGGERS:
        return trigger
    return "fast" if trigger == "schedule" else "manual-fast"


def fast_presets(app) -> list[str]:
    """Gewählte Deal-Seiten mit eigenem schnellen Takt (``Preset.lane == "fast"``)."""
    return [i for i in app.get("feed_presets") or []
            if i in PRESETS_BY_ID and PRESETS_BY_ID[i].lane == "fast"]


class _SentDeals:
    """Merkt sich, welche Deals ein Lauf gemeldet hat (für die Sofort-Prüfung)."""

    name = "sofort-prüfung"

    def __init__(self) -> None:
        self.items: list = []

    def send(self, alert_name, items) -> None:
        self.items += items


def hot_alerts(sent: list) -> list[str]:
    """Alarme, deren gerade gemeldete Artikel-Deals eine Sofort-Prüfung bei Google wert sind:
    Hot-Marker (``ERROR FARE`` …) oder Hot-Wort im Titel ("Preissturz", "CRAZY HOT") oder Preis
    unter der Grenze des Alarms. Radar zählt nicht."""
    out: list[str] = []
    for deal, _status in sent:
        a, o = deal.alert, deal.offer
        if a.id == radar.RADAR_ALERT_ID or not o.title or a.id in out:
            continue
        if o.hot or radar.hot_reason(o.title) or (
                a.max_price is not None and o.price and 0 < o.price <= a.max_price):
            out.append(a.id)
    return out


def _update_health(store, sources: list, notifiers: list, **kw) -> list[str]:
    try:
        return health.update(store, sources, notifiers, **kw)
    except Exception as e:  # noqa: BLE001 - Gesundheit ist Zugabe, nie den Lauf stoppen
        log.exception("Quellen-Gesundheit nicht aktualisiert")
        return [f"Quellen-Gesundheit nicht aktualisiert: {e}"]


def _rates(ctx: Context, store, mode: str) -> None:
    """Wechselkurse: im vollen Lauf höchstens täglich bei der EZB nachladen (nicht mit
    Test-/Demo-Quellen), sonst die gespeicherten übernehmen. Nie fatal."""
    try:
        if mode == "full" and ctx.source_factory is None:
            currency.refresh_rates(store)
        else:
            currency.load_rates(store)
    except Exception:  # noqa: BLE001
        log.exception("Wechselkurse nicht geladen")


def execute(ctx: Context, alert_id: str | None = None, trigger: str = "manual", *,
            source_names: list[str] | None = None, notifiers: list | None = None,
            mode: str = "full") -> dict:
    """Einen Lauf synchron ausführen und protokollieren. Rückgabe: die ``runs``-Zeile, dazu
    ``hot_alerts`` (Alarme für eine Sofort-Prüfung), wenn es welche gibt.

    ``mode="feeds"``: nur die Deal-Seiten (Feeds) + Radar, kein API-/Google-Scan.
    ``mode="fast"``: nur die schnellen Deal-Seiten (``lane="fast"``) + Radar.
    ``mode="hot"``: nur Google Flights für ``alert_id``, höchstens ``HOT_GOOGLE_REQUESTS``.
    """
    if mode not in MODES:
        raise ValueError(f"mode muss {' oder '.join(MODES)} sein")
    if mode == "feeds":
        trigger = feed_trigger(trigger)
    elif mode == "fast":
        trigger = fast_trigger(trigger)
    elif mode == "hot":
        trigger = HOT_TRIGGER
    store = ctx.store()
    run_id = store.start_run(trigger, alert_id)
    res = RunResult()
    hot: list[str] = []
    try:
        app = ctx.settings(store)
        cfg = ctx.build_config(store, app)
        _rates(ctx, store, mode)
        only = None
        if alert_id:
            only = [alert_id]
            match = [a for a in cfg.alerts if a.id == alert_id]
            if not match:
                res.errors.append(f"Alarm '{alert_id}' nicht gefunden")
            for a in match:
                a.enabled = True   # "Nur diesen prüfen" geht auch bei pausierten Alarmen
        names = source_names
        active = source_names if source_names is not None else app.get("sources")
        if mode in ("feeds", "fast"):
            names = [n for n in active if n in ("feeds", "feed")]
            if not names:
                res.errors.append("Deal-Seiten (Feeds) sind nicht als Quelle aktiviert "
                                  "(Einstellungen → Quellen)")
            elif mode == "fast" and not fast_presets(app):
                res.errors.append("Keine schnelle Deal-Seite ausgewählt "
                                  "(Einstellungen → Quellen → Deal-Seiten)")
        elif mode == "hot":
            names = [n for n in active if n == "google_flights"]
            if not alert_id:
                res.errors.append("Sofort-Prüfung nur für einen Alarm")
            elif not names:
                res.errors.append("Google Flights ist nicht als Quelle aktiviert "
                                  "(Einstellungen → Quellen)")
        sources = []
        if not res.errors:
            sources = ctx.sources(cfg, store, names, app,
                                  lanes={"fast"} if mode == "fast" else None,
                                  google_max=HOT_GOOGLE_REQUESTS if mode == "hot" else None)
        for src in sources:     # Rotation: nur volle Läufe aller Alarme zählen weiter
            if hasattr(src, "advance_rotation"):
                src.advance_rotation = mode == "full" and not alert_id
        if not sources and not res.errors:
            res.errors.append("Keine Deal-Seite ausgewählt (Einstellungen → Quellen → Deal-Seiten)"
                              if mode in ("feeds", "fast") else
                              "Keine Preisquelle aktiviert (Einstellungen → Quellen)")
        if notifiers is None:
            notifiers = ctx.notifiers(store, app)
        if sources and not res.errors:
            verifier = ctx.verifier(sources, app) if mode == "full" else None
            sent = _SentDeals()
            r = run(cfg, sources, [*notifiers, sent], store, only=only, today=ctx.today,
                    radar_settings=app.radar_settings(), verifier=verifier)
            res.add(r)
            if mode in ("feeds", "fast") and "google_flights" in active:
                hot = hot_alerts(sent.items)
            # voller Lauf mit allen gewählten Quellen: Abgewähltes fällt aus der Gesundheit
            res.errors += _update_health(store, sources, notifiers, quick=mode == "hot",
                                         complete=mode == "full" and source_names is None)
    except Exception as e:  # noqa: BLE001 - ein Lauf darf nie den Server/Zeitplan beenden
        log.exception("Lauf fehlgeschlagen")
        res.errors.append(f"Lauf fehlgeschlagen: {e}")
    finally:
        try:
            store.finish_run(run_id, offers=res.offers, deals=res.deals, notified=res.notified,
                             expired=res.expired, errors=merge_errors(res.errors),
                             radar=res.radar, rejected=res.rejected)
            row = next((r for r in store.runs(5) if r["id"] == run_id), None)
        finally:
            store.close()
    row = row or {"id": run_id, "trigger": trigger, "alert_id": alert_id,
                  "offers": res.offers, "deals": res.deals, "notified": res.notified,
                  "expired": res.expired, "errors": res.errors, "radar": res.radar,
                  "rejected": res.rejected}
    if hot:
        row["hot_alerts"] = hot
    return row


class Scheduler:
    def __init__(self, ctx: Context, *, enabled: bool = True) -> None:
        self.ctx = ctx
        self.enabled = enabled
        #: aktuelle Uhrzeit (für Tests austauschbar)
        self.clock = datetime.now
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._workers: list[threading.Thread] = []
        #: Alarme, die auf eine Sofort-Prüfung warten, und wann sie zuletzt eine hatten
        self._hot: list[str] = []
        self._hot_last: dict[str, datetime] = {}
        self._hot_lock = threading.Lock()

    # -- Steuerung ------------------------------------------------------------
    def start(self) -> None:
        if self.enabled and self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="zeitplan", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def trigger(self, alert_id: str | None = None, trigger: str = "manual",
                mode: str = "full") -> bool:
        """Lauf im Hintergrund starten. ``False``, wenn bereits einer läuft.
        ``trigger="hot"`` startet die Sofort-Prüfung des Alarms (nur Google, kleines Budget)."""
        if trigger == HOT_TRIGGER:
            mode = "hot"
        if not self._lock.acquire(blocking=False):
            return False
        t = threading.Thread(target=self._locked_run, args=(alert_id, trigger, mode),
                             name="lauf", daemon=True)
        self._workers = [w for w in self._workers if w.is_alive()] + [t]
        t.start()
        return True

    def run_now(self, alert_id: str | None = None, trigger: str = "manual",
                mode: str = "full") -> dict | None:
        """Lauf synchron ausführen. ``None``, wenn bereits einer läuft."""
        if not self._lock.acquire(blocking=False):
            return None
        return self._locked_run(alert_id, trigger, mode)

    def wait(self, timeout: float = 30) -> bool:
        """Auf laufende manuelle Läufe warten (für Tests/Demo). True = fertig."""
        for w in list(self._workers):
            w.join(timeout)
        return not self.running

    def queue_hot(self, alert_ids: list[str]) -> list[str]:
        """Alarme für eine Sofort-Prüfung vormerken (der Zeitplan startet sie, sobald kein
        Lauf mehr läuft). Nicht doppelt und je Alarm höchstens alle ``HOT_COOLDOWN``."""
        now = self.clock()
        added = []
        with self._hot_lock:
            for a in alert_ids:
                last = self._hot_last.get(a)
                if a in self._hot or (last is not None and now - last < HOT_COOLDOWN):
                    continue
                self._hot.append(a)
                added.append(a)
        if added:
            log.info("Sofort-Prüfung vorgemerkt: %s", ", ".join(added))
        return added

    @property
    def hot_pending(self) -> list[str]:
        with self._hot_lock:
            return list(self._hot)

    def _locked_run(self, alert_id, trigger, mode="full") -> dict | None:
        try:
            row = execute(self.ctx, alert_id, trigger, mode=mode)
            if mode == "full" and alert_id is None:
                with self._hot_lock:       # voller Lauf hat Google für alle Alarme gefragt
                    self._hot.clear()
            elif row and row.get("hot_alerts"):
                self.queue_hot(row["hot_alerts"])
            return row
        except Exception:  # noqa: BLE001
            log.exception("Lauf fehlgeschlagen")
            return None
        finally:
            self._lock.release()

    # -- Zeitplan ---------------------------------------------------------------
    def _intervals(self, store) -> tuple[float, int, bool]:
        """(Stunden voller Lauf, Minuten Feed-Lauf, Feeds aktiv und Deal-Seite gewählt?)"""
        app = self.ctx.settings(store)
        feeds_on = "feeds" in app.get("sources") and bool(app.configured()["feeds"])
        return (float(app.get("interval_hours")), int(app.get("feed_interval_minutes")), feeds_on)

    def _fast(self, store) -> tuple[int, bool]:
        """(Minuten Schnell-Lauf, Feeds aktiv und schnelle Deal-Seite gewählt?)"""
        app = self.ctx.settings(store)
        on = "feeds" in app.get("sources") and bool(fast_presets(app))
        return int(app.get("fast_interval_minutes")), on

    def next_run(self) -> datetime | None:
        """Nächster voller Lauf (Feed-Läufe zählen nicht)."""
        if not self.enabled:
            return None
        with self.ctx.store() as store:
            interval = self._intervals(store)[0]
            last = store.last_run(full_only=True, feed_runs=False)
        if not last:
            return self.clock().replace(microsecond=0)
        started = datetime.fromisoformat(last["started_at"])
        return (started + timedelta(hours=interval)).replace(microsecond=0)

    def next_feed_run(self) -> datetime | None:
        """Nächster Feed-Lauf - gerechnet ab dem letzten Lauf, der alle Feeds gelesen hat
        (voller Lauf oder Feed-Lauf). ``None``, wenn Feeds nicht aktiv sind."""
        if not self.enabled:
            return None
        with self.ctx.store() as store:
            _, minutes, feeds_on = self._intervals(store)
            last = store.last_run(full_only=True, fast_runs=False)
        if not feeds_on:
            return None
        if not last:
            return self.clock().replace(microsecond=0)
        started = datetime.fromisoformat(last["started_at"])
        return (started + timedelta(minutes=minutes)).replace(microsecond=0)

    def next_fast_run(self) -> datetime | None:
        """Nächster Schnell-Lauf - ab dem letzten Lauf, der die schnellen Deal-Seiten gelesen
        hat (jeder Lauf über alle Alarme). ``None`` ohne gewählte schnelle Deal-Seite."""
        if not self.enabled:
            return None
        with self.ctx.store() as store:
            minutes, on = self._fast(store)
            last = store.last_run(full_only=True)
        if not on:
            return None
        if not last:
            return self.clock().replace(microsecond=0)
        started = datetime.fromisoformat(last["started_at"])
        return (started + timedelta(minutes=minutes)).replace(microsecond=0)

    def status(self) -> dict:
        with self.ctx.store() as store:
            last = store.last_run()
            # reine Feed-Läufe (alle 30 min) fragen Travelpayouts/Google nicht ab - deren Fehler
            # stehen im letzten vollen Lauf und dürfen nicht hinter einem Feed-Lauf verschwinden
            last_full = store.last_run(feed_runs=False)
            interval, minutes, _ = self._intervals(store)
            fast_minutes, _ = self._fast(store)
        nxt = self.next_run()
        nxt_feed = self.next_feed_run()
        nxt_fast = self.next_fast_run()
        # Steht der volle Lauf früher an, erübrigt sich der Feed-Lauf davor (ebenso Schnell-Lauf)
        if nxt and nxt_feed and nxt <= nxt_feed:
            nxt_feed = None
        if nxt_fast and any(x and x <= nxt_fast for x in (nxt, nxt_feed)):
            nxt_fast = None
        return {"running": self.running, "last_run": last, "last_full_run": last_full,
                "next_run": nxt.isoformat(timespec="seconds") if nxt else None,
                "next_feed_run": nxt_feed.isoformat(timespec="seconds") if nxt_feed else None,
                "next_fast_run": nxt_fast.isoformat(timespec="seconds") if nxt_fast else None,
                "interval_hours": interval, "feed_interval_minutes": minutes,
                "fast_interval_minutes": fast_minutes, "hot_pending": self.hot_pending}

    def _start(self, alert_id, trigger, mode, message) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        log.info(message)
        self._locked_run(alert_id, trigger, mode)
        return True

    def tick(self) -> float:
        """Einen fälligen Lauf ausführen (höchstens einen). Rückgabe: Sekunden bis zum nächsten
        Blick. Reihenfolge: voller Lauf, Sofort-Prüfung, Feed-Lauf, Schnell-Lauf."""
        now = self.clock()
        nxt = self.next_run()
        if nxt is None or now >= nxt:
            self._start(None, "schedule", "full", "Geplante Prüfung startet")
            return POLL_SECONDS
        wait = min(POLL_SECONDS, (nxt - now).total_seconds())
        with self._hot_lock:
            hot = self._hot[0] if self._hot else None
        if hot is not None:
            if self._lock.acquire(blocking=False):
                with self._hot_lock:
                    if hot in self._hot:
                        self._hot.remove(hot)
                    self._hot_last[hot] = now
                log.info("Sofort-Prüfung für %s startet", hot)
                self._locked_run(hot, HOT_TRIGGER, "hot")
            return 1.0
        lanes = (("feeds", self.next_feed_run(), "Geplanter Feed-Check startet"),
                 ("fast", self.next_fast_run(), "Geplanter Schnell-Check startet"))
        for mode, due, message in lanes:
            if due is None:
                continue
            if now >= due:
                self._start(None, "schedule", mode, message)
                return wait
            wait = min(wait, (due - now).total_seconds())
        return wait

    def _loop(self) -> None:
        log.info("Zeitplan gestartet")
        while not self._stop.is_set():
            wait = POLL_SECONDS
            try:
                wait = self.tick()
            except Exception:  # noqa: BLE001 - Zeitplan muss weiterlaufen
                log.exception("Fehler im Zeitplan")
            self._stop.wait(max(1.0, wait))
