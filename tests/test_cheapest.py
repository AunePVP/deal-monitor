"""Bestpreise: günstigstes regelkonformes Angebot je Alarm/Strecke - auch über der Preisgrenze."""

import json
import logging
import tempfile
import unittest
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from dealmonitor.config import Config, Settings
from dealmonitor.models import Alert, Offer
from dealmonitor.monitor import cheapest_entries, run_alert
from dealmonitor.principles import evaluate
from dealmonitor.sources import SourceError
from dealmonitor.store import CHEAPEST_MAX_AGE, Store
from dealmonitor.web import cheapest_json

TODAY = date(2026, 11, 4)


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def alert(**kw):
    base = dict(id="zrh-ham", name="Zürich → Hamburg", origins=["ZRH"], destinations=["HAM"],
                max_price=110, baseline_price=190, max_stops=1)
    base.update(kw)
    return Alert(**base)


def offer(price=150, source="travelpayouts", day=15, **kw):
    base = dict(source=source, origin="ZRH", destination="HAM", price=price, airline="LX",
                stops=0, departure=date(2027, 1, day), return_date=date(2027, 1, day + 4),
                link=f"https://example.org/{source}/{day}")
    base.update(kw)
    return Offer(**base)


def cfg(alerts):
    return Config(settings=Settings(min_history_samples=100), alerts=alerts, path=Path("x"))


class ListSource:
    records_history = True

    def __init__(self, name, offers, incomplete=None, error=None):
        self.name, self.offers, self.incomplete, self.error = name, offers, incomplete, error

    def fetch(self, a):
        if self.error:
            raise SourceError(self.error)
        return [o for o in self.offers if (o.origin, o.destination) in a.pairs]


class EntriesTest(unittest.TestCase):
    def test_cheapest_fitting_offer_even_over_limit(self):
        a = alert()
        offers = [offer(180, day=10), offer(150, day=12), offer(120, day=14, stops=2),
                  offer(99, day=16, airline="FR", cabin="business")]
        out = cheapest_entries(a, offers, [], set(), TODAY)
        e = out["travelpayouts"]["ZRH-HAM"]
        # 120 € hat 2 Umstiege (max. 1), 99 € ist Business -> beide passen nicht zum Alarm
        self.assertEqual(e["price"], 150)
        self.assertEqual(e["departure"], "2027-01-12")
        self.assertEqual(e["kind"], "cache")
        self.assertFalse(e["confirmed"])

    def test_live_sources_and_confirmed_cache(self):
        a = alert()
        tp = offer(89)
        d = evaluate(a, tp, [], today=TODAY)
        d.reasons.append("Google Flights bestätigt: 92 €")
        out = cheapest_entries(a, [tp], [d], set(), TODAY)
        self.assertTrue(out["travelpayouts"]["ZRH-HAM"]["confirmed"])
        out = cheapest_entries(a, [offer(170, source="google_flights")], [], set(), TODAY)
        e = out["google_flights"]["ZRH-HAM"]
        self.assertEqual((e["kind"], e["confirmed"]), ("live", True))

    def test_rejected_cache_price_is_not_shown(self):
        a = alert()
        tp = offer(60)
        out = cheapest_entries(a, [tp, offer(140, day=20)], [], {f"{a.id}:{tp.fingerprint}"}, TODAY)
        self.assertEqual(out["travelpayouts"]["ZRH-HAM"]["price"], 140)

    def test_articles_and_unsafe_links(self):
        a = alert()
        art = Offer(source="feed:travel-dealz", origin="ZRH", destination="HAM", price=79,
                    title="Swiss: Zürich – Hamburg ab 79€", link="javascript:alert(1)")
        e = cheapest_entries(a, [art], [], set(), TODAY)["feed:travel-dealz"]["artikel"]
        self.assertEqual((e["kind"], e["price"], e["title"]), ("article", 79, art.title))
        self.assertIsNone(e["link"])

    def test_past_departure_and_wrong_trip_are_skipped(self):
        a = alert()
        offers = [offer(50, departure=date(2026, 11, 1), return_date=date(2026, 11, 5)),
                  offer(60, roundtrip=False, return_date=None)]
        self.assertEqual(cheapest_entries(a, offers, [], set(), TODAY), {})


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def entry(self, price, dep="2027-01-15"):
        return {"price": price, "origin": "ZRH", "destination": "HAM", "departure": dep}

    def test_replace_merge_and_age(self):
        s = self.store
        now = datetime(2026, 11, 4, 12)
        s.save_cheapest("a", "google_flights", {"ZRH-HAM": self.entry(170)}, replace=True, now=now)
        s.save_cheapest("a", "travelpayouts", {"ZRH-HAM": self.entry(150)}, replace=True, now=now)
        got = s.cheapest(today=TODAY, now=now)["a"]
        self.assertEqual([(e["source"], e["price"]) for e in got],
                         [("travelpayouts", 150), ("google_flights", 170)])
        # vollständige Antwort ohne passendes Angebot: alter Eintrag dieser Quelle fällt weg
        s.save_cheapest("a", "travelpayouts", {}, replace=True, now=now)
        self.assertEqual([e["source"] for e in s.cheapest(today=TODAY, now=now)["a"]],
                         ["google_flights"])
        # unvollständig: nur neue Strecken überschreiben, andere bleiben
        s.save_cheapest("a", "google_flights", {"ZRH-BSL": self.entry(200)}, replace=False, now=now)
        self.assertEqual(len(s.cheapest(today=TODAY, now=now)["a"]), 2)
        # veraltet
        later = now + CHEAPEST_MAX_AGE + timedelta(minutes=1)
        self.assertEqual(s.cheapest(today=TODAY, now=later), {})

    def test_departed_hidden_and_alert_delete(self):
        s = self.store
        s.insert_alert({"id": "a", "name": "A", "origins": ["ZRH"], "destinations": ["HAM"],
                        "max_price": 100})
        s.save_cheapest("a", "travelpayouts", {"ZRH-HAM": self.entry(90, dep="2026-11-04")},
                        replace=True)
        self.assertEqual(s.cheapest(today=TODAY), {})
        s.save_cheapest("a", "travelpayouts", {"ZRH-HAM": self.entry(90)}, replace=True)
        self.assertIn("a", s.cheapest(today=TODAY))
        s.delete_alert("a")
        self.assertEqual(s.cheapest(today=TODAY), {})


class RunAlertTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def best(self, a):
        return cheapest_json(self.store.cheapest(today=TODAY).get(a.id))

    def test_no_deal_but_cheapest_saved_and_kept_on_error(self):
        a = alert()
        src = ListSource("travelpayouts", [offer(183), offer(169, day=20)])
        items, r = run_alert(a, [src], self.store, cfg([a]), today=TODAY)
        self.assertEqual((items, r.deals), ([], 0))
        self.assertEqual(self.best(a)["best"]["price"], 169)
        # Quelle fällt aus: der letzte bekannte Bestpreis bleibt stehen
        run_alert(a, [ListSource("travelpayouts", [], error="weg")], self.store, cfg([a]),
                  today=TODAY)
        self.assertEqual(self.best(a)["best"]["price"], 169)
        # vollständige Antwort mit neuem (teurerem) Preis ersetzt ihn - "aktuell", nicht "je"
        run_alert(a, [ListSource("travelpayouts", [offer(199)])], self.store, cfg([a]), today=TODAY)
        self.assertEqual(self.best(a)["best"]["price"], 199)

    def test_incomplete_source_merges(self):
        a = alert(origins=["ZRH", "BSL"])
        full = ListSource("google_flights", [offer(170, source="google_flights"),
                                             offer(210, source="google_flights", origin="BSL")])
        run_alert(a, [full], self.store, cfg([a]), today=TODAY)
        part = ListSource("google_flights", [offer(180, source="google_flights")],
                          incomplete={"message": "Limit", "routes": {("BSL", "HAM")}})
        run_alert(a, [part], self.store, cfg([a]), today=TODAY)
        routes = {e["route"]: e["price"] for e in self.best(a)["routes"]}
        self.assertEqual(routes, {"ZRH-HAM": 180, "BSL-HAM": 210})

    def test_summary_live_price_when_best_is_cache(self):
        a = alert()
        run_alert(a, [ListSource("travelpayouts", [offer(140)]),
                      ListSource("google_flights", [offer(175, source="google_flights")])],
                  self.store, cfg([a]), today=TODAY)
        j = self.best(a)
        self.assertEqual((j["best"]["price"], j["best"]["source"]), (140, "travelpayouts"))
        self.assertEqual(j["best_live"]["price"], 175)
        self.assertEqual(j["route_count"], 1)
        self.assertIn("google.com/travel/flights", j["best"]["compare"])
        self.assertEqual(j["best"]["source_label"], "Travelpayouts (Preis-API)")


class WebTest(unittest.TestCase):
    """/api/alerts liefert die Bestpreise; geänderte Regeln löschen sie, Umbenennen nicht."""

    def setUp(self):
        from dealmonitor.appsettings import Context
        from dealmonitor.web import make_server

        self.tmp = tempfile.TemporaryDirectory()
        cfg_path = Path(self.tmp.name) / "alerts.toml"
        cfg_path.write_text('[[alert]]\nid = "zrh-ham"\nname = "ZRH-HAM"\norigins = ["ZRH"]\n'
                            'destinations = ["HAM"]\nmax_price = 110\n')
        from dealmonitor.scheduler import Scheduler
        import threading

        self.ctx = Context(Path(self.tmp.name) / "data", cfg_path, env={},
                           source_factory=lambda cfg, names: [], today=TODAY)
        self.ctx.seed()
        self.scheduler = Scheduler(self.ctx, enabled=False)
        self.server = make_server(self.ctx, self.scheduler, "127.0.0.1", 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        with self.ctx.store() as st:
            st.save_cheapest("zrh-ham", "google_flights",
                             {"ZRH-HAM": {"price": 183.0, "origin": "ZRH", "destination": "HAM",
                                          "departure": "2027-01-15", "return_date": "2027-01-19",
                                          "kind": "live", "confirmed": True}}, replace=True)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())

    def test_alerts_include_cheapest(self):
        a = self.call("GET", "/api/alerts")["alerts"][0]
        self.assertEqual(a["cheapest"]["best"]["price"], 183)
        self.assertIsNone(a["cheapest"]["best_live"])
        a = self.call("PUT", "/api/alerts/zrh-ham", {"name": "Neu", "enabled": False})["alert"]
        self.assertEqual(a["cheapest"]["best"]["price"], 183)
        a = self.call("PUT", "/api/alerts/zrh-ham", {"max_stops": 0})["alert"]
        self.assertIsNone(a["cheapest"])
        self.assertIsNone(self.call("GET", "/api/alerts")["alerts"][0]["cheapest"])


if __name__ == "__main__":
    unittest.main()
