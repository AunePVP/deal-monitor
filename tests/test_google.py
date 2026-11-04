"""Tests für die Google-Flights-Quelle (Parser, Datumsplanung, Budget, Sperre, Gegenprüfung).

Die Fixtures sind gekürzte Ausschnitte echter Google-Flights-Antworten vom 04.11.2026
(nur Ergebnisblöcke + ausgewählte Suchoptionen); der Parser liefert darauf dieselben
Ergebnisse wie auf den 2,4-MB-Vollseiten.
"""

import gzip
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from dealmonitor import config as config_mod
from dealmonitor import http
from dealmonitor.models import Alert
from dealmonitor.monitor import run_alert
from dealmonitor.sources import SourceError
from dealmonitor.sources import googleflights as gf
from dealmonitor.sources.googleflights import (
    GoogleBlocked, GoogleFlightsSource, ParseError, blocked_reason, build_url, page_settings,
    parse_label, parse_results, scan_dates, stay_nights,
)
from dealmonitor.store import Store

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 4)
ZRH_HAM = (FIX / "google_flights_zrh_ham.html").read_text(encoding="utf-8")
FRA_SHA_OW = (FIX / "google_flights_fra_sha_oneway.html").read_text(encoding="utf-8")
FRA_BJS_BIZ = (FIX / "google_flights_fra_bjs_business.html").read_text(encoding="utf-8")
CONSENT = ('<html><head><title>Bevor du zu Google weitergehst</title></head><body>'
           '<form action="https://consent.google.com/save" method="POST"></form></body></html>')
NO_FLIGHTS = '<html><body><div>Für diese Suche wurden keine Flüge gefunden.</div></body></html>'


def alert(**kw):
    base = dict(id="t", name="Test", origins=["ZRH"], destinations=["HAM"], max_price=200,
                baseline_price=250, max_stops=1, min_nights=1, max_nights=21, months_ahead=1)
    base.update(kw)
    return Alert(**base)


def source(page=ZRH_HAM, **kw):
    """Quelle mit festem Seiteninhalt statt Netz; zeichnet die abgefragten URLs auf."""
    calls = []

    def fetcher(url):
        calls.append(url)
        return page(url) if callable(page) else page

    opts = dict(max_requests=40, delay=0, today=TODAY, fetcher=fetcher, seed=1)
    opts.update(kw)
    src = GoogleFlightsSource(**opts)
    src.calls = calls
    return src


class UrlTest(unittest.TestCase):
    def test_roundtrip_economy(self):
        url = build_url("zrh", "ham", date(2027, 1, 15), date(2027, 1, 19))
        self.assertEqual(url, "https://www.google.com/travel/flights?hl=de&curr=EUR&q="
                              "Flights%20from%20ZRH%20to%20HAM%20on%202027-01-15%20through%202027-01-19")

    def test_oneway_and_cabin(self):
        self.assertTrue(build_url("FRA", "SHA", date(2027, 3, 12), None, "economy", False)
                        .endswith("on%202027-03-12%20one%20way"))
        self.assertTrue(build_url("FRA", "BJS", date(2027, 3, 12), date(2027, 3, 24), "business")
                        .endswith("through%202027-03-24%20business%20class"))
        # Rückflugdatum ohne roundtrip -> one-way
        self.assertIn("one%20way", build_url("FRA", "BJS", date(2027, 3, 12), date(2027, 3, 24),
                                             roundtrip=False))


class ParserTest(unittest.TestCase):
    def test_zrh_ham_fixture(self):
        rows = parse_results(ZRH_HAM)
        self.assertEqual(len(rows), 11, "11 verschiedene Flüge (jeder steht 2x auf der Seite)")
        self.assertEqual([r["price"] for r in rows],
                         [168, 183, 183, 208, 208, 208, 220, 220, 220, 226, 291])
        best = rows[0]
        self.assertEqual((best["airline"], best["airline_code"], best["stops"]), ("KLM", "KL", 1))
        self.assertEqual((best["duration_min"], best["departure_time"]), (215, "14:20"))
        self.assertEqual(best["departure_airport"], "Zürich")
        self.assertTrue(best["roundtrip"])
        swiss = [r for r in rows if r["price"] == 183]
        self.assertEqual({r["stops"] for r in swiss}, {0})
        self.assertEqual({r["airline_code"] for r in swiss}, {"LX"})
        self.assertEqual(sorted(r["departure_time"] for r in swiss), ["07:00", "08:40"])
        multi = rows[-1]
        self.assertEqual(multi["airline"], "SWISS und Brussels Airlines")
        self.assertIsNone(multi["airline_code"], "mehrere Airlines -> kein eindeutiger Code")
        self.assertEqual(multi["stops"], 1)
        self.assertEqual(page_settings(ZRH_HAM), {"trip": "roundtrip", "cabin": "economy"})

    def test_oneway_fixture(self):
        rows = parse_results(FRA_SHA_OW)
        self.assertEqual(len(rows), 8)
        self.assertEqual([r["price"] for r in rows][:4], [697, 709, 720, 1090])
        self.assertEqual((rows[0]["airline"], rows[0]["airline_code"]), ("Cathay Pacific", "CX"))
        self.assertEqual(rows[0]["duration_min"], 17 * 60 + 20)
        self.assertFalse(rows[0]["roundtrip"], '"Ab 697 Euro." ohne "für Hin- und Rückflug"')
        two = next(r for r in rows if r["airline"] == "Austrian und Air China")
        self.assertEqual(two["stops"], 2)   # "mit 2 Zwischenlandungen"
        self.assertEqual(page_settings(FRA_SHA_OW), {"trip": "oneway", "cabin": "economy"})

    def test_business_fixture(self):
        rows = parse_results(FRA_BJS_BIZ)
        self.assertEqual(len(rows), 11)
        self.assertEqual(rows[0]["price"], 2690)
        self.assertEqual((rows[0]["airline"], rows[0]["airline_code"], rows[0]["stops"]),
                         ("China Southern", "CZ", 1))
        nonstop = [r for r in rows if r["stops"] == 0]
        self.assertEqual({r["airline"] for r in nonstop}, {"Air China"})
        self.assertEqual({r["duration_min"] for r in nonstop}, {570})
        hours_only = next(r for r in rows if r["price"] == 3588)
        self.assertEqual(hours_only["duration_min"], 900, '"Gesamtdauer: 15 Std.." ohne Minuten')
        self.assertEqual(page_settings(FRA_BJS_BIZ), {"trip": "roundtrip", "cabin": "business"})

    def test_label_variants(self):
        row = parse_label("Ab 1.234 Euro f&uuml;r Hin- und R&uuml;ckflug. Flug mit Lufthansa mit "
                          "2 Zwischenlandungen. Abflug von Flughafen Frankfurt am Montag, März 1, "
                          "06:05 und Ankunft an Flughafen Peking am Dienstag, März 2, 09:10. "
                          "Gesamtdauer: 20 Std..")
        self.assertEqual((row["price"], row["airline"], row["stops"], row["duration_min"]),
                         (1234, "Lufthansa", 2, 1200))
        self.assertEqual(row["departure_time"], "06:05")
        self.assertTrue(row["roundtrip"])
        row = parse_label("Ab 89 Euro. Nonstop-Flug mit easyJet. Durchgeführt von easyJet Europe. "
                          "Abflug von Flughafen Basel am Freitag, Januar 15, 07:00 und Ankunft an "
                          "Flughafen Hamburg am Freitag, Januar 15, 08:35. Gesamtdauer: 45 Min..")
        self.assertEqual((row["price"], row["airline"], row["stops"], row["duration_min"]),
                         (89, "easyJet", 0, 45))
        self.assertFalse(row["roundtrip"])
        self.assertIsNone(parse_label("Preis nicht verfügbar für Hin- und Rückflug."))

    def test_price_span_fallback(self):
        html = ('<div><span aria-label="183 Euro" role="text">183 €</span>'
                '<span aria-label="1.299 Euro" role="text">1.299 €</span>'
                '<span aria-label="183 Euro" role="text">183 €</span></div>')
        rows = parse_results(html)
        self.assertEqual([r["price"] for r in rows], [183, 1299])
        self.assertIsNone(rows[0]["airline"])

    def test_empty(self):
        self.assertEqual(parse_results(""), [])
        self.assertEqual(parse_results(NO_FLIGHTS), [])
        self.assertEqual(page_settings("<html></html>"), {"trip": None, "cabin": None})


class BlockDetectionTest(unittest.TestCase):
    def test_normal_page_is_not_blocked(self):
        for page in (ZRH_HAM, FRA_SHA_OW, FRA_BJS_BIZ):
            self.assertIsNone(blocked_reason("https://www.google.com/travel/flights?q=x", page))

    def test_consent_redirect_and_pages(self):
        self.assertIsNotNone(blocked_reason("https://consent.google.com/m?continue=x", ZRH_HAM))
        self.assertIsNotNone(blocked_reason("https://www.google.com/sorry/index?continue=x", ""))
        self.assertIsNotNone(blocked_reason("https://www.google.com/travel/flights", CONSENT))
        sorry = ("<html><body>Unsere Systeme haben ungewöhnlichen Datenverkehr aus Ihrem "
                 'Computernetzwerk festgestellt.<div id="recaptcha"></div></body></html>')
        self.assertIsNotNone(blocked_reason("https://www.google.com/travel/flights", sorry))

    def _response(self, body, url="https://www.google.com/travel/flights?q=x", **headers):
        return http.Response(body, 200, headers, url)

    def test_fetch_html_decodes_gzip(self):
        resp = self._response(gzip.compress(ZRH_HAM.encode()), **{"Content-Encoding": "gzip"})
        with mock.patch("dealmonitor.sources.googleflights.http.request", return_value=resp) as req:
            html = gf.fetch_html("https://www.google.com/travel/flights?q=x")
        self.assertEqual(html, ZRH_HAM)
        kw = req.call_args.kwargs
        self.assertTrue(kw["full"])
        self.assertEqual(kw["retries"], 0, "bei Google nie stur wiederholen")
        self.assertEqual(kw["timeout"], 30)
        self.assertIn("Chrome", kw["headers"]["User-Agent"])
        self.assertEqual(kw["headers"]["Accept-Language"], "de-DE,de;q=0.9")

    def test_fetch_html_blocked(self):
        with mock.patch("dealmonitor.sources.googleflights.http.request",
                        return_value=self._response(ZRH_HAM.encode(), url="https://consent.google.com/m")):
            with self.assertRaises(GoogleBlocked):
                gf.fetch_html("https://www.google.com/travel/flights?q=x")
        with mock.patch("dealmonitor.sources.googleflights.http.request",
                        return_value=self._response(CONSENT.encode())):
            with self.assertRaises(GoogleBlocked):
                gf.fetch_html("https://www.google.com/travel/flights?q=x")
        for status in (403, 429):
            with mock.patch("dealmonitor.sources.googleflights.http.request",
                            side_effect=http.HttpError("zu viele Anfragen", status)):
                with self.assertRaises(GoogleBlocked):
                    gf.fetch_html("https://www.google.com/travel/flights?q=x")
        # andere HTTP-Fehler bleiben HttpError (kein Schutzschalter)
        with mock.patch("dealmonitor.sources.googleflights.http.request",
                        side_effect=http.HttpError("kaputt", 503)):
            with self.assertRaises(http.HttpError):
                gf.fetch_html("https://www.google.com/travel/flights?q=x")

    def test_http_full_response(self):
        """http.request(full=True) liefert Body, Status, Header und endgültige URL."""
        class FakeResp:
            status = 200
            headers = {"Content-Type": "text/html"}

            def read(self):
                return b"<html>ok</html>"

            def geturl(self):
                return "https://www.google.com/travel/flights?final"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
            resp = http.request("https://www.google.com/travel/flights", full=True)
            raw = http.request("https://www.google.com/travel/flights")
        self.assertIsInstance(resp, http.Response)
        self.assertEqual((resp.body, resp.status, resp.url),
                         (b"<html>ok</html>", 200, "https://www.google.com/travel/flights?final"))
        self.assertEqual(resp.header("content-type"), "text/html")
        self.assertEqual(raw, b"<html>ok</html>")


class PlanningTest(unittest.TestCase):
    def test_scan_dates_mid_month(self):
        self.assertEqual(scan_dates(2027, 1, TODAY), [date(2027, 1, 15), date(2027, 1, 12)])
        self.assertEqual(scan_dates(2026, 11, TODAY), [date(2026, 11, 13), date(2026, 11, 17)])
        for d in scan_dates(2027, 1, TODAY):
            self.assertIn(d.weekday(), (4, 1))   # Freitag, Dienstag

    def test_scan_dates_shift_or_skip_in_current_month(self):
        self.assertEqual(scan_dates(2026, 11, date(2026, 11, 20)),
                         [date(2026, 11, 27), date(2026, 11, 24)])
        self.assertEqual(scan_dates(2026, 11, date(2026, 11, 28)), [])

    def test_stay_nights(self):
        self.assertEqual(stay_nights(alert(), "ZRH", "HAM"), 11)        # Mitte aus 1..21
        self.assertEqual(stay_nights(alert(min_nights=None, max_nights=None), "ZRH", "HAM"), 7)
        self.assertEqual(stay_nights(alert(min_nights=None, max_nights=None), "FRA", "BJS"), 12)
        self.assertEqual(stay_nights(alert(min_nights=None, max_nights=None), "FRA", "LIS"), 7)
        self.assertEqual(stay_nights(alert(min_nights=3, max_nights=None), "ZRH", "HAM"), 7)
        self.assertEqual(stay_nights(alert(min_nights=None, max_nights=4), "FRA", "SHA"), 4)
        self.assertEqual(stay_nights(alert(min_nights=20, max_nights=None), "ZRH", "HAM"), 20)

    def test_plan_routes_random_months_near_to_far(self):
        src = source()
        a = alert(origins=["ZRH", "BSL", "GVA"], destinations=["HAM"], months_ahead=2)
        plan = src.plan(a)
        self.assertEqual(len(plan), 3 * 2 * 2)
        self.assertEqual({p[0] for p in plan}, {"ZRH", "BSL", "GVA"})
        for i in range(0, len(plan), 4):
            route = plan[i:i + 4]
            self.assertEqual(len({r[0] for r in route}), 1, "Strecke für Strecke")
            self.assertEqual([r[2].month for r in route], [11, 11, 12, 12], "Monate nah -> fern")
            self.assertEqual([r[3] - r[2] for r in route], [gf.timedelta(days=11)] * 4)
        oneway = src.plan(alert(trip="oneway"))
        self.assertTrue(all(p[3] is None for p in oneway))


class SourceTest(unittest.TestCase):
    def test_fetch_offers(self):
        src = source()
        offers = src.fetch(alert())
        self.assertEqual(len(src.calls), 2, "2 Termine x 1 Monat")
        self.assertEqual(src.requests_made, 2)
        self.assertEqual(len(offers), 20, "günstigste 10 je Abfrage")
        self.assertIsNone(src.incomplete)
        o = min(offers, key=lambda o: o.price)
        self.assertEqual((o.source, o.origin, o.destination, o.price, o.currency),
                         ("google_flights", "ZRH", "HAM", 168.0, "EUR"))
        self.assertEqual((o.airline, o.stops, o.duration_out_min, o.duration_back_min),
                         ("KLM", 1, 215, None))
        self.assertTrue(o.roundtrip)
        self.assertEqual(o.cabin, "economy")
        self.assertIn(o.departure, (date(2026, 11, 13), date(2026, 11, 17)))
        self.assertEqual(o.nights, 11)
        self.assertEqual(o.link, build_url("ZRH", "HAM", o.departure, o.return_date))
        self.assertIn(o.link, src.calls)
        self.assertTrue(GoogleFlightsSource.records_history)

    def test_exclude_airlines_by_code(self):
        offers = source().fetch(alert(exclude_airlines=["kl"]))
        self.assertFalse(any(o.airline == "KLM" for o in offers))
        self.assertTrue(any(o.airline == "SWISS" for o in offers))

    def test_budget_limits_requests_and_reports_incomplete(self):
        src = source(max_requests=3)
        a = alert(origins=["ZRH", "GVA"], destinations=["HAM"])
        offers = src.fetch(a)
        self.assertEqual(src.requests_made, 3)
        self.assertEqual(len(offers), 30)
        self.assertEqual(src.incomplete["message"], "Google Flights: 3 von 4 Abfragen (Limit)")
        self.assertEqual(len(src.incomplete["routes"]), 1, "nur die unvollständige Strecke")
        self.assertEqual(src.remaining, 0)
        # nächster Alarm im selben Lauf: Budget aufgebraucht -> keine neue Anfrage, nur noch
        # gecachte Seiten, Strecke gilt als unvollständig (nichts als abgelaufen markieren)
        offers = src.fetch(alert(origins=["BSL"]))
        self.assertEqual(len(src.calls), 3, "keine neue Anfrage mehr")
        self.assertEqual(offers, [])
        self.assertEqual(src.incomplete["routes"], {("BSL", "HAM")})
        self.assertEqual(src.incomplete["message"], "Google Flights: 0 von 2 Abfragen (Limit)")
        src.reset()
        self.assertEqual((src.requests_made, src.remaining), (0, 3))

    def test_cache_within_run(self):
        src = source()
        src.fetch(alert())
        src.fetch(alert(id="t2", origins=["ZRH"], destinations=["HAM"]))
        self.assertEqual(src.requests_made, 2, "gleiche URL im selben Lauf nur einmal laden")

    def test_pause_between_requests(self):
        src = source(delay=3.0)
        src._last_request_at = gf.time.monotonic()
        with mock.patch("dealmonitor.sources.googleflights.time.sleep") as sleep:
            src.fetch(alert())
        waits = [c.args[0] for c in sleep.call_args_list]
        self.assertEqual(len(waits), 2)
        for w in waits:
            self.assertTrue(1.9 <= w <= 4.0, w)
        src = source(delay=0)
        with mock.patch("dealmonitor.sources.googleflights.time.sleep") as sleep:
            src.fetch(alert())
        sleep.assert_not_called()

    def test_blocked_trips_breaker_for_the_run(self):
        src = source(page=lambda url: (_ for _ in ()).throw(GoogleBlocked("HTTP 429")))
        with self.assertRaises(SourceError) as cm:
            src.fetch(alert())
        self.assertIn("blockiert gerade", str(cm.exception))
        self.assertEqual(len(src.calls), 1, "sofort abbrechen")
        self.assertTrue(src.down)
        with self.assertRaises(SourceError):
            src.fetch(alert(id="t2"))
        self.assertEqual(len(src.calls), 1, "im selben Lauf nicht erneut anfragen")
        self.assertIsNone(src.verify("ZRH", "HAM", date(2027, 1, 15), date(2027, 1, 19)))
        self.assertIn("blockiert", src.last_error)
        self.assertEqual(len(src.calls), 1)

    def test_unparseable_pages_count_as_errors_then_breaker(self):
        src = source(page="<html><body>völlig neues Layout</body></html>")
        a = alert(origins=["ZRH", "GVA"], destinations=["HAM"])
        with self.assertRaises(SourceError) as cm:
            src.fetch(a)
        self.assertEqual(len(src.calls), GoogleFlightsSource.MAX_CONSECUTIVE_FAILURES)
        self.assertIn("keine auswertbaren Ergebnisse", str(cm.exception))

    def test_no_flights_page_is_not_an_error(self):
        src = source(page=NO_FLIGHTS)
        self.assertEqual(src.fetch(alert()), [])
        self.assertIsNone(src.incomplete)
        self.assertFalse(src.down)

    def test_network_error_marks_route_incomplete(self):
        pages = iter([ZRH_HAM, http.HttpError("Zeitüberschreitung", None, network=True)])

        def fetcher(url):
            item = next(pages)
            if isinstance(item, Exception):
                raise item
            return item

        src = GoogleFlightsSource(max_requests=40, delay=0, today=TODAY, fetcher=fetcher)
        offers = src.fetch(alert())
        self.assertEqual(len(offers), 10)
        self.assertEqual(src.incomplete["routes"], {("ZRH", "HAM")})
        self.assertIn("1 von 2 Abfragen fehlgeschlagen", src.incomplete["message"])
        src = GoogleFlightsSource(max_requests=40, delay=0, today=TODAY,
                                 fetcher=lambda url: (_ for _ in ()).throw(http.HttpError("weg", 503)))
        with self.assertRaises(SourceError) as cm:
            src.fetch(alert())
        self.assertIn("Alle 2", str(cm.exception))

    def test_cabin_and_trip_safety_net(self):
        # Google liefert Economy-Seite für Business-Suche -> keine Economy-Preise als Business
        src = source(page=ZRH_HAM)
        with self.assertRaises(SourceError):
            src.fetch(alert(cabin="business", max_price=1500))
        # Business-Seite für Business-Alarm ist in Ordnung
        src = source(page=FRA_BJS_BIZ)
        offers = src.fetch(alert(origins=["FRA"], destinations=["BJS"], cabin="business",
                                 max_price=3000, baseline_price=4000))
        self.assertEqual({o.cabin for o in offers}, {"business"})
        self.assertEqual(min(o.price for o in offers), 2690)
        # One-way-Seite für Hin/Rück-Suche -> verwerfen
        src = source(page=FRA_SHA_OW)
        with self.assertRaises(SourceError):
            src.fetch(alert(origins=["FRA"], destinations=["SHA"]))
        offers = src.fetch(alert(origins=["FRA"], destinations=["SHA"], trip="oneway",
                                 max_price=800, baseline_price=900))
        self.assertEqual(min(o.price for o in offers), 697)
        self.assertTrue(all(not o.roundtrip and o.return_date is None for o in offers))

    def test_verify_only_makes_no_scan(self):
        src = source(verify_only=True)
        self.assertEqual(src.fetch(alert()), [])
        self.assertEqual(src.calls, [])
        self.assertIsNone(src.incomplete)

    def test_verify(self):
        src = source()
        hit = src.verify("ZRH", "HAM", date(2027, 1, 15), date(2027, 1, 19), "economy", True)
        self.assertEqual(hit, {"price": 168.0, "airline": "KLM", "stops": 1,
                               "link": build_url("ZRH", "HAM", date(2027, 1, 15), date(2027, 1, 19))})
        self.assertEqual((src.requests_made, src.verifications), (1, 1))
        self.assertIsNone(src.last_error)
        # gleiche Daten nochmal: aus dem Cache, keine Anfrage, zählt nicht als Gegenprüfung
        src.verify("ZRH", "HAM", date(2027, 1, 15), date(2027, 1, 19))
        self.assertEqual((src.requests_made, src.verifications), (1, 1))

    def test_verify_limits(self):
        src = source(max_requests=4)
        self.assertEqual(src.verify_limit, 2)
        for day in (10, 11):
            self.assertIsNotNone(src.verify("ZRH", "HAM", date(2027, 1, day), date(2027, 1, 19)))
        self.assertIsNone(src.verify("ZRH", "HAM", date(2027, 1, 12), date(2027, 1, 19)))
        self.assertIn("Gegenprüf-Limit", src.last_error)
        self.assertEqual(src.requests_made, 2)
        # Gesamtbudget erschöpft
        src = source(max_requests=2)
        src.fetch(alert())
        self.assertIsNone(src.verify("ZRH", "HAM", date(2027, 1, 12), date(2027, 1, 19)))
        self.assertIn("Limit", src.last_error)

    def test_verify_without_results_or_with_errors(self):
        src = source(page=NO_FLIGHTS)
        self.assertIsNone(src.verify("ZRH", "HAM", date(2027, 1, 15), date(2027, 1, 19)))
        self.assertIn("keine Flüge", src.last_error)
        src = source(page=lambda url: (_ for _ in ()).throw(http.HttpError("weg", None, network=True)))
        self.assertIsNone(src.verify("ZRH", "HAM", date(2027, 1, 15), date(2027, 1, 19)))
        self.assertIn("nicht erreichbar", src.last_error)
        self.assertFalse(src.down, "Netzwerkfehler ist keine Sperre")


class MonitorIntegrationTest(unittest.TestCase):
    """Die Quelle im normalen Lauf: Deals, Historie, unvollständige Läufe."""

    def setUp(self):
        self.store = Store(":memory:")
        self.cfg = config_mod.Config(settings=config_mod.Settings(), alerts=[], path=Path("x"))

    def tearDown(self):
        self.store.close()

    def test_run_alert_finds_deal_and_records_history(self):
        src = source()
        a = alert(max_price=170)
        items, res = run_alert(a, [src], self.store, self.cfg, today=TODAY)
        self.assertEqual(res.errors, [])
        self.assertEqual(res.offers, 20)
        self.assertEqual([d.offer.price for d, _ in items], [168.0])
        d, status = items[0]
        self.assertEqual(status, "new")
        self.assertEqual(d.offer.source, "google_flights")
        self.assertIn("KLM", d.headline)
        self.assertTrue(d.offer.link.startswith("https://www.google.com/travel/flights?"))
        # 5 verschiedene Preise je Abfrage x 2 Termine (Historie ist pro Preis/Tag eindeutig)
        self.assertEqual(sorted(self.store.history("ZRH-HAM-RT-economy", 60)),
                         sorted([168, 183, 208, 220, 226] * 2),
                         "Live-Preise fließen in die Historie ein")

    def test_limit_reported_but_nothing_expired(self):
        src = source()
        run_alert(alert(max_price=170), [src], self.store, self.cfg, today=TODAY)
        src = source(max_requests=0)
        items, res = run_alert(alert(max_price=170), [src], self.store, self.cfg, today=TODAY)
        self.assertEqual(items, [])
        self.assertEqual(res.expired, 0, "nicht abgefragt != verschwunden")
        self.assertTrue(any("Limit" in e for e in res.errors))
        self.assertEqual(len(self.store.deals(status="active")), 1)


if __name__ == "__main__":
    unittest.main()
