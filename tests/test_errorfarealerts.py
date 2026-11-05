"""ErrorFareAlerts.com: Karten der Deal-Liste lesen (Titel, Link, Zeit, Preis, IATA-Codes).

Fixtures unter tests/fixtures/feeds/ sind gekürzte Ausschnitte der echten Seiten vom
05.11.2026 (nur die Deal-Karten). Kein Netz: ``http.request`` wird ersetzt.
"""

import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from dealmonitor import http, regions
from dealmonitor.models import Alert
from dealmonitor.sources import SourceError
from dealmonitor.sources import errorfarealerts as efa
from dealmonitor.sources.feed import (
    PRESETS_BY_ID, FeedSource, Preset, Profile, analyze, normalize_item,
)

FIX = Path(__file__).parent / "fixtures" / "feeds"
NOW = datetime(2026, 11, 5, 11, 0, tzinfo=timezone.utc)
PROFILE = Profile(lang="de", price_from="title", default_origin="home")


def page(name="flights"):
    return (FIX / f"errorfarealerts_{name}.html").read_text(encoding="utf-8")


def card(title="✈️ Condor: Von Frankfurt nach New York ab 393 €", href="/deals/flights/x",
         when="04.11.2026 07:38", box="393", von="Frankfurt Flughafen (FRA)",
         nach="John F. Kennedy Flughafen (JFK)"):
    return (f'<div class="strip_all_tour_list wow"><a href="{href}">'
            f'<h3 class="mb-1">{title}</h3></a><div class="date mb-2"> {when}</div>'
            f'<p class="text-small">Kurz &amp; gut</p>'
            f'<div><strong>Von</strong> {von}</div><div><strong>nach</strong> {nach} </div>'
            f'<div class="price_list"><sup> € </sup> {box}<small>ab</small></div></div>')


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.items = efa.parse(page(), NOW)
        self.by_airline = {it["title"].split(":")[0]: it for it in self.items}

    def test_ten_cards_all_from_dach(self):
        self.assertEqual(len(self.items), 10)
        for it in self.items:
            self.assertFalse(it["title"].startswith("✈"), it["title"])
            self.assertTrue(it["origin_codes"])
            self.assertTrue(all(regions.is_home(c) for c in it["origin_codes"]), it["title"])
            self.assertEqual(len(it["dest_codes"]), 1)
            self.assertTrue(it["link"].startswith("https://errorfarealerts.com/deals/flights/"))
            self.assertTrue(it["link"].isascii(), it["link"])     # Emoji/€ im Slug kodiert
            self.assertIsNotNone(it["published"])
            self.assertIsNotNone(normalize_item(it, "loader:errorfarealerts",
                                                max_age_days=14, now=NOW))

    def test_prices(self):
        avianca = self.by_airline["Avianca Business Class Deal"]
        self.assertEqual(avianca["price_eur"], 1959.0)            # "ab 1.959 €"
        # Titel vor Preiskasten (Kasten sagt 1238, Titel 1.288 €)
        self.assertEqual(self.by_airline["Turkmenistan Airlines"]["price_eur"], 1288.0)
        # ohne Preis im Titel: Preiskasten
        self.assertEqual(self.by_airline["Air India Premium Economy"]["price_eur"], 1059.0)

    def test_codes_and_title_origins(self):
        avianca = self.by_airline["Avianca Business Class Deal"]
        self.assertEqual(avianca["origin_codes"], ["ZRH", "GVA"])  # Genf nur im Titel
        self.assertEqual(avianca["dest_codes"], ["PTY"])
        self.assertEqual(self.by_airline["Icelandair"]["origin_codes"], ["BER"])

    def test_time_as_berlin_local(self):
        tk = self.by_airline["Turkmenistan Airlines"]               # 04.11.2026 07:46 (MEZ)
        self.assertEqual(tk["published"], datetime(2026, 11, 4, 6, 46, tzinfo=timezone.utc))
        summer = efa.parse(page("fra_pek"), NOW)[0]                 # 20.07.2026 06:30 (MESZ)
        self.assertEqual(summer["published"], datetime(2026, 7, 20, 4, 30, tzinfo=timezone.utc))

    def test_analyze_business_and_premium(self):
        info = analyze(normalize_item(self.by_airline["Avianca Business Class Deal"]), PROFILE)
        self.assertEqual((info.flight, info.price, info.cabin, info.origin),
                         (True, 1959.0, "business", "home"))
        info = analyze(normalize_item(self.by_airline["Air India Premium Economy"]), PROFILE)
        self.assertEqual(info.cabin, "premium")

    def test_route_page_china_business(self):
        items = efa.parse(page("fra_pek"), NOW)
        self.assertEqual([it["price_eur"] for it in items], [1790.0, 1545.0, 1335.0])
        self.assertEqual(items[1]["title"][:19], "Business Class Deal")   # Flagge entfernt
        self.assertTrue(all(it["dest_codes"] == ["PEK"] for it in items))


class DefensiveTest(unittest.TestCase):
    def test_garbage_never_raises(self):
        for junk in ("", "<html>kaputt", None, card(href="")[:-80], "strip_all_tour_list"):
            self.assertIsInstance(efa.parse(junk, NOW), list)
        self.assertEqual(efa.parse('<div class="strip_all_tour_list">ohne Titel</div>'), [])

    def test_card_variants(self):
        it = efa.parse(card(when="kein Datum", box="kaputt", von="Irgendwo", nach=""), NOW)[0]
        self.assertIsNone(it["published"])
        self.assertEqual(it["price_eur"], 393.0)
        self.assertNotIn("dest_codes", it)
        self.assertEqual(it["origin_codes"], ["FRA"])                # aus dem Titel
        self.assertEqual(it["description"], "Kurz & gut")
        # unplausibler Titelpreis -> Preiskasten; beides unbrauchbar -> kein Preis
        it = efa.parse(card(title="Deal ab 0 €", box="455"), NOW)[0]
        self.assertEqual(it["price_eur"], 455.0)
        it = efa.parse(card(title="Deal ohne Preis", box="9999999"), NOW)[0]
        self.assertNotIn("price_eur", it)
        # Error Fare markiert, doppelte Karten nur einmal
        two = card(title="✈️ Error Fare: Von Wien nach Bali ab 299 €") * 2
        items = efa.parse(two, NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["hot"], "ERROR FARE")

    def test_future_time_capped(self):
        it = efa.parse(card(when="05.11.2026 23:59"), NOW)[0]
        self.assertEqual(it["published"], NOW)
        self.assertIsNone(efa.parse_time("31.02.2026 10:00"))

    def test_berlin_to_utc_switch_days(self):
        utc = timezone.utc
        cases = {
            datetime(2026, 3, 29, 1, 30): datetime(2026, 3, 29, 0, 30, tzinfo=utc),  # noch MEZ
            datetime(2026, 3, 29, 3, 30): datetime(2026, 3, 29, 1, 30, tzinfo=utc),  # MESZ
            datetime(2026, 10, 25, 2, 30): datetime(2026, 10, 25, 0, 30, tzinfo=utc),
            datetime(2026, 10, 25, 3, 30): datetime(2026, 10, 25, 2, 30, tzinfo=utc),
            datetime(2026, 12, 31, 23, 0): datetime(2026, 12, 31, 22, 0, tzinfo=utc),
        }
        for local, expected in cases.items():
            self.assertEqual(efa.berlin_to_utc(local), expected, local)

    def test_link_and_urls(self):
        it = efa.parse(card(href="/deals/flights/✈️-flug-ab-99-€-–-x"), NOW)[0]
        self.assertEqual(it["link"], "https://errorfarealerts.com/deals/flights/"
                                     "%E2%9C%88%EF%B8%8F-flug-ab-99-%E2%82%AC-%E2%80%93-x")
        self.assertEqual(efa.page_url(), "https://errorfarealerts.com/deals/flights")
        self.assertEqual(efa.page_url(0), efa.page_url(1))           # page=0 -> HTTP 500
        self.assertTrue(efa.page_url(2).endswith("?page=2"))
        self.assertEqual(efa.route_url("fra", "pek", 2), "https://errorfarealerts.com/deals/"
                                                         "flights?fromIata=FRA&toIata=PEK&page=2")


class FetchTest(unittest.TestCase):
    def test_fetch_items(self):
        body = page().encode("utf-8")
        with mock.patch.object(http, "request", return_value=body) as req:
            items = efa.fetch_items(timeout=7, state={})
        self.assertEqual(len(items), 10)
        self.assertEqual(req.call_args.args[0], "https://errorfarealerts.com/deals/flights")
        self.assertEqual(req.call_args.kwargs["timeout"], 7)

    def test_errors(self):
        with mock.patch.object(http, "request", side_effect=http.HttpError("HTTP 500", 500)):
            with self.assertRaisesRegex(SourceError, "nicht erreichbar"):
                efa.fetch_items()
        with mock.patch.object(http, "request", return_value=b"<html>Wartung</html>"):
            with self.assertRaisesRegex(SourceError, "keine Deals"):
                efa.fetch_items()

    def test_through_feed_source(self):
        preset = Preset("errorfarealerts", "ErrorFareAlerts", (), "de", True, PROFILE,
                        "https://errorfarealerts.com/", "Test",
                        loader="errorfarealerts:fetch_items")
        alert = Alert(id="de-cn-business", name="DE-CN", origins=["@DE_HUBS"],
                      destinations=["@CN_MAJOR"], cabin="business", max_price=1999,
                      feed_keywords=[["Peking", "Business"]])
        body = page("fra_pek").encode("utf-8")
        with mock.patch.dict(PRESETS_BY_ID, {"errorfarealerts": preset}), \
                mock.patch.object(http, "request", return_value=body):
            src = FeedSource(presets=["errorfarealerts"], max_age_days=400)
            offers = src.fetch(alert)
        self.assertEqual(sorted(o.price for o in offers), [1335.0, 1545.0, 1790.0])
        self.assertTrue(all(o.source == "feed:errorfarealerts" for o in offers))
        self.assertEqual(src.stats["feed:errorfarealerts"]["items"], 3)
        self.assertIsNone(src.stats["feed:errorfarealerts"]["error"])


if __name__ == "__main__":
    unittest.main()
