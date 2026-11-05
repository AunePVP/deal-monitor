"""Befunde aus dem Review (05.11.2026): Feed-Erkennung, Ablauf, Suche, Sicherheit.

Die Titel sind die im Review nachgestellten Fälle (echte bzw. realistische Überschriften).
"""

import hashlib
import io
import json
import logging
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from dealmonitor import http, regions
from dealmonitor.config import Config, Settings, load
from dealmonitor.models import Alert, Deal, Offer
from dealmonitor.monitor import run
from dealmonitor.notify import Ntfy, Telegram, Webhook, format_text, safe_link
from dealmonitor.principles import evaluate
from dealmonitor.radar import RadarSettings, check
from dealmonitor.report import _card
from dealmonitor.sources.feed import (
    PRESETS_BY_ID, FeedSource, analyze, detect_cabin, detect_origin, matches, origin_codes,
    parse_price,
)
from dealmonitor.store import Store

ROOT = Path(__file__).resolve().parent.parent
ALERTS = {a.id: a for a in load(ROOT / "config" / "alerts.toml").alerts}


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def item(title, preset="travel-dealz", cats=(), desc="", link=None, i=[0]):
    i[0] += 1
    p = PRESETS_BY_ID[preset]
    return {"title": title, "link": link or f"https://{preset}.example/deal/{i[0]}/",
            "published": datetime.now(timezone.utc), "categories": list(cats), "feed": "",
            "description": desc, "description_text": desc, "merchant_price": None,
            "profile": p.profile, "source": p.source}


def feed_with(*items) -> FeedSource:
    """FeedSource mit festen Artikeln (kein Netz)."""
    src = FeedSource(presets=["travel-dealz", "mydealz-fluege", "fly4free", "premium-flights"],
                     search_enabled=False)
    for it in items:
        it["key"] = it["link"].rstrip("/")
        it["preset"] = it["source"].removeprefix("feed:")
    src._items = list(items)
    return src


def offers_for(alert_id, *items):
    return feed_with(*items).fetch(ALERTS[alert_id])


def is_deal(alert_id, offer):
    return evaluate(ALERTS[alert_id], offer, [], today=date(2026, 11, 5)) is not None


class CabinTest(unittest.TestCase):
    def test_first_alone_is_not_first_class(self):
        for title in ("First Minute: Flüge nach Mallorca inkl. Rückflug ab 49€ von Hamburg",
                      "For the first time: direct flights from Frankfurt to Lima from €499"):
            self.assertEqual(detect_cabin(title), "economy", title)
        info = analyze(item("First Minute: Flüge nach Mallorca inkl. Rückflug ab 49€ von Hamburg",
                            "mydealz-fluege"))
        self.assertEqual(info.cabin, "economy")
        it = item("For the first time: direct flights from Frankfurt to Lima from €499",
                  "fly4free", cats=["cheap flights from frankfurt"])
        self.assertNotIn("First Class ≤ 2.500 €", check(it, RadarSettings()) or [])
        for title in ("Lufthansa First Class nach Tokio ab 2.499€", "First-Class nach Dubai ab 1.999€",
                      "Swiss First ab 3.999€ nach Tokio", "Mit Lufthansa in der First nach Peking"):
            self.assertEqual(detect_cabin(title), "first", title)

    def test_businessclass_and_premium_with_hyphen(self):
        cases = {"Peking: Businessclass mit Lufthansa ab 1.199€": "business",
                 "Mit Qatar in der Businessclass nach Bangkok ab 1.199€ von Frankfurt": "business",
                 "Lufthansa Premium-Economy nach Tokio ab 599€": "premium",
                 "Condor Premium-Class nach New York ab 640€": "premium"}
        for title, cabin in cases.items():
            self.assertEqual(analyze(item(title, "mydealz-fluege")).cabin, cabin, title)
        # de-cn-business bekommt den Peking-Deal (Kabine Business)
        offers = offers_for("de-cn-business", item("Peking: Businessclass mit Lufthansa ab 1.199€",
                                                   "mydealz-fluege"))
        self.assertEqual([(o.cabin, o.price) for o in offers], [("business", 1199)])
        self.assertTrue(is_deal("de-cn-business", offers[0]))
        it = item("Condor Premium-Class nach New York ab 640€", "mydealz-fluege")
        self.assertEqual(check(it, RadarSettings()), ["Premium Economy ≤ 700 €"])


class OriginTest(unittest.TestCase):
    def test_airline_names_are_not_origins(self):
        self.assertEqual(detect_origin("Peking: Business Class von Air China ab 1.199€"), "unknown")
        self.assertEqual(detect_origin("Singapore Airlines – Business Class nach Bali ab 1.099€"),
                         "unknown")
        offers = offers_for("de-cn-business", item(
            "Peking: Business Class von Air China ab 1.199€", cats=["Business Class"]))
        self.assertEqual([o.price for o in offers], [1199])
        it = item("Singapore Airlines – Business Class nach Bali ab 1.099€", cats=["Business Class"])
        self.assertEqual(check(it, RadarSettings()), ["Business Class ≤ 1.300 €"])

    def test_dash_without_place_behind_is_no_route(self):
        self.assertEqual(detect_origin("Shanghai - Flüge inkl. Rückflug mit Lufthansa ab 449€"),
                         "unknown")
        offers = offers_for("de-cn-economy", item(
            "Shanghai - Flüge inkl. Rückflug mit Lufthansa ab 449€", "mydealz-fluege"))
        self.assertEqual([o.price for o in offers], [449])
        self.assertTrue(is_deal("de-cn-economy", offers[0]))
        it = item("New York - Flüge inkl. Rückflug ab 249€", "mydealz-fluege")
        self.assertEqual(check(it, RadarSettings()), ["Economy ≤ 300 €"])
        # echte Strecken bleiben erkannt
        self.assertEqual(detect_origin("Swiss: Zürich – Hamburg ab 79€"), "home")
        self.assertEqual(origin_codes("Bonaire ab 416€ von Amsterdam"), {"AMS"})
        self.assertEqual(detect_origin("Paris → Maldives Business Class Round Trip 1680€", "en"),
                         "foreign")


class CantonTest(unittest.TestCase):
    def test_swiss_canton_and_can_are_not_guangzhou(self):
        for title in ("Kanton Zürich: Flüge nach Mallorca ab 49€ von Zürich",
                      "Kanton Graubünden: Flüge nach Bangkok ab 449€ von Zürich"):
            self.assertEqual(offers_for("de-cn-economy", item(title, cats=["Economy Class"])), [],
                             title)
        it = item("YES YOU CAN! Cheap flights from Germany to Bali from €449", "fly4free",
                  cats=["cheap flights from germany"])
        self.assertEqual(offers_for("de-cn-economy", it), [])
        # Kanton = Guangzhou und der Code im Strecken-Kontext zählen weiter
        a = ALERTS["de-cn-economy"]
        self.assertTrue(matches(a, "Flüge nach Kanton ab 499€"))
        self.assertTrue(matches(a, "FRA-CAN ab 399€", [["Guangzhou"]]))
        self.assertFalse(regions.places_in("YES YOU CAN") & {"Guangzhou"})

    def test_swiss_departure_not_for_german_alert(self):
        offers = offers_for("de-cn-economy", item("Peking ab 449€ von Zürich",
                                                  cats=["Economy Class"]))
        self.assertEqual(offers, [])
        offers = offers_for("de-cn-economy", item("Peking ab 449€ von Frankfurt",
                                                  cats=["Economy Class"]))
        self.assertEqual([o.price for o in offers], [449])


class SoftExclusionTest(unittest.TestCase):
    def test_flight_deals_with_miles_or_nights(self):
        for title in ("Erbil, Irak ab 390€ in der Eurowings „Business Class“ "
                      "(inkl. 400 Miles&More Statuspunkten)",
                      "Lufthansa Business Class nach New York ab 1.199€ + 5.000 Meilen geschenkt",
                      "Flüge nach Bangkok mit Thai inkl. Gepäck & 14 Nächte frei wählbar ab 449€"):
            self.assertTrue(analyze(item(title, "mydealz-fluege")).flight, title)

    def test_non_flights_still_excluded(self):
        for title in ("Miles & More: 5.000 Meilen geschenkt",
                      "7 Nächte Mallorca inkl. Flug ab 299€",
                      "AIDA Kreuzfahrt ab 499€", "Hotel in Rom ab 49€",
                      "Status-Match: Gold bei Turkish"):
            self.assertFalse(analyze(item(title, "mydealz-fluege")).flight, title)


class ImpliedOriginTest(unittest.TestCase):
    def test_single_city_alert_needs_both_words(self):
        it = item("Zürich: Oneway-Flüge mit easyJet ab 29€ (Dez - Feb)", cats=["Economy Class"])
        self.assertEqual(offers_for("zrh-ham-oneway", it), [])
        # Region (@DE): Travel-Dealz ohne Abflugangabe = Deutschland, Ziel-Wort reicht
        it = item("Peking ab 429€ hin und zurück", cats=["Economy Class"])
        self.assertEqual([o.price for o in offers_for("de-cn-economy", it)], [429])


class PriceTest(unittest.TestCase):
    def test_comma_thousands_and_dash_decimals(self):
        self.assertEqual(parse_price("Frankfurt → Bangkok Business Class Round Trip from 1,210€",
                                     "en"), 1210)
        self.assertEqual(parse_price("Vienna → New Delhi Business Class Round Trip 1,830€", "en"),
                         1830)
        self.assertEqual(parse_price("1,299€", "de"), 1299)
        self.assertEqual(parse_price("ab 1.299,–€", "de"), 1299)
        self.assertEqual(parse_price("from €1.299", "en"), 1299)
        # unverändert
        self.assertEqual(parse_price("ab 39,99€"), 39.99)
        self.assertEqual(parse_price("ab 89,- €"), 89)
        self.assertEqual(parse_price("from €1,299", "en"), 1299)
        it = item("Frankfurt → Bangkok Business Class Round Trip from 1,210€", "premium-flights",
                  cats=["Cheap Business Class flights"])
        self.assertEqual(analyze(it).price, 1210)
        self.assertEqual(check(it, RadarSettings()), ["Business Class ≤ 1.300 €"])


class LinkSafetyTest(unittest.TestCase):
    XML = (b'<rss><channel><item><title>Peking ab 429\xe2\x82\xac</title>'
           b'<link>javascript:alert(1)</link></item></channel></rss>')

    def test_only_http_links(self):
        items = FeedSource().parse(self.XML)
        self.assertEqual(items[0]["link"], "")
        self.assertIsNone(safe_link("javascript:alert(1)"))
        self.assertEqual(safe_link("https://x.example/a"), "https://x.example/a")
        a = Alert(id="a", name="A", origins=["ZRH"], destinations=["HAM"], max_price=150)
        d = evaluate(a, Offer(source="feed:custom", origin="ZRH", destination="HAM", price=79,
                              link="javascript:alert(1)", title="Zürich – Hamburg ab 79€"), [])
        self.assertNotIn("javascript", format_text(d, "new"))
        card = _card({"status": "active", "rating": "Deal", "alert_id": "a", "source": "feed:x",
                      "first_seen": "2026-11-05T10:00", "headline": "x", "price": 79,
                      "link": "javascript:alert(1)"})
        self.assertNotIn("javascript", card)
        sent = []
        with mock.patch.object(http, "request", side_effect=lambda *a, **k: sent.append(k)):
            Webhook("https://hook.example/").send("A", [(d, "new")])
            Ntfy("t").send("A", [(d, "new")])
            Telegram("123456:test-dummy", "1").send("A", [(d, "new")])
        self.assertNotIn(b"javascript", b"".join(k["data"] for k in sent))


class FeedErrorTest(unittest.TestCase):
    def test_credentials_redacted_and_no_body(self):
        url = "https://user:test-pw@feeds.example/rss?token=test-dummy-token"
        src = FeedSource(presets=[], extra_feeds=[url, "https://ok.example/feed"])
        calls = []

        def fake(u, **kw):
            calls.append((u, kw))
            if "feeds.example" in u:
                raise http.HttpError(f"HTTP 404 für {http.redact(u)}", 404)
            return b"<rss><channel></channel></rss>"

        with mock.patch.object(http, "request", side_effect=fake):
            src.load()
        self.assertNotIn("test-pw", src.load_errors)
        self.assertNotIn("test-dummy-token", src.load_errors)
        kw = dict(calls[0][1])
        self.assertEqual((kw["error_body"], kw["public_only"]), (False, True))
        self.assertLessEqual(kw["max_bytes"], 5_000_000)

    def test_duplicate_feed_url_loaded_once(self):
        src = FeedSource(presets=["travel-dealz"], extra_feeds=["https://travel-dealz.de/feed/",
                                                                "https://travel-dealz.de/feed"])
        urls = [u for u, _ in src._feed_jobs()]
        self.assertEqual(len(urls), 5)
        self.assertEqual(len(set(urls)), 5)

    def test_internal_feed_urls_rejected(self):
        from dealmonitor.appsettings import coerce
        for bad in ("http://localhost:8080/feed", "http://127.0.0.1/feed", "http://10.0.0.5/rss",
                    "http://192.168.1.10/rss", "http://[::1]/rss", "http://169.254.169.254/latest",
                    "http://nas.local/rss", "http://router/rss"):
            with self.assertRaises(ValueError, msg=bad):
                coerce("feeds", [bad])
            with self.assertRaises(http.HttpError, msg=bad):
                http.check_public(bad)
        self.assertEqual(coerce("feeds", ["https://example.org/feed"]), ["https://example.org/feed"])
        # Webhook/ntfy dürfen ins eigene Netz (eigener Benachrichtigungsserver)
        self.assertEqual(coerce("webhook_url", "http://192.168.1.10:8000/hook"),
                         "http://192.168.1.10:8000/hook")

    def test_dns_resolving_to_private_address_rejected(self):
        with mock.patch("urllib.request.getproxies", return_value={}), \
                mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.1.2.3", 443))]):
            with self.assertRaises(http.HttpError) as cm:
                http.check_public("https://evil.example/feed")
        self.assertIn("10.1.2.3", str(cm.exception))


class SlowServer(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "100000000")
        self.end_headers()
        try:
            if self.path == "/big":
                for _ in range(200):
                    self.wfile.write(b"x" * 65536)
            else:
                for _ in range(30):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.2)
        except OSError:
            pass

    def log_message(self, *a):
        pass


class HttpLimitTest(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), SlowServer)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_total_time_and_size_limits(self):
        t0 = time.monotonic()
        with self.assertRaises(http.HttpError) as cm:
            http.request(self.base + "/slow", timeout=2, retries=0, max_time=1.0)
        self.assertLess(time.monotonic() - t0, 3)
        self.assertIn("dauert zu lange", str(cm.exception))
        with self.assertRaises(http.HttpError) as cm:
            http.request(self.base + "/big", timeout=5, retries=0, max_bytes=1_000_000)
        self.assertIn("zu groß", str(cm.exception))


class ExpiryTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def deal(self, source, title="Peking ab 429€", link="https://travel-dealz.de/peking/"):
        a = ALERTS["de-cn-economy"]
        return evaluate(a, Offer(source=source, origin="", destination="", price=429, link=link,
                                 title=title), [], today=date(2026, 11, 5))

    def test_legacy_feed_source_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deals.sqlite"
            with Store(path) as st:
                st.upsert_deal(self.deal("travel-dealz-feed"), 0.05)
            # Update auf die neue Version: Quelle + Fingerprint werden umgeschrieben
            from dealmonitor import store as store_mod
            store_mod._READY.discard(str(path))
            with Store(path) as st:
                rows = st.deals()
                self.assertEqual([r["source"] for r in rows], ["feed:travel-dealz"])
                new = self.deal("feed:travel-dealz")
                self.assertEqual(rows[0]["fingerprint"], new.fingerprint)
                self.assertEqual(st.upsert_deal(new, 0.05), "known", "nicht erneut als NEU")

    def test_stale_articles_expire(self):
        old = datetime.now() - timedelta(days=20)
        self.store.upsert_deal(self.deal("feed:urlaubspiraten"), 0.05, now=old)
        self.store.upsert_deal(self.deal("feed:travel-dealz", link="https://x/neu"), 0.05)
        tp = evaluate(ALERTS["zrh-ham"], Offer(source="travelpayouts", origin="ZRH",
                                                destination="HAM", price=89, stops=0,
                                                departure=date(2027, 1, 15),
                                                return_date=date(2027, 1, 19)), [],
                      today=date(2026, 11, 5))
        self.store.upsert_deal(tp, 0.05, now=old)
        self.assertEqual(self.store.expire_stale_articles(14), 1)
        active = {d["source"] for d in self.store.deals(status="active")}
        self.assertEqual(active, {"feed:travel-dealz", "travelpayouts"})

    def test_failed_search_keeps_its_radar_deals(self):
        hit = item("Error Fare: Peking ab 299€", "travel-dealz-suche", cats=["Economy Class"],
                   link="https://travel-dealz.de/error-peking/")

        class Src(FeedSource):
            fail = False

            def _fetch_one(inner, url):
                if "feed=rss2" in url:
                    if inner.fail:
                        raise http.HttpError("HTTP 503", 503)
                    return [dict(hit)]
                return []

        c = Config(settings=Settings(), alerts=[ALERTS["zrh-ham"]], path=Path("x"))
        src = Src(presets=["travel-dealz", "travel-dealz-suche"])
        run(c, [src], [], self.store, radar_settings=RadarSettings())
        self.assertEqual(len(self.store.deals(status="active", alert_id="radar")), 1)
        src = Src(presets=["travel-dealz", "travel-dealz-suche"])
        src.fail = True
        r = run(c, [src], [], self.store, radar_settings=RadarSettings())
        self.assertEqual(len(self.store.deals(status="active", alert_id="radar")), 1)
        self.assertTrue(any("Suche" in e for e in r.errors))

    def test_skipped_search_terms_reported(self):
        src = FeedSource(presets=["travel-dealz-suche"], max_searches=1)
        with mock.patch.object(http, "request", return_value=b"<rss><channel></channel></rss>"):
            src.fetch(ALERTS["de-cn-economy"])
        self.assertIn("nicht gesucht: Peking", src.incomplete["message"])
        self.assertEqual(src.incomplete["sources"], {"feed:travel-dealz-suche"})


class SettingsMigrationTest(unittest.TestCase):
    def test_old_main_settings_migrated_once(self):
        from dealmonitor.appsettings import AppSettings, Context, migrate_settings
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Context(tmp, ROOT / "config" / "alerts.toml", env={})
            with ctx.store() as st:            # so speicherte die alte Version (main)
                st.kv_set("seeded", True)
                st.kv_set("sources", ["travelpayouts", "feed"])
                st.kv_set("feeds", ["https://travel-dealz.de/feed/", "https://example.org/rss"])
            ctx.seed()
            with ctx.store() as st:
                app = AppSettings(st, {})
                self.assertEqual(app.get("sources"), ["travelpayouts", "google_flights", "feeds"])
                self.assertEqual(app.get("feeds"), ["https://example.org/rss"])
                # Nutzer schaltet Google danach bewusst ab -> bleibt aus (Migration nur einmal)
                app.update({"sources": ["travelpayouts", "feeds"]})
                self.assertEqual(migrate_settings(st), [])
            ctx.seed()
            with ctx.store() as st:
                self.assertEqual(AppSettings(st, {}).get("sources"), ["travelpayouts", "feeds"])

    def test_fresh_install_untouched(self):
        from dealmonitor.appsettings import AppSettings, Context
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Context(tmp, ROOT / "config" / "alerts.toml", env={})
            ctx.seed()
            with ctx.store() as st:
                self.assertEqual(AppSettings(st, {}).get("sources"),
                                 ["travelpayouts", "google_flights", "feeds"])
                self.assertEqual(st.kv_get("settings_version"), 2)


class StatusAndScheduleTest(unittest.TestCase):
    def setUp(self):
        from dealmonitor.appsettings import Context
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = Context(self.tmp.name, Path(self.tmp.name) / "fehlt.toml", env={},
                           source_factory=lambda c, names: [])

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_feed_cycle_without_deal_sites(self):
        from dealmonitor.appsettings import AppSettings
        from dealmonitor.scheduler import Scheduler, execute
        s = Scheduler(self.ctx)
        self.assertIsNotNone(s.next_feed_run())
        with self.ctx.store() as st:
            AppSettings(st, {}).update({"feed_presets": [], "feeds": []})
        self.assertIsNone(s.next_feed_run(), "ohne Deal-Seite kein Feed-Takt")
        row = execute(self.ctx, mode="feeds")
        self.assertEqual(row["errors"], ["Keine Deal-Seite ausgewählt (Einstellungen → Quellen → "
                                         "Deal-Seiten)"])

    def test_full_run_errors_survive_feed_runs(self):
        from dealmonitor.scheduler import Scheduler
        with self.ctx.store() as st:
            rid = st.start_run("schedule")
            st.finish_run(rid, errors=["[zrh-ham] google_flights: Google Flights blockiert gerade"])
            rid = st.start_run("feeds")
            st.finish_run(rid)
        status = Scheduler(self.ctx, enabled=False).status()
        self.assertEqual(status["last_run"]["trigger"], "feeds")
        self.assertEqual(status["last_full_run"]["errors"],
                         ["[zrh-ham] google_flights: Google Flights blockiert gerade"])


class DryRunTest(unittest.TestCase):
    def test_dry_run_leaves_database_untouched(self):
        from dealmonitor import cli
        from dealmonitor.store import Store as S
        deal = ExpiryTest.deal(ExpiryTest(), "feed:travel-dealz")
        seen_dbs = []

        def fake_execute(ctx, alert_id, trigger, **kw):
            seen_dbs.append(ctx.db_path)
            with ctx.store() as st:
                self.assertTrue(st.alert_rows(), "Kopie enthält die Alarme")
                st.upsert_deal(deal, 0.05)
            return {"offers": 1, "deals": 1, "notified": 1, "errors": []}

        with tempfile.TemporaryDirectory() as tmp:
            toml = str(ROOT / "config" / "alerts.toml")
            with mock.patch.object(cli, "execute", side_effect=fake_execute), \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(cli.main(["--data", tmp, "-c", toml, "run", "--dry-run"]), 0)
            self.assertIn("Probelauf", out.getvalue())
            self.assertNotEqual(seen_dbs[0], Path(tmp) / "deals.sqlite")
            self.assertFalse(seen_dbs[0].exists(), "Kopie wird aufgeräumt")
            if (Path(tmp) / "deals.sqlite").exists():
                with S(Path(tmp) / "deals.sqlite") as st:
                    self.assertEqual(st.deals(), [])
            # ohne --dry-run landet der Deal in der echten Datenbank
            with mock.patch.object(cli, "execute", side_effect=fake_execute), \
                    mock.patch("sys.stdout", new_callable=io.StringIO):
                cli.main(["--data", tmp, "-c", toml, "run"])
            with S(Path(tmp) / "deals.sqlite") as st:
                self.assertEqual(len(st.deals()), 1)
            # Probelauf nach echtem Lauf: Kopie hat den Deal, echte DB bleibt bei einem
            with mock.patch.object(cli, "execute", side_effect=fake_execute), \
                    mock.patch("sys.stdout", new_callable=io.StringIO):
                cli.main(["--data", tmp, "-c", toml, "run", "--dry-run"])
            with S(Path(tmp) / "deals.sqlite") as st:
                self.assertEqual(len(st.deals()), 1)


class BudgetPlanningTest(unittest.TestCase):
    def test_run_distributes_google_budget(self):
        from dealmonitor.sources.googleflights import GoogleFlightsSource
        page = (ROOT / "tests" / "fixtures" / "google_flights_zrh_ham.html").read_text()
        g = GoogleFlightsSource(max_requests=8, delay=0, today=date(2026, 11, 4),
                                fetcher=lambda url: page, seed=1, verify_reserve=2)
        alerts = [Alert(id=f"a{i}", name="x", origins=[o], destinations=[d], max_price=50,
                        months_ahead=6) for i, (o, d) in enumerate([("ZRH", "HAM"),
                                                                    ("HAM", "ZRH"),
                                                                    ("FRA", "SHA")])]
        c = Config(settings=Settings(), alerts=alerts, path=Path("x"))
        with Store(":memory:") as st:
            r = run(c, [g], [], st)
        self.assertEqual(g._shares, {"a0": 2, "a1": 2, "a2": 2})
        self.assertEqual(g.requests_made, 6)
        self.assertEqual(len([e for e in r.errors if "2 von 12 Abfragen (Limit)" in e]), 3)


if __name__ == "__main__":
    unittest.main()
