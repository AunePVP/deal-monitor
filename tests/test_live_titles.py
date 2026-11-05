"""Regressionstests mit echten Überschriften aus dem Live-Lauf (05.11.2026).

Alle Titel, Kategorien und Beschreibungsanfänge stammen unverändert aus den Feeds
(Travel-Dealz, Travel-Dealz-Suche, mydealz, fly4free, premium-flights). Geprüft wird,
was die Standard-Alarme aus ``config/alerts.toml`` und das Radar daraus machen.
"""

import unittest
from datetime import datetime, timezone
from pathlib import Path

from dealmonitor import config, principles, radar
from dealmonitor.models import Alert
from dealmonitor.notify import format_text
from dealmonitor.radar import RadarSettings, scan
from dealmonitor.sources.feed import (
    PRESETS_BY_ID, FeedSource, analyze, cabin_prices, is_oneway, search_terms,
)

ROOT = Path(__file__).resolve().parent.parent

# (Titel, Kategorien, Preset, Beschreibungsanfang) - so wie im Live-Lauf gelesen
LIVE = [
    ('New York: Nonstop in der Condor „Premium Class“ von Frankfurt ab 640€ (540€ mit Amex Platinum)',
     ['Premium Economy Class'], 'travel-dealz', ''),
    ('Island ab 156€ mit Discover Airlines & Bus von Innsbruck (nur Personal Item; 236€ mit Gepäck)',
     ['Economy Class'], 'travel-dealz', ''),
    ('Condor: Oneway-Flüge nach San José del Cabo (Mexiko) ab 210€ inkl. Gepäck',
     ['Economy Class', 'Oneway', 'Weltreisebaustein'], 'travel-dealz', ''),
    ('Erbil, Irak ab 390€ in der Eurowings „Business Class“ (inkl. 400 Miles&More Statuspunkten)',
     ['Business Class'], 'travel-dealz', ''),
    ('Bangkok: Business Class von Frankfurt ab 1.208€ mit Turkmenistan Airlines',
     ['Business Class', 'Instagram'], 'travel-dealz', ''),
    ('Kuala Lumpur: Business Class von Frankfurt ab 1.148€ mit Turkmenistan Airlines',
     ['Business Class', 'Instagram'], 'travel-dealz', ''),
    ('Peking: Business Class von Frankfurt ab 1.123€ mit Turkmenistan Airlines',
     ['Business Class'], 'travel-dealz', ''),
    ('Blind Booking bei Austrian Airlines: Europaflüge von Wien ab 99€ roundtrip (Business Class ab 229€)',
     ['Business Class', 'Economy Class', 'Blind Booking', 'Instagram'], 'travel-dealz', ''),
    ('Direktflüge nach Izmir / Türkei nonstop von Berlin, Stuttgart, Köln, Düsseldorf, München, Frankfurt '
     'inkl. Rückflug (Nov - Apr / Pegasus)',
     ['Urlaub & Reisen'], 'mydealz-fluege',
     '125€ - SkyExplorer Super Preise für Direktflüge in die Türkei mit Pegasus Airlines! (party) Schon ab '
     '125€ könnt ihr nonstop inkl. Rückflug von Berlin, Stuttgart, Köln, Düsseldorf, München und Frankfur'),
    ('Flüge USA / New York von München, Berlin, Hamburg und Frankfurt inkl. Rückflug (Okt - Apr / Icelandair)',
     ['Urlaub & Reisen'], 'mydealz-fluege',
     '297€ - SkyExplorer Spitzen Preise für Flüge in die USA (party) Schon ab 298€ 389€ könnt ihr mit '
     'Icelandair inkl. Rückflug von Hamburg, Berlin, München und Frankfurt nach New York fliegen.'),
    ('Paris (Frankreich): Hin- und Rückflug von Berlin ab 72€',
     ['Urlaub & Reisen'], 'mydealz-fluege',
     '72€ - Skyscanner Über Skyscanner Beispielflug : Berlin Brandenburg - Paris-Orly (ca. 20km von der '
     'Stadtmitte entfernt) Fluggesellschaft : Transavia France'),
    ('SAS: Stockholm ab 85€ und viele Regionalflughäfen in Schweden ab 103€ von Berlin (Personal Item)',
     ['Urlaub & Reisen'], 'mydealz-fluege',
     '85€ Coole Preise dpr Flüge nach Schweden. SAS von November bis April Beispiele von Travel-Dealz '
     'Stockholm ab 85€ (138€) nonstop Östersund ab 103€ (158€)'),
    ('Direktflüge nach Antalya / Türkei nonstop inkl. Rückflug von Köln, Berlin, München, Hamburg, '
     'Düsseldorf, Frankfurt, Nürnberg uvm (Sep - Dez)',
     ['Urlaub & Reisen'], 'mydealz-fluege',
     '59€ - SkyExplorer Update: Pricedrop auf 59€! Super Preise für Direktflüge nach Antalya im Sommer: '
     'Schon ab 59€ 96€ könnt ihr nonstop inkl. Rückflug von Nürnberg, Dortmund, Hamburg, Köln'),
    ('Frankfurt → Bangkok Business Class Round Trip 1210€ (Budget-Friendly Lie-Flat Option)',
     ['Active', 'Cheap Business Class flights', 'From Europe', 'bangkok'], 'premium-flights', ''),
    # --- nicht zu melden -------------------------------------------------------------------
    ('China Eastern: Gabelflüge Amsterdam – Tokio – Frankfurt ab 426€ (Nov. / Dez. 2026)',
     ['Economy Class', 'Gabelflüge'], 'travel-dealz', ''),
    ('Costa Toscana: 7 Nächte Mittelmeer-Kreuzfahrten von Savona ab 399€ p.P. (auch Alleinreisende)',
     ['Kreuzfahrten', 'Alleinreisende'], 'travel-dealz', ''),
    ('Last Minute: Bonaire ab 416€ mit Corendon von Amsterdam (Handgepäck)',
     ['Economy Class'], 'travel-dealz', ''),
    ('Deutsche Bahn: 1 Million zusätzliche Sparpreise ab 7€ (25% Rabatt mit BahnCard)',
     ['Bahn', 'Instagram'], 'travel-dealz', ''),
    ('New York ab 306€ mit Icelandair von Berlin, München & Frankfurt (441€ mit Gepäck)',
     ['Economy Class'], 'travel-dealz', ''),
    ('Flying Blue Promo Awards: Business Class nach New York ab 45.000 Meilen + 317€ pro Richtung (ohne Lounge)',
     ['Meilen, Punkte & Status', 'Flying Blue'], 'travel-dealz', ''),
    ('Indonesien: Jakarta ab 462€ mit Air China von Frankfurt inkl. Gepäck (Abflug Nov. 2026)',
     ['Economy Class'], 'travel-dealz', ''),
    ('Bangkok ab 1.274€ in der Royal Jordanian Business Class von Athen (Langer Layover)',
     ['Business Class'], 'travel-dealz', ''),
    ('China ab 1.440€ in der China Southern Business Class von Budapest',
     ['Business Class'], 'travel-dealz', ''),
    ('Oneway: SAS Business Class Thailand → Frankfurt ab 806€ (Economy ab 233€)',
     ['Business Class', 'Economy Class', 'Premium Economy Class', 'Oneway'], 'travel-dealz', ''),
    ('Ryanair Blitzverkauf | Flüge ab 14,99€ | z.B. London, Mallorca, Porto, Barcelona, Alicante uvm | '
     'von Berlin, Köln, uvm | bis 21.12. | oneway',
     ['Urlaub & Reisen'], 'mydealz-fluege',
     '14,99€ - Ryanair Ryanair hat eine neue Blitzverkauf Aktion gestartet - ihr könnt Flüge ab 14,99€ '
     'für Reisen bis zum 21.12. buchen.'),
    ('Flüge nach Danzig mit Buzz Air / Wizz Air nonstop von Hamburg inkl. Rückflug (Jan - Mär) | oneway ab 19€',
     ['Urlaub & Reisen'], 'mydealz-fluege',
     '36€ - SkyExplorer Spitzen Preise für Flüge nach Danzig: Schon ab 36€ könnt ihr nonstop inkl. '
     'Rückflug mit Buzz von Hamburg nach Danzig fliegen. Oneway ab 19€ .'),
    ('Kreta (Griechenland): 7 Nächte im 4*-Amazones Village Suites | inkl. Direktflug ab Berlin, Mietwagen '
     '& Frühstück | 341€ p.P. | Juli',
     ['Urlaub & Reisen'], 'mydealz-fluege',
     '682€ - Urlaubspiraten Kreta (Griechenland): 7 Nächte im 4*-Amazones Village Suites bei Hersonissos '
     'inklusive Direktflügen ab Berlin und Frühstück.'),
    ('Eurowings Flug-Sale | Bis zu 15 % Rabatt auf Direktflüge | ab 33,99€ nach Pula, Mallorca, Split, '
     'Alicante, Athen, Ibiza uvm. | Herbst-Winter',
     ['Urlaub & Reisen'], 'mydealz-fluege',
     '33,99€ - Eurowings Bei Eurowings gibt es aktuell einige gute Preise für Last-Minute Flüge innerhalb '
     'Europas und Nordafrika'),
    ('Flüge nach Thailand / Bangkok inkl. Gepäck inkl. Rückflug von Frankfurt und München mit Air China (Nov - Apr)',
     ['Urlaub & Reisen'], 'mydealz-fluege',
     '494€ - SkyExplorer Super Preise für Flüge nach Thailand inkl. Gepäck: Schon ab 494€ könnt ihr von '
     'Frankfurt und München inkl. Rückflug nach Bangkok fliegen.'),
    ('CHEAP 💥 Flights from several German cities to New York 🗽 from €302',
     ['Europe', 'We recommend', 'berlin', 'Berlin to New York'], 'fly4free', ''),
    ('2-in-1 full-service flights from London to Jordan & China from £457',
     ['Europe', 'cheap flights from London', 'cheap flights to China', 'cheap flights to Jordan'],
     'fly4free', ''),
    ('Vienna → New Delhi Business Class Round Trip €1830 (All of 2027)',
     ['Active', 'Cheap Business Class flights', 'From Europe', 'Austria'], 'premium-flights', ''),
    ('Houston → Kilimanjaro Business Class Round Trip from $3,650 (Safari Gateway Deal)',
     ['Active', 'Cheap Business Class flights', 'From USA & Canada'], 'premium-flights', ''),
    ('Oneway: Hanoi → Deutschland ab 172€ mit Air China (inkl. Gepäck)',
     ['Economy Class', 'Oneway', 'Weltreisebaustein'], 'travel-dealz-suche', ''),
    ('Taipeh ab 411€ mit Air China von München & Frankfurt (inkl. Gepäck)',
     ['Economy Class'], 'travel-dealz-suche', ''),
]


def make_item(i, title, cats, preset, desc):
    p = PRESETS_BY_ID[preset]
    return {"title": title, "link": f"https://{preset}.example/deal/{i}/",
            "published": datetime.now(timezone.utc), "categories": list(cats), "feed": "",
            "description": desc, "description_text": desc, "merchant_price": None,
            "profile": p.profile, "source": p.source}


def live_items():
    return [make_item(i, *row) for i, row in enumerate(LIVE)]


def live_source() -> FeedSource:
    """FeedSource, deren Artikel die echten Titel oben sind (kein Netz)."""
    src = FeedSource(presets=["travel-dealz", "mydealz-fluege", "fly4free", "premium-flights"],
                     search_enabled=False)
    items = live_items()
    for it in items:
        it["key"] = it["link"].rstrip("/")
        it["preset"] = it["source"].removeprefix("feed:")
    src._items = items
    return src


def find(deals, prefix):
    hits = [d for d in deals if d.offer.title.startswith(prefix)]
    assert len(hits) == 1, (prefix, [d.offer.title for d in deals])
    return hits[0]


class LiveRadarTest(unittest.TestCase):
    def test_reported_hits(self):
        deals = scan(live_items(), RadarSettings())
        got = sorted((d.offer.title[:30], d.offer.price, d.offer.cabin) for d in deals)
        expected = sorted([
            ("New York: Nonstop in der Condo", 640, "premium"),
            ("Island ab 156€ mit Discover Ai", 156, "economy"),
            ("Condor: Oneway-Flüge nach San ", 210, "economy"),
            ("Erbil, Irak ab 390€ in der Eur", 390, "business"),
            ("Bangkok: Business Class von Fr", 1208, "business"),
            ("Kuala Lumpur: Business Class v", 1148, "business"),
            ("Peking: Business Class von Fra", 1123, "business"),
            ("Blind Booking bei Austrian Air", 99, "economy"),
            ("Direktflüge nach Izmir / Türke", 125, "economy"),
            ("Flüge USA / New York von Münch", 298, "economy"),
            ("Paris (Frankreich): Hin- und R", 72, "economy"),
            ("SAS: Stockholm ab 85€ und viel", 85, "economy"),
            ("Direktflüge nach Antalya / Tür", 59, "economy"),
        ])
        self.assertEqual(got, expected)

    def test_blind_booking_is_economy_not_business(self):
        # vorher: "Business Class ≤ 1.300 €" für den Economy-Preis 99 €
        d = find(scan(live_items()), "Blind Booking")
        self.assertEqual((d.offer.cabin, d.offer.price, d.reasons), ("economy", 99, ["Economy ≤ 300 €"]))

    def test_other_cabin_price_in_title_is_used(self):
        # Economy-Grenze 50 €: 99 € Economy reicht nicht, aber "Business Class ab 229€" schon
        d = find(scan(live_items(), {"radar_economy_max": 50}), "Blind Booking")
        self.assertEqual((d.offer.cabin, d.offer.price), ("business", 229))
        self.assertEqual(d.reasons, ["Business Class ≤ 1.300 €"])

    def test_same_deal_on_two_sites_reported_once(self):
        deals = scan(live_items())
        d = find(deals, "Bangkok: Business Class von Frankfurt")
        self.assertIn("Auch bei premium-flights: 1.210 €", d.reasons)
        self.assertFalse(any(x.offer.title.startswith("Frankfurt → Bangkok") for x in deals))
        # Kuala Lumpur (gleiche Airline, ähnlicher Preis, anderes Ziel) bleibt eigener Treffer
        find(deals, "Kuala Lumpur: Business Class")

    def test_twin_of_alarm_deal_not_in_radar(self):
        src = live_source()
        deals = scan(src, exclude_links={"https://travel-dealz.example/deal/4"})   # Bangkok TD
        self.assertFalse(any("Bangkok" in d.offer.title and d.offer.cabin == "business" for d in deals))

    def test_never_reported(self):
        titles = [d.offer.title for d in scan(live_items())]
        for bad in ("China Eastern: Gabelflüge Amsterdam", "Costa Toscana", "Last Minute: Bonaire",
                    "Deutsche Bahn", "New York ab 306€", "Flying Blue Promo Awards", "Bangkok ab 1.274€",
                    "China ab 1.440€", "Oneway: SAS Business Class Thailand", "Ryanair Blitzverkauf",
                    "Flüge nach Danzig", "Kreta", "Eurowings Flug-Sale", "CHEAP 💥", "2-in-1",
                    "Vienna → New Delhi", "Houston", "Oneway: Hanoi", "Frankfurt → Bangkok"):
            self.assertFalse(any(t.startswith(bad) for t in titles), bad)

    def test_notification_shows_price_for_mydealz(self):
        # mydealz-Titel nennen den Preis oft nicht ("Direktflüge nach Antalya …")
        d = find(scan(live_items()), "Direktflüge nach Antalya")
        text = format_text(d, "new")
        self.assertIn("Preis: ab 59 €", text.splitlines()[1])


class LiveParserTest(unittest.TestCase):
    def info(self, prefix):
        row = next(r for r in LIVE if r[0].startswith(prefix))
        return analyze(make_item(0, *row))

    def test_cabin_prices(self):
        self.assertEqual(cabin_prices("Blind Booking bei Austrian Airlines: Europaflüge von Wien ab 99€ "
                                      "roundtrip (Business Class ab 229€)"), [("business", 229)])
        self.assertEqual(cabin_prices("Oneway: SAS Business Class Thailand → Frankfurt ab 806€ "
                                      "(Economy ab 233€)"), [("economy", 233)])
        self.assertEqual(cabin_prices("San José del Cabo: Condor „Premium Economy“ ab 760€ roundtrip "
                                      "(660€ mit Amex Platinum)"), [("premium", 760)])
        self.assertEqual(cabin_prices("Erbil, Irak ab 390€ in der Eurowings „Business Class“ "
                                      "(inkl. 400 Miles&More Statuspunkten)"), [])

    def test_mixed_cabin_titles(self):
        i = self.info("Blind Booking")
        self.assertEqual((i.cabin, i.price, i.alt_prices, i.oneway), ("economy", 99, {"business": 229}, False))
        i = self.info("Oneway: SAS Business Class Thailand")
        self.assertEqual((i.cabin, i.price, i.alt_prices, i.oneway, i.origin),
                         ("business", 806, {"economy": 233}, True, "foreign"))
        for prefix, cabin, price in (("Erbil", "business", 390), ("New York: Nonstop in der Condor", "premium", 640),
                                     ("Peking: Business", "business", 1123)):
            i = self.info(prefix)
            self.assertEqual((i.cabin, i.price, i.alt_prices), (cabin, price, {}), prefix)

    def test_oneway_vs_roundtrip(self):
        # vorher als one-way gewertet, obwohl der Hauptpreis 36 € inkl. Rückflug ist
        self.assertFalse(is_oneway("Flüge nach Danzig mit Buzz Air / Wizz Air nonstop von Hamburg inkl. "
                                   "Rückflug (Jan - Mär) | oneway ab 19€"))
        self.assertEqual(self.info("Flüge nach Danzig").price, 36)
        for t in ("Ryanair Blitzverkauf | Flüge ab 14,99€ | z.B. London, Mallorca, Porto, Barcelona, Alicante "
                  "uvm | von Berlin, Köln, uvm | bis 21.12. | oneway",
                  "Condor: Oneway-Flüge nach San José del Cabo (Mexiko) ab 210€ inkl. Gepäck",
                  "Weltreisebausteine von Turkmenistan Airlines: Business Class Frankfurt ↔︎ Asien ab 612€ oneway",
                  "Oneway: SAS Business Class Thailand → Frankfurt ab 806€ (Economy ab 233€)"):
            self.assertTrue(is_oneway(t), t)
        self.assertFalse(is_oneway("Blind Booking bei Austrian Airlines: Europaflüge von Wien ab 99€ roundtrip "
                                   "(Business Class ab 229€)"))

    def test_search_terms_skip_synonyms(self):
        # "Peking" und "Beijing" sind dieselbe Stadt - nicht zwei der 8 Suchen verbrauchen
        cfg = config.load(ROOT / "config" / "alerts.toml")
        cn = next(a for a in cfg.alerts if a.id == "de-cn-economy")
        terms = search_terms(cn)
        self.assertIn("Peking", terms)
        self.assertNotIn("Beijing", terms)
        self.assertEqual(terms[:3], ["China", "Peking", "Shanghai"])


class LiveAlarmTest(unittest.TestCase):
    """Die Standard-Alarme gegen die echten Titel: nur der Peking-Business-Deal passt - und seit
    Paket 4 (Nachbar-Hubs) China-Business ab Budapest mit 180 € Zubringer (1.440 + 180 €)."""

    def test_standard_alerts(self):
        cfg = config.load(ROOT / "config" / "alerts.toml")
        src = live_source()
        found = {}
        for a in cfg.alerts:
            deals = [principles.evaluate(a, o, [], min_samples=8) for o in src.fetch(a)]
            found[a.id] = [(d.offer.title[:24], d.offer.price, d.offer.cabin) for d in deals if d]
        self.assertEqual(found, {"zrh-ham": [], "ham-zrh": [], "zrh-ham-oneway": [], "de-cn-economy": [],
                                 "de-cn-business": [("Peking: Business Class v", 1123, "business"),
                                                    ("China ab 1.440€ in der C", 1620, "business")]})
        # das Radar meldet den Alarm-Deal nicht noch einmal
        self.assertFalse(any(d.offer.title.startswith("Peking") for d in scan(src)))

    def test_business_alarm_gets_business_price(self):
        biz = Alert(id="vie-biz", name="Wien Business", origins=["VIE"], destinations=["LON"],
                    cabin="business", max_price=300, feed_keywords=[["Europaflüge", "Wien"]])
        eco = Alert(id="vie-eco", name="Wien Economy", origins=["VIE"], destinations=["LON"],
                    max_price=300, feed_keywords=[["Europaflüge", "Wien"]])
        self.assertEqual([(o.cabin, o.price) for o in live_source().fetch(biz)], [("business", 229)])
        self.assertEqual([(o.cabin, o.price) for o in live_source().fetch(eco)], [("economy", 99)])


if __name__ == "__main__":
    unittest.main()
