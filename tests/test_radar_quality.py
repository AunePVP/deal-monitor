"""Radar-Qualität: Live-Probelauf vom 05.11.2026 (alle 12 Quellen) als Regressionstest.

Damals 14 Radar-Treffer, davon 4 relevant (einer doppelt), 7 irrelevant (Blind Booking,
Kurz-/Mittelstrecke), 2 falsch gelesen (Eurowings „Business Class“) und einer nur wegen des
ErrorFareAlerts-Slogans "zum Hammerpreis!". Jetzt bleiben 3: Bangkok Business 1.208 € (mit
"Auch bei ErrorFareAlerts: 1.288 €"), New York 295 € (3 Quellen) und Condor Mexiko one-way.

Titel, Kategorien, Codes und Beschreibungsanfänge sind unverändert aus dem Live-Lauf; die
Zeitpunkte liegen relativ zu jetzt (Abstände wie live), damit kein Test mit der Zeit altert.
"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dealmonitor import dedupe, monitor, radar, regions, sightings
from dealmonitor.config import Config, Settings
from dealmonitor.radar import RadarSettings, check, hot_reason, scan
from dealmonitor.sources.feed import PRESETS_BY_ID, analyze, detect_cabin
from dealmonitor.store import Store

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ago(days=0, hours=0):
    return NOW - timedelta(days=days, hours=hours)


def item(preset, title, link, published, cats=(), desc="", **extra):
    p = PRESETS_BY_ID[preset]
    it = {"title": title, "link": link, "published": published, "categories": list(cats),
          "feed": "", "description": desc, "description_text": desc, "merchant_price": None,
          "profile": p.profile, "source": p.source}
    it.update(extra)
    return it


TD = "travel-dealz-rest"
EFA = "errorfarealerts"
MYDEALZ = "mydealz-fluege"

# -- relevant ---------------------------------------------------------------------------------
TD_BKK = item(TD, "Bangkok: Business Class von Frankfurt ab 1.208€ mit Turkmenistan Airlines",
              "https://travel-dealz.de/deal/turkmenistan-airlines-frankfurt-bangkok-business/",
              ago(7, 17), ["Flüge", "Business Class"], origin_codes={"FRA"},
              dest_codes={"BKK", "DMK"})
# 6 Tage 19 Stunden später, 6,6 % teurer - derselbe Deal
EFA_BKK = item(EFA, "Turkmenistan Airlines: Business Class von Frankfurt nach Bangkok ab 1.288 € – "
               "Luxus zum Economy-Preis!",
               "https://errorfarealerts.com/deals/flights/turkmenistan-airlines--business-class-"
               "von-frankfurt-nach-bangkok-ab-1288", ago(0, 22), price_eur=1288.0,
               origin_codes={"FRA"}, dest_codes={"BKK"})
TD_KUL = item(TD, "Kuala Lumpur: Business Class von Frankfurt ab 1.148€ mit Turkmenistan Airlines",
              "https://travel-dealz.de/deal/turkmenistan-kuala-lumpur/", ago(7, 18),
              ["Flüge", "Business Class"], origin_codes={"FRA"}, dest_codes={"KUL"})
MD_NYC = item(MYDEALZ, "Flüge USA / New York von München, Berlin, Hamburg und Frankfurt inkl. Rückflug "
              "(Okt - Apr / Icelandair)",
              "https://www.mydealz.de/deals/fluge-usa-new-york-icelandair-2859132", ago(0, 16),
              ["Urlaub & Reisen"],
              "297€ - SkyExplorer Spitzen Preise für Flüge in die USA (party) Schon ab 298€ 389€ "
              "könnt ihr mit Icelandair inkl. Rückflug von Hamburg, Berlin, München und Frankfurt "
              "nach New York fliegen.", merchant_price="297€")
UP_NYC = item("urlaubspiraten", "Bestpreis ‼️ New York Flüge mit Icelandair",
              "https://www.urlaubspiraten.de/fluege/new-york-icelandair", ago(0, 18), [],
              "Ahoi Piraten, Uff, wir haben gerade wirklich günstige Flüge zum Big Apple entdeckt: "
              "Ab 297€ fliegt ihr bereits hin und zurück nach New York City mit Icelandair.")
EFA_NYC = item(EFA, "Icelandair: Von deutschen Airports nach New York ab 295 € – Big Apple zum Mini-Preis!",
               "https://errorfarealerts.com/deals/flights/icelandair--von-berlin-nach-new-york",
               ago(0, 2), price_eur=295.0, origin_codes={"BER"}, dest_codes={"JFK"})
TD_SJD = item(TD, "Condor: Oneway-Flüge nach San José del Cabo (Mexiko) ab 210€ inkl. Gepäck",
              "https://travel-dealz.de/ticker/485791/", ago(6), ["Flüge", "Economy Class"])

# -- irrelevant / falsch gelesen ----------------------------------------------------------------
TD_DUB = item(TD, "Dublin: Hin- und Rückflug mit Aer Lingus ab 61€ (inkl. Personal Item + 10 kg Gepäck)",
              "https://travel-dealz.de/deal/aer-lingus-angebot/", ago(10), ["Flüge", "Economy Class"],
              origin_codes={"BER", "FRA", "DUS", "ZRH", "HAM", "MUC"}, dest_codes={"DUB"})
TD_LH_BLIND = item(TD, "Lufthansa Surprise: Blind Booking ab 109€ roundtrip (Business Class ab 229€)",
                   "https://travel-dealz.de/deal/lufthansa-surprise/", ago(10),
                   ["Flüge", "Business Class", "Economy Class"], origin_codes={"FRA", "MUC"})
TD_EW_BLIND = item(TD, "Blind Booking: Kurzfristige Eurowings-Flüge ab 94€ roundtrip (nur Personal Item)",
                   "https://travel-dealz.de/deal/eurowings-blind-booking/", ago(10),
                   ["Flüge", "Economy Class"], origin_codes={"BER", "CGN", "DUS", "HAM", "STR"})
TD_JED = item(TD, "Dschidda, Saudi-Arabien ab 470€ in der Eurowings „Business Class“ (inkl. 400 "
              "Statuspunkten bei Miles&More)", "https://travel-dealz.de/deal/dschidda-eurowings-business-class/",
              ago(10), ["Flüge", "Business Class"], origin_codes={"CGN", "BER"}, dest_codes={"JED"})
TD_EBL = item(TD, "Erbil, Irak ab 390€ in der Eurowings „Business Class“ (inkl. 400 Miles&More "
              "Statuspunkten)", "https://travel-dealz.de/ticker/485569/", ago(7),
              ["Flüge", "Business Class"], origin_codes={"HAJ", "CGN", "HAM"}, dest_codes={"EBL"})
TD_KEF_INN = item(TD, "Island ab 156€ mit Discover Airlines & Bus von Innsbruck (nur Personal Item; "
                  "236€ mit Gepäck)", "https://travel-dealz.de/deal/lufthansa-island/", ago(4),
                  ["Flüge", "Economy Class"], origin_codes={"INN"}, dest_codes={"RKV", "KEF"})
TD_KEF_MUC = item(TD, "Island ab 196€ mit Icelandair von München (284€ mit Gepäck)",
                  "https://travel-dealz.de/deal/icelandair-reykjavik-ab-deutschland/", ago(2),
                  ["Flüge", "Economy Class"], origin_codes={"MUC"}, dest_codes={"RKV", "KEF"})
MD_PAR = item(MYDEALZ, "Paris (Frankreich): Hin- und Rückflug von Berlin ab 72€",
              "https://www.mydealz.de/deals/paris-berlin-72", ago(3), ["Urlaub & Reisen"])
MD_ADB = item(MYDEALZ, "Direktflüge nach Izmir / Türkei nonstop von Berlin, Stuttgart, Köln, Düsseldorf, "
              "München, Frankfurt inkl. Rückflug (Nov - Apr / Pegasus)",
              "https://www.mydealz.de/deals/direktfluge-nach-izmir-2865023", ago(0, 14),
              ["Urlaub & Reisen"], "125€ - SkyExplorer Super Preise für Direktflüge in die Türkei mit "
              "Pegasus Airlines! (party) Schon ab 125€ könnt ihr nonstop inkl. Rückflug von Berlin "
              "nach Izmir fliegen.", merchant_price="125€")
EFA_SIN = item(EFA, "China Eastern Airlines: Von Wien nach Singapur ab 388 € inklusive Gepäck – "
               "Asien zum Hammerpreis!", "https://errorfarealerts.com/deals/flights/china-eastern-"
               "airlines--von-wien-nach-singapur-ab-388", ago(1, 1), price_eur=388.0,
               origin_codes={"VIE"}, dest_codes={"SIN"})

LIVE_RADAR = [TD_DUB, TD_LH_BLIND, TD_EW_BLIND, TD_JED, TD_EBL, TD_BKK, EFA_BKK, TD_SJD, TD_KEF_INN,
              TD_KEF_MUC, MD_PAR, MD_ADB, MD_NYC, UP_NYC, EFA_NYC, EFA_SIN]


def key(it):
    return radar.deal_key(it["title"], analyze(it), published=it["published"], link=it["link"])


def titles(deals):
    return [d.offer.title for d in deals]


class LiveRunTest(unittest.TestCase):
    def test_fourteen_hits_become_three(self):
        deals = scan(LIVE_RADAR, RadarSettings())
        got = {d.offer.title[:40]: (d.offer.price, d.reasons) for d in deals}
        self.assertEqual(got, {
            "Bangkok: Business Class von Frankfurt ab": (
                1208, ["Business Class ≤ 1.300 €", "Auch bei ErrorFareAlerts: 1.288 €"]),
            "Icelandair: Von deutschen Airports nach ": (
                295, ["Economy ≤ 300 €", "Auch bei Urlaubspiraten: 297 €", "Auch bei mydealz: 297 €"]),
            "Condor: Oneway-Flüge nach San José del C": (210, ["Economy ≤ 300 €", "one-way"]),
        })

    def test_load_order_does_not_matter(self):
        a = {(d.offer.title, tuple(d.reasons)) for d in scan(LIVE_RADAR)}
        b = {(d.offer.title, tuple(sorted(d.reasons))) for d in scan(list(reversed(LIVE_RADAR)))}
        self.assertEqual({(t, tuple(sorted(r))) for t, r in a}, b)


class DedupeTest(unittest.TestCase):
    def test_same_airline_fifteen_percent(self):
        self.assertTrue(radar.same_deal(key(TD_BKK), key(EFA_BKK)))       # 6,6 %, gleiche Airline
        self.assertFalse(radar.same_deal(key(TD_BKK), key(TD_KUL)))       # anderes Ziel
        # ohne Airline-Angabe auf einer Seite gilt weiter 5 %
        plain = dict(EFA_BKK, title="Business Class von Frankfurt nach Bangkok ab 1.288 €")
        self.assertFalse(radar.same_deal(key(TD_BKK), key(plain)))
        # mehr als 15 % auch bei derselben Airline nicht
        far = dict(EFA_BKK, price_eur=1400.0)
        self.assertFalse(radar.same_deal(key(TD_BKK), key(far)))

    def test_different_airlines_never(self):
        ce = item(TD, "Hongkong ab 484€ mit China Eastern von Frankfurt (inkl. Gepäck)",
                  "https://travel-dealz.de/deal/hongkong-china-eastern/", ago(10),
                  ["Flüge", "Economy Class"], origin_codes={"FRA"}, dest_codes={"HKG"})
        ca = item(TD, "Hongkong ab 469€ mit Air China von München und Frankfurt (inklusive Gepäck)",
                  "https://travel-dealz.de/deal/air-china-frankfurt-hongkong/", ago(10),
                  ["Flüge", "Economy Class"], origin_codes={"FRA"}, dest_codes={"HKG"})
        self.assertFalse(radar.same_deal(key(ce), key(ca)))
        # auch wenn Air China bei einer anderen Seite steht (3,2 % auseinander)
        ca_sf = dict(ca, link="https://www.secretflying.com/posts/fra-hkg-air-china",
                     source="feed:secretflying")
        self.assertFalse(radar.same_deal(key(ce), key(ca_sf)))
        self.assertTrue(radar.same_deal(key(ca), key(ca_sf)))              # Gegenprobe

    def test_no_airline_stays_at_five_percent(self):
        sf = item("secretflying", "Zurich, Switzerland to Johannesburg, South Africa for only €471 roundtrip",
                  "https://www.secretflying.com/posts/zurich-switzerland-to-johannesburg", ago(1),
                  ["Flights"], price_eur=471.0, origin_codes={"ZRH"}, dest_codes={"JNB"})
        md = item(MYDEALZ, "Flüge nach Johannesburg / Südafrika ab 410€ inkl Rückflug (Frankfurt nonstop / "
                  "Berlin, Prag, Wien, Zürich 1 Stopp)(Condor, Nov-Feb)",
                  "https://www.mydealz.de/deals/fluge-nach-johannesburg-2845895", ago(1, 12),
                  ["Urlaub & Reisen"], "409,98€ - SkyExplorer Tolle Preise für Flüge nach Johannesburg.",
                  merchant_price="409,98€")
        efa = item(EFA, "Ethiopian Airlines: Von Zürich nach Johannesburg ab 475 € – inklusive Gepäck!",
                   "https://errorfarealerts.com/deals/flights/ethiopian-zrh-jnb", ago(0, 20),
                   price_eur=475.0, origin_codes={"ZRH"}, dest_codes={"JNB"})
        self.assertFalse(radar.same_deal(key(sf), key(md)))                # 14,9 %
        self.assertTrue(radar.same_deal(key(sf), key(efa)))                # 0,8 %

    def test_same_site_two_links_never(self):
        other = dict(TD_BKK, link="https://travel-dealz.de/deal/bangkok-update/", published=ago(7))
        self.assertFalse(radar.same_deal(key(TD_BKK), key(other)))
        # derselbe Artikel über RSS und REST (gleicher Link) bleibt einer
        rss = dict(TD_BKK, link=TD_BKK["link"] + "?utm_source=feed", source="feed:travel-dealz")
        self.assertTrue(radar.same_deal(key(TD_BKK), key(rss)))

    def test_window_seven_days(self):
        late = dict(EFA_BKK, published=TD_BKK["published"] + timedelta(days=8))
        self.assertFalse(radar.same_deal(key(TD_BKK), key(late)))

    def test_cheapest_is_main_article(self):
        deals = scan([MD_NYC, UP_NYC, EFA_NYC])
        self.assertEqual(titles(deals), [EFA_NYC["title"]])

    def test_airline_names_in_places_ignored(self):
        k = key(EFA_SIN)
        self.assertIn("SIN", k.dests)
        self.assertFalse(k.dests & {"HKG", "BJS", "SHA"})   # "China Eastern" ist kein Ziel
        self.assertEqual(k.airlines, {"china eastern"})
        # fremder Abflug zählt nicht als Ziel ("Hanoi von Brüssel", "Brüssel–Cancún")
        a = dedupe.route("Aer Lingus: Brüssel – Cancún ab 399€", {"BRU"})
        b = dedupe.route("Hanoi ab 399€ von Brüssel", {"BRU"})
        self.assertTrue(a is None or "BRU" not in a[0])
        self.assertEqual(b, ({"HAN"}, {"BRU"}))

    def test_airlines(self):
        A = dedupe.airlines
        self.assertEqual(A("Turkish Airlines: Bangkok"), A("Bangkok mit Turkish ab 499€"))
        self.assertEqual(A("Qatar Airways nach Doha"), {"qatar"})
        self.assertEqual(A("Frankfurt, Germany to Doha, Qatar for €399"), frozenset())
        self.assertNotEqual(A("Eurowings Discover nach Windhoek"), A("Eurowings nach Palma"))
        self.assertEqual(A("Discover Airlines nach Windhoek"), A("Eurowings Discover nach Windhoek"))
        self.assertEqual(A("Dubai, United Arab Emirates for €299"), frozenset())
        self.assertEqual(A("United States from €299"), frozenset())
        self.assertEqual(A("United Airlines: Frankfurt – Newark"), {"united"})
        self.assertEqual(A("a lot of cheap flights"), frozenset())
        self.assertEqual(A("LOT: Warschau – Seoul"), {"lot"})


class FirstReportTest(unittest.TestCase):
    """Über Läufe hinweg: Travel-Dealz meldet zuerst, ErrorFareAlerts 6 Tage später."""

    def deal(self, it):
        deals = scan([it])
        self.assertEqual(len(deals), 1, it["title"])
        return deals[0]

    def report(self, store, deal):
        kept, merged = monitor.first_reports(store, [(deal, store.deal_status(deal, 0.1))], [])
        for d, _ in kept:
            store.upsert_deal(d, 0.1)
        return kept, merged

    def test_later_report_same_airline_merged(self):
        with Store(":memory:") as store:
            td = self.deal(TD_BKK)
            self.assertEqual(len(self.report(store, td)[0]), 1)
            kept, merged = self.report(store, self.deal(EFA_BKK))
            self.assertEqual((kept, list(merged)), ([], [td.fingerprint]))
            rows = store.sightings(fingerprint=td.fingerprint)
            self.assertEqual({r["airline"] for r in rows}, {"turkmenistan"})

    def test_old_rows_without_airline_use_five_percent(self):
        with Store(":memory:") as store:
            td = self.deal(TD_BKK)
            self.report(store, td)
            store.db.execute("UPDATE sightings SET airline=''")     # Zeile von vor der Spalte
            kept, merged = self.report(store, self.deal(EFA_BKK))
            self.assertEqual((len(kept), dict(merged)), (1, {}))

    def test_cheaper_twin_later_keeps_first_deal_active(self):
        """Lauf 1: nur Urlaubspiraten 297 €. Lauf 2: dazu ErrorFareAlerts 295 € (günstiger,
        also Hauptartikel) - nicht noch einmal melden, und die Erstmeldung läuft nicht ab."""

        class Feed:
            name = "feeds"
            offer_sources = ["feed:urlaubspiraten", "feed:errorfarealerts", "feed:mydealz-fluege"]
            matched_links: set = set()

            def __init__(self, items):
                self.items = items

            def all_items(self):
                return self.items

            def unreliable_sources(self):
                return set()

        cfg = Config(settings=Settings(min_history_samples=100), alerts=[], path=Path("x"))
        with Store(":memory:") as store:
            sent, _ = monitor.run_radar([Feed([UP_NYC])], store, cfg, RadarSettings())
            self.assertEqual([d.offer.source for d, _ in sent], ["feed:urlaubspiraten"])
            sent, r = monitor.run_radar([Feed([UP_NYC, EFA_NYC, MD_NYC])], store, cfg,
                                        RadarSettings())
            self.assertEqual((sent, r.expired), ([], 0))
            active = store.deals(status="active", alert_id="radar")
            self.assertEqual([d["source"] for d in active], ["feed:urlaubspiraten"])
            self.assertEqual(len(active[0]["sources"]), 3)


class RelevanceTest(unittest.TestCase):
    def test_irrelevant_live_hits(self):
        for it in (TD_DUB, TD_LH_BLIND, TD_EW_BLIND, TD_KEF_INN, TD_KEF_MUC, MD_PAR, MD_ADB,
                   TD_JED, TD_EBL, EFA_SIN):
            with self.subTest(it["title"][:40]):
                self.assertIsNone(check(it))

    def test_blind_booking_not_even_business_price(self):
        self.assertEqual(analyze(TD_LH_BLIND).alt_prices, {"business": 229})
        for s in (RadarSettings(economy_max=50), RadarSettings(long_haul_only=False),
                  RadarSettings(business_max=5000)):
            self.assertIsNone(check(TD_LH_BLIND, s))
        for t in ("Wundertüte: Flüge ab 49€", "Überraschungsflug ab 99€ nach Asien"):
            self.assertIsNone(check(item(TD, t, "https://travel-dealz.de/x/", ago(1),
                                          ["Flüge", "Economy Class"])))

    def test_relevant_live_hits(self):
        self.assertEqual(check(TD_SJD), ["Economy ≤ 300 €", "one-way"])
        self.assertEqual(check(MD_NYC), ["Economy ≤ 300 €"])
        self.assertEqual(check(TD_BKK), ["Business Class ≤ 1.300 €"])

    def test_switch_off_restores_old_rules(self):
        old = RadarSettings(long_haul_only=False)
        self.assertEqual(check(TD_DUB, old), ["Economy ≤ 300 €"])
        self.assertEqual(check(TD_KEF_INN, old), ["Economy ≤ 300 €"])
        self.assertIsNone(check(item(TD, "Mailand ab 35€ von Berlin", "https://travel-dealz.de/m/",
                                     ago(1), ["Economy Class"]), old))      # Untergrenze 40 €

    def test_unknown_route_only_with_error_fare(self):
        unknown = item(TD, "Flüge ab 199€ hin und zurück", "https://travel-dealz.de/u/", ago(1),
                       ["Flüge", "Economy Class"])
        self.assertIsNone(check(unknown))
        ef = dict(unknown, title="Error Fare: Flüge ab 199€ hin und zurück")
        self.assertEqual(check(ef), ["Error Fare laut Überschrift", "Economy ≤ 300 €"])
        # Error Fare auf Kurzstrecke bleibt gemeldet, aber ohne Economy-Treffer
        short = dict(unknown, title="Error Fare: Mallorca ab 19€ von Berlin")
        self.assertEqual(check(short), ["Error Fare laut Überschrift"])
        # "Hammer" auf Kurzstrecke: kein Grund mehr
        self.assertIsNone(check(dict(unknown, title="Hammer: Mallorca ab 19€ von Berlin")))
        self.assertEqual(check(dict(unknown, title="Hammer: Bangkok ab 299€ von Berlin")),
                         ["Als Hammer-Deal angekündigt", "Economy ≤ 300 €"])

    def test_business_short_haul_needs_economy_price(self):
        biz = item(TD, "Mallorca: Business Class von Frankfurt ab 399€", "https://travel-dealz.de/b/",
                   ago(1), ["Flüge", "Business Class"])
        self.assertIsNone(check(biz))
        self.assertEqual(check(dict(biz, title="Mallorca: Business Class von Frankfurt ab 289€")),
                         ["Business Class ≤ 300 €"])
        self.assertEqual(check(biz, RadarSettings(long_haul_only=False)), ["Business Class ≤ 1.300 €"])

    def test_haul(self):
        H = regions.haul
        self.assertEqual(H("Island ab 196€ mit Icelandair von München", {"KEF"}), "short")
        self.assertEqual(H("Condor: Oneway-Flüge nach San José del Cabo (Mexiko) ab 210€"), "long")
        self.assertEqual(H("Direktflüge nach Izmir / Türkei"), "short")
        self.assertEqual(H("Dschidda, Saudi-Arabien ab 470€", {"JED"}), "short")
        self.assertEqual(H("Erbil, Irak ab 390€", {"EBL"}), "short")
        self.assertEqual(H("Flüge USA / New York von München"), "long")
        self.assertEqual(H("Bangkok ab Athen", (), {"ATH"}), "long")
        self.assertEqual(H("Business ab Budapest nach Asien", (), {"BUD"}), "long")
        self.assertEqual(H("Tel Aviv ab 199€"), "short")
        self.assertEqual(H("Europaflüge von Wien ab 99€"), "short")
        self.assertIsNone(H("Flüge ab 99€ hin und zurück"))
        self.assertEqual(H("Island-Stopover auf dem Weg nach New York"), "long")


class ParserTest(unittest.TestCase):
    def test_eurowings_business_is_economy(self):
        for it in (TD_JED, TD_EBL):
            i = analyze(it)
            self.assertEqual((i.cabin, i.alt_prices), ("economy", {}), it["title"])
        self.assertEqual(detect_cabin(TD_JED["title"], ["Flüge", "Business Class"]), "economy")
        self.assertEqual(detect_cabin("London: Eurowings BIZclass ab 240€"), "economy")
        # Eurowings Discover hat eine echte Business Class
        self.assertEqual(detect_cabin("Windhoek: Eurowings Discover Business Class ab 1.499€",
                                      ["Business Class"]), "business")
        self.assertEqual(detect_cabin("Bangkok: Business Class von Frankfurt ab 1.208€",
                                      ["Business Class"]), "business")

    def test_errorfarealerts_slogan_not_hot(self):
        self.assertIsNone(hot_reason(EFA_SIN["title"], [], EFA_SIN["source"]))
        self.assertIsNone(hot_reason(EFA_SIN["title"], [], analyze(EFA_SIN).source))
        # dieselbe Überschrift woanders: Hammer zählt weiter
        self.assertEqual(hot_reason(EFA_SIN["title"], [], "feed:travel-dealz"),
                         "Als Hammer-Deal angekündigt")
        # Error Fare im übrigen Titel oder als Marker der Quelle zählt weiter
        self.assertEqual(hot_reason("Error Fare: Von Wien nach Tokio ab 299 € – Japan zum Hammerpreis!",
                                    [], "feed:errorfarealerts"), "Error Fare laut Überschrift")
        self.assertEqual(hot_reason(EFA_SIN["title"], ["ERROR FARE"], "feed:errorfarealerts"),
                         "Error Fare laut Überschrift")


class MigrationTest(unittest.TestCase):
    def test_airline_column_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deals.sqlite"
            db = sqlite3.connect(path)
            db.executescript("""
                CREATE TABLE sightings (deal_key TEXT NOT NULL, origin TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL, first_seen TEXT NOT NULL, price REAL NOT NULL,
                    fingerprint TEXT NOT NULL, link TEXT, UNIQUE (deal_key, source, fingerprint));
                INSERT INTO sightings VALUES ('BKK-business-RT', 'FRA', 'feed:travel-dealz',
                    '2026-10-28T12:48:09', 1208, 'radar:alt', 'https://travel-dealz.de/x');
            """)
            db.commit()
            db.close()
            with Store(path) as store:
                rows = store.sightings(["BKK-business-RT"])
                self.assertEqual([r["airline"] for r in rows], [""])
                store.add_sighting(["BKK-business-RT"], "FRA", "feed:errorfarealerts",
                                   datetime(2026, 11, 4, 7, 46), 1288, "radar:alt",
                                   "https://errorfarealerts.com/y", airline="turkmenistan")
                self.assertEqual([r["airline"] for r in store.sightings(["BKK-business-RT"])],
                                 ["", "turkmenistan"])
            with Store(path) as store:      # zweites Öffnen: Spalte schon da, kein Fehler
                self.assertEqual(len(store.sightings(["BKK-business-RT"])), 2)


if __name__ == "__main__":
    unittest.main()
