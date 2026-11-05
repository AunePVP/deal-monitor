"""SQLite-Speicher: Preis-Historie, Deals, Alarme, Einstellungen, Meldungen und Läufe.

Jeder Thread (Web-Request, Zeitplan) öffnet seine eigene ``Store``-Instanz;
WAL-Modus und ``busy_timeout`` sorgen dafür, dass parallele Zugriffe warten
statt fehlzuschlagen.
"""

from __future__ import annotations

import hashlib
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

-- günstigstes regelkonformes Angebot je Alarm, Quelle und Strecke - auch über der Preisgrenze
-- ("Bestpreis": zeigt, wo der Markt gerade steht, wenn es keinen Deal gibt)
CREATE TABLE IF NOT EXISTS cheapest (
    alert_id    TEXT NOT NULL,
    source      TEXT NOT NULL,
    route       TEXT NOT NULL,          -- "ZRH-HAM", bei Artikeln "artikel"
    price       REAL NOT NULL,
    payload     TEXT NOT NULL,          -- JSON
    seen_at     TEXT NOT NULL,
    PRIMARY KEY (alert_id, source, route)
);

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

#: alte Quelle der Travel-Dealz-Artikel und ihr heutiger Name (Deal-Seiten-Preset)
LEGACY_FEED_SOURCE = "travel-dealz-feed"
FEED_SOURCE = "feed:travel-dealz"

#: Bestpreise, die so lange in keinem Lauf mehr gesehen wurden, werden nicht mehr angezeigt
CHEAPEST_MAX_AGE = timedelta(days=3)

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
        self._migrate_feed_source()
        self.db.commit()

    def _migrate_feed_source(self) -> None:
        """Artikel-Deals der alten Quelle ``travel-dealz-feed`` (bis Version 0.1) auf
        ``feed:travel-dealz`` umschreiben - samt Fingerprint, sonst käme derselbe Artikel nach
        dem Update erneut als NEU und die alte Zeile liefe nie ab."""
        rows = self.db.execute(
            "SELECT fingerprint, alert_id, link, headline FROM deals "
            "WHERE source=?", (LEGACY_FEED_SOURCE,)).fetchall()
        for fp, alert_id, link, headline in rows:
            offer_fp = hashlib.sha1(
                f"{FEED_SOURCE}|{link}|{headline}".encode()).hexdigest()[:16]
            new_fp = f"{alert_id}:{offer_fp}"
            if self.db.execute("SELECT 1 FROM deals WHERE fingerprint=?", (new_fp,)).fetchone():
                self.db.execute("DELETE FROM deals WHERE fingerprint=?", (fp,))
            else:
                self.db.execute("UPDATE deals SET fingerprint=?, source=? WHERE fingerprint=?",
                                (new_fp, FEED_SOURCE, fp))
            self.db.execute("UPDATE notifications SET fingerprint=? WHERE fingerprint=?",
                            (new_fp, fp))

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
    def _deal_row(self, fingerprint: str):
        return self.db.execute(
            "SELECT status, notified_price, expired_at FROM deals WHERE fingerprint=?",
            (fingerprint,)).fetchone()

    @staticmethod
    def _status(row, price: float, renotify_drop: float, now: datetime | None) -> str:
        """Meldestatus eines Deals gegenüber seiner gespeicherten Zeile (ohne zu schreiben)."""
        if row is None:
            return "new"
        notified = row["notified_price"]
        cheaper = bool(notified) and price <= notified * (1 - renotify_drop)
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
                return "revived"
            return "known"
        if notified is None:
            return "new"          # gespeichert, aber noch nie gemeldet (z. B. aussortiert)
        return "cheaper" if cheaper else "known"

    def deal_status(self, deal: Deal, renotify_drop: float, now: datetime | None = None) -> str:
        """Was ``upsert_deal`` zurückgeben würde - ohne etwas zu speichern."""
        return self._status(self._deal_row(deal.fingerprint), deal.offer.price, renotify_drop, now)

    def upsert_deal(self, deal: Deal, renotify_drop: float, now: datetime | None = None, *,
                    notify: bool = True) -> str:
        """Speichert den Deal. Rückgabe: 'new', 'cheaper', 'revived' oder 'known'.

        ``notify=False``: speichern, ohne ihn als gemeldet zu vermerken (Deal wurde in diesem
        Lauf aussortiert, z. B. über ``max_deals_per_alert``) - Rückgabe dann immer 'known';
        ein späterer Lauf meldet ihn als 'new'.
        """
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
            **o.extras(),       # insight, via_hub, … nur wenn gesetzt (alte Zeilen haben sie nicht)
        }, ensure_ascii=False)
        row = self._deal_row(deal.fingerprint)

        if row is None:
            self.db.execute(
                "INSERT INTO deals (fingerprint, alert_id, rating, headline, price, "
                "notified_price, source, link, payload, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (deal.fingerprint, deal.alert.id, deal.rating, deal.headline, o.price,
                 o.price if notify else None, o.source, o.link, payload, now_s, now_s),
            )
            self.db.commit()
            return "new" if notify else "known"

        if notify:
            result = self._status(row, o.price, renotify_drop, now)
            set_notified, reset_notified = result != "known", False
        else:
            # still: ein wieder aufgetauchter Deal gilt bis zur nächsten Meldung als ungemeldet
            result, set_notified = "known", False
            reset_notified = row["status"] == "expired"
        self.db.execute(
            "UPDATE deals SET status='active', expired_at=NULL, rating=?, headline=?, "
            "price=?, link=?, payload=?, last_seen=?, "
            "notified_price=CASE WHEN ? THEN ? WHEN ? THEN NULL ELSE notified_price END "
            "WHERE fingerprint=?",
            (deal.rating, deal.headline, o.price, o.link, payload, now_s,
             set_notified, o.price, reset_notified, deal.fingerprint),
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

    def expire_stale_articles(self, max_age_days: int, now: datetime | None = None) -> int:
        """Aktive Artikel-Deals (Deal-Seiten, Radar) ablaufen lassen, die seit ``max_age_days``
        in keinem Lauf mehr gesehen wurden - z. B. von einer abgewählten Deal-Seite."""
        now_dt = now or datetime.now()
        cutoff = (now_dt - timedelta(days=max_age_days)).isoformat(timespec="seconds")
        cur = self.db.execute(
            "UPDATE deals SET status='expired', expired_at=? WHERE status='active' "
            "AND last_seen<? AND (source LIKE 'feed:%' OR source LIKE '%-feed')",
            (now_dt.isoformat(timespec="seconds"), cutoff))
        self.db.commit()
        return cur.rowcount

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
        # Reste eines früher gelöschten Alarms mit gleicher ID gehören nicht zum neuen
        self.db.execute("DELETE FROM cheapest WHERE alert_id=?", (data["id"],))
        self.db.commit()

    def update_alert(self, data: dict) -> bool:
        cur = self.db.execute("UPDATE alerts SET data=?, updated_at=? WHERE id=?",
                              (json.dumps(data, ensure_ascii=False), now_iso(), data["id"]))
        self.db.commit()
        return cur.rowcount > 0

    def delete_alert(self, alert_id: str) -> bool:
        """Löscht den Alarm samt seiner Deals und Bestpreise."""
        cur = self.db.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
        self.db.execute("DELETE FROM deals WHERE alert_id=?", (alert_id,))
        self.db.execute("DELETE FROM cheapest WHERE alert_id=?", (alert_id,))
        self.db.commit()
        return cur.rowcount > 0

    # -- Bestpreise (günstigstes Angebot je Alarm, auch ohne Deal) ----------
    def save_cheapest(self, alert_id: str, source: str, entries: dict[str, dict], *,
                      replace: bool, keep_routes: set[str] | None = None,
                      now: datetime | None = None) -> None:
        """Bestpreise einer Quelle für einen Alarm speichern (``entries``: Strecke -> Angaben
        mit ``price``). ``replace``: Die Quelle hat geantwortet - ihre alten Einträge fallen
        weg, außer für ``keep_routes`` ("ZRH-HAM", in diesem Lauf nicht abgefragt); ohne
        ``replace`` werden nur die neuen Strecken überschrieben."""
        now_s = (now or datetime.now()).isoformat(timespec="seconds")
        if replace:
            rows = self.db.execute("SELECT route FROM cheapest WHERE alert_id=? AND source=?",
                                   (alert_id, source)).fetchall()
            for (route,) in rows:
                if not (keep_routes and route.partition("|")[0] in keep_routes):
                    self.db.execute("DELETE FROM cheapest WHERE alert_id=? AND source=? "
                                    "AND route=?", (alert_id, source, route))
        for route, e in entries.items():
            self.db.execute(
                "INSERT OR REPLACE INTO cheapest (alert_id, source, route, price, payload, seen_at) "
                "VALUES (?,?,?,?,?,?)",
                (alert_id, source, route, float(e["price"]),
                 json.dumps(e, ensure_ascii=False), now_s))
        self.db.commit()

    def cheapest(self, alert_id: str | None = None, *, today: date | None = None,
                 now: datetime | None = None,
                 max_age: timedelta | None = None) -> dict[str, list[dict]]:
        """Aktuelle Bestpreise je Alarm (günstigste zuerst) - ohne veraltete Einträge
        (``max_age``, mindestens ``CHEAPEST_MAX_AGE``) und ohne Flüge, deren Abflug schon
        vorbei ist."""
        today = today or date.today()
        age = max(max_age or CHEAPEST_MAX_AGE, CHEAPEST_MAX_AGE)
        cutoff = ((now or datetime.now()) - age).isoformat(timespec="seconds")
        sql, args = "SELECT * FROM cheapest WHERE seen_at >= ?", [cutoff]
        if alert_id:
            sql += " AND alert_id=?"
            args.append(alert_id)
        out: dict[str, list[dict]] = {}
        for r in self.db.execute(sql + " ORDER BY price", args):
            try:
                e = json.loads(r["payload"])
            except ValueError:
                continue
            dep = e.get("departure")
            if dep and dep <= today.isoformat():
                continue
            e.update(source=r["source"], route=r["route"], price=r["price"],
                     seen_at=r["seen_at"])
            out.setdefault(r["alert_id"], []).append(e)
        return out

    def delete_cheapest(self, alert_id: str) -> None:
        self.db.execute("DELETE FROM cheapest WHERE alert_id=?", (alert_id,))
        self.db.commit()

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

    # Zustand einzelner Quellen (Cursor, Zeitstempel, Gesundheit) - eigener Schlüssel je
    # Quelle ``state:<name>``, damit er nie mit Einstellungen kollidiert. Inhalt: JSON-Dict.
    STATE_PREFIX = "state:"

    def source_state(self, name: str) -> dict:
        """Gespeicherter Zustand einer Quelle (leeres Dict, wenn keiner oder unbrauchbar)."""
        value = self.kv_get(self.STATE_PREFIX + name)
        return dict(value) if isinstance(value, dict) else {}

    def set_source_state(self, name: str, state: dict) -> None:
        """Zustand einer Quelle speichern (muss JSON-tauglich sein; Datumswerte als ISO-Text)."""
        if not isinstance(state, dict):
            raise TypeError("Quellen-Zustand muss ein Dict sein")
        self.kv_set(self.STATE_PREFIX + name, state)

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
