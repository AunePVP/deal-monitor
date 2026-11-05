"""Secret Flying über Bluesky (Paket 1): API- und RSS-Parser, Filter, Zustand, Fallback.

Kein Netz: ``secretflying.http.request`` wird durch Fixtures ersetzt
(``tests/fixtures/bsky_secretflying*.json`` = gekürzte getAuthorFeed-Seiten vom 05.11.2026,
``bsky_secretflying.xml`` = Profil-RSS vom selben Zeitpunkt).
"""

import json
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from dealmonitor import currency, http, radar
from dealmonitor.models import Alert
from dealmonitor.sources import SourceError
from dealmonitor.sources import feed
from dealmonitor.sources import secretflying as sf

FIX = Path(__file__).parent / "fixtures"
PAGE1 = (FIX / "bsky_secretflying.json").read_bytes()
PAGE2 = (FIX / "bsky_secretflying_p2.json").read_bytes()
RSS = (FIX / "bsky_secretflying.xml").read_bytes()
EMPTY = b'{"feed": []}'
#: Zeitpunkt der Fixtures (für Alters- und Ablaufprüfungen)
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
EN = feed.Profile(lang="en")


class FakeBluesky:
    """Ersetzt ``http.request``: Seite 1/2 der API, danach leer; RSS; Fehler auf Wunsch."""

    def __init__(self, *, api_error=None, rss_error=None, page2_error=None, page1=PAGE1):
        self.api_error, self.rss_error, self.page2_error = api_error, rss_error, page2_error
        self.page1 = page1
        self.calls = []

    def __call__(self, url, *, params=None, **kw):
        self.calls.append((url, dict(params or {})))
        if url == sf.RSS_URL:
            if self.rss_error:
                raise self.rss_error
            return RSS
        if url != sf.API_URL:
            raise AssertionError(f"unerwartete Adresse {url}")
        if self.api_error:
            raise self.api_error
        cursor = (params or {}).get("cursor")
        if cursor is None:
            return self.page1
        if cursor == json.loads(PAGE1)["cursor"]:
            if self.page2_error:
                raise self.page2_error
            return PAGE2
        return EMPTY

    @property
    def api_calls(self):
        return [c for c in self.calls if c[0] == sf.API_URL]


def fetch(fake=None, state=None):
    fake = fake or FakeBluesky()
    with mock.patch.object(sf.http, "request", side_effect=fake), \
            mock.patch.object(sf, "_now", return_value=NOW):
        return sf.fetch_items(state=state), fake


def info(item):
    it = feed.normalize_item(item, "loader:secretflying", now=NOW)
    it["profile"] = EN
    it["source"] = "feed:secretflying"
    return feed.analyze(it)


def by_title(items, start):
    hits = [i for i in items if i["title"].startswith(start)]
    if not hits:
        raise AssertionError(f"kein Artikel '{start}'")
    return hits[0]


def post(text, uri="at://did:plc:x/app.bsky.feed.post/abc", links=(), created=NOW):
    return {"text": text, "created": created, "uri": uri, "links": list(links), "pinned": False}


class FixtureDealsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items, cls.fake = fetch(state={})

    def test_dach_deals_from_fixture(self):
        for start, price, code in (
                ("Zurich, Switzerland to Mauritius", 471.0, "ZRH"),
                ("Frankfurt, Germany to Phuket", 496.0, "FRA"),
                ("Zurich, Switzerland to Johannesburg", 471.0, "ZRH")):
            item = by_title(self.items, start)
            i = info(item)
            self.assertTrue(i.flight, start)
            self.assertEqual(i.price, price)
            self.assertEqual(i.origin, "home")
            self.assertEqual(i.origin_codes, {code})
            self.assertFalse(i.oneway)
            self.assertTrue(item["link"].startswith("https://www.secretflying.com/posts/"))
            self.assertTrue(item["uid"].startswith("at://did:plc:ffurylltuiy7da64xuf5lgne/"))
        self.assertEqual(info(by_title(self.items, "Zurich, Switzerland to Johannesburg"))
                         .dest_codes, {"JNB"})

    def test_price_never_from_slug(self):
        item = by_title(self.items, "Zurich, Switzerland to Mauritius")
        self.assertIn("e213", item["link"])          # Slug nennt 213 €, der Text 471 €
        self.assertEqual(item["price_eur"], 471.0)

    def test_neighbor_hub_not_unknown(self):
        item = by_title(self.items, "Paris, France to Mauritius")
        self.assertEqual(item["origin_codes"], {"PAR"})
        i = info(item)
        self.assertEqual(i.origin, "foreign")
        self.assertEqual(i.price, 498.0)
        multi = by_title(self.items, "Stockholm or Gothenburg, Sweden")
        self.assertEqual(multi["origin_codes"], {"STO", "GOT"})
        self.assertEqual(multi["airline"], "British Airways & American Airlines")

    def test_foreign_currency_converted(self):
        usd = by_title(self.items, "PRICE DROP: Miami to Manaus")
        self.assertEqual(usd["price_eur"], currency.to_eur(391, "USD"))
        self.assertEqual(usd["hot"], "PRICE DROP")
        gbp = by_title(self.items, "London or Manchester, UK to Moroccan cities")
        self.assertEqual(gbp["price_eur"], currency.to_eur(29, "GBP"))
        self.assertEqual(gbp["origin_codes"], {"LON", "MAN"})

    def test_filters(self):
        titles = " | ".join(i["title"] for i in self.items)
        self.assertNotIn("per night", titles)                  # Hotels
        self.assertNotIn("EXPIRED", titles)                    # abgelaufene Error Fare
        self.assertNotIn("Tokyo, Japan to Singapore", titles)  # Abflug fern, ohne Marker
        self.assertNotIn("South-East Asia", titles)            # Teaser ab Asien
        for item in self.items:
            self.assertNotIn("#", item["title"])

    def test_business_teaser_without_price(self):
        teasers = [i for i in self.items if "Business-Teaser" in i["categories"]]
        self.assertEqual(len(teasers), 2)        # 2x Europa ja, 1x Südostasien nein
        t = teasers[0]
        self.assertEqual(t["title"], "A new Business Class deal from Europe has just been posted")
        self.assertNotIn("price_eur", t)
        self.assertEqual(t["cabin"], "business")
        self.assertTrue(t["link"].startswith("https://bsky.app/profile/secretflying.bsky.social/"))
        i = info(t)
        self.assertIsNone(i.price)
        self.assertEqual(i.cabin, "business")
        self.assertIsNone(radar.check(feed.normalize_item(t, now=NOW) | {"profile": EN}))

    def test_country_origin_becomes_region(self):
        item = by_title(self.items, "Germany to Moroccan cities")
        self.assertEqual(item["origin_codes"], set(feed.regions.REGIONS["DE"]))
        self.assertEqual(info(item).origin, "home")

    def test_open_jaw_and_oneway(self):
        jaw = by_title(self.items, "Vienna, Austria to Singapore returning to London")
        self.assertIs(jaw["oneway"], False)
        self.assertEqual(jaw["dest_codes"], {"SIN"})
        hot = by_title(self.items, "CRAZY HOT: Non-stop from Dublin")
        self.assertIs(hot["oneway"], True)
        self.assertEqual(hot["hot"], "CRAZY HOT")
        self.assertEqual(hot["origin_codes"], {"DUB"})

    def test_backfill_three_pages_on_first_run(self):
        self.assertEqual(len(self.fake.api_calls), 3)
        self.assertEqual(self.fake.api_calls[0][1]["limit"], 50)
        self.assertEqual(self.fake.api_calls[0][1]["filter"], "posts_no_replies")
        self.assertIn("cursor", self.fake.api_calls[1][1])
        self.assertTrue(any(i["title"].startswith("London, UK to Beijing") for i in self.items))


class RssTest(unittest.TestCase):
    def test_rss_and_api_give_same_items(self):
        api = {i["uid"]: i for i in (sf.post_item(p)
                                     for p in sf.parse_api(json.loads(PAGE1))[0]) if i}
        rss = {i["uid"]: i for i in (sf.post_item(p) for p in sf.parse_rss(RSS)) if i}
        self.assertGreaterEqual(len(rss), 10)
        self.assertLessEqual(set(rss), set(api))
        for uid, r in rss.items():
            a = dict(api[uid])
            r = dict(r)
            pa, pr = a.pop("published"), r.pop("published")
            self.assertEqual(a, r)
            self.assertEqual(pa.replace(second=0, microsecond=0), pr)   # RSS ohne Sekunden

    def test_rss_date_formats(self):
        self.assertEqual(sf._rss_date("05 Nov 2026 10:27 +0000"),
                         datetime(2026, 11, 5, 10, 27, tzinfo=timezone.utc))
        self.assertEqual(sf._rss_date("Thu, 05 Nov 2026 10:27:11 GMT"),
                         datetime(2026, 11, 5, 10, 27, 11, tzinfo=timezone.utc))
        self.assertIsNone(sf._rss_date("gestern"))
        self.assertIsNone(sf._rss_date(None))

    def test_rss_without_guid_uses_link(self):
        xml = (b'<rss><channel><item><link>https://bsky.app/profile/secretflying.bsky.social/'
               b'post/3abc</link><description>#Vienna, Austria to Tokyo, Japan for only '
               b'\xe2\x82\xac399 roundtrip</description></item></channel></rss>')
        item = sf.post_item(sf.parse_rss(xml)[0])
        self.assertEqual(item["uid"], f"at://{sf.DID}/app.bsky.feed.post/3abc")
        self.assertEqual(item["link"], "https://bsky.app/profile/secretflying.bsky.social/post/3abc")
        self.assertEqual(item["price_eur"], 399.0)


class PostParserTest(unittest.TestCase):
    def test_business_error_fare_from_dach(self):
        item = sf.post_item(post(
            "⚠️ BUSINESS CLASS ERROR FARE ⚠️ #Frankfurt, Germany to Beijing, China for only "
            "€999 roundtrip with Air China #ErrorFare\n\nwww.secretflying.com/posts/fra-...",
            links=["https://www.secretflying.com/posts/fra-pek-business-e999"]))
        self.assertEqual(item["hot"], "ERROR FARE")
        self.assertEqual(item["cabin"], "business")
        self.assertEqual(item["origin_codes"], {"FRA"})
        self.assertEqual(item["dest_codes"], {"BJS", "PEK", "PKX"})
        self.assertEqual(item["price_eur"], 999.0)
        self.assertEqual(item["airline"], "Air China")
        self.assertEqual(item["link"], "https://www.secretflying.com/posts/fra-pek-business-e999")
        i = info(item)
        self.assertEqual((i.cabin, i.price, i.origin, i.hot), ("business", 999.0, "home",
                                                                "ERROR FARE"))
        self.assertEqual(radar.hot_reason(item["title"], item["categories"]),
                         "Error Fare laut Überschrift")

    def test_chf_prices(self):
        for text in ("#Zurich, Switzerland to Bangkok, Thailand for only CHF 399 roundtrip",
                     "#Zurich, Switzerland to Bangkok, Thailand for only 399 CHF roundtrip"):
            item = sf.post_item(post(text))
            self.assertEqual(item["price_eur"], currency.to_eur(399, "CHF"), text)

    def test_usd_post_from_dach(self):
        item = sf.post_item(post("Non-stop from #Munich, Germany to #NewYork for only "
                                 "$1,099 USD roundtrip"))
        self.assertEqual(item["price_eur"], currency.to_eur(1099, "USD"))
        self.assertEqual(item["origin_codes"], {"MUC"})
        self.assertEqual(item["dest_codes"], {"NYC"})

    def test_far_origin_only_with_marker_and_known_code(self):
        far = "#Tokyo, Japan to Singapore for only $291 USD roundtrip #Travel"
        self.assertIsNone(sf.post_item(post(far)))
        hot = sf.post_item(post("⚠️ ERROR FARE ⚠️ " + far))
        self.assertEqual(hot["hot"], "ERROR FARE")
        self.assertEqual(hot["origin_codes"], {"TYO"})
        self.assertEqual(info(hot).origin, "foreign")
        # ohne bekannten Code wäre der Abflug "unbekannt" und passte zu jedem Alarm -> weg
        self.assertIsNone(sf.post_item(post(
            "⚠️ ERROR FARE ⚠️ US cities to Japan from only $154 roundtrip #ErrorFare")))
        self.assertIsNone(sf.post_item(post(
            "We’ve found an Error Fare from Victoria, Canada 🇨🇦 to London, UK 🇬🇧 for only "
            "$123 CAD roundtrip.")))

    def test_error_fare_teaser_from_europe(self):
        item = sf.post_item(post(
            "⚠️ WE’VE JUST FOUND ANOTHER ERROR FARE!! ⚠️\n\nThis time it’s Economy Class and "
            "departing from Europe ✈️\n\n🔓 Paid members have priority access.\n\n👉 "
            "www.secretflying.com/error-fares/", uri=f"at://{sf.DID}/app.bsky.feed.post/3t"))
        self.assertEqual(item["hot"], "ERROR FARE")
        self.assertIn("Teaser", item["categories"])
        self.assertNotIn("price_eur", item)
        self.assertIn("departing from Europe", item["title"])
        self.assertEqual(item["link"], "https://bsky.app/profile/secretflying.bsky.social/post/3t")
        us = post("⚠️ WE’VE JUST FOUND ANOTHER ERROR FARE!! ⚠️\n\nThis time it’s Economy Class "
                  "and departing from the United States ✈️")
        self.assertIsNone(sf.post_item(us))

    def test_teaser_from_germany_has_codes(self):
        item = sf.post_item(post("A new Business Class deal from Germany has just been posted ✈️"
                                 "\n\nPremium members can log in now."))
        self.assertIn("Business-Teaser", item["categories"])
        self.assertEqual(item["origin_codes"], set(feed.regions.REGIONS["DE"]))

    def test_odd_posts_never_crash(self):
        for text in ("", "   ", "#Travel", "Hello world", "to to to for only €",
                     "#Zurich to for only €12abc", "❌ BUSINESS CLASS ERROR FARE EXPIRED ❌",
                     "5* Hotel in Vienna, Austria to see for only €99 per night",
                     "#Zurich, Switzerland to Mauritius for only ¥471 roundtrip"):
            item = sf.post_item(post(text))
            if item is not None:
                self.assertNotIn("price_eur", item, text)
        self.assertIsNone(sf._amount("1.2.3"))
        self.assertEqual(sf._amount("1,134"), 1134.0)
        self.assertEqual(sf._amount("29.99"), 29.99)

    def test_classify_origin(self):
        self.assertEqual(sf.classify_origin("Zurich, Switzerland"), ("home", {"ZRH"}))
        self.assertEqual(sf.classify_origin("Paris, France"), ("europe", {"PAR"}))
        self.assertEqual(sf.classify_origin("European cities")[0], "europe")
        self.assertEqual(sf.classify_origin("UK cities"), ("europe", set()))
        self.assertEqual(sf.classify_origin("Tampa, Florida")[0], "far")
        self.assertEqual(sf.classify_origin("Washington DC")[1], {"WAS"})
        self.assertEqual(sf.classify_origin("Sao Vicente, Cape Verde")[1], set())   # nicht SAO


class ApiParserTest(unittest.TestCase):
    def test_broken_entries_are_skipped(self):
        data = {"feed": [None, {}, {"post": {}}, {"post": {"record": "x"}},
                         {"post": {"uri": "at://a/app.bsky.feed.post/1",
                                   "record": {"text": "#Vienna, Austria to Seoul, South Korea "
                                                      "for only €474 roundtrip",
                                              "createdAt": "kaputt", "facets": "x"},
                                   "indexedAt": "2026-11-05T10:00:00Z"}}],
                "cursor": 5}
        posts, cursor = sf.parse_api(data)
        self.assertEqual(len(posts), 1)
        self.assertIsNone(cursor)
        self.assertEqual(posts[0]["created"], datetime(2026, 11, 5, 10, tzinfo=timezone.utc))
        self.assertEqual(sf.post_item(posts[0])["price_eur"], 474.0)

    def test_reposts_skipped_pins_kept(self):
        entry = json.loads(PAGE1)["feed"][0]
        repost = dict(entry, reason={"$type": "app.bsky.feed.defs#reasonRepost"})
        pin = dict(entry, reason={"$type": "app.bsky.feed.defs#reasonPin"})
        posts, _ = sf.parse_api({"feed": [repost, pin]})
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0]["pinned"])

    def test_unexpected_answer(self):
        for data in ([], {"error": "x"}, {"feed": "x"}, None):
            with self.assertRaises(SourceError):
                sf.parse_api(data)


class FetchTest(unittest.TestCase):
    def test_state_newest_and_single_page_afterwards(self):
        state = {}
        items, _ = fetch(state=state)
        self.assertEqual(state["via"], "api")
        self.assertEqual(state["newest"], "2026-11-05T10:27:10.242000+00:00")
        json.dumps(state)                                     # JSON-tauglich
        again, fake = fetch(state=state)
        self.assertEqual(len(fake.api_calls), 1)              # Anschluss auf Seite 1
        # ältere Deals (Seite 2) kommen aus dem Gedächtnis, laufen also nicht ab
        self.assertEqual({i["uid"] for i in again}, {i["uid"] for i in items})

    def test_gap_loads_more_pages(self):
        state = {"newest": "2026-11-02T00:00:00+00:00"}
        _, fake = fetch(state=state)
        self.assertEqual(len(fake.api_calls), 2)              # Seite 2 reicht bis 01.11.
        self.assertEqual(state["newest"], "2026-11-05T10:27:10.242000+00:00")

    def test_old_memory_is_dropped(self):
        old = {"uri": "at://x/app.bsky.feed.post/old", "links": [],
               "text": "#Zurich, Switzerland to Bali, Indonesia for only €399 roundtrip",
               "created": (NOW - timedelta(days=30)).isoformat()}
        mid = dict(old, uri="at://x/app.bsky.feed.post/mid",
                   created=(NOW - timedelta(days=9)).isoformat())
        state = {"newest": NOW.isoformat(), "posts": [old, mid, "kaputt", {"uri": 1}]}
        items, _ = fetch(state=state)
        uids = {i["uid"] for i in items}
        self.assertIn("at://x/app.bsky.feed.post/mid", uids)
        self.assertNotIn("at://x/app.bsky.feed.post/old", uids)
        self.assertLessEqual(len(state["posts"]), sf.KEEP_POSTS)

    def test_rss_fallback(self):
        state = {}
        fake = FakeBluesky(api_error=http.HttpError("HTTP 502", 502))
        items, fake = fetch(fake, state)
        self.assertEqual(state["via"], "rss")
        self.assertNotIn("newest", state)                     # nächstes Mal API-Backfill
        self.assertTrue(any(i["title"].startswith("Zurich, Switzerland to Mauritius")
                            for i in items))
        bad_json = FakeBluesky(page1=b"<html>Wartung</html>")
        items, _ = fetch(bad_json, {})
        self.assertTrue(items)

    def test_both_down(self):
        fake = FakeBluesky(api_error=http.HttpError("HTTP 503", 503),
                           rss_error=http.HttpError("HTTP 403", 403))
        with self.assertRaises(SourceError) as cm:
            fetch(fake, {})
        self.assertIn("RSS", str(cm.exception))
        broken_rss = FakeBluesky(api_error=http.HttpError("x"))
        with mock.patch.object(sf, "parse_rss", side_effect=SourceError("kein RSS-Kanal")):
            with self.assertRaises(SourceError):
                fetch(broken_rss, {})

    def test_page_two_error_keeps_page_one(self):
        state = {}
        items, _ = fetch(FakeBluesky(page2_error=http.HttpError("HTTP 500", 500)), state)
        self.assertTrue(any(i["title"].startswith("Frankfurt, Germany to Phuket") for i in items))
        self.assertNotIn("newest", state)

    def test_weird_post_does_not_stop_fetch(self):
        with mock.patch.object(sf, "post_item", side_effect=[ValueError("kaputt")] +
                               [None] * 500):
            items, _ = fetch(state={})
        self.assertEqual(items, [])


class FeedSourceIntegrationTest(unittest.TestCase):
    """So hängt Paket 3 die Quelle ein (Preset ``secretflying``, Loader, Lane fast)."""

    def setUp(self):
        preset = feed.Preset("secretflying", "Secret Flying", (), "en", True, EN,
                             "https://www.secretflying.com/", "Test",
                             loader="dealmonitor.sources.secretflying:fetch_items", lane="fast")
        patcher = mock.patch.dict(feed.PRESETS_BY_ID, {"secretflying": preset})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_alert_matches_zurich_deal(self):
        fake = FakeBluesky()
        alert = Alert(id="zrh-jnb", name="Zürich Südafrika", origins=["ZRH"],
                      destinations=["JNB"], max_price=500)
        src = feed.FeedSource(presets=["secretflying"], lanes={"fast"}, max_age_days=3650)
        with mock.patch.object(sf.http, "request", side_effect=fake), \
                mock.patch.object(sf, "_now", return_value=NOW):
            offers = src.fetch(alert)
        self.assertEqual([(o.price, o.source) for o in offers], [(471.0, "feed:secretflying")])
        self.assertTrue(offers[0].uid.startswith("at://"))
        st = src.stats["feed:secretflying"]
        self.assertIsNone(st["error"])
        self.assertEqual(st["items"], 32)        # 100 Posts, davon 32 Deals ab Europa
        self.assertEqual(st["parsed"], 30)       # ohne die 2 Teaser (kein Preis)

    def test_paris_does_not_match_german_alert(self):
        alert = Alert(id="de-mru", name="Mauritius", origins=["@DE"],
                      destinations=["PAR"], max_price=600)
        src = feed.FeedSource(presets=["secretflying"], max_age_days=3650)
        with mock.patch.object(sf.http, "request", side_effect=FakeBluesky()), \
                mock.patch.object(sf, "_now", return_value=NOW):
            items = src.load()
        paris = next(i for i in items if i["title"].startswith("Paris, France to Mauritius"))
        self.assertFalse(feed.origin_ok(alert, feed.analyze(paris)))

    def test_source_error_marks_preset_failed(self):
        fake = FakeBluesky(api_error=http.HttpError("x"), rss_error=http.HttpError("y"))
        src = feed.FeedSource(presets=["secretflying"], max_age_days=3650)
        with mock.patch.object(sf.http, "request", side_effect=fake):
            with self.assertRaises(SourceError):
                src.load()
        self.assertIn("feed:secretflying", src.unreliable_sources())


class LiveUrlTest(unittest.TestCase):
    def test_api_params(self):
        _, fake = fetch(state={"newest": "2030-01-01T00:00:00+00:00"})
        url, params = fake.api_calls[0]
        self.assertEqual(url, "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed")
        self.assertEqual(params["actor"], "secretflying.bsky.social")
        self.assertIsNone(params.get("cursor"))
        self.assertEqual(urllib.parse.urlsplit(sf.RSS_URL).path,
                         "/profile/did:plc:ffurylltuiy7da64xuf5lgne/rss")


if __name__ == "__main__":
    unittest.main()
