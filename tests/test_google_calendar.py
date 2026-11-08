"""Google-Preiskalender (``GetCalendarGraph``) als Vorfilter: Anfrage, Parser, Kandidaten,
Planung mit Bestätigung, Budget, Wochentage, Rückfall und Einstellung."""

import json
import logging
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote

from dealmonitor import rotation
from dealmonitor.appsettings import Context
from dealmonitor.models import Alert
from dealmonitor.sources import SourceError
from dealmonitor.sources import google_calendar as gc
from dealmonitor.sources.googleflights import GoogleBlocked, GoogleFlightsSource

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 8)                       # Sonntag
OW_PAGE = (FIX / "google_flights_zrh_ham_oneway_ds1.html").read_text(encoding="utf-8")
RT_PAGE = (FIX / "google_flights_zrh_ham.html").read_text(encoding="utf-8")


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def fixture(name):
    return (FIX / f"google_calendar_{name}.txt").read_text(encoding="utf-8")


def cal_text(prices, nights=None):
    """Kalender-Antwort im echten Aufbau aus ``{Tag: Preis}``."""
    rows = [[d.isoformat(), (d + timedelta(days=nights)).isoformat() if nights else None,
             [[None, p], "tok"], 1] for d, p in sorted(prices.items())]
    inner = json.dumps([[None, None, 0, "x"], rows])
    return ")]}'\n\n" + json.dumps([["wrb.fr", None, inner]]) + "\n"


def decode(body):
    outer = json.loads(unquote(body.decode()[len("f.req="):]))
    return json.loads(outer[1])


def ow_alert(**kw):
    base = dict(id="ow", name="OW", origins=["HAM"], destinations=["ZRH"], trip="oneway",
                max_price=45, baseline_price=95, max_stops=0, months_ahead=2)
    base.update(kw)
    return Alert(**base)


def rt_alert(**kw):
    base = dict(id="rt", name="RT", origins=["ZRH"], destinations=["HAM"], max_price=110,
                baseline_price=190, max_stops=1, min_nights=1, max_nights=21, months_ahead=2)
    base.update(kw)
    return Alert(**base)


def source(prices=None, *, cal=None, **kw):
    """Quelle mit Fake-Kalender (``prices`` je Tag, oder ``cal(body) -> text``) und fester
    Ergebnisseite für die Bestätigungen; zeichnet Kalender-Bodys und Seiten auf."""
    pages, bodies = [], []

    def calendar_fetcher(body):
        bodies.append(decode(body))
        if cal is not None:
            return cal(body)
        inner = decode(body)
        start = date.fromisoformat(inner[2][0])
        end = date.fromisoformat(inner[2][1])
        nights = inner[4][0] if len(inner) > 4 else None
        days = {d: p for d, p in (prices or {}).items() if start <= d <= end}
        return cal_text(days, nights)

    def fetcher(url):
        pages.append(url)
        return OW_PAGE if "%20one%20way" in url else RT_PAGE

    opts = dict(max_requests=40, delay=0, today=TODAY, fetcher=fetcher, seed=1,
                calendar_fetcher=calendar_fetcher, ota=False)
    opts.update(kw)
    src = GoogleFlightsSource(**opts)
    src.pages, src.bodies = pages, bodies
    return src


def flat(prices_from, days, price=100.0):
    return {prices_from + timedelta(days=i): price for i in range(days)}


class RequestParseTest(unittest.TestCase):
    def test_request_oneway_and_roundtrip(self):
        ow = decode(gc.request_body("HAM", "ZRH", date(2027, 1, 1), date(2027, 1, 31),
                                    max_stops=0))
        self.assertEqual(len(ow), 3)
        self.assertEqual(ow[2], ["2027-01-01", "2027-01-31"])
        filt = ow[1]
        self.assertEqual((filt[2], filt[5]), (2, 1))           # One-way, Economy
        self.assertEqual(filt[13][0][3], 1)                     # nonstop
        self.assertEqual(filt[13][0][0], [[["HAM", 0]]])
        rt = decode(gc.request_body("FRA", "BJS", date(2026, 12, 1), date(2027, 2, 28),
                                    "business", 12, 1))
        self.assertEqual(rt[4], [12, 12])
        self.assertEqual((rt[1][2], rt[1][5]), (1, 3))
        legs = rt[1][13]
        self.assertEqual(legs[0][1], [[["PEK", 0], ["PKX", 0]]])    # Stadtcode -> Flughäfen
        self.assertEqual(legs[1][0], [[["PEK", 0], ["PKX", 0]]])
        self.assertEqual(legs[1][6], "2026-12-13")
        self.assertEqual(legs[0][3], 2)                         # höchstens 1 Umstieg

    def test_parse_real_answers(self):
        ow = gc.parse(fixture("ham_zrh_oneway"))
        self.assertEqual(len(ow), 62)
        self.assertEqual((ow[date(2026, 12, 1)], ow[date(2027, 1, 12)]), (110.0, 60.0))
        rt = gc.parse(fixture("fra_pek_rt"), 12)
        self.assertEqual(len(rt), 90)
        self.assertEqual(rt[date(2027, 1, 10)], 710.0)
        self.assertEqual(gc.parse(fixture("fra_pek_rt"), 11), {})    # andere Dauer: nichts
        self.assertEqual(gc.parse(fixture("fra_pek_rt")), {})        # kein One-way
        self.assertEqual(gc.parse(fixture("empty")), {})

    def test_errors(self):
        with self.assertRaisesRegex(gc.CalendarError, "Fehler 3"):
            gc.parse(fixture("error"))
        for bad in ("", "<html>captcha</html>", ")]}'\n[[\"wrb.fr\",null,\"{]\"]]"):
            with self.assertRaises(gc.CalendarError):
                gc.parse(bad)

    def test_windows(self):
        w = gc.windows(date(2026, 11, 10), date(2027, 5, 31))
        self.assertEqual(len(w), 2)
        self.assertEqual((w[0][1] - w[0][0]).days + 1, gc.MAX_DAYS)
        self.assertEqual(w[1], (w[0][1] + timedelta(days=1), date(2027, 5, 31)))
        self.assertEqual(gc.windows(date(2027, 1, 1), date(2027, 1, 1)),
                         [(date(2027, 1, 1), date(2027, 1, 1))])

    def test_candidates_generous(self):
        prices = {date(2027, 1, d): 100.0 + d for d in range(1, 31)}
        prices[date(2027, 1, 20)] = 40.0
        prices.update({date(2027, 2, d): 200.0 + d for d in range(1, 29)})
        got = gc.candidates(prices, 45, k=20)
        self.assertEqual(got[0], date(2027, 1, 20))             # unter Grenze × 1,3 zuerst
        # dann je Monat die günstigsten 20 % (Jan 6, Feb 6), Runde für Runde (Jan 20 schon dabei)
        self.assertEqual(got[1:3], [date(2027, 2, 1), date(2027, 1, 1)])
        self.assertEqual(len(got), 12)
        self.assertEqual(len(gc.candidates(prices, 45, k=3)), 3)
        hot = gc.candidates({date(2027, 1, d): 50.0 for d in range(1, 11)}, 45, k=20)
        self.assertEqual(len(hot), 10)                          # alle bis 58,50 €
        self.assertEqual(gc.candidates({}, 45), [])


class PlanningTest(unittest.TestCase):
    def test_oneway_confirms_cheapest_days_only(self):
        prices = flat(TODAY, 90, 90.0)
        cheap = [date(2026, 12, 9), date(2026, 12, 16), date(2027, 1, 13)]
        for d in cheap:
            prices[d] = 40.0
        src = source(prices)
        offers = src.fetch(ow_alert())
        self.assertEqual(src.calendar_calls, 1)                  # ein Abruf für alle Tage
        self.assertEqual(len(src.bodies), 1)
        span = src.bodies[0][2]
        self.assertEqual(span, [(TODAY + timedelta(days=2)).isoformat(), "2026-12-31"])
        confirmed = {o.departure for o in offers}
        self.assertTrue(set(cheap[:2]) <= confirmed)            # Kandidaten bestätigt
        self.assertNotIn(date(2027, 1, 13), confirmed)          # außerhalb months_ahead
        self.assertEqual(len(src.pages), gc.CONFIRM_PER_ROUTE)
        self.assertEqual(src.requests_made, 1 + len(src.pages))
        self.assertEqual(src.scans_made, src.requests_made)
        # gemeldet wird nur, was die normale Suche liefert (Preis der Ergebnisseite)
        self.assertEqual(min(o.price for o in offers), 126.0)
        hint = src.calendar_hints["ow"][0]
        self.assertEqual((hint["price"], hint["label"]), (40.0, "Kalenderpreis, unbestätigt"))
        self.assertTrue(any("Kalenderpreis, unbestätigt" in n for n in src.notes))
        self.assertIsNone(src.incomplete)

    def test_weekdays_filter_calendar_days(self):
        prices = flat(TODAY, 60, 40.0)
        src = source(prices)
        offers = src.fetch(ow_alert(depart_weekdays=[2, 3, 4]))
        self.assertTrue(offers)
        self.assertTrue(all(o.departure.weekday() in (2, 3, 4) for o in offers))
        self.assertTrue(all(h["departure"].weekday() in (2, 3, 4)
                            for h in src.calendar_hints["ow"]))

    def test_roundtrip_nights_groups_follow_return_weekdays(self):
        a = rt_alert(depart_weekdays=[2, 3, 4], return_weekdays=[5, 6, 0])
        src = source(flat(TODAY, 90, 80.0))
        calls = src.calendar_calls_for(a)
        nights = sorted(c[4] for c in calls)
        self.assertEqual(len(calls), 3)                          # Mi/Do/Fr: je eine Dauer
        offers = src.fetch(a)
        self.assertTrue(offers)
        for o in offers:
            self.assertIn(o.departure.weekday(), (2, 3, 4))
            self.assertIn(o.return_date.weekday(), (5, 6, 0))
            self.assertIn((o.return_date - o.departure).days, nights)
        plain = source().calendar_calls_for(rt_alert())
        self.assertEqual([c[4] for c in plain], [7])             # ohne Wochentage: 7 Nächte

    def test_stops_and_cabin_in_request(self):
        src = source(flat(TODAY, 30, 50.0))
        src.fetch(rt_alert(cabin="business", max_stops=0, max_price=None))
        filt = src.bodies[0][1]
        self.assertEqual((filt[5], filt[13][0][3]), (3, 1))

    def test_need_and_share(self):
        china = rt_alert(id="cn", origins=["FRA", "HAM", "MUC", "ZRH"],
                         destinations=["BJS", "SHA", "CAN", "SZX", "CTU", "HGH"],
                         max_price=499, months_ahead=6)
        src = source(flat(TODAY, 200, 700.0), max_requests=40, verify_reserve=10)
        self.assertEqual(src.need(china), 24 + 24 * gc.CONFIRM_PER_ROUTE)
        small = ow_alert()
        src.begin_run([small, china])
        share = src._shares["cn"]
        src.fetch(small)
        before = len(src.pages)
        src.fetch(china)
        cn_calls = src.calendar_calls - 1
        # nichts unter Grenze × 1,3: der Anteil geht in Kalender, eine Bestätigung bleibt
        self.assertEqual(cn_calls, share - 1)
        self.assertEqual(len(src.pages) - before, 1)
        self.assertLessEqual(src.scans_made, src.scan_budget)
        self.assertLessEqual(src.requests_made, src.max_requests - src.verify_reserve)
        self.assertIn("Limit", src.incomplete["message"])
        self.assertGreaterEqual(len(src.incomplete["routes"]), 24 - cn_calls)

    def test_hot_routes_reserve_confirmations(self):
        china = rt_alert(id="cn", origins=["FRA", "HAM", "MUC", "ZRH"],
                         destinations=["BJS", "SHA", "CAN", "SZX", "CTU", "HGH"],
                         max_price=499, months_ahead=6)
        src = source(flat(TODAY, 200, 600.0), max_requests=12)     # 600 <= 499 × 1,3
        src.fetch(china)
        calls = src.calendar_calls
        self.assertEqual(calls, 6)                               # je Kalender eine Bestätigung
        self.assertEqual(len(src.pages), 6)
        confirmed = {(u.split("from%20")[1][:3], u.split("to%20")[1][:3]) for u in src.pages}
        self.assertEqual(len(confirmed), 6)                      # erst je Strecke der beste Tag

    def test_no_budget_left_skips_routes(self):
        src = source(flat(TODAY, 60, 40.0), max_requests=1)
        offers = src.fetch(ow_alert())
        self.assertEqual((offers, src.requests_made), ([], 0))  # Kalender + 1 passt nicht
        self.assertIn("Limit", src.incomplete["message"])

    def test_rotation_oneway_uses_calendar_and_memory(self):
        a = rotation_alert = Alert(id="zrh-ham-oneway", name="OW", origins=["ZRH", "HAM"],
                                   destinations=["HAM", "ZRH"], trip="oneway", max_price=45,
                                   baseline_price=95, max_stops=0, months_ahead=4,
                                   rotation=True)
        src = source(flat(TODAY, 150, 40.0))
        offers = src.fetch(rotation_alert)
        self.assertEqual(src.calendar_calls, 2)                  # ein Abruf je Richtung
        self.assertTrue(offers)
        days = src.memory.get("ZRH", "HAM", "economy", TODAY, src._now())
        self.assertTrue(days)                                    # Bestätigtes im Gedächtnis
        self.assertFalse(source().calendar_active(Alert(**{**a.__dict__, "trip": "roundtrip"})))


class FallbackTest(unittest.TestCase):
    def test_error_falls_back_to_old_plan_for_the_run(self):
        src = source(cal=lambda body: fixture("error"))
        a = rt_alert()
        offers = src.fetch(a)
        self.assertEqual(src.calendar_calls, 1)
        self.assertIn("alte Planung", src._calendar_off)
        self.assertTrue(any("Google-Kalender nicht nutzbar" in n for n in src.notes))
        old = source(calendar=False).plan(a)
        self.assertEqual(len(src.pages), len(old))               # 2 Termine je Monat
        self.assertTrue(offers)
        self.assertEqual(src.requests_made, 1 + len(old))
        src.fetch(rt_alert(id="b"))                              # danach ohne Kalender
        self.assertEqual(src.calendar_calls, 1)
        src.reset()
        self.assertTrue(src.calendar_active(a))                  # nächster Lauf: wieder an

    def test_rotation_falls_back_to_rotation(self):
        a = Alert(id="zrh-ham-oneway", name="OW", origins=["ZRH", "HAM"],
                  destinations=["HAM", "ZRH"], trip="oneway", max_price=45, baseline_price=95,
                  max_stops=0, months_ahead=4, rotation=True)
        src = source(cal=lambda body: "kaputt")
        src.fetch(a)
        self.assertEqual(len(src.pages), rotation.PER_RUN)

    def test_blocked_stops_the_source(self):
        def blocked(body):
            raise GoogleBlocked("HTTP 429")
        src = source(cal=blocked)
        with self.assertRaises(SourceError):
            src.fetch(ow_alert())
        self.assertIn("429", src.down)
        self.assertEqual(src.pages, [])
        with self.assertRaises(SourceError):
            src.fetch(rt_alert())

    def test_network_errors_trip_breaker(self):
        from dealmonitor import http

        def down(body):
            raise http.HttpError("weg", network=True)
        src = source(cal=down)
        a = rt_alert(destinations=["HAM", "BER", "MUC", "FRA"])
        with self.assertRaises(SourceError):
            src.fetch(a)
        self.assertIn("nicht erreichbar", src.down)
        self.assertEqual(src.calendar_calls, GoogleFlightsSource.MAX_CONSECUTIVE_FAILURES)

    def test_test_fetcher_without_calendar_fetcher_keeps_old_plan(self):
        src = GoogleFlightsSource(delay=0, today=TODAY, fetcher=lambda u: RT_PAGE)
        self.assertFalse(src.calendar_active(rt_alert()))
        self.assertEqual(src.need(rt_alert()), len(src.plan(rt_alert())))


class SettingTest(unittest.TestCase):
    def test_google_calendar_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Context(tmp, Path(tmp) / "fehlt.toml", env={}, today=TODAY)
            for value, want in ((None, True), (False, False)):
                with ctx.store() as store:
                    app = ctx.settings(store)
                    if value is not None:
                        app.update({"google_calendar": value})
                    src = ctx.sources(ctx.build_config(store, app), store, ["google_flights"],
                                      app)
                self.assertIs(src[0].calendar, want)


if __name__ == "__main__":
    unittest.main()
