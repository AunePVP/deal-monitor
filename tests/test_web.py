"""Tests für Web-API, Einstellungen, Inbox und Zeitplan (Server läuft in einem Thread)."""

import base64
import json
import logging
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from dealmonitor import web
from dealmonitor.appsettings import AppSettings, Context
from dealmonitor.models import Offer
from dealmonitor.scheduler import Scheduler, execute
from dealmonitor.sources import SourceError
from dealmonitor.store import Store

TODAY = date(2026, 11, 4)
TOML = '''[settings]
scan_months = 4
database = "ignored.sqlite"

[[alert]]
id = "zrh-ham"
name = "Zürich → Hamburg"
origins = ["ZRH"]
destinations = ["HAM"]
max_price = 110
baseline_price = 190

[[alert]]
id = "de-cn"
name = "Deutschland → China"
origins = ["@DE_HUBS"]
destinations = ["SHA"]
max_price = 499
baseline_price = 750
'''


def offer(price=89, origin="ZRH", destination="HAM", day=15):
    return Offer(source="travelpayouts", origin=origin, destination=destination, price=price,
                 airline="LX", stops=0, departure=date(2027, 1, day),
                 return_date=date(2027, 1, day + 4), link=f"https://example.org/{origin}{day}")


def setUpModule():
    logging.disable(logging.CRITICAL)   # erwartete Fehler nicht ins Testprotokoll


def tearDownModule():
    logging.disable(logging.NOTSET)


class ListSource:
    name = "travelpayouts"
    records_history = True

    def __init__(self, offers, gate=None):
        self.offers, self.gate = offers, gate

    def fetch(self, a):
        if self.gate:
            self.gate.wait(10)
        return [o for o in self.offers if (o.origin, o.destination) in a.pairs]


class Broken:
    name = "travelpayouts"
    records_history = True

    def fetch(self, a):
        raise SourceError("offline")


class ServerTest(unittest.TestCase):
    env: dict = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "alerts.toml").write_text(TOML, encoding="utf-8")
        self.offers = [offer(89), offer(450, "FRA", "SHA")]
        self.gate = None
        self.ctx = Context(base / "data", base / "alerts.toml", env=dict(self.env),
                           source_factory=lambda cfg, names: [ListSource(self.offers, self.gate)],
                           today=TODAY)
        self.ctx.seed()
        self.scheduler = Scheduler(self.ctx, enabled=False)
        self.server = web.make_server(self.ctx, self.scheduler, "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.console = mock.patch("dealmonitor.notify.Console.send")
        self.console.start()

    def tearDown(self):
        self.console.stop()
        if self.gate:
            self.gate.set()
        self.scheduler.wait(10)
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json",
                                              **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw, status, hdrs = r.read(), r.status, r.headers
        except urllib.error.HTTPError as e:
            raw, status, hdrs = e.read(), e.code, e.headers
        if "json" in (hdrs.get("Content-Type") or ""):
            return status, json.loads(raw.decode("utf-8")), hdrs
        return status, raw, hdrs

    def get(self, path):
        return self.call("GET", path)[:2]

    def run_and_wait(self, alert_id=None):
        status, data, _ = self.call("POST", "/api/run", {"alert_id": alert_id} if alert_id else {})
        self.assertEqual(status, 202, data)
        self.assertTrue(self.scheduler.wait(10))


class SeedAndAlertsTest(ServerTest):
    def test_seeding_from_toml_once(self):
        status, data = self.get("/api/alerts")
        self.assertEqual(status, 200)
        ids = [a["id"] for a in data["alerts"]]
        self.assertEqual(ids, ["zrh-ham", "de-cn"])
        de = data["alerts"][1]
        self.assertEqual(de["origins"], ["@DE_HUBS"])           # roh gespeichert
        self.assertIn("FRA", de["origins_expanded"])           # aufgelöst ausgeliefert
        self.assertEqual(de["pairs"], 5)
        self.assertEqual(de["active_deals"], 0)
        self.assertIsNone(de["best_price"])
        self.assertEqual(self.get("/api/settings")[1]["values"]["scan_months"], 4)
        # alle löschen -> beim nächsten Start NICHT neu importieren
        for i in ids:
            self.assertEqual(self.call("DELETE", f"/api/alerts/{i}")[0], 200)
        self.assertFalse(self.ctx.seed())
        self.assertEqual(self.get("/api/alerts")[1]["alerts"], [])

    def test_import_config_cli(self):
        from dealmonitor.cli import main
        self.call("DELETE", "/api/alerts/de-cn")
        args = ["-c", str(self.ctx.config_path), "--data", str(self.ctx.data_dir)]
        with mock.patch("builtins.print"):
            self.assertEqual(main(args + ["import-config"]), 0)
        self.assertEqual(len(self.get("/api/alerts")[1]["alerts"]), 2)

    def test_create_update_delete(self):
        body = {"name": "Wien → Peking", "origins": "VIE", "destinations": ["@CN_MAJOR"],
                "max_price": "550", "feed_keywords": "Wien, Peking\nWien, Beijing"}
        status, data, _ = self.call("POST", "/api/alerts", body)
        self.assertEqual(status, 201, data)
        a = data["alert"]
        self.assertEqual(a["id"], "wien-peking")
        self.assertEqual(a["origins"], ["VIE"])
        self.assertEqual(a["max_price"], 550)
        self.assertEqual(a["feed_keywords"], [["Wien", "Peking"], ["Wien", "Beijing"]])
        self.assertEqual(a["pairs"], 6)
        # gleicher Name ohne ID -> eindeutige ID
        status, data, _ = self.call("POST", "/api/alerts", body)
        self.assertEqual((status, data["alert"]["id"]), (201, "wien-peking-2"))
        # doppelte ID
        status, data, _ = self.call("POST", "/api/alerts", dict(body, id="zrh-ham"))
        self.assertEqual(status, 409)
        self.assertIn("existiert bereits", data["error"])
        # Validierungsfehler
        for bad in ({"name": "x", "origins": ["@MARS"], "destinations": ["HAM"], "max_price": 9},
                    {"name": "x", "origins": ["ZRH"], "destinations": ["HAM"]},
                    {"name": "x", "origins": ["ZRH"], "destinations": ["HAM"], "max_price": 9,
                     "cabin": "luxus"},
                    {"name": "x", "origins": ["ZRH"], "destinations": ["HAM"], "max_price": 9,
                     "min_nights": 9, "max_nights": 2},
                    {"name": "", "origins": ["ZRH"], "destinations": ["HAM"], "max_price": 9},
                    {"name": "x", "origins": [], "destinations": ["HAM"], "max_price": 9},
                    {"name": "x", "origins": ["ZRH"], "destinations": ["HAM"], "max_price": 9,
                     "foo": 1}):
            status, data, _ = self.call("POST", "/api/alerts", bad)
            self.assertEqual(status, 400, bad)
            self.assertTrue(data["error"])
        status, data, _ = self.call("POST", "/api/alerts", None)
        self.assertEqual(status, 400)
        # Teil-Update wird gemerged; abgeleitete Felder dürfen mitgeschickt werden
        status, data, _ = self.call("PUT", "/api/alerts/wien-peking",
                                    {"enabled": False, "max_price": 499, "pairs": 99})
        self.assertEqual(status, 200, data)
        self.assertEqual((data["alert"]["enabled"], data["alert"]["max_price"],
                          data["alert"]["destinations"]), (False, 499, ["@CN_MAJOR"]))
        self.assertEqual(self.call("PUT", "/api/alerts/wien-peking", {"id": "neu"})[0], 400)
        self.assertEqual(self.call("PUT", "/api/alerts/wien-peking", {"max_stops": -1})[0], 400)
        self.assertEqual(self.call("PUT", "/api/alerts/gibtsnicht", {"enabled": False})[0], 404)
        self.assertEqual(self.get("/api/status")[1]["counts"]["alerts_enabled"], 3)
        # Löschen
        status, data, _ = self.call("DELETE", "/api/alerts/wien-peking")
        self.assertEqual((status, data), (200, {"deleted": "wien-peking"}))
        self.assertEqual(self.call("DELETE", "/api/alerts/wien-peking")[0], 404)
        ids = [a["id"] for a in self.get("/api/alerts")[1]["alerts"]]
        self.assertEqual(ids, ["zrh-ham", "de-cn", "wien-peking-2"])

    def test_delete_removes_deals(self):
        self.run_and_wait()
        self.assertEqual(len(self.get("/api/deals?alert_id=zrh-ham")[1]["deals"]), 1)
        self.call("DELETE", "/api/alerts/zrh-ham")
        with self.ctx.store() as store:
            self.assertEqual(store.deals(alert_id="zrh-ham"), [])

    def test_regions(self):
        status, data = self.get("/api/regions")
        self.assertEqual(status, 200)
        self.assertIn("FRA", data["regions"]["DE"])
        self.assertEqual(data["airports"]["MUC"], "München")

    def test_unknown_api_and_method(self):
        self.assertEqual(self.get("/api/gibtsnicht")[0], 404)
        self.assertEqual(self.call("DELETE", "/api/status")[0], 405)
        self.assertEqual(self.get("/api/deals?status=kaputt")[0], 400)


class RunTest(ServerTest):
    def test_run_creates_runs_notifications_deals(self):
        status, data = self.get("/api/status")
        self.assertIsNone(data["last_run"])
        self.assertFalse(data["running"])
        self.run_and_wait()
        runs = self.get("/api/runs")[1]["runs"]
        self.assertEqual(len(runs), 1)
        r = runs[0]
        self.assertEqual((r["trigger"], r["offers"], r["deals"], r["notified"], r["errors"]),
                         ("manual", 2, 2, 2, []))
        self.assertTrue(r["finished_at"])
        deals = self.get("/api/deals")[1]["deals"]
        self.assertEqual([d["price"] for d in deals], [89, 450])
        d = deals[0]
        for key in ("fingerprint", "alert_id", "status", "rating", "headline", "price",
                    "notified_price", "source", "link", "first_seen", "last_seen", "expired_at",
                    "origin", "destination", "departure", "return_date", "airline", "stops",
                    "cabin", "roundtrip", "nights", "reference_price", "discount", "reasons",
                    "alert_name", "compare"):
            self.assertIn(key, d)
        self.assertTrue(d["compare"].startswith("https://www.google.com/travel/flights"))
        self.assertEqual(d["departure"], "2027-01-15")
        notes = self.get("/api/notifications")[1]
        self.assertEqual(notes["unread"], 2)
        n = notes["notifications"][0]
        self.assertEqual(set(n), {"id", "created_at", "alert_id", "alert_name", "status", "rating",
                                  "headline", "price", "link", "compare", "read"})
        self.assertFalse(n["read"])
        st = self.get("/api/status")[1]
        self.assertEqual(st["counts"], {"alerts": 2, "alerts_enabled": 2, "active_deals": 2,
                                        "radar_deals": 0, "unread": 2})
        self.assertEqual(st["last_run"]["id"], r["id"])
        self.assertIsNone(st["next_run"])   # Zeitplan aus
        # zweiter Lauf: nichts Neues -> keine neuen Meldungen; dann verschwindet ein Deal
        self.offers = [offer(89)]
        self.run_and_wait()
        self.assertEqual(self.get("/api/notifications")[1]["unread"], 2)
        expired = self.get("/api/deals?status=expired")[1]["deals"]
        self.assertEqual([d["price"] for d in expired], [450])
        self.assertEqual(len(self.get("/api/deals?status=all")[1]["deals"]), 2)

    def test_single_alert_and_conflict(self):
        self.gate = threading.Event()
        self.assertEqual(self.call("POST", "/api/run", {"alert_id": "zrh-ham"})[0], 202)
        status, data, _ = self.call("POST", "/api/run", {})
        self.assertEqual((status, data), (409, {"error": "Es läuft bereits eine Prüfung"}))
        self.assertTrue(self.get("/api/status")[1]["running"])
        self.gate.set()
        self.assertTrue(self.scheduler.wait(10))
        r = self.get("/api/runs?limit=5")[1]["runs"][0]
        self.assertEqual((r["alert_id"], r["offers"]), ("zrh-ham", 1))
        self.assertEqual(self.call("POST", "/api/run", {"alert_id": "nix"})[0], 404)

    def test_errors_are_recorded(self):
        self.ctx.source_factory = lambda cfg, names: [Broken()]
        self.run_and_wait()
        r = self.get("/api/runs")[1]["runs"][0]
        self.assertEqual(r["errors"], ["[zrh-ham, de-cn] travelpayouts: offline"])
        # Absturz im Lauf selbst landet ebenfalls in runs.errors
        self.ctx.source_factory = lambda cfg, names: 1 / 0
        self.run_and_wait()
        r = self.get("/api/runs")[1]["runs"][0]
        self.assertIn("Lauf fehlgeschlagen", r["errors"][0])
        self.assertFalse(self.scheduler.running)

    def test_notifications_read(self):
        self.run_and_wait()
        notes = self.get("/api/notifications")[1]["notifications"]
        status, data, _ = self.call("POST", "/api/notifications/read", {"ids": [notes[0]["id"]]})
        self.assertEqual((status, data), (200, {"unread": 1}))
        self.assertTrue(self.get("/api/notifications")[1]["notifications"][0]["read"])
        self.assertEqual(self.call("POST", "/api/notifications/read", {"ids": "x"})[0], 400)
        self.assertEqual(self.call("POST", "/api/notifications/read")[1], {"unread": 0})

    def test_test_notify(self):
        status, data, _ = self.call("POST", "/api/test-notify")
        self.assertEqual(status, 200)
        names = [c["name"] for c in data["channels"]]
        self.assertEqual(names, ["inbox", "console"])
        self.assertTrue(all(c["ok"] for c in data["channels"]))
        n = self.get("/api/notifications")[1]["notifications"][0]
        self.assertEqual(n["status"], "test")
        self.assertIn("[TEST]", n["headline"])

    def test_feed_article_merged(self):
        class Feed:
            name = "travel-dealz-feed"
            records_history = False

            def fetch(self, a):
                return [Offer(source=self.name, origin="", destination="", price=79,
                              link="https://travel-dealz.de/x/", title="Zürich & China ab 79€")]

        self.ctx.source_factory = lambda cfg, names: [Feed()]
        self.call("PUT", "/api/alerts/de-cn", {"max_price": 100})
        self.run_and_wait()
        deals = self.get("/api/deals")[1]["deals"]
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0]["alert_name"], "Zürich → Hamburg · Deutschland → China")
        self.assertIsNone(deals[0]["compare"])
        self.assertEqual(len(self.get("/api/deals?alert_id=de-cn")[1]["deals"]), 1)


class SettingsTest(ServerTest):
    env = {"WEBHOOK_URL": "https://notify.example/hook", "TELEGRAM_BOT_TOKEN": "env-token-9999"}

    def test_settings(self):
        status, data = self.get("/api/settings")
        self.assertEqual(status, 200)
        self.assertEqual(data["values"]["interval_hours"], 3)
        self.assertEqual(data["values"]["webhook_url"], "https://notify.example/hook")
        self.assertEqual(set(data["env_locked"]), {"webhook_url", "telegram_bot_token"})
        self.assertEqual(data["secrets"]["telegram_bot_token"], {"set": True, "hint": "…9999"})
        self.assertEqual(data["secrets"]["travelpayouts_token"], {"set": False, "hint": None})

        body = {"values": {"interval_hours": "1,5", "sources": ["feed"],   # alter Name -> feeds
                           "webhook_url": "https://anders.example"},
                "secrets": {"travelpayouts_token": "geheimertoken1234",
                            "telegram_bot_token": "ignoriert"}}
        status, data, _ = self.call("PUT", "/api/settings", body)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["values"]["interval_hours"], 1.5)
        self.assertEqual(data["values"]["sources"], ["feeds"])
        self.assertEqual(data["values"]["webhook_url"], "https://notify.example/hook")  # Env gewinnt
        self.assertEqual(data["secrets"]["travelpayouts_token"], {"set": True, "hint": "…1234"})
        self.assertEqual(data["secrets"]["telegram_bot_token"]["hint"], "…9999")
        self.assertNotIn("geheimertoken1234", json.dumps(data))
        self.assertTrue(self.get("/api/status")[1]["configured"]["travelpayouts"])
        self.assertEqual(self.get("/api/status")[1]["interval_hours"], 1.5)

        # fehlend = unverändert, "" = löschen
        data = self.call("PUT", "/api/settings", {"secrets": {}})[1]
        self.assertTrue(data["secrets"]["travelpayouts_token"]["set"])
        data = self.call("PUT", "/api/settings", {"secrets": {"travelpayouts_token": ""}})[1]
        self.assertFalse(data["secrets"]["travelpayouts_token"]["set"])

        for bad in ({"values": {"interval_hours": 0.2}}, {"values": {"interval_hours": "abc"}},
                    {"values": {"sources": ["kayak"]}}, {"values": {"gibtsnicht": 1}},
                    {"values": {"ntfy_server": "ftp://x"}}, {"secrets": {"x": "y"}},
                    {"values": {"scan_months": 2.5}}, {"irgendwas": 1}):
            status, data, _ = self.call("PUT", "/api/settings", bad)
            self.assertEqual(status, 400, bad)
            self.assertTrue(data["error"])
        self.assertEqual(self.get("/api/settings")[1]["values"]["interval_hours"], 1.5)

    def test_notifiers_and_sources_from_settings(self):
        with self.ctx.store() as store:
            app = AppSettings(store, {"NTFY_TOPIC": "deals"})
            app.update({"telegram_chat_id": "42"}, {"telegram_bot_token": "abc"})
            names = [n.name for n in self.ctx.notifiers(store)]
        self.assertEqual(names, ["inbox", "console", "telegram", "webhook"])
        ctx = Context(self.ctx.data_dir, env={"TRAVELPAYOUTS_TOKEN": "t"})
        with ctx.store() as store:
            app = ctx.settings(store)
            app.update({"travelpayouts_marker": "777", "sources": ["travelpayouts", "feed"]})
            cfg = ctx.build_config(store)
            self.assertEqual(cfg.settings.scan_months, 4)
            self.assertEqual(cfg.settings.database, str(ctx.db_path))
            src = ctx.sources(cfg, store)
        self.assertEqual([s.name for s in src], ["travelpayouts", "feeds"])
        self.assertEqual((src[0].token, src[0].marker, src[0].scan_months), ("t", "777", 4))
        # Deal-Seiten: Standard-Presets inkl. Suche, keine eigenen Feeds
        self.assertEqual([p.id for p in src[1].presets],
                         ["travel-dealz", "mydealz-fluege", "fly4free", "premium-flights"])
        self.assertTrue(src[1].search_enabled)
        self.assertEqual(src[1].extra_feeds, [])


class AuthTest(ServerTest):
    env = {"DEALMONITOR_PASSWORD": "sesam"}

    def auth(self, user, pw):
        return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}

    def test_basic_auth(self):
        status, data, hdrs = self.call("GET", "/api/status")
        self.assertEqual(status, 401)
        self.assertIn("Basic", hdrs["WWW-Authenticate"])
        self.assertEqual(self.call("GET", "/", headers=self.auth("x", "falsch"))[0], 401)
        self.assertEqual(self.call("GET", "/api/status", headers=self.auth("egal", "sesam"))[0],
                         200)
        self.assertEqual(self.call("GET", "/", headers=self.auth("", "sesam"))[0], 200)


class StaticTest(ServerTest):
    def test_static_files_and_traversal(self):
        static = Path(self.tmp.name) / "static"
        static.mkdir()
        (static / "index.html").write_text("<h1>Hallo</h1>", encoding="utf-8")
        (static / "app.js").write_text("console.log(1)", encoding="utf-8")
        (Path(self.tmp.name) / "geheim.txt").write_text("nein")
        with mock.patch.object(web, "STATIC_DIR", static):
            status, body, hdrs = self.call("GET", "/")
            self.assertEqual((status, body), (200, "<h1>Hallo</h1>".encode()))
            self.assertTrue(hdrs["Content-Type"].startswith("text/html"))
            status, body, hdrs = self.call("GET", "/app.js")
            self.assertTrue(hdrs["Content-Type"].startswith("text/javascript"))
            self.assertEqual(self.call("GET", "/fehlt.css")[0], 404)
            for evil in ("/../geheim.txt", "/%2e%2e/geheim.txt", "/..%2fgeheim.txt",
                         "/%2e%2e%2f%2e%2e%2fetc/passwd", "/..\\geheim.txt"):
                req = urllib.request.Request(self.base + evil)
                req.full_url = self.base + evil   # nicht normalisieren
                try:
                    with urllib.request.urlopen(req, timeout=5) as r:
                        code, raw = r.status, r.read()
                except urllib.error.HTTPError as e:
                    code, raw = e.code, e.read()
                self.assertEqual(code, 404, evil)
                self.assertNotIn(b"nein", raw)
            self.assertEqual(self.call("POST", "/app.js")[0], 405)
        with mock.patch.object(web, "STATIC_DIR", Path(self.tmp.name) / "gibtsnicht"):
            status, body, _ = self.call("GET", "/")
            self.assertEqual(status, 200)
            self.assertIn("Deal-Monitor".encode(), body)
        _, _, hdrs = self.call("GET", "/api/status")
        self.assertEqual(hdrs["Cache-Control"], "no-store")


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = Context(self.tmp.name, Path(self.tmp.name) / "fehlt.toml", env={},
                           source_factory=lambda cfg, names: [ListSource([])], today=TODAY)

    def tearDown(self):
        self.tmp.cleanup()

    def test_next_run(self):
        s = Scheduler(self.ctx)
        self.assertLessEqual(s.next_run(), datetime.now())          # nie gelaufen -> sofort
        self.assertIsNone(Scheduler(self.ctx, enabled=False).next_run())
        execute(self.ctx, trigger="schedule")
        with self.ctx.store() as store:
            AppSettings(store, {}).update({"interval_hours": 2})
            started = datetime.fromisoformat(store.last_run()["started_at"])
        self.assertEqual(s.next_run(), started + timedelta(hours=2))
        execute(self.ctx, alert_id="x", trigger="manual")          # Einzel-Lauf zählt nicht
        self.assertEqual(s.next_run(), started + timedelta(hours=2))
        self.assertEqual(s.status()["last_run"]["alert_id"], "x")

    def test_loop_runs_when_due_and_survives_errors(self):
        self.ctx.source_factory = lambda cfg, names: 1 / 0
        s = Scheduler(self.ctx)
        s.start()
        try:
            for _ in range(100):
                with self.ctx.store() as store:
                    if store.runs(1) and store.runs(1)[0]["finished_at"]:
                        break
                threading.Event().wait(0.05)
            with self.ctx.store() as store:
                r = store.runs(1)[0]
            self.assertEqual(r["trigger"], "schedule")
            self.assertIn("Lauf fehlgeschlagen", r["errors"][0])
            self.assertTrue(s._thread.is_alive())
        finally:
            s.stop()

    def test_unfinished_runs_marked(self):
        with Store(self.ctx.db_path) as store:
            store.start_run("schedule")
            self.assertEqual(store.abort_unfinished_runs(), 1)
            self.assertTrue(store.runs(1)[0]["errors"])


if __name__ == "__main__":
    unittest.main()
