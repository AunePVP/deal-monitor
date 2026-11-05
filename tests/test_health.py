"""Paket 6: Takt (schnelle Deal-Seiten, Sofort-Prüfung) und Quellen-Gesundheit."""

import json
import logging
import tempfile
import threading
import unittest
import urllib.request
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from dealmonitor import health, notify, web
from dealmonitor.appsettings import AppSettings, Context, coerce
from dealmonitor.config import normalize_alert
from dealmonitor.models import Offer
from dealmonitor.scheduler import (HOT_GOOGLE_REQUESTS, Scheduler, execute, fast_trigger,
                                   hot_alerts)
from dealmonitor.sources.feed import PRESETS_BY_ID, FeedSource, source_label
from dealmonitor.store import Store

TODAY = date(2026, 11, 4)
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def stat(items=5, parsed=3, newest=NOW - timedelta(hours=1), error=None):
    return {"items": items, "parsed": parsed, "newest": newest, "error": error}


class FakeFeed:
    """Deal-Seiten-Quelle mit ``stats`` wie ``FeedSource``."""

    name = "feeds"
    records_history = False

    def __init__(self, stats=None, offers=()):
        self.stats = stats or {}
        self.offers = list(offers)

    def fetch(self, alert):
        return [replace(o) for o in self.offers]


class FakeGoogle:
    name = "google_flights"

    def __init__(self, made=10, empty=0, retries=0, down=None):
        self.requests_made, self.empty_fetches, self.retries, self.down = made, empty, retries, down


class Recorder:
    """Kanal mit ``send_text`` (wie ntfy/Webhook)."""

    name = "rec"

    def __init__(self):
        self.texts = []

    def send(self, alert_name, items):
        pass

    def send_text(self, title, text, *, priority=3, tags=()):
        self.texts.append((title, text, priority))


class ClassifyTest(unittest.TestCase):
    def test_states(self):
        r = health.Rule(20)
        self.assertEqual(health.classify(stat(), r, NOW)[0], health.OK)
        self.assertEqual(health.classify(stat(items=0, parsed=0), r, NOW),
                         (health.EMPTY, "0 Artikel geladen"))
        self.assertEqual(health.classify(stat(parsed=0), r, NOW)[0], health.EMPTY)
        self.assertEqual(health.classify(stat(items=0, parsed=0, error="X: 403"), r, NOW),
                         (health.ERROR, "X: 403"))
        # teilweiser Ausfall (eine von mehreren URLs): Artikel da -> nicht FEHLER
        self.assertEqual(health.classify(stat(error="X: 403"), r, NOW)[0], health.OK)
        # newest fehlt -> Alter unbekannt, kein VERALTET
        self.assertEqual(health.classify(stat(newest=None), r, NOW)[0], health.OK)

    def test_td_rest_21h_is_stale(self):
        rule = health.rule_for("feed:travel-dealz-rest")
        state, detail = health.classify(stat(newest=NOW - timedelta(hours=21)), rule, NOW)
        self.assertEqual(state, health.STALE)
        self.assertIn("21 Std.", detail)
        self.assertIn("Grenze 20 Std.", detail)
        ok, _ = health.classify(stat(newest=NOW - timedelta(hours=19)), rule, NOW)
        self.assertEqual(ok, health.OK)

    def test_rules(self):
        self.assertEqual(health.rule_for("feed:secretflying").stale_hours, 10)
        self.assertEqual(health.rule_for("feed:premium-flights-china").stale_hours, 24)
        self.assertEqual(health.rule_for("feed:errorfarealerts").stale_hours, 48)
        self.assertEqual(health.rule_for("feed:reisetopia").stale_hours, 36)
        self.assertEqual(health.rule_for("feed:unbekannt"), health.Rule())
        # Suche ohne Treffer ist normal
        search = health.rule_for("feed:travel-dealz-suche")
        self.assertEqual(health.classify(stat(items=0, parsed=0, newest=None), search, NOW)[0],
                         health.OK)

    def test_google(self):
        def one(**kw):
            return health.collect([FakeGoogle(**kw)], NOW).get("google_flights")
        self.assertEqual(one(made=10, empty=5)["state"], health.OK)        # genau 50 %
        g = one(made=10, empty=6)
        self.assertEqual((g["state"], g["detail"]), (health.EMPTY, "6 von 10 Abrufen leer"))
        # zweite Abrufe zählen nicht als eigene Abfrage: 8 Abfragen, 5 leer -> > 50 %
        self.assertEqual(one(made=10, retries=2, empty=5)["state"], health.EMPTY)
        self.assertEqual(one(made=3, down="gesperrt")["state"], health.ERROR)
        self.assertIsNone(one(made=0))                                     # nichts abgefragt
        # ohne "Abruf leer"-Zähler (ältere Google-Quelle): OK
        old = FakeGoogle()
        del old.empty_fetches
        self.assertEqual(health.collect([old], NOW)["google_flights"]["state"], health.OK)

    def test_garbage_never_crashes(self):
        out = health.collect([FakeFeed({"feed:x": "kaputt", "feed:y": {"newest": "unsinn"}}),
                              object(), FakeFeed(None)], NOW)
        self.assertEqual(list(out), ["feed:y"])
        self.assertEqual(out["feed:y"]["state"], health.EMPTY)

    def test_real_feed_source_stats(self):
        xml = (b"<?xml version='1.0'?><rss><channel><item><title>Fl\xc3\xbcge Z\xc3\xbcrich - "
               b"Hamburg ab 59 Euro</title><link>https://travel-dealz.de/a/</link><pubDate>"
               + NOW.strftime("%a, %d %b %Y %H:%M:%S +0000").encode()
               + b"</pubDate></item></channel></rss>")
        src = FeedSource(presets=["travel-dealz"], search_enabled=False)
        with mock.patch.object(FeedSource, "_fetch_one", lambda self, url: self.parse(xml, url)):
            src.load()
        out = health.collect([src], NOW)
        self.assertEqual(out["feed:travel-dealz"]["state"], health.OK)
        self.assertGreater(out["feed:travel-dealz"]["items"], 0)


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "h.sqlite")
        self.rec = Recorder()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def update(self, stats, now=NOW, sources=None):
        return health.update(self.store, sources or [FakeFeed(stats)], [self.rec], now=now)

    def test_empty_source_reports_once(self):
        night = NOW.replace(hour=2)       # vor 8 Uhr: kein Lebenszeichen dazwischen
        self.update({"feed:secretflying": stat(items=0, parsed=0)}, night)
        self.assertEqual(len(self.rec.texts), 1)
        title, text, prio = self.rec.texts[0]
        self.assertEqual((title, prio), ("Quelle gestört", 3))
        self.assertIn(source_label("feed:secretflying"), text)
        self.assertIn("Leer – 0 Artikel geladen", text)
        self.update({"feed:secretflying": stat(items=0, parsed=0)}, night + timedelta(minutes=10))
        self.assertEqual(len(self.rec.texts), 1, "zweiter Lauf: keine neue Meldung")
        # wieder OK -> eine Entwarnung
        self.update({"feed:secretflying": stat(newest=night)}, night + timedelta(minutes=20))
        self.assertEqual(len(self.rec.texts), 2)
        self.assertEqual(self.rec.texts[1][0], "Quelle wieder OK")
        self.assertEqual(self.rec.texts[1][2], 2)

    def test_first_ok_is_silent_and_since_is_kept(self):
        night = NOW.replace(hour=3)
        self.update({"feed:travel-dealz": stat(newest=night)}, night)
        self.update({"feed:travel-dealz": stat(newest=night)}, night + timedelta(hours=1))
        self.assertEqual(self.rec.texts, [])
        e = self.store.source_state("health")["sources"]["feed:travel-dealz"]
        self.assertEqual(e["state"], "ok")
        self.assertLess(e["since"], e["checked"])

    def test_unread_sources_keep_state(self):
        night = NOW.replace(hour=1)
        self.update({"feed:a": stat(items=0, parsed=0), "feed:b": stat(newest=night)}, night)
        self.update({"feed:b": stat(newest=night)}, night + timedelta(minutes=10))
        known = self.store.source_state("health")["sources"]
        self.assertEqual((known["feed:a"]["state"], known["feed:b"]["state"]), ("leer", "ok"))

    def test_several_changes_one_message(self):
        night = NOW.replace(hour=1)
        self.update({"feed:a": stat(items=0, parsed=0), "feed:b": stat(items=0, error="x: 500")},
                    night)
        self.assertEqual(len(self.rec.texts), 1)
        self.assertEqual(self.rec.texts[0][0], "Quellen: 2 Änderungen")
        self.assertIn("Fehler – x: 500", self.rec.texts[0][1])

    def test_heartbeat_once_a_day(self):
        local_morning = datetime(2026, 11, 5, 9, 0).astimezone()
        self.update({"feed:travel-dealz": stat(items=42, newest=local_morning)}, local_morning)
        beats = [t for t in self.rec.texts if t[0] == "Deal-Monitor läuft"]
        self.assertEqual(len(beats), 1)
        self.assertIn("42 Artikel", beats[0][1])
        self.update({"feed:travel-dealz": stat(newest=local_morning)},
                    local_morning + timedelta(hours=3))
        self.assertEqual(len([t for t in self.rec.texts if t[0] == "Deal-Monitor läuft"]), 1)
        self.update({"feed:travel-dealz": stat(newest=local_morning + timedelta(days=1))},
                    local_morning + timedelta(days=1))
        self.assertEqual(len([t for t in self.rec.texts if t[0] == "Deal-Monitor läuft"]), 2)

    def test_channel_errors_are_returned(self):
        class Broken(Recorder):
            def send_text(self, *a, **kw):
                raise OSError("weg")
        errors = health.update(self.store, [FakeFeed({"feed:a": stat(items=0, parsed=0)})],
                               [Broken(), object()], now=NOW.replace(hour=1))
        self.assertEqual(len(errors), 1)
        self.assertIn("weg", errors[0])

    def test_report(self):
        night = NOW.replace(hour=1)
        self.update({"feed:travel-dealz-rest": stat(newest=night - timedelta(hours=21)),
                     "feed:travel-dealz": stat(newest=night)},
                    night, sources=[FakeFeed({"feed:travel-dealz-rest":
                                              stat(newest=night - timedelta(hours=21)),
                                              "feed:travel-dealz": stat(newest=night)}),
                                    FakeGoogle(made=4, empty=3)])
        rep = health.report(self.store, now=night)
        by_id = {s["id"]: s for s in rep["sources"]}
        self.assertEqual(by_id["feed:travel-dealz-rest"]["state"], "veraltet")
        self.assertEqual(by_id["feed:travel-dealz-rest"]["threshold_hours"], 20)
        self.assertAlmostEqual(by_id["feed:travel-dealz-rest"]["age_hours"], 21, delta=0.1)
        self.assertEqual(by_id["google_flights"]["state"], "leer")
        self.assertEqual(rep["sources"][0]["id"], "google_flights")      # Probleme zuerst
        json.dumps(rep)                                                  # JSON-tauglich


class NotifyTextTest(unittest.TestCase):
    def test_ntfy_webhook_telegram(self):
        with mock.patch("dealmonitor.http.request") as req:
            notify.Ntfy("topic", "https://ntfy.example/").send_text("T", "Zeile", priority=2)
            notify.Webhook("https://hook.example/x", "geheim").send_text("T", "Zeile")
            notify.Telegram("tok", "1").send_text("T", "<b>")
        ntfy, hook, tg = (json.loads(c.kwargs["data"]) for c in req.call_args_list)
        self.assertEqual((ntfy["topic"], ntfy["title"], ntfy["priority"]), ("topic", "T", 2))
        self.assertEqual((hook["kind"], hook["title"], hook["message"]), ("status", "T", "Zeile"))
        self.assertEqual(req.call_args_list[1].kwargs["headers"]["Authorization"], "Bearer geheim")
        self.assertIn("&lt;b&gt;", tg["text"])

    def test_send_status_skips_deal_only_channels(self):
        rec = Recorder()
        self.assertEqual(notify.send_status([notify.Console(), rec], "T", "x"), [])
        self.assertEqual(rec.texts, [("T", "x", 3)])


def feed_offer(price=89, hot=None):
    return Offer(source="feed:travel-dealz", origin="ZRH", destination="HAM", price=price,
                 title=f"Zürich – Hamburg ab {price} €", link=f"https://travel-dealz.de/{price}/",
                 hot=hot)


def fast_travel_dealz():
    """Travel-Dealz (Standard-Preset) testweise als schnelle Deal-Seite."""
    return mock.patch.dict(PRESETS_BY_ID,
                           {"travel-dealz": replace(PRESETS_BY_ID["travel-dealz"], lane="fast")})


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        self.feed = FakeFeed({"feed:travel-dealz": stat(items=0, parsed=0)})

        def factory(c, names):
            self.calls.append(list(names))
            return [self.feed] if "feeds" in names else []

        self.ctx = Context(self.tmp.name, Path(self.tmp.name) / "fehlt.toml", env={},
                           source_factory=factory, today=TODAY)
        with self.ctx.store() as store:
            store.insert_alert(normalize_alert({
                "id": "zrh-ham", "name": "Zürich → Hamburg", "origins": ["ZRH"],
                "destinations": ["HAM"], "max_price": 110}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_setting(self):
        self.assertEqual(coerce("fast_interval_minutes", "5"), 5)
        with self.assertRaises(ValueError):
            coerce("fast_interval_minutes", 4)
        with self.ctx.store() as store:
            self.assertEqual(AppSettings(store, {}).get("fast_interval_minutes"), 10)

    def test_fast_mode(self):
        self.assertEqual([fast_trigger(t) for t in ("schedule", "manual", "fast")],
                         ["fast", "manual-fast", "fast"])
        row = execute(self.ctx, mode="fast")
        self.assertIn("Keine schnelle Deal-Seite", row["errors"][0])
        self.assertEqual(self.calls, [])
        with fast_travel_dealz():
            row = execute(self.ctx, trigger="schedule", mode="fast")
        self.assertEqual((row["trigger"], self.calls[-1]), ("fast", ["feeds"]))
        # Schnell-Läufe verschieben weder den vollen Lauf noch den Feed-Takt
        s = Scheduler(self.ctx)
        self.assertLessEqual(s.next_run(), datetime.now())
        self.assertLessEqual(s.next_feed_run(), datetime.now())

    def test_context_builds_lanes_and_hot_budget(self):
        ctx = Context(self.tmp.name, Path(self.tmp.name) / "fehlt.toml", env={}, today=TODAY)
        with ctx.store() as store:
            app = ctx.settings(store)
            cfg = ctx.build_config(store, app)
            with fast_travel_dealz():
                feed, = ctx.sources(cfg, store, ["feeds"], app, lanes={"fast"})
            self.assertEqual([p.id for p in feed.presets], ["travel-dealz"])
            self.assertFalse(feed.search_enabled)
            google, = ctx.sources(cfg, store, ["google_flights"], app, google_max=5)
            self.assertEqual(google.max_requests, 5)
            full, = ctx.sources(cfg, store, ["google_flights"], app)
            self.assertEqual(full.max_requests, 40)

    def test_health_after_run(self):
        rec = Recorder()
        execute(self.ctx, mode="feeds", notifiers=[rec])
        execute(self.ctx, mode="feeds", notifiers=[rec])
        problems = [t for t in rec.texts if t[0] != "Deal-Monitor läuft"]
        self.assertEqual(len(problems), 1)
        self.assertIn("Leer", problems[0][1])
        with self.ctx.store() as store:
            self.assertEqual(health.report(store)["sources"][0]["state"], "leer")

    def test_health_failure_does_not_stop_run(self):
        with mock.patch("dealmonitor.health.update", side_effect=RuntimeError("kaputt")):
            row = execute(self.ctx, mode="feeds", notifiers=[])
        self.assertEqual(row["errors"], ["Quellen-Gesundheit nicht aktualisiert: kaputt"])

    def test_hot_alerts(self):
        self.feed.offers = [feed_offer(89)]
        row = execute(self.ctx, mode="feeds", notifiers=[])
        self.assertEqual(row["hot_alerts"], ["zrh-ham"])
        # ohne aktives Google keine Sofort-Prüfung
        with self.ctx.store() as store:
            AppSettings(store, {}).update({"sources": ["feeds"]})
        self.feed.offers = [feed_offer(79)]
        self.assertNotIn("hot_alerts", execute(self.ctx, mode="feeds", notifiers=[]))

    def test_hot_rule(self):
        from dealmonitor.models import Alert, Deal
        a = Alert(id="a", name="a", origins=["ZRH"], destinations=["HAM"], max_price=100,
                  baseline_price=300)
        r = Alert(id="radar", name="r", origins=["ZRH"], destinations=["HAM"], max_price=100)

        def deal(al, price, hot=None, title=True):
            o = feed_offer(price, hot)
            if not title:
                o.title = None
            return (Deal(alert=al, offer=o, reference_price=None, discount=0, rating="Deal",
                         headline="x", reasons=[]), "new")
        self.assertEqual(hot_alerts([deal(a, 99)]), ["a"])
        self.assertEqual(hot_alerts([deal(a, 150)]), [])                  # Rabatt-Deal, über Grenze
        self.assertEqual(hot_alerts([deal(a, 150, "ERROR FARE")]), ["a"])
        self.assertEqual(hot_alerts([deal(a, 99, title=False)]), [])      # kein Artikel
        self.assertEqual(hot_alerts([deal(r, 50, "ERROR FARE")]), [])     # Radar ist kein Alarm

    def test_hot_run_uses_google_only(self):
        seen = {}

        def sources(cfg, store, names=None, app=None, *, lanes=None, google_max=None):
            seen.update(names=names, google_max=google_max)
            return []
        with mock.patch.object(self.ctx, "sources", side_effect=sources):
            row = execute(self.ctx, "zrh-ham", "egal", mode="hot")
        self.assertEqual(row["trigger"], "hot")
        self.assertEqual(seen, {"names": ["google_flights"], "google_max": HOT_GOOGLE_REQUESTS})
        self.assertIn("Sofort-Prüfung nur für einen Alarm",
                      execute(self.ctx, mode="hot")["errors"][0])

    def test_queue_tick_and_cooldown(self):
        s = Scheduler(self.ctx)
        now = [datetime(2026, 11, 5, 12, 0)]
        s.clock = lambda: now[0]
        runs = []

        def fake_run(alert_id, trigger, mode="full"):
            runs.append((alert_id, trigger, mode))
            with Store(self.ctx.db_path) as store:
                rid = store.start_run(trigger, alert_id)
                store.finish_run(rid)
                store.db.execute("UPDATE runs SET started_at=? WHERE id=?",
                                 (now[0].isoformat(timespec="seconds"), rid))
                store.db.commit()
            s._lock.release()
        with mock.patch.object(s, "_locked_run", side_effect=fake_run):
            s.tick()                                               # nie gelaufen -> voll
            self.assertEqual(runs[-1], (None, "schedule", "full"))
            self.assertEqual(s.queue_hot(["zrh-ham", "zrh-ham"]), ["zrh-ham"])
            now[0] += timedelta(minutes=1)
            s.tick()
            self.assertEqual(runs[-1], ("zrh-ham", "hot", "hot"))
            self.assertEqual(s.hot_pending, [])
            self.assertEqual(s.queue_hot(["zrh-ham"]), [], "Sperrzeit")
            now[0] += timedelta(minutes=31)
            self.assertEqual(s.queue_hot(["zrh-ham"]), ["zrh-ham"])
        self.assertTrue(s.trigger("zrh-ham", "hot"))
        s.wait(10)
        with self.ctx.store() as store:
            self.assertEqual(store.runs(1)[0]["trigger"], "hot")

    def test_fast_lane_three_times_per_feed_run(self):
        """Fake-Takt: 10 min schnell, 30 min Feeds -> schnelle Seiten 3× je Feed-Lauf."""
        s = Scheduler(self.ctx)
        start = datetime(2026, 11, 5, 2, 0)        # nachts: kein Drosseln
        now = [start]
        s.clock = lambda: now[0]
        modes = []

        def fake_run(alert_id, trigger, mode="full"):
            modes.append(mode)
            name = {"full": "schedule", "feeds": "feeds", "fast": "fast"}[mode]
            with Store(self.ctx.db_path) as store:
                rid = store.start_run(name, alert_id)
                store.finish_run(rid)
                store.db.execute("UPDATE runs SET started_at=? WHERE id=?",
                                 (now[0].isoformat(timespec="seconds"), rid))
                store.db.commit()
            s._lock.release()
        with fast_travel_dealz(), mock.patch.object(s, "_locked_run", side_effect=fake_run):
            for minute in range(0, 61):
                now[0] = start + timedelta(minutes=minute)
                s.tick()
            st = s.status()
        self.assertEqual(modes, ["full", "fast", "fast", "feeds", "fast", "fast", "feeds"])
        fast_loads = sum(1 for m in modes[1:] if m in ("fast", "feeds"))
        self.assertEqual(fast_loads, 3 * modes.count("feeds"))
        self.assertEqual(st["fast_interval_minutes"], 10)
        self.assertEqual(st["next_fast_run"], (start + timedelta(minutes=70)).isoformat())


class ApiTest(unittest.TestCase):
    def test_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Context(Path(tmp) / "data", Path(tmp) / "fehlt.toml", env={},
                          source_factory=lambda c, n: [], today=TODAY)
            with ctx.store() as store:
                health.update(store, [FakeFeed({"feed:travel-dealz": stat(items=0, parsed=0)})],
                              [])
            sched = Scheduler(ctx, enabled=False)
            server = web.make_server(ctx, sched, "127.0.0.1", 0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                with urllib.request.urlopen(base + "/api/sources/health") as r:
                    data = json.load(r)
                with urllib.request.urlopen(base + "/api/status") as r:
                    status = json.load(r)
            finally:
                server.shutdown()
                server.server_close()
        self.assertEqual(data["sources"][0]["id"], "feed:travel-dealz")
        self.assertEqual((data["sources"][0]["state"], data["sources"][0]["label"]),
                         ("leer", "Leer"))
        self.assertIn("fast_interval_minutes", status)


if __name__ == "__main__":
    unittest.main()
