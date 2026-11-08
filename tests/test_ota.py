"""Tests für die Buchungsoptionen (OTA-Preise) der Google-Flights-Quelle.

``google_booking_ham_zrh.txt`` ist eine echte ``GetBookingResults``-Antwort vom 08.11.2026
(HAM→ZRH one-way 26.01.2027, Flug EW7762: Eurowings 60 €, Mytrip 80 €, SWISS 137 €,
Booking.com 159 € …). Für den Fall "Portal günstiger" wird Mytrip im Test auf 52 € gesetzt.
"""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

from dealmonitor import http
from dealmonitor.appsettings import Context
from dealmonitor.models import Alert, Offer
from dealmonitor.sources import googleflights as gf
from dealmonitor.sources.googleflights import GoogleBlocked, GoogleFlightsSource

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 4)
BOOK = (FIX / "google_booking_ham_zrh.txt").read_text(encoding="utf-8")
CHEAP = BOOK.replace("[[null,80],", "[[null,52],")          # Mytrip 52 € statt 80 €
#: One-way ZRH→HAM 04.01.2027, günstigster Flug Eurowings EW7761 für 126 €
OW = (FIX / "google_flights_zrh_ham_oneway_ds1.html").read_text(encoding="utf-8")


def alert(**kw):
    base = dict(id="ow", name="OW", origins=["ZRH"], destinations=["HAM"], trip="oneway",
                max_price=None, baseline_price=250, max_stops=1, months_ahead=1)
    base.update(kw)
    return Alert(**base)


def source(booking=CHEAP, **kw):
    """Quelle mit fester Ergebnisseite und festen Buchungsoptionen (``booking``: Text,
    Ausnahme oder Funktion); zeichnet beide Arten Abrufe auf."""
    pages, bodies = [], []

    def fetcher(url):
        pages.append(url)
        return OW

    def booking_fetcher(body):
        bodies.append(body)
        if isinstance(booking, Exception):
            raise booking
        return booking(body) if callable(booking) else booking

    opts = dict(max_requests=40, delay=0, today=TODAY, fetcher=fetcher, seed=1,
                booking_fetcher=booking_fetcher)
    opts.update(kw)
    src = GoogleFlightsSource(**opts)
    src.pages, src.bodies = pages, bodies
    return src


class BookingParserTest(unittest.TestCase):
    def test_fixture_options(self):
        opts = gf.parse_booking(BOOK)
        self.assertEqual([(o["seller"], o["price"]) for o in opts],
                         [("Eurowings", 60), ("Mytrip", 80), ("Tripado", 128), ("SWISS", 137),
                          ("Booking.com", 159), ("business-class.com", 409)])
        self.assertTrue(opts[0]["airline"])
        self.assertFalse(opts[1]["airline"])
        self.assertEqual(opts[4]["flight"], "LX4457")          # Booking.com über den Codeshare

    def test_summary_only_when_portal_cheaper(self):
        self.assertIsNone(gf.booking_summary(gf.parse_booking(BOOK), 60))
        s = gf.booking_summary(gf.parse_booking(CHEAP), 60)
        self.assertEqual(s, {"seller": "Mytrip", "price": 52.0, "airline_price": 60.0,
                             "airline_seller": "Eurowings"})
        self.assertEqual(gf.booking_text(s), "über Mytrip, bei Eurowings 60 €")
        self.assertIsNone(gf.booking_summary([], 60))

    def test_garbage_is_empty(self):
        for text in ("", "<html>Sorry</html>", ")]}'\n\n[[\"er\",null,null,null,null,400]]",
                     ")]}'\n\n[[\"wrb.fr\",null,\"[1,2]\"]]", ")]}'\n[[\"wrb.fr\",null,\"{kaputt\"]]",
                     ")]}'\n[[\"wrb.fr\",null,null,null,null,[7]]]"):
            self.assertEqual(gf.parse_booking(text), [], text)

    def test_request_body(self):
        body = gf.booking_request("HAM", "ZRH", date(2027, 1, 26),
                                  [["HAM", "2027-01-26", "ZRH", "EW", "7762"]], "business")
        outer = json.loads(parse_qs(body.decode())["f.req"][0])
        filt = json.loads(outer[1])[1]
        self.assertEqual((filt[2], filt[5]), (2, 3))             # one-way, Business
        leg = filt[13][0]
        self.assertEqual((leg[0][0][0][0], leg[1][0][0][0], leg[6]), ("HAM", "ZRH", "2027-01-26"))
        self.assertEqual(leg[8], [["HAM", "2027-01-26", "ZRH", None, "EW", "7762"]])

    def test_segments_attached_to_html_rows(self):
        rows = gf.parse_results(OW)
        gf.attach_segments(rows, OW)
        self.assertEqual(rows[0]["segments"], [["ZRH", "2027-01-04", "HAM", "EW", "7761"]])
        klm = next(r for r in rows if r["airline"] == "KLM")
        self.assertEqual([s[3] + s[4] for s in klm["segments"]], ["KL1918", "KL1755"])

    def test_airline_rows_and_extras(self):
        rows = [{"price": 52.0, "booking": {"seller": "Mytrip", "price": 52.0,
                                            "airline_price": 60.0, "airline_seller": "EW"}},
                {"price": 70.0}]
        self.assertEqual([r["price"] for r in gf.airline_rows(rows)], [60.0, 70.0])
        self.assertEqual(rows[0]["price"], 52.0)                 # Original unverändert
        o = Offer(source="google_flights", origin="ZRH", destination="HAM", price=52,
                  booking=rows[0]["booking"])
        self.assertEqual(o.extras()["booking"]["seller"], "Mytrip")
        self.assertNotIn("booking", Offer(source="x", origin="A", destination="B",
                                          price=1).extras())


class SourceOtaTest(unittest.TestCase):
    def test_cheaper_portal_sets_offer_price(self):
        src = source()
        offers = src.fetch(alert())
        n = len(src.pages)
        self.assertEqual(len(src.bodies), n)                     # je Abfrage eine Nachfrage
        self.assertEqual(src.requests_made, 2 * n)
        self.assertEqual(src.scans_made, 2 * n)                  # zählt aufs Scan-Budget
        self.assertEqual((src.ota_checked, src.ota_cheaper), (n, n))
        best = min(offers, key=lambda o: o.price)
        self.assertEqual(best.price, 52)
        self.assertEqual(best.airline, "Eurowings")              # Fingerprint unverändert
        self.assertEqual(best.booking, {"seller": "Mytrip", "price": 52.0,
                                        "airline_price": 126.0, "airline_seller": "Eurowings"})
        self.assertIn("EW", parse_qs(src.bodies[0].decode())["f.req"][0])
        self.assertTrue(any("Mytrip 52 € statt Eurowings 126 €" in x for x in src.notes))

    def test_not_cheaper_keeps_airline_price(self):
        src = source(booking=BOOK.replace("[[null,60],", "[[null,130],")
                     .replace("[[null,80],", "[[null,140],"))
        offers = src.fetch(alert())
        self.assertEqual(min(o.price for o in offers), 126)
        self.assertTrue(all(o.booking is None for o in offers))
        self.assertEqual(src.ota_cheaper, 0)

    def test_errors_never_break_the_fetch(self):
        for err in (http.HttpError("HTTP 500", 500), ValueError("kaputt"), "<html>nix</html>"):
            src = source(booking=err)
            offers = src.fetch(alert())
            self.assertTrue(offers, err)
            self.assertEqual(min(o.price for o in offers), 126, err)
            self.assertIsNone(src.incomplete, err)

    def test_blocked_turns_ota_off_for_the_run(self):
        src = source(booking=GoogleBlocked("HTTP 429"))
        offers = src.fetch(alert())
        self.assertEqual(len(src.bodies), 1)                     # danach keine Nachfrage mehr
        self.assertGreater(len(src.pages), 1)                    # Seiten laufen weiter
        self.assertIsNone(src.down)
        self.assertEqual(min(o.price for o in offers), 126)
        self.assertIn("gesperrt", src._ota_off)

    def test_switch_off_and_test_fetcher_without_booking(self):
        src = source(ota=False)
        src.fetch(alert())
        self.assertEqual((src.bodies, src.ota_checked), ([], 0))
        src = GoogleFlightsSource(max_requests=10, delay=0, today=TODAY, fetcher=lambda u: OW)
        src.fetch(alert())                                       # nie ins Netz
        self.assertEqual(src.ota_checked, 0)
        self.assertEqual(src.requests_made, 2)

    def test_limit_filters_candidates(self):
        src = source()
        src.fetch(alert(max_price=80))           # 126 > 80 × 1,3: nicht relevant
        self.assertEqual(src.bodies, [])
        src = source()
        src.fetch(alert(max_price=110))          # 126 und 141 ≤ 143: beide (Mytrip-Antwort gilt)
        n = len(src.pages)
        self.assertEqual(len(src.bodies), 2 * n)

    def test_budget_and_share(self):
        a = alert(months_ahead=6)
        for budget in (4, 10, 11):
            src = source(max_requests=budget)
            src.begin_run([a])
            share = src._shares[a.id]
            src.fetch(a)
            self.assertEqual(src.requests_made, share, budget)
            self.assertEqual(src.scans_made, share, budget)
            self.assertEqual(src.ota_checked, share // 5, budget)   # höchstens 1/5 des Anteils
            self.assertEqual(len(src.pages), share - share // 5, budget)
            if share // 5:
                self.assertIn("Limit", src.incomplete["message"])
        # Rückfahrt: keine Nachfrage
        rt = (FIX / "google_flights_zrh_ham.html").read_text(encoding="utf-8")
        calls = []
        src = GoogleFlightsSource(max_requests=40, delay=0, today=TODAY, fetcher=lambda u: rt,
                                  booking_fetcher=calls.append)
        self.assertTrue(src.fetch(alert(trip="roundtrip", min_nights=3, max_nights=10)))
        self.assertEqual(calls, [])

    def test_verify_uses_portal_price(self):
        src = source()
        hit = src.verify("ZRH", "HAM", date(2027, 1, 4), None, "economy", False)
        self.assertEqual(hit["price"], 52)
        self.assertEqual(hit["airline"], "Eurowings über Mytrip, bei Eurowings 126 €")
        self.assertEqual(hit["booking"]["airline_price"], 126)
        self.assertEqual((src.requests_made, src.verifications), (2, 1))
        # Budget für die Scans reserviert: nur die Seite, keine Nachfrage
        src = source(max_requests=10)
        src._scan_pending = 9
        hit = src.verify("ZRH", "HAM", date(2027, 1, 4), None, "economy", False)
        self.assertEqual((hit["price"], src.requests_made), (126, 1))
        # Fehler beim Nachholen: Airline-Preis
        src = source(booking=http.HttpError("HTTP 502", 502))
        hit = src.verify("ZRH", "HAM", date(2027, 1, 4), None, "economy", False)
        self.assertEqual(hit["price"], 126)
        self.assertNotIn("booking", hit)


class SettingTest(unittest.TestCase):
    def test_google_ota_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Context(tmp, Path(tmp) / "fehlt.toml", env={}, today=TODAY)
            for value, want in ((None, True), (False, False)):
                with ctx.store() as store:
                    app = ctx.settings(store)
                    if value is not None:
                        app.update({"google_ota": value})
                    src = ctx.sources(ctx.build_config(store, app), store, ["google_flights"],
                                      app)
                self.assertIs(src[0].ota, want)


if __name__ == "__main__":
    unittest.main()
