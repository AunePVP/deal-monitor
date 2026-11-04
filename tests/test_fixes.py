"""Regressionstests für bestätigte Fehler (Teilausfälle, Token-Lecks, CSRF, Flackern …)."""

import json
import logging
import os
import stat
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from dealmonitor import config as config_mod
from dealmonitor import http, notify
from dealmonitor.appsettings import AppSettings, Context, import_config
from dealmonitor.models import Alert, Offer
from dealmonitor.monitor import run
from dealmonitor.sources import SourceError
from dealmonitor.sources.feed import FeedSource, parse_price
from dealmonitor.sources.travelpayouts import TravelpayoutsSource
from dealmonitor.store import Store

from test_web import ServerTest

TODAY = date(2026, 11, 4)
TOML = ('[settings]\ndatabase = "db.sqlite"\n'
        '[[alert]]\nid = "ch-ham"\nname = "Schweiz → Hamburg"\norigins = ["ZRH", "BSL"]\n'
        'destinations = ["HAM"]\nmax_price = 150\nbaseline_price = 190\n')


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def offer(price=99, origin="ZRH", day=15, month=1):
    return Offer(source="travelpayouts", origin=origin, destination="HAM", price=price,
                 airline="LX", stops=0, departure=date(2027, month, day),
                 return_date=date(2027, month, day + 4))


class Collect:
    name = "collect"

    def __init__(self):
        self.sent = []

    def send(self, name, items):
        self.sent += [(d.offer.origin, d.offer.price, s) for d, s in items]


class Scripted:
    """Quelle mit festen Antworten pro Lauf; ``incomplete`` wie bei Travelpayouts."""

    name = "travelpayouts"
    records_history = True

    def __init__(self, offers, incomplete=None, exc=None):
        self.offers, self.incomplete_value, self.exc = offers, incomplete, exc
        self.incomplete = None

    def fetch(self, a):
        self.incomplete = self.incomplete_value
        if self.exc:
            raise self.exc
        return [o for o in self.offers if (o.origin, o.destination) in a.pairs]


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "alerts.toml"
        path.write_text(TOML, encoding="utf-8")
        self.cfg = config_mod.load(path)
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_partial_failure_keeps_failed_routes(self):
        n = Collect()
        run(self.cfg, [Scripted([offer(99, "ZRH"), offer(120, "BSL")])], [n], self.store,
            today=TODAY)
        self.assertEqual(sorted(x[0] for x in n.sent), ["BSL", "ZRH"])
        # Lauf 2: BSL-Anfragen scheitern (z. B. 429) -> BSL-Deal darf nicht ablaufen
        part = {"message": "6 von 12 Anfragen fehlgeschlagen", "routes": {("BSL", "HAM")}}
        res = run(self.cfg, [Scripted([offer(99, "ZRH")], part)], [n], self.store, today=TODAY)
        self.assertEqual(res.expired, 0)
        self.assertEqual(res.errors, ["[ch-ham] travelpayouts: 6 von 12 Anfragen fehlgeschlagen"])
        self.assertEqual(len(self.store.deals(status="active")), 2)
        # Lauf 3: alles wieder da -> keine erneute Meldung "WIEDER DA"
        run(self.cfg, [Scripted([offer(99, "ZRH"), offer(120, "BSL")])], [n], self.store,
            today=TODAY)
        self.assertEqual(len(n.sent), 2)

    def test_partial_failure_without_routes_expires_nothing(self):
        run(self.cfg, [Scripted([offer(99, "ZRH")])], [Collect()], self.store, today=TODAY)
        res = run(self.cfg, [Scripted([], {"message": "1 von 2 Feeds nicht lesbar",
                                           "routes": None})],
                  [Collect()], self.store, today=TODAY)
        self.assertEqual(res.expired, 0)

    def test_travelpayouts_reports_failed_pairs(self):
        a = Alert(id="a", name="A", origins=["ZRH", "BSL"], destinations=["HAM"], max_price=150)

        def fake(url, params, **kw):
            if params["origin"] == "BSL":
                raise http.HttpError("HTTP 429", 429)
            return {"success": True, "data": []}

        src = TravelpayoutsSource(token="t", scan_months=2, request_delay=0, today=TODAY)
        with mock.patch.object(http, "get_json", side_effect=fake):
            src.fetch(a)
        self.assertEqual(src.incomplete["routes"], {("BSL", "HAM")})
        self.assertEqual(src.incomplete["message"], "2 von 4 Anfragen fehlgeschlagen")

    def test_circuit_breaker(self):
        a = Alert(id="a", name="A", origins=["@DE"], destinations=["HAM"], max_price=150)
        calls = []

        def down(url, **kw):
            calls.append(kw.get("retries"))
            raise http.HttpError("Anfrage fehlgeschlagen (timed out)", network=True)

        src = TravelpayoutsSource(token="t", scan_months=6, request_delay=0, today=TODAY)
        with mock.patch.object(http, "get_json", side_effect=down):
            with self.assertRaises(SourceError) as cm:
                src.fetch(a)
            self.assertIn("nicht erreichbar", str(cm.exception))
            self.assertEqual(len(calls), TravelpayoutsSource.MAX_CONSECUTIVE_FAILURES)
            self.assertEqual(set(calls), {1})
            with self.assertRaises(SourceError):   # nächster Alarm: sofort, ohne Anfrage
                src.fetch(a)
        self.assertEqual(len(calls), TravelpayoutsSource.MAX_CONSECUTIVE_FAILURES)

    def test_unexpected_exception_does_not_abort_run(self):
        path = Path(self.tmp.name) / "zwei.toml"
        path.write_text(TOML + '[[alert]]\nid = "zrh-ham"\nname = "Z"\norigins = ["ZRH"]\n'
                        'destinations = ["HAM"]\nmax_price = 150\n', encoding="utf-8")
        cfg = config_mod.load(path)

        class Flaky(Scripted):
            def fetch(self, a):
                if a.id == "ch-ham":
                    raise json.JSONDecodeError("Expecting value", "<html>", 0)
                return super().fetch(a)

        n = Collect()
        res = run(cfg, [Flaky([offer(99, "ZRH")])], [n], self.store, today=TODAY)
        self.assertEqual(n.sent, [("ZRH", 99, "new")])
        self.assertEqual(len(res.errors), 1)
        self.assertIn("unerwarteter Fehler", res.errors[0])

    def test_flapping_within_slot(self):
        """Zwei Termine im selben Monat, der günstigste wechselt -> nicht ständig melden."""
        n = Collect()
        for a_price in (99, 101, 99, 101):
            offers = [offer(a_price, day=3, month=12), offer(100, day=10, month=12)]
            run(self.cfg, [Scripted(offers)], [n], self.store, today=TODAY)
        self.assertLessEqual(len(n.sent), 2, n.sent)
        self.assertNotIn("revived", [s for _, _, s in n.sent])

    def test_departed_deals_expire_even_if_paused(self):
        run(self.cfg, [Scripted([offer(99, "ZRH", day=15, month=1)])], [Collect()], self.store,
            today=TODAY)
        for a in self.cfg.alerts:
            a.enabled = False
        res = run(self.cfg, [Scripted([], exc=SourceError("offline"))], [Collect()], self.store,
                  today=date(2027, 1, 15))
        self.assertEqual(res.expired, 1)
        self.assertEqual(self.store.deals(status="active"), [])


class FeedTest(unittest.TestCase):
    XML = ('<rss><channel><item><title>Zürich nach Hamburg ab 59€</title>'
           '<link>https://travel-dealz.de/a/</link><pubDate>{}</pubDate></item>'
           '</channel></rss>')

    def test_pubdate_minus_0000(self):
        pub = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%a, %d %b %Y %H:%M:%S -0000")
        items = FeedSource().parse(self.XML.format(pub).encode())
        self.assertEqual(len(items), 1)
        self.assertIsNotNone(items[0]["published"].tzinfo)

    def test_partial_feed_failure(self):
        good = self.XML.format("").encode()

        def fake(url, **kw):
            if "kaputt" in url:
                raise http.HttpError("HTTP 500", 500)
            return good

        a = Alert(id="a", name="A", origins=["ZRH"], destinations=["HAM"], max_price=150,
                  feed_keywords=[["Zürich", "Hamburg"]])
        src = FeedSource(["https://ok.example/feed", "https://kaputt.example/feed"])
        with mock.patch.object(http, "request", side_effect=fake):
            offers = src.fetch(a)
        self.assertEqual(len(offers), 1)
        self.assertIn("1 von 2 Feeds", src.incomplete["message"])
        self.assertIsNone(src.incomplete["routes"])

    def test_unexpected_parse_error_becomes_source_error(self):
        src = FeedSource(["https://x.example/feed"])
        with mock.patch.object(http, "request", return_value=b"<rss/>"), \
                mock.patch.object(FeedSource, "parse", side_effect=TypeError("kaputt")):
            with self.assertRaises(SourceError):
                src.load()

    def test_price_parsing(self):
        self.assertIsNone(parse_price("Swiss: 30€ Rabatt auf Flüge ab Zürich nach Hamburg"))
        self.assertIsNone(parse_price("Bis zu 100€ Gutschein für Flüge nach Shanghai"))
        self.assertEqual(parse_price("Hamburg ab 39,99€"), 39.99)
        self.assertEqual(parse_price("New York ab 399€ inkl. 50€ Rabatt"), 399)
        self.assertEqual(parse_price("Spare 50€: Rom ab 99€"), 99)
        self.assertEqual(parse_price("Peking für 1.299 €"), 1299)


class MergeTest(unittest.TestCase):
    def test_merged_article_both_directions_has_no_route(self):
        from dealmonitor.report import _merge_articles
        rows = [{"source": "travel-dealz-feed", "link": "https://t/x", "alert_id": "zrh-ham",
                 "alert_name": "ZRH → HAM", "origin": "ZRH", "destination": "HAM"},
                {"source": "travel-dealz-feed", "link": "https://t/x", "alert_id": "ham-zrh",
                 "alert_name": "HAM → ZRH", "origin": "HAM", "destination": "ZRH"}]
        merged = _merge_articles(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0]["origin"], merged[0]["destination"]), ("", ""))
        self.assertEqual(merged[0]["alert_name"], "ZRH → HAM · HAM → ZRH")


class HttpTest(unittest.TestCase):
    def test_invalid_json_is_http_error(self):
        with mock.patch.object(http, "request", return_value=b"<html>Wartung</html>"):
            with self.assertRaises(http.HttpError):
                http.get_json("https://x.example/api")

    def test_incomplete_read_is_retried_and_wrapped(self):
        from http.client import IncompleteRead
        with mock.patch("urllib.request.urlopen", side_effect=IncompleteRead(b"")), \
                mock.patch("time.sleep"):
            with self.assertRaises(http.HttpError) as cm:
                http.request("https://x.example/", retries=1)
        self.assertTrue(cm.exception.network)

    def test_redact(self):
        r = http._redact("https://user:pw@api.telegram.org/bot123:SECRET/sendMessage?token=abc&x=1")
        self.assertNotIn("SECRET", r)
        self.assertNotIn("pw", r)
        self.assertNotIn("abc", r)
        self.assertIn("x=1", r)

    def test_telegram_token_not_in_test_notify(self):
        token = "999999:SECRETTOKENabcdefgh"
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("Name or service not known")), \
                mock.patch("time.sleep"):
            res = notify.send_test([notify.Telegram(token, "42")])
        self.assertFalse(res[0]["ok"])
        self.assertNotIn("SECRETTOKEN", res[0]["error"])


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "db.sqlite")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_webhook_url_change_needs_token(self):
        app = AppSettings(self.store, {})
        app.update({"webhook_url": "https://mein.example/hook"}, {"webhook_token": "geheim"})
        with self.assertRaises(ValueError):
            app.update({"webhook_url": "http://127.0.0.1:18799/x"})
        self.assertEqual(app.get("webhook_url"), "https://mein.example/hook")
        app.update({"webhook_url": "https://mein.example/hook"})          # unverändert: ok
        app.update({"webhook_url": "https://neu.example/"}, {"webhook_token": "neu"})
        self.assertEqual(app.get("webhook_token"), "neu")
        app.update({"webhook_url": "https://ohne.example/"}, {"webhook_token": ""})
        self.assertIsNone(app.get("webhook_token"))

    def test_env_token_locks_url(self):
        app = AppSettings(self.store, {"WEBHOOK_TOKEN": "env-geheim"})
        self.assertIn("webhook_url", app.env_locked())
        app.update({"webhook_url": "http://evil.example/"})
        self.assertEqual(app.get("webhook_url"), "")

    def test_renotify_drop_error_in_percent(self):
        with self.assertRaises(ValueError) as cm:
            AppSettings(self.store, {}).update({"renotify_drop": 0.95})
        self.assertIn("90 %", str(cm.exception))

    def test_settings_in_toml(self):
        path = Path(self.tmp.name) / "alerts.toml"
        path.write_text('[settings]\ninterval_hours = 6\nsources = ["feed"]\nscan_months = 3\n'
                        '[[alert]]\nid = "a"\nname = "A"\norigins = ["ZRH"]\n'
                        'destinations = ["HAM"]\nmax_price = 100\n', encoding="utf-8")
        import_config(self.store, path)
        app = AppSettings(self.store, {})
        # "feed" ist der alte Name der Deal-Seiten-Quelle - wird zu "feeds" normalisiert
        self.assertEqual((app.get("interval_hours"), app.get("sources"), app.get("scan_months")),
                         (6.0, ["feeds"], 3))
        path.write_text('[settings]\ngibtsnicht = 1\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            config_mod.load_raw(path)
        path.write_text('[settings]\ninterval_hours = 0.1\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            config_mod.load_raw(path)

    def test_airline_codes_validated(self):
        base = {"id": "a", "name": "A", "origins": ["ZRH"], "destinations": ["HAM"],
                "max_price": 100}
        with self.assertRaises(ValueError) as cm:
            config_mod.normalize_alert(dict(base, exclude_airlines=["Ryanair"]))
        self.assertIn("IATA", str(cm.exception))
        self.assertEqual(config_mod.normalize_alert(dict(base, exclude_airlines="fr, w6"))
                         ["exclude_airlines"], ["FR", "W6"])

    def test_error_messages_use_labels(self):
        base = {"id": "a", "name": "A", "origins": ["ZRH"], "destinations": ["HAM"],
                "max_price": 100}
        with self.assertRaises(ValueError) as cm:
            config_mod.normalize_alert(dict(base, max_stops=5))
        self.assertIn("Max. Umstiege", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            config_mod.normalize_alert(dict(base, min_discount=0))
        self.assertIn("%", str(cm.exception))

    @unittest.skipIf(os.name != "posix", "nur POSIX-Dateirechte")
    def test_private_permissions(self):
        ctx = Context(Path(self.tmp.name) / "daten", env={})
        with ctx.store() as store:
            store.kv_set("x", 1)
        self.assertEqual(stat.S_IMODE(os.stat(ctx.data_dir).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(ctx.db_path).st_mode), 0o600)


class WebSecurityTest(ServerTest):
    def raw(self, method, path, body=b"", headers=None):
        req = urllib.request.Request(self.base + path, data=body if method != "GET" else None,
                                     method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def test_content_type_required(self):
        body = json.dumps({"name": "X", "origins": ["ZRH"], "destinations": ["HAM"],
                           "max_price": 9}).encode()
        status, data = self.raw("POST", "/api/alerts", body, {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        self.assertIn("application/json", data["error"])
        status, _ = self.raw("POST", "/api/alerts", body,
                             {"Content-Type": "application/json; charset=utf-8"})
        self.assertEqual(status, 201)

    def test_cross_site_rejected(self):
        hdr = {"Content-Type": "application/json"}
        self.assertEqual(self.raw("POST", "/api/notifications/read", b"{}",
                                  dict(hdr, Origin="http://evil.example"))[0], 403)
        self.assertEqual(self.raw("DELETE", "/api/alerts/zrh-ham", b"",
                                  {"Sec-Fetch-Site": "cross-site"})[0], 403)
        own = self.base   # gleiche Herkunft ist erlaubt
        self.assertEqual(self.raw("POST", "/api/notifications/read", b"{}",
                                  dict(hdr, Origin=own))[0], 200)

    def test_unknown_host_rejected(self):
        status, data = self.raw("GET", "/api/settings", headers={"Host": "attacker.example:1"})
        self.assertEqual(status, 403)
        self.assertIn("DEALMONITOR_ALLOWED_HOSTS", data["error"])
        for ok in ("localhost:8080", "127.0.0.1", "[::1]:8080", "192.168.1.20:8080"):
            self.assertEqual(self.raw("GET", "/api/status", headers={"Host": ok})[0], 200, ok)
        self.ctx.env["DEALMONITOR_ALLOWED_HOSTS"] = "nas.local, deals.example"
        self.assertEqual(self.raw("GET", "/api/status", headers={"Host": "NAS.local:8080"})[0],
                         200)

    def test_negative_content_length(self):
        import socket
        port = self.server.server_address[1]
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(b"POST /api/run HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                      b"Content-Type: application/json\r\nContent-Length: -1\r\n\r\n{}")
            reply = s.recv(4096)
        self.assertIn(b" 400 ", reply.split(b"\r\n")[0])

    def test_body_too_large(self):
        import socket
        from dealmonitor import web
        port = self.server.server_address[1]
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(b"POST /api/run HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                      b"Content-Type: application/json\r\n"
                      b"Content-Length: " + str(web.MAX_BODY + 1).encode() + b"\r\n\r\n{}")
            reply = s.recv(4096)
        self.assertIn(b" 413 ", reply.split(b"\r\n")[0])

    def test_test_notify_hides_telegram_token(self):
        token = "999999:SECRETTOKENabcdefgh"
        with self.ctx.store() as store:
            AppSettings(store, self.ctx.env).update({"telegram_chat_id": "42"},
                                                    {"telegram_bot_token": token})
        def offline(url, **kw):   # Fehlertext enthält absichtlich die rohe URL
            raise http.HttpError(f"Anfrage fehlgeschlagen: {url} (offline)", network=True)

        with mock.patch("dealmonitor.notify.http.request", side_effect=offline):
            status, data, _ = self.call("POST", "/api/test-notify")
        self.assertEqual(status, 200)
        tg = [c for c in data["channels"] if c["name"] == "telegram"][0]
        self.assertFalse(tg["ok"])
        self.assertNotIn("SECRETTOKEN", json.dumps(data))


if __name__ == "__main__":
    unittest.main()


class RealFeedCasesTest(unittest.TestCase):
    """Fälle aus dem echten Travel-Dealz-Feed (November 2026)."""

    def test_airline_name_is_not_destination(self):
        from dealmonitor.sources.feed import matches
        from dealmonitor.models import Alert
        a = Alert(id="cn", name="CN", origins=["@DE"], destinations=["@CN"], max_price=499,
                  feed_keywords=[["China"], ["Peking"]])
        self.assertFalse(matches(a, "China Eastern: Gabelflüge Amsterdam – Tokio – Frankfurt ab 426€"))
        self.assertTrue(matches(a, "Air China: Deutschland – Peking ab 429€"))
        self.assertTrue(matches(a, "Business Class nach China ab 1.799€"))

    def test_non_flight_items_are_ignored(self):
        from dealmonitor.sources.feed import is_flight_item
        self.assertFalse(is_flight_item("Costa Toscana: 7 Nächte ab 399€", ["Kreuzfahrten"]))
        self.assertFalse(is_flight_item("Schweiz ↔ Paris mit dem TGV Lyria ab 20€", ["Bahn", "TGV"]))
        self.assertFalse(is_flight_item("TUI Hurghada ab 564€ p.P.", ["Pauschalreisen", "Tui"]))
        self.assertFalse(is_flight_item("Condor plant Lounges", ["Allgemein", "Lounge"]))
        self.assertTrue(is_flight_item("Bonaire ab 416€", ["Economy Class"]))
        self.assertTrue(is_flight_item("Lufthansa Gutschein: Seattle ab 529€", ["Flüge"]))
        self.assertTrue(is_flight_item("Hamburg – Zürich ab 79€", []))       # andere Feeds
        self.assertFalse(is_flight_item("7 Nächte Kreuzfahrt ab 399€", []))

    def test_cabin_from_category(self):
        from dealmonitor.sources.feed import detect_cabin
        self.assertEqual(detect_cabin("New York: Condor „Premium Class“ ab 640€",
                                      ["Premium Economy Class"]), "premium")
        self.assertEqual(detect_cabin("Condor „Premium Class“ ab 640€"), "premium")
        self.assertEqual(detect_cabin("Peking ab 1.799€", ["Business Class"]), "business")
        self.assertEqual(detect_cabin("Bonaire ab 416€", ["Economy Class"]), "economy")

    def test_per_direction_is_oneway(self):
        from dealmonitor.sources.feed import ONEWAY_RE
        self.assertTrue(ONEWAY_RE.search("Hamburg – Zürich ab 39€ pro Strecke"))


class InvalidTokenTest(unittest.TestCase):
    def test_401_stops_immediately(self):
        from dealmonitor.sources.travelpayouts import TravelpayoutsSource
        a = Alert(id="t", name="t", origins=["@DE"], destinations=["@CN_MAJOR"], max_price=499)
        calls = []

        def fake(url, **kw):
            calls.append(url)
            raise http.HttpError("HTTP 401: Unauthorized", 401)

        src = TravelpayoutsSource(token="falsch", request_delay=0)
        with mock.patch("dealmonitor.sources.travelpayouts.http.get_json", side_effect=fake):
            with self.assertRaises(SourceError) as ctx:
                src.fetch(a)
            self.assertIn("Token", str(ctx.exception))
            self.assertEqual(len(calls), 1)
            with self.assertRaises(SourceError):   # weitere Alarme im selben Lauf: kein Request
                src.fetch(a)
            self.assertEqual(len(calls), 1)
