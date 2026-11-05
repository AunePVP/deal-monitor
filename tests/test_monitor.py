import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

from dealmonitor import config as config_mod
from dealmonitor import regions
from dealmonitor.models import Alert, Offer
from dealmonitor.monitor import run
from dealmonitor.principles import evaluate, euro, quality_problems
from dealmonitor.sources.feed import FeedSource, matches, parse_price, detect_cabin
from dealmonitor.sources.travelpayouts import TravelpayoutsSource, _months
from dealmonitor.store import Store

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 4)


def alert(**kw):
    base = dict(id="t", name="Test", origins=["ZRH"], destinations=["HAM"], max_price=110,
                baseline_price=190, max_stops=1, min_nights=1, max_nights=21)
    base.update(kw)
    return Alert(**base)


def offer(**kw):
    base = dict(source="travelpayouts", origin="ZRH", destination="HAM", price=89, airline="LX",
                stops=0, departure=date(2027, 1, 15), return_date=date(2027, 1, 19))
    base.update(kw)
    return Offer(**base)


class RegionTest(unittest.TestCase):
    def test_expand(self):
        self.assertIn("FRA", regions.expand(["@DE"]))
        self.assertEqual(regions.expand(["zrh", "ZRH", "HAM"]), ["ZRH", "HAM"])
        with self.assertRaises(ValueError):
            regions.expand(["@MARS"])

    def test_pairs_skip_same(self):
        a = alert(origins=["ZRH", "HAM"], destinations=["HAM", "ZRH"])
        self.assertEqual(a.pairs, [("ZRH", "HAM"), ("HAM", "ZRH")])


class PrinciplesTest(unittest.TestCase):
    def test_deal_under_limit(self):
        d = evaluate(alert(), offer(), [], today=TODAY)
        self.assertIsNotNone(d)
        self.assertEqual(d.rating, "Top-Deal")  # 89 vs. 190 = 53 % günstiger
        self.assertIn("hin und zurück", d.headline)
        self.assertIn("direkt", d.headline)

    def test_over_limit_is_never_a_deal(self):
        self.assertIsNone(evaluate(alert(), offer(price=111), [], today=TODAY))

    def test_reference_uses_own_history_not_claims(self):
        history = [100.0] * 10   # Strecke ist sowieso immer billig
        d = evaluate(alert(), offer(price=95), history, min_samples=8, today=TODAY)
        self.assertEqual(d.reference_price, 100)
        self.assertEqual(d.rating, "Deal")  # nur 5 % - kein "Top-Deal"

    def test_discount_mode_without_max_price(self):
        a = alert(max_price=None, baseline_price=200, min_discount=0.3)
        self.assertIsNone(evaluate(a, offer(price=150), [], today=TODAY))
        self.assertIsNotNone(evaluate(a, offer(price=139), [], today=TODAY))

    def test_would_not_book_it(self):
        a = alert(max_duration_hours=6)
        self.assertTrue(quality_problems(a, offer(stops=2), TODAY))
        self.assertTrue(quality_problems(a, offer(duration_out_min=500), TODAY))
        self.assertTrue(quality_problems(a, offer(return_date=date(2027, 3, 1)), TODAY))
        self.assertTrue(quality_problems(a, offer(departure=date(2026, 1, 1)), TODAY))
        self.assertTrue(quality_problems(alert(exclude_airlines=["lx"]), offer(), TODAY))
        self.assertFalse(quality_problems(a, offer(), TODAY))

    def test_oneway_marked(self):
        a = alert(trip="oneway", max_price=45)
        d = evaluate(a, offer(price=39, roundtrip=False, return_date=None), [], today=TODAY)
        self.assertIn("one-way", d.headline)
        # Hin- und Rückflug-Angebot passt nicht auf einen one-way-Alarm
        self.assertIsNone(evaluate(a, offer(price=39), [], today=TODAY))

    def test_cabin_must_match(self):
        a = alert(cabin="business", max_price=2000, baseline_price=3000)
        self.assertIsNone(evaluate(a, offer(price=500), [], today=TODAY))
        d = evaluate(a, offer(price=1800, cabin="business"), [], today=TODAY)
        self.assertTrue(d.headline.startswith("Business Class:"))

    def test_euro_format(self):
        self.assertEqual(euro(1799), "1.799 €")
        self.assertEqual(euro(89.4), "89 €")


class FeedTest(unittest.TestCase):
    def feed(self):
        xml = (FIX / "travel-dealz-feed.xml").read_text()
        xml = xml.replace("PUBDATE", format_datetime(datetime.now().astimezone()))
        src = FeedSource()
        src._items = src.parse(xml.encode())
        return src

    def test_parse_price(self):
        self.assertEqual(parse_price("Peking ab 1.799€"), 1799)
        self.assertEqual(parse_price("Hamburg ab 79 € hin und zurück"), 79)
        self.assertEqual(parse_price("für 89,- Euro"), 89)
        self.assertIsNone(parse_price("Neue Lounge in Zürich"))

    def test_keyword_umlauts(self):
        a = alert(feed_keywords=[["Zürich", "Hamburg"]])
        self.assertTrue(matches(a, "Hamburg - Zurich ab 59€"))
        self.assertTrue(matches(a, "HAMBURG nach ZUERICH"))
        self.assertFalse(matches(a, "Hamburg - Wien ab 59€"))

    def test_expired_items_skipped(self):
        titles = [i["title"] for i in self.feed().load()]
        self.assertFalse(any("Abgelaufen" in t for t in titles))

    def test_old_items_skipped(self):
        xml = (FIX / "travel-dealz-feed.xml").read_text().replace(
            "PUBDATE", format_datetime(datetime.now().astimezone() - timedelta(days=30)))
        self.assertEqual(FeedSource(max_age_days=14).parse(xml.encode()), [])

    def test_cabin_detection_separates_alerts(self):
        src = self.feed()
        eco = alert(id="e", origins=["@DE"], destinations=["@CN"], max_price=499,
                    baseline_price=750, feed_keywords=[["Peking"], ["Shanghai"]])
        offers = src.fetch(eco)
        self.assertEqual(detect_cabin(offers[0].title), "business")
        deals = [evaluate(eco, o, [], today=TODAY) for o in offers]
        hits = [d.headline for d in deals if d]
        self.assertEqual(hits, ["Air China: Deutschland – Peking ab 429€"])


class TravelpayoutsTest(unittest.TestCase):
    def test_months(self):
        self.assertEqual(_months(date(2026, 11, 20), 3), ["2026-11", "2026-12", "2027-01"])

    def test_parse_v3(self):
        rows = json.loads((FIX / "travelpayouts_zrh_ham.json").read_text())["data"]
        src = TravelpayoutsSource(token="x", marker="12345")
        o = src.parse_v3(rows[0], alert())
        self.assertEqual((o.origin, o.destination, o.price, o.stops), ("ZRH", "HAM", 89, 0))
        self.assertEqual(o.nights, 4)
        self.assertTrue(o.link.startswith("https://www.aviasales.com/search/"))
        self.assertIn("marker=12345", o.link)

    def test_missing_token(self):
        import os
        from unittest import mock
        from dealmonitor.sources import SourceError
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(SourceError):
            TravelpayoutsSource(token=None).fetch(alert())


class ListSource:
    name = "travelpayouts"
    records_history = True

    def __init__(self, offers):
        self.offers = offers

    def fetch(self, a):
        return [o for o in self.offers if (o.origin, o.destination) in a.pairs]


class Collect:
    name = "collect"

    def __init__(self):
        self.sent = []

    def send(self, name, items):
        self.sent += [(name, d.offer.price, s) for d, s in items]


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "alerts.toml"
        path.write_text('[settings]\ndatabase = "db.sqlite"\nreport = "r.html"\n'
                        '[[alert]]\nid = "zrh-ham"\nname = "ZRH-HAM"\norigins = ["ZRH"]\n'
                        'destinations = ["HAM"]\nmax_price = 110\nbaseline_price = 190\n')
        self.cfg = config_mod.load(path)
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_lifecycle_new_known_cheaper_expired(self):
        n = Collect()
        run(self.cfg, [ListSource([offer(price=99)])], [n], self.store, today=TODAY)
        self.assertEqual(n.sent, [("ZRH-HAM", 99, "new")])

        run(self.cfg, [ListSource([offer(price=98)])], [n], self.store, today=TODAY)
        self.assertEqual(len(n.sent), 1, "1 € billiger ist kein Grund für Spam")

        run(self.cfg, [ListSource([offer(price=85)])], [n], self.store, today=TODAY)
        self.assertEqual(n.sent[-1], ("ZRH-HAM", 85, "cheaper"))

        res = run(self.cfg, [ListSource([])], [n], self.store, today=TODAY)
        self.assertEqual(res.expired, 1)
        self.assertEqual(self.store.deals(status="expired")[0]["price"], 85)

        # gleich teuer kurz nach dem Ablaufen: still reaktivieren, nicht erneut melden
        sent = len(n.sent)
        run(self.cfg, [ListSource([offer(price=85)])], [n], self.store, today=TODAY)
        self.assertEqual(len(n.sent), sent, "Flackern darf keine neue Meldung auslösen")
        self.assertEqual(len(self.store.deals(status="active")), 1)

        # deutlich billiger wieder da -> melden
        run(self.cfg, [ListSource([])], [n], self.store, today=TODAY)
        run(self.cfg, [ListSource([offer(price=80)])], [n], self.store, today=TODAY)
        self.assertEqual(n.sent[-1], ("ZRH-HAM", 80, "revived"))

        # länger als einen Tag weg -> auch zum gleichen Preis wieder melden
        run(self.cfg, [ListSource([])], [n], self.store, today=TODAY)
        self.store.db.execute("UPDATE deals SET expired_at='2026-01-01T00:00:00'")
        run(self.cfg, [ListSource([offer(price=80)])], [n], self.store, today=TODAY)
        self.assertEqual(n.sent[-1], ("ZRH-HAM", 80, "revived"))
        self.assertEqual(len(n.sent), sent + 2)

    def test_failed_source_does_not_expire(self):
        from dealmonitor.sources import SourceError

        class Broken(ListSource):
            def fetch(self, a):
                raise SourceError("offline")

        run(self.cfg, [ListSource([offer(price=99)])], [Collect()], self.store, today=TODAY)
        res = run(self.cfg, [Broken([])], [Collect()], self.store, today=TODAY)
        self.assertEqual(res.expired, 0)
        self.assertEqual(len(res.errors), 1)

    def test_one_deal_per_month_and_route(self):
        n = Collect()
        offers = [offer(price=p, departure=date(2027, 1, d), return_date=date(2027, 1, d + 3))
                  for p, d in ((99, 3), (95, 10), (105, 20))]
        run(self.cfg, [ListSource(offers)], [n], self.store, today=TODAY)
        self.assertEqual(n.sent, [("ZRH-HAM", 95, "new")])

    def test_add_alert_both_directions(self):
        from dealmonitor.cli import main
        data = Path(self.tmp.name) / "data"
        rc = main(["-c", str(self.cfg.path), "--data", str(data), "add", "--id", "de-cn",
                   "--from", "@DE", "--to", "@CN_MAJOR", "--max-price", "499",
                   "--both-directions"])
        self.assertEqual(rc, 0)
        # Alarme landen in der Datenbank (vorher aus der TOML geseedet), TOML bleibt unverändert
        with Store(data / "deals.sqlite") as store:
            rows = store.alert_rows()
        self.assertEqual([a["id"] for a in rows], ["zrh-ham", "de-cn", "de-cn-retour"])
        self.assertEqual(rows[2]["origins"], ["@CN_MAJOR"])
        self.assertEqual([a.id for a in config_mod.load(self.cfg.path).alerts], ["zrh-ham"])
        # doppelte ID -> Fehler, nichts geschrieben
        rc = main(["-c", str(self.cfg.path), "--data", str(data), "add", "--id", "de-cn",
                   "--from", "FRA", "--to", "SHA", "--max-price", "499"])
        self.assertEqual(rc, 2)


class NotifyTest(unittest.TestCase):
    def test_webhook_payload_and_token(self):
        import os
        from unittest import mock
        from dealmonitor import notify
        env = {"WEBHOOK_URL": "https://notify.example/hook", "WEBHOOK_TOKEN": "test-dummy-token"}
        with mock.patch.dict(os.environ, env, clear=True):
            channels = notify.from_env()
        self.assertEqual([c.name for c in channels], ["console", "webhook"])
        deal = evaluate(alert(), offer(), [], today=TODAY)
        with mock.patch("dealmonitor.notify.http.request") as req:
            channels[1].send("Test", [(deal, "new")])
        url = req.call_args.args[0]
        kw = req.call_args.kwargs
        body = json.loads(kw["data"])
        self.assertEqual(url, "https://notify.example/hook")
        self.assertEqual(kw["headers"]["Authorization"], "Bearer test-dummy-token")
        self.assertEqual(body["deals"][0]["price"], 89)
        self.assertEqual(body["deals"][0]["status"], "new")
        self.assertEqual(body["deals"][0]["return"], "2027-01-19")

    def test_no_email_channel(self):
        import os
        from unittest import mock
        from dealmonitor import notify
        env = {"SMTP_HOST": "smtp.example", "MAIL_TO": "a@example.org"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual([c.name for c in notify.from_env()], ["console"])
        self.assertFalse(hasattr(notify, "Email"))


class ShippedConfigTest(unittest.TestCase):
    def test_default_config_loads(self):
        cfg = config_mod.load(Path(__file__).parent.parent / "config" / "alerts.toml")
        ids = {a.id for a in cfg.alerts}
        self.assertTrue({"zrh-ham", "ham-zrh", "de-cn-economy", "de-cn-business"} <= ids)


if __name__ == "__main__":
    unittest.main()
