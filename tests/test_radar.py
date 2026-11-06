"""Radar: alle Flug-Artikel der Feeds auf Hammer-Deals prüfen (unabhängig von Alarmen)."""

import unittest
from pathlib import Path
from unittest import mock

from dealmonitor import http, radar
from dealmonitor.models import Alert
from dealmonitor.radar import RadarSettings, check, hot_reason, scan
from dealmonitor.sources import SourceError
from dealmonitor.sources.feed import FEED_PRESETS, PRESETS_BY_ID, FeedSource

FIX = Path(__file__).parent / "fixtures" / "feeds"
OLD = 36500


def fixture_for(url: str) -> bytes:
    if "feed=rss2" in url:
        return (FIX / "travel-dealz-suche.xml").read_bytes()
    for p in FEED_PRESETS:
        if url in p.urls:
            return (FIX / f"{p.id}.xml").read_bytes()
    raise http.HttpError(f"HTTP 404 für {url}", 404)


def item(title, cats=(), link="https://travel-dealz.de/deal/x/", preset="travel-dealz", **extra):
    p = PRESETS_BY_ID[preset]
    d = {"title": title, "categories": list(cats), "link": link, "published": None,
         "description": "", "profile": p.profile, "source": p.source}
    d.update(extra)
    return d


class SettingsTest(unittest.TestCase):
    def test_defaults_and_from_values(self):
        s = RadarSettings()
        self.assertEqual((s.economy_max, s.economy_min, s.business_max, s.first_max, s.premium_max),
                         (300, 40, 1300, 2500, 700))
        self.assertTrue(s.enabled and s.home_only and s.long_haul_only)
        self.assertEqual(set(radar.DEFAULTS), set(radar.LABELS))
        self.assertFalse(RadarSettings.from_values({"radar_long_haul_only": "aus"}).long_haul_only)
        s = RadarSettings.from_values({"radar_economy_max": "250", "radar_home_only": "false",
                                       "radar_enabled": 0, "business_max": 999})
        self.assertEqual((s.economy_max, s.home_only, s.enabled, s.business_max), (250, False, False, 999))
        self.assertEqual(RadarSettings.from_values(None), RadarSettings())
        self.assertEqual(RadarSettings.from_values(radar.DEFAULTS), RadarSettings())

    def test_radar_alert(self):
        self.assertEqual((radar.RADAR_ALERT.id, radar.RADAR_ALERT.name), ("radar", "Radar: Hammer-Deals"))
        self.assertIsInstance(radar.RADAR_ALERT, Alert)


class HotTest(unittest.TestCase):
    def test_hot_words(self):
        self.assertEqual(hot_reason("Error Fare: Business Class nach Tokio ab 899€"), "Error Fare laut Überschrift")
        self.assertEqual(hot_reason("Fehlerpreis? New York ab 149€"), "Fehlerpreis laut Überschrift")
        self.assertEqual(hot_reason("Hammer-Deal: Bangkok ab 333€"), "Als Hammer-Deal angekündigt")
        self.assertEqual(hot_reason("Mistake fare! Flights from Frankfurt to Tokyo for €280"),
                         "Error Fare laut Überschrift")
        self.assertEqual(hot_reason("Peking ab 399€", ["Economy Class", "Error Fare"]), "Error Fare laut Überschrift")
        self.assertIsNone(hot_reason("Flüge nach Hammerfest ab 199€"))
        self.assertIsNone(hot_reason("New York ab 306€ mit Icelandair"))


class CheckTest(unittest.TestCase):
    def test_economy_limits(self):
        self.assertEqual(check(item("New York ab 298€ von Berlin", ["Economy Class"])), ["Economy ≤ 300 €"])
        self.assertIsNone(check(item("New York ab 306€ von Berlin", ["Economy Class"])))
        self.assertEqual(check(item("New York ab 306€", ["Economy Class"]), RadarSettings(economy_max=350)),
                         ["Economy ≤ 350 €"])

    def test_short_haul_promos_ignored(self):
        self.assertIsNone(check(item("Ryanair Blitzverkauf: Flüge ab 14,99€", ["Economy Class"])))
        self.assertIsNone(check(item("Mailand ab 35€ von Berlin", ["Economy Class"])))
        # Kurzstrecke: auch ohne Untergrenze kein Treffer - nur mit "nicht nur Langstrecke"
        self.assertIsNone(check(item("Mailand ab 35€ von Berlin", ["Economy Class"]),
                                RadarSettings(economy_min=0)))
        self.assertEqual(check(item("Mailand ab 35€ von Berlin", ["Economy Class"]),
                               RadarSettings(economy_min=0, long_haul_only=False)), ["Economy ≤ 300 €"])

    def test_other_cabins(self):
        self.assertEqual(check(item("Peking: Business Class von Frankfurt ab 1.123€", ["Business Class"])),
                         ["Business Class ≤ 1.300 €"])
        self.assertIsNone(check(item("Singapur ab 1.368€ in der Business Class", ["Business Class"])))
        self.assertEqual(check(item("First Class nach Tokio ab 2.400€", ["First Class"])),
                         ["First Class ≤ 2.500 €"])
        self.assertEqual(check(item("New York: Condor „Premium Class“ ab 640€", ["Premium Economy Class"])),
                         ["Premium Economy ≤ 700 €"])
        self.assertIsNone(check(item("New York: Condor „Premium Class“ ab 760€", ["Premium Economy Class"])))

    def test_hot_always_reported(self):
        self.assertEqual(check(item("Error Fare: Business Class nach Tokio ab 1.899€", ["Business Class"])),
                         ["Error Fare laut Überschrift"])
        self.assertEqual(check(item("Error Fare: Business Class nach Tokio ab 999€", ["Business Class"])),
                         ["Error Fare laut Überschrift", "Business Class ≤ 1.300 €"])
        self.assertEqual(check(item("Fehlerpreis bei Lufthansa nach Tokio", ["Flüge"])),
                         ["Fehlerpreis laut Überschrift", "Preis nicht in der Überschrift"])
        # Kreuzfahrt bleibt Kreuzfahrt, auch als "Hammer"
        self.assertIsNone(check(item("Hammer: 7 Nächte Karibik ab 399€", ["Kreuzfahrten"])))

    def test_home_only(self):
        foreign = item("Bonaire ab 199€ mit Corendon von Amsterdam", ["Economy Class"])
        self.assertIsNone(check(foreign))
        self.assertEqual(check(foreign, RadarSettings(home_only=False)), ["Economy ≤ 300 €"])
        # Error Fare Economy ab Nachbar-Hub (Langstrecke): zählt mit Zubringer (Moduldoku)
        self.assertEqual(check(item("Error Fare: Tokio ab 99€ von Amsterdam", ["Economy Class"])),
                         ["Error Fare laut Überschrift",
                          "Abflug Nachbar-Hub Amsterdam (+80 € Zubringer)"])
        # ... auf der Kurzstrecke nicht, ohne Error Fare auch nicht
        self.assertIsNone(check(item("Error Fare: Mallorca ab 9€ von Amsterdam", ["Economy Class"])))
        self.assertIsNone(check(item("Tokio ab 199€ von Amsterdam", ["Economy Class"])))
        self.assertEqual(check(item("Error Fare: Tokio ab 99€ von Amsterdam", ["Economy Class"]),
                               RadarSettings(home_only=False)),
                         ["Error Fare laut Überschrift", "Economy ≤ 300 €"])
        # ohne Abflugangabe: Travel-Dealz = Deutschland, fly4free = unbekannt -> beide erlaubt
        self.assertEqual(check(item("Cheap flights to New York from €290", ["Europe", "cheap flights"],
                                    link="https://www.fly4free.com/x/", preset="fly4free")),
                         ["Economy ≤ 300 €"])

    def test_foreign_currency_and_oneway(self):
        self.assertIsNone(check(item("Houston → Kilimanjaro Business Class from $1,000",
                                     ["Cheap Business Class flights"], preset="premium-flights")))
        self.assertEqual(check(item("Error fare! Houston → Tokyo Business from $1,000",
                                    ["Cheap Business Class flights"], preset="premium-flights"),
                               RadarSettings(home_only=False)),
                         ["Error Fare laut Überschrift", "Preis nur in Fremdwährung"])
        self.assertEqual(check(item("Condor: Oneway-Flüge nach San José del Cabo ab 210€",
                                    ["Economy Class", "Oneway"])), ["Economy ≤ 300 €", "one-way"])
        # Abflug im Ausland (Vietnam) bleibt bei "nur DACH" draußen, auch one-way
        self.assertIsNone(check(item("Oneway: Vietnam → Frankfurt ab 194€", ["Economy Class", "Oneway"])))


class ScanTest(unittest.TestCase):
    def all_presets(self) -> FeedSource:
        return FeedSource(presets=[p.id for p in FEED_PRESETS], max_age_days=OLD)

    def test_scan_fixtures(self):
        src = self.all_presets()
        with mock.patch.object(http, "request", side_effect=lambda url, **kw: fixture_for(url)):
            deals = scan(src, RadarSettings())
        titles = [d.offer.title for d in deals]

        def find(prefix):
            hits = [d for d in deals if d.offer.title.startswith(prefix)]
            self.assertEqual(len(hits), 1, prefix)
            return hits[0]

        find("Peking: Business Class von Frankfurt ab 1.123€")      # Business ≤ 1.300
        find("Bestpreis ‼️ New York Flüge mit Icelandair")          # Urlaubspiraten 297 €
        find("Frankfurt → Bangkok Business Class Round Trip 1210€")  # premium-flights 1.210 €
        # mydealz "Flüge USA / New York … Icelandair" (297 € laut führendem Deal-Preis) ist derselbe Deal wie Urlaubspiraten
        # (297 €) -> einmal gemeldet, mit dem günstigeren Artikel und Hinweis auf die zweite Seite
        self.assertIn("Auch bei mydealz: 297 €", find("Bestpreis ‼️ New York").reasons)
        self.assertFalse(any(t.startswith("Flüge USA / New York") for t in titles))
        for bad in ("Costa Toscana", "Ryanair Blitzverkauf", "🏝️ CARIBBEAN DEAL", "Copenhagen → Mumbai",
                    "Houston", "New York ab 306€", "CHEAP 💥 Flights from several German",
                    "Erbil"):   # Erbil steckt nur im Suchfeed, der hier nicht abgefragt wird
            self.assertFalse(any(t.startswith(bad) for t in titles), bad)
        self.assertEqual(len(deals), 3)
        d = find("Bestpreis ‼️ New York")
        self.assertEqual((d.alert.id, d.alert.name, d.rating), ("radar", "Radar: Hammer-Deals", "Radar"))
        self.assertEqual(d.reasons, ["Economy ≤ 300 €", "Auch bei mydealz: 297 €"])
        self.assertEqual(d.offer.source, "feed:urlaubspiraten")
        self.assertEqual(d.headline, d.offer.title)
        self.assertEqual(d.fingerprint[:6], "radar:")
        self.assertIsNone(d.reference_price)
        prices = [d.offer.price for d in deals]
        self.assertEqual(prices, sorted(prices))
        self.assertEqual(radar.sources_used(deals) >= {"feed:urlaubspiraten", "feed:premium-flights"}, True)

    def test_matched_articles_not_repeated(self):
        src = self.all_presets()
        a = Alert(id="cn-biz", name="CN", origins=["@DE_HUBS"], destinations=["@CN_MAJOR"],
                  cabin="business", max_price=1999, feed_keywords=[["Peking", "Business"]])
        with mock.patch.object(http, "request", side_effect=lambda url, **kw: fixture_for(url)):
            offers = src.fetch(a)
            deals = scan(src, RadarSettings())
        self.assertEqual(len(offers), 1)
        self.assertFalse(any("Peking: Business Class" in d.offer.title for d in deals))
        with mock.patch.object(http, "request", side_effect=lambda url, **kw: fixture_for(url)):
            again = scan(self.all_presets(), {"radar_economy_max": 300})
        self.assertTrue(any("Peking: Business Class" in d.offer.title for d in again))

    def test_scan_items_list_and_order(self):
        items = [
            item("New York ab 290€ von Berlin", ["Economy Class"], link="https://t/a/"),
            item("Error Fare: Business Class nach Tokio ab 1.199€", ["Business Class"], link="https://t/b/"),
            item("Costa Toscana: 7 Nächte ab 399€", ["Kreuzfahrten"], link="https://t/c/"),
            item("New York ab 290€ von Berlin", ["Economy Class"], link="https://t/a/?utm_source=x"),
        ]
        deals = scan(items)
        self.assertEqual([d.offer.price for d in deals], [1199, 290])      # Error Fare zuerst, Dedup
        self.assertEqual(deals[0].reasons, ["Error Fare laut Überschrift", "Business Class ≤ 1.300 €"])
        self.assertEqual(scan(items, {"radar_enabled": False}), [])
        self.assertEqual([d.fingerprint for d in scan(items, exclude_links={"https://t/a"})],
                         [deals[0].fingerprint])

    def test_source_error_propagates(self):
        src = FeedSource(presets=["fly4free"], max_age_days=OLD)
        with mock.patch.object(http, "request", side_effect=http.HttpError("offline", None, network=True)):
            with self.assertRaises(SourceError):
                scan(src)


if __name__ == "__main__":
    unittest.main()
