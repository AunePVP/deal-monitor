"""Paket 8: Deal-Bewertung - Bezugspreis aus Googles Preisniveau, Beinahe-Deals,
Fehlerpreis-Verdacht, Preissturz, Hot-Wörter, Kurzbegründung und Erstmeldung über alle Quellen
(Tabelle ``sightings``, Migration alter Datenbanken)."""

import json
import logging
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from dealmonitor import notify, principles, radar, sightings
from dealmonitor.config import Config, Settings
from dealmonitor.models import Alert, Deal, Offer
from dealmonitor.monitor import cheapest_entries, run, run_alert, verify_deal
from dealmonitor.principles import Market, evaluate, near_deal, reference
from dealmonitor.scheduler import hot_alerts
from dealmonitor.sources import errorfarealerts as efa
from dealmonitor.sources import secretflying as sf
from dealmonitor.sources.feed import FeedSource
from dealmonitor.store import Store
from dealmonitor.web import alert_json

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 5)
#: Preisniveau der Google-Seite HAM-ZRH hin und zurück (Messung 05.11.: 168 / 235 / 195–275)
INSIGHT = {"level_code": 1, "lowest": 168, "typical": 235, "saving": 67, "typical_low": 195,
           "typical_high": 275, "level": "niedrig",
           "history": [[f"2026-10-{d:02d}", p] for d, p in
                       zip(range(1, 11), (240, 250, 230, 260, 245, 255, 235, 248, 252, 242))]}


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def alert(**kw):
    base = dict(id="zrh-ham", name="Zürich ⇄ Hamburg", origins=["ZRH"], destinations=["HAM"],
                max_price=180, max_stops=1, min_nights=1, max_nights=21)
    base.update(kw)
    return Alert(**base)


def offer(**kw):
    base = dict(source="google_flights", origin="ZRH", destination="HAM", price=168,
                airline="Eurowings", stops=0, departure=date(2027, 1, 14),
                return_date=date(2027, 1, 17))
    base.update(kw)
    return Offer(**base)


class ReferenceTest(unittest.TestCase):
    def test_google_history_median_without_own_history(self):
        d = evaluate(alert(), offer(insight=INSIGHT), [], min_samples=8, today=TODAY)
        self.assertEqual(d.reference_price, 246.5)          # Median der 10 Verlaufspunkte
        self.assertEqual(d.reference_source, principles.REF_GOOGLE_HISTORY)
        self.assertIn("14% unter typischer Spanne (195 €–275 €)", d.reasons)
        self.assertIn("Google: Preise derzeit niedrig (67 € unter Normalpreis)", d.reasons)
        self.assertEqual(d.rating, "Sehr gut")              # 32 % unter 246,50 €

    def test_typical_without_history_points(self):
        ins = dict(INSIGHT, history=[])
        self.assertEqual(reference(alert(), [], 8, ins), (235, principles.REF_GOOGLE_TYPICAL))

    def test_own_history_wins(self):
        ref, source = reference(alert(), [200.0] * 8, 8, INSIGHT)
        self.assertEqual((ref, source), (200, principles.REF_MEDIAN))

    def test_without_insight_unchanged(self):
        a = alert(baseline_price=190)
        self.assertEqual(reference(a, [], 8, None), (190, principles.REF_BASELINE))
        d = evaluate(a, offer(), [], today=TODAY)
        self.assertEqual(d.reasons[0], "unter deiner Grenze von 180 €")

    def test_broken_insight_never_crashes(self):
        for bad in ({"typical": "235", "typical_low": -1, "typical_high": 10},
                    {"typical_low": 300, "typical_high": 200, "history": "x"},
                    {"history": [[1], None, ["d", float("nan")], ["d", True]]},
                    ["kein", "dict"], {"level": 5, "saving": float("inf")}):
            o = offer(insight=bad)
            d = evaluate(alert(baseline_price=190), o, [], today=TODAY)
            self.assertEqual(d.reference_price, 190, bad)
            self.assertEqual(principles.market_notes(o), [])
            self.assertIsNone(near_deal(alert(max_price=100), o, [], today=TODAY))

    def test_reprice_switches_to_google_level(self):
        a = alert(baseline_price=190)
        d = evaluate(a, offer(source="travelpayouts", price=150), [], today=TODAY)
        self.assertEqual(d.reference_source, principles.REF_BASELINE)
        d.offer.insight = INSIGHT
        self.assertTrue(principles.reprice(d, 160, TODAY))
        self.assertEqual(d.reference_source, principles.REF_GOOGLE_HISTORY)
        self.assertTrue(any("unter typischer Spanne" in r for r in d.reasons))


class NearDealTest(unittest.TestCase):
    def test_china_economy_over_limit_but_under_typical(self):
        a = alert(id="de-cn", origins=["FRA"], destinations=["PEK"], max_price=499,
                  max_stops=1, min_nights=None, max_nights=None)
        ins = {"typical": 723, "typical_low": 600, "typical_high": 1050}
        o = offer(origin="FRA", destination="PEK", price=560, insight=ins)
        self.assertIsNone(evaluate(a, o, [], today=TODAY))
        self.assertEqual(near_deal(a, o, [], today=TODAY),
                         ["61 € über deiner Grenze", "7% unter typischer Spanne (600 €–1.050 €)"])

    def test_own_median(self):
        a = alert(max_price=100)
        self.assertEqual(near_deal(a, offer(price=140), [200.0] * 8, min_samples=8, today=TODAY),
                         ["40 € über deiner Grenze", "30% unter dem Median 200 €"])
        self.assertIsNone(near_deal(a, offer(price=160), [200.0] * 8, min_samples=8,
                                    today=TODAY))           # nur 20 % - normal

    def test_real_deal_or_bad_flight_is_no_near_deal(self):
        self.assertIsNone(near_deal(alert(), offer(insight=INSIGHT), [], today=TODAY))
        self.assertIsNone(near_deal(alert(max_price=100), offer(insight=INSIGHT, stops=3), [],
                                    today=TODAY))

    def test_near_deal_lands_in_alert_api(self):
        class Google:
            name, records_history = "google_flights", True

            def fetch(self, al):
                return [offer(price=190, insight=INSIGHT), offer(price=320, departure=date(2027, 2, 1),
                                                                  return_date=date(2027, 2, 4))]

        with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp) / "d.sqlite") as store:
            a = alert(max_price=150)
            cfg = Config(Settings(), [a], Path(tmp))
            items, res = run_alert(a, [Google()], store, cfg, today=TODAY)
            self.assertEqual((items, res.deals), ([], 0))
            api = alert_json({"id": a.id, "name": a.name, "origins": ["ZRH"],
                              "destinations": ["HAM"], "max_price": 150},
                             cheapest=store.cheapest(today=TODAY))
        near = api["near_deals"]
        self.assertEqual(len(near), 1)
        self.assertEqual(near[0]["price"], 190)
        self.assertEqual(near[0]["near"][0], "40 € über deiner Grenze")
        self.assertTrue(near[0]["compare"].startswith("https://www.google.com/travel/flights"))

    def test_cheapest_entries_without_near_unchanged(self):
        a = alert()
        o = offer(price=300)
        entries = cheapest_entries(a, [o], [], set(), TODAY)
        self.assertNotIn("near", entries["google_flights"]["ZRH-HAM"])


class ErrorFareTest(unittest.TestCase):
    def test_far_below_typical_range(self):
        d = evaluate(alert(), offer(price=70, insight=INSIGHT), [], today=TODAY)
        self.assertEqual(d.reasons[0], "Verdacht auf Fehlerpreis: 64% unter typischer Spanne")
        self.assertTrue(principles.suspected_error_fare(d.reasons))
        self.assertEqual(notify.priority(d), 5)

    def test_far_below_own_median(self):
        d = evaluate(alert(), offer(price=70), [200.0] * 8, min_samples=8, today=TODAY)
        self.assertEqual(d.reasons[0], "Verdacht auf Fehlerpreis: 65% unter dem Median 200 €")

    def test_business_close_to_economy(self):
        a = alert(id="de-cn-business", origins=["FRA"], destinations=["PEK"], cabin="business",
                  max_price=1999, min_nights=None, max_nights=None)
        o = offer(origin="FRA", destination="PEK", cabin="business", price=1123)
        d = evaluate(a, o, [], today=TODAY, market=Market(economy=720))
        self.assertIn("Verdacht auf Fehlerpreis: Business Class nur 1,6-mal so teuer wie "
                      "Economy (720 €)", d.reasons)
        normal = evaluate(a, offer(origin="FRA", destination="PEK", cabin="business", price=1900),
                          [], today=TODAY, market=Market(economy=720))
        self.assertFalse(principles.suspected_error_fare(normal.reasons))

    def test_price_drop(self):
        d = evaluate(alert(), offer(price=120), [], today=TODAY,
                     market=Market(recent=[170.0] * 8))
        self.assertIn("Preissturz: 29% unter dem bisherigen Bestpreis 170 € – vermuteter "
                      "Fehlerpreis", d.reasons)
        d = evaluate(alert(), offer(price=140), [], today=TODAY,
                     market=Market(recent=[150.0] + [200.0] * 7))
        self.assertIn("Preissturz: 30% unter dem Median der letzten 30 Tage (200 €) – vermuteter "
                      "Fehlerpreis", d.reasons)
        calm = evaluate(alert(), offer(price=160), [], today=TODAY,
                        market=Market(recent=[170.0] * 8))
        self.assertFalse(principles.suspected_error_fare(calm.reasons))

    def test_monitor_feeds_market_from_store(self):
        """Business-Preis aus der Historie der Economy-Strecke und Preissturz im echten Lauf."""
        a = alert(id="biz", origins=["FRA"], destinations=["PEK"], cabin="business",
                  max_price=1999, min_nights=None, max_nights=None)

        class Src:
            name, records_history = "google_flights", True

            def fetch(self, al):
                return [offer(origin="FRA", destination="PEK", cabin="business", price=1100)]

        with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp) / "d.sqlite") as store:
            eco = [offer(origin="FRA", destination="PEK", price=700 + i,
                         departure=date(2027, 1, 1 + i)) for i in range(8)]
            store.add_prices(eco)
            cfg = Config(Settings(), [a], Path(tmp))
            items, _ = run_alert(a, [Src()], store, cfg, today=TODAY)
        self.assertTrue(any("nur 1,6-mal so teuer wie Economy" in r
                            for r in items[0][0].reasons))


class WhyTest(unittest.TestCase):
    def test_order_and_shortening(self):
        reasons = ["Abflug Nachbar-Hub Budapest (+180 € Zubringer): Ticket 1.310 €, mit "
                   "Zubringer 1.490 €", "unter deiner Grenze von 1.999 €",
                   "31% unter typischer Spanne (195 €–275 €)"]
        self.assertEqual(principles.why(reasons),
                         "31% unter typischer Spanne (195 €–275 €) · Abflug Nachbar-Hub "
                         "Budapest (+180 € Zubringer)")
        self.assertEqual(principles.why(["Verdacht auf Fehlerpreis: 64% unter typischer Spanne",
                                         "64% unter typischer Spanne (195 €–275 €)",
                                         "Erstmeldung Secret Flying, 19 h vor ErrorFareAlerts"]),
                         "Verdacht auf Fehlerpreis: 64% unter typischer Spanne · Erstmeldung "
                         "Secret Flying, 19 h vor ErrorFareAlerts")
        self.assertIsNone(principles.why(["Economy ≤ 300 €"]))
        self.assertIsNone(principles.why(None))

    def test_notifications_use_it(self):
        d = evaluate(alert(), offer(price=70, insight=INSIGHT), [], today=TODAY)
        text = notify.format_text(d, "new")
        self.assertIn("Warum: Verdacht auf Fehlerpreis: 64% unter typischer Spanne · ", text)
        sent = []
        with mock.patch.object(notify.http, "request",
                               side_effect=lambda url, **kw: sent.append(json.loads(kw["data"]))):
            notify.Webhook("https://example.org/hook").send("Alarm", [(d, "new")])
            notify.Ntfy("topic").send("Alarm", [(d, "new")])
        self.assertTrue(sent[0]["deals"][0]["error_fare"])
        self.assertEqual(sent[0]["deals"][0]["why"], principles.why(d.reasons))
        self.assertEqual(sent[1]["priority"], 5)

    def test_api_payload_has_why(self):
        d = evaluate(alert(), offer(insight=INSIGHT), [], today=TODAY)
        with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp) / "d.sqlite") as store:
            store.upsert_deal(d, 0.05)
            row = store.deals()[0]
        # Median 246,5 € -> kaufmännisch 247 € (wie die Oberfläche)
        self.assertEqual(row["why"], "14% unter typischer Spanne (195 €–275 €) · 32% unter "
                                     "Referenz 247 € (Median des Google-Preisverlaufs)")
        self.assertEqual(row["insight"]["typical"], 235)


class HotWordTest(unittest.TestCase):
    def item(self, title, **kw):
        return {"title": title, "link": "https://example.org/" + str(abs(hash(title))),
                "categories": [], "published": None, "description": "", "description_text": "",
                "merchant_price": None, "source": "feed:secretflying", **kw}

    def test_crazy_hot_with_price_under_limit(self):
        it = self.item("CRAZY HOT: Zurich, Switzerland to Mauritius for only €271 roundtrip",
                       price_eur=271.0, origin_codes={"ZRH"})
        deals = radar.scan([it])
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].reasons[:2], ["Als Hammer-Deal angekündigt", "Economy ≤ 300 €"])

    def test_new_words(self):
        self.assertEqual(radar.hot_reason("PRICE DROP: Vienna to Tokyo"),
                         "Preissturz laut Überschrift")
        self.assertEqual(radar.hot_reason("Preissturz bei Condor"), "Preissturz laut Überschrift")
        self.assertEqual(radar.hot_reason("OTA-Glitch: Business nach Bali"),
                         "Error Fare laut Überschrift")
        self.assertIsNone(radar.hot_reason("Flash Sale: Lissabon ab 39 €"))
        self.assertIsNone(radar.hot_reason("Preisstabil nach Rom"))

    def test_hot_title_triggers_google_check(self):
        a = alert(max_price=110)
        o = offer(source="feed:travel-dealz", price=150, title="Preissturz: Zürich nach Hamburg",
                  departure=None, return_date=None)
        d = Deal(alert=a, offer=o, rating="Deal", reference_price=None, discount=None,
                 reasons=[], headline=o.title)
        self.assertEqual(hot_alerts([(d, "new")]), ["zrh-ham"])


# -- Erstmeldung über alle Quellen ------------------------------------------------------------

#: Stand der Secret-Flying-/ErrorFareAlerts-Fixtures
FIXTURE_NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


def fresh(items):
    """Zeiten der Fixtures so verschieben, als wären sie gerade erst erschienen (Abstände
    bleiben gleich) - sonst greift nach ein paar Tagen die Altersregel der Feeds."""
    delta = datetime.now(timezone.utc) - FIXTURE_NOW
    out = []
    for it in items:
        it = dict(it)
        for key in ("published", "expires"):
            if isinstance(it.get(key), datetime):
                it[key] = it[key] + delta
        out.append(it)
    return out


def sf_items():
    posts = []
    for name in ("bsky_secretflying.json", "bsky_secretflying_p2.json"):
        posts += sf.parse_api(json.loads((FIX / name).read_text(encoding="utf-8")))[0]
    return fresh(it for it in map(sf.post_item, posts) if it)


def efa_items():
    return fresh(efa.parse((FIX / "feeds" / "errorfarealerts_flights.html")
                           .read_text(encoding="utf-8")))


class Collector:
    name = "col"

    def __init__(self):
        self.items = []

    def send(self, alert_name, items):
        self.items += items


JNB = Alert(id="zrh-jnb", name="Zürich – Johannesburg", origins=["ZRH"], destinations=["JNB"],
            max_price=500, max_stops=2)


def feed_run(store, sf_list, efa_list, alerts=(JNB,), radar_settings=None):
    col = Collector()
    with mock.patch.object(sf, "fetch_items", return_value=sf_list), \
            mock.patch.object(efa, "fetch_items", return_value=efa_list):
        src = FeedSource(presets=["secretflying", "errorfarealerts"], max_age_days=36500,
                         search_enabled=False)
        res = run(Config(Settings(), list(alerts), Path(".")), [src], [col], store,
                  radar_settings=radar_settings or radar.RadarSettings(enabled=False))
    return col.items, res


def jnb(items):
    return [it for it in items if "Johannesburg" in it["title"]]


class FirstReportTest(unittest.TestCase):
    """Secret Flying meldete ZRH–Johannesburg 471 € am 04.11. 09:13 UTC, ErrorFareAlerts
    denselben Deal (475 €) am 05.11. 04:28 UTC - 19 h später (Fixtures)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "d.sqlite")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_same_run_one_alarm_two_sources(self):
        sent, _ = feed_run(self.store, jnb(sf_items()), jnb(efa_items()))
        self.assertEqual(len(sent), 1)
        deal, status = sent[0]
        self.assertEqual((deal.offer.source, status), ("feed:secretflying", "new"))
        self.assertIn("Erstmeldung Secret Flying, 19 h vor ErrorFareAlerts", deal.reasons)
        rows = self.store.deals()
        self.assertEqual(len(rows), 1)
        self.assertEqual([s["label"] for s in rows[0]["sources"]],
                         ["Secret Flying", "ErrorFareAlerts"])
        self.assertEqual(rows[0]["lead"], "Erstmeldung Secret Flying, 19 h vor ErrorFareAlerts")
        self.assertTrue(rows[0]["why"].startswith("Erstmeldung Secret Flying"))

    def test_later_source_only_extends(self):
        sent, _ = feed_run(self.store, jnb(sf_items()), [])
        self.assertEqual(len(sent), 1)
        self.assertNotIn("sources", self.store.deals()[0])
        sent, _ = feed_run(self.store, jnb(sf_items()), jnb(efa_items()))
        self.assertEqual(sent, [])                       # kein zweiter Alarm
        row = self.store.deals()[0]
        self.assertEqual(len(row["sources"]), 2)
        self.assertIn(row["lead"], row["reasons"])
        # weitere Läufe überschreiben Quellenliste und Vorsprung nicht
        sent, _ = feed_run(self.store, jnb(sf_items()), jnb(efa_items()))
        self.assertEqual(sent, [])
        row = self.store.deals()[0]
        self.assertEqual(len(row["sources"]), 2)
        self.assertEqual(row["reasons"].count(row["lead"]), 1)

    def test_earlier_publication_wins_even_if_seen_later(self):
        sent, _ = feed_run(self.store, [], jnb(efa_items()))
        self.assertEqual(sent[0][0].offer.source, "feed:errorfarealerts")
        sent, _ = feed_run(self.store, jnb(sf_items()), jnb(efa_items()))
        self.assertEqual(sent, [])
        self.assertEqual(self.store.deals()[0]["lead"],
                         "Erstmeldung Secret Flying, 19 h vor ErrorFareAlerts")

    def test_different_deals_stay_apart(self):
        sf_jnb = jnb(sf_items())
        for change in ({"price_eur": 420.0},
                       {"published": sf_jnb[0]["published"] - timedelta(days=9)}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp, \
                    Store(Path(tmp) / "d.sqlite") as store:
                sent, _ = feed_run(store, [dict(it, **change) for it in sf_jnb],
                                   jnb(efa_items()))
                self.assertEqual(len(sent), 2)
        title = "Zürich nach Johannesburg ab 471 €"
        eco = Offer(source="a", origin="", destination="", price=471, title=title)
        biz = Offer(source="b", origin="", destination="", price=471, title=title,
                    cabin="business")
        ow = Offer(source="b", origin="", destination="", price=471, title=title,
                   roundtrip=False)
        self.assertEqual(sightings.parts(eco)[0], ["JNB-economy-RT"])
        self.assertEqual(sightings.parts(biz)[0], ["JNB-business-RT"])
        self.assertEqual(sightings.parts(ow)[0], ["JNB-economy-OW"])
        # anderer bekannter Abflug: nicht derselbe Deal
        sightings.record(self.store, eco, sightings.parts(eco), "x:1")
        self.store.upsert_deal(Deal(alert=JNB, offer=eco, rating="Deal", reference_price=None,
                                    discount=None, reasons=[], headline=title), 0.05)
        other = Offer(source="b", origin="", destination="", price=471,
                      title="Frankfurt nach Johannesburg ab 471 €")
        d = Deal(alert=JNB, offer=other, rating="Deal", reference_price=None, discount=None,
                 reasons=[], headline=other.title)
        self.assertIsNone(sightings.first_report(self.store, d, sightings.parts(other), {"x:1"}))
        same = Offer(source="b", origin="", destination="", price=480, title=title)
        d = Deal(alert=JNB, offer=same, rating="Deal", reference_price=None, discount=None,
                 reasons=[], headline=title)
        self.assertEqual(sightings.first_report(self.store, d, sightings.parts(same), {"x:1"}),
                         "x:1")

    def test_same_source_twice_is_not_merged(self):
        it = jnb(sf_items())[0]
        twin = dict(it, link=it["link"] + "-update", uid=None, title=it["title"] + " (update)")
        sent, _ = feed_run(self.store, [it, twin], [])
        self.assertEqual(len(sent), 2)

    def test_radar_twins_get_lead(self):
        sent, _ = feed_run(self.store, sf_items(), efa_items(), alerts=(),
                           radar_settings=radar.RadarSettings(economy_max=500))
        phuket = [d for d, _ in sent if "Phuket" in d.headline]
        self.assertEqual(len(phuket), 1)                 # EFA-Phuket nicht noch einmal
        self.assertIn("Erstmeldung Secret Flying, 18 h vor ErrorFareAlerts", phuket[0].reasons)
        seoul = next(r for r in self.store.deals() if "Seoul" in r["headline"])
        self.assertEqual(seoul["lead"], "Erstmeldung Secret Flying, 17 h vor ErrorFareAlerts")
        # zweiter Lauf: nichts Neues, Quellenliste bleibt
        sent, _ = feed_run(self.store, sf_items(), efa_items(), alerts=(),
                           radar_settings=radar.RadarSettings(economy_max=500))
        self.assertEqual(sent, [])

    def test_unknown_destination_word(self):
        o = Offer(source="feed:secretflying", origin="", destination="", price=471,
                  title="Zurich, Switzerland to Mauritius for only €471 roundtrip")
        keys, origins = sightings.parts(o)
        self.assertEqual(keys, ["~mauritius-economy-RT"])
        self.assertIn("ZRH", origins)
        o2 = Offer(source="feed:errorfarealerts", origin="", destination="", price=469,
                   title="Von Zürich nach Mauritius ab 469 €")
        self.assertEqual(sightings.parts(o2)[0], keys)
        self.assertIsNone(sightings.parts(Offer(source="x", origin="", destination="",
                                                price=0, title="Mauritius")))

    def test_lead_text(self):
        src = [{"label": "A", "seen": "2026-11-01T10:00:00"},
               {"label": "B", "seen": "2026-11-01T10:20:00"}]
        self.assertEqual(sightings.lead_text(src), "Erstmeldung A, kurz vor B")
        src[1]["seen"] = "2026-11-04T10:00:00"
        src.append({"label": "C", "seen": "2026-11-05T10:00:00"})
        self.assertEqual(sightings.lead_text(src), "Erstmeldung A, 3 Tage vor B (insgesamt 3 "
                                                   "Quellen)")
        self.assertIsNone(sightings.lead_text(src[:1]))
        self.assertIsNone(sightings.lead_text([{"label": "A", "seen": "x"}, {"label": "B",
                                                                            "seen": None}]))


class VerifyInsightTest(unittest.TestCase):
    def test_google_level_used_for_confirmed_cache_deal(self):
        class Verifier:
            def verify(self, *a, **kw):
                return {"price": 150, "airline": "Eurowings", "stops": 0, "duration_min": 85,
                        "insight": INSIGHT}

        d = evaluate(alert(baseline_price=190), offer(source="travelpayouts", price=145), [],
                     today=TODAY)
        self.assertEqual(verify_deal(d, Verifier(), TODAY), "confirmed")
        self.assertEqual(d.reference_source, principles.REF_GOOGLE_HISTORY)
        self.assertIn("23% unter typischer Spanne (195 €–275 €)", d.reasons)
        self.assertTrue(d.reasons[-1].startswith("Google Flights bestätigt"))


OLD_SCHEMA = """
CREATE TABLE prices (route_key TEXT NOT NULL, source TEXT NOT NULL, departure TEXT,
    return_date TEXT, price REAL NOT NULL, observed_on TEXT NOT NULL,
    UNIQUE (route_key, source, departure, return_date, price, observed_on));
CREATE TABLE deals (fingerprint TEXT PRIMARY KEY, alert_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', rating TEXT NOT NULL, headline TEXT NOT NULL,
    price REAL NOT NULL, notified_price REAL, source TEXT NOT NULL, link TEXT,
    payload TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, expired_at TEXT);
CREATE TABLE alerts (id TEXT PRIMARY KEY, data TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
    alert_id TEXT, alert_name TEXT, status TEXT, rating TEXT, headline TEXT, price REAL,
    link TEXT, compare TEXT, read INTEGER NOT NULL DEFAULT 0, fingerprint TEXT);
CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, trigger TEXT NOT NULL, alert_id TEXT,
    started_at TEXT NOT NULL, finished_at TEXT, offers INTEGER NOT NULL DEFAULT 0,
    deals INTEGER NOT NULL DEFAULT 0, notified INTEGER NOT NULL DEFAULT 0,
    expired INTEGER NOT NULL DEFAULT 0, errors TEXT NOT NULL DEFAULT '[]');
"""


class MigrationTest(unittest.TestCase):
    def test_old_database_upgrades(self):
        """Datenbank ohne ``sightings``/``cheapest`` und ohne neue Payload-Felder."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alt.sqlite"
            db = sqlite3.connect(path)
            db.executescript(OLD_SCHEMA)
            it = jnb(sf_items())[0]
            sent_fp = None
            with Store(":memory:") as probe:     # Fingerprint wie im echten Lauf ermitteln
                sent, _ = feed_run(probe, [it], [])
                sent_fp = sent[0][0].fingerprint
            old_payload = {"origin": "ZRH", "destination": "JNB", "cabin": "economy",
                           "roundtrip": True, "reasons": ["unter deiner Grenze von 500 €"],
                           "alert_name": "Zürich – Johannesburg"}
            db.execute("INSERT INTO deals (fingerprint, alert_id, rating, headline, price, "
                       "notified_price, source, link, payload, first_seen, last_seen) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (sent_fp, "zrh-jnb", "Deal", it["title"], 471, 471, "feed:secretflying",
                        it["link"], json.dumps(old_payload), "2026-11-04T10:00:00",
                        "2026-11-04T10:00:00"))
            db.execute("INSERT INTO runs (trigger, started_at) VALUES ('manual', '2026-11-04')")
            db.commit()
            db.close()

            with Store(path) as store:
                tables = {r[0] for r in store.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"sightings", "cheapest"} <= tables)
                self.assertEqual(store.runs()[0]["radar"], 0)
                row = store.deals()[0]
                self.assertNotIn("sources", row)
                self.assertNotIn("why", row)
                # alter Deal (ohne Erstmeldungs-Eintrag) bleibt still, der neue läuft normal
                sent, _ = feed_run(store, [it], [])
                self.assertEqual(sent, [])
                sent, _ = feed_run(store, [it], jnb(efa_items()))
                self.assertEqual(sent, [])
                row = store.deals()[0]
                self.assertEqual(row["lead"], "Erstmeldung Secret Flying, 19 h vor ErrorFareAlerts")
                self.assertEqual(len(store.deals()), 1)


if __name__ == "__main__":
    unittest.main()
