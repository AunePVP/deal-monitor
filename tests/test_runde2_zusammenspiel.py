"""Runde 2, Zusammenspiel von Paket 7 (Google-Datumsrotation) und Paket 8 (Bewertung,
Erstmeldung über alle Quellen) in einem kompletten ``run_alert`` mit Fake-Quellen - ohne Netz:

* Rotations-Treffer werden mit Googles Preisniveau bewertet (Bezugspreis aus dem Verlauf),
  Beinahe-Deals landen in den Bestpreisen.
* Derselbe Artikel-Deal von Secret Flying und Travel-Dealz-REST wird nur einmal gemeldet.
* Preise aus dem Rotations-Gedächtnis zählen nicht jeden Tag neu im Preisverlauf.
* Das Radar legt Artikel mit verschiedenem Abflug nicht zusammen.
"""

import logging
import re
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from dealmonitor import config as config_mod
from dealmonitor import principles, radar
from dealmonitor.models import Alert, Offer
from dealmonitor.monitor import _seen_today, run_alert
from dealmonitor.sources import google_insights
from dealmonitor.sources import secretflying as sf
from dealmonitor.sources import traveldealz_rest as tdr
from dealmonitor.sources.feed import FeedSource
from dealmonitor.sources.googleflights import GoogleFlightsSource
from dealmonitor.store import Store
from dealmonitor.web import alert_json

TODAY = date(2026, 11, 4)
NOW = datetime(2026, 11, 4, 12, 0)
Q_RE = re.compile(r"Flights from (\w{3}) to (\w{3}) on (\d{4}-\d\d-\d\d)")
#: Preisniveau der Google-Seite ZRH-HAM one-way (Messung: 82 / 105 / 90–150 €)
INSIGHT = {"level_code": 1, "level": "niedrig", "lowest": 82, "typical": 105, "saving": 23,
           "typical_low": 90, "typical_high": 150,
           "history": [[f"2026-10-{d:02d}", p] for d, p in
                       zip(range(1, 11), (100, 110, 95, 105, 98, 112, 104, 99, 108, 101))]}
GOOGLE_MEDIAN = 102.5


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def page(price: int, airline: str = "Eurowings") -> str:
    return ('<html><body><ul><li><div aria-label="Ab {p} Euro. Nonstop-Flug mit {a}. Abflug von '
            'Flughafen Zürich am Donnerstag, Januar 14, 07:00. Gesamtdauer: 1 Std. 25 Min.">'
            '</div></li></ul></body></html>').format(p=price, a=airline)


def oneway_alert() -> Alert:
    return Alert(id="zrh-ham-oneway", name="Zürich ⇄ Hamburg one-way", origins=["ZRH", "HAM"],
                 destinations=["HAM", "ZRH"], trip="oneway", max_price=45, baseline_price=95,
                 max_stops=0, months_ahead=4, rotation=True,
                 feed_keywords=[["Zürich", "Hamburg"]])


def article(title: str, link: str, price: float, published: datetime, uid: str) -> dict:
    return {"title": title, "link": link, "published": published, "price_eur": price,
            "origin_codes": ["ZRH"], "dest_codes": ["HAM"], "oneway": True, "uid": uid,
            "categories": ["Flüge"], "description": ""}


SF_ITEM = article("Non-stop from Zurich, Switzerland to Hamburg, Germany for only €29 one-way",
                  "https://www.secretflying.com/posts/zurich-hamburg-29/", 29,
                  datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc), "at://sf/1")
TD_ITEM = article("Zürich nach Hamburg one-way ab 29 €",
                  "https://travel-dealz.de/deal/zuerich-hamburg-29/", 29,
                  datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc), "td:1")


class RotationScoringRunTest(unittest.TestCase):
    """Ein Lauf mit Deal-Seiten (Secret Flying + Travel-Dealz-REST) und Google-Rotation."""

    def setUp(self):
        self.store = Store(":memory:")
        self.cfg = config_mod.Config(settings=config_mod.Settings(), alerts=[], path=Path("x"))
        self.alert = oneway_alert()
        self.calls: list[tuple[str, str, str]] = []
        probe = self.google()
        plan = probe.plan(self.alert)
        # aus dem Stück dieses Laufs: ein billiger Tag ZRH→HAM, HAM→ZRH knapp über der Grenze
        self.cheap = next(d for o, _, d, _ in plan if o == "ZRH")
        self.prices = {("ZRH", "HAM"): 120, ("HAM", "ZRH"): 70}

    def tearDown(self):
        self.store.close()

    def fetch(self, url: str) -> str:
        o, d, day = Q_RE.search(unquote(url)).groups()
        self.calls.append((o, d, day))
        if (o, d) == ("ZRH", "HAM") and day == self.cheap.isoformat():
            return page(39)
        return page(self.prices[(o, d)])

    def google(self, now: datetime = NOW) -> GoogleFlightsSource:
        return GoogleFlightsSource(max_requests=40, delay=0, today=TODAY, now=now, seed=1,
                                   fetcher=self.fetch, state_store=self.store)

    def feeds(self) -> FeedSource:
        return FeedSource(presets=["secretflying", "travel-dealz-rest"], max_age_days=36500,
                          search_enabled=False)

    def run_once(self, now: datetime = NOW, items=(SF_ITEM, TD_ITEM)):
        google = self.google(now)
        google.begin_run([self.alert])
        sf_items = [it for it in items if "secretflying" in it["link"]]
        td_items = [it for it in items if "travel-dealz" in it["link"]]
        with mock.patch.object(sf, "fetch_items", return_value=sf_items), \
                mock.patch.object(tdr, "fetch_items", return_value=td_items), \
                mock.patch.object(google_insights, "parse", return_value=dict(INSIGHT)):
            return run_alert(self.alert, [self.feeds(), google], self.store, self.cfg,
                             today=TODAY)

    def test_full_run(self):
        sent, res = self.run_once()
        self.assertEqual(res.errors, [])

        # 1) Rotations-Treffer: bewertet mit Googles Preisverlauf als Bezugspreis
        google = [d for d, _ in sent if d.offer.source == "google_flights"]
        self.assertEqual([(d.offer.price, d.offer.departure) for d in google],
                         [(39, self.cheap)])
        deal = google[0]
        self.assertEqual(deal.reference_source, principles.REF_GOOGLE_HISTORY)
        self.assertAlmostEqual(deal.reference_price, GOOGLE_MEDIAN)
        self.assertIn("57% unter typischer Spanne (90 €–150 €)", deal.reasons)
        self.assertFalse(principles.suspected_error_fare(deal.reasons))   # 39 € > 40 % von 90 €
        self.assertEqual(deal.offer.insight["typical_low"], 90)

        # 2) Artikel: Secret Flying zuerst, Travel-Dealz ergänzt nur die Erstmeldung
        articles = [d for d, _ in sent if d.offer.title]
        self.assertEqual([d.offer.source for d in articles], ["feed:secretflying"])
        self.assertIn("Erstmeldung Secret Flying, 6 h vor Travel-Dealz", articles[0].reasons)
        rows = {r["source"]: r for r in self.store.deals()}
        self.assertNotIn("feed:travel-dealz-rest", rows)
        self.assertEqual([s["label"] for s in rows["feed:secretflying"]["sources"]],
                         ["Secret Flying", "Travel-Dealz"])
        self.assertTrue(rows["google_flights"]["why"])

        # 3) Bestpreise: Rotation je Richtung, HAM→ZRH ist Beinahe-Deal (unter typischer Spanne)
        best = {(e["source"], e["route"]): e
                for e in self.store.cheapest(self.alert.id)[self.alert.id]}
        self.assertEqual(best[("google_flights", "ZRH-HAM")]["price"], 39)
        self.assertNotIn("near", best[("google_flights", "ZRH-HAM")])
        back = best[("google_flights", "HAM-ZRH")]
        self.assertEqual(back["price"], 70)
        self.assertEqual(back["insight"]["typical"], 105)
        self.assertIn("25 € über deiner Grenze", back["near"])
        self.assertIn(("feed:secretflying", "artikel"), best)
        raw = {k: getattr(self.alert, k) for k in ("id", "name", "trip", "max_price",
                                                     "baseline_price", "max_stops", "rotation")}
        raw.update(origins=["ZRH", "HAM"], destinations=["HAM", "ZRH"])
        near = alert_json(raw, cheapest=self.store.cheapest(today=TODAY))["near_deals"]
        self.assertEqual([n["price"] for n in near], [70])

        # 4) Nächster Lauf: andere Tage, nichts neu gemeldet, nichts abgelaufen; Preise aus
        #    dem Gedächtnis tragen den Zeitpunkt der Abfrage (zählen nicht erneut im Verlauf)
        before = len(self.calls)
        sent2, res2 = self.run_once(NOW + timedelta(hours=3))
        self.assertEqual((sent2, res2.expired, res2.errors), ([], 0, []))
        asked = {c[2] for c in self.calls[before:]}
        self.assertNotIn(self.cheap.isoformat(), asked)
        active = [r for r in self.store.deals(status="active") if r["source"] == "google_flights"]
        self.assertEqual([r["price"] for r in active], [39])

    def test_broken_insight_never_crashes(self):
        with mock.patch.object(google_insights, "parse",
                               return_value={"typical": "viel", "history": [["x", None]],
                                             "typical_low": -5}):
            google = self.google()
            google.begin_run([self.alert])
            sent, res = run_alert(self.alert, [google], self.store, self.cfg, today=TODAY)
        self.assertEqual(res.errors, [])
        deal = next(d for d, _ in sent if d.offer.price == 39)
        self.assertEqual(deal.reference_source, principles.REF_BASELINE)


class HistoryTest(unittest.TestCase):
    def test_remembered_prices_not_counted_again(self):
        now = datetime.now()
        fresh = Offer(source="google_flights", origin="ZRH", destination="HAM", price=82)
        old = Offer(source="google_flights", origin="ZRH", destination="HAM", price=82,
                    found_at=now - timedelta(days=1))
        self.assertEqual(_seen_today([fresh, old]), [fresh])


class HomeDestinationTest(unittest.TestCase):
    """Ziel in DACH: Zürich→Hamburg und Hamburg→Zürich sind verschiedene Deals."""

    def test_direction(self):
        from dealmonitor import sightings

        def keys(title, *codes):
            o = Offer(source="x", origin="", destination="", price=29, roundtrip=False,
                      title=title)
            return sightings.parts(o, *codes)
        self.assertEqual(keys("Zürich nach Hamburg one-way ab 29 €"),
                         (["HAM-economy-OW"], {"ZRH"}))
        self.assertEqual(keys("Hamburg nach Zürich one-way ab 29 €"),
                         (["ZRH-economy-OW"], {"HAM"}))
        self.assertEqual(keys(SF_ITEM["title"], {"ZRH"}, {"HAM"}), (["HAM-economy-OW"], {"ZRH"}))
        self.assertIsNone(keys("Flüge ab Hamburg für 29 €"))      # kein Ziel erkennbar


class RadarOriginTest(unittest.TestCase):
    """Live gesehen: "Bangkok ab Athen 1.274 €" (Travel-Dealz) und "FRA–Bangkok 1.288 €"
    (ErrorFareAlerts) wurden als derselbe Deal zusammengelegt."""

    def item(self, title, link, price, origins=()):
        return {"title": title, "link": link, "price_eur": price, "cabin": "business",
                "origin_codes": list(origins), "categories": ["Flüge"], "description": "",
                "published": datetime(2026, 11, 3, tzinfo=timezone.utc), "feed": "x"}

    def test_different_origin_not_merged(self):
        from dealmonitor.sources.feed import analyze, normalize_item
        a = normalize_item(self.item("Bangkok ab Athen in der Business Class für 1.274 €",
                                     "https://travel-dealz.de/deal/ath-bkk/", 1274))
        b = normalize_item(self.item("Error Fare: Frankfurt to Bangkok business class €1288",
                                     "https://errorfarealerts.com/x/", 1288, ["FRA"]))
        c = normalize_item(self.item("Frankfurt – Bangkok in der Business Class für 1.288 €",
                                     "https://travel-dealz.de/deal/fra-bkk/", 1288))
        ka, kb, kc = (radar.deal_key(it["title"], analyze(it)) for it in (a, b, c))
        self.assertFalse(radar.same_deal(ka, kb))
        self.assertTrue(radar.same_deal(kb, kc))       # FRA (Loader) = Frankfurt (Titel)
        # Schlüssel: Ziel ohne Abflug, Abflug, Airlines, Veröffentlichung (dealmonitor.dedupe)
        self.assertEqual((ka.cabin, ka.oneway, ka.price, ka.origins), ("business", False, 1274, {"ATH"}))
        self.assertIn("BKK", ka.dests)
        self.assertNotIn("ATH", ka.dests)
        self.assertEqual((kb.origins, kb.airlines), ({"FRA"}, frozenset()))
        self.assertIsNone(kb.published)          # normalize_item ohne Lauf-Kontext: kein Datum


if __name__ == "__main__":
    unittest.main()
