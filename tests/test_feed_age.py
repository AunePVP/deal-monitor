"""Altersregel der Deal-Seiten (``feed.is_current``): Ablaufdatum geht vor dem Alter, Höchstalter
je Quelle (YHBU 45, premium-flights 30, sonst ``feed_max_age_days``), Gesundheit auf Rohwerten,
alte Artikel werden gespeichert, aber nicht gemeldet. Ohne Netz."""

import logging
import re
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from unittest import mock

from dealmonitor import config, health, http
from dealmonitor.models import Offer
from dealmonitor.monitor import old_article, quiet_old, run
from dealmonitor.principles import evaluate
from dealmonitor.sources.feed import (
    PRESETS_BY_ID, FeedSource, is_current, normalize_item,
)
from dealmonitor.store import Store

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures" / "feeds"
NOW = datetime(2026, 11, 5, 14, 0, tzinfo=timezone.utc)
YHBU_URL = PRESETS_BY_ID["yhbu"].urls[0]


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def item(days: float, **kw) -> dict:
    return {"title": "Business Class Frankfurt – Peking ab 1.330 €", "categories": [],
            "published": NOW - timedelta(days=days), **kw}


def shifted_yhbu(newest_age: timedelta) -> bytes:
    """YHBU-Fixture (50 Artikel, neuester 18.10.2026) so verschoben, dass der neueste Artikel
    ``newest_age`` alt ist - die Tests hängen nicht vom Datum ab."""
    xml = (FIX / "yhbu-premium-tickets.xml").read_text(encoding="utf-8")
    dates = [parsedate_to_datetime(d) for d in re.findall(r"<pubDate>([^<]*)</pubDate>", xml)]
    delta = datetime.now(timezone.utc) - newest_age - max(dates)
    return re.sub(r"<pubDate>([^<]*)</pubDate>",
                  lambda m: f"<pubDate>{format_datetime(parsedate_to_datetime(m.group(1)) + delta)}"
                            "</pubDate>", xml).encode()


class IsCurrentTest(unittest.TestCase):
    def test_expires_wins_over_age(self):
        self.assertTrue(is_current(item(60, expires=NOW + timedelta(days=5)), NOW, 14))
        self.assertFalse(is_current(item(1, expires=NOW - timedelta(minutes=1)), NOW, 14))
        self.assertTrue(is_current(item(60, expires=(NOW + timedelta(days=5)).isoformat()), NOW, 14))

    def test_age_without_expires(self):
        yhbu, pf = PRESETS_BY_ID["yhbu"], PRESETS_BY_ID["premium-flights"]
        src = FeedSource(presets=["yhbu"], max_age_days=14)
        self.assertEqual((src.age_limit(yhbu), src.age_limit(pf)), (45, 30))
        self.assertEqual(src.age_limit(PRESETS_BY_ID["travel-dealz"]), 14)
        self.assertEqual(src.age_limit(None), 14)
        self.assertTrue(is_current(item(40), NOW, src.age_limit(yhbu)))
        self.assertFalse(is_current(item(50), NOW, src.age_limit(yhbu)))
        self.assertTrue(is_current(item(29), NOW, src.age_limit(pf)))
        self.assertFalse(is_current(item(31), NOW, src.age_limit(pf)))
        self.assertFalse(is_current(item(15), NOW, src.age_limit(PRESETS_BY_ID["travel-dealz"])))
        self.assertTrue(is_current(item(13), NOW, 14))
        self.assertTrue(is_current({"title": "ohne Datum"}, NOW, 14))

    def test_effective_limit_is_the_larger_value(self):
        src = FeedSource(presets=["yhbu"], max_age_days=60)
        self.assertEqual(src.age_limit(PRESETS_BY_ID["yhbu"]), 60)
        self.assertEqual(src.age_limit(PRESETS_BY_ID["premium-flights"]), 60)
        src = FeedSource(presets=["yhbu"], max_age_days=3)
        self.assertEqual(src.age_limit(PRESETS_BY_ID["yhbu"]), 45)
        self.assertEqual(src.age_limit(PRESETS_BY_ID["mydealz-fluege"]), 3)

    def test_marked_expired_and_implausible_expires(self):
        self.assertFalse(is_current(item(1, title="EXPIRED: Peking ab 999 €"), NOW, 14))
        self.assertFalse(is_current(item(1, categories=["Abgelaufen"]), NOW, 14))
        # Ablaufdatum über 400 Tage voraus = unbekannt -> Alter zählt
        far = NOW + timedelta(days=500)
        self.assertFalse(is_current(item(20, expires=far), NOW, 14))
        self.assertTrue(is_current(item(5, expires=far), NOW, 14))

    def test_normalize_item_uses_same_rule(self):
        future = NOW + timedelta(days=5)
        self.assertIsNotNone(normalize_item(item(30, expires=future), now=NOW, max_age_days=14))
        self.assertIsNone(normalize_item(item(30), now=NOW, max_age_days=14))
        self.assertIsNotNone(normalize_item(item(30), now=NOW))    # ohne Grenze: bleibt


class ParseTest(unittest.TestCase):
    def test_reisetopia_infobox_expiry_keeps_old_article(self):
        pub = format_datetime(datetime.now(timezone.utc) - timedelta(days=20))
        until = datetime.now(timezone.utc) + timedelta(days=10)
        content = (f"<p>Business Class von Frankfurt nach Shanghai Buchbar ab nur 1.799 Euro pro "
                   f"Person Reisezeitraum: Offen Buchungszeitraum: Bis {until:%d.%m.%Y} "
                   f"Fluggesellschaft: Air China</p>")
        rss = ("<rss xmlns:content=\"http://purl.org/rss/1.0/modules/content/\"><channel>"
               f"<item><title>Air China Business nach Shanghai</title><link>https://reisetopia.de/"
               f"x/</link><pubDate>{pub}</pubDate><category>Business Class</category>"
               f"<content:encoded><![CDATA[{content}]]></content:encoded></item>"
               f"<item><title>Alter Artikel ohne Infobox</title><link>https://reisetopia.de/y/"
               f"</link><pubDate>{pub}</pubDate></item></channel></rss>").encode()
        src = FeedSource(presets=["reisetopia"], max_age_days=14)
        url = PRESETS_BY_ID["reisetopia"].urls[0]
        self.assertEqual(len(src.parse_all(rss, url)), 2)            # ohne Altersregel
        kept = src.parse(rss, url)
        self.assertEqual([i["title"] for i in kept], ["Air China Business nach Shanghai"])
        self.assertEqual(kept[0]["expires"].date(), until.date())

    def test_yhbu_stats_are_raw_and_current_counted(self):
        xml = shifted_yhbu(timedelta(days=18))
        src = FeedSource(presets=["yhbu"], max_age_days=14, search_enabled=False)
        with mock.patch.object(http, "request", return_value=xml):
            items = src.load()
        st = src.stats["feed:yhbu"]
        # 50 im Feed, davon 5 höchstens 45 Tage alt (vorher: 0 -> "OK, 0 Artikel")
        self.assertEqual((st["items"], st["current"], len(items)), (50, 5, 5))
        self.assertGreater(st["parsed"], 5)
        newest = max(i["published"] for i in items)
        self.assertEqual(st["newest"], newest)
        self.assertAlmostEqual((datetime.now(timezone.utc) - newest).total_seconds() / 86400, 18,
                               places=2)
        state, _ = health.classify(st, health.rule_for("feed:yhbu"), datetime.now(timezone.utc))
        self.assertEqual(state, health.OK)

    def test_future_items_do_not_count_as_newest(self):
        future = format_datetime(datetime.now(timezone.utc) + timedelta(days=2))
        now = format_datetime(datetime.now(timezone.utc) - timedelta(hours=3))
        rss = (f"<rss><channel><item><title>A ab 99€</title><link>https://travel-dealz.de/a/</link>"
               f"<pubDate>{future}</pubDate></item><item><title>B ab 99€</title><link>https://"
               f"travel-dealz.de/b/</link><pubDate>{now}</pubDate></item></channel></rss>").encode()
        src = FeedSource(presets=["fly4free"], search_enabled=False)
        with mock.patch.object(http, "request", return_value=rss):
            src.load()
        st = src.stats["feed:fly4free"]
        self.assertEqual(st["items"], 2)
        self.assertEqual(st["newest"], parsedate_to_datetime(now))


class HealthTest(unittest.TestCase):
    def stat(self, days, items=50, current=0, parsed=40):
        return {"items": items, "parsed": parsed, "current": current,
                "newest": NOW - timedelta(days=days), "error": None}

    def test_yhbu_thresholds(self):
        rule = health.rule_for("feed:yhbu")
        self.assertEqual(health.classify(self.stat(18), rule, NOW)[0], health.OK)
        state, detail = health.classify(self.stat(31), rule, NOW)
        self.assertEqual(state, health.STALE)
        self.assertIn("Grenze 720 Std.", detail)
        # 50 roh, 0 aktuell: kein LEER (der Feed liefert ja)
        self.assertEqual(health.classify(self.stat(20, current=0), rule, NOW)[0], health.OK)
        # 0 Artikel im Feed: LEER (YHBU listet immer 50)
        self.assertEqual(health.classify(self.stat(1, items=0, parsed=0), rule, NOW)[0],
                         health.EMPTY)

    def test_current_in_collect_report_and_heartbeat(self):
        class Src:
            name = "feeds"
            offer_sources = ["feed:yhbu"]
            stats = {"feed:yhbu": {"items": 50, "parsed": 40, "current": 2,
                                   "newest": NOW - timedelta(days=18), "error": None}}
        out = health.collect([Src()], NOW)
        self.assertEqual(out["feed:yhbu"]["current"], 2)
        self.assertIn("You have been upgraded: 50 Artikel, 2 aktuell",
                      health._heartbeat_text(out))
        store = Store(":memory:")
        self.addCleanup(store.close)
        health.update(store, [Src()], now=NOW)
        rep = health.report(store, NOW, feed_max_age_days=14)
        row = next(s for s in rep["sources"] if s["id"] == "feed:yhbu")
        self.assertEqual((row["items"], row["current"], row["max_age_days"]), (50, 2, 45))
        self.assertEqual(health.report(store, NOW)["sources"][0]["max_age_days"], 45)

    def test_old_state_without_current(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        store.set_source_state(health.STATE_NAME, {"sources": {
            "feed:travel-dealz": {"state": "ok", "items": 3, "parsed": 2,
                                  "newest": "2026-11-05T10:00:00"},
            "google_flights": {"state": "ok", "items": 8, "parsed": 8}}})
        rep = health.report(store, NOW, feed_max_age_days=14)
        by_id = {s["id"]: s for s in rep["sources"]}
        self.assertIsNone(by_id["feed:travel-dealz"]["current"])
        self.assertEqual(by_id["feed:travel-dealz"]["max_age_days"], 14)
        self.assertIsNone(by_id["google_flights"]["max_age_days"])
        self.assertNotIn("aktuell", health._heartbeat_text(
            store.source_state(health.STATE_NAME)["sources"]))


class Collect:
    name = "collect"

    def __init__(self):
        self.sent = []

    def send(self, name, items):
        self.sent += [(d.offer.title, s) for d, s in items]


class ArticleFeed:
    """Deal-Seiten-Quelle mit festen Artikel-Angeboten."""

    name = "feeds"
    records_history = False
    offer_sources = ["feed:yhbu"]
    incomplete = None

    def __init__(self, offers):
        self.offers = offers

    def fetch(self, alert):
        return list(self.offers) if alert.id == "de-cn-business" else []


def article(title, days, price=1330):
    when = datetime.now() - timedelta(days=days)
    return Offer(source="feed:yhbu", origin="FRA", destination="", price=price,
                 cabin="business", title=title, link=f"https://example.org/{price}",
                 found_at=when, published=when.astimezone())


class QuietOldArticlesTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(ROOT / "config" / "alerts.toml")
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def test_old_article_saved_not_notified(self):
        old = article("DEAL: Azerbaijan Business Istanbul – Peking (Return): 900 Euro", 39, 1080)
        fresh = article("DEAL: MIAT Business Frankfurt – Peking (Return): 1330 Euro", 2, 1330)
        n = Collect()
        run(self.cfg, [ArticleFeed([old, fresh])], [n], self.store, only=["de-cn-business"])
        self.assertEqual(n.sent, [(fresh.title, "new")])
        rows = {r["headline"]: r for r in self.store.deals("active")}
        self.assertEqual(len(rows), 2)
        quiet = next(r for r in rows.values() if r["price"] == 1080)
        self.assertIsNone(quiet["notified_price"])
        self.assertTrue(quiet["published"].startswith(old.published.isoformat()[:10]))
        # zweiter Lauf: weiter still, aber aktiv
        n2 = Collect()
        run(self.cfg, [ArticleFeed([old, fresh])], [n2], self.store, only=["de-cn-business"])
        self.assertEqual(n2.sent, [])
        self.assertEqual(len(self.store.deals("active")), 2)

    def test_quiet_old_statuses(self):
        a = next(x for x in self.cfg.alerts if x.id == "de-cn-business")
        d_old = evaluate(a, article("Business Peking 1330", 20), [], min_samples=8)
        d_new = evaluate(a, article("Business Peking 1331", 1, 1331), [], min_samples=8)
        self.assertTrue(old_article(d_old, 14) and not old_article(d_new, 14))
        out = quiet_old([(d_old, "new"), (d_old, "revived"), (d_old, "cheaper"),
                         (d_new, "new")], 14)
        self.assertEqual([s for _, s in out], ["quiet", "quiet", "cheaper", "new"])
        # Preis-Angebote (ohne Artikel) nie
        plain = evaluate(a, Offer(source="travelpayouts", origin="FRA", destination="PEK",
                                  price=1500, cabin="business",
                                  found_at=datetime.now() - timedelta(days=30)), [], min_samples=8)
        self.assertEqual(quiet_old([(plain, "new")], 14), [(plain, "new")])

    def test_yhbu_feed_end_to_end(self):
        """Echter Feed-Code: Budapest (33 Tage) und Istanbul (39 Tage) kommen als Deal an,
        werden aber nicht gemeldet; vorher fielen sie wegen 14 Tagen ganz weg."""
        src = FeedSource(presets=["yhbu"], max_age_days=14, search_enabled=False)
        n = Collect()
        with mock.patch.object(http, "request", return_value=shifted_yhbu(timedelta(days=18))):
            run(self.cfg, [src], [n], self.store, only=["de-cn-business"])
        self.assertEqual(n.sent, [])
        found = {(r["price"], r.get("via_hub")) for r in self.store.deals("active")}
        self.assertEqual(found, {(1670, "BUD"), (1080, "IST")})
        self.assertTrue(all(r["notified_price"] is None for r in self.store.deals("active")))


if __name__ == "__main__":
    unittest.main()
