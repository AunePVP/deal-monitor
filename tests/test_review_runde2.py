"""Regressionstests zu den bestätigten Review-Funden von Runde 2 (ohne Netz).

Je Fund mindestens ein Test: Abflug-Erkennung (Secret Flying, YHBU), Gedächtnis von
ErrorFareAlerts, Fehlerpreis-Hinweise, Erstmeldung über Quellen, Google-Rotation und -Budget,
Quellen-Gesundheit und die Anzeige-Daten der Oberfläche.
"""

import json
import logging
import tempfile
import threading
import unittest
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from dealmonitor import config as config_mod
from dealmonitor import health, monitor, notify, principles, radar, rotation, web
from dealmonitor.appsettings import AppSettings, Context
from dealmonitor.config import normalize_alert
from dealmonitor.models import Alert, Deal, Offer
from dealmonitor.monitor import run_alert
from dealmonitor.scheduler import Scheduler, execute
from dealmonitor.sources import errorfarealerts as efa
from dealmonitor.sources import feed
from dealmonitor.sources import secretflying as sf
from dealmonitor.sources import traveldealz_rest as tdr
from dealmonitor.sources.feed import FeedSource, YHBU_PROFILE, analyze, origin_ok
from dealmonitor.sources.googleflights import GoogleBlocked
from dealmonitor.store import Store

import test_google_insights as gi
import test_rotation as rot
from test_errorfarealerts import card

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 4)


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


# -- Alarme wie in config/alerts.toml --------------------------------------------------------
HAM_ZRH = Alert(id="ham-zrh", name="Hamburg → Zürich", origins=["HAM"], destinations=["ZRH"],
                max_price=110, feed_keywords=[["Hamburg", "Zürich"]])
ZRH_HAM = Alert(id="zrh-ham", name="Zürich → Hamburg", origins=["ZRH"], destinations=["HAM"],
                max_price=110, feed_keywords=[["Zürich", "Hamburg"]])
ZRH_HAM_OW = Alert(id="zrh-ham-oneway", name="Zürich ⇄ Hamburg one-way",
                   origins=["ZRH", "HAM"], destinations=["HAM", "ZRH"], trip="oneway",
                   max_price=45)
DE_CN = Alert(id="de-cn-economy", name="Deutschland → China", origins=["@DE"],
              destinations=["@CN_MAJOR"], max_price=499,
              feed_keywords=[["China"], ["Peking"], ["Beijing"], ["Shanghai"]])
DE_CN_BIZ = Alert(id="de-cn-business", name="Deutschland → China Business",
                  origins=["@DE_HUBS"], destinations=["@CN_MAJOR"], cabin="business",
                  max_price=1999, feed_keywords=[["Peking", "Business"], ["China", "Business"]])


def sf_items(*heads):
    out = []
    for n, h in enumerate(heads):
        it = sf.post_item({"text": h, "created": NOW, "uri": f"at://did:x/app.bsky.feed.post/k{n}",
                           "links": [], "pinned": False})
        if it:
            out.append(it)
    return out


def sf_hits(alert, *heads):
    src = FeedSource(presets=["secretflying"], search_enabled=False, max_age_days=36500)
    with mock.patch.object(feed, "load_loader", return_value=lambda **kw: sf_items(*heads)):
        return [o.title for o in src.fetch(alert)]


def sf_info(head):
    it = feed.normalize_item(sf_items(head)[0], "loader:secretflying", now=NOW)
    it["profile"], it["source"] = feed.Profile(lang="en"), "feed:secretflying"
    return it, analyze(it)


class SecretFlyingOriginTest(unittest.TestCase):
    """Fund 1: Europa/DACH ohne Ort darf nie als "Abflug unbekannt" durch jeden Alarm."""

    def test_europe_without_codes_is_foreign(self):
        for head in ("European cities to Shanghai, China from only €389 roundtrip #Travel",
                     "UK cities to Beijing, China from only £300 roundtrip #Travel",
                     "ERROR FARE: European cities to Shanghai, China from only €1,099 roundtrip "
                     "in Business Class #Travel"):
            _, info = sf_info(head)
            self.assertEqual(info.origin, "foreign", head)
            self.assertEqual(sf_hits(DE_CN, head), [], head)
            self.assertEqual(sf_hits(DE_CN_BIZ, head), [], head)

    def test_austrian_cities_are_austria(self):
        head = "Austrian cities to Beijing, China from only €399 roundtrip #Travel"
        self.assertEqual(sf.classify_origin("Austrian cities"),
                         ("home", set(sf.regions.REGIONS["AT"])))
        self.assertEqual(sf_hits(DE_CN, head), [])          # Österreich passt nicht zu @DE
        self.assertEqual(sf.classify_origin("Swiss cities")[0], "home")

    def test_german_region_still_matches_region_alert(self):
        heads = ("#Germany to Beijing, China from only €399 roundtrip #Travel",
                 "German cities to Shanghai, China from only €420 roundtrip #Travel")
        self.assertEqual(len(sf_hits(DE_CN, *heads)), 2)

    def test_radar_home_only_drops_uk_cities(self):
        item, info = sf_info("ERROR FARE: UK cities to New York, USA from only £199 roundtrip")
        self.assertIsNone(radar.check(item, radar.RadarSettings(), info))


class CountryOriginTest(unittest.TestCase):
    """Fund 2: ein ganzes Land als Abflug belegt keine einzelne Stadt (HAM/ZRH)."""

    def test_country_does_not_match_city_alerts(self):
        self.assertEqual(sf_hits(HAM_ZRH, "#Germany to Zurich, Switzerland from only €29 "
                                          "roundtrip #Travel"), [])
        for head in ("#Switzerland to Hamburg, Germany for only €35 one-way #Travel",
                     "#Lugano, Switzerland to Hamburg, Germany for only €39 one-way #Travel"):
            self.assertEqual(sf_hits(ZRH_HAM_OW, head), [], head)
            self.assertEqual(sf_hits(ZRH_HAM, head), [], head)
        item = sf_items("#Lugano, Switzerland to Hamburg, Germany for only €39 one-way")[0]
        self.assertTrue(item["origin_region"])

    def test_city_still_matches(self):
        head = "#Zurich, Switzerland to Hamburg, Germany for only €39 one-way #Travel"
        self.assertEqual(len(sf_hits(ZRH_HAM_OW, head)), 1)
        self.assertNotIn("origin_region", sf_items(head)[0])

    def test_region_flag_needs_codes_and_true(self):
        it = feed.normalize_item({"title": "x", "link": "", "origin_region": "ja",
                                  "origin_foreign": 1}, "loader:x", now=NOW)
        self.assertNotIn("origin_region", it)
        self.assertNotIn("origin_foreign", it)


def yhbu_item(title):
    it = {"title": title, "link": "https://youhavebeenupgraded.boardingarea.com/x/",
          "published": NOW, "categories": [], "description": "", "description_text": "",
          "merchant_price": None}
    feed._enrich(it, YHBU_PROFILE)
    it["profile"], it["source"] = YHBU_PROFILE, "feed:yhbu"
    return it


class YhbuOriginTest(unittest.TestCase):
    """Fund 3: fester Titel mit unbekanntem Abflug (Baku, Tunis) ist "foreign"."""

    def test_unknown_city_is_foreign(self):
        for title in ("DEAL: Azerbaijan Business Baku – Peking (Return): 990 Euro",
                      "DEAL: Etihad First Tunis – Shanghai (Oneway): 1.380 Euro"):
            info = analyze(yhbu_item(title))
            self.assertEqual(info.origin, "foreign", title)
            self.assertFalse(origin_ok(DE_CN_BIZ, info), title)
            self.assertIsNone(feed.hub_origin(DE_CN_BIZ, info), title)

    def test_home_and_europe_unchanged(self):
        info = analyze(yhbu_item("DEAL: MIAT Business Frankfurt – Peking (Return): 1.330 Euro"))
        self.assertEqual((info.origin, info.origin_codes), ("home", {"FRA"}))
        self.assertTrue(origin_ok(DE_CN_BIZ, info))
        # "Europa" kann auch ab Deutschland sein: bleibt unbekannt
        self.assertEqual(analyze(yhbu_item("DEAL: Egyptair Business Europa – Johannesburg "
                                           "(Return): 1.300 Euro")).origin, "unknown")
        de = yhbu_item("DEAL: Finnair Business Deutschland – Seoul (Return): 1.430 Euro")
        self.assertTrue(de["origin_region"])

    def test_through_feed_source(self):
        rss = ("<?xml version='1.0'?><rss><channel>" + "".join(
            f"<item><title>{t}</title><link>https://youhavebeenupgraded.boardingarea.com/{n}/"
            f"</link><pubDate>Thu, 05 Nov 2026 08:00:00 +0000</pubDate></item>"
            for n, t in enumerate(("DEAL: Azerbaijan Business Baku – Peking (Return): 990 Euro",
                                   "DEAL: MIAT Business Frankfurt – Peking (Return): 1.330 Euro"))
        ) + "</channel></rss>").encode()
        src = FeedSource(presets=["yhbu"], search_enabled=False, max_age_days=36500)
        with mock.patch.object(feed.http, "request", return_value=rss):
            offers = src.fetch(DE_CN_BIZ)
        self.assertEqual([o.price for o in offers], [1330.0])


class ErrorFareAlertsMemoryTest(unittest.TestCase):
    """Fund 4: Deals von Seite 1 bleiben im Gedächtnis, solange sie jung genug sind."""

    @staticmethod
    def html(*cards):
        return ("<html><body>" + "".join(cards) + "</body></html>").encode()

    def test_remembers_older_deals(self):
        old = card("✈️ Air China: Von Frankfurt nach Hongkong ab 465 €", "/deals/flights/hkg",
                   "02.11.2026 05:40", "465", nach="Hong Kong International (HKG)")
        new = card("✈️ Condor: Von Frankfurt nach New York ab 393 €", "/deals/flights/jfk",
                   "05.11.2026 04:46")
        state = {}
        with mock.patch.object(efa, "_now", return_value=NOW):
            with mock.patch.object(efa.http, "request", return_value=self.html(old)):
                self.assertEqual(len(efa.fetch_items(state=state)), 1)
            json.dumps(state)                                       # speicherbar
            with mock.patch.object(efa.http, "request", return_value=self.html(new)):
                items = efa.fetch_items(state=state)
        self.assertEqual([i["link"].rsplit("/", 1)[-1] for i in items], ["jfk", "hkg"])
        self.assertEqual(items[1]["price_eur"], 465.0)
        self.assertIsInstance(items[1]["published"], datetime)
        # nach KEEP_DAYS fällt der alte Deal heraus; Kaputtes im Zustand stört nie
        state["deals"].append({"title": 5})
        later = NOW + timedelta(days=efa.KEEP_DAYS, hours=1)
        with mock.patch.object(efa, "_now", return_value=later), \
                mock.patch.object(efa.http, "request", return_value=self.html(new)):
            self.assertEqual(len(efa.fetch_items(state=state)), 1)


class RepriceKeepsWarningsTest(unittest.TestCase):
    """Fund 5: Google-Gegenprüfung behält Fehlerpreis-Verdacht und Preissturz."""

    def test_reprice_keeps_market_signs(self):
        a = Alert(id="cn-biz", name="China Business", origins=["FRA"], destinations=["PEK"],
                  cabin="business", max_price=1999)
        o = Offer(source="travelpayouts", origin="FRA", destination="PEK", price=900,
                  roundtrip=True, cabin="business", departure=date(2027, 3, 1),
                  return_date=date(2027, 3, 15))
        m = principles.Market(recent=[2400, 2500, 2300, 2600, 2450], economy=650.0)
        d = principles.evaluate(a, o, [], min_samples=5, today=TODAY, market=m)
        self.assertTrue(principles.suspected_error_fare(d.reasons))
        self.assertTrue(principles.reprice(d, 920.0, TODAY))
        self.assertTrue(principles.suspected_error_fare(d.reasons))
        self.assertTrue(any("nur 1,4-mal so teuer" in r for r in d.reasons))
        self.assertTrue(any(r.startswith(principles.DROP_PREFIX) for r in d.reasons))
        self.assertEqual(notify.priority(d), 5)


class DropMedianTest(unittest.TestCase):
    """Fund 8 (Preissturz-Median): Bestpreis je Tag, Googles Spanne hat Vorrang."""

    def offer(self, insight=None):
        return Offer(source="google_flights", origin="ZRH", destination="HAM", price=44,
                     roundtrip=False, departure=date(2027, 1, 14), airline="Eurowings",
                     stops=0, insight=insight)

    def test_google_range_blocks_median_drop(self):
        recent = [49, 82, 95, 120, 150, 174] * 10
        signs = principles.warning_signs(
            self.offer({"typical_low": 60, "typical_high": 140, "typical": 95}),
            market=principles.Market(recent=recent))
        self.assertFalse(principles.suspected_error_fare(signs))
        # ohne Google-Spanne greift die Regel weiter (Bauplan)
        signs = principles.warning_signs(self.offer(), market=principles.Market(recent=recent))
        self.assertTrue(any(s.startswith(principles.DROP_PREFIX) for s in signs))

    def test_market_uses_daily_best(self):
        with Store(":memory:") as store:
            today = datetime.now().date()
            for n in range(10):
                day = today - timedelta(days=n)
                for price, dep in ((49 + n, "2027-01-14"), (120, "2027-01-15"),
                                   (174, "2027-01-16")):
                    store.db.execute(
                        "INSERT INTO prices (route_key, source, departure, return_date, price, "
                        "observed_on) VALUES (?,?,?,?,?,?)",
                        ("ZRH-HAM-ow-economy", "google_flights", dep, None, price,
                         day.isoformat()))
            market = monitor._market(store, "ZRH-HAM-ow-economy", config_mod.Settings())
        self.assertEqual(sorted(market.recent), [49 + n for n in range(10)])
        signs = principles.warning_signs(self.offer(), market=market)
        self.assertFalse(principles.suspected_error_fare(signs))       # 44 vs. Tagesbest ~53


def article_deal(alert, src, title, link, when, price):
    o = Offer(source=src, origin="FRA", destination="PEK", price=price, roundtrip=True,
              cabin="economy", link=link, title=title, found_at=when)
    return Deal(alert=alert, offer=o, rating="Deal", reference_price=None, discount=None,
                reasons=[], headline=title)


class FirstReportTest(unittest.TestCase):
    """Fund 6 und 7: Zuordnung zur Erstmeldung ist stabil und lässt nichts verschwinden."""

    CN = Alert(id="cn", name="China", origins=["FRA"], destinations=["PEK"], max_price=499)
    SF = ("feed:secretflying", "Frankfurt, Germany to Beijing, China for only €450 roundtrip",
          "https://www.secretflying.com/posts/fra-pek")
    TD = ("feed:travel-dealz", "Peking ab 450 € hin und zurück ab Frankfurt",
          "https://travel-dealz.de/deal/peking-450")

    def first(self, store):
        sf_deal = article_deal(self.CN, *self.SF, datetime(2026, 11, 1, 8), 450)
        kept, _ = monitor.first_reports(store, [(sf_deal, store.deal_status(sf_deal, 0.1))], [])
        for d, _ in kept:
            store.upsert_deal(d, 0.1)
        return sf_deal

    def test_moved_found_at_keeps_assignment(self):
        with Store(":memory:") as store:
            sf_deal = self.first(store)
            td = article_deal(self.CN, *self.TD, datetime(2026, 11, 2, 9), 455)
            kept, merged = monitor.first_reports(store, [(td, store.deal_status(td, 0.1))], [])
            self.assertEqual((kept, list(merged)), ([], [sf_deal.fingerprint]))
            # Tag 9: Artikel aktualisiert -> found_at weit außerhalb des 7-Tage-Fensters
            td2 = article_deal(self.CN, *self.TD, datetime(2026, 11, 10, 9), 455)
            self.assertEqual(td2.fingerprint, td.fingerprint)
            kept, merged = monitor.first_reports(store, [(td2, store.deal_status(td2, 0.1))], [])
            self.assertEqual((kept, list(merged)), ([], [sf_deal.fingerprint]))

    def test_first_expired_second_is_kept_quietly(self):
        with Store(":memory:") as store:
            self.first(store)
            td = article_deal(self.CN, *self.TD, datetime(2026, 11, 2, 9), 455)
            monitor.first_reports(store, [(td, store.deal_status(td, 0.1))], [])
            store.expire_missing("cn", "feed:secretflying", set())
            kept, merged = monitor.first_reports(store, [(td, store.deal_status(td, 0.1))], [])
            self.assertEqual(([(d.offer.source, st) for d, st in kept], dict(merged)),
                             ([("feed:travel-dealz", "quiet")], {}))

    def test_run_alert_keeps_second_report_active(self):
        """Ganzer Lauf: Secret Flying zuerst, dann nur noch Travel-Dealz -> Deal bleibt aktiv,
        ohne zweite Meldung."""
        alert = Alert(id="zrh-ham-ow", name="Zürich → Hamburg one-way", origins=["ZRH"],
                      destinations=["HAM"], trip="oneway", max_price=45,
                      feed_keywords=[["Zürich", "Hamburg"]])
        cfg = config_mod.Config(settings=config_mod.Settings(), alerts=[], path=Path("x"))
        items = {"sf": dict(rot_article("Non-stop from Zurich, Switzerland to Hamburg, Germany "
                                        "for only €29 one-way",
                                        "https://www.secretflying.com/posts/zrh-ham-29/", 3, 8)),
                 "td": dict(rot_article("Zürich nach Hamburg one-way ab 29 €",
                                        "https://travel-dealz.de/deal/zrh-ham-29/", 3, 14))}

        def run(keys):
            src = FeedSource(presets=["secretflying", "travel-dealz-rest"],
                             max_age_days=36500, search_enabled=False)
            with mock.patch.object(sf, "fetch_items",
                                   return_value=[items[k] for k in keys if k == "sf"]), \
                    mock.patch.object(tdr, "fetch_items",
                                      return_value=[items[k] for k in keys if k == "td"]):
                return run_alert(alert, [src], store, cfg, today=TODAY)

        with Store(":memory:") as store:
            sent, _ = run(["sf", "td"])
            self.assertEqual([d.offer.source for d, _ in sent], ["feed:secretflying"])
            for _ in range(2):              # Secret Flying nimmt den Post heraus
                sent, _ = run(["td"])
                self.assertEqual(sent, [])
                active = [r["source"] for r in store.deals(status="active")]
                self.assertEqual(active, ["feed:travel-dealz-rest"])


def rot_article(title, link, day, hour):
    return {"title": title, "link": link, "published": datetime(2026, 11, day, hour,
                                                                 tzinfo=timezone.utc),
            "price_eur": 29, "origin_codes": ["ZRH"], "dest_codes": ["HAM"], "oneway": True,
            "uid": link, "categories": ["Flüge"], "description": ""}


class FakeStateStore:
    def __init__(self):
        self.state = {}

    def source_state(self, name):
        return json.loads(json.dumps(self.state.get(name, {})))

    def set_source_state(self, name, value):
        self.state[name] = json.loads(json.dumps(value))


class GoogleBlockedKeepsMemoryTest(unittest.TestCase):
    """Fund 9: Sperre mitten im Rotations-Alarm - abgefragte Tage bleiben gespeichert."""

    def test_memory_saved_on_block(self):
        store, calls = FakeStateStore(), []

        def fetch(url):
            calls.append(url)
            if len(calls) > 4:
                raise GoogleBlocked("HTTP 429")
            return rot.page([(39, "Eurowings", 0)])

        src = rot.source(fetch, state_store=store)
        with self.assertRaises(feed.SourceError):
            src.fetch(rot.oneway())
        saved = rotation.Memory(store.state.get(rotation.STATE_NAME))
        days = [e for o, d in rot.ROUTES
                for e in saved.get(o, d, "economy", rot.TODAY, rot.NOW).values()]
        self.assertEqual(len(days), 4)                      # die 4 Abfragen vor der Sperre
        self.assertEqual({e["rows"][0]["price"] for e in days}, {39.0})


class RetryBudgetTest(unittest.TestCase):
    """Fund 10: zweiter Abruf nur aus dem eigenen Anteil, nie aus Reserve oder fremdem Anteil."""

    def test_last_query_no_retry_into_reserve(self):
        src = gi.source([gi.OW, gi.OW, gi.EMPTY_MASK, gi.OW], max_requests=4, verify_reserve=1)
        src.fetch(gi.alert(months_ahead=2))
        self.assertEqual((src.requests_made, src.scans_made), (3, 3))
        self.assertIsNotNone(src.verify("ZRH", "HAM", date(2027, 3, 3), None, "economy", False))

    def test_last_query_no_retry_into_other_share(self):
        src = gi.source([gi.OW, gi.EMPTY_MASK, gi.OW], max_requests=4)
        a = gi.alert(id="a", months_ahead=1)
        b = gi.alert(id="b", origins=["GVA"], months_ahead=1)
        src.begin_run([a, b])
        src.fetch(a)
        self.assertEqual(src.requests_made, 2)
        src.fetch(b)
        self.assertIsNone(src.incomplete)          # b bekommt beide Abfragen

    def test_retry_inside_share_still_happens(self):
        src = gi.source([gi.EMPTY_MASK, gi.OW, gi.OW], max_requests=6)
        src.fetch(gi.alert(months_ahead=1))
        self.assertEqual(src.retries, 1)


class CompactRowsTest(unittest.TestCase):
    """Fund 11: Gedächtnis behält auch die schnellere, etwas teurere Zeile."""

    def test_faster_row_kept(self):
        rows = [{"price": 89, "airline": "Lufthansa", "stops": 1, "duration_min": 480},
                {"price": 99, "airline": "Lufthansa", "stops": 1, "duration_min": 180},
                {"price": 120, "airline": "Lufthansa", "stops": 1, "duration_min": 300}]
        self.assertEqual([r[0] for r in rotation.compact_rows(rows)], [89.0, 99.0])

    def test_combined_respects_duration(self):
        src = rot.source()
        d1, d2 = rot.TODAY + timedelta(days=10), rot.TODAY + timedelta(days=13)
        row = lambda p, a, s, m: {"price": p, "airline": a, "airline_code": None, "stops": s,
                                  "duration_min": m}
        src.memory.put("ZRH", "HAM", "economy", d1,
                       [row(89, "Lufthansa", 1, 480), row(99, "Lufthansa", 1, 180)], None, rot.NOW)
        src.memory.put("HAM", "ZRH", "economy", d2, [row(60, "Eurowings", 0, 85)], None, rot.NOW)
        rt = rot.roundtrip(max_price=200)
        self.assertEqual([o.price for o in src.combined(rt)], [159.0])


class RunCounterTest(unittest.TestCase):
    """Fund 12: der Lauf-Index zählt je Lauf eins weiter, auch um eine Taktgrenze herum."""

    def test_jitter_around_boundary_skips_nothing(self):
        mem = rotation.Memory()
        boundary = datetime.fromtimestamp(rotation.run_index(rot.NOW) * 3 * 3600 + 3 * 3600)
        seen = []
        for k in range(30):                    # Läufe alle 3 h, Beginn ±20 s um die Grenze
            now = boundary + timedelta(hours=3 * k, seconds=20 if k % 2 else -20)
            seen.append(mem.run_number("a", now))
        self.assertEqual(seen, list(range(seen[0], seen[0] + 30)))
        # derselbe Lauf noch einmal (weniger als ein halber Takt später): gleicher Index
        self.assertEqual(mem.run_number("a", now + timedelta(minutes=30)), seen[-1])
        # gespeichert und wieder geladen
        again = rotation.Memory(json.loads(json.dumps(mem.dump())))
        self.assertEqual(again.run_number("a", now + timedelta(hours=3)), seen[-1] + 1)
        # kaputter Zustand: aus der Uhrzeit, nie ein Absturz
        broken = rotation.Memory({"runs": {"a": {"n": "x"}}})
        self.assertEqual(broken.run_number("a", rot.NOW), rotation.run_index(rot.NOW))

    def test_full_cycle_without_gaps(self):
        """Läufe nahe der Grenze decken alle 180 (Strecke, Tag) ab."""
        store = FakeStateStore()
        alert = rot.oneway()
        boundary = datetime.fromtimestamp(rotation.run_index(rot.NOW) * 3 * 3600 + 3 * 3600)
        asked = set()
        for k in range(8):
            now = boundary + timedelta(hours=3 * k, seconds=20 if k % 2 else -20)
            src = rot.source(state_store=store, now=now, today=rot.TODAY)
            asked |= {(o, d, day) for o, d, day, _ in src.plan(alert)}
            src._save_memory(alert, 23)
        self.assertEqual(asked, set(rotation.items(rot.ROUTES, rot.TODAY, 90)))


class HealthRulesTest(unittest.TestCase):
    """Fund 13: Schwellen über den gemessenen Lücken."""

    def stat(self, hours):
        return {"items": 5, "parsed": 3, "newest": NOW - timedelta(hours=hours)}

    def test_night_gap_is_ok(self):
        self.assertEqual(health.classify(self.stat(15.2), health.rule_for("feed:secretflying"),
                                         NOW)[0], health.OK)
        self.assertEqual(health.classify(self.stat(21), health.rule_for("feed:secretflying"),
                                         NOW)[0], health.STALE)
        self.assertEqual(health.classify(self.stat(98.5), health.rule_for("feed:premium-flights"),
                                         NOW)[0], health.OK)


class Feed:
    name = "feeds"

    def __init__(self, stats):
        self.stats = stats


class Google:
    name = "google_flights"

    def __init__(self, made, empty, retries=0, down=None):
        self.requests_made, self.empty_fetches, self.retries, self.down = made, empty, retries, down


class HealthPruneTest(unittest.TestCase):
    """Fund 14: abgewählte Quellen fallen nach einem vollen Lauf heraus."""

    def test_complete_run_drops_unselected(self):
        ok = {"items": 5, "parsed": 3, "newest": NOW - timedelta(hours=1)}
        with Store(":memory:") as store:
            health.update(store, [Feed({"feed:secretflying": {"items": 0, "error": "HTTP 502"}}),
                                  Google(10, 0)], [], now=NOW)
            # Teil-Lauf (nur Travel-Dealz): alter Stand bleibt
            health.update(store, [Feed({"feed:travel-dealz": ok})], [], now=NOW)
            self.assertIn("feed:secretflying", ids(store))
            # voller Lauf ohne Secret Flying und ohne Google
            health.update(store, [Feed({"feed:travel-dealz": ok})], [],
                          now=NOW + timedelta(days=1), complete=True)
            self.assertEqual(ids(store), ["feed:travel-dealz"])

    def test_scheduler_marks_full_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed_src = Feed({"feed:travel-dealz": {"items": 5, "parsed": 3}})
            feed_src.records_history = False
            feed_src.fetch = lambda alert: []
            ctx = Context(tmp, Path(tmp) / "fehlt.toml", env={}, today=TODAY,
                          source_factory=lambda c, names: [feed_src] if "feeds" in names else [])
            with ctx.store() as store:
                store.insert_alert(normalize_alert({
                    "id": "zrh-ham", "name": "Zürich → Hamburg", "origins": ["ZRH"],
                    "destinations": ["HAM"], "max_price": 110}))
            with mock.patch("dealmonitor.health.update", return_value=[]) as upd:
                execute(ctx, notifiers=[])
                execute(ctx, mode="feeds", notifiers=[])
                execute(ctx, alert_id="zrh-ham", source_names=["feeds"], notifiers=[])
        self.assertEqual([c.kwargs["complete"] for c in upd.call_args_list], [True, False, False])
        self.assertEqual([c.kwargs["quick"] for c in upd.call_args_list], [False] * 3)


def ids(store):
    return [s["id"] for s in health.report(store, now=NOW)["sources"]]


class HotRunGoogleTest(unittest.TestCase):
    """Fund 15: Sofort-Prüfung (wenige Abfragen) stuft Google nicht als LEER ein."""

    def test_quick_run_keeps_google_state(self):
        with Store(":memory:") as store:
            health.update(store, [Google(40, 5)], [], now=NOW)
            errs = []
            sent = []
            rec = type("Rec", (), {"name": "rec", "send": lambda *a: None,
                                   "send_text": lambda s, t, x, **k: sent.append(t)})()
            errs += health.update(store, [Google(4, 2, retries=2)], [rec], now=NOW, quick=True)
            self.assertEqual(sent, [])
            self.assertEqual(health.report(store, now=NOW)["sources"][0]["state"], health.OK)
            # Schutzschalter zählt auch in der Sofort-Prüfung
            health.update(store, [Google(2, 0, down="gesperrt")], [rec], now=NOW, quick=True)
            self.assertEqual(health.report(store, now=NOW)["sources"][0]["state"], health.ERROR)
        self.assertEqual(errs, [])


class UntilTest(unittest.TestCase):
    """Fund 16 (Teil Daten): "Bis 12. November" endet am 12.11. um 23:59 deutscher Zeit."""

    def test_end_of_day_berlin(self):
        self.assertEqual(feed._until("Buchungszeitraum: Bis 12. November 2026"),
                         datetime(2026, 11, 12, 22, 59, tzinfo=timezone.utc))
        self.assertEqual(feed._until("Buchungszeitraum: Bis 16.07.2026"),
                         datetime(2026, 7, 16, 21, 59, tzinfo=timezone.utc))
        self.assertIs(efa.berlin_to_utc, feed.berlin_to_utc)


class NearDealsTest(unittest.TestCase):
    """Fund 17: kein Beinahe-Deal, wenn die Strecke billiger schon einen Treffer hat."""

    def entry(self, source, price, near=None, origin="FRA"):
        e = {"source": source, "route": f"{origin}-PEK", "origin": origin, "destination": "PEK",
             "price": price, "departure": "2027-03-01", "return_date": "2027-03-15",
             "cabin": "economy", "roundtrip": True}
        if near:
            e["near"] = near
        return e

    def test_cheaper_hit_hides_near(self):
        near = ["21 € über deiner Grenze", "30% unter dem Median 740 €"]
        out = web.near_deals_json([self.entry("google_flights", 450),
                                   self.entry("travelpayouts", 520, near),
                                   self.entry("travelpayouts", 560, near, origin="MUC")])
        self.assertEqual([(e["origin"], e["price"]) for e in out], [("MUC", 560)])


class RotationCoverTest(unittest.TestCase):
    """Fund 19 und 20: Rotations-Hinweis nur bei aktivem Alarm mit Google, Tage im Horizont."""

    def alert(self, **kw):
        base = dict(id="ow", name="x", origins=["ZRH"], destinations=["HAM"], max_price=45,
                    trip="oneway", rotation=True)
        return Alert(**{**base, **kw})

    def cover(self, alert, memory=None, **kw):
        return web.rotation_json(alert, [alert], memory or rotation.Memory(), today=TODAY,
                                 now=datetime(2026, 11, 4, 12), scan_months=6, **kw)

    def test_paused_or_without_google(self):
        self.assertIsNotNone(self.cover(self.alert()))
        self.assertIsNone(self.cover(self.alert(enabled=False)))
        self.assertIsNone(self.cover(self.alert(), google=False))

    def test_days_within_horizon(self):
        mem = rotation.Memory()
        row = {"price": 39.0, "airline": "Eurowings", "stops": 0}
        for n in range(1, 91):
            mem.put("ZRH", "HAM", "economy", TODAY + timedelta(days=n), [row], None,
                    datetime(2026, 11, 4, 11))
        self.assertEqual(self.cover(self.alert(months_ahead=1), mem),
                         {"horizon_days": 30, "days": 30, "no_deal_sites": True})

    def test_api_respects_google_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Context(Path(tmp) / "data", Path(tmp) / "fehlt.toml", env={},
                          source_factory=lambda c, n: [], today=TODAY)
            with ctx.store() as store:
                store.insert_alert(normalize_alert({
                    "id": "ow", "name": "Zürich → Hamburg one-way", "origins": ["ZRH"],
                    "destinations": ["HAM"], "trip": "oneway", "max_price": 45,
                    "rotation": True}))
            server = web.make_server(ctx, Scheduler(ctx, enabled=False), "127.0.0.1", 0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_address[1]}"

            def cover():
                with urllib.request.urlopen(base + "/api/alerts", timeout=10) as r:
                    return json.load(r)["alerts"][0]["rotation_cover"]
            try:
                self.assertTrue(cover())
                with ctx.store() as store:
                    AppSettings(store, {}).update({"sources": ["feeds"]})
                self.assertIsNone(cover())
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
