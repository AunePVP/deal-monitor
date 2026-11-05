"""Zusammenspiel der Quellen im Lauf: Google-Gegenprüfung, Radar, Quellen-Fabrik, Feed-Takt."""

import logging
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from unittest import mock

from dealmonitor import monitor, radar
from dealmonitor.appsettings import AppSettings, Context, order_sources
from dealmonitor.config import Config, Settings
from dealmonitor.models import Alert, Offer
from dealmonitor.monitor import (VERIFIED_PREFIX, best_across_sources, is_confirmed, run,
                                 run_alert, verify_deal)
from dealmonitor.principles import evaluate
from dealmonitor.radar import RadarSettings
from dealmonitor.scheduler import Scheduler, execute, feed_trigger
from dealmonitor.sources.amadeus import AmadeusDatesSource, AmadeusOffersSource
from dealmonitor.sources.feed import PRESETS_BY_ID, FeedSource
from dealmonitor.sources.googleflights import GoogleFlightsSource
from dealmonitor.sources.travelpayouts import TravelpayoutsSource
from dealmonitor.store import Store

TODAY = date(2026, 11, 4)


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def alert(**kw):
    base = dict(id="zrh-ham", name="Zürich → Hamburg", origins=["ZRH"], destinations=["HAM"],
                max_price=110, baseline_price=190, feed_keywords=[["Zürich", "Hamburg"]])
    base.update(kw)
    return Alert(**base)


def offer(price=89, source="travelpayouts", day=15, **kw):
    base = dict(source=source, origin="ZRH", destination="HAM", price=price, airline="LX",
                stops=0, departure=date(2027, 1, day), return_date=date(2027, 1, day + 4),
                link=f"https://example.org/{source}/{day}")
    base.update(kw)
    return Offer(**base)


def cfg(alerts):
    return Config(settings=Settings(min_history_samples=100), alerts=alerts, path=Path("x"))


class ListSource:
    records_history = True

    def __init__(self, name, offers):
        self.name, self.offers = name, offers

    def fetch(self, a):
        return [o for o in self.offers if (o.origin, o.destination) in a.pairs]


class FakeGoogle:
    """Verifier-Attrappe mit der Schnittstelle von ``GoogleFlightsSource.verify``."""

    name = "google_flights"
    records_history = True

    def __init__(self, price=None, error="Google Flights blockiert gerade", raise_=None):
        self.price, self.error, self.raise_ = price, error, raise_
        self.calls = []
        self.last_error = None

    def fetch(self, a):
        return []

    def verify(self, origin, dest, dep, ret, cabin="economy", roundtrip=True, **criteria):
        self.calls.append((origin, dest, dep, ret, cabin, roundtrip))
        self.criteria = criteria
        if self.raise_:
            raise self.raise_
        if self.price is None:
            self.last_error = self.error
            return None
        return {"price": self.price, "airline": "SWISS", "stops": 0, "link": "https://g/"}


def rss(*items) -> bytes:
    now = format_datetime(datetime.now().astimezone())
    body = "".join(
        f"<item><title>{t}</title><link>{link}</link><pubDate>{now}</pubDate>"
        + "".join(f"<category>{c}</category>" for c in cats) + "</item>"
        for t, link, cats in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'.encode()


class XmlFeed(FeedSource):
    """Deal-Seiten-Quelle (Preset Travel-Dealz) mit Artikeln aus dem Test statt aus dem Netz."""

    def __init__(self, items, fail=False):
        super().__init__(presets=["travel-dealz"], search_enabled=False)
        self._xml, self._fail = rss(*items), fail

    def _feed_jobs(self):
        p = PRESETS_BY_ID["travel-dealz"]
        return [(p.urls[0], p), (p.urls[1], p)]

    def _fetch_one(self, url):
        from dealmonitor import http
        if self._fail and url.endswith("paged=2"):
            raise http.HttpError("HTTP 500", 500)
        return self.parse(self._xml if not url.endswith("paged=2") else rss(), url)


HAMBURG = ("Swiss: Zürich – Hamburg ab 79€ hin und zurück", "https://travel-dealz.de/zrh-ham/",
           ["Flüge"])
ERROR_FARE = ("Error Fare: Lufthansa First Class nach Tokio ab 1.499€",
              "https://travel-dealz.de/error-fare/?utm_source=feed", ["First Class"])
BANGKOK = ("Qatar Airways: Business Class nach Bangkok ab 1.199€",
           "https://travel-dealz.de/bangkok/", ["Business Class"])
CRUISE = ("MSC Kreuzfahrt Mittelmeer ab 199€", "https://travel-dealz.de/kreuzfahrt/",
          ["Kreuzfahrten"])
AMSTERDAM = ("Bonaire ab 416€ von Amsterdam", "https://travel-dealz.de/bonaire/", ["Flüge"])


class RowGoogle(FakeGoogle):
    """Verifier mit echten Google-Ergebniszeilen (Fixture ZRH-HAM): die Regeln des Alarms
    werden wie in ``GoogleFlightsSource.verify`` angewendet."""

    def __init__(self, page=None):
        super().__init__()
        from dealmonitor.sources.googleflights import GoogleFlightsSource
        html = page or (Path(__file__).parent / "fixtures" / "google_flights_zrh_ham.html").read_text()
        self.src = GoogleFlightsSource(max_requests=10, delay=0, fetcher=lambda url: html)

    def verify(self, *args, **criteria):
        self.calls.append(args)
        return self.src.verify(*args, **criteria)


class VerifyTest(unittest.TestCase):
    def deal(self, price=89, **kw):
        return evaluate(alert(**kw), offer(price), [], today=TODAY)

    def test_confirmed_takes_google_price_and_flight(self):
        d = self.deal()
        g = FakeGoogle(price=92)
        self.assertEqual(verify_deal(d, g), "confirmed")
        self.assertEqual(d.offer.price, 92)            # buchbarer Preis
        self.assertIn(f"{VERIFIED_PREFIX}: 92 € (SWISS, direkt)", d.reasons)
        self.assertEqual(g.calls, [("ZRH", "HAM", date(2027, 1, 15), date(2027, 1, 19),
                                    "economy", True)])
        self.assertEqual(g.criteria, {"max_stops": 1, "max_minutes": None,
                                      "exclude_airlines": []})
        self.assertTrue(is_confirmed(d))

    def test_cheaper_google_price_adopted(self):
        d = self.deal()
        fp = d.fingerprint
        self.assertEqual(verify_deal(d, FakeGoogle(price=79)), "confirmed")
        self.assertEqual(d.offer.price, 79)
        self.assertIn("79 €", d.headline)
        self.assertAlmostEqual(d.discount, 1 - 79 / 190)
        self.assertIn("58% unter Referenz 190 € (Normalpreis laut Konfiguration)", d.reasons)
        self.assertEqual(d.fingerprint, fp, "bleibt derselbe Deal, auch mit Google-Airline")

    def test_tolerance_and_rejection(self):
        self.assertEqual(verify_deal(self.deal(100, max_price=200), FakeGoogle(price=125)),
                         "confirmed")
        self.assertEqual(verify_deal(self.deal(100, max_price=200), FakeGoogle(price=126)),
                         "rejected")

    def test_google_price_above_max_price_rejected(self):
        # Travelpayouts 95 €, Google 115 € (≤ +25 %), Grenze 110 € -> nicht buchbar unter Grenze
        d = self.deal(95)
        self.assertEqual(verify_deal(d, FakeGoogle(price=115)), "rejected")
        self.assertEqual(d.offer.price, 95, "verworfener Deal bleibt unverändert")
        # ohne max_price: Rabatt gegen den Google-Preis
        a = Alert(id="x", name="x", origins=["ZRH"], destinations=["HAM"], baseline_price=190,
                  min_discount=0.4)
        d = evaluate(a, offer(100), [], today=TODAY)
        self.assertEqual(verify_deal(d, FakeGoogle(price=120)), "rejected")   # nur 37 %
        d = evaluate(a, offer(100), [], today=TODAY)
        self.assertEqual(verify_deal(d, FakeGoogle(price=110)), "confirmed")  # 42 %
        self.assertEqual(d.offer.price, 110)

    def test_google_rows_must_fit_alert_rules(self):
        # Fixture: 168 € KLM 1 Umstieg 3:35 h; 183 € SWISS nonstop 1:30 h
        d = self.deal(190, max_price=200, max_stops=0, max_duration_hours=2,
                      exclude_airlines=["KL"])
        self.assertEqual(verify_deal(d, RowGoogle()), "confirmed")
        self.assertEqual((d.offer.price, d.offer.airline, d.offer.stops, d.offer.duration_out_min),
                         (183.0, "SWISS", 0, 90))
        self.assertTrue(d.headline.startswith("Zürich nach Hamburg ab 183 € hin und zurück "
                                              "(SWISS, direkt"), d.headline)
        # kein Google-Flug passt (alles über 1 h) -> veralteter Nonstop-Preis verworfen
        d = self.deal(190, max_price=200, max_stops=0, max_duration_hours=1)
        self.assertEqual(verify_deal(d, RowGoogle()), "rejected")
        # ohne Einschränkung: billigster Flug, Angaben aus derselben Zeile
        d = self.deal(190, max_price=200)
        self.assertEqual(verify_deal(d, RowGoogle()), "confirmed")
        self.assertEqual((d.offer.price, d.offer.airline, d.offer.stops), (168.0, "KLM", 1))
        self.assertIn("1 Umstieg", d.headline)

    def test_unverified_when_google_unavailable(self):
        d = self.deal()
        self.assertEqual(verify_deal(d, FakeGoogle(price=None)), "unverified")
        self.assertEqual(d.reasons[-1], "nicht gegengeprüft (Google Flights blockiert gerade)")
        self.assertFalse(is_confirmed(d))
        d = self.deal()
        self.assertEqual(verify_deal(d, FakeGoogle(raise_=RuntimeError("kaputt"))), "unverified")
        self.assertIn("kaputt", d.reasons[-1])
        # Artikel ohne Daten werden nicht gegengeprüft
        d = evaluate(alert(), offer(79, source="feed:travel-dealz", departure=None,
                                    return_date=None, title="x"), [], today=TODAY)
        g = FakeGoogle(price=1)
        self.assertEqual(verify_deal(d, g), "unverified")
        self.assertEqual(g.calls, [])


class SlotGoogle(FakeGoogle):
    """Google-Preis je Abflugtag."""

    def __init__(self, by_day):
        super().__init__()
        self.by_day = by_day

    def verify(self, origin, dest, dep, ret, cabin="economy", roundtrip=True, **criteria):
        self.calls.append((origin, dest, dep, ret, cabin, roundtrip))
        price = self.by_day.get(dep.day)
        if price is None:
            self.last_error = "Google Flights blockiert gerade"
            return None
        return {"price": price, "airline": "SWISS", "stops": 0, "link": "https://g/"}


class RunAlertVerifyTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_rejected_deal_not_stored_and_expires(self):
        a = alert()
        c = cfg([a])
        tp = ListSource("travelpayouts", [offer(89)])
        items, r = run_alert(a, [tp], self.store, c, today=TODAY, verifier=FakeGoogle(price=92))
        self.assertEqual((len(items), r.rejected), (1, 0))
        self.assertIn(f"{VERIFIED_PREFIX}: 92 € (SWISS, direkt)",
                      self.store.deals(status="active")[0]["reasons"])
        # gleicher Lauf nochmal: derselbe Deal (Fingerprint trotz Google-Airline), nichts läuft ab
        items, r = run_alert(a, [tp], self.store, c, today=TODAY, verifier=FakeGoogle(price=92))
        self.assertEqual((items, r.expired, len(self.store.deals(status="active"))), ([], 0, 1))
        # nächster Lauf: Google zeigt 200 € -> verworfen, gespeicherter Deal läuft ab
        items, r = run_alert(a, [tp], self.store, c, today=TODAY, verifier=FakeGoogle(price=200))
        self.assertEqual((items, r.rejected, r.deals, r.expired), ([], 1, 0, 1))
        self.assertEqual(self.store.deals(status="active"), [])

    def test_next_cheapest_date_checked_when_cheapest_rejected(self):
        # Travelpayouts: 79 € am 15.01. (Google 160 €, veraltet) und 99 € am 22.01. (Google 101 €)
        a = alert()
        tp = ListSource("travelpayouts", [offer(79, day=15), offer(99, day=22)])
        g = SlotGoogle({15: 160, 22: 101})
        items, r = run_alert(a, [tp], self.store, cfg([a]), today=TODAY, verifier=g)
        self.assertEqual([(d.offer.departure.day, d.offer.price, st) for d, st in items],
                         [(22, 101, "new")])
        self.assertEqual((r.rejected, r.deals), (1, 1))
        self.assertEqual([c[2].day for c in g.calls], [15, 22])
        # höchstens 3 Versuche je Strecke/Monat
        tp = ListSource("travelpayouts", [offer(70 + i, day=10 + i) for i in range(5)])
        g = SlotGoogle({10 + i: 500 for i in range(5)})
        items, r = run_alert(a, [tp], self.store, cfg([a]), today=TODAY, verifier=g)
        self.assertEqual((items, r.rejected, len(g.calls)), ([], 3, 3))

    def test_dropped_deals_are_not_marked_notified(self):
        # Lauf 1: Travelpayouts 89 € (Google nicht erreichbar -> unbestätigt), Google-Livepreis
        # 100 € gewinnt als bestätigter Deal; der Travelpayouts-Deal ist aussortiert
        a = alert()
        tp = ListSource("travelpayouts", [offer(89)])
        live = ListSource("google_flights", [offer(100, source="google_flights", day=22)])
        items, _ = run_alert(a, [tp, live], self.store, cfg([a]), today=TODAY,
                             verifier=FakeGoogle(price=None))
        self.assertEqual([d.offer.source for d, _ in items], ["google_flights"])
        rows = {d["source"]: d for d in self.store.deals(status="active")}
        self.assertIsNone(rows["travelpayouts"]["notified_price"], "nicht als gemeldet vermerkt")
        # Lauf 2: Google bestätigt 90 € -> jetzt wird er gemeldet (als NEU)
        items, _ = run_alert(a, [tp, live], self.store, cfg([a]), today=TODAY,
                             verifier=FakeGoogle(price=90))
        self.assertEqual([(d.offer.source, d.offer.price, st) for d, st in items],
                         [("travelpayouts", 90, "new")])

    def test_max_deals_cut_does_not_swallow_deals(self):
        a = alert()
        c = cfg([a])
        c.settings.max_deals_per_alert = 1
        tp = ListSource("travelpayouts", [offer(80, airline="LX"), offer(90, airline="EW")])
        g = SlotGoogle({15: 80})
        tp.offers[1].departure = date(2027, 2, 15)
        tp.offers[1].return_date = date(2027, 2, 19)
        items, _ = run_alert(a, [tp], self.store, c, today=TODAY, verifier=g)
        self.assertEqual([d.offer.price for d, _ in items], [80])
        # nächster Lauf: der zweite Deal (Februar) wird nachgemeldet, der erste ist bekannt
        items, _ = run_alert(a, [tp], self.store, c, today=TODAY, verifier=g)
        self.assertEqual([(d.offer.departure.month, st) for d, st in items], [(2, "new")])
        items, _ = run_alert(a, [tp], self.store, c, today=TODAY, verifier=g)
        self.assertEqual(items, [])

    def test_only_cached_sources_are_verified(self):
        a = alert()
        g = FakeGoogle(price=500)
        live = ListSource("google_flights", [offer(95, source="google_flights", day=16)])
        items, r = run_alert(a, [live], self.store, cfg([a]), today=TODAY, verifier=g)
        self.assertEqual((len(items), r.rejected, g.calls), (1, 0, []))
        # die Google-Quelle prüft sich nicht selbst gegen
        g2 = FakeGoogle(price=500)
        g2.offers = [offer(95, source="google_flights")]
        g2.fetch = lambda al: list(g2.offers)
        items, r = run_alert(a, [g2], self.store, cfg([a]), today=TODAY, verifier=g2)
        self.assertEqual((r.rejected, g2.calls), (0, []))

    def test_confirmed_preferred_across_sources(self):
        a = alert()
        tp = ListSource("travelpayouts", [offer(80)])
        live = ListSource("google_flights", [offer(95, source="google_flights", day=22)])
        # Google nicht erreichbar -> Travelpayouts-Deal unbestätigt -> Google-Livepreis gewinnt
        items, _ = run_alert(a, [tp, live], self.store, cfg([a]), today=TODAY,
                             verifier=FakeGoogle(price=None))
        self.assertEqual([d.offer.source for d, _ in items], ["google_flights"])
        self.assertEqual(len(self.store.deals(status="active")), 2)   # beide gespeichert

    def test_best_across_sources_keeps_articles(self):
        a = alert()
        mk = lambda o: evaluate(a, o, [], today=TODAY)  # noqa: E731
        items = [mk(offer(80)), mk(offer(90, source="google_flights")),
                 mk(offer(70, source="feed:travel-dealz", departure=None, return_date=None,
                          title="A", origin="", destination="")),
                 mk(offer(75, source="feed:mydealz-fluege", departure=None, return_date=None,
                          title="B", origin="", destination=""))]
        out = best_across_sources(items)
        self.assertEqual(sorted(d.offer.price for d in out), [70, 75, 90])


class RadarRunTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.sent = []

        class Rec:
            name = "rec"

            def send(inner, name, items):
                self.sent.append((name, [d.offer.title or d.headline for d, _ in items]))

        self.notifier = Rec()

    def tearDown(self):
        self.store.close()

    def radar_titles(self):
        return sorted(d["headline"] for d in self.store.deals(status="active", alert_id="radar"))

    def test_radar_after_alerts_without_duplicates(self):
        feed = XmlFeed([HAMBURG, ERROR_FARE, BANGKOK, CRUISE, AMSTERDAM])
        r = run(cfg([alert()]), [feed], [self.notifier], self.store, today=TODAY,
                radar_settings=RadarSettings())
        self.assertEqual((r.deals, r.radar, r.errors), (1, 2, []))
        # Hamburg-Artikel gehört zum Alarm -> nicht noch einmal im Radar
        self.assertEqual(self.radar_titles(), sorted([ERROR_FARE[0], BANGKOK[0]]))
        self.assertEqual([n for n, _ in self.sent], ["Zürich → Hamburg", radar.RADAR_ALERT_NAME])
        self.assertEqual(self.sent[1][1][0], ERROR_FARE[0])        # Error Fare zuerst
        rows = self.store.deals(alert_id="radar")
        self.assertTrue(all(d["rating"] == radar.RATING for d in rows))
        self.assertTrue(all(d["source"] == "feed:travel-dealz" for d in rows))

    def test_keyword_match_without_deal_still_in_radar(self):
        # passt zu den Stichwörtern, ist aber über der Preisgrenze des Alarms -> kein Alarm-Deal,
        # darf deshalb nicht aus dem Radar verschwinden
        feed = XmlFeed([HAMBURG, ERROR_FARE])
        r = run(cfg([alert(max_price=50)]), [feed], [], self.store, today=TODAY,
                radar_settings=RadarSettings())
        self.assertEqual((r.deals, r.radar), (0, 2))
        self.assertIn(HAMBURG[0], self.radar_titles())

    def test_all_radar_hits_notified(self):
        many = [(f"Business Class nach Ziel {i} ab {900 + i}€", f"https://travel-dealz.de/z{i}/",
                 ["Business Class"]) for i in range(8)]
        c = cfg([])
        c.settings.max_deals_per_alert = 2
        r = run(c, [XmlFeed(many)], [self.notifier], self.store, today=TODAY,
                radar_settings=RadarSettings())
        self.assertEqual((r.radar, r.notified, len(self.sent[0][1])), (8, 8, 8))

    def test_without_alert_article_goes_to_radar(self):
        feed = XmlFeed([HAMBURG, CRUISE, AMSTERDAM])
        r = run(cfg([]), [feed], [self.notifier], self.store, today=TODAY,
                radar_settings=RadarSettings())
        self.assertEqual(r.radar, 1)
        self.assertEqual(self.radar_titles(), [HAMBURG[0]])

    def test_radar_settings_and_single_alert_runs(self):
        feed = XmlFeed([ERROR_FARE, BANGKOK])
        r = run(cfg([alert()]), [feed], [], self.store, today=TODAY,
                radar_settings=RadarSettings(enabled=False))
        self.assertEqual(r.radar, 0)
        r = run(cfg([alert()]), [XmlFeed([ERROR_FARE, BANGKOK])], [], self.store, today=TODAY,
                radar_settings=RadarSettings(business_max=1000))
        self.assertEqual(self.radar_titles(), [ERROR_FARE[0]])
        # "Nur diesen Alarm prüfen" -> kein Radar
        r = run(cfg([alert()]), [XmlFeed([BANGKOK])], [], self.store, today=TODAY,
                only=["zrh-ham"], radar_settings=RadarSettings())
        self.assertEqual(r.radar, 0)
        # ohne Deal-Seiten-Quelle kein Radar, kein Fehler
        r = run(cfg([alert()]), [ListSource("travelpayouts", [])], [], self.store, today=TODAY,
                radar_settings=RadarSettings())
        self.assertEqual((r.radar, r.errors), (0, []))

    def test_radar_deals_expire_but_not_on_partial_failure(self):
        run(cfg([]), [XmlFeed([ERROR_FARE, BANGKOK])], [], self.store, today=TODAY,
            radar_settings=RadarSettings())
        self.assertEqual(len(self.radar_titles()), 2)
        # ein Feed fällt aus -> nichts läuft ab, Hinweis im Lauf
        r = run(cfg([]), [XmlFeed([BANGKOK], fail=True)], [], self.store, today=TODAY,
                radar_settings=RadarSettings())
        self.assertEqual(len(self.radar_titles()), 2)
        self.assertTrue(any(e.startswith("[radar]") and "nicht lesbar" in e for e in r.errors))
        # alle Feeds gelesen, Error Fare weg -> läuft ab
        r = run(cfg([]), [XmlFeed([BANGKOK])], [], self.store, today=TODAY,
                radar_settings=RadarSettings())
        self.assertEqual((r.expired, self.radar_titles()), (1, [BANGKOK[0]]))

    def test_total_feed_failure_reported_once_per_message(self):
        class Dead(XmlFeed):
            def _fetch_one(self, url):
                from dealmonitor import http
                raise http.HttpError("HTTP 503", 503)

        r = run(cfg([alert()]), [Dead([])], [], self.store, today=TODAY,
                radar_settings=RadarSettings())
        self.assertEqual(r.radar, 0)
        self.assertTrue(any(e.startswith("[zrh-ham]") for e in r.errors))
        self.assertTrue(any(e.startswith("[radar]") for e in r.errors))


class ContextSourcesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"TRAVELPAYOUTS_TOKEN": "test-dummy", "AMADEUS_API_KEY": "test-dummy-key",
                    "AMADEUS_API_SECRET": "test-dummy-secret"}
        self.ctx = Context(self.tmp.name, Path(self.tmp.name) / "fehlt.toml", env=self.env,
                           today=TODAY)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, names=None, **values):
        with self.ctx.store() as store:
            app = self.ctx.settings(store)
            if values:
                app.update(values)
            c = self.ctx.build_config(store, app)
            return self.ctx.sources(c, store, names, app), app

    def test_default_sources(self):
        src, app = self.build()
        self.assertEqual(app.get("sources"), ["travelpayouts", "google_flights", "feeds"])
        self.assertEqual([type(s) for s in src],
                         [TravelpayoutsSource, GoogleFlightsSource, FeedSource])
        g = src[1]
        self.assertEqual((g.max_requests, g.delay, g.scan_months, g.today), (40, 3.0, 6, TODAY))
        self.assertIs(self.ctx.verifier(src, app), g)
        self.assertEqual((g.verify_reserve, g.scan_budget), (10, 30))   # Rest für Gegenprüfung

    def test_all_sources_order_alias_and_settings(self):
        src, app = self.build(["feed", "google_flights", "amadeus", "travelpayouts"],
                              google_max_requests=12, amadeus_env="production",
                              amadeus_max_requests=7, feed_presets=["fly4free"],
                              feeds=["https://example.org/feed/"], feed_max_age_days=3)
        self.assertEqual([s.name for s in src], ["travelpayouts", "amadeus-dates",
                                                 "amadeus-offers", "google_flights", "feeds"])
        self.assertIsInstance(src[1], AmadeusDatesSource)
        self.assertIsInstance(src[2], AmadeusOffersSource)
        self.assertIs(src[1].client, src[2].client)            # gemeinsames Budget + Token
        self.assertEqual((src[1].client.env, src[1].client.max_requests), ("production", 7))
        self.assertEqual(src[3].max_requests, 12)
        feed = src[4]
        self.assertEqual([p.id for p in feed.presets], ["fly4free"])
        self.assertEqual(feed.extra_feeds, ["https://example.org/feed/"])
        self.assertFalse(feed.search_enabled)
        self.assertEqual(feed.max_age_days, 3)
        self.assertEqual(feed.offer_sources, ["feed:fly4free", "feed:custom"])

    def test_no_feeds_without_presets_and_verify_off(self):
        src, app = self.build(feed_presets=[], feeds=[], google_verify=False)
        self.assertEqual([s.name for s in src], ["travelpayouts", "google_flights"])
        self.assertIsNone(self.ctx.verifier(src, app))
        self.assertEqual(src[1].verify_reserve, 0)       # ohne Gegenprüfung kein Vorbehalt
        with self.assertRaises(ValueError):
            order_sources(["kayak"])
        self.assertEqual(order_sources(["feed", "feeds", "travelpayouts"]),
                         ["travelpayouts", "feeds"])

    def test_configured(self):
        with self.ctx.store() as store:
            app = AppSettings(store, self.env)
            c = app.configured()
            self.assertTrue(c["amadeus"] and c["feeds"] and c["feed"] and c["radar"])
            app.update({"feed_presets": [], "radar_enabled": False})
            c = app.configured()
            self.assertFalse(c["feeds"] or c["radar"])
            self.assertFalse(AppSettings(store, {}).configured()["amadeus"])
            self.assertTrue(AppSettings(store, {}).configured(demo=True)["travelpayouts"])


class NumberFormatTest(unittest.TestCase):
    def test_german_numbers(self):
        from dealmonitor.appsettings import coerce
        cases = [("radar_business_max", "1.300", 1300.0), ("radar_business_max", "1.299,50", 1299.5),
                 ("radar_economy_max", "12.500", 12500.0), ("interval_hours", "1,5", 1.5),
                 ("interval_hours", "1.5", 1.5), ("request_delay", "0.35", 0.35),
                 ("renotify_drop", "0.050", 0.05), ("google_max_requests", "40", 40)]
        for name, raw, want in cases:
            self.assertEqual(coerce(name, raw), want, (name, raw))
        with self.assertRaises(ValueError):
            coerce("radar_first_max", "1.2.3")
        with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp) / "x.sqlite") as store:
            app = AppSettings(store, {"RADAR_BUSINESS_MAX": "1.300"})
            self.assertEqual(app.radar_settings().business_max, 1300.0)


class FeedScheduleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.names = []

        def factory(c, names):
            self.names.append(names)
            return [ListSource("travelpayouts", [])]

        self.ctx = Context(self.tmp.name, Path(self.tmp.name) / "fehlt.toml", env={},
                           source_factory=factory, today=TODAY)

    def tearDown(self):
        self.tmp.cleanup()

    def test_feed_mode_and_triggers(self):
        row = execute(self.ctx, trigger="manual", mode="feeds")
        self.assertEqual((row["trigger"], row["errors"]), ("manual-feeds", []))
        self.assertEqual(self.names[-1], ["feeds"])
        execute(self.ctx, trigger="schedule", mode="feeds")
        self.assertEqual(self.names[-1], ["feeds"])
        execute(self.ctx, trigger="schedule")
        self.assertEqual(self.names[-1], ["travelpayouts", "google_flights", "feeds"])
        self.assertEqual([feed_trigger(t) for t in ("schedule", "manual", "feeds", "cli")],
                         ["feeds", "manual-feeds", "feeds", "manual-feeds"])
        with self.assertRaises(ValueError):
            execute(self.ctx, mode="kaputt")
        # Feeds nicht aktiv -> Feed-Lauf meldet das, fragt nichts ab
        with self.ctx.store() as store:
            AppSettings(store, {}).update({"sources": ["travelpayouts"]})
        n = len(self.names)
        row = execute(self.ctx, mode="feeds")
        self.assertEqual(len(self.names), n)
        self.assertIn("nicht als Quelle aktiviert", row["errors"][0])

    def test_next_runs(self):
        s = Scheduler(self.ctx)
        self.assertLessEqual(s.next_feed_run(), datetime.now())       # nie gelaufen -> sofort
        execute(self.ctx, trigger="schedule")
        with self.ctx.store() as store:
            full = datetime.fromisoformat(store.last_run()["started_at"])
        self.assertEqual(s.next_run(), full + timedelta(hours=3))
        self.assertEqual(s.next_feed_run(), full + timedelta(minutes=30))
        st = s.status()
        self.assertEqual((st["next_feed_run"], st["feed_interval_minutes"]),
                         ((full + timedelta(minutes=30)).isoformat(), 30))
        # Feed-Lauf verschiebt nur den Feed-Takt, nicht den vollen Lauf
        with Store(self.ctx.db_path) as store:
            rid = store.start_run("feeds")
            store.finish_run(rid)
            store.db.execute("UPDATE runs SET started_at=? WHERE id=?",
                             ((full + timedelta(minutes=31)).isoformat(timespec="seconds"), rid))
            store.db.commit()
            AppSettings(store, {}).update({"feed_interval_minutes": 15})
        self.assertEqual(s.next_run(), full + timedelta(hours=3))
        self.assertEqual(s.next_feed_run(), full + timedelta(minutes=46))
        # voller Lauf früher fällig als der Feed-Lauf -> kein eigener Feed-Termin
        with self.ctx.store() as store:
            AppSettings(store, {}).update({"interval_hours": 0.5})
        self.assertIsNone(s.status()["next_feed_run"])
        # Feeds aus -> kein Feed-Takt
        with self.ctx.store() as store:
            AppSettings(store, {}).update({"sources": ["travelpayouts"], "interval_hours": 3})
        self.assertIsNone(s.next_feed_run())
        self.assertIsNone(Scheduler(self.ctx, enabled=False).next_feed_run())

    def test_loop_runs_feed_check_when_due(self):
        execute(self.ctx, trigger="schedule")     # voller Lauf gerade erst -> nicht fällig
        with Store(self.ctx.db_path) as store:
            store.db.execute("UPDATE runs SET started_at=?",
                             ((datetime.now() - timedelta(minutes=40)).isoformat(
                                 timespec="seconds"),))
            store.db.commit()
        s = Scheduler(self.ctx)
        with mock.patch.object(Scheduler, "_locked_run",
                               side_effect=lambda *a: (s._lock.release(), s._stop.set())) as m:
            s._loop()
        self.assertEqual(m.call_args[0], (None, "schedule", "feeds"))


class MigrationTest(unittest.TestCase):
    def test_old_runs_table_gets_new_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alt.sqlite"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, trigger TEXT "
                       "NOT NULL, alert_id TEXT, started_at TEXT NOT NULL, finished_at TEXT, "
                       "offers INTEGER NOT NULL DEFAULT 0, deals INTEGER NOT NULL DEFAULT 0, "
                       "notified INTEGER NOT NULL DEFAULT 0, expired INTEGER NOT NULL DEFAULT 0, "
                       "errors TEXT NOT NULL DEFAULT '[]')")
            db.execute("INSERT INTO runs (trigger, started_at) VALUES ('manual', '2026-01-01')")
            db.commit()
            db.close()
            with Store(path) as store:
                rid = store.start_run("feeds")
                store.finish_run(rid, radar=3, rejected=2)
                runs = store.runs(5)
            self.assertEqual((runs[0]["radar"], runs[0]["rejected"]), (3, 2))
            self.assertEqual((runs[1]["radar"], runs[1]["rejected"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
