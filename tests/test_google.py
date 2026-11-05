"""Tests für die Google-Flights-Quelle (Parser, Datumsplanung, Budget, Sperre, Gegenprüfung).

Die Fixtures sind gekürzte Ausschnitte echter Google-Flights-Antworten vom 04.11.2026
(nur Ergebnisblöcke + ausgewählte Suchoptionen); der Parser liefert darauf dieselben
Ergebnisse wie auf den 2,4-MB-Vollseiten.
"""

import gzip
import re
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from dealmonitor import config as config_mod
from dealmonitor import http
from dealmonitor.models import Alert
from dealmonitor.monitor import run_alert
from dealmonitor.sources import SourceError
from dealmonitor.sources import googleflights as gf
from dealmonitor.sources.googleflights import (
    EmptyPage, GoogleBlocked, GoogleFlightsSource, ParseError, blocked_reason, build_tfs_url,
    build_url, empty_kind, page_settings, parse_label, parse_results, scan_dates, search_urls,
    short_stay, stay_nights,
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
#: gekürzte echte Seiten ohne Ergebnisse (05.11.2026), siehe Kommentar in den Dateien
UNPARSED = (FIX / "google_flights_unparsed.html").read_text(encoding="utf-8")
NO_ITIN = (FIX / "google_flights_rt_no_itineraries.html").read_text(encoding="utf-8")
ERROR13 = (FIX / "google_flights_error13.html").read_text(encoding="utf-8")
FRA_PEK = (FIX / "google_flights_fra_pek_ds1.html").read_text(encoding="utf-8")
#: Verbindungen nur in ds:1, keine Ergebniszeilen im HTML (FRA→PEK Business, live 05.11.2026)
DS1_ONLY = (FIX / "google_flights_ds1_only.html").read_text(encoding="utf-8")


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
        self.assertEqual(kw["max_bytes"], gf.MAX_PAGE_BYTES)              # keine Riesen-Antworten
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

            def __init__(self):
                self.rest = b"<html>ok</html>"

            def read(self, n=-1):           # wie HTTPResponse: stückweise, am Ende b""
                out, self.rest = self.rest, b""
                return out

            def geturl(self):
                return "https://www.google.com/travel/flights?final"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: FakeResp()):
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
        # Standard (7 Europa / 12 Langstrecke), begrenzt auf min/max - nicht die Mitte
        self.assertEqual(stay_nights(alert(), "ZRH", "HAM"), 7)         # 1..21
        self.assertEqual(stay_nights(alert(min_nights=5, max_nights=30), "FRA", "SHA"), 12)
        self.assertEqual(stay_nights(alert(min_nights=5, max_nights=30), "HAJ", "HGH"), 12)
        self.assertEqual(stay_nights(alert(min_nights=14, max_nights=30), "FRA", "SHA"), 14)
        self.assertEqual(stay_nights(alert(min_nights=2, max_nights=5), "ZRH", "HAM"), 5)
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
            self.assertEqual([r[3] - r[2] for r in route], [gf.timedelta(days=7)] * 4)
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
        self.assertEqual(o.nights, 7)
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

    def test_network_down_trips_breaker(self):
        calls = []

        def fetcher(url):
            calls.append(url)
            raise http.HttpError("Name or service not known", None, network=True)

        src = GoogleFlightsSource(max_requests=40, delay=0, today=TODAY, fetcher=fetcher)
        a = alert(origins=["ZRH", "GVA", "BSL"], destinations=["HAM"])
        with self.assertRaises(SourceError) as cm:
            src.fetch(a)
        self.assertEqual(len(calls), GoogleFlightsSource.MAX_CONSECUTIVE_FAILURES)
        self.assertIn("nicht erreichbar", str(cm.exception))
        self.assertIsNone(src.verify("ZRH", "HAM", date(2027, 1, 15), date(2027, 1, 19)))
        self.assertEqual(len(calls), GoogleFlightsSource.MAX_CONSECUTIVE_FAILURES)

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
        self.assertEqual(hit, {"price": 168.0, "airline": "KLM", "airline_code": "KL", "stops": 1,
                               "duration_min": 215,
                               "link": build_url("ZRH", "HAM", date(2027, 1, 15), date(2027, 1, 19))})
        self.assertEqual((src.requests_made, src.verifications), (1, 1))
        self.assertIsNone(src.last_error)
        # gleiche Daten nochmal: aus dem Cache, keine Anfrage, zählt nicht als Gegenprüfung
        src.verify("ZRH", "HAM", date(2027, 1, 15), date(2027, 1, 19))
        self.assertEqual((src.requests_made, src.verifications), (1, 1))

    def test_verify_respects_alert_rules(self):
        # Fixture: 168 € KLM (1 Umstieg, 3:35 h), 183 € SWISS nonstop (1:35 h), ...
        src = source()
        args = ("ZRH", "HAM", date(2027, 1, 15), date(2027, 1, 19), "economy", True)
        hit = src.verify(*args, max_stops=0)
        self.assertEqual((hit["price"], hit["airline"], hit["stops"], hit["duration_min"]),
                         (183.0, "SWISS", 0, 90))
        hit = src.verify(*args, max_minutes=120, exclude_airlines=["KL"])
        self.assertEqual((hit["price"], hit["stops"]), (183.0, 0))
        hit = src.verify(*args, exclude_airlines=["LX", "KL", "EW", "BA"])
        self.assertEqual(hit["price"], 291.0)        # SWISS und Brussels Airlines (ohne Code)
        # Google zeigt Flüge, aber keiner passt -> kein Preis, mit Grund
        hit = src.verify(*args, max_minutes=60)
        self.assertIsNone(hit["price"])
        self.assertEqual(hit["cheapest"], 168.0)
        self.assertIn("kein passender Flug", hit["reason"])
        self.assertEqual(src.requests_made, 1, "alles aus dem Cache")

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


class BudgetTest(unittest.TestCase):
    """Budget: Reserve für die Gegenprüfung, faire Verteilung auf die Alarme."""

    def test_rules_filter_before_cut_to_ten(self):
        rows = [{"price": 100.0 + i, "airline": "KLM", "airline_code": "KL", "stops": 1,
                 "duration_min": 200, "roundtrip": True} for i in range(12)]
        rows.append({"price": 150.0, "airline": "SWISS", "airline_code": "LX", "stops": 0,
                     "duration_min": 95, "roundtrip": True})
        src = source()
        offers = src.to_offers(rows, alert(max_stops=0), "ZRH", "HAM", date(2027, 1, 15),
                               date(2027, 1, 19), "https://g/")
        self.assertEqual([(o.price, o.stops) for o in offers], [(150.0, 0)])
        offers = src.to_offers(rows, alert(exclude_airlines=["LX"]), "ZRH", "HAM",
                               date(2027, 1, 15), date(2027, 1, 19), "https://g/")
        self.assertEqual(len(offers), 10)
        self.assertNotIn("SWISS", {o.airline for o in offers})

    def test_reserve_left_for_verification(self):
        src = source(max_requests=4, verify_reserve=2)
        src.fetch(alert(months_ahead=2))              # 4 Termine geplant
        self.assertEqual(src.requests_made, 2)
        self.assertIn("2 von 4 Abfragen (Limit)", src.incomplete["message"])
        self.assertIsNotNone(src.verify("ZRH", "HAM", date(2027, 3, 3), date(2027, 3, 9)))
        self.assertEqual(src.requests_made, 3)

    def test_budget_shared_fairly_between_alerts(self):
        src = source(max_requests=40, verify_reserve=10)
        small = alert(id="klein", origins=["HAM"], destinations=["ZRH"],
                      months_ahead=1)                                   # 2 Abfragen
        mid = alert(id="mittel", months_ahead=6)                        # 12 Abfragen
        china = alert(id="china", origins=["@DE"], destinations=["@CN_MAJOR"],
                      months_ahead=6, max_price=500)                    # 792 Abfragen
        src.begin_run([mid, small, china])
        self.assertEqual(src._shares, {"klein": 2, "mittel": 12, "china": 16})
        for a in (mid, small, china):
            src.fetch(a)
        self.assertEqual(src.requests_made, 30)
        self.assertIn("16 von 792 Abfragen (Limit)", src.incomplete["message"])
        # ohne Planung (direkter Aufruf) gilt wie bisher das ganze Scan-Budget
        src = source(max_requests=3)
        src.fetch(alert(months_ahead=6))
        self.assertEqual(src.requests_made, 3)

    def test_verifications_do_not_starve_later_alerts(self):
        """Gegenprüfungen des ersten Alarms dürfen den späteren Alarmen ihren Scan-Anteil nicht
        wegnehmen (vorher: China-Alarme bekamen 0 Google-Abfragen)."""
        src = source(max_requests=40, verify_reserve=10)
        first = alert(id="zrh-ham", months_ahead=6)                        # 12 Abfragen
        china = alert(id="china", origins=["@DE"], destinations=["@CN_MAJOR"],
                      months_ahead=6, max_price=500)
        business = alert(id="biz", origins=["@DE_HUBS"], destinations=["@CN_MAJOR"],
                         months_ahead=6, cabin="business", max_price=2000)
        src.begin_run([first, china, business])
        pending = sum(src._shares.values())
        self.assertEqual(pending, 30)
        # erster Alarm: viele Cache-Deals wollen gegengeprüft werden, bevor gescannt wird
        done = [src.verify("ZRH", "HAM", date(2027, m, 3), date(2027, m, 9))
                for m in range(1, 13) for _ in range(2)]
        self.assertEqual(src.requests_made, 10, "nur die Reserve darf vorab gegengeprüft werden")
        self.assertIn("reserviert", src.last_error)
        self.assertTrue(any(done))
        for a in (first, china, business):
            src.fetch(a)
            self.assertNotRegex((src.incomplete or {}).get("message", ""), r"(?<!\d)0 von", a.id)
        self.assertEqual(src.scans_made, 30)
        self.assertEqual(src.requests_made, 40)

    def test_unused_share_frees_verifications(self):
        src = source(max_requests=10, verify_reserve=2)
        a = alert(id="a", months_ahead=1)                                  # 2 Abfragen
        src.begin_run([a])
        self.assertEqual(src._scan_pending, 2)
        src.fetch(a)
        self.assertEqual(src._scan_pending, 0)
        self.assertIsNotNone(src.verify("ZRH", "HAM", date(2027, 3, 3), date(2027, 3, 9)))


class TfsUrlTest(unittest.TestCase):
    def test_fixed_value(self):
        """Deckt sich mit Googles eigenen Such-URLs ("CBwQAh…"), live geprüft 05.11.2026."""
        self.assertEqual(
            build_tfs_url("FRA", "SHA", date(2027, 3, 12), date(2027, 3, 24)),
            "https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI3LTAzLTEyagcIARIDRlJB"
            "cgcIARIDU0hBGh4SCjIwMjctMDMtMjRqBwgBEgNTSEFyBwgBEgNGUkFAAUgBmAEB&hl=de&curr=EUR")
        # Business (Feld 9 = 3) und one-way (Feld 19 = 2), Werte aus den Messabrufen
        self.assertIn("tfs=CBwQAhoeEgoyMDI3LTAxLTE1agcIARIDRlJBcgcIARIDUEVLGh4SCjIwMjctMDEtMjdq"
                      "BwgBEgNQRUtyBwgBEgNGUkFAAUgDmAEB&",
                      build_tfs_url("fra", "pek", date(2027, 1, 15), date(2027, 1, 27), "business"))
        self.assertIn("tfs=CBwQAhoeEgoyMDI2LTEyLTExagcIARIDSEFKcgcIARIDSEdIQAFIAZgBAg&",
                      build_tfs_url("HAJ", "HGH", date(2026, 12, 11), None, "economy", False))
        self.assertEqual(build_tfs_url("HAJ", "HGH", date(2026, 12, 11), date(2026, 12, 20),
                                       roundtrip=False),
                         build_tfs_url("HAJ", "HGH", date(2026, 12, 11), None, roundtrip=False))

    def test_search_urls_order(self):
        dep, ret = date(2027, 1, 15), date(2027, 1, 27)
        q, tfs = build_url("FRA", "PEK", dep, ret), build_tfs_url("FRA", "PEK", dep, ret)
        self.assertEqual(search_urls("FRA", "PEK", dep, ret), (q, tfs), "Economy: erst Suchtext")
        biz = search_urls("FRA", "PEK", dep, ret, "business")
        self.assertEqual(biz, (build_tfs_url("FRA", "PEK", dep, ret, "business"),
                               build_url("FRA", "PEK", dep, ret, "business")),
                         "Business: erst tfs-Link")


class Ds1ResultsTest(unittest.TestCase):
    def test_rows_from_ds1_only_page(self):
        self.assertNotIn('aria-label="Ab ', DS1_ONLY)
        rows = parse_results(DS1_ONLY)
        self.assertEqual(len(rows), 11)
        self.assertEqual({k: rows[0][k] for k in ("price", "airline", "airline_code", "stops",
                                                   "duration_min", "departure_time",
                                                   "departure_airport")},
                         {"price": 3210.0, "airline": "Air Astana", "airline_code": "KC",
                          "stops": 1, "duration_min": 2770, "departure_time": "17:05",
                          "departure_airport": "Frankfurt"})
        self.assertEqual(rows, sorted(rows, key=lambda r: r["price"]))
        self.assertIsNone(empty_kind(DS1_ONLY, rows))
        src = source(page=DS1_ONLY)
        got = src.query("FRA", "PEK", date(2027, 1, 15), date(2027, 1, 27), "business")
        self.assertEqual((got[0]["price"], src.requests_made), (3210.0, 1))
        self.assertEqual(src.insight(build_url("FRA", "PEK", date(2027, 1, 15), date(2027, 1, 27),
                                               "business"))["lowest"], 3210)

    def test_ds1_matches_html_rows(self):
        """Wo beides da ist, liefert ds:1 dieselben Zeilen wie die aria-labels."""
        for page in (FRA_PEK, (FIX / "google_flights_zrh_ham_oneway_ds1.html").read_text("utf-8")):
            key = lambda r: (r["price"], r["airline"], r["stops"], r["duration_min"],
                             r["departure_time"])
            html = {key(r) for r in parse_results(page)}
            self.assertTrue(html and html <= {key(r) for r in gf.ds1_results(page)})
        self.assertEqual(gf._names(["Lufthansa", "SWISS", "Air China"]),
                         "Lufthansa, SWISS und Air China")

    def test_ds1_garbage_is_ignored(self):
        for data in ("[13,null,[]]", "[[1],[],null,null]", "[[1],[],[[[\"x\"]]],[[[[1]]]]]",
                     "[[1],[],[[[[\"LH\",[\"Lufthansa\"],[]],[[null,\"x\"]]]]],null]"):
            page = f"<script>AF_initDataCallback({{key: 'ds:1', hash: '1', data:{data}, sideChannel: {{}}}});</script>"
            self.assertEqual(gf.ds1_results(page), [], data)
        self.assertEqual(gf.ds1_results(NO_ITIN), [])


class EmptyKindTest(unittest.TestCase):
    def test_real_pages(self):
        self.assertEqual(empty_kind(ERROR13, []), "error")
        self.assertEqual(empty_kind(UNPARSED, []), "unparsed")
        self.assertEqual(empty_kind(NO_ITIN, []), "no_itineraries")
        # Suche verstanden (Strecke in ds:0), nur der Titel fehlt -> trotzdem unparsed über ds:0
        self.assertEqual(empty_kind(UNPARSED.replace("Günstige Flüge weltweit finden", "X"), []),
                         "unparsed")
        self.assertEqual(empty_kind(UNPARSED.split('<script class="ds:0">')[0], []), "unparsed",
                         "Titel der Startseite reicht")

    def test_result_pages_are_none(self):
        for name in ("google_flights_zrh_ham.html", "google_flights_fra_pek_ds1.html",
                     "google_flights_zrh_ham_oneway_ds1.html", "google_flights_fra_sha_oneway.html",
                     "google_flights_fra_bjs_business.html"):
            page = (FIX / name).read_text(encoding="utf-8")
            self.assertIsNone(empty_kind(page, parse_results(page)), name)
        self.assertIsNone(empty_kind(NO_FLIGHTS, []))

    def test_short_stay(self):
        self.assertEqual(short_stay(12, 5, 30), 7)
        self.assertEqual(short_stay(17, 10, 30), 10, "min_nights größer als 7")
        self.assertIsNone(short_stay(6, 1, 6), "7 Nächte über max_nights")
        self.assertIsNone(short_stay(7, 1, 21), "schon 7 Nächte")
        self.assertIsNone(short_stay(12, 12, 30), "min_nights = aktueller Aufenthalt")
        self.assertEqual(short_stay(12, None, None), 7)


def by_nights(pages: dict, default: str):
    """Fetcher-Ersatz: Seite je Aufenthalt (aus dem Suchtext "on … through …")."""
    def page(url):
        q = unquote(url.split("q=", 1)[-1])
        m = re.search(r"on (\S+) through (\S+)", q)
        if not m:
            return default
        nights = (date.fromisoformat(m.group(2)) - date.fromisoformat(m.group(1))).days
        return pages.get(nights, default)
    return page


def by_url(**pages):
    """Fetcher-Ersatz: erste Seite, deren Schlüssel in der URL steht (``default`` sonst)."""
    def page(url):
        for key, html in pages.items():
            if key != "default" and key in url:
                return html
        return pages["default"]
    return page


class SecondFetchTest(unittest.TestCase):
    """Je Fall höchstens ein gezielter zweiter Abruf statt blind dieselbe URL."""

    DEP = date(2027, 1, 15)

    def china(self, **kw):
        base = dict(id="cn", origins=["FRA"], destinations=["PEK"], max_price=900,
                    baseline_price=1000, min_nights=5, max_nights=30, max_stops=None,
                    max_duration_hours=None)
        base.update(kw)
        return alert(**base)

    def test_unparsed_text_then_tfs(self):
        src = source(page=by_url(**{"tfs=": ZRH_HAM, "default": UNPARSED}))
        dep, ret = date(2027, 1, 15), date(2027, 1, 22)
        rows = src.query("ZRH", "HAM", dep, ret)
        key, tfs = build_url("ZRH", "HAM", dep, ret), build_tfs_url("ZRH", "HAM", dep, ret)
        self.assertEqual(src.calls, [key, tfs])
        self.assertEqual((rows[0]["price"], src.requests_made, src.retries), (168, 2, 1))
        self.assertEqual((src.empty_fetches, src.no_results, src.rescued), (0, 0, 1))
        self.assertIn(key, src._cache, "Ergebnisse unter der ursprünglichen URL")
        self.assertEqual(src.loaded(key), (tfs, ret))
        self.assertEqual(src.notes, ["ZRH–HAM 15.01.–22.01.2027: Google verstand den Suchtext "
                                     "nicht, per tfs-Link geprüft – 11 Flüge ab 168 €"])
        # im Scan: Angebote mit dem tatsächlich geladenen Link
        src = source(page=by_url(**{"tfs=": ZRH_HAM, "default": UNPARSED}))
        offers = src.fetch(alert(min_nights=7, max_nights=7))
        self.assertTrue(offers)
        self.assertTrue(all(o.link.startswith(gf.TFS_BASE) for o in offers))
        self.assertEqual(src.requests_made, 4)
        self.assertIsNone(src.incomplete)

    def test_business_asks_tfs_first_then_text(self):
        a = alert(origins=["FRA"], destinations=["BJS"], cabin="business", max_price=3000,
                  baseline_price=4000, months_ahead=1)
        src = source(page=FRA_BJS_BIZ)
        offers = src.fetch(a)
        self.assertTrue(all(u.startswith(gf.TFS_BASE) for u in src.calls), src.calls)
        self.assertEqual(src.requests_made, 2, "ein Abruf je Termin")
        self.assertTrue(all(o.link.startswith(gf.TFS_BASE) for o in offers))
        # tfs-Link nicht verstanden -> Suchtext
        src = source(page=by_url(**{"tfs=": UNPARSED, "default": FRA_BJS_BIZ}))
        rows = src.query("FRA", "BJS", self.DEP, date(2027, 1, 27), "business")
        self.assertEqual((len(src.calls), rows[0]["price"]), (2, 2690))
        self.assertFalse(src.calls[1].startswith(gf.TFS_BASE))
        self.assertIn("verstand den tfs-Link nicht, per Suchtext geprüft", src.notes[0])

    def test_unparsed_twice_is_empty_fetch(self):
        src = source(page=UNPARSED)
        with self.assertRaises(EmptyPage) as cm:
            src.query("FRA", "SHA", self.DEP, date(2027, 1, 27), "business")
        self.assertIn("nicht verstanden (weder als Suchtext noch als tfs-Link)", str(cm.exception))
        self.assertEqual((src.requests_made, src.empty_fetches, src.no_results), (2, 1, 0))
        self.assertNotEqual(src.calls[0], src.calls[1])

    def test_unparsed_cabin_safety_net_stays(self):
        # tfs-Link liefert Economy statt Business -> verwerfen wie bisher
        src = source(page=by_url(**{"tfs=": UNPARSED, "default": ZRH_HAM}))
        with self.assertRaises(ParseError) as cm:
            src.query("ZRH", "HAM", self.DEP, date(2027, 1, 22), "business")
        self.assertIn("economy statt business", str(cm.exception))

    def test_no_itineraries_then_seven_nights(self):
        src = source(page=by_nights({7: FRA_PEK}, NO_ITIN))
        a = self.china()
        plan = src.plan(a)
        self.assertEqual({(r - d).days for _, _, d, r in plan}, {12}, "12 statt 17 Nächte")
        offers = src.fetch(a)
        self.assertEqual(len(src.calls), 4, "2 Termine x (12 Nächte + 7 Nächte)")
        self.assertTrue(offers)
        self.assertEqual({o.nights for o in offers}, {7}, "echter Rückflug-Termin")
        dep = offers[0].departure
        short = build_url("FRA", "PEK", dep, dep + gf.timedelta(days=7))
        self.assertTrue(all(o.link == short for o in offers if o.departure == dep))
        self.assertEqual((src.rescued, src.no_results, src.empty_fetches), (2, 0, 0))
        key = build_url("FRA", "PEK", dep, dep + gf.timedelta(days=12))
        self.assertEqual(src.insight(key)["lowest"], 720, "Preisniveau der geladenen Seite")
        self.assertIsNone(src.incomplete)
        self.assertRegex(src.notes[0], r"^FRA–PEK \d\d\.11\.–\d\d\.\d\d\.2026: Google ohne "
                                       r"Hin/Rück-Flüge für 12 Nächte, mit 7 Nächten geprüft – "
                                       r"8 Flüge ab 720 €$")

    def test_no_itineraries_twice_is_no_result(self):
        src = source(page=NO_ITIN)
        offers = src.fetch(self.china())
        self.assertEqual((offers, src.requests_made), ([], 4))
        self.assertEqual((src.no_results, src.empty_fetches, src.rescued), (2, 0, 0))
        self.assertIsNone(src.incomplete, "keine Flüge ist kein Fehler")
        self.assertIn("Google ohne Hin/Rück-Flüge für 12 Nächte, auch mit 7 Nächten keine",
                      src.notes[0])
        self.assertFalse(src.down)

    def test_no_itineraries_then_error(self):
        # live 05.11.2026: HAJ–PEK 12 Nächte ohne Verbindungen, mit 7 Nächten ErrorResponse
        src = source(page=by_nights({7: ERROR13}, NO_ITIN))
        self.assertEqual(src.query("HAJ", "PEK", self.DEP, date(2027, 1, 27), nights=(5, 30)), [])
        self.assertEqual((src.requests_made, src.no_results, src.empty_fetches), (2, 1, 0))
        self.assertEqual(src.notes, ["HAJ–PEK 15.01.–27.01.2027: Google ohne Hin/Rück-Flüge für "
                                     "12 Nächte, mit 7 Nächten meldete Google einen Fehler"])

    def test_no_itineraries_without_short_stay(self):
        # max_nights 6: 7 Nächte liegen nicht in der Spanne -> kein zweiter Abruf
        src = source(page=NO_ITIN)
        src.fetch(self.china(min_nights=3, max_nights=6))
        self.assertEqual((src.requests_made, src.no_results, src.retries), (2, 2, 0))
        self.assertTrue(src.notes[0].endswith(": Google ohne Hin/Rück-Flüge für 6 Nächte"),
                        src.notes)
        # Gegenprüfung genauer Daten: nie mit anderem Rückflug
        src = source(page=by_url(**{"2027-01-22": FRA_PEK, "default": NO_ITIN}))
        self.assertIsNone(src.verify("FRA", "PEK", self.DEP, date(2027, 1, 27)))
        self.assertIn("keine Flüge", src.last_error)
        self.assertEqual(src.requests_made, 1)

    def test_no_itineraries_oneway(self):
        src = source(page=NO_ITIN.replace("Hin- und Rückreise", "Nur Hinreise"))
        offers = src.fetch(self.china(trip="oneway"))
        self.assertEqual((offers, src.requests_made, src.retries), ([], 2, 0))
        self.assertEqual((src.no_results, src.empty_fetches), (2, 0))
        self.assertIsNone(src.incomplete)
        self.assertEqual(src.notes, [], "one-way ohne Flüge: normal, kein Hinweis")

    def test_error_retries_same_url(self):
        src = source(page=lambda url: ERROR13 if len(src.calls) == 1 else ZRH_HAM)
        rows = src.query("ZRH", "HAM", self.DEP, date(2027, 1, 22))
        self.assertEqual(src.calls[0], src.calls[1])
        self.assertEqual((len(rows), src.retries, src.rescued, src.empty_fetches), (11, 1, 0, 0))
        src = source(page=ERROR13)
        with self.assertRaises(EmptyPage) as cm:
            src.query("ZRH", "HAM", self.DEP, date(2027, 1, 22))
        self.assertIn("Fehler (ErrorResponse) auch beim zweiten Abruf", str(cm.exception))
        self.assertEqual(src.empty_fetches, 1)

    def test_retry_false_and_budget(self):
        for page in (UNPARSED, ERROR13):
            src = source(page=page)
            with self.assertRaises(EmptyPage):
                src.query("FRA", "PEK", self.DEP, date(2027, 1, 27), retry=False)
            self.assertEqual((src.requests_made, src.retries, src.empty_fetches), (1, 0, 1))
        src = source(page=NO_ITIN)
        self.assertEqual(src.query("FRA", "PEK", self.DEP, date(2027, 1, 27), retry=False,
                                   nights=(5, 30)), [])
        self.assertEqual((src.requests_made, src.no_results), (1, 1))
        src = source(page=NO_ITIN, max_requests=1)
        src.query("FRA", "PEK", self.DEP, date(2027, 1, 27), nights=(5, 30))
        self.assertEqual(src.requests_made, 1, "Budget erschöpft -> kein zweiter Abruf")

    def test_second_fetch_only_from_own_share(self):
        src = source(page=by_nights({7: FRA_PEK}, NO_ITIN), max_requests=6)
        a, b = self.china(id="a"), self.china(id="b", origins=["MUC"])
        src.begin_run([a, b])
        self.assertEqual(src._shares, {"a": 2, "b": 2})
        offers = src.fetch(a)
        self.assertEqual((src.requests_made, src.scans_made), (2, 2), "Anteil von a verbraucht")
        self.assertEqual({o.nights for o in offers}, {7})
        self.assertIn("1 von 2 Abfragen (Limit)", src.incomplete["message"])
        self.assertEqual(src._scan_pending, 2, "Anteil von b bleibt reserviert")
        # letzte Abfrage des Anteils: kein zweiter Abruf in fremde Anteile
        src.fetch(b)
        self.assertEqual(src.requests_made, 4)
        src = source(page=by_url(**{"2026-11-13": FRA_PEK, "default": NO_ITIN}), max_requests=2)
        src.begin_run([a])
        src.fetch(a)
        self.assertEqual((src.requests_made, src.retries, src.no_results), (2, 0, 1))
        self.assertIsNone(src.incomplete)

    def test_reset_clears_new_counters(self):
        src = source(page=NO_ITIN)
        src.fetch(self.china())
        src.reset()
        self.assertEqual((src.no_results, src.rescued, src.notes, src._loaded), (0, 0, [], {}))


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
