"""Zeitplan: prüft automatisch und startet manuelle Läufe.

Zwei Takte:

* **voller Lauf** alle ``interval_hours`` - alle aktiven Quellen (Preis-APIs, Google Flights,
  Deal-Seiten) plus Radar (``trigger="schedule"``),
* **Feed-Lauf** alle ``feed_interval_minutes`` - nur die Deal-Seiten (Feeds) plus Radar
  (``trigger="feeds"``), damit ein Error Fare nicht erst Stunden später auffällt.

Es läuft immer höchstens ein Lauf gleichzeitig (Lock). Jeder Lauf - egal ob
geplant, manuell oder per CLI - landet als Zeile in der Tabelle ``runs``.
Fehler beenden den Zeitplan-Thread nie.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta

from .appsettings import Context
from .monitor import RunResult, run
from .store import FEED_TRIGGERS

log = logging.getLogger(__name__)

POLL_SECONDS = 30   # so oft wird geprüft, ob ein Lauf fällig ist (Intervall-Änderungen greifen)
MODES = ("full", "feeds")


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
    if trigger in FEED_TRIGGERS:
        return trigger
    return "feeds" if trigger == "schedule" else "manual-feeds"


def execute(ctx: Context, alert_id: str | None = None, trigger: str = "manual", *,
            source_names: list[str] | None = None, notifiers: list | None = None,
            mode: str = "full") -> dict:
    """Einen Lauf synchron ausführen und protokollieren. Rückgabe: die ``runs``-Zeile.

    ``mode="feeds"``: nur die Deal-Seiten (Feeds) + Radar, kein API-/Google-Scan.
    """
    if mode not in MODES:
        raise ValueError(f"mode muss {' oder '.join(MODES)} sein")
    if mode == "feeds":
        trigger = feed_trigger(trigger)
    store = ctx.store()
    run_id = store.start_run(trigger, alert_id)
    res = RunResult()
    try:
        app = ctx.settings(store)
        cfg = ctx.build_config(store, app)
        only = None
        if alert_id:
            only = [alert_id]
            match = [a for a in cfg.alerts if a.id == alert_id]
            if not match:
                res.errors.append(f"Alarm '{alert_id}' nicht gefunden")
            for a in match:
                a.enabled = True   # "Nur diesen prüfen" geht auch bei pausierten Alarmen
        names = source_names
        if mode == "feeds":
            active = source_names if source_names is not None else app.get("sources")
            names = [n for n in active if n in ("feeds", "feed")]
            if not names:
                res.errors.append("Deal-Seiten (Feeds) sind nicht als Quelle aktiviert "
                                  "(Einstellungen → Quellen)")
        sources = ctx.sources(cfg, store, names, app) if not res.errors else []
        if not sources and not res.errors:
            res.errors.append("Keine Deal-Seite ausgewählt (Einstellungen → Quellen → Deal-Seiten)"
                              if mode == "feeds" else
                              "Keine Preisquelle aktiviert (Einstellungen → Quellen)")
        if notifiers is None:
            notifiers = ctx.notifiers(store, app)
        if sources and not res.errors:
            verifier = ctx.verifier(sources, app) if mode == "full" else None
            r = run(cfg, sources, notifiers, store, only=only, today=ctx.today,
                    radar_settings=app.radar_settings(), verifier=verifier)
            res.add(r)
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
    return row or {"id": run_id, "trigger": trigger, "alert_id": alert_id,
                   "offers": res.offers, "deals": res.deals, "notified": res.notified,
                   "expired": res.expired, "errors": res.errors, "radar": res.radar,
                   "rejected": res.rejected}


class Scheduler:
    def __init__(self, ctx: Context, *, enabled: bool = True) -> None:
        self.ctx = ctx
        self.enabled = enabled
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._workers: list[threading.Thread] = []

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
        """Lauf im Hintergrund starten. ``False``, wenn bereits einer läuft."""
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

    def _locked_run(self, alert_id, trigger, mode="full") -> dict | None:
        try:
            return execute(self.ctx, alert_id, trigger, mode=mode)
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

    def next_run(self) -> datetime | None:
        """Nächster voller Lauf (Feed-Läufe zählen nicht)."""
        if not self.enabled:
            return None
        with self.ctx.store() as store:
            interval = self._intervals(store)[0]
            last = store.last_run(full_only=True, feed_runs=False)
        if not last:
            return datetime.now().replace(microsecond=0)
        started = datetime.fromisoformat(last["started_at"])
        return (started + timedelta(hours=interval)).replace(microsecond=0)

    def next_feed_run(self) -> datetime | None:
        """Nächster Feed-Lauf - gerechnet ab dem letzten Lauf, der die Feeds gelesen hat
        (voller Lauf oder Feed-Lauf). ``None``, wenn Feeds nicht aktiv sind."""
        if not self.enabled:
            return None
        with self.ctx.store() as store:
            _, minutes, feeds_on = self._intervals(store)
            last = store.last_run(full_only=True)
        if not feeds_on:
            return None
        if not last:
            return datetime.now().replace(microsecond=0)
        started = datetime.fromisoformat(last["started_at"])
        return (started + timedelta(minutes=minutes)).replace(microsecond=0)

    def status(self) -> dict:
        with self.ctx.store() as store:
            last = store.last_run()
            # reine Feed-Läufe (alle 30 min) fragen Travelpayouts/Google nicht ab - deren Fehler
            # stehen im letzten vollen Lauf und dürfen nicht hinter einem Feed-Lauf verschwinden
            last_full = store.last_run(feed_runs=False)
            interval, minutes, _ = self._intervals(store)
        nxt = self.next_run()
        nxt_feed = self.next_feed_run()
        # Steht der volle Lauf früher an, erübrigt sich der Feed-Lauf davor
        if nxt and nxt_feed and nxt <= nxt_feed:
            nxt_feed = None
        return {"running": self.running, "last_run": last, "last_full_run": last_full,
                "next_run": nxt.isoformat(timespec="seconds") if nxt else None,
                "next_feed_run": nxt_feed.isoformat(timespec="seconds") if nxt_feed else None,
                "interval_hours": interval, "feed_interval_minutes": minutes}

    def _loop(self) -> None:
        log.info("Zeitplan gestartet")
        while not self._stop.is_set():
            wait = POLL_SECONDS
            try:
                now = datetime.now()
                nxt = self.next_run()
                if nxt is None or now >= nxt:
                    if self._lock.acquire(blocking=False):
                        log.info("Geplante Prüfung startet")
                        self._locked_run(None, "schedule")
                else:
                    wait = min(POLL_SECONDS, (nxt - now).total_seconds())
                    nxt_feed = self.next_feed_run()
                    if nxt_feed is not None and now >= nxt_feed:
                        if self._lock.acquire(blocking=False):
                            log.info("Geplanter Feed-Check startet")
                            self._locked_run(None, "schedule", "feeds")
                    elif nxt_feed is not None:
                        wait = min(wait, (nxt_feed - now).total_seconds())
            except Exception:  # noqa: BLE001 - Zeitplan muss weiterlaufen
                log.exception("Fehler im Zeitplan")
            self._stop.wait(max(1.0, wait))
