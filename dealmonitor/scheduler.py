"""Zeitplan: prüft alle ``interval_hours`` automatisch und startet manuelle Läufe.

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

log = logging.getLogger(__name__)

POLL_SECONDS = 30   # so oft wird geprüft, ob ein Lauf fällig ist (Intervall-Änderungen greifen)


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


def execute(ctx: Context, alert_id: str | None = None, trigger: str = "manual", *,
            source_names: list[str] | None = None, notifiers: list | None = None) -> dict:
    """Einen Lauf synchron ausführen und protokollieren. Rückgabe: die ``runs``-Zeile."""
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
        sources = ctx.sources(cfg, store, source_names, app)
        if not sources:
            res.errors.append("Keine Preisquelle aktiviert (Einstellungen → Preisquelle)")
        if notifiers is None:
            notifiers = ctx.notifiers(store, app)
        if sources and not res.errors:
            r = run(cfg, sources, notifiers, store, only=only, today=ctx.today)
            res.offers, res.deals, res.notified, res.expired = r.offers, r.deals, r.notified, r.expired
            res.errors += r.errors
    except Exception as e:  # noqa: BLE001 - ein Lauf darf nie den Server/Zeitplan beenden
        log.exception("Lauf fehlgeschlagen")
        res.errors.append(f"Lauf fehlgeschlagen: {e}")
    finally:
        try:
            store.finish_run(run_id, offers=res.offers, deals=res.deals, notified=res.notified,
                             expired=res.expired, errors=merge_errors(res.errors))
            row = next((r for r in store.runs(5) if r["id"] == run_id), None)
        finally:
            store.close()
    return row or {"id": run_id, "trigger": trigger, "alert_id": alert_id,
                   "offers": res.offers, "deals": res.deals, "notified": res.notified,
                   "expired": res.expired, "errors": res.errors}


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

    def trigger(self, alert_id: str | None = None, trigger: str = "manual") -> bool:
        """Lauf im Hintergrund starten. ``False``, wenn bereits einer läuft."""
        if not self._lock.acquire(blocking=False):
            return False
        t = threading.Thread(target=self._locked_run, args=(alert_id, trigger),
                             name="lauf", daemon=True)
        self._workers = [w for w in self._workers if w.is_alive()] + [t]
        t.start()
        return True

    def run_now(self, alert_id: str | None = None, trigger: str = "manual") -> dict | None:
        """Lauf synchron ausführen. ``None``, wenn bereits einer läuft."""
        if not self._lock.acquire(blocking=False):
            return None
        return self._locked_run(alert_id, trigger)

    def wait(self, timeout: float = 30) -> bool:
        """Auf laufende manuelle Läufe warten (für Tests/Demo). True = fertig."""
        for w in list(self._workers):
            w.join(timeout)
        return not self.running

    def _locked_run(self, alert_id, trigger) -> dict | None:
        try:
            return execute(self.ctx, alert_id, trigger)
        except Exception:  # noqa: BLE001
            log.exception("Lauf fehlgeschlagen")
            return None
        finally:
            self._lock.release()

    # -- Zeitplan ---------------------------------------------------------------
    def _interval(self, store) -> float:
        return float(self.ctx.settings(store).get("interval_hours"))

    def next_run(self) -> datetime | None:
        if not self.enabled:
            return None
        with self.ctx.store() as store:
            interval = self._interval(store)
            last = store.last_run(full_only=True)
        if not last:
            return datetime.now().replace(microsecond=0)
        started = datetime.fromisoformat(last["started_at"])
        return (started + timedelta(hours=interval)).replace(microsecond=0)

    def status(self) -> dict:
        with self.ctx.store() as store:
            last = store.last_run()
            interval = self._interval(store)
        nxt = self.next_run()
        return {"running": self.running, "last_run": last,
                "next_run": nxt.isoformat(timespec="seconds") if nxt else None,
                "interval_hours": interval}

    def _loop(self) -> None:
        log.info("Zeitplan gestartet")
        while not self._stop.is_set():
            wait = POLL_SECONDS
            try:
                nxt = self.next_run()
                now = datetime.now()
                if nxt is None or now >= nxt:
                    if self._lock.acquire(blocking=False):
                        log.info("Geplante Prüfung startet")
                        self._locked_run(None, "schedule")
                else:
                    wait = min(POLL_SECONDS, (nxt - now).total_seconds())
            except Exception:  # noqa: BLE001 - Zeitplan muss weiterlaufen
                log.exception("Fehler im Zeitplan")
            self._stop.wait(max(1.0, wait))
