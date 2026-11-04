"""SQLite-Speicher: Preis-Historie, Deals, Alarme, Einstellungen, Meldungen und Läufe.

Jeder Thread (Web-Request, Zeitplan) öffnet seine eigene ``Store``-Instanz;
WAL-Modus und ``busy_timeout`` sorgen dafür, dass parallele Zugriffe warten
statt fehlzuschlagen.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import Deal, Offer

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    route_key   TEXT NOT NULL,
    source      TEXT NOT NULL,
    departure   TEXT,
    return_date TEXT,
    price       REAL NOT NULL,
    observed_on TEXT NOT NULL,
    UNIQUE (route_key, source, departure, return_date, price, observed_on)
);
CREATE INDEX IF NOT EXISTS prices_route ON prices (route_key, observed_on);

CREATE TABLE IF NOT EXISTS deals (
    fingerprint   TEXT PRIMARY KEY,
    alert_id      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',   -- active | expired
    rating        TEXT NOT NULL,
    headline      TEXT NOT NULL,
    price         REAL NOT NULL,
    notified_price REAL,
    source        TEXT NOT NULL,
    link          TEXT,
    payload       TEXT NOT NULL,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    expired_at    TEXT
);
CREATE INDEX IF NOT EXISTS deals_alert ON deals (alert_id, status);

CREATE TABLE IF NOT EXISTS alerts (
    id          TEXT PRIMARY KEY,
    data        TEXT NOT NULL,          -- JSON, Rohfelder wie in der TOML (Regionen unaufgelöst)
    position    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT                          -- JSON
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    alert_id    TEXT,
    alert_name  TEXT,
    status      TEXT,
    rating      TEXT,
    headline    TEXT,
    price       REAL,
    link        TEXT,
    compare     TEXT,
    read        INTEGER NOT NULL DEFAULT 0,
    fingerprint TEXT
);
CREATE INDEX IF NOT EXISTS notifications_read ON notifications (read);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger     TEXT NOT NULL,
    alert_id    TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    offers      INTEGER NOT NULL DEFAULT 0,
    deals       INTEGER NOT NULL DEFAULT 0,
    notified    INTEGER NOT NULL DEFAULT 0,
    expired     INTEGER NOT NULL DEFAULT 0,
    errors      TEXT NOT NULL DEFAULT '[]'   -- JSON-Liste
);
"""


#: Ein abgelaufener Deal, der zum gleichen Preis wieder auftaucht, wird erst nach dieser
#: Zeit erneut gemeldet (sonst "flackern" Deals bei kleinen Preisschwankungen).
REVIVE_AFTER = timedelta(hours=24)
#: später hinzugekommene Zähler der Tabelle ``runs`` (werden per ALTER TABLE nachgerüstet):
#: radar = Radar-Treffer, rejected = bei Google Flights verworfene Deals
RUN_EXTRA_COLUMNS = ("radar", "rejected")
#: Läufe, die nur die Deal-Seiten (Feeds + Radar) prüfen - zählen nicht als volle Prüfung
FEED_TRIGGERS = ("feeds", "manual-feeds")

_INIT_LOCK = threading.Lock()
_READY: set[str] = set()   # Dateien, deren Schema in diesem Prozess schon angelegt ist


def now_iso() -> str:
    """Zeitstempel ISO 8601, lokal, ohne Zeitzone (2026-11-04T16:20:00)."""
    return datetime.now().isoformat(timespec="seconds")


def _restrict(path: str) -> None:
    """Datenbank (enthält Tokens im Klartext) nur für den eigenen Benutzer lesbar machen.

    SQLite legt -wal/-shm mit denselben Rechten wie die Hauptdatei an.
    """
    for suffix in ("", "-wal", "-shm"):
        try:
            os.chmod(path + suffix, 0o600)
        except OSError:
            pass


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=5000")
        try:
            self._init_schema(str(path))
        except BaseException:
            self.db.close()
            raise

    def _init_schema(self, key: str) -> None:
        """WAL + Tabellen einmal pro Prozess und Datei; bei Sperre kurz erneut versuchen."""
        if key != ":memory:" and key in _READY:
            return
        with _INIT_LOCK:
            for attempt in range(20):
                try:
                    if key != ":memory:":
                        mode = self.db.execute("PRAGMA journal_mode").fetchone()[0]
                        if mode.lower() != "wal":
                            self.db.execute("PRAGMA journal_mode=WAL")
                    self.db.executescript(SCHEMA)
                    self._migrate()
                    break
                except sqlite3.OperationalError as e:
                    if "locked" not in str(e) or attempt == 19:
                        raise
                    time.sleep(0.05 * (attempt + 1))
            if key != ":memory:":
                _restrict(key)
                _READY.add(key)

    def _migrate(self) -> None:
        """Spalten nachrüsten, die es in älteren Datenbanken noch nicht gab."""
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(runs)")}
        for col in RUN_EXTRA_COLUMNS:
            if col not in cols:
                self.db.execute(f"ALTER TABLE runs ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
        self.db.commit()

    def close(self) -> None:
        try:
            self.db.commit()
        finally:
            self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- Preis-Historie ---------------------------------------------------
    def add_prices(self, offers: list[Offer], when: datetime | None = None) -> None:
        day = (when or datetime.now()).date().isoformat()
        self.db.executemany(
            "INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?)",
            [(o.route_key, o.source, o.departure and o.departure.isoformat(),
              o.return_date and o.return_date.isoformat(), o.price, day) for o in offers],
        )
        self.db.commit()

    def history(self, route_key: str, days: int, now: datetime | None = None) -> list[float]:
        since = ((now or datetime.now()) - timedelta(days=days)).date().isoformat()
        rows = self.db.execute(
            "SELECT price FROM prices WHERE route_key=? AND observed_on>=?",
            (route_key, since),
        )
        return [r[0] for r in rows]

    # -- Deals ------------------------------------------------------------
    def upsert_deal(self, deal: Deal, renotify_drop: float, now: datetime | None = None) -> str:
        """Speichert den Deal. Rückgabe: 'new', 'cheaper', 'revived' oder 'known'."""
        now_s = (now or datetime.now()).isoformat(timespec="seconds")
        o = deal.offer
        payload = json.dumps({
            "origin": o.origin, "destination": o.destination,
            "departure": o.departure and o.departure.isoformat(),
            "return_date": o.return_date and o.return_date.isoformat(),
            "airline": o.airline, "stops": o.stops, "cabin": o.cabin,
            "roundtrip": o.roundtrip, "nights": o.nights,
            "reference_price": deal.reference_price, "discount": deal.discount,
            "reasons": deal.reasons, "alert_name": deal.alert.name,
        }, ensure_ascii=False)
        row = self.db.execute(
            "SELECT status, notified_price, expired_at FROM deals WHERE fingerprint=?",
            (deal.fingerprint,),
        ).fetchone()

        if row is None:
            self.db.execute(
                "INSERT INTO deals (fingerprint, alert_id, rating, headline, price, "
                "notified_price, source, link, payload, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (deal.fingerprint, deal.alert.id, deal.rating, deal.headline, o.price,
                 o.price, o.source, o.link, payload, now_s, now_s),
            )
            self.db.commit()
            return "new"

        result = "known"
        notified = row["notified_price"]
        cheaper = bool(notified) and o.price <= notified * (1 - renotify_drop)
        if row["status"] == "expired":
            # Nur melden, wenn deutlich billiger oder schon länger weg - sonst still reaktivieren
            gone_since = None
            if row["expired_at"]:
                try:
                    gone_since = datetime.fromisoformat(row["expired_at"])
                except ValueError:
                    pass
            long_gone = gone_since is None or (now or datetime.now()) - gone_since >= REVIVE_AFTER
            if cheaper or long_gone or not notified:
                result = "revived"
        elif cheaper:
            result = "cheaper"
        self.db.execute(
            "UPDATE deals SET status='active', expired_at=NULL, rating=?, headline=?, "
            "price=?, link=?, payload=?, last_seen=?, "
            "notified_price=CASE WHEN ? THEN ? ELSE notified_price END "
            "WHERE fingerprint=?",
            (deal.rating, deal.headline, o.price, o.link, payload, now_s,
             result != "known", o.price, deal.fingerprint),
        )
        self.db.commit()
        return result

    def refresh_deal(self, deal: Deal, now: datetime | None = None) -> bool:
        """Aktiven Deal still aktualisieren (Preis, last_seen), ohne Meldung.

        Für Deals, die weiter alle Kriterien erfüllen, in diesem Lauf aber nicht
        der günstigste ihrer Strecke/ihres Monats sind.
        """
        now_s = (now or datetime.now()).isoformat(timespec="seconds")
        cur = self.db.execute(
            "UPDATE deals SET price=?, link=?, last_seen=? WHERE fingerprint=? AND status='active'",
            (deal.offer.price, deal.offer.link, now_s, deal.fingerprint))
        self.db.commit()
        return cur.rowcount > 0

    def expire_missing(self, alert_id: str, source: str, seen: set[str],
                       now: datetime | None = None,
                       skip_routes: set[tuple[str, str]] | None = None) -> int:
        """Markiert aktive Deals als abgelaufen, die in diesem Lauf fehlten.

        ``skip_routes``: Strecken (origin, destination), die in diesem Lauf nicht
        abgefragt werden konnten - deren Deals bleiben unverändert.
        """
        now_s = (now or datetime.now()).isoformat(timespec="seconds")
        rows = self.db.execute(
            "SELECT fingerprint, payload FROM deals WHERE alert_id=? AND source=? "
            "AND status='active'",
            (alert_id, source),
        ).fetchall()
        gone = []
        for fp, payload in rows:
            if fp in seen:
                continue
            if skip_routes:
                p = json.loads(payload)
                if (p.get("origin"), p.get("destination")) in skip_routes:
                    continue
            gone.append(fp)
        self.db.executemany(
            "UPDATE deals SET status='expired', expired_at=? WHERE fingerprint=?",
            [(now_s, fp) for fp in gone],
        )
        self.db.commit()
        return len(gone)

    def expire_departed(self, today: date, now: datetime | None = None) -> int:
        """Aktive Deals, deren Abflug heute oder früher ist, als abgelaufen markieren.

        Unabhängig von Quelle und Alarm-Status (auch pausierte Alarme).
        """
        now_s = (now or datetime.now()).isoformat(timespec="seconds")
        rows = self.db.execute(
            "SELECT fingerprint, payload FROM deals WHERE status='active'").fetchall()
        gone = []
        for fp, payload in rows:
            dep = json.loads(payload).get("departure")
            if dep and dep <= today.isoformat():
                gone.append(fp)
        self.db.executemany(
            "UPDATE deals SET status='expired', expired_at=? WHERE fingerprint=?",
            [(now_s, fp) for fp in gone])
        self.db.commit()
        return len(gone)

    def deals(self, status: str | None = None, alert_id: str | None = None,
              limit: int = 500) -> list[dict]:
        sql, args = "SELECT * FROM deals WHERE 1=1", []
        if status:
            sql += " AND status=?"
            args.append(status)
        if alert_id:
            sql += " AND alert_id=?"
            args.append(alert_id)
        sql += " ORDER BY status, price LIMIT ?"
        args.append(limit)
        out = []
        for r in self.db.execute(sql, args):
            d = dict(r)
            d.update(json.loads(d.pop("payload")))
            out.append(d)
        return out

    def deal_stats(self) -> dict[str, dict]:
        """Pro Alarm: Anzahl aktiver Deals und günstigster Preis."""
        rows = self.db.execute(
            "SELECT alert_id, COUNT(*) AS n, MIN(price) AS best FROM deals "
            "WHERE status='active' GROUP BY alert_id")
        return {r["alert_id"]: {"active_deals": r["n"], "best_price": r["best"]} for r in rows}

    def delete_deals(self, alert_id: str) -> int:
        cur = self.db.execute("DELETE FROM deals WHERE alert_id=?", (alert_id,))
        self.db.commit()
        return cur.rowcount

    # -- Alarme -----------------------------------------------------------
    def alert_rows(self) -> list[dict]:
        """Rohdaten aller Alarme in Anzeigereihenfolge."""
        rows = self.db.execute("SELECT data FROM alerts ORDER BY position, created_at, id")
        return [json.loads(r[0]) for r in rows]

    def get_alert(self, alert_id: str) -> dict | None:
        row = self.db.execute("SELECT data FROM alerts WHERE id=?", (alert_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def has_alert(self, alert_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM alerts WHERE id=?", (alert_id,)).fetchone() is not None

    def insert_alert(self, data: dict) -> None:
        """Neuer Alarm (ans Ende). KeyError, wenn die ID schon existiert."""
        if self.has_alert(data["id"]):
            raise KeyError(data["id"])
        pos = self.db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM alerts").fetchone()[0]
        now = now_iso()
        self.db.execute("INSERT INTO alerts (id, data, position, created_at, updated_at) "
                        "VALUES (?,?,?,?,?)",
                        (data["id"], json.dumps(data, ensure_ascii=False), pos, now, now))
        self.db.commit()

    def update_alert(self, data: dict) -> bool:
        cur = self.db.execute("UPDATE alerts SET data=?, updated_at=? WHERE id=?",
                              (json.dumps(data, ensure_ascii=False), now_iso(), data["id"]))
        self.db.commit()
        return cur.rowcount > 0

    def delete_alert(self, alert_id: str) -> bool:
        """Löscht den Alarm samt seiner Deals."""
        cur = self.db.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
        self.db.execute("DELETE FROM deals WHERE alert_id=?", (alert_id,))
        self.db.commit()
        return cur.rowcount > 0

    # -- Schlüssel/Wert (Einstellungen, Flags) ----------------------------
    def kv_get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def kv_set(self, key: str, value) -> None:
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value, ensure_ascii=False)))
        self.db.commit()

    def kv_delete(self, key: str) -> None:
        self.db.execute("DELETE FROM kv WHERE key=?", (key,))
        self.db.commit()

    def kv_all(self) -> dict:
        return {r[0]: json.loads(r[1]) for r in self.db.execute("SELECT key, value FROM kv")}

    # -- Meldungen (Inbox) ------------------------------------------------
    def add_notification(self, *, alert_id: str | None, alert_name: str | None, status: str,
                         rating: str | None, headline: str, price: float | None,
                         link: str | None, compare: str | None,
                         fingerprint: str | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO notifications (created_at, alert_id, alert_name, status, rating, "
            "headline, price, link, compare, fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (now_iso(), alert_id, alert_name, status, rating, headline, price, link,
             compare, fingerprint))
        self.db.commit()
        return cur.lastrowid

    def notifications(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, created_at, alert_id, alert_name, status, rating, headline, price, "
            "link, compare, read FROM notifications ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r, read=bool(r["read"])) for r in rows]

    def unread_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM notifications WHERE read=0").fetchone()[0]

    def mark_read(self, ids: list[int] | None = None) -> int:
        if ids is None:
            self.db.execute("UPDATE notifications SET read=1 WHERE read=0")
        else:
            self.db.executemany("UPDATE notifications SET read=1 WHERE id=?",
                                [(int(i),) for i in ids])
        self.db.commit()
        return self.unread_count()

    # -- Läufe ------------------------------------------------------------
    def start_run(self, trigger: str, alert_id: str | None = None) -> int:
        cur = self.db.execute("INSERT INTO runs (trigger, alert_id, started_at) VALUES (?,?,?)",
                              (trigger, alert_id, now_iso()))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, *, offers: int = 0, deals: int = 0, notified: int = 0,
                   expired: int = 0, errors: list[str] | None = None, radar: int = 0,
                   rejected: int = 0) -> None:
        self.db.execute(
            "UPDATE runs SET finished_at=?, offers=?, deals=?, notified=?, expired=?, errors=?, "
            "radar=?, rejected=? WHERE id=?",
            (now_iso(), offers, deals, notified, expired,
             json.dumps(errors or [], ensure_ascii=False), radar, rejected, run_id))
        self.db.commit()

    def abort_unfinished_runs(self) -> int:
        """Läufe ohne Ende (Absturz/Neustart) als abgebrochen markieren."""
        cur = self.db.execute(
            "UPDATE runs SET finished_at=?, errors=? WHERE finished_at IS NULL",
            (now_iso(), json.dumps(["Lauf wurde abgebrochen (Neustart)"], ensure_ascii=False)))
        self.db.commit()
        return cur.rowcount

    @staticmethod
    def _run(row) -> dict:
        d = dict(row)
        d["errors"] = json.loads(d["errors"] or "[]")
        return d

    def runs(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        return [self._run(r) for r in rows]

    def last_run(self, full_only: bool = False, *, feed_runs: bool = True) -> dict | None:
        """Letzter Lauf. ``full_only``: nur Läufe über alle Alarme; ``feed_runs=False``:
        reine Feed-Läufe (``FEED_TRIGGERS``) nicht mitzählen."""
        conds = []
        if full_only:
            conds.append("alert_id IS NULL")
        if not feed_runs:
            conds.append("trigger NOT IN (%s)" % ",".join("?" * len(FEED_TRIGGERS)))
        sql = "SELECT * FROM runs" + (" WHERE " + " AND ".join(conds) if conds else "")
        args = tuple(FEED_TRIGGERS) if not feed_runs else ()
        row = self.db.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
        return self._run(row) if row else None
