"""Tests für Googles Preisniveau (ds:1) und den zweiten Abruf bei leerer Seite.

Die ds1-Fixtures sind gekürzte echte Antworten vom 05.11.2026 (ZRH→HAM 04.01.2027 one-way,
FRA→PEK 10.–24.03.2027 Hin/Rück): Suchoptionen, Ergebniszeilen, Preisniveau-Satz und der
unveränderte ds:1-Block. Weitere Messwerte aus der Recherche (RESEARCH.md, Paket 2) werden als
kleine ds:1-Blöcke nachgebaut.
"""

import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from dealmonitor import http
from dealmonitor.models import Alert
from dealmonitor.sources import google_insights as gi
from dealmonitor.sources.googleflights import (
    EmptyPage, GoogleFlightsSource, ParseError, build_url, empty_kind, parse_results,
)

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 4)
OW = (FIX / "google_flights_zrh_ham_oneway_ds1.html").read_text(encoding="utf-8")
PEK = (FIX / "google_flights_fra_pek_ds1.html").read_text(encoding="utf-8")
OLD = (FIX / "google_flights_zrh_ham.html").read_text(encoding="utf-8")
#: leere Suchmaske: Google-Seite ohne Ergebnisse und ohne "keine Flüge"
EMPTY_MASK = ("<html><head><title>Google Flüge</title></head><body><script>"
              "AF_initDataCallback({key: 'ds:0', hash: '1', data:[], sideChannel: {}});"
              "</script></body></html>")
#: ErrorResponse in der echten Form: ohne ``hash``, zwei Leerzeichen, ``errorHasStatus``
ERROR_PAGE = ("<html><head><title>Google Flüge</title></head><body><script>"
              "AF_initDataCallback({key: 'ds:1',  data:[13,null,\"x\"], errorHasStatus: true,});"
              "</script></body></html>")
#: echte Fehlerseite (gekürzt): HAJ→HGH one-way per tfs-Link, 05.11.2026
ERROR13 = (FIX / "google_flights_error13.html").read_text(encoding="utf-8")


def ds1_page(block, at=5, wrap=False):
    """Seite mit ds:1, das Preisniveau-Element an Position ``at`` (ggf. eine Ebene tiefer)."""
    data = [[None, [[1, 2, 3]]], [], None, [[["x", 1]]], None, None, None, None]
    data[at] = [block] if wrap else block
    return ("<script class=\"ds:1\">AF_initDataCallback({key: 'ds:1', hash: '9', data:"
            + json.dumps(data) + ", sideChannel: {}});</script>")


def block(code, low, typical, diff, lo, hi, history=()):
    return [code, [None, low], [None, typical], [None, diff], [None, lo], [None, hi], 1,
            None, None, None, [list(map(list, history))]]


def summary(ins):
    return [ins[k] for k in ("level_code", "lowest", "typical", "saving", "typical_low",
                             "typical_high")]


def alert(**kw):
    base = dict(id="t", name="Test", origins=["ZRH"], destinations=["HAM"], max_price=200,
                baseline_price=250, max_stops=1, min_nights=1, max_nights=21, months_ahead=1,
                trip="oneway")
    base.update(kw)
    return Alert(**base)


def source(pages, **kw):
    """Quelle, deren Abrufe der Reihe nach ``pages`` liefern (danach immer die letzte)."""
    pages = list(pages)
    calls = []

    def fetcher(url):
        calls.append(url)
        item = pages.pop(0) if len(pages) > 1 else pages[0]
        if isinstance(item, Exception):
            raise item
        return item

    opts = dict(max_requests=40, delay=0, today=TODAY, fetcher=fetcher, seed=1)
    opts.update(kw)
    src = GoogleFlightsSource(**opts)
    src.calls = calls
    return src


class ParseTest(unittest.TestCase):
    def test_live_fixtures(self):
        ins = gi.parse(OW)
        self.assertEqual(summary(ins), [3, 126, 105, -22, 90, 150])
        self.assertEqual(ins["level"], "typisch", "126 € liegt in der Spanne 90–150 €")
        self.assertEqual(len(ins["history"]), 62)
        self.assertEqual(ins["history"][0], ["2026-09-05", 113])
        self.assertEqual(ins["history"][-1], ["2026-11-05", 126])
        self.assertEqual(ins["lowest"], parse_results(OW)[0]["price"], "Tiefstpreis = 1. Zeile")
        ins = gi.parse(PEK)
        self.assertEqual(summary(ins), [2, 720, 723, 3, 600, 1050])
        self.assertEqual(ins["level"], "typisch")
        json.dumps(ins)   # JSON-tauglich (landet in Offer.insight / Store)

    def test_old_fixture_has_no_insight(self):
        self.assertIsNone(gi.parse(OLD))
        self.assertFalse(gi.is_error_response(OLD))

    def test_measured_values(self):
        cases = [  # Messwerte aus der Recherche -> abgeleitetes Niveau
            ((1, 168, 235, 67, 195, 275), "niedrig"),        # ZRH–HAM RT 15.–19.01.27
            ((2, 720, 723, 3, 600, 1050), "typisch"),        # FRA–PEK RT
            ((4, 220, 181, -40, 155, 250), "typisch"),       # HAM–ZRH RT, Code 4 = "normal"
            ((2, 70, 79, 9, 55, 135), "typisch"),            # HAM→ZRH one-way
            ((1, 82, 105, 23, 90, 150), "niedrig"),          # ZRH→HAM one-way 14.01.27
            ((2, 2898, 3061, 163, 2600, 3750), "typisch"),   # FRA–PEK Business
            ((5, 400, 300, -100, 250, 350), "hoch"),         # Code 5 ungesehen: Preis entscheidet
        ]
        for values, level in cases:
            ins = gi.parse(ds1_page(block(*values, history=[(1788559200000, 164)])))
            self.assertEqual(summary(ins), list(values))
            self.assertEqual(ins["level"], level, values)
            self.assertEqual(ins["history"], [["2026-09-05", 164]])

    def test_found_by_structure_not_index(self):
        b = block(1, 168, 235, 67, 195, 275)
        for at, wrap in ((5, False), (7, False), (6, True)):
            self.assertEqual(gi.parse(ds1_page(b, at, wrap))["lowest"], 168, (at, wrap))
        # ohne hash-Angabe
        page = ds1_page(b).replace(" hash: '9',", "")
        self.assertEqual(gi.parse(page)["lowest"], 168)

    def test_error_response(self):
        self.assertTrue(gi.is_error_response(ERROR_PAGE))
        self.assertIsNone(gi.parse(ERROR_PAGE))
        self.assertFalse(gi.is_error_response(OW))

    def test_real_error_response_form(self):
        """Echte Fehlerseite: ``{key: 'ds:1',  data:[13,…], errorHasStatus: true,}`` - ohne
        ``hash`` und ohne ``sideChannel``; das alte Muster erkannte sie nie."""
        self.assertIn("errorHasStatus", ERROR13)
        self.assertNotRegex(ERROR13, r"key: 'ds:1', hash:")
        self.assertTrue(gi.is_error_response(ERROR13))
        self.assertEqual(gi.ds1_data(ERROR13)[0], 13)
        self.assertIsNone(gi.parse(ERROR13))
        # die übrigen echten Vorlagen sind keine Fehlerseiten
        for name in ("google_flights_unparsed.html", "google_flights_rt_no_itineraries.html"):
            self.assertFalse(gi.is_error_response((FIX / name).read_text(encoding="utf-8")), name)

    def test_broken_structures_give_none(self):
        pages = [
            None, "", "<html></html>",
            "AF_initDataCallback({key: 'ds:1', hash: '1', data:[1,2, sideChannel: {}});",  # kaputt
            ds1_page([1, [None, "168"], [None, 235], [None, 67], [None, 195], [None, 275]]),
            ds1_page([True, [None, 168], [None, 235], [None, 67], [None, 195], [None, 275]]),
            ds1_page(block(1, 168, 235, 67, 300, 200)),     # Spanne verdreht
            ds1_page(block(1, 0, 235, 67, 195, 275)),       # Preis 0
            ds1_page([1, [None, 168], [None, 235]]),        # zu kurz
        ]
        for page in pages:
            self.assertIsNone(gi.parse(page), page and page[-120:])

    def test_strange_history_is_dropped(self):
        b = block(1, 168, 235, 67, 195, 275)
        b[10] = [[["gestern", 1]]]
        self.assertEqual(gi.parse(ds1_page(b))["history"], [])
        b[10] = [[[10 ** 30, 1]]]                           # Zeitstempel unmöglich
        self.assertEqual(gi.parse(ds1_page(b))["history"], [])

    def test_text_fallback(self):
        ins = gi.parse('<div role="heading">Die Preise sind aktuell <span class="g">niedrig'
                       '</span></div><div>– 67 € unter dem Normalpreis der Suchergebnisse</div>')
        self.assertEqual((ins["level"], ins["saving"], ins["lowest"]), ("niedrig", 67, None))
        ins = gi.parse('Die Preise sind derzeit <span class="g">normal</span>')
        self.assertEqual((ins["level"], ins["saving"]), ("typisch", None))
        ins = gi.parse('Die Preise sind derzeit <span>hoch</span> – 40 € über dem Normalpreis')
        self.assertEqual((ins["level"], ins["saving"]), ("hoch", -40))
        # ds:1 hat Vorrang vor dem Text
        self.assertEqual(gi.parse(OW)["level_code"], 3)


class OfferInsightTest(unittest.TestCase):
    def test_every_offer_carries_insight(self):
        src = source([OW])
        offers = src.fetch(alert())
        self.assertEqual(len(offers), 2 * 10)
        self.assertTrue(all(o.insight and o.insight["lowest"] == 126 for o in offers))
        self.assertEqual(offers[0].extras()["insight"]["typical_low"], 90)
        json.dumps(offers[0].extras())
        self.assertEqual(src.requests_made, 2, "kein zusätzlicher Abruf")

    def test_to_offers_and_query(self):
        src = source([PEK])
        dep, ret = date(2027, 3, 10), date(2027, 3, 24)
        rows = src.query("FRA", "PEK", dep, ret)
        url = build_url("FRA", "PEK", dep, ret)
        self.assertEqual(src.insight(url)["lowest"], 720)
        a = alert(origins=["FRA"], destinations=["PEK"], trip="roundtrip", max_price=900)
        offers = src.to_offers(rows, a, "FRA", "PEK", dep, ret, url, insight=src.insight(url))
        self.assertEqual({o.insight["typical"] for o in offers}, {723})
        self.assertIsNone(src.to_offers(rows, a, "FRA", "PEK", dep, ret, url)[0].insight)

    def test_old_fixture_offers_without_insight(self):
        src = source([OLD])
        offers = src.fetch(alert(trip="roundtrip"))
        self.assertTrue(offers)
        self.assertTrue(all(o.insight is None for o in offers))
        self.assertNotIn("insight", offers[0].extras())

    def test_verify_carries_insight(self):
        src = source([OW])
        hit = src.verify("ZRH", "HAM", date(2027, 1, 4), None, "economy", False)
        self.assertEqual((hit["price"], hit["airline"]), (126.0, "Eurowings"))
        self.assertEqual(hit["insight"]["level"], "typisch")
        miss = src.verify("ZRH", "HAM", date(2027, 1, 4), None, "economy", False,
                          max_minutes=60)
        self.assertIsNone(miss["price"])
        self.assertEqual(miss["insight"]["lowest"], 126)
        # Seite ohne ds:1: kein insight-Schlüssel (wie bisher)
        hit = source([OLD]).verify("ZRH", "HAM", date(2027, 1, 15), date(2027, 1, 19))
        self.assertNotIn("insight", hit)


class RetryTest(unittest.TestCase):
    def test_empty_kind_fallbacks(self):
        self.assertEqual(empty_kind(EMPTY_MASK, []), "empty", "Notbehelf: klein, ohne Merkmale")
        self.assertEqual(empty_kind(ERROR_PAGE, []), "error")
        self.assertEqual(empty_kind(ERROR13, []), "error")
        self.assertIsNone(empty_kind(OW, parse_results(OW)))
        self.assertIsNone(empty_kind("<html><body>neues Layout</body></html>", []),
                          "keine Google-Seite -> wie bisher Parser-Fehler")
        self.assertIsNone(empty_kind(
            "<html><body>Google Flüge: keine Flüge gefunden</body></html>", []))
        big = EMPTY_MASK + " " * 2_200_000
        self.assertIsNone(empty_kind(big, []), "groß und ohne Zeilen = Layout geändert")

    def test_empty_mask_then_results(self):
        src = source([EMPTY_MASK, OW])
        rows = src.query("ZRH", "HAM", date(2027, 1, 4), None, "economy", False)
        self.assertEqual(rows[0]["price"], 126)
        self.assertEqual((src.requests_made, src.retries, src.empty_fetches), (2, 1, 0))
        self.assertEqual(src.insight(src.calls[0])["lowest"], 126)

    def test_error_response_then_results(self):
        src = source([ERROR_PAGE, OW])
        rows = src.query("ZRH", "HAM", date(2027, 1, 4), None, "economy", False)
        self.assertEqual(rows[0]["price"], 126)
        self.assertEqual((src.requests_made, src.retries, src.empty_fetches), (2, 1, 0))

    def test_retry_waits(self):
        for page in (EMPTY_MASK, ERROR_PAGE, ERROR13):
            src = source([page, OW], delay=3.0)
            with mock.patch("dealmonitor.sources.googleflights.time.sleep") as sleep:
                src.query("ZRH", "HAM", date(2027, 1, 4), None, "economy", False)
            waits = [c.args[0] for c in sleep.call_args_list]
            self.assertEqual(len(waits), 1)
            self.assertTrue(3.8 <= waits[0] <= 6.0, waits)
            self.assertEqual(src.calls[0], src.calls[1], "vorübergehend: dieselbe URL")

    def test_error_response_with_rows_keeps_rows(self):
        page = OW.replace("data:[[null,", "data:[13,[null,", 1)
        self.assertTrue(gi.is_error_response(page))
        src = source([page])
        rows = src.query("ZRH", "HAM", date(2027, 1, 4), None, "economy", False)
        self.assertEqual(rows[0]["price"], 126, "Zeilen gelten, nur das Preisniveau fehlt")
        self.assertEqual(src.requests_made, 2)
        self.assertEqual(src.insight(src.calls[0])["level"], "typisch", "Satz als Rückfall")

    def test_empty_twice_is_empty_fetch_not_network_error(self):
        src = source([EMPTY_MASK, EMPTY_MASK, OW])
        offers = src.fetch(alert())
        self.assertEqual(len(src.calls), 3, "1. Termin: 2 Abrufe leer, 2. Termin: ok")
        self.assertEqual(len(offers), 10)
        self.assertEqual((src.empty_fetches, src._consecutive_failures), (1, 0))
        self.assertEqual(src.incomplete["routes"], {("ZRH", "HAM")})
        self.assertIn("1 von 2 Abfragen fehlgeschlagen (davon 1 leer)", src.incomplete["message"])
        self.assertFalse(src.down)

    def test_empty_fetch_does_not_feed_network_breaker(self):
        net = http.HttpError("Zeitüberschreitung", None, network=True)
        src = source([net, net, EMPTY_MASK, EMPTY_MASK, net, OW])
        a = alert(origins=["ZRH", "GVA", "BSL"])
        src.fetch(a)
        self.assertFalse(src.down, "Abruf leer: Seite kam an -> Netzfehler-Zähler beginnt neu")
        self.assertEqual(src.empty_fetches, 1)

    def test_only_empty_pages_trip_own_breaker(self):
        src = source([EMPTY_MASK])
        a = alert(origins=["ZRH", "GVA", "BSL"])
        with self.assertRaises(Exception) as cm:
            src.fetch(a)
        self.assertIn("nur leere Seiten", str(cm.exception))
        self.assertEqual(len(src.calls), 2 * GoogleFlightsSource.MAX_CONSECUTIVE_FAILURES)
        self.assertEqual(src.empty_fetches, GoogleFlightsSource.MAX_CONSECUTIVE_FAILURES)
        self.assertTrue(src.down)

    def test_no_retry_without_budget(self):
        src = source([EMPTY_MASK, OW], max_requests=1)
        with self.assertRaises(EmptyPage):
            src.query("ZRH", "HAM", date(2027, 1, 4), None, "economy", False)
        self.assertEqual((src.requests_made, src.retries, src.empty_fetches), (1, 0, 1))

    def test_unparseable_google_page_still_parse_error(self):
        big = EMPTY_MASK + " " * 2_200_000
        src = source([big])
        with self.assertRaises(ParseError) as cm:
            src.query("ZRH", "HAM", date(2027, 1, 4), None, "economy", False)
        self.assertNotIsInstance(cm.exception, EmptyPage)
        self.assertEqual((src.requests_made, src.retries), (1, 0))

    def test_retry_comes_from_alert_share_not_reserve(self):
        src = source([EMPTY_MASK, OW], max_requests=4, verify_reserve=1)
        src.fetch(alert(months_ahead=2))                 # 4 Termine geplant, Scan-Budget 3
        self.assertEqual((src.requests_made, src.scans_made, src.retries), (3, 3, 1))
        self.assertIn("2 von 4 Abfragen (Limit)", src.incomplete["message"])
        self.assertIsNotNone(src.verify("ZRH", "HAM", date(2027, 3, 3), None, "economy", False),
                             "Reserve für die Gegenprüfung bleibt")
        self.assertEqual(src.requests_made, 4)

    def test_retry_counts_in_alert_share(self):
        src = source([EMPTY_MASK, OW], max_requests=10)
        a = alert(id="a", months_ahead=1)                # 2 Abfragen
        src.begin_run([a, alert(id="b", origins=["GVA"])])
        self.assertEqual(src._shares, {"a": 2, "b": 2})
        src.fetch(a)
        self.assertEqual((src.requests_made, src.scans_made), (2, 2), "Anteil von a verbraucht")
        self.assertIn("1 von 2 Abfragen (Limit)", src.incomplete["message"])
        self.assertEqual(src._scan_pending, 2, "Anteil von b bleibt reserviert")

    def test_verify_empty_twice(self):
        src = source([EMPTY_MASK])
        self.assertIsNone(src.verify("ZRH", "HAM", date(2027, 1, 4), None, "economy", False))
        self.assertIn("Abruf leer", src.last_error)
        self.assertEqual((src.requests_made, src.verifications, src.empty_fetches), (2, 1, 1))

    def test_reset_clears_counters(self):
        src = source([EMPTY_MASK])
        src.verify("ZRH", "HAM", date(2027, 1, 4), None, "economy", False)
        src.reset()
        self.assertEqual((src.retries, src.empty_fetches, src._consecutive_empty), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
