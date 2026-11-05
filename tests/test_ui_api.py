"""Paket 9: Felder für die Oberfläche (Preisniveau, Quellen, Nachbar-Hub, Beinahe-Deals,
Google-Rotation, Quellen-Gesundheit) - über die echte API mit den Demo-Daten."""

import json
import logging
import threading
import unittest
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from dealmonitor import demo, health, rotation, web
from dealmonitor.models import Alert
from dealmonitor.scheduler import Scheduler
from dealmonitor.store import Store

CONFIG = Path(__file__).resolve().parent.parent / "config" / "alerts.toml"


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


class DemoApiTest(unittest.TestCase):
    """Ein Demo-Lauf wie ``serve --demo``, dann die API abfragen (kein Netz)."""

    @classmethod
    def setUpClass(cls):
        cls.console = mock.patch("dealmonitor.notify.Console.send")
        cls.console.start()
        cls.ctx = demo.server_context(str(CONFIG))
        cls.ctx.seed()
        cls.scheduler = Scheduler(cls.ctx, enabled=False)
        demo.prepare_server(cls.ctx, cls.scheduler)
        cls.server = web.make_server(cls.ctx, cls.scheduler, "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.deals = cls.get("/api/deals")["deals"]
        cls.alerts = {a["id"]: a for a in cls.get("/api/alerts")["alerts"]}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.console.stop()

    @classmethod
    def get(cls, path):
        with urllib.request.urlopen(cls.base + path, timeout=10) as r:
            return json.loads(r.read())

    def deal(self, alert_id, price):
        return next(d for d in self.deals if d["alert_id"] == alert_id and d["price"] == price)

    def test_insight_per_deal(self):
        d = self.deal("zrh-ham", 92)                   # bei Google bestätigt, mit Preisniveau
        ins = d["insight"]
        self.assertEqual((ins["level"], ins["typical_low"], ins["typical_high"], ins["saving"]),
                         ("niedrig", 160, 230, 97))
        self.assertEqual(len(ins["history"]), 62)
        self.assertEqual(ins["history"][-1][1], 92)
        self.assertIn("43% unter typischer Spanne (160 €–230 €)", d["reasons"])
        ow = self.deal("zrh-ham-oneway", 39)           # Tagesrotation, echte Seite mit 39 €
        self.assertEqual((ow["insight"]["level"], ow["insight"]["typical_low"]), ("niedrig", 90))

    def test_sources_and_lead_per_deal(self):
        d = self.deal("de-cn-business", 1749)
        self.assertEqual([s["label"] for s in d["sources"]],
                         ["Secret Flying", "Travel-Dealz-Artikel", "ErrorFareAlerts"])
        self.assertTrue(all(s["link"].startswith("https://") for s in d["sources"]))
        self.assertEqual(d["lead"], "Erstmeldung Secret Flying, 7 h vor Travel-Dealz-Artikel "
                                    "(insgesamt 3 Quellen)")
        eco = self.deal("de-cn-economy", 429)
        self.assertEqual(len(eco["sources"]), 2)
        self.assertTrue(eco["lead"].startswith("Erstmeldung Travel-Dealz-Artikel, 19 h vor"))

    def test_hub_and_expires(self):
        d = self.deal("de-cn-business", 1670)
        self.assertEqual((d["via_hub"], d["hub_name"], d["surcharge"]), ("BUD", "Budapest", 180))
        expires = datetime.fromisoformat(d["expires"])
        self.assertGreater(expires, datetime.now(timezone.utc) + timedelta(days=5))

    def test_near_deals_and_rotation(self):
        near = self.alerts["de-cn-economy"]["near_deals"]
        self.assertEqual(len(near), 1)
        self.assertEqual((near[0]["price"], near[0]["near"]),
                         (559, ["60 € über deiner Grenze", "7% unter typischer Spanne (600 €–1.050 €)"]))
        self.assertEqual(len(near[0]["insight"]["history"]), 62)
        cover = self.alerts["zrh-ham-oneway"]["rotation_cover"]
        self.assertEqual((cover["horizon_days"], cover["no_deal_sites"]), (90, True))
        self.assertGreater(cover["days"], 0)
        self.assertTrue(self.alerts["zrh-ham"]["rotation_cover"])        # Kombis aus One-ways
        self.assertIsNone(self.alerts["de-cn-economy"]["rotation_cover"])

    def test_health_page(self):
        rep = self.get("/api/sources/health")
        by_id = {s["id"]: s for s in rep["sources"]}
        self.assertEqual(by_id["feed:premium-flights"]["state"], "veraltet")
        self.assertEqual(rep["sources"][0]["id"], "feed:premium-flights")     # Probleme zuerst
        for name in ("feed:secretflying", "feed:errorfarealerts", "feed:travel-dealz"):
            self.assertEqual(by_id[name]["state"], "ok", name)
        self.assertEqual(by_id["google_flights"]["empty_share"], 0)
        self.assertIsNone(by_id["feed:secretflying"]["empty_share"])
        names = [x["name"] for x in rep["not_used"]]
        self.assertIn("secretflying.com", names)
        self.assertIn("Amadeus Self-Service", names)
        self.assertTrue(all(x["reason"] for x in rep["not_used"]))


class RotationJsonTest(unittest.TestCase):
    TODAY = date(2026, 11, 4)

    def alert(self, **kw):
        base = dict(id="x", name="x", origins=["ZRH"], destinations=["HAM"], max_price=99)
        return Alert(**{**base, **kw})

    def cover(self, alert, alerts, memory=None):
        return web.rotation_json(alert, alerts, memory or rotation.Memory(), today=self.TODAY,
                                 now=datetime(2026, 11, 4, 12), scan_months=6)

    def test_covered_alerts(self):
        ow = self.alert(id="ow", trip="oneway", rotation=True, origins=["ZRH", "HAM"],
                        destinations=["HAM", "ZRH"])
        rt = self.alert(id="rt")
        cn = self.alert(id="cn", origins=["FRA"], destinations=["PEK"])
        mem = rotation.Memory()
        row = {"price": 39.0, "airline": "Eurowings", "stops": 0}
        for day in (5, 6):
            mem.put("ZRH", "HAM", "economy", date(2026, 11, day + 10), [row], None,
                    datetime(2026, 11, 4, 11))
        self.assertEqual(self.cover(ow, [ow, rt, cn], mem),
                         {"horizon_days": 90, "days": 2, "no_deal_sites": True})
        self.assertEqual(self.cover(rt, [ow, rt, cn], mem)["days"], 2)
        self.assertIsNone(self.cover(cn, [ow, rt, cn], mem))
        # Rotations-Alarm pausiert oder andere Kabine -> Hin/Rück nicht abgedeckt
        paused = self.alert(id="ow", trip="oneway", rotation=True, enabled=False,
                            origins=["ZRH", "HAM"], destinations=["HAM", "ZRH"])
        self.assertIsNone(self.cover(rt, [paused, rt]))
        self.assertIsNone(self.cover(self.alert(id="b", cabin="business"), [ow]))
        far = self.alert(id="far", trip="oneway", rotation=True, destinations=["PEK"])
        self.assertFalse(self.cover(far, [far])["no_deal_sites"])

    def test_broken_memory_never_crashes(self):
        ow = self.alert(id="ow", trip="oneway", rotation=True)
        mem = rotation.Memory({"legs": {"ZRH-HAM-economy": {"kaputt": {"at": 1}, "2026-12-01": 5}}})
        self.assertEqual(self.cover(ow, [ow], mem)["days"], 0)


class HealthReportFieldsTest(unittest.TestCase):
    def test_empty_share_and_not_used(self):
        with Store(":memory:") as store:
            store.set_source_state(health.STATE_NAME, {"sources": {
                "google_flights": {"state": "leer", "items": 8, "parsed": 2},
                "feed:x": {"state": "ok", "items": 3, "parsed": 3},
                "kaputt": "kein dict"}})
            rep = health.report(store)
        by_id = {s["id"]: s for s in rep["sources"]}
        self.assertEqual(by_id["google_flights"]["empty_share"], 0.75)
        self.assertIsNone(by_id["feed:x"]["empty_share"])
        self.assertNotIn("kaputt", by_id)
        self.assertEqual(len(rep["not_used"]), len(health.NOT_USED))
        json.dumps(rep)


if __name__ == "__main__":
    unittest.main()
