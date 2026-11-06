"""Regressionstests zu den bestätigten Review-Funden von Runde 3 (ohne Netz).

Parser (P1-P5), Alarm-Abgleich und Meldungen (A1-A6, A8), Eingaben der Oberfläche (W1, W2).
"""

import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path

from dealmonitor import appsettings, config as config_mod, dedupe, monitor, radar
from dealmonitor.config import normalize_alert
from dealmonitor.models import Alert
from dealmonitor.sources import feed
from dealmonitor.sources import secretflying as sf
from dealmonitor.sources.feed import (MYDEALZ_PROFILE, PRESETS_BY_ID, REISETOPIA_PROFILE,
                                      FeedSource, analyze, detect_origin, normalize_item,
                                      origin_ok, read_infobox)
from dealmonitor.store import Store

CONFIG = Path(__file__).resolve().parent.parent / "config" / "alerts.toml"
NOW = datetime.now(timezone.utc)


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


CFG = config_mod.load(CONFIG)
A = {a.id: a for a in CFG.alerts}


def item(title, preset="travel-dealz", cats=("Flüge",), **extra):
    p = PRESETS_BY_ID[preset]
    it = {"title": title, "categories": list(cats), "link": "https://x.test/" + str(abs(hash(title))),
          "published": NOW - timedelta(hours=2), "description": "", "description_text": "",
          "merchant_price": None, "feed": "", "profile": p.profile, "source": p.source}
    it.update(extra)
    it["key"] = it["link"]
    return it


def fetch(alert, *items, presets=("travel-dealz",)):
    src = FeedSource(presets=list(presets), search_enabled=False)
    src.load = lambda: list(items)
    return src.fetch(alert)


# -- Parser -------------------------------------------------------------------------------------

class ParserTest(unittest.TestCase):
    def test_p1_many_european_cities_is_foreign(self):
        title = "🦒 Great fares! Flights from many European cities to Cape Town from €370 return"
        self.assertEqual(detect_origin(title, "en"), "foreign")
        info = analyze(item(title, "fly4free", cats=("Cheap flights",)))
        self.assertEqual(info.origin, "foreign")
        self.assertFalse(origin_ok(A["de-cn-economy"], info))
        self.assertEqual(detect_origin("Flüge ab vielen europäischen Städten nach Kapstadt ab 370€"),
                         "foreign")

    def test_p2_mydealz_leading_price_wins(self):
        it = item("AJet Sale | Flüge in die Türkei ab 103€ inkl. Rückflug aus DACH | Handgepäck für 9€",
                  "mydealz-fluege", cats=(), merchant_price="103€",
                  description_text="103€ - AJet AJet hat einen Sale für Türkei-Flüge gestartet. "
                                   "Flüge kosten in der Aktion ab 15€ Ticketpreis zzgl. Gebühren")
        self.assertEqual(analyze(it, MYDEALZ_PROFILE).price, 103)
        it = item("Swiss: Flüge Zürich – Hamburg inkl. Rückflug", "mydealz-fluege", cats=(),
                  description_text="69€ - Swiss Hin und zurück, Rückflug allein schon ab 19€")
        self.assertEqual(analyze(it, MYDEALZ_PROFILE).price, 69)
        # ohne führenden Betrag: Händlerpreis vor "ab"-Beträgen im Text
        it = item("Flüge nach Hamburg ab Zürich", "mydealz-fluege", cats=(), merchant_price="49€",
                  description_text="Tolle Preise, Teilstrecken schon ab 19€")
        self.assertEqual(analyze(it, MYDEALZ_PROFILE).price, 49)

    def test_p3_game_is_no_flight(self):
        it = item("[Xbox Store] Owlboy für 2,49€ statt 24,99€ | Charmantes Pixel-Art-Adventure mit "
                  "Flug-Gameplay | Xbox One & Series X|S", "mydealz-fluege", cats=(),
                  description_text="2,49€ - Xbox Action-Adventure mit Flug-Gameplay")
        self.assertFalse(analyze(it, MYDEALZ_PROFILE).flight)
        self.assertFalse(feed.FLIGHT_WORD_RE.search("Microsoft Flight Simulator 2024 für 39€"))
        self.assertTrue(feed.FLIGHT_WORD_RE.search("Flüge nach Rom ab 39€"))

    def test_p4_fork_flight_origin(self):
        info = analyze(item("Aer Lingus: Gabelflüge Brüssel – Cancún – Deutschland ab 374€ (inkl. Gepäck)"))
        self.assertEqual((info.origin, info.origin_codes), ("foreign", {"BRU"}))
        # bekannte Strecken bleiben, wie sie waren
        self.assertEqual(detect_origin("China Eastern: Gabelflüge Amsterdam – Tokio – Frankfurt ab 426€"),
                         "foreign")
        self.assertEqual(detect_origin("New York - Flüge inkl. Rückflug ab 299€"), "unknown")

    def test_p5_infobox_country_is_region(self):
        text = ("In der Condor Business Class nonstop von Deutschland nach Bangkok … Business Class "
                "von Deutschland nach Bangkok Buchbar ab nur 2.330 Euro pro Person")
        box = read_infobox(text, REISETOPIA_PROFILE.price_regex)
        self.assertTrue(box["origin_region"])
        self.assertIn("FRA", box["origin_codes"])
        city = read_infobox("Business Class von Frankfurt nach Bangkok Buchbar ab 2.330 Euro pro Person",
                            REISETOPIA_PROFILE.price_regex)
        self.assertNotIn("origin_region", city)
        it = item("Condor Business Class nach Bangkok", "reisetopia", cats=("Business Class",),
                  content_text=text)
        feed._enrich(it, REISETOPIA_PROFILE)
        self.assertTrue(analyze(it).origin_region)


# -- Alarm-Abgleich -------------------------------------------------------------------------------

def sf_item(text, i=0):
    raw = sf.post_item({"text": text, "created": "2026-11-06T08:00:00Z", "uri": f"at://x/{i}",
                        "links": [f"https://www.secretflying.com/posts/x{i}"]})
    it = normalize_item(raw, "loader:secretflying")
    p = PRESETS_BY_ID["secretflying"]
    it.update(source=p.source, profile=p.profile, preset=p.id, key=it["link"])
    return it


class MatchTest(unittest.TestCase):
    def test_a1_dest_codes_must_fit_alert(self):
        a = A["de-cn-economy"]
        for i, text in enumerate([
                "#Frankfurt, Germany to Hong Kong, China for only €399 roundtrip #Travel",
                "#Munich, Germany to Macau, China for only €389 roundtrip #Travel",
                "#Germany to Sanya, Hainan, China for only €420 roundtrip #Travel",
                "#Frankfurt, Germany to Urumqi, China for only €350 roundtrip #Travel"]):
            self.assertEqual(fetch(a, sf_item(text, i), presets=("secretflying",)), [], text)
        # Peking: Ziel-Code PEK/PKX passt zur Stadt BJS des Alarms
        offers = fetch(a, sf_item("#Frankfurt, Germany to Beijing, China for only €389 roundtrip #Travel", 9),
                       presets=("secretflying",))
        self.assertEqual([o.price for o in offers], [389])

    def test_a2_stopover_is_no_destination(self):
        a = A["de-cn-economy"]
        for title in ["Air China: Flüge nach Bangkok über Peking ab 399€",
                      "Flüge nach Tokio mit Stopover in Shanghai ab 449€",
                      "China Eastern: Flüge nach Sydney ab 699€ (mit Stopover in Shanghai)",
                      "Flüge nach Bali ab 459€ mit Zwischenstopp in Guangzhou"]:
            self.assertEqual(fetch(a, item(title)), [], title)
        self.assertEqual(len(fetch(a, item("Flüge nach Peking via Shanghai ab 389€"))), 1)

    def test_a3_hin_und_zurueck_with_oneway_price(self):
        title = "Swiss: Zürich – Hamburg ab 59€ hin und zurück | oneway ab 29€"
        self.assertFalse(feed.is_oneway(title))
        self.assertTrue(feed.is_oneway("Hamburg – Zürich ab 29€ one-way | hin und zurück ab 59€"))
        rt = fetch(A["zrh-ham"], item(title))
        self.assertEqual([(o.price, o.roundtrip) for o in rt], [(59, True)])
        ow = fetch(A["zrh-ham-oneway"], item(title))
        self.assertEqual([(o.price, o.roundtrip) for o in ow], [(29, False)])
        self.assertTrue(feed.is_oneway("Eurowings: Hamburg – Zürich ab 29€ one-way"))

    def test_a5_route_without_nach(self):
        dests, origins = dedupe.route("Eurowings: Flüge Hamburg – Zürich ab 29€ one-way")
        self.assertEqual(dests, {"ZRH"})
        self.assertIn("HAM", origins)

    def test_a6_error_fare_economy_from_hub(self):
        hit = item("Error Fare: Flüge von Amsterdam nach Tokio ab 229€")
        deals = radar.scan([hit], radar.RadarSettings())
        self.assertEqual(len(deals), 1)
        d = deals[0]
        self.assertEqual((d.offer.via_hub, d.offer.price), ("AMS", 229 + d.offer.surcharge))
        self.assertIn("Error Fare laut Überschrift", d.reasons)


# -- Läufe mit Meldungen ---------------------------------------------------------------------------

def rss(entries):
    body = ""
    for title, link, cats, pub in entries:
        body += (f"<item><title>{escape(title)}</title><link>{link}</link>"
                 f"<pubDate>{format_datetime(pub)}</pubDate>"
                 + "".join(f"<category>{escape(c)}</category>" for c in cats) + "</item>")
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'.encode()


class Feed(FeedSource):
    def __init__(self, by_preset):
        super().__init__(presets=list(by_preset), search_enabled=False, max_age_days=14)
        self.by = by_preset

    def _feed_jobs(self):
        return [(PRESETS_BY_ID[p].urls[0], PRESETS_BY_ID[p]) for p in self.by]

    def _fetch_one(self, url):
        p = next(p for p in self.by if PRESETS_BY_ID[p].urls[0] == url)
        return self.parse_all(rss(self.by[p]), url)


class Rec:
    name = "rec"

    def __init__(self):
        self.sent = []

    def send(self, alert_name, items):
        self.sent += [(alert_name, st, d.offer.price, d.offer.cabin, d.offer.roundtrip)
                      for d, st in items]


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def run_feeds(self, by_preset, *, radar_on=False):
        rec = Rec()
        with Store(self.db) as st:
            monitor.run(CFG, [Feed(by_preset)], [rec], st,
                        radar_settings=radar.RadarSettings() if radar_on else None)
        return rec.sent

    def test_a4_title_price_drop_is_cheaper(self):
        link = "https://travel-dealz.de/deal/peking-air-china/"
        t0 = NOW - timedelta(hours=3)
        first = self.run_feeds({"travel-dealz": [
            ("Air China: Flüge von Frankfurt nach Peking ab 449€", link, ["Flüge"], t0)]})
        self.assertEqual([s[:3] for s in first], [("Deutschland → China (Economy)", "new", 449)])
        again = self.run_feeds({"travel-dealz": [
            ("Air China: Flüge von Frankfurt nach Peking ab 349€", link, ["Flüge"], t0)]})
        self.assertEqual([s[:3] for s in again], [("Deutschland → China (Economy)", "cheaper", 349)])
        with Store(self.db) as st:
            rows = [d for d in st.deals() if d["alert_id"] == "de-cn-economy"]
        self.assertEqual([(r["status"], r["price"]) for r in rows], [("active", 349)])
        # kleine Änderung (unter renotify_drop): still aktualisiert, keine neue Meldung
        self.assertEqual(self.run_feeds({"travel-dealz": [
            ("Air China: Flüge von Frankfurt nach Peking ab 345€", link, ["Flüge"], t0)]}), [])

    def test_a5_same_oneway_fare_two_sites_once(self):
        t = NOW - timedelta(hours=5)
        td = ("Eurowings: Flüge Hamburg – Zürich ab 29€ one-way", "https://travel-dealz.de/ham-zrh-ew/",
              ["Flüge"], t)
        up = ("Eurowings-Flüge: Hamburg – Zürich one-way ab 29€", "https://www.urlaubspiraten.de/ham-zrh-ew",
              ["Flüge"], t + timedelta(hours=3))
        self.assertEqual(len(self.run_feeds({"travel-dealz": [td]})), 1)
        self.assertEqual(self.run_feeds({"travel-dealz": [td], "urlaubspiraten": [up]}), [])

    def test_a3_a8_one_article_several_deals(self):
        t = NOW - timedelta(hours=1)
        sent = self.run_feeds({"travel-dealz": [
            ("Swiss: Zürich – Hamburg ab 59€ hin und zurück | oneway ab 29€",
             "https://travel-dealz.de/zrh-ham/", ["Flüge"], t),
            ("Flüge von Frankfurt nach Peking ab 399€ (Business Class ab 1.599€)",
             "https://travel-dealz.de/pek/", ["Flüge"], t)]})
        got = {(s[0], s[2], s[3], s[4]) for s in sent}
        self.assertEqual(got, {
            ("Zürich → Hamburg (und zurück)", 59, "economy", True),
            ("Zürich ⇄ Hamburg one-way", 29, "economy", False),
            ("Deutschland → China (Economy)", 399, "economy", True),
            ("Deutschland → China (Business Class)", 1599, "business", True)})
        self.assertEqual(len(sent), 4)

    def test_a8_same_deal_two_alerts_still_once(self):
        sent = self.run_feeds({"travel-dealz": [
            ("Swiss: Hamburg und Zürich ab 39€ hin und zurück", "https://travel-dealz.de/a/",
             ["Flüge"], NOW - timedelta(hours=1))]})
        self.assertEqual(len(sent), 1)


# -- Eingaben -------------------------------------------------------------------------------------

class NumberTest(unittest.TestCase):
    BASE = {"id": "x", "name": "X", "origins": ["HAM"], "destinations": ["ZRH"], "max_price": 100}

    def test_w1_w2_infinity_rejected(self):
        for field in ("max_price", "baseline_price", "max_duration_hours", "max_stops",
                      "min_nights", "months_ahead"):
            for bad in (float("inf"), float("-inf"), float("nan"), "Infinity", "-inf", "nan"):
                with self.subTest(field=field, value=bad):
                    with self.assertRaises(ValueError):
                        normalize_alert({**self.BASE, field: bad})

    def test_w1_toml_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.toml"
            path.write_text('[[alert]]\nid = "x"\nname = "X"\norigins = ["HAM"]\n'
                            'destinations = ["ZRH"]\nmax_price = inf\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                config_mod.load_raw(path)

    def test_settings_infinity_rejected(self):
        with self.assertRaises(ValueError):
            appsettings.coerce("radar_business_max", float("inf"))


if __name__ == "__main__":
    unittest.main()
