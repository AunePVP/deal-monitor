"""Fundament Runde 2: Loader-Presets, optionale Item-Schlüssel, Quellen-Statistik,
Quellen-Zustand im Store, Zusatzfelder am Offer und Währungsumrechnung.

Kein Netz: Der Dummy-Loader ist ein Modul, das nur in ``sys.modules`` existiert.
"""

import json
import sys
import types
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from dealmonitor import currency, monitor, radar
from dealmonitor.models import Alert, Deal, Offer
from dealmonitor.sources import SourceError
from dealmonitor.sources import feed
from dealmonitor.sources.feed import (
    PRESETS_BY_ID, FeedSource, Preset, Profile, analyze, load_loader, normalize_item,
)
from dealmonitor.store import Store

NOW = datetime.now(timezone.utc)
DUMMY = "dealmonitor.sources._dummy_loader"
SF_TITLE = "Zurich, Switzerland to Mauritius for only €471 roundtrip"


def item(**kw):
    base = {"title": SF_TITLE, "link": "https://www.secretflying.com/posts/zurich-mauritius/",
            "published": NOW - timedelta(hours=1), "categories": [], "description": ""}
    base.update(kw)
    return base


def alert(**kw):
    base = dict(id="t", name="Test", origins=["ZRH"], destinations=["HAM"], max_price=110,
                baseline_price=190)
    base.update(kw)
    return Alert(**base)


class DummyLoader:
    """Ersetzt ``fetch_items`` eines Quellmoduls; merkt sich Aufrufe, ändert ``state``."""

    def __init__(self, items=None, error=None):
        self.items, self.error, self.calls = items or [], error, []

    def __call__(self, *, timeout=20.0, state=None):
        self.calls.append({"timeout": timeout, "state": dict(state or {})})
        state["cursor"] = state.get("cursor", 0) + 1     # Zustand ändern ...
        if self.error:
            raise self.error                              # ... und trotzdem scheitern
        return self.items


class LoaderPresetTest(unittest.TestCase):
    def setUp(self):
        self.loader = DummyLoader()
        module = types.ModuleType(DUMMY)
        module.fetch_items = self.loader
        sys.modules[DUMMY] = module
        self.addCleanup(sys.modules.pop, DUMMY, None)
        self.preset = Preset("dummy", "Dummy", (), "en", False,
                             Profile(lang="en", price_from="title"), "https://example.org/",
                             loader="_dummy_loader:fetch_items", lane="fast")
        self.rss = PRESETS_BY_ID["fly4free"]
        patcher = mock.patch.dict(PRESETS_BY_ID, {"dummy": self.preset})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def source(self, presets=("dummy",), **kw):
        kw.setdefault("max_age_days", 14)
        return FeedSource(presets=list(presets), state_store=self.store, **kw)

    def test_preset_defaults(self):
        p = PRESETS_BY_ID["travel-dealz"]
        self.assertIsNone(p.loader)
        self.assertEqual(p.lane, "normal")
        self.assertEqual(p.jobs, p.urls)
        self.assertEqual(self.preset.jobs, ("_dummy_loader:fetch_items",))
        with self.assertRaises(ValueError):
            Preset("x", "X", (), "de", False, Profile(), "", lane="langsam")

    def test_load_loader(self):
        self.assertIs(load_loader("_dummy_loader:fetch_items"), self.loader)
        self.assertIs(load_loader(f"{DUMMY}:fetch_items"), self.loader)
        for bad in ("gibtsnicht:fetch_items", "_dummy_loader:fehlt", "ohne_doppelpunkt", ""):
            with self.assertRaises(SourceError):
                load_loader(bad)

    def test_loader_items_state_and_stats(self):
        self.loader.items = [
            item(origin_codes={"ZRH"}, dest_codes=["mru"], price_eur=471, uid="at://1"),
            item(title="Hotel in Paris for €49 per night", link="https://example.org/hotel"),
            {"title": ""},                                          # ohne Titel: weg
            item(title="Old deal for €99", link="https://example.org/old",
                 expires=NOW - timedelta(days=1)),                  # abgelaufen: weg
        ]
        self.store.set_source_state("dummy", {"cursor": 41})
        src = self.source()
        items = src.load()
        self.assertEqual(len(items), 2)
        self.assertEqual(self.loader.calls, [{"timeout": 20.0, "state": {"cursor": 41}}])
        self.assertEqual(self.store.source_state("dummy"), {"cursor": 42})
        it = items[0]
        self.assertEqual(it["source"], "feed:dummy")
        self.assertEqual(it["feed"], "loader:dummy")
        self.assertEqual(it["dest_codes"], {"MRU"})
        st = src.stats["feed:dummy"]
        self.assertEqual((st["items"], st["parsed"], st["error"]), (2, 1, None))
        self.assertEqual(st["newest"], it["published"])
        info = analyze(it)
        self.assertEqual((info.origin, info.price, info.uid), ("home", 471.0, "at://1"))
        self.assertEqual(src.offer_sources, ["feed:dummy"])

    def test_loader_error_keeps_state_and_other_presets(self):
        self.store.set_source_state("dummy", {"cursor": 7})
        self.loader.error = SourceError("HTTP 429")
        src = self.source(presets=("dummy", "fly4free"))
        rss = (b"<rss><channel><item><title>Flights to Tokyo from Vienna for \xe2\x82\xac499"
               b"</title><link>https://www.fly4free.com/flights/x/</link></item></channel></rss>")
        with mock.patch.object(src, "_fetch_one", return_value=src.parse(rss)):
            items = src.load()
        self.assertEqual([i["source"] for i in items], ["feed:fly4free"])
        self.assertEqual(self.store.source_state("dummy"), {"cursor": 7})   # nicht gespeichert
        self.assertIn("HTTP 429", src.stats["feed:dummy"]["error"])
        self.assertEqual(src.stats["feed:dummy"]["items"], 0)
        self.assertEqual(src.stats["feed:fly4free"]["items"], 1)
        self.assertIsNone(src.stats["feed:fly4free"]["error"])
        self.assertIn("Dummy: HTTP 429", src.load_errors)
        self.assertEqual(src.unreliable_sources(), {"feed:dummy"})

    def test_unexpected_loader_errors_never_crash(self):
        for error, result in ((ValueError("kaputt"), None), (None, {"kein": "list"})):
            self.loader.error = error
            self.loader.items = result
            src = self.source()
            with self.assertRaises(SourceError):     # einzige Quelle -> wie bei RSS: Fehler
                src.load()
            self.assertTrue(src.stats["feed:dummy"]["error"])

    def test_missing_module_is_source_error(self):
        broken = Preset("kaputt", "Kaputt", (), "de", False, Profile(), "",
                        loader="gibtsnicht:fetch_items")
        with mock.patch.dict(PRESETS_BY_ID, {"kaputt": broken}):
            src = FeedSource(presets=["kaputt", "dummy"], state_store=self.store)
            self.loader.items = [item(origin_codes={"ZRH"}, price_eur=471)]
            self.assertEqual(len(src.load()), 1)
            self.assertIn("nicht verfügbar", src.stats["feed:kaputt"]["error"])

    def test_without_state_store(self):
        self.loader.items = [item(price_eur=300)]
        src = FeedSource(presets=["dummy"])
        self.assertEqual(len(src.load()), 1)
        self.assertEqual(self.loader.calls[0]["state"], {})

    def test_lanes(self):
        both = ["dummy", "fly4free", "travel-dealz-suche"]
        fast = FeedSource(presets=both, extra_feeds=["https://example.org/feed"], lanes={"fast"})
        self.assertEqual([p.id for p in fast.presets], ["dummy"])
        self.assertEqual((fast.extra_feeds, fast.search_enabled), ([], False))
        normal = FeedSource(presets=both, lanes={"normal"})
        self.assertEqual([p.id for p in normal.presets], ["fly4free"])
        self.assertTrue(normal.search_enabled)
        self.assertEqual(len(FeedSource(presets=both).presets), 2)

    def test_fetch_uses_codes_and_carries_extras(self):
        expires = NOW + timedelta(days=3)
        self.loader.items = [
            item(title="Eurowings: nonstop for only €39", link="https://example.org/a",
                 origin_codes={"ZRH"}, dest_codes={"HAM"}, price_eur=39, oneway=True,
                 expires=expires, hot="PRICE DROP", uid="td:1:2026"),
            item(title="Eurowings: nonstop for only €35", link="https://example.org/b",
                 origin_codes={"BER"}, dest_codes={"HAM"}, price_eur=35),   # falscher Abflug
        ]
        src = self.source()
        offers = src.fetch(alert(trip="oneway", max_price=45))
        self.assertEqual(len(offers), 1)
        o = offers[0]
        self.assertEqual((o.price, o.roundtrip, o.hot, o.uid), (39.0, False, "PRICE DROP",
                                                                "td:1:2026"))
        self.assertEqual(o.expires, expires)
        self.assertIn("https://example.org/a", src.matched_links)


class AnalyzeKeysTest(unittest.TestCase):
    def test_origin_codes_and_price_override_title(self):
        """Bauplan-Test: origin_codes={"ZRH"}, price_eur=471 -> home, 471."""
        info = analyze(item(origin_codes={"ZRH"}, price_eur=471))
        self.assertEqual((info.flight, info.origin, info.price), (True, "home", 471.0))
        self.assertEqual(info.origin_codes, {"ZRH"})
        foreign = analyze(item(title="Paris to Mauritius for only €471", origin_codes=["cdg"]))
        self.assertEqual((foreign.origin, foreign.origin_codes), ("foreign", {"CDG"}))
        self.assertFalse(feed.origin_ok(alert(), foreign))

    def test_without_keys_unchanged(self):
        plain = item(title="Flüge von Zürich nach Hamburg ab 39€ (Business Class ab 229€)")
        info = analyze(plain)
        self.assertEqual((info.price, info.cabin, info.alt_prices), (39.0, "economy",
                                                                      {"business": 229.0}))
        self.assertEqual((info.dest_codes, info.expires, info.hot, info.uid),
                         (set(), None, None, None))

    def test_price_eur_drops_title_cabin_prices(self):
        info = analyze(item(title="Tokio ab 650€ (Business Class ab 1.199€)", price_eur=640))
        self.assertEqual((info.price, info.alt_prices), (640.0, {}))

    def test_cabin_oneway_override(self):
        info = analyze(item(cabin="business", oneway=True, price_eur=1490))
        self.assertEqual((info.cabin, info.oneway), ("business", True))
        info = analyze(item(cabin="sofa", oneway="ja"))
        self.assertEqual((info.cabin, info.oneway), ("economy", False))

    def test_expired_item_is_no_deal(self):
        info = analyze(item(expires=NOW - timedelta(minutes=1), price_eur=99))
        self.assertFalse(info.flight)
        self.assertIsNone(info.price)
        self.assertTrue(analyze(item(expires=NOW + timedelta(days=1))).flight)
        self.assertIsNone(normalize_item(item(expires=NOW - timedelta(minutes=1))))
        naive = (NOW - timedelta(hours=2)).replace(tzinfo=None)      # naiv = UTC
        self.assertIsNone(normalize_item(item(expires=naive)))
        self.assertIsNone(normalize_item(item(expires=(NOW - timedelta(hours=2)).isoformat())))

    def test_hot_marker_for_radar_only(self):
        raw = normalize_item(item(title="Zurich to Bangkok for €299", hot="ERROR FARE"))
        self.assertEqual(raw["categories"], ["ERROR FARE"])
        info = analyze(raw)
        self.assertTrue(info.flight)          # Marker zählt nicht als "Nicht-Flug-Kategorie"
        self.assertEqual(info.hot, "ERROR FARE")
        self.assertIn("Error Fare", radar.check(raw, info=info)[0])
        crazy = normalize_item(item(title="Zurich to Bangkok for €299", hot="CRAZY HOT"))
        self.assertTrue(analyze(crazy).flight)

    def test_normalize_item_cleans_types(self):
        raw = normalize_item({"title": "  X to Y for €10 ", "link": "javascript:alert(1)",
                              "published": "2026-11-04T09:46:00Z", "categories": "Flights",
                              "price_eur": float("nan"), "origin_codes": "zrh",
                              "dest_codes": ["HAM", "x", None], "uid": 12, "extra": 1})
        self.assertEqual(raw["title"], "X to Y for €10")
        self.assertEqual(raw["link"], "")
        self.assertEqual(raw["published"], datetime(2026, 11, 4, 9, 46, tzinfo=timezone.utc))
        self.assertEqual(raw["categories"], ["Flights"])
        self.assertNotIn("price_eur", raw)
        self.assertEqual((raw["origin_codes"], raw["dest_codes"]), ({"ZRH"}, {"HAM"}))
        self.assertEqual((raw["uid"], raw["extra"], raw["merchant_price"]), ("12", 1, None))
        self.assertEqual(raw["description_text"], "")
        self.assertIsNone(normalize_item("kein dict"))
        self.assertEqual(normalize_item({"title": "X", "categories": 5})["categories"], [])
        old = item(published=NOW - timedelta(days=20))
        self.assertIsNone(normalize_item(old, max_age_days=14))
        self.assertIsNotNone(normalize_item(old))


class OfferExtrasTest(unittest.TestCase):
    def offer(self, **kw):
        base = dict(source="feed:dummy", origin="FRA", destination="PEK", price=1490.0,
                    cabin="business", title="Business Budapest – China 1.490 Euro",
                    link="https://example.org/d", found_at=datetime(2026, 11, 5, 8, 0))
        base.update(kw)
        return Offer(**base)

    def test_defaults_and_identity(self):
        plain = self.offer()
        rich = self.offer(insight={"lowest": 168}, via_hub="BUD", surcharge=180.0,
                          expires=datetime(2026, 11, 12, 23, 59), hot="ERROR FARE", uid="x")
        self.assertEqual(plain.extras(), {})
        self.assertEqual(plain, rich)                       # compare=False
        self.assertEqual(plain.fingerprint, rich.fingerprint)
        self.assertEqual(rich.extras(), {"insight": {"lowest": 168}, "via_hub": "BUD",
                                         "surcharge": 180.0, "expires": "2026-11-12T23:59",
                                         "hot": "ERROR FARE", "uid": "x"})
        json.dumps(rich.extras())

    def test_store_and_cheapest_pass_extras(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        a = alert(id="de-cn-business", origins=["@DE_HUBS"], destinations=["PEK"],
                  cabin="business", max_price=1999)
        rich = self.offer(via_hub="BUD", surcharge=180.0, hot="CRAZY HOT")
        deal = Deal(alert=a, offer=rich, rating="Deal", reference_price=None, discount=None,
                    reasons=[], headline=rich.title)
        store.upsert_deal(deal, 0.1)
        row = store.deals()[0]
        self.assertEqual((row["via_hub"], row["surcharge"], row["hot"]), ("BUD", 180.0,
                                                                          "CRAZY HOT"))
        self.assertNotIn("insight", row)
        # alte Zeile ohne Zusatzfelder lädt weiter
        store.db.execute("UPDATE deals SET payload=?", (json.dumps({"cabin": "economy"}),))
        self.assertNotIn("via_hub", store.deals()[0])
        entry = monitor._entry(rich, False)
        self.assertEqual((entry["via_hub"], entry["surcharge"]), ("BUD", 180.0))
        self.assertNotIn("uid", monitor._entry(self.offer(), False))


class SourceStateTest(unittest.TestCase):
    def test_roundtrip(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        self.assertEqual(store.source_state("secretflying"), {})
        store.set_source_state("secretflying", {"cursor": "2026-11-04T09:45:00"})
        self.assertEqual(store.source_state("secretflying"), {"cursor": "2026-11-04T09:45:00"})
        self.assertEqual(store.kv_get("state:secretflying"), {"cursor": "2026-11-04T09:45:00"})
        store.kv_set("state:kaputt", [1, 2])
        self.assertEqual(store.source_state("kaputt"), {})
        with self.assertRaises(TypeError):
            store.set_source_state("x", [1])


ECB_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
    xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube><Cube time='2026-11-05'>
    <Cube currency='USD' rate='1.2000'/><Cube currency='CHF' rate='0.9000'/>
    <Cube currency='XXX' rate='kaputt'/>
  </Cube></Cube>
</gesmes:Envelope>"""


class CurrencyTest(unittest.TestCase):
    def setUp(self):
        currency.reset_rates()
        self.addCleanup(currency.reset_rates)

    def test_fixed_rates(self):
        self.assertAlmostEqual(currency.to_eur(291, "USD"), 258.28, places=2)
        self.assertAlmostEqual(currency.to_eur(291, "$"), 258.28, places=2)
        self.assertAlmostEqual(currency.to_eur(100, "£"), 119.82, places=2)
        self.assertAlmostEqual(currency.to_eur(274, "chf"), 298.22, places=2)
        self.assertAlmostEqual(currency.to_eur("3650", "CNY"), 485.52, places=2)
        self.assertEqual(currency.to_eur(471, "EUR"), 471.0)
        self.assertEqual(currency.to_eur(471, "€"), 471.0)
        for amount, cur in ((100, "XYZ"), (100, None), ("abc", "USD"), (-5, "USD"),
                            (float("inf"), "USD"), (None, "USD")):
            self.assertIsNone(currency.to_eur(amount, cur))
        self.assertEqual(currency.code_for("US$"), "USD")
        self.assertEqual(currency.code_for("A$"), "AUD")
        self.assertEqual(currency.rates()[0], currency.RATES_DATE)

    def test_parse_ecb(self):
        day, rates = currency.parse_ecb(ECB_XML)
        self.assertEqual((day, rates), ("2026-11-05", {"USD": 1.2, "CHF": 0.9}))
        for bad in (b"<html>", b"<a/>"):
            with self.assertRaises(ValueError):
                currency.parse_ecb(bad)

    def test_refresh_and_load(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        now = datetime(2026, 11, 5, 17, 0)
        calls = []

        def fetch(url):
            calls.append(url)
            return ECB_XML
        self.assertTrue(currency.refresh_rates(store, now=now, fetch=fetch))
        self.assertEqual(currency.to_eur(120, "USD"), 100.0)
        self.assertAlmostEqual(currency.to_eur(100, "GBP"), 119.82, places=2)   # fester Kurs
        self.assertEqual(store.kv_get("fx_rates")["date"], "2026-11-05")
        # innerhalb von 24 h: kein neuer Abruf
        self.assertFalse(currency.refresh_rates(store, now=now + timedelta(hours=5), fetch=fetch))
        self.assertEqual(calls, [currency.ECB_URL])
        currency.reset_rates()
        self.assertTrue(currency.load_rates(store))
        self.assertEqual(currency.to_eur(120, "USD"), 100.0)

    def test_refresh_error_keeps_rates(self):
        def broken(url):
            raise OSError("offline")
        self.assertFalse(currency.refresh_rates(None, fetch=broken))
        self.assertAlmostEqual(currency.to_eur(291, "USD"), 258.28, places=2)
        self.assertFalse(currency.refresh_rates(None, fetch=lambda url: b"<html></html>"))
        self.assertFalse(currency.load_rates(None))


if __name__ == "__main__":
    unittest.main()
