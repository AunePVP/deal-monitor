"""Paket 10: Travelpayouts (Kalender, latest mit Ländercodes, Token im Header) und Amadeus-Status.

Ohne Token ist nichts live prüfbar - die Fixtures folgen der Struktur aus der Doku.
"""

import json
import logging
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from dealmonitor import http
from dealmonitor.appsettings import SOURCE_LABELS
from dealmonitor.models import Alert
from dealmonitor.sources import SourceError, amadeus
from dealmonitor.sources import travelpayouts as tp
from dealmonitor.sources.travelpayouts import TravelpayoutsSource, country_code

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 5)
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
MONTHS = tp._months(TODAY, 6)


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def fixture(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def alert(origins=("ZRH",), dests=("HAM",), **kw):
    kw.setdefault("max_price", 110)
    return Alert(id="a", name="A", origins=list(origins), destinations=list(dests), **kw)


def business():
    return alert(["@DE_HUBS"], ["@CN_MAJOR"], cabin="business", max_price=1999)


def source(**kw):
    return TravelpayoutsSource(token="test-dummy", request_delay=0, today=TODAY, **kw)


class Recorder:
    """Ersatz für ``http.get_json``: merkt sich Pfad, Parameter und Header."""

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer or (lambda path, params: {"success": True, "data": []})

    def __call__(self, url, params=None, headers=None, **kw):
        path = url[len(tp.BASE):]
        self.calls.append((path, dict(params or {}), dict(headers or {})))
        result = self.answer(path, params)
        if isinstance(result, Exception):
            raise result
        return result

    def paths(self):
        return [c[0] for c in self.calls]


def fetch(src, a, recorder):
    with mock.patch.object(http, "get_json", side_effect=recorder):
        return src.fetch(a)


class ParamsTest(unittest.TestCase):
    def test_calendar_params(self):
        src = source()
        ow = src.calendar_params(alert(trip="oneway"), "ZRH", "HAM", "2026-12")
        self.assertEqual(ow, {"origin": "ZRH", "destination": "HAM", "depart_date": "2026-12",
                              "calendar_type": "departure_date", "currency": "eur"})
        rt = src.calendar_params(alert(), "ZRH", "HAM", "2026-12")
        self.assertEqual(rt["return_date"], "2026-12", "ohne return_date wären es one-way-Preise")

    def test_latest_params(self):
        p = source().latest_params(business(), "DE", "CN")
        self.assertEqual((p["origin"], p["destination"], p["period_type"], p["limit"],
                          p["show_to_affiliates"], p["trip_class"], p["one_way"]),
                         ("DE", "CN", "year", 1000, "false", 1, "false"))
        self.assertNotIn("beginning_of_period", p)

    def test_token_only_in_header(self):
        rec = Recorder()
        fetch(source(), alert(trip="oneway", months_ahead=1), rec)
        self.assertTrue(rec.calls)
        for _, params, headers in rec.calls:
            self.assertNotIn("token", params)
            self.assertEqual(headers["X-Access-Token"], "test-dummy")

    def test_country_code(self):
        self.assertEqual(country_code(business().origins), "DE")
        self.assertEqual(country_code(business().destinations), "CN")
        self.assertIsNone(country_code(["FRA"]), "ein einzelner Code bleibt ein Code")
        self.assertIsNone(country_code(["ZRH", "HAM"]), "zwei Länder")
        self.assertIsNone(country_code(["BJS", "HKG"]), "Hongkong ist ein eigenes Land")


class CalendarTest(unittest.TestCase):
    def setUp(self):
        self.data = fixture("travelpayouts_calendar_zrh_ham.json")

    def test_oneway(self):
        offers = source(marker="12345").parse_calendar(self.data, alert(trip="oneway"),
                                                       "ZRH", "HAM", now=NOW)
        self.assertEqual([(o.departure, o.price, o.stops, o.airline) for o in offers],
                         [(date(2027, 1, 14), 39, 0, "EW"), (date(2027, 1, 15), 52, 1, "LX"),
                          (date(2027, 1, 17), 44, 0, "EW")])   # 16.: abgelaufen, 18./19.: kaputt
        o = offers[0]
        self.assertEqual((o.origin, o.destination, o.roundtrip, o.return_date, o.cabin),
                         ("ZRH", "HAM", False, None, "economy"))
        self.assertEqual(o.link, "https://www.aviasales.com/search/ZRH1401HAM1?marker=12345")
        self.assertEqual(o.expires, datetime(2026, 11, 7, 10, 12, tzinfo=timezone.utc))

    def test_roundtrip_needs_return(self):
        offers = source().parse_calendar(self.data, alert(), "ZRH", "HAM", now=NOW)
        self.assertEqual([(o.departure, o.return_date, o.price) for o in offers],
                         [(date(2027, 1, 14), date(2027, 1, 18), 39),
                          (date(2027, 1, 15), date(2027, 1, 19), 52)])
        self.assertEqual(offers[0].link, "https://www.aviasales.com/search/ZRH1401HAM18011")

    def test_list_form_and_garbage(self):
        src = source()
        rows = {"data": [{"price": 40, "departure_at": "2027-02-01T08:00:00Z"}]}
        self.assertEqual([o.price for o in src.parse_calendar(rows, alert(trip="oneway"),
                                                              "ZRH", "HAM", now=NOW)], [40])
        for bad in (None, "x", [], {}, {"data": None}, {"data": "x"}, {"data": {"x": 1}}):
            self.assertEqual(src.parse_calendar(bad, alert(), "ZRH", "HAM", now=NOW), [])

    def test_home_route_fetches_calendar(self):
        rec = Recorder(lambda path, p: self.data if path == tp.CALENDAR_PATH
                       else {"success": True, "data": []})
        src = source()
        with mock.patch.object(tp, "datetime", wraps=datetime) as dt:
            dt.now.return_value = NOW
            offers = fetch(src, alert(trip="oneway", months_ahead=2), rec)
        self.assertEqual(rec.paths(), [tp.V3_PATH, tp.CALENDAR_PATH] * 2)
        self.assertEqual([c[1]["depart_date"] for c in rec.calls[1::2]], ["2026-11", "2026-12"])
        self.assertEqual(len(offers), 6)
        self.assertIsNone(src.incomplete)

    def test_no_calendar_for_long_haul_or_when_off(self):
        rec = Recorder()
        fetch(source(), alert(["FRA"], ["BJS"], months_ahead=2, max_price=499), rec)
        self.assertEqual(rec.paths(), [tp.V3_PATH] * 2)
        rec = Recorder()
        fetch(source(calendar=False), alert(months_ahead=2), rec)
        self.assertEqual(rec.paths(), [tp.V3_PATH] * 2)

    def test_calendar_failure_marks_route(self):
        def answer(path, p):
            if path == tp.CALENDAR_PATH and p["origin"] == "HAM":
                return http.HttpError("HTTP 400", 400)
            return {"success": True, "data": []}
        src = source()
        fetch(src, alert(["ZRH", "HAM"], ["HAM", "ZRH"], trip="oneway", months_ahead=1),
              Recorder(answer))
        self.assertEqual(src.incomplete, {"message": "1 von 2 Anfragen fehlgeschlagen",
                                          "routes": {("HAM", "ZRH")}})


class LatestTest(unittest.TestCase):
    def setUp(self):
        self.data = fixture("travelpayouts_latest_de_cn_business.json")

    def test_country_query_once(self):
        rec = Recorder(lambda path, p: self.data)
        src = source()
        offers = fetch(src, business(), rec)
        self.assertEqual(len(rec.calls), 1, "ein Länderpaar statt 30 Strecken × 6 Monate")
        path, params, _ = rec.calls[0]
        self.assertEqual((path, params["origin"], params["destination"]), (tp.LATEST_PATH, "DE", "CN"))
        # nicht: Economy-Zeile, LEJ (nicht im Alarm), actual=false, außerhalb des Zeitraums,
        # ohne Rückflug, Preis 0, kaputte Zeile
        self.assertEqual([(o.origin, o.destination, o.price, o.cabin, o.stops) for o in offers],
                         [("FRA", "BJS", 1789, "business", 1), ("MUC", "SHA", 1950, "business", 0)])
        self.assertEqual((offers[0].departure, offers[0].return_date),
                         (date(2027, 3, 10), date(2027, 3, 24)))
        self.assertIsNone(src.incomplete)

    def test_country_rejected_falls_back_to_pairs(self):
        def answer(path, p):
            if len(p["origin"]) == 2:
                return http.HttpError("HTTP 400 für …", 400)
            return {"success": True, "data": [r for r in self.data["data"] if isinstance(r, dict)
                                              and (r["origin"], r["destination"])
                                              == (p["origin"], p["destination"])]}
        rec = Recorder(answer)
        offers = fetch(source(), business(), rec)
        self.assertEqual(len(rec.calls), 1 + len(business().pairs))
        self.assertEqual(sorted(o.price for o in offers), [1789, 1950])

    def test_country_failure_marks_all_routes(self):
        src = source()
        with self.assertRaises(SourceError) as cm:
            fetch(src, business(), Recorder(lambda path, p: http.HttpError("HTTP 502", 502)))
        self.assertIn("Alle 1 Travelpayouts-Anfragen", str(cm.exception))

    def test_query_shapes(self):
        rec = Recorder()
        fetch(source(), alert(["ZRH"], ["@CN_MAJOR"], cabin="business", max_price=1999), rec)
        self.assertEqual([(c[1]["origin"], c[1]["destination"]) for c in rec.calls], [("ZRH", "CN")])
        rec = Recorder()
        fetch(source(), alert(["FRA"], ["BJS"], cabin="first", max_price=4000), rec)
        self.assertEqual([(c[1]["origin"], c[1]["destination"], c[1]["trip_class"])
                          for c in rec.calls], [("FRA", "BJS", 2)])
        rec = Recorder()
        fetch(source(), alert(["ZRH", "FRA"], ["BJS", "HKG"], cabin="business", max_price=1999), rec)
        self.assertEqual(len(rec.calls), 4, "gemischte Länder: je Strecke")

    def test_garbage(self):
        src = source()
        for bad in (None, "x", {}, {"data": None}, {"data": {"a": 1}}, {"data": [None, 1, "x"]}):
            self.assertEqual(src.parse_latest(bad, business(), MONTHS), [])
        row = {"trip_class": "x", "value": "teuer", "depart_date": 5}
        self.assertEqual(src.parse_latest({"data": [row]}, business(), MONTHS), [])


class ErrorTest(unittest.TestCase):
    def run_with(self, answer, a=None):
        rec = Recorder(answer)
        src = source()
        with self.assertRaises(SourceError) as cm:
            fetch(src, a or alert(months_ahead=3), rec)
        return str(cm.exception), rec, src

    def test_401_means_token_invalid(self):
        msg, rec, src = self.run_with(lambda path, p: http.HttpError("HTTP 401: Unauthorized", 401))
        self.assertIn("Token ungültig", msg)
        self.assertEqual(len(rec.calls), 1)
        with mock.patch.object(http, "get_json", side_effect=AssertionError("kein Abruf mehr")):
            with self.assertRaises(SourceError) as cm:
                src.fetch(business())
        self.assertIn("Token ungültig", str(cm.exception))

    def test_403_and_error_body(self):
        msg, _, _ = self.run_with(lambda path, p: http.HttpError("HTTP 403", 403))
        self.assertIn("gesperrt", msg)
        msg, _, _ = self.run_with(lambda path, p: {"success": False, "data": None,
                                                    "error": "Unauthorized"})
        self.assertIn("Token ungültig", msg)

    def test_wrong_currency_and_odd_answers_never_crash(self):
        msg, _, _ = self.run_with(lambda path, p: {"success": True, "currency": "rub", "data": []})
        self.assertIn("Alle 3", msg)
        a = alert(["FRA"], ["BJS"], months_ahead=1, max_price=499)
        for odd in ([], "x", None):     # keine JSON-Objekte: kontrollierter Quellenfehler
            with self.assertRaises(SourceError):
                fetch(source(), a, Recorder(lambda path, p, odd=odd: odd))
        for odd in ({"success": True, "data": "x"}, {"success": True}, {"data": [None, "x"]}):
            self.assertEqual(fetch(source(), a, Recorder(lambda path, p, odd=odd: odd)), [])


class AmadeusStatusTest(unittest.TestCase):
    def test_marked_as_shut_down(self):
        self.assertEqual(amadeus.STATUS, "abgeschaltet seit 17.07.2026")
        self.assertIn(amadeus.STATUS, SOURCE_LABELS["amadeus"])
        self.assertIn(amadeus.STATUS, amadeus.MISSING_KEY)

    def test_unreachable_names_shutdown(self):
        client = amadeus.AmadeusClient("test-dummy-key", "test-dummy-secret", request_delay=0)
        err = http.HttpError("Anfrage fehlgeschlagen (Tunnel connection failed: 502)", network=True)
        with mock.patch.object(http, "request", side_effect=err):
            with self.assertRaises(SourceError) as cm:
                client.token()
        self.assertIn("nicht erreichbar", str(cm.exception))
        self.assertIn(amadeus.STATUS, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
