"""Wochentage je Alarm (``depart_weekdays``/``return_weekdays``): Planung, Rotation,
Regel-Filter, Bestpreise, Migration und API."""

import logging
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path

from dealmonitor import config as config_mod
from dealmonitor import rotation
from dealmonitor.appsettings import SETTINGS_VERSION, Context, migrate_settings
from dealmonitor.config import normalize_alert
from dealmonitor.models import Alert, Offer, weekday_text
from dealmonitor.monitor import cheapest_entries
from dealmonitor.principles import evaluate, quality_problems

import test_rotation as tr
import test_web as tw

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 8)                # Sonntag
MIDFR = [2, 3, 4]
SASOMO = [5, 6, 0]
ROUTES = [("ZRH", "HAM"), ("HAM", "ZRH")]
WD = {("ZRH", "HAM"): frozenset(MIDFR), ("HAM", "ZRH"): frozenset(SASOMO)}


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def rt(**kw):
    base = dict(id="zrh-ham", name="Zürich → Hamburg", origins=["ZRH"], destinations=["HAM"],
                max_price=110, baseline_price=190, max_stops=1, min_nights=1, max_nights=21,
                depart_weekdays=MIDFR, return_weekdays=SASOMO)
    base.update(kw)
    return Alert(**base)


def ow(**kw):
    return tr.oneway(depart_weekdays=MIDFR, return_weekdays=SASOMO, **kw)


def offer(dep, ret=None, origin="ZRH", dest="HAM", **kw):
    return Offer(source="travelpayouts", origin=origin, destination=dest, price=80, stops=0,
                 departure=dep, return_date=ret, roundtrip=ret is not None, **kw)


class ModelTest(unittest.TestCase):
    def test_defaults_and_directions(self):
        a = Alert(id="x", name="x", origins=["ZRH"], destinations=["HAM"], max_price=1)
        self.assertEqual((a.depart_weekdays, a.return_weekdays, a.has_weekdays), ([], [], False))
        self.assertIsNone(a.weekdays_for("ZRH", "HAM"))
        b = ow()
        self.assertEqual(b.return_weekdays, [0, 5, 6])          # sortiert gespeichert
        self.assertEqual(b.weekdays_for("ZRH", "HAM"), frozenset(MIDFR))
        self.assertEqual(b.weekdays_for("HAM", "ZRH"), frozenset(SASOMO))
        self.assertEqual(b.leg_kind("HAM", "ZRH"), "return")
        self.assertEqual(weekday_text(b.return_weekdays), "Sa/So/Mo")

    def test_invalid(self):
        for bad in ([7], [-1], ["Mo"], [True], 3, [1.5]):
            with self.assertRaises(ValueError):
                rt(depart_weekdays=bad)

    def test_normalize(self):
        raw = normalize_alert({"id": "a", "name": "a", "origins": ["ZRH"], "destinations": ["HAM"],
                               "max_price": 50, "depart_weekdays": ["4", 2, 2]})
        self.assertEqual((raw["depart_weekdays"], raw["return_weekdays"]), ([2, 4], []))
        with self.assertRaisesRegex(ValueError, "Hinflug an"):
            normalize_alert({**raw, "depart_weekdays": [8]})
        with self.assertRaisesRegex(ValueError, "Rückflug an"):
            normalize_alert({**raw, "return_weekdays": "Montag"})

    def test_toml_defaults(self):
        cfg = config_mod.load(ROOT / "config" / "alerts.toml")
        by_id = {a.id: a for a in cfg.alerts}
        for alert_id in ("zrh-ham", "zrh-ham-oneway"):
            self.assertEqual((by_id[alert_id].depart_weekdays, by_id[alert_id].return_weekdays),
                             (MIDFR, [0, 5, 6]))
        self.assertFalse(by_id["ham-zrh"].has_weekdays)
        self.assertTrue(by_id["zrh-ham-oneway"].rotation)
        _, raws = config_mod.load_raw(ROOT / "config" / "alerts.toml")
        self.assertEqual(next(r for r in raws if r["id"] == "zrh-ham")["depart_weekdays"], MIDFR)


class PlanTest(unittest.TestCase):
    def test_roundtrip_dates_on_weekdays(self):
        src = tr.source(today=TODAY)
        plain = src.plan(rt(depart_weekdays=[], return_weekdays=[]))
        plan = src.plan(rt())
        self.assertLessEqual(len(plan), len(plain))
        self.assertGreater(len(plan), 0)
        months = {}
        for o, d, dep, ret in plan:
            self.assertIn(dep.weekday(), MIDFR)
            self.assertIn(ret.weekday(), SASOMO)
            self.assertTrue(1 <= (ret - dep).days <= 21)
            self.assertGreater(dep, TODAY)
            months[(dep.year, dep.month)] = months.get((dep.year, dep.month), 0) + 1
        self.assertLessEqual(max(months.values()), 2)

    def test_scan_dates_on(self):
        self.assertEqual(rotation.scan_dates_on(2027, 1, TODAY, frozenset(MIDFR)),
                         [date(2027, 1, 13), date(2027, 1, 15)])         # Mi + Fr
        self.assertEqual(rotation.scan_dates_on(2027, 1, TODAY, frozenset({4})),
                         [date(2027, 1, 15), date(2027, 1, 22)])         # nur Fr: zwei Wochen

    def test_return_on(self):
        fri = date(2027, 1, 15)
        self.assertEqual(rotation.return_on(fri, 7, frozenset(SASOMO), 1, 21), date(2027, 1, 23))
        self.assertEqual(rotation.return_on(fri, 2, frozenset(SASOMO), 1, 3), date(2027, 1, 17))
        self.assertIsNone(rotation.return_on(fri, 1, frozenset({2}), 1, 3))
        self.assertEqual(rotation.return_on(fri, 7, None), date(2027, 1, 22))

    def test_impossible_stay_skipped(self):
        plan = tr.source(today=TODAY).plan(rt(min_nights=1, max_nights=1, return_weekdays=[1]))
        self.assertEqual(plan, [])


class RotationTest(unittest.TestCase):
    def test_horizon_whole_weeks(self):
        self.assertEqual(rotation.horizon_for(ow(), 6), 42)
        self.assertEqual(rotation.horizon_for(tr.oneway(), 6), 45)

    def test_ring_only_weekdays_and_fixed_length(self):
        sizes = set()
        for k in range(14):
            base, extra = rotation._rings(ROUTES, TODAY + timedelta(days=k), 42, WD)
            sizes.add(len(base))
            for o, d, day in base:
                self.assertIn(day.weekday(), WD[(o, d)])
            self.assertTrue(set(extra) <= set(base))
        self.assertEqual(sizes, {36})
        self.assertEqual(rotation.ring_size(ROUTES, 42, WD), 36)

    def test_every_item_in_cycle_across_midnight(self):
        """Wie test_review3_google.MidnightCoverageTest: jeder passende (Strecke, Tag) ist
        innerhalb eines Durchgangs dran, egal wann er beginnt."""
        for per_run in (3, 5, 23):
            cycle = rotation.cycle_hours(ROUTES, 42, per_run, 3, WD)
            n_runs = int(cycle // 3)
            for day in range(10):
                for hour in (0, 12, 21):
                    start = datetime.combine(TODAY + timedelta(days=day), time(hour, 10))
                    seen, t = set(), start
                    for k in range(n_runs):
                        seen |= set(rotation.slice_for_run(t, ROUTES, 42, per_run, today=t.date(),
                                                           index=day * 9 + hour + k, weekdays=WD))
                        t += timedelta(hours=3)
                    last = t - timedelta(hours=3)
                    wanted = {(o, d, last.date() + timedelta(days=i)) for o, d in ROUTES
                              for i in range(1, 43)
                              if (last.date() + timedelta(days=i)).weekday() in WD[(o, d)]
                              and last.date() + timedelta(days=i) <= start.date() + timedelta(42)}
                    self.assertEqual(wanted - seen, set(), (per_run, start))

    def test_shorter_cycle(self):
        self.assertEqual(rotation.cycle_hours(ROUTES, 45, 6), 45)
        self.assertEqual(rotation.cycle_hours(ROUTES, 42, 6, 3, WD), 24)

    def test_source_full_cycle(self):
        """Wie test_review_runde2.RunCounterTest: volle Läufe decken alle Einträge ab."""
        store = FakeStateStore()
        alert = ow()
        asked = set()
        for k in range(8):
            src = tr.source(state_store=store, now=tr.NOW + timedelta(hours=3 * k), today=tr.TODAY)
            asked |= {(o, d, day) for o, d, day, _ in src.plan(alert)}
            src._save_memory(alert, 23)
        wanted = set(rotation.items(ROUTES, tr.TODAY, 42, weekdays=rotation.route_weekdays(alert)))
        self.assertEqual(asked, wanted)
        self.assertTrue(all(day.weekday() in WD[(o, d)] for o, d, day in asked))

    def test_combine_and_remembered(self):
        mem = {}

        class St:
            def source_state(self, name):
                return mem.get(name, {})

            def set_source_state(self, name, data):
                mem[name] = data

        src = tr.source(state_store=St(), today=TODAY, now=datetime(2026, 11, 8, 12))
        row = {"price": 30.0, "airline": "EW", "stops": 0}
        for i in range(1, 30):
            day = TODAY + timedelta(days=i)
            src.memory.put("ZRH", "HAM", "economy", day, [row], None, src._now())
            src.memory.put("HAM", "ZRH", "economy", day, [row], None, src._now())
        combos = src.combined(rt())
        self.assertTrue(combos)
        for c in combos:
            self.assertIn(c.departure.weekday(), MIDFR)
            self.assertIn(c.return_date.weekday(), SASOMO)
        self.assertGreater(len(src.combined(rt(depart_weekdays=[], return_weekdays=[]))),
                           len(combos))
        remembered = src._remembered(ow(), set())
        self.assertTrue(remembered)
        for o in remembered:
            self.assertIn(o.departure.weekday(), MIDFR if o.origin == "ZRH" else SASOMO)
        self.assertEqual(rotation.combine({date(2027, 1, 12): {"price": 1}},     # Dienstag
                                          {date(2027, 1, 16): {"price": 1}},
                                          depart_weekdays=frozenset(MIDFR)), [])


class FakeStateStore:
    def __init__(self):
        self.data = {}

    def source_state(self, name):
        return self.data.get(name, {})

    def set_source_state(self, name, data):
        self.data[name] = data


class FilterTest(unittest.TestCase):
    TUE = date(2027, 1, 12)
    FRI = date(2027, 1, 15)
    SUN = date(2027, 1, 17)

    def test_roundtrip(self):
        self.assertEqual(quality_problems(rt(), offer(self.FRI, self.SUN), TODAY), [])
        self.assertEqual(quality_problems(rt(), offer(self.TUE, self.SUN), TODAY),
                         ["Abflug Dienstag – nur Mi/Do/Fr gewünscht"])
        self.assertEqual(quality_problems(rt(), offer(self.FRI, self.TUE + timedelta(7)), TODAY),
                         ["Rückflug Dienstag – nur Sa/So/Mo gewünscht"])
        self.assertIsNone(evaluate(rt(), offer(self.TUE, self.SUN), [], today=TODAY))
        self.assertIsNotNone(evaluate(rt(), offer(self.FRI, self.SUN), [], today=TODAY))

    def test_oneway_both_directions(self):
        a = ow()
        self.assertEqual(quality_problems(a, offer(self.FRI), TODAY), [])
        self.assertEqual(quality_problems(a, offer(self.SUN, origin="HAM", dest="ZRH"), TODAY), [])
        self.assertEqual(quality_problems(a, offer(self.FRI, origin="HAM", dest="ZRH"), TODAY),
                         ["Rückflug Freitag – nur Sa/So/Mo gewünscht"])

    def test_articles_and_unknown_dates_untouched(self):
        art = offer(self.TUE, self.SUN, title="Zürich nach Hamburg ab 80 €")
        art.source = "feed:travel-dealz"
        self.assertEqual(quality_problems(rt(), art, TODAY), [])
        self.assertEqual(quality_problems(rt(), offer(None, None), TODAY),
                         ["falscher Reisetyp (one-way/Hin- und Rückflug)"])
        nodate = offer(None, None)
        nodate.roundtrip = True
        self.assertEqual(quality_problems(rt(), nodate, TODAY), [])

    def test_cheapest_respects_weekdays(self):
        good, bad = offer(self.FRI, self.SUN), offer(self.TUE, self.SUN)
        bad.price = 20
        entries = cheapest_entries(rt(), [good, bad], [], set(), TODAY)
        self.assertEqual(entries["travelpayouts"]["ZRH-HAM"]["departure"], "2027-01-15")


class MigrationTest(unittest.TestCase):
    def test_once_only_for_default_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Context(tmp, ROOT / "config" / "alerts.toml", env={})
            ctx.seed()
            with ctx.store() as st:
                self.assertEqual(st.get_alert("zrh-ham")["depart_weekdays"], MIDFR)  # aus TOML
                strip = lambda r: {k: v for k, v in r.items()                     # noqa: E731
                                   if k not in ("depart_weekdays", "return_weekdays")}
                for alert_id in ("zrh-ham", "zrh-ham-oneway", "ham-zrh"):
                    st.update_alert(strip(st.get_alert(alert_id)))
                st.update_alert({**st.get_alert("zrh-ham-oneway"), "rotation": False})
                st.save_cheapest("zrh-ham", "google_flights", {"ZRH-HAM": {"price": 50}},
                                 replace=True)
                st.kv_set("settings_version", 4)
                self.assertEqual(migrate_settings(st),
                                 ["weekdays:zrh-ham", "weekdays:zrh-ham-oneway"])
                for alert_id in ("zrh-ham", "zrh-ham-oneway"):
                    raw = st.get_alert(alert_id)
                    self.assertEqual((raw["depart_weekdays"], raw["return_weekdays"]),
                                     (MIDFR, [0, 5, 6]))
                    Alert(**raw)
                self.assertIs(st.get_alert("zrh-ham-oneway")["rotation"], False)  # unberührt
                self.assertNotIn("depart_weekdays", st.get_alert("ham-zrh"))
                self.assertEqual(st.cheapest("zrh-ham"), {})
                self.assertEqual(st.kv_get("settings_version"), SETTINGS_VERSION)
                # Nutzer ändert danach -> bleibt
                st.update_alert({**st.get_alert("zrh-ham"), "depart_weekdays": [],
                                 "return_weekdays": []})
                self.assertEqual(migrate_settings(st), [])
                self.assertEqual(st.get_alert("zrh-ham")["depart_weekdays"], [])
                # schon gesetzte Felder (auch leer) werden auch bei älterer Version nie überschrieben
                st.kv_set("settings_version", 4)
                self.assertEqual(migrate_settings(st), [])
                self.assertEqual(st.get_alert("zrh-ham")["depart_weekdays"], [])


class ChinaOriginsMigrationTest(unittest.TestCase):
    def test_old_default_origins_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Context(tmp, ROOT / "config" / "alerts.toml", env={})
            ctx.seed()
            new = ["FRA", "HAM", "MUC", "ZRH"]
            with ctx.store() as st:
                self.assertEqual(st.get_alert("de-cn-economy")["origins"], new)   # aus TOML
                # alte Datenbank: Standard-Abflüge von früher, Business vom Nutzer geändert
                st.update_alert({**st.get_alert("de-cn-economy"), "origins": ["@DE"]})
                st.update_alert({**st.get_alert("de-cn-business"), "origins": ["BER"]})
                st.kv_set("settings_version", 5)
                self.assertEqual(migrate_settings(st), ["origins:de-cn-economy"])
                self.assertEqual(st.get_alert("de-cn-economy")["origins"], new)
                self.assertEqual(st.get_alert("de-cn-business")["origins"], ["BER"])
                Alert(**st.get_alert("de-cn-economy"))
                self.assertEqual(migrate_settings(st), [])       # genau einmal


class ApiTest(tw.ServerTest):
    def test_create_update_validate(self):
        body = {"name": "Basel → Berlin", "origins": ["BSL"], "destinations": ["BER"],
                "max_price": 99, "depart_weekdays": [4, 3], "return_weekdays": [6, 0]}
        status, data, _ = self.call("POST", "/api/alerts", body)
        self.assertEqual(status, 201, data)
        a = data["alert"]
        self.assertEqual((a["depart_weekdays"], a["return_weekdays"]), ([3, 4], [0, 6]))
        for bad in ([7], ["Fr"], "4", [None], [2.5]):
            status, data, _ = self.call("POST", "/api/alerts", dict(body, depart_weekdays=bad))
            self.assertEqual(status, 400, bad)
            self.assertIn("Hinflug an", data["error"])
            status, data, _ = self.call("PUT", f"/api/alerts/{a['id']}", {"return_weekdays": bad})
            self.assertEqual(status, 400, bad)
        status, data, _ = self.call("PUT", f"/api/alerts/{a['id']}", {"depart_weekdays": []})
        self.assertEqual((status, data["alert"]["depart_weekdays"]), (200, []))
        self.assertEqual(data["alert"]["return_weekdays"], [0, 6])
        # alter Alarm ohne die Felder: liefert Standard (leer)
        status, data = self.get("/api/alerts")
        old = next(x for x in data["alerts"] if x["id"] == "de-cn")
        self.assertEqual(old.get("depart_weekdays", []), [])


if __name__ == "__main__":
    unittest.main()
