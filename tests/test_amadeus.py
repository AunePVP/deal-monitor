"""Tests für die Amadeus-Quellen (Token, Parameter, Parser, Fehler, Budget) - ohne Netz.

Die Fixtures sind nach der offiziellen Doku gebaut (OpenAPI-Spezifikationen
``FlightCheapestDateSearch_v1`` / ``FlightOffersSearch_v2`` und die Developer-Guides von
amadeus4dev auf GitHub); ein Live-Test war ohne Key nicht möglich. ``dealmonitor.http.request``
wird durch eine Attrappe ersetzt, die je Pfad mit Fixture-JSON antwortet.
"""

import copy
import json
import os
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from dealmonitor import config as config_mod
from dealmonitor import http
from dealmonitor.models import Alert
from dealmonitor.monitor import run_alert
from dealmonitor.sources import SourceError
from dealmonitor.sources.amadeus import (
    DATES_PATH, OFFERS_PATH, TOKEN_PATH, AmadeusClient, AmadeusDatesSource, AmadeusOffersSource,
    build_sources, error_detail, month_range, parse_duration,
)
from dealmonitor.store import Store

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 4)
TOKEN = json.loads((FIX / "amadeus_token.json").read_text(encoding="utf-8"))
DATES = json.loads((FIX / "amadeus_flight_dates_fra_pek.json").read_text(encoding="utf-8"))
OFFERS = json.loads((FIX / "amadeus_flight_offers_fra_pek_business.json").read_text(encoding="utf-8"))
ERRORS = json.loads((FIX / "amadeus_errors.json").read_text(encoding="utf-8"))
ROUTE = ("FRA", "PEK")


def http_error(name_or_status, body=None):
    """``http.HttpError`` genau so, wie ``dealmonitor.http.request`` ihn für diese Antwort wirft:
    bei 4xx (außer 429) mit den ersten 300 Zeichen des Bodys in der Meldung, sonst ohne."""
    if isinstance(name_or_status, str):
        spec = ERRORS[name_or_status]
        status, body = spec["status"], spec["body"]
    else:
        status = name_or_status
    url = "https://test.api.amadeus.com/x"
    if status is None:
        return http.HttpError(f"Anfrage fehlgeschlagen: {url} (timed out)", None, network=True)
    if 400 <= status < 500 and status != 429:
        text = json.dumps(body)[:300] if body is not None else ""
        return http.HttpError(f"HTTP {status} für {url}: {text}", status)
    return http.HttpError(f"Anfrage fehlgeschlagen: {url} (HTTP Error {status})", status)


class Call:
    def __init__(self, url, params, headers, data, method):
        self.url, self.params, self.headers, self.data, self.method = url, params, headers, data, method
        self.path = urlsplit(url).path


class FakeApi:
    """Attrappe für ``dealmonitor.http.request``: antwortet je Pfad mit Fixture-JSON.

    ``queue[path]`` kann Antworten (dict) oder Ausnahmen vorgeben, die der Reihe nach
    verbraucht werden; danach gilt wieder die Standardantwort aus ``responses``.
    """

    def __init__(self, token=TOKEN, dates=DATES, offers=OFFERS):
        self.responses = {TOKEN_PATH: token, DATES_PATH: dates, OFFERS_PATH: offers}
        self.queue = {}
        self.calls = []

    def __call__(self, url, *, params=None, data=None, headers=None, method=None, timeout=20,
                 retries=2, full=False):
        call = Call(url, params, headers or {}, data, method)
        self.calls.append(call)
        pending = self.queue.get(call.path)
        if pending:
            item = pending.pop(0)
            if isinstance(item, Exception):
                raise item
            return json.dumps(item).encode()
        if call.path not in self.responses:
            raise AssertionError(f"unerwartete Anfrage: {url}")
        return json.dumps(self.responses[call.path]).encode()

    def paths(self):
        return [c.path for c in self.calls]

    def of(self, path):
        return [c for c in self.calls if c.path == path]


def alert(**kw):
    base = dict(id="t", name="Test", origins=["FRA"], destinations=["PEK"], max_price=500,
                baseline_price=750, max_stops=1, min_nights=1, max_nights=21, months_ahead=1)
    base.update(kw)
    return Alert(**base)


def business(**kw):
    base = dict(cabin="business", max_price=1500, baseline_price=2500)
    base.update(kw)
    return alert(**base)


def client(**kw):
    opts = dict(api_key="KEY", api_secret="SECRET", request_delay=0, max_requests=100)
    opts.update(kw)
    return AmadeusClient(**opts)


class ApiCase(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        patcher = mock.patch("dealmonitor.http.request", self.api)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = client()
        self.dates = AmadeusDatesSource(self.client, today=TODAY)
        self.offers = AmadeusOffersSource(self.client, today=TODAY)


# -- Helfer -------------------------------------------------------------------------------
class HelperTest(unittest.TestCase):
    def test_parse_duration(self):
        self.assertEqual(parse_duration("PT12H30M"), 750)
        self.assertEqual(parse_duration("PT9H"), 540)
        self.assertEqual(parse_duration("PT45M"), 45)
        self.assertEqual(parse_duration("P1DT2H"), 1560)
        self.assertEqual(parse_duration("PT15H5M"), 905)
        self.assertIsNone(parse_duration(None))
        self.assertIsNone(parse_duration(""))
        self.assertIsNone(parse_duration("12:30"))
        self.assertIsNone(parse_duration("PT"))

    def test_month_range(self):
        self.assertEqual(month_range(2026, 11, TODAY), (date(2026, 11, 5), date(2026, 11, 30)))
        self.assertEqual(month_range(2027, 2, TODAY), (date(2027, 2, 1), date(2027, 2, 28)))
        self.assertEqual(month_range(2027, 12, TODAY), (date(2027, 12, 1), date(2027, 12, 31)))
        self.assertIsNone(month_range(2026, 10, TODAY))
        self.assertIsNone(month_range(2026, 11, date(2026, 11, 30)), "nur noch heute übrig")

    def test_error_detail(self):
        self.assertEqual(error_detail(http_error("invalid_date")), "Date/Time is in the past")
        self.assertEqual(error_detail(http_error("token_invalid_client")),
                         "Client credentials are invalid")
        # Body nach 300 Zeichen abgeschnitten -> trotzdem der Anfang des Details
        cut = http.HttpError('HTTP 400 für x: {"errors":[{"status":400,"code":477,'
                             '"title":"INVALID FORMAT","detail":"invalid query par', 400)
        self.assertEqual(error_detail(cut), "invalid query par")
        # nur Titel vorhanden (500er ohne detail)
        only_title = http.HttpError('HTTP 400 für x: {"errors":[{"status":400,"code":141,'
                                    '"title":"SYSTEM ERROR HAS OCCURRED"}]}', 400)
        self.assertEqual(error_detail(only_title), "SYSTEM ERROR HAS OCCURRED")
        self.assertEqual(error_detail(http_error("too_many_requests")), "", "429 ohne Body")
        self.assertEqual(error_detail(http_error(None)), "")

    def test_client_env_and_keys(self):
        self.assertEqual(client().base, "https://test.api.amadeus.com")
        self.assertEqual(client(env="production").base, "https://api.amadeus.com")
        self.assertEqual(client(env=" Production ").base, "https://api.amadeus.com")
        with self.assertRaises(ValueError):
            client(env="staging")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(AmadeusClient().configured)
            self.assertFalse(AmadeusClient(api_key="nur-key").configured)
        env = {"AMADEUS_API_KEY": "k", "AMADEUS_API_SECRET": "s"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertTrue(AmadeusClient().configured)

    def test_build_sources_share_client(self):
        a, b = build_sources("k", "s", env="production", max_requests=7, request_delay=0,
                             scan_months=3, today=TODAY)
        self.assertIs(a.client, b.client)
        self.assertEqual((a.name, b.name), ("amadeus-dates", "amadeus-offers"))
        self.assertTrue(a.records_history and b.records_history)
        self.assertEqual(a.client.max_requests, 7)
        self.assertEqual(a.client.base, "https://api.amadeus.com")
        self.assertEqual(a.scan_months, 3)


# -- Token ----------------------------------------------------------------------------------
class TokenTest(ApiCase):
    def test_token_request_form_encoded_and_cached(self):
        self.dates.fetch(alert())
        first = self.api.calls[0]
        self.assertEqual(first.path, TOKEN_PATH)
        self.assertEqual(first.url, "https://test.api.amadeus.com/v1/security/oauth2/token")
        self.assertEqual(first.method, "POST")
        self.assertEqual(first.headers["Content-Type"], "application/x-www-form-urlencoded")
        self.assertEqual(first.data, b"grant_type=client_credentials&client_id=KEY&client_secret=SECRET")
        data_call = self.api.of(DATES_PATH)[0]
        self.assertEqual(data_call.headers["Authorization"], "Bearer CpjU0sEenniHCgPDrndzOSWFk5mN")
        self.assertIn("application/vnd.amadeus+json", data_call.headers["Accept"])
        self.assertEqual(data_call.method, None, "GET")
        # zweiter Abruf: Token aus dem Cache
        self.dates.fetch(alert())
        self.assertEqual(self.api.paths().count(TOKEN_PATH), 1)
        self.assertEqual(self.client.token_requests, 1)
        self.assertEqual(self.client.requests_made, 2, "Token-Abruf zählt nicht als Abfrage")

    def test_token_refreshed_when_expired(self):
        self.dates.fetch(alert())
        self.client._token_expires_at = 0
        self.dates.fetch(alert())
        self.assertEqual(self.api.paths().count(TOKEN_PATH), 2)

    def test_key_rejected_on_token_endpoint(self):
        self.api.queue[TOKEN_PATH] = [http_error("token_invalid_client")]
        with self.assertRaises(SourceError) as cm:
            self.dates.fetch(alert())
        self.assertIn("Amadeus lehnt den Key ab", str(cm.exception))
        self.assertIn("Client credentials are invalid", str(cm.exception))
        self.assertIn("Einstellungen", str(cm.exception))
        n = len(self.api.calls)
        # Schutzschalter: die zweite Quelle versucht es gar nicht erst
        with self.assertRaises(SourceError) as cm:
            self.offers.fetch(business())
        self.assertIn("Amadeus lehnt den Key ab", str(cm.exception))
        self.assertEqual(len(self.api.calls), n)

    def test_token_response_without_access_token(self):
        self.api.queue[TOKEN_PATH] = [{"type": "amadeusOAuth2Token", "state": "expired"}]
        with self.assertRaises(SourceError) as cm:
            self.dates.fetch(alert())
        self.assertIn("access_token", str(cm.exception))

    def test_missing_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            src = AmadeusDatesSource(AmadeusClient(request_delay=0), today=TODAY)
            with self.assertRaises(SourceError) as cm:
                src.fetch(alert())
            self.assertIn("Amadeus-Key/Secret fehlen", str(cm.exception))
            self.assertEqual(self.api.calls, [])
            # nicht zuständige Kabine: leise leer, kein Gemecker über den Key
            self.assertEqual(AmadeusOffersSource(src.client, today=TODAY).fetch(alert()), [])


# -- Economy: Flight Cheapest Date Search ------------------------------------------------
class DatesTest(ApiCase):
    def test_params_roundtrip(self):
        self.dates.fetch(alert())
        calls = self.api.of(DATES_PATH)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url, "https://test.api.amadeus.com/v1/shopping/flight-dates")
        self.assertEqual(calls[0].params, {
            "origin": "FRA", "destination": "PEK", "departureDate": "2026-11-05,2026-11-30",
            "oneWay": "false", "duration": "1,21", "nonStop": "false", "viewBy": "DATE"})

    def test_params_oneway_nonstop_more_months(self):
        self.dates.fetch(alert(trip="oneway", max_stops=0, months_ahead=2))
        calls = self.api.of(DATES_PATH)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].params["oneWay"], "true")
        self.assertIsNone(calls[0].params["duration"], "duration ist bei oneWay verboten")
        self.assertEqual(calls[0].params["nonStop"], "true")
        self.assertEqual(calls[1].params["departureDate"], "2026-12-01,2026-12-31")

    def test_duration_defaults(self):
        for nights, wanted in (((None, None), "1,30"), ((5, None), "5,30"), ((None, 10), "1,10"),
                               ((7, 7), "7,7"), ((40, 60), "40,60")):
            api = FakeApi()
            with mock.patch("dealmonitor.http.request", api):
                AmadeusDatesSource(client(), today=TODAY).fetch(
                    alert(min_nights=nights[0], max_nights=nights[1]))
            self.assertEqual(api.of(DATES_PATH)[0].params["duration"], wanted, nights)

    def test_parse_fixture(self):
        offers = self.dates.fetch(alert())
        self.assertIsNone(self.dates.incomplete)
        self.assertEqual(len(offers), 6)
        self.assertEqual({o.source for o in offers}, {"amadeus-dates"})
        self.assertEqual([o.price for o in offers], [489.37, 412.9, 455.12, 398.6, 521.0, 604.75])
        best = min(offers, key=lambda o: o.price)
        self.assertEqual((best.origin, best.destination), ROUTE)
        self.assertEqual((best.departure, best.return_date), (date(2027, 3, 12), date(2027, 3, 26)))
        self.assertEqual(best.nights, 14)
        self.assertEqual((best.currency, best.cabin, best.roundtrip), ("EUR", "economy", True))
        self.assertIsNone(best.stops, "Umstiege sind ohne nonStop unbekannt")
        self.assertIsNone(best.airline)
        self.assertIsNone(best.link)
        self.assertEqual(len({o.fingerprint for o in offers}), 6)

    def test_nonstop_request_means_zero_stops(self):
        offers = self.dates.fetch(alert(max_stops=0))
        self.assertEqual({o.stops for o in offers}, {0})

    def test_oneway_has_no_return(self):
        offers = self.dates.fetch(alert(trip="oneway"))
        self.assertEqual({o.return_date for o in offers}, {None})
        self.assertEqual({o.roundtrip for o in offers}, {False})

    def test_currency_must_be_eur(self):
        data = copy.deepcopy(DATES)
        data["meta"]["currency"] = "USD"
        self.api.responses[DATES_PATH] = data
        with self.assertLogs("dealmonitor.sources.amadeus", "WARNING") as logs:
            self.assertEqual(self.dates.fetch(alert()), [])
        self.assertIn("USD statt EUR", "\n".join(logs.output))
        self.assertIsNone(self.dates.incomplete)
        del data["meta"]["currency"]
        self.assertEqual(self.dates.fetch(alert()), [], "ohne Währungsangabe kein Angebot")

    def test_broken_rows_skipped(self):
        data = copy.deepcopy(DATES)
        data["data"][0]["price"] = {}
        data["data"][1]["departureDate"] = "irgendwann"
        del data["data"][2]["returnDate"]
        data["data"].append("kaputt")
        self.api.responses[DATES_PATH] = data
        self.assertEqual(len(self.dates.fetch(alert())), 3)

    def test_skips_other_cabins(self):
        for cabin in ("premium", "business", "first"):
            self.assertEqual(self.dates.fetch(business(cabin=cabin)), [])
        self.assertEqual(self.api.calls, [])

    def test_404_means_no_data(self):
        self.api.queue[DATES_PATH] = [http_error("not_found")]
        self.assertEqual(self.dates.fetch(alert()), [])
        self.assertIsNone(self.dates.incomplete)

    def test_400_detail_in_log_and_partial_result(self):
        self.api.queue[DATES_PATH] = [http_error("invalid_date")]
        with self.assertLogs("dealmonitor.sources.amadeus", "WARNING") as logs:
            offers = self.dates.fetch(alert(months_ahead=2))
        self.assertEqual(len(offers), 6, "zweiter Monat trotzdem abgefragt")
        self.assertIn("HTTP 400: Date/Time is in the past", "\n".join(logs.output))
        self.assertEqual(self.dates.incomplete,
                         {"message": "1 von 2 Anfragen fehlgeschlagen", "routes": {ROUTE}})

    def test_all_failed_raises(self):
        self.api.queue[DATES_PATH] = [http_error("invalid_format"), http_error("invalid_format")]
        with self.assertRaises(SourceError) as cm:
            self.dates.fetch(alert(months_ahead=2))
        self.assertEqual(str(cm.exception), "Alle 2 Amadeus-Anfragen fehlgeschlagen")

    def test_429_trips_breaker_for_both_sources(self):
        self.api.queue[DATES_PATH] = [http_error("too_many_requests")]
        with self.assertRaises(SourceError) as cm:
            self.dates.fetch(alert())
        self.assertIn("HTTP 429", str(cm.exception))
        self.assertIn("später erneut", str(cm.exception))
        n = len(self.api.calls)
        with self.assertRaises(SourceError):
            self.offers.fetch(business())
        self.assertEqual(len(self.api.calls), n, "nach 429 keine weitere Anfrage in diesem Lauf")
        self.client.reset()
        self.assertIsNone(self.client.down)
        self.assertEqual(len(self.dates.fetch(alert())), 6, "neuer Lauf: Schutzschalter wieder zu")
        self.assertEqual(self.api.paths().count(TOKEN_PATH), 1, "Token überlebt den Reset")

    def test_403_trips_breaker(self):
        self.api.queue[DATES_PATH] = [http_error("forbidden")]
        with self.assertRaises(SourceError) as cm:
            self.dates.fetch(alert())
        self.assertIn("HTTP 403: Access forbidden", str(cm.exception))
        self.assertIsNotNone(self.client.down)

    def test_401_refreshes_token_once_then_rejects(self):
        self.api.queue[DATES_PATH] = [http_error("access_token_expired"),
                                      http_error("api_key_revoked")]
        with self.assertRaises(SourceError) as cm:
            self.dates.fetch(alert())
        self.assertIn("Amadeus lehnt den Key ab", str(cm.exception))
        self.assertIn("The API key is revoked", str(cm.exception))
        self.assertEqual(self.api.paths().count(TOKEN_PATH), 2, "genau ein neuer Token")
        self.assertIsNotNone(self.client.down)

    def test_401_then_success_after_token_refresh(self):
        self.api.queue[DATES_PATH] = [http_error("invalid_access_token")]
        self.assertEqual(len(self.dates.fetch(alert())), 6)
        self.assertEqual(self.api.paths().count(TOKEN_PATH), 2)
        self.assertEqual(self.client.requests_made, 2)
        self.assertIsNone(self.dates.incomplete)

    def test_network_breaker_after_five_failures(self):
        self.api.queue[DATES_PATH] = [http_error(None)] * 5
        with self.assertRaises(SourceError) as cm:
            self.dates.fetch(alert(months_ahead=6))
        self.assertIn("nicht erreichbar", str(cm.exception))
        self.assertIn("5 Fehler hintereinander", str(cm.exception))
        self.assertEqual(len(self.api.of(DATES_PATH)), 5)

    def test_server_error_counts_as_partial_not_breaker(self):
        self.api.queue[DATES_PATH] = [http_error("server_error")]
        offers = self.dates.fetch(alert(months_ahead=2))
        self.assertEqual(len(offers), 6)
        self.assertEqual(self.dates.incomplete["message"], "1 von 2 Anfragen fehlgeschlagen")
        self.assertIsNone(self.client.down)

    def test_budget_limit_shared_between_sources(self):
        self.client.max_requests = 2
        offers = self.dates.fetch(alert(months_ahead=4))
        self.assertEqual(len(offers), 12)
        self.assertEqual(self.client.requests_made, 2)
        self.assertEqual(self.client.remaining, 0)
        self.assertEqual(self.dates.incomplete,
                         {"message": "Amadeus: 2 von 4 Abfragen (Limit)", "routes": {ROUTE}})
        # Budget ist aufgebraucht: die Angebots-Quelle fragt nichts mehr ab, meldet es aber
        n = len(self.api.calls)
        self.assertEqual(self.offers.fetch(business()), [])
        self.assertEqual(self.offers.incomplete,
                         {"message": "Amadeus: 0 von 2 Abfragen (Limit)", "routes": {ROUTE}})
        self.assertEqual(len(self.api.calls), n)

    def test_limit_plus_errors_in_one_message(self):
        self.client.max_requests = 2
        self.api.queue[DATES_PATH] = [http_error("invalid_format")]
        self.dates.fetch(alert(months_ahead=3))
        self.assertEqual(self.dates.incomplete["message"],
                         "Amadeus: 2 von 3 Abfragen (Limit); 1 von 3 Anfragen fehlgeschlagen")

    def test_month_already_over_is_skipped(self):
        src = AmadeusDatesSource(self.client, today=date(2026, 11, 30))
        self.assertEqual(src.fetch(alert(months_ahead=1)), [])
        self.assertEqual(self.api.calls, [])
        self.assertIsNone(src.incomplete)

    def test_several_routes(self):
        offers = self.dates.fetch(alert(origins=["FRA", "MUC"], destinations=["PEK"]))
        self.assertEqual([c.params["origin"] for c in self.api.of(DATES_PATH)], ["FRA", "MUC"])
        # Die Attrappe antwortet immer mit FRA-PEK; Quelle übernimmt origin/destination der Antwort
        self.assertEqual({(o.origin, o.destination) for o in offers}, {ROUTE})


# -- Business/First: Flight Offers Search --------------------------------------------------
class OffersTest(ApiCase):
    def test_params_two_dates_per_month(self):
        self.offers.fetch(business())
        calls = self.api.of(OFFERS_PATH)
        self.assertEqual(len(calls), 2, "Freitag und Dienstag um die Monatsmitte")
        self.assertEqual(calls[0].url, "https://test.api.amadeus.com/v2/shopping/flight-offers")
        self.assertEqual(calls[0].params, {
            "originLocationCode": "FRA", "destinationLocationCode": "PEK",
            "departureDate": "2026-11-13", "returnDate": "2026-11-24", "adults": 1,
            "travelClass": "BUSINESS", "nonStop": "false", "currencyCode": "EUR", "max": 10,
            "excludedAirlineCodes": None})
        self.assertEqual((calls[1].params["departureDate"], calls[1].params["returnDate"]),
                         ("2026-11-17", "2026-11-28"))

    def test_params_other_cabins_and_oneway(self):
        self.offers.fetch(business(cabin="first", trip="oneway", max_stops=0))
        p = self.api.of(OFFERS_PATH)[0].params
        self.assertEqual((p["travelClass"], p["returnDate"], p["nonStop"]), ("FIRST", None, "true"))
        self.offers.fetch(business(cabin="premium"))
        self.assertEqual(self.api.of(OFFERS_PATH)[-1].params["travelClass"], "PREMIUM_ECONOMY")

    def test_exclude_airlines_param_and_filter(self):
        offers = self.offers.fetch(business(exclude_airlines=["ca", "Ryanair"]))
        self.assertEqual(self.api.of(OFFERS_PATH)[0].params["excludedAirlineCodes"], "CA",
                         "nur IATA-Codes an die API")
        self.assertEqual({o.airline for o in offers}, {"LH", "TK"})

    def test_parse_fixture(self):
        offers = self.offers.fetch(business())
        self.assertEqual(len(offers), 6, "3 Angebote x 2 Termine")
        self.assertEqual({o.source for o in offers}, {"amadeus-offers"})
        by_airline = {o.airline: o for o in offers[:3]}
        ca, lh, tk = by_airline["CA"], by_airline["LH"], by_airline["TK"]
        self.assertEqual(ca.price, 1289.46)
        self.assertEqual((ca.origin, ca.destination), ROUTE)
        self.assertEqual((ca.departure, ca.return_date), (date(2027, 3, 12), date(2027, 3, 24)))
        self.assertEqual(ca.nights, 12)
        self.assertEqual((ca.stops, ca.duration_out_min, ca.duration_back_min), (0, 620, 670))
        self.assertEqual((ca.cabin, ca.roundtrip, ca.currency, ca.link),
                         ("business", True, "EUR", None))
        self.assertEqual((lh.price, lh.stops, lh.duration_out_min, lh.duration_back_min),
                         (1489.0, 1, 810, 685))
        self.assertEqual((tk.price, tk.stops, tk.duration_out_min, tk.duration_back_min),
                         (1099.0, 1, 905, 1010))
        self.assertIsNone(self.offers.incomplete)

    def test_cabin_safety_net(self):
        data = copy.deepcopy(OFFERS)
        # API ignoriert travelClass -> alle Segmente Economy -> nie als Business melden
        for fd in data["data"][0]["travelerPricings"][0]["fareDetailsBySegment"]:
            fd["cabin"] = "ECONOMY"
        # Zubringer in Economy, Langstrecke Business -> bleibt ein Business-Angebot
        data["data"][1]["travelerPricings"][0]["fareDetailsBySegment"][0]["cabin"] = "ECONOMY"
        self.api.responses[OFFERS_PATH] = data
        offers = self.offers.fetch(business(months_ahead=1))
        self.assertEqual({o.airline for o in offers}, {"LH", "TK"})
        # First-Alarm: Business-Angebote reichen nicht
        self.assertEqual(self.offers.fetch(business(cabin="first")), [])

    def test_currency_check_per_offer(self):
        data = copy.deepcopy(OFFERS)
        data["data"][2]["price"]["currency"] = "USD"
        self.api.responses[OFFERS_PATH] = data
        with self.assertLogs("dealmonitor.sources.amadeus", "WARNING"):
            offers = self.offers.fetch(business())
        self.assertEqual({o.airline for o in offers}, {"CA", "LH"})

    def test_technical_stop_counts_as_stop(self):
        data = copy.deepcopy(OFFERS)
        data["data"][0]["itineraries"][0]["segments"][0]["numberOfStops"] = 1
        self.api.responses[OFFERS_PATH] = data
        ca = [o for o in self.offers.fetch(business()) if o.airline == "CA"][0]
        self.assertEqual(ca.stops, 1)

    def test_grand_total_preferred_over_total(self):
        data = copy.deepcopy(OFFERS)
        data["data"][0]["price"]["grandTotal"] = "1299.99"
        self.api.responses[OFFERS_PATH] = data
        ca = [o for o in self.offers.fetch(business()) if o.airline == "CA"][0]
        self.assertEqual(ca.price, 1299.99)

    def test_roundtrip_offers_do_not_fit_oneway_alert(self):
        self.assertEqual(self.offers.fetch(business(trip="oneway")), [])
        self.assertEqual(len(self.api.of(OFFERS_PATH)), 2)

    def test_broken_offers_skipped(self):
        data = copy.deepcopy(OFFERS)
        data["data"][0]["itineraries"] = []
        data["data"][1]["price"]["grandTotal"] = "0.00"
        data["data"].append({"type": "flight-offer"})
        data["data"].append(None)
        self.api.responses[OFFERS_PATH] = data
        self.assertEqual({o.airline for o in self.offers.fetch(business())}, {"TK"})

    def test_skips_economy(self):
        self.assertEqual(self.offers.fetch(alert()), [])
        self.assertEqual(self.api.calls, [])

    def test_404_and_400_per_query(self):
        self.api.queue[OFFERS_PATH] = [http_error("not_found"), http_error("invalid_date")]
        offers = self.offers.fetch(business())
        self.assertEqual(offers, [])
        self.assertEqual(self.offers.incomplete,
                         {"message": "1 von 2 Anfragen fehlgeschlagen", "routes": {ROUTE}})


# -- Zusammenspiel mit dem Monitor ---------------------------------------------------------
class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        patcher = mock.patch("dealmonitor.http.request", self.api)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.cfg = config_mod.Config(settings=config_mod.Settings(), alerts=[], path=Path("x"))

    def sources(self, **kw):
        opts = dict(request_delay=0, today=TODAY)
        opts.update(kw)
        return build_sources("KEY", "SECRET", **opts)

    def test_business_deal_and_history(self):
        items, res = run_alert(business(max_price=1200), self.sources(), self.store, self.cfg,
                               today=TODAY)
        self.assertEqual(res.errors, [], "die Economy-Quelle schweigt bei Business-Alarmen")
        self.assertEqual(res.offers, 6)
        self.assertEqual([(d.offer.price, s) for d, s in items], [(1099.0, "new")])
        d = items[0][0]
        self.assertEqual(d.offer.source, "amadeus-offers")
        self.assertTrue(d.headline.startswith("Business Class:"), d.headline)
        # Die Attrappe liefert für beide Termine dieselben Abflugtage -> Historie ist pro
        # Preis/Tag eindeutig, also einmal je Angebot
        self.assertEqual(sorted(self.store.history("FRA-PEK-RT-business", 60)),
                         [1099.0, 1289.46, 1489.0], "Preise fließen in die Historie")

    def test_economy_deal_from_month_scan(self):
        items, res = run_alert(alert(max_price=400), self.sources(), self.store, self.cfg,
                               today=TODAY)
        self.assertEqual(res.errors, [])
        self.assertEqual([(d.offer.price, d.offer.source) for d, _ in items],
                         [(398.6, "amadeus-dates")])
        self.assertEqual(len(self.store.history("FRA-PEK-RT-economy", 60)), 6)

    def test_limit_reported_but_nothing_expired(self):
        run_alert(alert(max_price=400), self.sources(), self.store, self.cfg, today=TODAY)
        self.assertEqual(len(self.store.deals(status="active")), 1)
        items, res = run_alert(alert(max_price=400), self.sources(max_requests=0), self.store,
                               self.cfg, today=TODAY)
        self.assertEqual(items, [])
        self.assertEqual(res.expired, 0, "nicht abgefragt != verschwunden")
        self.assertTrue(any("Limit" in e for e in res.errors), res.errors)
        self.assertEqual(len(self.store.deals(status="active")), 1)

    def test_key_error_reported_once_per_alert(self):
        self.api.queue[TOKEN_PATH] = [http_error("token_invalid_client")]
        items, res = run_alert(alert(), self.sources(), self.store, self.cfg, today=TODAY)
        self.assertEqual(items, [])
        self.assertEqual(len(res.errors), 1, "nur die zuständige Quelle meldet den Key-Fehler")
        self.assertIn("Amadeus lehnt den Key ab", res.errors[0])


if __name__ == "__main__":
    unittest.main()
