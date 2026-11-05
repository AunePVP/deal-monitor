"""Deal-Feeds: Presets, Parser-Profile, Preise, Abflugort, Stichwörter, Dedup, Suche.

Die Fixtures unter tests/fixtures/feeds/ sind gekürzte Ausschnitte der echten
Feeds (04.11.2026, ≤ 8 Artikel je Feed, Originaltitel).
"""

import unittest
from pathlib import Path
from unittest import mock

from dealmonitor import http, regions
from dealmonitor.models import Alert
from dealmonitor.sources import SourceError
from dealmonitor.sources.feed import (
    CUSTOM_SOURCE, FEED_PRESETS, PRESETS_BY_ID, SOURCE_LABELS, TRAVEL_DEALZ_SEARCH, FeedSource,
    analyze, auto_keywords, default_preset_ids, detect_cabin, detect_origin, is_flight_item,
    matches, normalize_link, origin_codes, parse_price, preset_for_url, search_terms,
    source_label,
)

FIX = Path(__file__).parent / "fixtures" / "feeds"
OLD = 36500   # Fixtures haben echte Datumsangaben -> Altersgrenze praktisch aus


def alert(**kw):
    base = dict(id="t", name="Test", origins=["ZRH"], destinations=["HAM"], max_price=110,
                baseline_price=190)
    base.update(kw)
    return Alert(**base)


def fixture_for(url: str) -> bytes:
    """Jede Preset-URL -> passende Fixture-Datei (alle Travel-Dealz-URLs -> travel-dealz.xml)."""
    if "feed=rss2" in url:
        return (FIX / "travel-dealz-suche.xml").read_bytes()
    for p in FEED_PRESETS:
        if url in p.urls:
            return (FIX / f"{p.id}.xml").read_bytes()
    raise http.HttpError(f"HTTP 404 für {url}", 404)


def source(presets, **kw) -> FeedSource:
    kw.setdefault("max_age_days", OLD)
    return FeedSource(presets=presets, **kw)


def fetch(src: FeedSource, a: Alert):
    with mock.patch.object(http, "request", side_effect=lambda url, **kw: fixture_for(url)):
        return src.fetch(a)


class PresetTest(unittest.TestCase):
    def test_presets_and_defaults(self):
        ids = [p.id for p in FEED_PRESETS]
        # Runde 2: neue Quellen (Loader, YHBU, fly4free Business), reisetopia jetzt an
        self.assertEqual(ids, ["travel-dealz", "travel-dealz-rest", "travel-dealz-suche",
                               "secretflying", "mydealz-fluege", "fly4free", "fly4free-business",
                               "premium-flights", "yhbu", "reisetopia", "urlaubspiraten",
                               "errorfarealerts"])
        self.assertEqual(default_preset_ids(), ["travel-dealz", "travel-dealz-rest",
                                                "travel-dealz-suche", "secretflying",
                                                "mydealz-fluege", "fly4free", "premium-flights",
                                                "yhbu", "reisetopia"])
        self.assertEqual(len(PRESETS_BY_ID["travel-dealz"].urls), 5)
        self.assertIn("https://travel-dealz.de/ticker/feed/", PRESETS_BY_ID["travel-dealz"].urls)
        for p in FEED_PRESETS:
            self.assertIn(p.source, SOURCE_LABELS)
            self.assertEqual(p.source, f"feed:{p.id}")
            self.assertIn(p.language, ("de", "en"))
        self.assertEqual(source_label("feed:mydealz-fluege"), "mydealz")
        self.assertEqual(source_label("travel-dealz-feed"), "Travel-Dealz-Artikel")
        self.assertEqual(source_label("unbekannt"), "unbekannt")

    def test_preset_for_url(self):
        self.assertEqual(preset_for_url("https://travel-dealz.de/kategorie/economy-class/feed/").id,
                         "travel-dealz")
        self.assertEqual(preset_for_url("https://mydealz.de/rss/gruppe/reisen").id, "mydealz-fluege")
        self.assertIsNone(preset_for_url("https://example.org/feed"))

    def test_unknown_preset_rejected(self):
        with self.assertRaises(ValueError):
            FeedSource(presets=["travel-dealz", "gibtsnicht"])

    def test_offer_sources(self):
        src = source(["travel-dealz", "travel-dealz-suche", "fly4free"],
                     extra_feeds=["https://example.org/feed", "https://travel-dealz.de/x/feed/"])
        self.assertEqual(src.offer_sources, ["feed:travel-dealz", "feed:fly4free",
                                             "feed:travel-dealz-suche", CUSTOM_SOURCE])
        self.assertTrue(src.search_enabled)
        self.assertFalse(source(["travel-dealz"]).search_enabled)
        self.assertTrue(source(["travel-dealz"], search_enabled=True).search_enabled)


class PriceTest(unittest.TestCase):
    def test_german_formats(self):
        self.assertEqual(parse_price("Peking ab 1.799€"), 1799)
        self.assertEqual(parse_price("ab 89,- €"), 89)
        self.assertEqual(parse_price("Hamburg ab 39,99€"), 39.99)
        self.assertEqual(parse_price("599 Euro"), 599)
        self.assertEqual(parse_price("1910€"), 1910)

    def test_english_formats(self):
        self.assertEqual(parse_price("Direct flights from Amsterdam to Bonaire from €394", "en"), 394)
        self.assertEqual(parse_price("flights for €1,299 return", "en"), 1299)
        self.assertEqual(parse_price("Business Class Round Trip from 1910€", "en"), 1910)
        self.assertEqual(parse_price("Round Trip 1680€ (Luxury Deal, Save up to €1000+)", "en"), 1680)

    def test_foreign_currency_gives_no_price(self):
        self.assertIsNone(parse_price("Houston → Kilimanjaro Business Class from $3,650", "en"))
        self.assertIsNone(parse_price("Vancouver → Hong Kong Business Class from US$2,580", "en"))
        self.assertIsNone(parse_price("London → Johannesburg Business Class from £1610", "en"))
        self.assertIsNone(parse_price("Swiss-Passagiere können 4,60 CHF zurückfordern"))
        self.assertEqual(parse_price("ab 29 CHF / 31€ mit Halbtax"), 31)

    def test_discounts_are_not_prices(self):
        self.assertIsNone(parse_price("Nur für kurze Zeit: Sparen Sie 40 € auf Flüge in die USA"))
        self.assertIsNone(parse_price("Lufthansa 40€ Rabatt"))
        self.assertIsNone(parse_price("400 Euro Bordguthaben bei Sea Cloud"))
        self.assertIsNone(parse_price("€50 off all flights", "en"))
        self.assertEqual(parse_price("Lufthansa Gutschein: 40€ Rabatt auf Langstrecken "
                                     "(z.B. Seattle ab 529€ inkl. Gepäck)"), 529)

    def test_mydealz_description_prefers_ab(self):
        text = "297€ - SkyExplorer Spitzen Preise für Flüge in die USA Schon ab 298€ 389€ könnt ihr"
        self.assertEqual(parse_price(text), 298)


class FlightItemTest(unittest.TestCase):
    def test_negative_categories(self):
        self.assertFalse(is_flight_item("Costa Toscana: 7 Nächte ab 399€", ["Kreuzfahrten"]))
        self.assertFalse(is_flight_item("Schweiz ↔ Paris mit dem TGV Lyria ab 20€", ["Bahn", "TGV"]))
        self.assertFalse(is_flight_item("Cheap 7-day cruise aboard MSC Euribia from €499", ["Europe", "cruise"]))
        self.assertFalse(is_flight_item("Well-rated resort on Koh Tao for €65/double", ["Europe", "Hotel"]))
        self.assertFalse(is_flight_item("SWISS Senses Routes", ["Airline Tips, Stories & Guides"]))
        self.assertFalse(is_flight_item("Flying Blue Promo Awards: Business Class nach New York",
                                        ["Meilen, Punkte & Status", "Flying Blue"]))
        self.assertFalse(is_flight_item("Condor startet tägliche Strecke", ["Aviation", "News"]))
        self.assertFalse(is_flight_item("Bewertung: SAS Business Class im A321LR",
                                        ["Bewertungen", "Business Class"]))

    def test_negative_titles_without_categories(self):
        self.assertFalse(is_flight_item("AIDA Traumstart um 12: 14 Nächte Karibik inkl. Flug ab 1849€", []))
        self.assertFalse(is_flight_item("Madeira: 7 Nächte im 3*-Hotel inkl. Direktflug 539€ p.P.", []))
        self.assertFalse(is_flight_item("Lufthansa 40€ Rabatt", [],
                                        "https://www.mydealz.de/gutscheine/lufthansa-40eur-rabatt"))
        self.assertFalse(is_flight_item("Im Winter nach Prag", [], "https://www.urlaubspiraten.de/hotels/x"))

    def test_positive(self):
        self.assertTrue(is_flight_item("Cheap flights from Frankfurt to Phuket for €472",
                                       ["Europe", "cheap flights from frankfurt"]))
        self.assertTrue(is_flight_item("Frankfurt → Bangkok Business Class Round Trip 1210€",
                                       ["Active", "Cheap Business Class flights", "From Europe"]))
        self.assertTrue(is_flight_item("Lufthansa Gutschein: 40€ Rabatt (Seattle ab 529€)", ["Flüge"],
                                       "https://travel-dealz.de/gutschein/lufthansa/"))
        self.assertTrue(is_flight_item("Flüge nach Costa Rica ab 399€", []))
        self.assertTrue(is_flight_item("Sale bei Ryanair", [], "https://www.urlaubspiraten.de/fluege/x"))

    def test_cabin_english(self):
        self.assertEqual(detect_cabin("Stockholm → USA FIRST Class Round Trip from 3375€"), "first")
        self.assertEqual(detect_cabin("London: Eurowings BIZClass ab 240€"), "business")


class OriginTest(unittest.TestCase):
    def test_home(self):
        for title, lang in [
            ("Peking: Business Class von Frankfurt ab 1.123€", "de"),
            ("Air China: Deutschland – Peking ab 429€", "de"),
            ("Flüge nach Venedig inkl. Rückflug von Berlin und Brüssel", "de"),
            ("Flüge nach Mailand/Bergamo mit Malta Air von Berlin, Köln, Weeze", "de"),
            ("Blind Booking bei Austrian Airlines: Europaflüge von Wien ab 99€", "de"),
            ("Hamburg – Zürich ab 79€", "de"),
            ("Schnell sein: Von Frankfurt nach Kathmandu", "de"),
            ("Cheap full-service flights from Frankfurt to Phuket for €472", "en"),
            ("CHEAP Flights from several German cities to New York from €302", "en"),
            ("Frankfurt → Bangkok Business Class Round Trip 1210€", "en"),
        ]:
            self.assertEqual(detect_origin(title, lang), "home", title)

    def test_foreign(self):
        for title, lang in [
            ("Last Minute: Bonaire ab 416€ mit Corendon von Amsterdam (Handgepäck)", "de"),
            ("China Eastern: Gabelflüge Amsterdam – Tokio – Frankfurt ab 426€", "de"),
            ("Qatar Airways: Gabelflüge nach Down Under ab 745€, z.B. Warschau – Auckland – München", "de"),
            ("Belo Horizonte ab 1.427€ in der GOL Business Class ab Lissabon", "de"),
            ("Singapur ab 1.368€ in der Gulf Air Business Class von Mailand & Rom", "de"),
            ("New York ab 248€ mit Avianca von Spanien inkl. Gepäck", "de"),
            ("Oneway: SAS Business Class Thailand → Frankfurt ab 806€", "de"),
            ("Direct flights from Amsterdam to Bonaire from €394", "en"),
            ("SAS flights from Stockholm & Copenhagen from €514 return", "en"),
            ("Copenhagen → Mumbai Business Class Round Trip from 1910€", "en"),
            ("Canada → Lisbon Business Class Round Trip from US$2350 (Nonstop)", "en"),
            ("Flights from many Polish cities to South Korea from €562", "en"),
        ]:
            self.assertEqual(detect_origin(title, lang), "foreign", title)

    def test_unknown(self):
        for title, lang in [
            ("Business Class nach China: Peking & Shanghai ab 1.799€", "de"),
            ("Erbil, Irak ab 390€ in der Eurowings „Business Class“", "de"),
            ("Condor: Oneway-Flüge nach San José del Cabo (Mexiko) ab 210€ (Abflug ab Januar)", "de"),
            ("Flights from many European cities to Miami from €347", "en"),
        ]:
            self.assertEqual(detect_origin(title, lang), "unknown", title)

    def test_origin_codes(self):
        self.assertEqual(origin_codes("Bonaire ab 416€ von Amsterdam"), {"AMS"})
        self.assertEqual(origin_codes("New York ab 306€ von Berlin, München & Frankfurt"),
                         {"BER", "MUC", "FRA"})
        self.assertEqual(origin_codes("New York ab 248€ von Spanien"), set())

    def test_profile_default_origin(self):
        item = {"title": "Business Class nach China ab 1.799€", "categories": ["Business Class"],
                "link": "https://travel-dealz.de/deal/x/"}
        self.assertEqual(analyze(item, PRESETS_BY_ID["travel-dealz"].profile).origin, "home")
        self.assertEqual(analyze(item, PRESETS_BY_ID["fly4free"].profile).origin, "unknown")


class KeywordTest(unittest.TestCase):
    def test_synonyms(self):
        a = alert(origins=["@DE"], destinations=["@CN_MAJOR"], max_price=499,
                  feed_keywords=[["Peking"], ["München", "Shanghai"]])
        self.assertTrue(matches(a, "Cheap flights from Munich to Beijing from €420"))
        self.assertTrue(matches(a, "Munich – Shanghai for €399"))
        self.assertFalse(matches(a, "Cheap flights from Prague to Tokyo"))
        z = alert(feed_keywords=[["Zürich", "Hamburg"]])
        self.assertTrue(matches(z, "Zurich - Hamburg ab 59€"))
        self.assertTrue(matches(z, "Swiss: ZRH – HAM ab 79€"))      # IATA-Codes
        self.assertFalse(matches(z, "Swiss: zrh – ham ab 79€"))     # nur Großschreibung

    def test_departure_cities_are_not_a_destination(self):
        z = alert(feed_keywords=[["Zürich", "Hamburg"]])
        t = ("Direktflüge nach Thailand / Bangkok inkl. Rückflug nonstop von Frankfurt | Berlin, "
             "Hamburg, Zürich, München mit 1 Stop (Nov - Dez / Condor)")
        self.assertFalse(matches(z, t, title=t))
        self.assertFalse(matches(z, "Flüge nach Bangkok von Hamburg und Zürich ab 499€"))
        self.assertTrue(matches(z, "Hamburg – Zürich ab 79€"))
        self.assertTrue(matches(z, "Swiss: Hamburg – Zürich – Hamburg ab 79€"))
        self.assertTrue(matches(z, "Flüge nach Hamburg ab Zürich und Genf"))
        # Ziel-Stichwort mit genanntem Abflug bleibt ein Treffer
        b = alert(origins=["@DE"], destinations=["BKK"], max_price=600, feed_keywords=[["Bangkok"]])
        self.assertTrue(matches(b, "Flüge nach Bangkok von Hamburg und Zürich ab 499€"))

    def test_airline_name_is_not_a_destination(self):
        a = alert(origins=["@DE"], destinations=["@CN"], max_price=499,
                  feed_keywords=[["China"], ["Shanghai"]])
        self.assertFalse(matches(a, "China Eastern: Gabelflüge Amsterdam – Tokio – Frankfurt ab 426€"))
        self.assertFalse(matches(a, "Shanghai Airlines fliegt neu nach Budapest"))
        self.assertTrue(matches(a, "Shanghai ab 1.486€ in der China Southern Business Class"))

    def test_auto_keywords(self):
        self.assertEqual(auto_keywords(alert()), [["Zürich", "Hamburg"]])
        self.assertEqual(auto_keywords(alert(), origin_required=False), [["Hamburg"]])
        groups = auto_keywords(alert(origins=["@DE"], destinations=["@CN_MAJOR"], max_price=499))
        self.assertIn(["Deutschland", "China"], groups)
        self.assertIn(["Frankfurt", "Peking"], groups)
        a = alert(origins=["@DE"], destinations=["@CN_MAJOR"], max_price=499)
        self.assertTrue(matches(a, "Air China: Deutschland – Peking ab 429€"))
        self.assertTrue(matches(a, "Cheap flights from several German cities to Shanghai from €399"))
        self.assertFalse(matches(a, "Business Class nach China ab 1.799€"))   # Von-Wort fehlt

    def test_search_terms(self):
        a = alert(feed_keywords=[["Zürich", "Hamburg"], ["ZRH", "HAM"], ["Rom"], ["Peking"]])
        self.assertEqual(search_terms(a), ["Zürich", "Peking"])    # ≥ 4 Zeichen, keine Codes
        self.assertEqual(search_terms(alert(origins=["@DE"], destinations=["@CN_MAJOR"],
                                            max_price=499)),
                         ["Peking", "Shanghai", "Guangzhou", "Shenzhen", "Chengdu", "Hangzhou",
                          "China"])


class LinkTest(unittest.TestCase):
    def test_normalize_link(self):
        self.assertEqual(normalize_link("https://travel-dealz.de/ticker/486604/?utm_source=feed"
                                        "&utm_medium=rss&utm_campaign=rss-feed#top"),
                         "https://travel-dealz.de/ticker/486604")
        self.assertEqual(normalize_link("HTTPS://Travel-Dealz.de/deal/x/"),
                         "https://travel-dealz.de/deal/x")
        self.assertEqual(normalize_link("https://x.de/a?b=1&utm_x=2"), "https://x.de/a?b=1")
        self.assertEqual(normalize_link(""), "")


class TravelDealzFixtureTest(unittest.TestCase):
    def test_dedup_across_feeds(self):
        src = source(["travel-dealz"])
        with mock.patch.object(http, "request", side_effect=lambda url, **kw: fixture_for(url)) as m:
            items = src.load()
        self.assertEqual(m.call_count, 5)              # 5 URLs, alle liefern dieselben Artikel
        self.assertEqual(len(items), 8)                 # ... per Link dedupliziert
        self.assertTrue(all(it["source"] == "feed:travel-dealz" for it in items))
        self.assertNotIn("utm_", items[0]["key"])

    def test_business_deal_matched(self):
        a = alert(id="cn-biz", origins=["@DE_HUBS"], destinations=["@CN_MAJOR"], cabin="business",
                  max_price=1999, feed_keywords=[["Peking", "Business"]])
        offers = fetch(source(["travel-dealz"]), a)
        self.assertEqual([o.title[:30] for o in offers], ["Peking: Business Class von Fra"])
        o = offers[0]
        self.assertEqual((o.source, o.price, o.cabin, o.roundtrip), ("feed:travel-dealz", 1123, "business", True))
        self.assertEqual(o.link, "https://travel-dealz.de/deal/turkmenistan-airlines-business-peking/"
                                 "?utm_source=feed&utm_medium=rss&utm_campaign=rss-feed")
        self.assertIn("https://travel-dealz.de/deal/turkmenistan-airlines-business-peking",
                      source(["travel-dealz"]).matched_links or {o.link.split("?")[0].rstrip("/")})

    def test_foreign_departure_rejected_for_dach_alert(self):
        a = alert(origins=["@DE"], destinations=["BON"], max_price=500, feed_keywords=[["Bonaire"]])
        self.assertEqual(fetch(source(["travel-dealz"]), a), [])
        ams = alert(origins=["AMS"], destinations=["BON"], max_price=500, feed_keywords=[["Bonaire"]])
        offers = fetch(source(["travel-dealz"]), ams)
        self.assertEqual([o.price for o in offers], [416])

    def test_non_flight_items_never_match(self):
        a = alert(origins=["@DE"], destinations=["PAR"], max_price=500,
                  feed_keywords=[["Paris"], ["Kreuzfahrt"], ["Savona"], ["Lufthansa"]])
        titles = [o.title for o in fetch(source(["travel-dealz"]), a)]
        self.assertFalse(any("TGV" in t or "Costa" in t for t in titles))
        # Der Lufthansa-Gutschein ist ein Flug-Deal (Kategorie "Flüge"), Preis ist das Beispiel 529€
        self.assertEqual([(o.price) for o in fetch(source(["travel-dealz"]), a) if "Lufthansa" in o.title], [529])

    def test_airline_name_as_destination_rejected(self):
        a = alert(origins=["@DE"], destinations=["@CN"], max_price=499, feed_keywords=[["China"]])
        self.assertEqual(fetch(source(["travel-dealz"]), a), [])   # "China Eastern: … Amsterdam – Tokio"

    def test_auto_keywords_without_feed_keywords(self):
        a = alert(id="nyc", origins=["@DE"], destinations=["NYC"], max_price=350)
        offers = fetch(source(["travel-dealz"]), a)
        self.assertEqual([o.price for o in offers], [306])       # "New York ab 306€ … von Berlin"
        self.assertEqual(offers[0].origin, "")                    # mehrere Abflughäfen -> offen
        self.assertEqual(offers[0].destination, "NYC")

    def test_legacy_constructor(self):
        src = FeedSource(max_age_days=OLD)
        self.assertEqual(src.name, "travel-dealz-feed")
        self.assertEqual(src.offer_sources, ["travel-dealz-feed"])
        a = alert(id="cn-biz", origins=["@DE_HUBS"], destinations=["@CN_MAJOR"], cabin="business",
                  max_price=1999, feed_keywords=[["Peking", "Business"]])
        with mock.patch.object(http, "request", return_value=(FIX / "travel-dealz.xml").read_bytes()):
            offers = src.fetch(a)
        self.assertEqual([o.source for o in offers], ["travel-dealz-feed"])
        self.assertEqual(src.fetch(alert()), [])                  # ohne Stichwörter wie bisher nichts


class MydealzFixtureTest(unittest.TestCase):
    def test_price_from_description(self):
        a = alert(id="nyc", origins=["@DE"], destinations=["NYC"], max_price=350,
                  feed_keywords=[["New York"]])
        offers = fetch(source(["mydealz-fluege"]), a)
        self.assertEqual(len(offers), 1)
        o = offers[0]
        self.assertEqual((o.source, o.price, o.roundtrip), ("feed:mydealz-fluege", 298, True))
        self.assertTrue(o.title.startswith("Flüge USA / New York von München"))

    def test_voucher_hotel_cruise_rejected(self):
        a = alert(origins=["@DE"], destinations=["FNC"], max_price=2000,
                  feed_keywords=[["Lufthansa"], ["Madeira"], ["AIDA"], ["Karibik"]])
        self.assertEqual(fetch(source(["mydealz-fluege"]), a), [])

    def test_departure_list_does_not_match_route_alert(self):
        # "Direktflüge nach Bangkok … von Frankfurt | Berlin, Hamburg, Zürich" ist kein ZRH-HAM-Deal
        for a in (alert(feed_keywords=[["Zürich", "Hamburg"]]), alert(),
                  alert(origins=["ZRH", "HAM"], destinations=["HAM", "ZRH"], trip="oneway", max_price=45)):
            self.assertEqual(fetch(source(["mydealz-fluege"]), a), [])
        bkk = alert(origins=["@DACH"], destinations=["BKK"], max_price=700)
        self.assertEqual([o.price for o in fetch(source(["mydealz-fluege"]), bkk)], [617])

    def test_deal_price_from_description_start(self):
        # "Round the World … ab 898€": tiefer in der Beschreibung stehen Teilpreise ("Paris für 40€")
        a = alert(origins=["@DE"], destinations=["HAN"], max_price=1000, feed_keywords=[["Hanoi"]])
        self.assertEqual([o.price for o in fetch(source(["mydealz-fluege"]), a)], [898])
        item = {"title": "Round the World: Hanoi ab 898€", "categories": [], "link": "https://www.mydealz.de/deals/x",
                "description": "898€ Coole Weltreise mit günstigen Flügen. " + "Blabla " * 40
                               + "Teil 1: Frankfurt – Paris für 40€"}
        self.assertEqual(analyze(item, PRESETS_BY_ID["mydealz-fluege"].profile).price, 898)

    def test_oneway_promo_detected(self):
        a = alert(origins=["@DE"], destinations=["LON"], trip="oneway", max_price=50,
                  feed_keywords=[["London"]])
        offers = fetch(source(["mydealz-fluege"]), a)
        self.assertEqual([(o.price, o.roundtrip) for o in offers], [(14.99, False)])


class EnglishFixtureTest(unittest.TestCase):
    def test_fly4free_home_and_foreign(self):
        a = alert(id="nyc", origins=["@DE"], destinations=["NYC"], max_price=400)
        offers = fetch(source(["fly4free"]), a)
        self.assertEqual(sorted(o.price for o in offers), [302, 382])
        self.assertTrue(all(o.source == "feed:fly4free" for o in offers))
        bon = alert(origins=["@DE"], destinations=["BON"], max_price=500, feed_keywords=[["Bonaire"]])
        self.assertEqual(fetch(source(["fly4free"]), bon), [])     # "from Amsterdam"
        gbp = alert(origins=["LON"], destinations=["TYO"], max_price=600, feed_keywords=[["Tokyo"]])
        self.assertEqual(fetch(source(["fly4free"]), gbp), [])     # "£451" -> kein €-Preis
        hotel = alert(origins=["@DE"], destinations=["BKK"], max_price=600,
                      feed_keywords=[["Koh Tao"], ["cruise"], ["MSC"]])
        self.assertEqual(fetch(source(["fly4free"]), hotel), [])

    def test_premium_flights(self):
        a = alert(origins=["FRA"], destinations=["BKK"], cabin="business", max_price=1500,
                  feed_keywords=[["Bangkok"]])
        offers = fetch(source(["premium-flights"]), a)
        self.assertEqual([(o.source, o.price, o.cabin) for o in offers],
                         [("feed:premium-flights", 1210, "business")])
        # fremde Abflughäfen, $-/£-Preise: nichts
        for kw in (["Mumbai"], ["Maldives"], ["Kilimanjaro"], ["Johannesburg"], ["Lisbon"]):
            b = alert(origins=["@DE"], destinations=["BKK"], cabin="business", max_price=5000,
                      feed_keywords=[kw])
            self.assertEqual(fetch(source(["premium-flights"]), b), [], kw)
        cph = alert(origins=["CPH"], destinations=["BOM"], cabin="business", max_price=2000,
                    feed_keywords=[["Mumbai"]])
        self.assertEqual([o.price for o in fetch(source(["premium-flights"]), cph)], [1910])

    def test_premium_flights_only_premium_cabins(self):
        item = {"title": "Frankfurt → Bangkok Economy Round Trip 410€",
                "categories": ["Active", "Cheap flights"], "link": "https://premium-flights.com/x/"}
        self.assertFalse(analyze(item, PRESETS_BY_ID["premium-flights"].profile).flight)
        self.assertTrue(analyze(item, PRESETS_BY_ID["fly4free"].profile).flight)


class OptionalPresetTest(unittest.TestCase):
    def test_urlaubspiraten(self):
        a = alert(origins=["@DE"], destinations=["NYC"], max_price=350, feed_keywords=[["New York"]])
        offers = fetch(source(["urlaubspiraten"]), a)
        self.assertEqual([(o.source, o.price) for o in offers], [("feed:urlaubspiraten", 297)])
        b = alert(origins=["@DE"], destinations=["PRG"], max_price=350,
                  feed_keywords=[["Prag"], ["Nürnberg"]])
        self.assertEqual(fetch(source(["urlaubspiraten"]), b), [])    # Hotel-Deals

    def test_reisetopia(self):
        a = alert(origins=["FRA"], destinations=["KTM"], cabin="business", max_price=3000,
                  feed_keywords=[["Kathmandu"], ["Hongkong"], ["Calgary"]])
        self.assertEqual(fetch(source(["reisetopia"]), a), [])    # keine Preise im Titel, Meilen-Deals


class SearchTest(unittest.TestCase):
    def test_search_adds_articles_and_caches(self):
        src = source(["travel-dealz", "travel-dealz-suche"])
        a = alert(id="erbil", origins=["@DE"], destinations=["EBL"], cabin="business", max_price=500,
                  feed_keywords=[["Erbil", "Business"], ["Erbil", "Eurowings"]])
        calls = []

        def fake(url, **kw):
            calls.append(url)
            return fixture_for(url)

        with mock.patch.object(http, "request", side_effect=fake):
            offers = src.fetch(a)
            src.fetch(a)       # zweiter Alarm-Durchlauf: Cache, keine neue Suche
        self.assertEqual([o.source for o in offers], ["feed:travel-dealz-suche"])
        self.assertEqual(offers[0].price, 390)
        searches = [u for u in calls if "feed=rss2" in u]
        self.assertEqual(searches, [TRAVEL_DEALZ_SEARCH.format(word="Erbil")])
        self.assertIsNone(src.incomplete)
        self.assertEqual(len(src.all_items()), 8 + 6 - 0)   # Suchtreffer kommen zum Radar dazu

    def test_search_limit_and_errors(self):
        src = source(["travel-dealz-suche"], max_searches=2)
        a = alert(origins=["@DE"], destinations=["@CN_MAJOR"], max_price=499,
                  feed_keywords=[["Peking"], ["Shanghai"], ["Guangzhou"]])
        calls = []

        def fake(url, **kw):
            calls.append(url)
            if "Shanghai" in url:
                raise http.HttpError("HTTP 503", 503)
            return fixture_for(url)

        with mock.patch.object(http, "request", side_effect=fake):
            src.fetch(a)
        self.assertEqual(len([u for u in calls if "feed=rss2" in u]), 2)   # Limit
        self.assertIn("Suche 'Shanghai'", src.incomplete["message"])
        self.assertIsNone(src.incomplete["routes"])
        self.assertTrue(all(it["source"] == "feed:travel-dealz-suche" for it in src.all_items()))


class ErrorHandlingTest(unittest.TestCase):
    def test_partial_failure_keeps_other_feeds(self):
        src = source(["travel-dealz", "mydealz-fluege"])

        def fake(url, **kw):
            if "mydealz" in url:
                raise http.HttpError("HTTP 500", 500)
            return fixture_for(url)

        a = alert(id="nyc", origins=["@DE"], destinations=["NYC"], max_price=350)
        with mock.patch.object(http, "request", side_effect=fake):
            offers = src.fetch(a)
        self.assertEqual([o.price for o in offers], [306])
        self.assertIn("1 von 6 Feeds nicht lesbar", src.incomplete["message"])
        self.assertIsNone(src.incomplete["routes"])

    def test_all_fail_is_source_error(self):
        src = source(["fly4free"])
        with mock.patch.object(http, "request", side_effect=http.HttpError("offline", None, network=True)):
            with self.assertRaises(SourceError):
                src.fetch(alert())
            with self.assertRaises(SourceError):
                src.load()                      # bleibt für den Lauf gemerkt

    def test_custom_feed_gets_generic_profile(self):
        src = source([], extra_feeds=["https://example.org/feed"])
        xml = ('<rss><channel><item><title>Zürich nach Hamburg ab 59€</title>'
               '<link>https://example.org/a/</link></item></channel></rss>').encode()
        with mock.patch.object(http, "request", return_value=xml):
            offers = src.fetch(alert())
        self.assertEqual([(o.source, o.price) for o in offers], [(CUSTOM_SOURCE, 59)])


class RegionsTest(unittest.TestCase):
    def test_synonyms_and_codes(self):
        self.assertEqual(regions.synonyms("Peking"), ["Peking", "Beijing", "BJS", "PEK", "PKX"])
        self.assertIn("Zürich", regions.synonyms("ZRH"))
        self.assertEqual(regions.synonyms("Erbil"), ["Erbil"])
        self.assertEqual(regions.codes_for("Lissabon"), {"LIS"})
        self.assertEqual(regions.place_kind("Weeze"), "home")
        self.assertEqual(regions.place_kind("Vienna"), "home")
        self.assertEqual(regions.place_kind("Prague"), "foreign")
        self.assertEqual(regions.place_kind("Januar"), "unknown")
        self.assertTrue(regions.is_home("GVA"))
        self.assertFalse(regions.is_home("AMS"))
        self.assertEqual(regions.region_names(regions.expand(["@DACH"])),
                         ["Deutschland", "Schweiz", "Österreich"])
        self.assertEqual(regions.name("AMS"), "Amsterdam")


if __name__ == "__main__":
    unittest.main()
