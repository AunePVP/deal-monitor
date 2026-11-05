"""Runde 2, Paket 3: neue Presets und besser gelesene Feeds.

* reisetopia: Preis, Strecke und Buchungsende aus der Infobox im Artikeltext (content:encoded)
* YHBU: Titel "DEAL: Hainan Business Budapest – China (Return): 1.490 Euro"
* premium-flights: China-Tag-Feeds (ohne www), "Expired"-Kategorie
* Loader-Presets (Secret Flying, Travel-Dealz schnell, ErrorFareAlerts): nur Registrierung -
  fehlt das Modul, ist das ein Quellenfehler, kein Absturz

Fixtures (tests/fixtures/feeds/, Originaltexte vom 05.11.2026, gekürzt):
reisetopia-artikel.xml (Hauptfeed + Business-Class-Tag, Artikeltext ohne Flugsuche-Widget),
yhbu-premium-tickets.xml, premium-flights-china.xml, premium-flights-pek.xml,
fly4free-business-class.xml (eigene Namen: test_feeds/test_radar laden {preset}.xml).
"""

import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from dealmonitor import http
from dealmonitor.models import Alert
from dealmonitor.sources import feed
from dealmonitor.sources.feed import (
    CONTENT_CHARS, FEED_PRESETS, PRESETS_BY_ID, SOURCE_LABELS, FeedSource, Profile, analyze,
    is_flight_item, non_flight_title, place_codes, read_infobox, regex_price,
)

FIX = Path(__file__).parent / "fixtures" / "feeds"
OLD = 36500                                          # Altersgrenze praktisch aus
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)   # Stand der Fixtures
_is_expired = feed.is_expired

FIXTURES = {
    "https://reisetopia.de/feed/": "reisetopia-artikel.xml",
    "https://reisetopia.de/feed/?paged=2": "reisetopia-artikel.xml",
    "https://reisetopia.de/tag/business-class/feed/": "reisetopia-artikel.xml",
    "https://youhavebeenupgraded.boardingarea.com/category/billige-premium-tickets/feed/":
        "yhbu-premium-tickets.xml",
    "https://premium-flights.com/feed/": "premium-flights.xml",
    "https://premium-flights.com/tag/china/feed/": "premium-flights-china.xml",
    "https://premium-flights.com/tag/pek/feed/": "premium-flights-pek.xml",
    "https://premium-flights.com/tag/shanghai/feed/": "premium-flights-china.xml",
    "https://www.fly4free.com/flight-deals/business-class/feed/": "fly4free-business-class.xml",
    "https://www.fly4free.com/feed/": "fly4free.xml",
}


def fake_request(url, **kw):
    if url not in FIXTURES:
        raise http.HttpError(f"HTTP 404 für {url}", 404)
    return (FIX / FIXTURES[url]).read_bytes()


def alert(**kw):
    base = dict(id="t", name="Test", origins=["FRA"], destinations=["BJS"], cabin="business",
                max_price=2500)
    base.update(kw)
    return Alert(**base)


def at_fixture_time(test):
    """Ablauf (``expires``) gegen den Stand der Fixtures prüfen, nicht gegen heute."""
    return mock.patch.object(feed, "is_expired",
                             lambda item, now=None: _is_expired(item, now or NOW))(test)


def load(preset_id: str, fixture: str | None = None) -> list[dict]:
    """Artikel eines Presets wie im Lauf (parse + Quelle/Profil), aus einer Fixture."""
    preset = PRESETS_BY_ID[preset_id]
    src = FeedSource(presets=[preset_id], max_age_days=OLD)
    raw = (FIX / (fixture or FIXTURES[preset.urls[0]])).read_bytes()
    return src._tag(src.parse(raw, preset.urls[0]), preset)


def by_title(items: list[dict], part: str) -> dict:
    hits = [it for it in items if part in it["title"]]
    assert len(hits) == 1, (part, [it["title"] for it in items])
    return hits[0]


def fetch(presets: list[str], a: Alert, **kw):
    src = FeedSource(presets=presets, max_age_days=OLD, **kw)
    with mock.patch.object(http, "request", side_effect=fake_request):
        return src, src.fetch(a)


class RegistrationTest(unittest.TestCase):
    def test_loader_presets(self):
        expected = {"secretflying": ("dealmonitor.sources.secretflying:fetch_items", "fast", True),
                    "travel-dealz-rest": ("dealmonitor.sources.traveldealz_rest:fetch_items",
                                          "fast", True),
                    "errorfarealerts": ("dealmonitor.sources.errorfarealerts:fetch_items",
                                        "normal", False)}
        for pid, (loader, lane, enabled) in expected.items():
            p = PRESETS_BY_ID[pid]
            self.assertEqual((p.loader, p.lane, p.enabled, p.urls), (loader, lane, enabled, ()))
            self.assertEqual(p.jobs, (loader,))
            self.assertFalse(p.search)

    def test_rss_presets(self):
        f4f = PRESETS_BY_ID["fly4free-business"]
        self.assertEqual((f4f.urls, f4f.enabled, f4f.loader),
                         (("https://www.fly4free.com/flight-deals/business-class/feed/",), False,
                          None))
        pf = PRESETS_BY_ID["premium-flights"].urls
        self.assertEqual(pf[0], "https://premium-flights.com/feed/")      # ohne www (sonst 301)
        self.assertTrue(all("www." not in u for u in pf))
        self.assertEqual({u.split("/tag/")[1].split("/")[0] for u in pf if "/tag/" in u},
                         {"china", "pek", "shanghai"})
        rt = PRESETS_BY_ID["reisetopia"]
        self.assertTrue(rt.enabled)
        self.assertEqual(len(rt.urls), 3)
        self.assertEqual(rt.lane, "normal")      # Seiten sind 0,5–0,65 MB groß
        self.assertTrue(PRESETS_BY_ID["yhbu"].enabled)

    def test_labels_and_preset_for_url(self):
        for p in FEED_PRESETS:
            self.assertIn(p.source, SOURCE_LABELS, p.id)
            self.assertNotEqual(feed.source_label(p.source), p.source)
        self.assertEqual(feed.source_label("feed:secretflying"), "Secret Flying")
        self.assertEqual(feed.preset_for_url(
            "https://www.premium-flights.com/tag/zurich/feed/").id, "premium-flights")
        self.assertEqual(feed.preset_for_url(
            "https://youhavebeenupgraded.boardingarea.com/feed/").id, "yhbu")

    def test_missing_loader_module_is_a_source_error(self):
        # Die Loader-Module kommen aus anderen Paketen: fehlt eins, scheitert nur dieses Preset
        a = alert(origins=["@DE"], destinations=["BKK"], cabin="economy", max_price=600,
                  feed_keywords=[["Bangkok"]])
        missing = ImportError("No module named 'dealmonitor.sources.secretflying'")
        with mock.patch.object(feed.importlib, "import_module", side_effect=missing):
            src, offers = fetch(["secretflying", "travel-dealz-rest", "fly4free"], a)
            items = src.load()
        self.assertTrue(items)                                   # fly4free lief normal
        self.assertIn("Secret Flying", src.load_errors)
        self.assertIn("Travel-Dealz (schnell)", src.load_errors)
        self.assertEqual(src.unreliable_sources(),
                         {"feed:secretflying", "feed:travel-dealz-rest"})
        self.assertIn("nicht verfügbar", src.stats["feed:secretflying"]["error"])
        self.assertIsNotNone(src.incomplete)
        self.assertEqual({o.source for o in offers} - {"feed:fly4free"}, set())

    def test_fast_lane_and_dedup_order(self):
        src = FeedSource(presets=["travel-dealz", "travel-dealz-rest", "secretflying",
                                  "reisetopia"], lanes={"fast"})
        self.assertEqual([p.id for p in src.presets], ["travel-dealz-rest", "secretflying"])
        # alle Takte: schnelle Quellen zuerst -> derselbe Artikel behält ihre Quelle
        src = FeedSource(presets=["travel-dealz", "travel-dealz-rest"], search_enabled=False)
        self.assertEqual(src._feed_jobs()[0][1].id, "travel-dealz-rest")
        rest_item = {"title": "Frankfurt – Peking ab 399€", "link": "https://travel-dealz.de/x/",
                     "price_eur": 399.0, "origin_codes": ["FRA"], "categories": ["Flüge"]}
        rss = (b'<rss><channel><item><title>Frankfurt - Peking ab 399\xe2\x82\xac</title>'
               b'<link>https://travel-dealz.de/x/?utm_source=rss</link>'
               b'<category>Fl\xc3\xbcge</category></item></channel></rss>')
        with mock.patch.object(feed, "load_loader", return_value=lambda **kw: [rest_item]), \
                mock.patch.object(http, "request", return_value=rss):
            items = src.load()
        self.assertEqual([it["source"] for it in items], ["feed:travel-dealz-rest"])


class ReisetopiaTest(unittest.TestCase):
    PRICES = {   # Infobox-Preis, nicht die erste €-Zahl im Text
        "Frankfurt nach São Paulo": (2019, {"FRA"}, "home"),             # LATAM
        "Berlin nach Salala": (1819, {"BER"}, "home"),                   # Qatar
        "Condor Business Class von Frankfurt nach Sansibar": (2129, None, "home"),  # "unter 2.200"
        "Frankfurt nach Kathmandu": (1759, {"FRA"}, "home"),             # Air India
        "Etihad Business Class von Frankfurt nach Singapur": (2269, {"FRA"}, "home"),  # Gebühren 520/130
        "Barcelona nach Shanghai": (1919, {"BCN"}, "foreign"),           # Nachbar-Hub
        # Bauplan: 2.399 € - am 05.11. stand im Artikel 2.252 €
        "nach Calgary": (2252, None, "home"),
        "First Class von Mailand nach Phuket": (3249, {"MIL"}, "foreign"),
        "von Prag nach Kasachstan": (1619, {"PRG"}, "foreign"),
    }

    @at_fixture_time
    def test_prices_routes_and_cabins(self):
        items = load("reisetopia")
        for part, (price, codes, origin) in self.PRICES.items():
            info = analyze(by_title(items, part))
            self.assertTrue(info.flight, part)
            self.assertEqual((info.price, info.origin), (price, origin), part)
            self.assertEqual(info.cabin, "first" if "First" in part else "business", part)
            if codes:
                self.assertEqual(info.origin_codes, codes, part)
        # "Business Class von Deutschland nach Sansibar": alle deutschen Flughäfen
        self.assertIn("FRA", analyze(by_title(items, "Sansibar")).origin_codes)
        self.assertEqual(analyze(by_title(items, "Shanghai")).dest_codes, {"PVG", "SHA"})
        self.assertEqual(sum(analyze(it).flight for it in items), len(self.PRICES))

    @at_fixture_time
    def test_no_deals_from_news_hotels_miles(self):
        items = load("reisetopia")
        for part in ("Helvetic Airways", "Wyndham", "Kreditkarte", "Ab 45.000 Meilen"):
            info = analyze(by_title(items, part))
            self.assertTrue(not info.flight or info.price is None, part)

    def test_booking_deadline(self):
        items = load("reisetopia")
        latam = by_title(items, "São Paulo")
        self.assertEqual(latam["expires"], datetime(2026, 11, 13, tzinfo=timezone.utc))
        self.assertEqual(by_title(items, "Salala")["expires"],
                         datetime(2026, 12, 1, tzinfo=timezone.utc))      # "Bis zum 30. November"
        self.assertNotIn("expires", by_title(items, "Mailand"))          # "Schnellstmöglich"
        ktm = by_title(items, "Kathmandu")                                # "Bis 5. November 2026"
        self.assertFalse(_is_expired(ktm, NOW))
        self.assertTrue(_is_expired(ktm, datetime(2026, 11, 6, 1, tzinfo=timezone.utc)))
        with mock.patch.object(feed, "is_expired",
                               lambda item, now=None: _is_expired(item, datetime(2026, 11, 7,
                                                                                 tzinfo=timezone.utc))):
            self.assertFalse(analyze(ktm).flight)

    @at_fixture_time
    def test_alerts(self):
        src, offers = fetch(["reisetopia"], alert(destinations=["KTM"], max_price=2000,
                                                  feed_keywords=[["Kathmandu"], ["Nepal"]]))
        self.assertEqual([(o.source, o.price, o.cabin) for o in offers],
                         [("feed:reisetopia", 1759, "business")])
        self.assertIsNone(src.load_errors)
        self.assertEqual(src.stats["feed:reisetopia"]["parsed"], 3 * 9)   # 3 URLs, gleiche Fixture
        # Calgary ab Deutschland passt nicht zu einem Alarm ab Zürich
        _, offers = fetch(["reisetopia"], alert(origins=["ZRH"], destinations=["YYC"],
                                                feed_keywords=[["Calgary"]]))
        self.assertEqual(offers, [])
        # Barcelona–Shanghai ist kein Deal ab Deutschland
        _, offers = fetch(["reisetopia"], alert(origins=["@DE"], destinations=["@CN"],
                                                max_price=1999))
        self.assertEqual(offers, [])

    def test_without_article_text_no_price(self):
        # alte Fixture ohne content:encoded: keine erfundenen Preise aus dem Titel
        items = load("reisetopia", "reisetopia.xml")
        self.assertTrue(items)
        self.assertTrue(all(analyze(it).price is None for it in items))
        self.assertTrue(all("content_text" not in it for it in items))

    def test_content_text_is_plain_and_capped(self):
        items = load("reisetopia")
        text = by_title(items, "São Paulo")["content_text"]
        self.assertIn("Buchbar ab nur 2.019 Euro pro Person", text)
        self.assertNotIn("<", text)
        self.assertLessEqual(len(text), CONTENT_CHARS)


class InfoboxTest(unittest.TestCase):
    RX = PRESETS_BY_ID["reisetopia"].profile.price_regex

    def test_place_codes(self):
        self.assertEqual(place_codes("Frankfurt und München"), {"FRA", "MUC"})
        self.assertTrue({"FRA", "MUC", "HAM"} <= place_codes("Deutschland"))
        self.assertIn("ZRH", place_codes("Schweiz"))
        self.assertEqual(place_codes("Nirgendwo"), set())
        self.assertEqual(place_codes(""), set())

    def test_read_infobox(self):
        text = ("Mit LATAM in der Business Class von LATAM von Frankfurt nach São Paulo reisen. "
                "Business Class von Frankfurt nach São Paulo Buchbar ab nur 2.019 Euro pro Person "
                "Reisezeitraum: Offen Buchungszeitraum: Bis 12.11.2026 Fluggesellschaft: LATAM")
        box = read_infobox(text, self.RX)
        self.assertEqual((box["origin_codes"], box["dest_codes"]), ({"FRA"}, {"SAO"}))
        self.assertEqual(box["expires"], datetime(2026, 11, 13, tzinfo=timezone.utc))
        # unbekannte Form -> nichts erfinden, nie abstürzen
        self.assertEqual(read_infobox("Buchbar ab 45.075 Meilen", self.RX), {})
        self.assertEqual(read_infobox("", None), {})
        self.assertEqual(read_infobox(text, "(kaputt"), {})
        odd = text.replace("Bis 12.11.2026", "Bis 31. Februar 2027")
        self.assertNotIn("expires", read_infobox(odd, self.RX))

    def test_regex_price(self):
        prof = PRESETS_BY_ID["reisetopia"].profile
        self.assertEqual(regex_price("Buchbar ab 1.619 Euro pro Person", prof), 1619)
        self.assertIsNone(regex_price("ab 1.619 Euro", prof))
        self.assertIsNone(regex_price("Buchbar ab 0 Euro pro Person", prof))
        self.assertIsNone(regex_price("x", Profile(price_regex="(kaputt")))
        self.assertIsNone(regex_price("ab 99 Euro", Profile(price_regex=r"ab \d+ Euro")))


class YhbuTest(unittest.TestCase):
    def test_titles(self):
        items = load("yhbu")
        infos = [analyze(it) for it in items]
        priced = [i for i in infos if i.flight and i.price is not None]
        self.assertGreaterEqual(len(priced), 40)
        self.assertTrue(all(i.cabin in ("business", "first") for i in priced))
        hainan = analyze(by_title(items, "Hainan Business Budapest"))
        self.assertEqual((hainan.price, hainan.cabin, hainan.origin, hainan.origin_codes),
                         (1490, "business", "foreign", {"BUD"}))
        self.assertTrue({"BJS", "SHA"} <= hainan.dest_codes)
        first = analyze(by_title(items, "Etihad First Tunis"))
        self.assertEqual((first.cabin, first.price, first.oneway), ("first", 1380, True))
        apartment = analyze(by_title(items, "A321 First Paris"))      # "First Apartment" ist ein Sitz
        self.assertEqual((apartment.flight, apartment.cabin, apartment.price, apartment.origin_codes),
                         (True, "first", 2900, {"PAR"}))
        miat = analyze(by_title(items, "MIAT Business Frankfurt – Peking"))
        self.assertEqual((miat.origin, miat.origin_codes, miat.price), ("home", {"FRA"}, 1330))
        self.assertIsNone(analyze(by_title(items, "Malaysia Airlines Business Sale")).price)
        self.assertTrue(non_flight_title("Apartment in Rom ab 50€"))

    def test_alert(self):
        _, offers = fetch(["yhbu"], alert(max_price=1999, feed_keywords=[["Peking"]]))
        self.assertEqual([(o.source, o.price) for o in offers], [("feed:yhbu", 1330)])
        # ab Deutschland nur Frankfurt–Peking; Budapest/Rom/Oslo–China sind Nachbar-Hubs
        # (eigenes Paket), "Qatar Business China – Europa" fliegt nach Europa
        _, offers = fetch(["yhbu"], alert(origins=["@DE"], destinations=["@CN"], max_price=1999,
                                          feed_keywords=[["China"], ["Peking"], ["Shanghai"]]))
        self.assertEqual([o.price for o in offers], [1330])


class PremiumFlightsTagsTest(unittest.TestCase):
    def test_china_tag_feed(self):
        items = load("premium-flights", "premium-flights-china.xml")
        titles = [it["title"] for it in items]
        # "Expired" in den Kategorien: abgelaufen, wird nicht gelesen
        self.assertFalse(any("Rome → Guangzhou" in t for t in titles))
        oslo = analyze(by_title(items, "Oslo → Beijing"))
        self.assertEqual((oslo.price, oslo.cabin, oslo.origin, oslo.origin_codes),
                         (1875, "business", "foreign", {"OSL"}))
        # derselbe Artikel als "Active" (Kategorien sonst original): ROM–CAN 1.560 € Business
        raw = (FIX / "premium-flights-china.xml").read_text(encoding="utf-8")
        start = raw.index("<title>Rome → Guangzhou")
        block = raw[start:raw.index("</item>", start)]
        cats = [c for c in feed.re.findall(r"CDATA\[([^\]]+)\]", block) if c != "Expired"]
        info = analyze({"title": "Rome → Guangzhou Business Class Round Trip €1560",
                        "categories": ["Active", *cats],
                        "link": "https://premium-flights.com/rome-guangzhou/"},
                       PRESETS_BY_ID["premium-flights"].profile)
        self.assertEqual((info.flight, info.price, info.cabin, info.origin_codes),
                         (True, 1560, "business", {"ROM"}))

    def test_pek_tag_feed_finds_frankfurt_beijing(self):
        src, offers = fetch(["premium-flights"], alert(max_price=1999, feed_keywords=[["Beijing"]]))
        self.assertEqual([(o.source, o.price, o.cabin) for o in offers],
                         [("feed:premium-flights", 1810, "business")])
        self.assertIsNone(src.load_errors)
        self.assertEqual(src.stats["feed:premium-flights"]["error"], None)

    def test_fly4free_business(self):
        items = load("fly4free-business")
        flights = [analyze(it) for it in items if analyze(it).flight]
        self.assertTrue(flights)
        self.assertTrue(all(i.cabin == "business" for i in flights))
        kul = analyze(by_title(items, "Kuala Lumpur"))
        self.assertEqual((kul.price, kul.origin), (1129, "home"))
        self.assertTrue(is_flight_item("x", ["business class"]))


if __name__ == "__main__":
    unittest.main()
