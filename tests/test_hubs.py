"""Paket 4: Nachbar-Hubs (Abflug Budapest, Rom, Amsterdam … mit Zubringer) und China-Codes.

Fixture ``yhbu-china.xml``: gekürzte Artikel aus dem Feed von "You Have Been Upgraded"
(Recherche 04.11.2026), nur die China/Asien-Business-Titel.
"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from dealmonitor import appsettings, config, http, hubs, notify, principles, radar, regions
from dealmonitor.hubs import HubSettings
from dealmonitor.models import Alert, Offer
from dealmonitor.sources.feed import (
    PRESETS_BY_ID, FeedSource, Preset, Profile, analyze, hub_origin, origin_ok,
)
from dealmonitor.store import Store

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures" / "feeds"
YHBU = Preset("yhbu-test", "YHBU", ("https://youhavebeenupgraded.boardingarea.com/feed/",), "de",
              False, Profile(lang="de", price_from="title", use_categories=False),
              "https://youhavebeenupgraded.boardingarea.com/")
NOW = datetime.now(timezone.utc)


def cn_alerts() -> dict[str, Alert]:
    cfg = config.load(ROOT / "config" / "alerts.toml")
    return {a.id: a for a in cfg.alerts}


def alert(**kw) -> Alert:
    base = dict(id="t", name="Test", origins=["@DE_HUBS"], destinations=["@CN_MAJOR"],
                cabin="business", max_price=1999, baseline_price=3200)
    base.update(kw)
    return Alert(**base)


def item(title, *, preset="travel-dealz", link=None, cats=("Flüge",), **extra):
    p = PRESETS_BY_ID[preset]
    d = {"title": title, "categories": list(cats), "link": link or "https://x.test/" + quote(title),
         "published": NOW - timedelta(hours=2), "description": "", "description_text": "",
         "merchant_price": None, "feed": "", "profile": p.profile, "source": p.source}
    d.update(extra)
    d["key"] = d["link"]
    return d


def source_with(items, settings=None) -> FeedSource:
    src = FeedSource(presets=["travel-dealz"], search_enabled=False)
    src.load = lambda: items
    src.hub_settings = settings
    return src


def yhbu_offers(a: Alert, settings=None) -> list[Offer]:
    with mock.patch.dict(PRESETS_BY_ID, {YHBU.id: YHBU}), \
            mock.patch.object(http, "request", return_value=(FIX / "yhbu-china.xml").read_bytes()):
        src = FeedSource(presets=[YHBU.id], max_age_days=36500)
        src.hub_settings = settings
        return src.fetch(a)


def deals(a: Alert, offers: list[Offer]):
    out = [principles.evaluate(a, o, [], min_samples=8) for o in offers]
    return [d for d in out if d]


class RegionsTest(unittest.TestCase):
    def test_china_codes(self):
        cn = regions.expand(["@CN"])
        for code in ("DLC", "CGO", "HAK", "SYX"):
            self.assertIn(code, cn)
        all_cn = regions.expand(["@CN_ALL"])
        for code in ("FOC", "HRB", "CGQ", "URC", "WNZ", "NNG", "TNA", "HFE", "KHN", "BJS", "SHA"):
            self.assertIn(code, all_cn)
        self.assertNotIn("HKG", all_cn)
        self.assertEqual(regions.expand(["@CN_MAJOR"]), ["BJS", "SHA", "CAN", "SZX", "CTU", "HGH"])
        tw = regions.expand(["@CN_TW"])
        self.assertTrue({"TPE", "HKG", "BJS"} <= set(tw))
        self.assertIn("Taiwan", regions.region_names(tw))

    def test_synonyms(self):
        self.assertIn("URC", regions.synonyms("Urumqi"))
        self.assertIn("Ürümqi", regions.synonyms("Urumqi"))
        for city, code in (("Harbin", "HRB"), ("Fuzhou", "FOC"), ("Wenzhou", "WNZ"),
                           ("Nanning", "NNG"), ("Changchun", "CGQ"), ("Jinan", "TNA"),
                           ("Hefei", "HFE"), ("Sanya", "SYX"), ("Haikou", "HAK"), ("Dalian", "DLC"),
                           ("Zhengzhou", "CGO"), ("Shenyang", "SHE"), ("Nanchang", "KHN")):
            self.assertEqual(regions.codes_for(city), {code}, city)
        self.assertEqual(regions.codes_for("Strasbourg"), {"SXB"})
        self.assertEqual(regions.codes_for("Straßburg"), {"SXB"})
        self.assertEqual(regions.place_kind("Strasbourg"), "foreign")
        self.assertEqual(regions.place_kind("Basel"), "home")        # BSL bleibt Heimat

    def test_hub_tiers(self):
        self.assertEqual(regions.hub_tier("LUX"), "A")
        self.assertEqual(regions.hub_tier("sxb"), "A")
        self.assertEqual(regions.hub_tier("BUD"), "B")
        self.assertIsNone(regions.hub_tier("LIS"))
        self.assertIsNone(regions.hub_tier("FRA"))
        self.assertFalse(regions.NEIGHBOR_HUBS["A"] & regions.NEIGHBOR_HUBS["B"])
        # alle Hubs sind bekannte Codes (Name in der Meldung) und liegen in Europa
        for code in regions.NEIGHBOR_HUBS["A"] | regions.NEIGHBOR_HUBS["B"]:
            self.assertIn(code, regions.AIRPORT_NAMES)
            self.assertIn(code, regions.EUROPE_CODES)
            self.assertFalse(regions.is_home(code))


class ClassifyTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(hubs.classify({"BUD"}), ("hub_b", 180))
        self.assertEqual(hubs.classify({"LUX"}), ("hub_a", 80))
        self.assertEqual(hubs.classify(["FCO"]), ("hub_b", 180))            # Flughafen -> Rom
        self.assertEqual(hubs.classify({"FRA", "BUD"}), ("home", 0))
        self.assertEqual(hubs.classify({"LIS"}), ("foreign", 0))
        self.assertEqual(hubs.classify(set()), ("foreign", 0))
        self.assertEqual(hubs.classify(None), ("foreign", 0))
        # Mehrfach-Abflug: der günstigste Hub zählt
        self.assertEqual(hubs.cheapest({"MAD", "AMS"}), ("AMS", 80))
        self.assertEqual(hubs.cheapest({"LIS", "ARN"}), ("STO", 180))
        self.assertIsNone(hubs.cheapest({"LIS"}))

    def test_settings(self):
        off = HubSettings(enabled=False)
        self.assertEqual(hubs.classify({"BUD"}, off), ("foreign", 0))
        self.assertIsNone(hubs.cheapest({"BUD"}, off))
        own = HubSettings(surcharge_a=50, surcharge_b=250)
        self.assertEqual(hubs.classify({"BUD"}, own), ("hub_b", 250))
        self.assertEqual(hubs.cheapest({"BUD", "PRG"}, own), ("PRG", 50))
        s = HubSettings.from_values({"hubs_enabled": "aus", "hub_surcharge_a": "95,5",
                                     "hub_surcharge_b": "kaputt"})
        self.assertEqual((s.enabled, s.surcharge_a, s.surcharge_b), (False, 95.5, 180))
        self.assertEqual(HubSettings.from_values(None), hubs.DEFAULT)
        self.assertEqual(HubSettings.from_values({"hub_surcharge_a": -5}).surcharge_a, 80)
        self.assertEqual(HubSettings.from_values(hubs.DEFAULTS), hubs.DEFAULT)
        self.assertEqual(set(hubs.DEFAULTS), set(hubs.LABELS))

    def test_allowed(self):
        a = cn_alerts()
        self.assertTrue(hubs.allowed(a["de-cn-business"]))
        self.assertTrue(hubs.allowed(a["de-cn-economy"]))          # Langstrecke
        self.assertFalse(hubs.allowed(a["zrh-ham"]))                # Kurzstrecke Economy
        self.assertFalse(hubs.allowed(a["zrh-ham-oneway"]))
        self.assertTrue(hubs.allowed(alert(origins=["ZRH"], destinations=["HAM"])))  # Business
        self.assertFalse(hubs.allowed(alert(cabin="economy", destinations=["PMI"])))  # unbekannt
        self.assertFalse(hubs.allowed(alert(origins=["BUD"])))      # Alarm nicht ab DACH
        self.assertTrue(hubs.long_haul(["BJS", "TYO"]))
        self.assertFalse(hubs.long_haul(["BJS", "LIS"]))
        self.assertFalse(hubs.long_haul([]))

    def test_note(self):
        o = Offer(source="feed:x", origin="BUD", destination="", price=1670.0, via_hub="BUD",
                  surcharge=180.0, title="t")
        self.assertEqual(hubs.note(o), "Abflug Nachbar-Hub Budapest (+180 € Zubringer): "
                                       "Ticket 1.490 €, mit Zubringer 1.670 €")
        self.assertIsNone(hubs.note(Offer(source="x", origin="FRA", destination="BJS", price=1)))


class OriginTest(unittest.TestCase):
    def test_hub_origin(self):
        a = alert()
        bud = analyze(item("China ab 1.440€ in der China Southern Business Class von Budapest"))
        self.assertEqual((bud.origin, bud.origin_codes), ("foreign", {"BUD"}))
        self.assertFalse(origin_ok(a, bud))                        # unverändert streng
        self.assertEqual(hub_origin(a, bud), ("BUD", 180))
        self.assertIsNone(hub_origin(a, bud, HubSettings(enabled=False)))
        self.assertIsNone(hub_origin(alert(cabin="economy", destinations=["LIS"]), bud))
        fra = analyze(item("Peking: Business Class von Frankfurt ab 1.123€"))
        self.assertIsNone(hub_origin(a, fra))
        lis = analyze(item("Shanghai ab 1.299€ in der Business Class von Lissabon"))
        self.assertIsNone(hub_origin(a, lis))
        # Alarm fliegt selbst ab Budapest: normaler Treffer ohne Aufschlag
        self.assertTrue(origin_ok(alert(origins=["BUD"]), bud))
        self.assertIsNone(hub_origin(alert(origins=["BUD", "FRA"]), bud))


class FetchTest(unittest.TestCase):
    def test_yhbu_live_titles(self):
        a = cn_alerts()["de-cn-business"]
        found = {(d.offer.title[:40], d.offer.price, d.offer.via_hub, d.offer.surcharge)
                 for d in deals(a, yhbu_offers(a))}
        self.assertEqual(found, {
            ("DEAL: Hainan Business Budapest – China (", 1670, "BUD", 180),
            ("DEAL: Azerbaijan Business Istanbul – Pek", 1080, "IST", 180),
            ("DEAL: Hainan Business Oslo – Shanghai (R", 1780, "OSL", 180),
            ("DEAL: Qatar Business Rom – China (Return", 1860, "ROM", 180),
            ("DEAL: MIAT Business Frankfurt – Peking (", 1330, None, None),
        })
        bud = next(d for d in deals(a, yhbu_offers(a)) if d.offer.via_hub == "BUD")
        self.assertEqual(bud.offer.origin, "BUD")
        self.assertEqual(bud.reasons[0], "Abflug Nachbar-Hub Budapest (+180 € Zubringer): "
                                         "Ticket 1.490 €, mit Zubringer 1.670 €")
        self.assertEqual(bud.offer.extras(), {"via_hub": "BUD", "surcharge": 180.0})
        # ausgeschaltet: wie vorher nur der Deal ab Frankfurt
        off = deals(a, yhbu_offers(a, HubSettings(enabled=False)))
        self.assertEqual([d.offer.price for d in off], [1330])
        # höhere Aufschläge: Grenze gilt für Ticket + Aufschlag (Rom 1.680 + 400 > 1.999)
        high = deals(a, yhbu_offers(a, HubSettings(surcharge_b=400)))
        self.assertEqual(sorted(d.offer.price for d in high), [1300, 1330, 1890])

    def test_price_limit_uses_surcharge(self):
        a = alert()
        offers = source_with([
            item("Oslo → Beijing Business Class Round Trip 1875€", preset="premium-flights"),
            item("Business Class nach Shanghai ab 1.499€ von Madrid & Amsterdam",
                 cats=("Business Class",)),
            item("Shanghai ab 1.515€ in der Business Class von Luxemburg", cats=("Business Class",)),
            item("Guangzhou ab 1.560€ in der Business Class von Rom", cats=("Business Class",)),
            item("Shanghai ab 1.919€ in der Business Class von Barcelona", cats=("Business Class",)),
        ]).fetch(a)
        got = {o.title[:20]: (o.price, o.via_hub) for o in offers}
        self.assertEqual(got["Oslo → Beijing Busin"], (2055, "OSL"))
        self.assertEqual(got["Business Class nach "], (1579, "AMS"))   # günstigster Hub
        self.assertEqual(got["Shanghai ab 1.515€ i"], (1595, "LUX"))
        self.assertEqual(got["Guangzhou ab 1.560€ "], (1740, "ROM"))
        self.assertEqual(got["Shanghai ab 1.919€ i"], (2099, "BCN"))
        self.assertEqual(sorted(d.offer.price for d in deals(a, offers)), [1579, 1595, 1740])

    def test_no_hubs_for_short_haul_economy(self):
        a = cn_alerts()["zrh-ham-oneway"]
        offers = source_with([item("Hamburg ab 19€ one-way von Amsterdam", cats=("Economy",))]).fetch(a)
        self.assertEqual(offers, [])
        a = alert(origins=["ZRH"], destinations=["HAM"], cabin="economy", max_price=500,
                  baseline_price=600)
        offers = source_with([item("Hamburg ab 19€ von Amsterdam", cats=("Economy",))]).fetch(a)
        self.assertEqual(offers, [])

    def test_auto_keywords_need_only_destination(self):
        # Alarm ohne eigene Stichwörter: ab Hub reicht das Ziel-Wort ("Shanghai")
        a = alert(origins=["MUC"], destinations=["SHA"])
        offers = source_with([item("Shanghai ab 1.515€ in der Business Class von Luxemburg",
                                   cats=("Business Class",))]).fetch(a)
        self.assertEqual([(o.price, o.via_hub, o.origin) for o in offers], [(1595, "LUX", "LUX")])

    def test_loader_codes(self):
        # Loader-Quellen (Travel-Dealz-REST) nennen Flughäfen: "Rom [FCO]"
        a = alert()
        it = item("Guangzhou ab 1.560€ in der Business Class", cats=("Business Class",),
                  origin_codes=["FCO"], dest_codes=["CAN"], price_eur=1560.0, cabin="business")
        offers = source_with([it]).fetch(a)
        self.assertEqual([(o.price, o.via_hub, o.surcharge) for o in offers], [(1740, "ROM", 180)])


class RadarTest(unittest.TestCase):
    def test_hub_hint(self):
        s = radar.RadarSettings(business_max=1300)
        reasons = radar.check(item("Business Class nach Peking ab 900€ von Istanbul",
                                   cats=("Business Class",)), s)
        self.assertEqual(reasons, ["Business Class ≤ 1.300 €",
                                   "Abflug Nachbar-Hub Istanbul (+180 € Zubringer)"])
        # Ticket + Aufschlag über der Grenze: kein Treffer
        self.assertIsNone(radar.check(item("Business Class nach Peking ab 1.200€ von Istanbul",
                                           cats=("Business Class",)), s))
        # Economy ab Hub bleibt draußen (meist Kurzstrecke)
        self.assertIsNone(radar.check(item("Tokio ab 199€ von Amsterdam", cats=("Economy",)), s))
        # ausgeschaltet: wie vorher
        off = radar.RadarSettings.from_values({"hubs_enabled": False})
        self.assertIsNone(radar.check(item("Business Class nach Peking ab 900€ von Istanbul",
                                           cats=("Business Class",)), off))

    def test_scan_offer(self):
        deals_ = radar.scan([item("Business Class nach Peking ab 900€ von Istanbul",
                                  cats=("Business Class",))])
        self.assertEqual(len(deals_), 1)
        o = deals_[0].offer
        self.assertEqual((o.price, o.origin, o.via_hub, o.surcharge), (1080, "IST", "IST", 180))
        text = notify.format_text(deals_[0], "new")
        self.assertIn("Abflug Nachbar-Hub Istanbul (+180 € Zubringer)", text)


class SettingsTest(unittest.TestCase):
    def test_appsettings(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        app = appsettings.AppSettings(store, env={})
        self.assertEqual(app.hub_settings(), hubs.DEFAULT)
        app.update({"hubs_enabled": "aus", "hub_surcharge_a": "60", "hub_surcharge_b": "1.200"})
        self.assertEqual(app.hub_settings(), HubSettings(enabled=False, surcharge_a=60,
                                                         surcharge_b=1200))
        self.assertEqual(app.radar_settings().hub_settings, app.hub_settings())
        with self.assertRaises(ValueError):
            app.update({"hub_surcharge_b": -1})
        env = appsettings.AppSettings(store, env={"HUB_SURCHARGE_A": "99"})
        self.assertEqual(env.hub_settings().surcharge_a, 99)
        self.assertIn("hub_surcharge_a", env.env_locked())

    def test_sources_get_hub_settings(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            ctx = appsettings.Context(d, env={})
            with ctx.store() as store:
                app = ctx.settings(store)
                app.update({"hub_surcharge_a": 70, "sources": ["feeds"]})
                cfg = ctx.build_config(store, app)
                feed = next(s for s in ctx.sources(cfg, store, app=app) if s.name == "feeds")
                self.assertEqual(feed.hub_settings.surcharge_a, 70)


class NotifyTest(unittest.TestCase):
    def test_message_shows_hub(self):
        a = cn_alerts()["de-cn-business"]
        d = next(d for d in deals(a, yhbu_offers(a)) if d.offer.via_hub == "OSL")
        text = notify.format_text(d, "new")
        self.assertIn("Preis: ab 1.780 €", text)
        self.assertIn("• Abflug Nachbar-Hub Oslo (+180 € Zubringer): Ticket 1.600 €, "
                      "mit Zubringer 1.780 €", text)
        # neu bewertet (z. B. Preisänderung): Hinweis bleibt vorn
        self.assertTrue(principles.reprice(d, 1700.0))
        self.assertTrue(d.reasons[0].startswith("Abflug Nachbar-Hub Oslo"))
        self.assertEqual(sum(r.startswith("Abflug Nachbar-Hub") for r in d.reasons), 1)


if __name__ == "__main__":
    unittest.main()
