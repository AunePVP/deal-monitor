"""Google-Datumsrotation (Paket 7): jeden Tag einzeln prüfen, Budget-Anteil, Gedächtnis,
Hin/Rück aus zwei One-ways, Bestpreise und Ablauf über mehrere Läufe."""

import json
import logging
import re
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import unquote

from dealmonitor import config as config_mod
from dealmonitor import rotation
from dealmonitor.appsettings import SETTINGS_VERSION, Context, migrate_settings
from dealmonitor.models import Alert, Offer
from dealmonitor.monitor import cheapest_entries, run_alert
from dealmonitor.principles import evaluate
from dealmonitor.sources.googleflights import GoogleFlightsSource
from dealmonitor.store import Store

ROOT = Path(__file__).resolve().parent.parent
TODAY = date.today()                       # relativ zu heute: Store/Bestpreise nutzen die echte Uhr
NOW = datetime.combine(TODAY, time(12, 0))
ROUTES = [("ZRH", "HAM"), ("HAM", "ZRH")]
Q_RE = re.compile(r"Flights from (\w{3}) to (\w{3}) on (\d{4}-\d\d-\d\d)( through)?")


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def runs(k: int, start: datetime = NOW, hours: float = 3):
    return [start + timedelta(hours=hours * i) for i in range(k)]


def page(rows) -> str:
    """Minimale Google-Seite (One-way): rows = [(preis, airline, umstiege), …]."""
    items = []
    for price, airline, stops in rows:
        via = "Nonstop-Flug mit " + airline if stops == 0 else \
            f"Flug mit {airline} mit einer Zwischenlandung"
        items.append(f'<li><div aria-label="Ab {price} Euro. {via}. Abflug von Flughafen Zürich '
                     f'am Donnerstag, Januar 14, 07:00. Gesamtdauer: 1 Std. 25 Min."></div></li>')
    return "<html><body><ul>" + "".join(items) + "</ul></body></html>"


def prices(table: dict | None = None, default=(120, "SWISS", 0)):
    """Fetcher: Preis je (Strecke, Tag) aus ``table``, sonst ``default``; dazu ein 1-Stopp-Flug.
    Hin/Rück-Abfragen (" through") bekommen 999 € (sollen bei der Rotation nie vorkommen)."""
    table = table or {}
    calls = []

    def fetch(url):
        o, d, day, rt = Q_RE.search(unquote(url)).groups()
        calls.append((o, d, day, bool(rt)))
        if rt:
            return page([(999, "SWISS", 0)])
        main = table.get((o, d, day), default)
        return page([main, (main[0] - 10, "KLM", 1)])

    fetch.calls = calls
    return fetch


def source(fetcher=None, now=NOW, **kw):
    opts = dict(max_requests=40, delay=0, today=TODAY, now=now, seed=1,
                fetcher=fetcher or prices())
    opts.update(kw)
    return GoogleFlightsSource(**opts)


def oneway(**kw):
    base = dict(id="zrh-ham-oneway", name="Zürich ⇄ Hamburg one-way", origins=["ZRH", "HAM"],
                destinations=["HAM", "ZRH"], trip="oneway", max_price=45, baseline_price=95,
                max_stops=0, months_ahead=4, rotation=True)
    base.update(kw)
    return Alert(**base)


def roundtrip(**kw):
    base = dict(id="zrh-ham", name="Zürich → Hamburg", origins=["ZRH"], destinations=["HAM"],
                max_price=110, baseline_price=190, max_stops=1, max_duration_hours=6,
                min_nights=1, max_nights=21)
    base.update(kw)
    return Alert(**base)


def cfg():
    return config_mod.Config(settings=config_mod.Settings(), alerts=[], path=Path("x"))


class SliceTest(unittest.TestCase):
    def test_eight_runs_cover_all_180_items(self):
        ring = set(rotation.items(ROUTES, TODAY, 90))
        self.assertEqual(len(ring), 180)
        seen = set()
        for now in runs(8):
            part = rotation.slice_for_run(now, ROUTES, 90, 23, today=TODAY)
            self.assertLessEqual(len(part), 23)
            self.assertEqual(len(part), len(set(part)), "keine Doppelten in einem Lauf")
            seen |= set(part)
        self.assertEqual(seen, ring)
        self.assertEqual(min(d for _, _, d in seen), TODAY + timedelta(days=1))
        self.assertEqual(max(d for _, _, d in seen), TODAY + timedelta(days=90))

    def test_index_from_epoch_without_state(self):
        a = rotation.slice_for_run(NOW, ROUTES, today=TODAY)
        self.assertEqual(a, rotation.slice_for_run(NOW + timedelta(minutes=59), ROUTES, today=TODAY))
        self.assertNotEqual(a, rotation.slice_for_run(NOW + timedelta(hours=3), ROUTES, today=TODAY))
        # Takt 1 h: der Index zählt stündlich weiter
        self.assertNotEqual(a, rotation.slice_for_run(NOW + timedelta(hours=1), ROUTES,
                                                      today=TODAY, period_hours=1))

    def test_day_change_keeps_places(self):
        """Ein Tageswechsel verschiebt die anderen Tage nicht (Platz nach Kalenderdatum)."""
        a = rotation.items(ROUTES, TODAY, 90)
        b = rotation.items(ROUTES, TODAY + timedelta(days=1), 90)
        day = TODAY + timedelta(days=30)
        self.assertEqual(a.index(("ZRH", "HAM", day)), b.index(("ZRH", "HAM", day)))
        # der neue letzte Tag übernimmt den Platz des vergangenen
        self.assertEqual(a.index(("HAM", "ZRH", TODAY + timedelta(days=1))),
                         b.index(("HAM", "ZRH", TODAY + timedelta(days=91))))

    def test_spare_budget_doubles_near_days_and_weekends(self):
        ring = rotation.items(ROUTES, TODAY, 90, capacity=40 * 8)
        twice = {x for x in ring if ring.count(x) == 2}
        days = {d for _, _, d in twice}
        self.assertIn(TODAY + timedelta(days=14), days)                     # nahe Tage
        self.assertNotIn(TODAY + timedelta(days=15), days)                  # ein Donnerstag
        far = [d for d in days if (d - TODAY).days > 14]
        self.assertTrue(far and all(d.weekday() in (4, 6) for d in far))    # nur Fr/So
        # ohne freies Budget kein Zweitbesuch - Vollständigkeit geht vor
        self.assertEqual(len(rotation.items(ROUTES, TODAY, 90, capacity=6 * 8)), 180)

    def test_small_share_needs_more_runs_but_covers_all(self):
        seen = set()
        for now in runs(30):
            part = rotation.slice_for_run(now, ROUTES, 90, 6, today=TODAY)
            self.assertLessEqual(len(part), 6)
            seen |= set(part)
        self.assertEqual(len(seen), 180)
        self.assertEqual(rotation.cycle_hours(ROUTES, 90, 6), 90)
        self.assertEqual(rotation.slice_for_run(NOW, ROUTES, 90, 0, today=TODAY), [])

    def test_routes_and_horizon(self):
        self.assertEqual(rotation.routes_for(oneway()), ROUTES)
        self.assertEqual(rotation.routes_for(roundtrip(rotation=True)), ROUTES)
        self.assertEqual(rotation.horizon_for(oneway(months_ahead=4), 6), 90)
        self.assertEqual(rotation.horizon_for(oneway(months_ahead=1), 6), 30)


class CombineTest(unittest.TestCase):
    def test_70_and_82_make_152(self):
        out = {date(2027, 1, 14): {"price": 82.0}, date(2027, 1, 20): {"price": 60.0}}
        back = {date(2027, 1, 18): {"price": 70.0}, date(2027, 1, 13): {"price": 10.0},
                date(2027, 3, 1): {"price": 5.0}}
        combos = rotation.combine(out, back, 1, 21)
        self.assertEqual([(a, b, x["price"] + y["price"]) for a, b, x, y in combos],
                         [(date(2027, 1, 14), date(2027, 1, 18), 152.0)])
        self.assertEqual(rotation.combine(out, back, 5, 21), [])      # 4 Nächte zu kurz

    def test_note(self):
        class O:
            legs = [{"price": 82, "airline": "Eurowings"}, {"price": 70, "airline": "SWISS"}]
        self.assertEqual(rotation.note(O()), "kombiniert aus 2 One-way: hin 82 € (Eurowings), "
                                             "zurück 70 € (SWISS)")
        O.legs = [{"airline": "x"}, {}]
        self.assertEqual(rotation.note(O()), rotation.COMBO_LABEL)
        O.legs = None
        self.assertIsNone(rotation.note(O()))


class MemoryTest(unittest.TestCase):
    def test_roundtrip_prune_and_junk(self):
        mem = rotation.Memory()
        rows = [{"price": 82.0, "airline": "Eurowings", "airline_code": "EW", "stops": 0,
                 "duration_min": 85}, {"price": 90.0, "airline": "Eurowings",
                                       "airline_code": "EW", "stops": 0, "duration_min": 85}]
        mem.put("ZRH", "HAM", "economy", date(2027, 1, 14), rows,
                {"lowest": 82, "typical": 105, "history": [[1, 2]] * 60}, NOW)
        mem.put("ZRH", "HAM", "economy", TODAY, rows, None, NOW)          # heute: zu spät
        data = json.loads(json.dumps(mem.dump()))
        again = rotation.Memory(data)
        got = again.get("ZRH", "HAM", "economy", TODAY, NOW)
        self.assertEqual(list(got), [date(2027, 1, 14)])
        self.assertEqual([r["price"] for r in got[date(2027, 1, 14)]["rows"]], [82.0],
                         "je Airline/Umstiege nur die günstigste Zeile")
        self.assertNotIn("history", got[date(2027, 1, 14)]["insight"])
        self.assertEqual(again.get("ZRH", "HAM", "economy", TODAY, NOW + timedelta(hours=49)), {})
        again.prune(TODAY, NOW)
        self.assertEqual(list(again.legs["ZRH-HAM-economy"]), ["2027-01-14"])
        for junk in (None, [], {"legs": []}, {"legs": {"x": 1}, "keep_h": "a"},
                     {"legs": {"ZRH-HAM-economy": {"bad": {"at": "x"}, "2027-01-14": 5,
                                                    "2027-01-15": {"at": NOW.isoformat(),
                                                                   "rows": [[None], "x"]}}}}):
            m = rotation.Memory(junk)
            self.assertEqual(m.get("ZRH", "HAM", "economy", TODAY, NOW), {})
            m.prune(TODAY, NOW)


class SourceTest(unittest.TestCase):
    def test_plan_uses_share_and_does_not_displace_china(self):
        china = Alert(id="de-cn-economy", name="China", origins=["@DE"],
                      destinations=["@CN_MAJOR"], max_price=499, months_ahead=6)
        biz = Alert(id="de-cn-business", name="Business", origins=["@DE_HUBS"],
                    destinations=["@CN_MAJOR"], cabin="business", max_price=1999, months_ahead=6)
        with_rot = source(verify_reserve=10)
        with_rot.begin_run([oneway(), china, biz])
        without = source(verify_reserve=10)
        without.begin_run([oneway(rotation=False), china, biz])
        self.assertEqual(with_rot._shares, without._shares)
        self.assertEqual(with_rot._shares["zrh-ham-oneway"], 10)
        plan = with_rot.plan(oneway())
        self.assertEqual(len(plan), 10)
        self.assertTrue(all(ret is None for *_, ret in plan))
        with_rot.fetch(oneway())
        self.assertEqual(with_rot.requests_made, 10)
        self.assertTrue(all(not rt for *_, rt in with_rot.fetcher.calls), "nur One-way-Abfragen")
        # ohne Planung: höchstens PER_RUN
        self.assertEqual(len(source().plan(oneway())), rotation.PER_RUN)

    def test_budget_logic_still_applies(self):
        src = source(max_requests=5)
        offers = src.fetch(oneway())
        self.assertEqual(src.requests_made, 5)
        self.assertIn("5 von 23 Abfragen (Limit)", src.incomplete["message"])
        self.assertTrue(offers)
        self.assertTrue(all(o.roundtrip is False and o.return_date is None for o in offers))

    def test_rows_filtered_by_alert_rules(self):
        offers = source().fetch(oneway())
        self.assertTrue(offers)
        self.assertEqual({o.stops for o in offers}, {0}, "max_stops=0: 1-Stopp-Flug fällt raus")

    def test_broken_state_store_never_crashes(self):
        class Broken:
            def source_state(self, name):
                raise RuntimeError("kaputt")

            def set_source_state(self, name, state):
                raise RuntimeError("kaputt")

        src = source(state_store=Broken())
        self.assertTrue(src.fetch(oneway()))
        self.assertIsInstance(src.fetch(roundtrip()), list)


class RunTest(unittest.TestCase):
    """Mehrere Läufe mit Store: Gedächtnis, kein Ablauf ungeprüfter Tage, Bestpreise, Kombis."""

    def setUp(self):
        self.store = Store(":memory:")
        cheap_day = TODAY + timedelta(days=10)
        self.cheap = ("ZRH", "HAM", cheap_day.isoformat())
        self.table = {self.cheap: (39, "Eurowings", 0)}

    def tearDown(self):
        self.store.close()

    def run_at(self, now, alert, **kw):
        src = source(prices(self.table), now=now, state_store=self.store, **kw)
        src.begin_run([alert])
        return src, run_alert(alert, [src], self.store, cfg(), today=TODAY)

    def first_run_with_cheap_day(self, alert):
        for now in runs(16):
            src, (items, res) = self.run_at(now, alert)
            if any(d.offer.price == 39 for d, _ in items):
                return now, items, res
        self.fail("billiger Tag nie abgefragt")

    def test_cheap_day_found_kept_and_in_best_prices(self):
        a = oneway()
        now, items, res = self.first_run_with_cheap_day(a)
        self.assertEqual(res.errors, [])
        d, status = next(x for x in items if x[0].offer.price == 39)
        self.assertEqual(status, "new")
        self.assertIn("one-way", d.headline)
        # nächster Lauf fragt andere Tage ab: der Deal bleibt aktiv, nichts neu gemeldet
        src, (items2, res2) = self.run_at(now + timedelta(hours=3), a)
        self.assertNotIn(self.cheap[2], {c[2] for c in src.fetcher.calls})
        self.assertEqual(res2.expired, 0)
        self.assertEqual(items2, [])
        active = self.store.deals(status="active")
        self.assertEqual([x["price"] for x in active], [39])
        best = self.store.cheapest("zrh-ham-oneway")["zrh-ham-oneway"]
        self.assertEqual(best[0]["price"], 39)
        self.assertEqual(best[0]["departure"], self.cheap[2])
        # Preis gestiegen: beim nächsten Besuch des Tages läuft der Deal ab
        self.table[self.cheap] = (80, "Eurowings", 0)
        expired = 0
        for later in runs(16, now + timedelta(hours=6)):
            src, (_, r) = self.run_at(later, a)
            expired += r.expired
            if self.cheap[2] in {c[2] for c in src.fetcher.calls}:
                break
        self.assertEqual(expired, 1)
        self.assertEqual(self.store.deals(status="active"), [])

    def test_roundtrip_combined_from_two_oneways(self):
        out_day = TODAY + timedelta(days=20)
        back_day = out_day + timedelta(days=4)
        self.table = {("ZRH", "HAM", out_day.isoformat()): (45, "Eurowings", 0),
                      ("HAM", "ZRH", back_day.isoformat()): (50, "Eurowings", 0)}
        for now in runs(9):                       # ein Durchgang: alle Tage im Gedächtnis
            self.run_at(now, oneway(), max_requests=40)
        src, (items, res) = self.run_at(NOW + timedelta(hours=27), roundtrip(max_stops=0))
        self.assertEqual(res.errors, [])
        combos = [d for d, _ in items if d.offer.legs]
        self.assertTrue(combos)
        best = min(combos, key=lambda d: d.offer.price)
        self.assertEqual(best.offer.price, 95)
        self.assertEqual((best.offer.departure, best.offer.return_date), (out_day, back_day))
        self.assertTrue(best.offer.roundtrip)
        self.assertEqual(best.reasons[0], "kombiniert aus 2 One-way: hin 45 € (Eurowings), "
                                          "zurück 50 € (Eurowings)")
        self.assertEqual([x["origin"] for x in best.offer.legs], ["ZRH", "HAM"])
        self.assertTrue(all(x["link"].startswith("https://www.google.com/travel/flights")
                            for x in best.offer.legs))
        # Bestpreis der Strecke ist die Kombination (Google Hin/Rück kostet 999 €), gekennzeichnet
        entry = self.store.cheapest("zrh-ham")["zrh-ham"][0]
        self.assertEqual(entry["price"], 95)
        self.assertEqual(len(entry["legs"]), 2)
        self.assertEqual(entry["kind"], "live")

    def test_combo_respects_alert_rules(self):
        a = roundtrip(max_stops=0, exclude_airlines=["EW"])
        src = source()
        src.memory.put("ZRH", "HAM", "economy", date(2027, 1, 14),
                       [{"price": 30.0, "airline": "Eurowings", "airline_code": "EW", "stops": 0,
                         "duration_min": 85},
                        {"price": 82.0, "airline": "SWISS", "airline_code": "LX", "stops": 0,
                         "duration_min": 85}], None, NOW)
        src.memory.put("HAM", "ZRH", "economy", date(2027, 1, 18),
                       [{"price": 20.0, "airline": "KLM", "airline_code": "KL", "stops": 1,
                         "duration_min": 300},
                        {"price": 70.0, "airline": "SWISS", "airline_code": "LX", "stops": 0,
                         "duration_min": 85}], None, NOW)
        offers = src.combined(a)
        self.assertEqual([(o.price, o.airline, o.stops, o.nights) for o in offers],
                         [(152.0, "SWISS", 0, 4)])
        self.assertIsNotNone(evaluate(roundtrip(max_price=200), offers[0], [], today=TODAY))
        self.assertEqual(source().combined(a), [], "leeres Gedächtnis: keine Kombis")
        self.assertEqual(src.combined(oneway()), [])
        o = offers[0]
        real = Offer(source=o.source, origin=o.origin, destination=o.destination, price=o.price,
                     departure=o.departure, return_date=o.return_date, airline=o.airline)
        self.assertNotEqual(o.fingerprint, real.fingerprint, "nie derselbe Deal wie echtes Hin/Rück")
        entries = cheapest_entries(roundtrip(max_price=50), offers, [], set(), TODAY)
        self.assertEqual(entries["google_flights"]["ZRH-HAM"]["legs"][1]["price"], 70.0)


class ConfigTest(unittest.TestCase):
    def test_alerts_toml_and_validation(self):
        alerts = {a.id: a for a in config_mod.load(ROOT / "config" / "alerts.toml").alerts}
        self.assertTrue(alerts["zrh-ham-oneway"].rotation)
        self.assertFalse(alerts["de-cn-economy"].rotation)
        raw = config_mod.normalize_alert({"id": "x", "name": "x", "origins": ["ZRH"],
                                          "destinations": ["HAM"], "max_price": 50})
        self.assertIs(raw["rotation"], False)
        with self.assertRaises(ValueError):
            config_mod.normalize_alert({**raw, "rotation": "ja"})
        with self.assertRaises(ValueError):
            Alert(id="x", name="x", origins=["ZRH"], destinations=["HAM"], max_price=5,
                  rotation=1)

    def test_migration_switches_rotation_on_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Context(tmp, ROOT / "config" / "alerts.toml", env={})
            ctx.seed()
            with ctx.store() as st:
                raw = st.get_alert("zrh-ham-oneway")
                self.assertIs(raw["rotation"], True)       # frisch: aus der TOML
                old = {k: v for k, v in raw.items() if k != "rotation"}
                st.update_alert(old)
                st.update_alert({**st.get_alert("zrh-ham"), "rotation": False})
                st.kv_set("settings_version", 3)
                self.assertIn("rotation:zrh-ham-oneway", migrate_settings(st))
                self.assertIs(st.get_alert("zrh-ham-oneway")["rotation"], True)
                self.assertIs(st.get_alert("zrh-ham")["rotation"], False)
                self.assertEqual(st.kv_get("settings_version"), SETTINGS_VERSION)
                # bewusst ausgeschaltet -> bleibt aus
                st.update_alert({**st.get_alert("zrh-ham-oneway"), "rotation": False})
                st.kv_set("settings_version", 3)
                self.assertEqual(migrate_settings(st), [])
                self.assertIs(st.get_alert("zrh-ham-oneway")["rotation"], False)

    def test_sources_get_store_and_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Context(tmp, ROOT / "config" / "alerts.toml", env={})
            ctx.seed()
            with ctx.store() as st:
                app = ctx.settings(st)
                g = next(s for s in ctx.sources(ctx.build_config(st, app), st,
                                                ["google_flights"], app))
                self.assertIs(g.state_store, st)
                self.assertEqual(g.period_hours, app.get("interval_hours"))


if __name__ == "__main__":
    unittest.main()
