"""Runde 2, Zusammenspiel der Pakete.

* Jede registrierte Loader-Quelle (Preset mit ``loader``) wird wirklich importiert und läuft
  über ``FeedSource`` gegen die Fixture ihres Pakets (Secret Flying, Travel-Dealz-REST,
  ErrorFareAlerts) - ohne Netz.
* Nachbar-Hubs (Paket 4) greifen für Artikel aus Loadern und Feeds (Pakete 1, 3, 5).
* Die Quellen-Gesundheit (Paket 6) sieht die neuen Quellen, auch im echten Schnell-Lauf.
* Gespeicherte Einstellungen bekommen die neuen Deal-Seiten einmal dazu.
* Die Google-Gegenprüfung übernimmt das Preisniveau (Paket 2) in den Deal.
"""

import json
import logging
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import test_traveldealz_rest as ttd
import test_integration as ti

from dealmonitor import config, health, http
from dealmonitor.appsettings import (
    NEW_PRESETS_V3, SETTINGS_VERSION, AppSettings, Context, migrate_settings,
)
from dealmonitor.hubs import HubSettings
from dealmonitor.monitor import verify_deal
from dealmonitor.scheduler import execute
from dealmonitor.sources import SourceError
from dealmonitor.sources import errorfarealerts as efa
from dealmonitor.sources import secretflying as sf
from dealmonitor.sources.feed import (
    FEED_PRESETS, PRESETS_BY_ID, SOURCE_LABELS, FeedSource, analyze, load_loader,
)

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"
OLD = 36500                                     # Altersgrenze praktisch aus
#: Stand der Bluesky-Fixtures
BSKY_NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
ISO_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$")
LOADER_PRESETS = {"secretflying", "travel-dealz-rest", "errorfarealerts"}


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def _shift(value, delta: timedelta):
    """Alle ISO-Zeitangaben (createdAt, indexedAt, cursor) einer Bluesky-Antwort verschieben."""
    if isinstance(value, dict):
        return {k: _shift(v, delta) for k, v in value.items()}
    if isinstance(value, list):
        return [_shift(v, delta) for v in value]
    if isinstance(value, str) and ISO_RE.match(value):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00")) + delta
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return value


class FakeWeb:
    """Ersetzt ``http.request`` für alle Loader-Quellen; Zeitangaben wirken "von jetzt"."""

    def __init__(self):
        now = datetime.now(timezone.utc)
        delta = now - BSKY_NOW
        self.pages = [_shift(json.loads((FIX / name).read_text(encoding="utf-8")), delta)
                      for name in ("bsky_secretflying.json", "bsky_secretflying_p2.json")]
        self.td = ttd.live_api(now - ttd.FIXTURE_NOW)
        self.efa = (FIX / "feeds" / "errorfarealerts_flights.html").read_bytes()
        self.calls: list[str] = []

    def __call__(self, url, *, params=None, **kw):
        self.calls.append(url)
        if url == sf.API_URL:
            cursor = (params or {}).get("cursor")
            if cursor is None:
                return json.dumps(self.pages[0]).encode()
            if cursor == self.pages[0]["cursor"]:
                return json.dumps(self.pages[1]).encode()
            return b'{"feed": []}'
        if url.startswith("https://travel-dealz.de/wp-json/"):
            return self.td(url, **kw)
        if url.startswith(efa.LIST_URL):
            return self.efa
        raise http.HttpError(f"HTTP 404 für {url}", 404)

    def count(self, url: str) -> int:
        return sum(1 for u in self.calls if u.startswith(url))


def load(presets, web=None, **kw) -> tuple[FeedSource, list[dict]]:
    src = FeedSource(presets=list(presets), max_age_days=OLD, search_enabled=False, **kw)
    with mock.patch.object(http, "request", side_effect=web or FakeWeb()):
        return src, src.load()


def cn_alerts():
    return {a.id: a for a in config.load(ROOT / "config" / "alerts.toml").alerts}


class LoaderRegistryTest(unittest.TestCase):
    def test_every_loader_preset_is_covered(self):
        # neue Loader-Quelle -> hier mit Fixture aufnehmen
        self.assertEqual({p.id for p in FEED_PRESETS if p.loader}, LOADER_PRESETS)

    def test_loaders_import_and_run_from_fixture(self):
        for pid in sorted(LOADER_PRESETS):
            with self.subTest(pid):
                preset = PRESETS_BY_ID[pid]
                self.assertTrue(callable(load_loader(preset.loader)))
                self.assertIn(preset.source, SOURCE_LABELS)
                src, items = load([pid])
                stat = src.stats[preset.source]
                self.assertIsNone(src.load_errors)
                self.assertIsNone(stat["error"])
                self.assertGreater(stat["items"], 5)
                self.assertGreater(stat["parsed"], 5)
                self.assertTrue(all(it["source"] == preset.source for it in items))
                deals = [analyze(it) for it in items]
                self.assertTrue(any(i.flight and i.price and i.origin == "home" for i in deals))

    def test_fast_lane_loads_only_fast_presets(self):
        web = FakeWeb()
        src, _ = load(sorted(LOADER_PRESETS), web, lanes={"fast"})
        self.assertEqual(set(src.stats), {"feed:secretflying", "feed:travel-dealz-rest"})
        self.assertEqual(web.count(efa.LIST_URL), 0)

    def test_loader_state_survives_between_runs(self):
        class Kv:
            def __init__(self):
                self.data = {}

            def source_state(self, name):
                return json.loads(json.dumps(self.data.get(name, {})))

            def set_source_state(self, name, state):
                self.data[name] = state

        kv = Kv()
        web = FakeWeb()
        first = FeedSource(presets=["secretflying"], max_age_days=OLD, search_enabled=False,
                           state_store=kv)
        with mock.patch.object(http, "request", side_effect=web):
            n_first = len(first.load())
            calls = web.count(sf.API_URL)
            second = FeedSource(presets=["secretflying"], max_age_days=OLD,
                                search_enabled=False, state_store=kv)
            n_second = len(second.load())
        self.assertGreater(calls, 1)                       # erster Lauf: mehrere Seiten
        self.assertEqual(web.count(sf.API_URL) - calls, 1)  # danach reicht Seite 1
        self.assertEqual(n_first, n_second)                 # Rest aus dem gespeicherten Zustand


class HubAcrossSourcesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, cls.items = load(sorted(LOADER_PRESETS))
        cls.alerts = cn_alerts()

    def offers(self, alert_id, settings=None):
        src = FeedSource(presets=sorted(LOADER_PRESETS), search_enabled=False)
        src.load = lambda: self.items
        src.hub_settings = settings
        return src.fetch(self.alerts[alert_id])

    def test_traveldealz_rest_budapest_is_hub_deal(self):
        hubs = {(o.source, o.via_hub, o.price) for o in self.offers("de-cn-business") if o.via_hub}
        self.assertIn(("feed:travel-dealz-rest", "BUD", 1620), hubs)     # 1.440 + 180
        frankfurt = [o for o in self.offers("de-cn-business") if "Frankfurt" in o.title]
        self.assertTrue(frankfurt and all(o.via_hub is None for o in frankfurt))

    def test_secretflying_istanbul_is_hub_offer_above_limit(self):
        ist = [o for o in self.offers("de-cn-economy") if o.source == "feed:secretflying"]
        self.assertEqual([(o.via_hub, o.price, o.surcharge) for o in ist], [("IST", 629, 180)])
        self.assertGreater(ist[0].price, self.alerts["de-cn-economy"].max_price)

    def test_hubs_off_keeps_foreign_out(self):
        off = self.offers("de-cn-business", HubSettings(enabled=False))
        self.assertTrue(off)
        self.assertFalse(any(o.via_hub for o in off))
        self.assertFalse(any("Budapest" in o.title for o in off))

    def test_short_haul_economy_gets_no_hub(self):
        self.assertFalse(any(o.via_hub for o in self.offers("zrh-ham")))

    def test_yhbu_feed_budapest_is_hub_deal(self):
        xml = (FIX / "feeds" / "yhbu-premium-tickets.xml").read_bytes()
        src = FeedSource(presets=["yhbu"], max_age_days=OLD, search_enabled=False)
        with mock.patch.object(http, "request", return_value=xml):
            offers = src.fetch(self.alerts["de-cn-business"])
        self.assertIn(("BUD", 1670), [(o.via_hub, o.price) for o in offers])


class HealthSeesNewSourcesTest(unittest.TestCase):
    def test_rules_for_new_sources(self):
        self.assertEqual(health.rule_for("feed:secretflying").stale_hours, 10)
        self.assertEqual(health.rule_for("feed:travel-dealz-rest").stale_hours, 20)
        self.assertEqual(health.rule_for("feed:errorfarealerts").stale_hours, 48)
        self.assertFalse(health.rule_for("feed:yhbu").need_items)     # postet etwa wöchentlich

    def test_collect_after_loader_run(self):
        src, _ = load(sorted(LOADER_PRESETS))
        states = health.collect([src], datetime.now(timezone.utc))
        self.assertEqual({k: v["state"] for k, v in states.items()
                          if k != "feed:errorfarealerts"},
                         {"feed:secretflying": health.OK, "feed:travel-dealz-rest": health.OK})
        # ErrorFareAlerts-Fixture hat feste Zeiten: nur "gelesen", Alter hängt vom Datum ab
        self.assertIn(states["feed:errorfarealerts"]["state"], (health.OK, health.STALE))
        self.assertEqual(states["feed:errorfarealerts"]["items"], 10)

    def test_broken_loader_is_error_not_crash(self):
        def down(url, **kw):
            raise http.HttpError("HTTP 503", 503)
        src = FeedSource(presets=sorted(LOADER_PRESETS), search_enabled=False)
        with mock.patch.object(http, "request", side_effect=down), \
                self.assertRaises(SourceError):          # nur der Feed-Teil des Laufs fällt aus
            src.load()
        states = health.collect([src], datetime.now(timezone.utc))
        self.assertEqual({v["state"] for v in states.values()}, {health.ERROR})
        self.assertEqual(set(states), {f"feed:{p}" for p in LOADER_PRESETS})


class FastRunTest(unittest.TestCase):
    def test_fast_run_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Context(tmp, ROOT / "config" / "alerts.toml", env={})
            ctx.seed()
            web = FakeWeb()
            with mock.patch.object(http, "request", side_effect=web):
                row = execute(ctx, mode="fast", notifiers=[])
            self.assertEqual(row["errors"], [])
            self.assertEqual(row["trigger"], "manual-fast")
            self.assertEqual(web.count(efa.LIST_URL), 0)          # normaler Takt bleibt aus
            with ctx.store() as store:
                report = {s["id"]: s for s in health.report(store)["sources"]}
                deals = store.deals(alert_id="de-cn-business")
            self.assertEqual({k: v["state"] for k, v in report.items()},
                             {"feed:secretflying": health.OK, "feed:travel-dealz-rest": health.OK})
            self.assertEqual(report["feed:secretflying"]["threshold_hours"], 10)
            budapest = [d for d in deals if "Budapest" in d["headline"]]
            self.assertEqual(len(budapest), 1)
            self.assertEqual(budapest[0]["price"], 1620)
            self.assertIn("Nachbar-Hub Budapest", json.dumps(budapest[0], ensure_ascii=False))


class SettingsMigrationV3Test(unittest.TestCase):
    def test_saved_presets_get_new_sources_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Context(tmp, ROOT / "config" / "alerts.toml", env={})
            with ctx.store() as st:
                st.kv_set("seeded", True)
                st.kv_set("settings_version", 2)
                st.kv_set("feed_presets", ["travel-dealz", "fly4free"])
            ctx.seed()
            with ctx.store() as st:
                app = AppSettings(st, {})
                self.assertEqual(app.get("feed_presets"),
                                 ["travel-dealz", "fly4free", *NEW_PRESETS_V3])
                self.assertEqual(st.kv_get("settings_version"), SETTINGS_VERSION)
                # danach bewusst abgewählt -> bleibt ab
                app.update({"feed_presets": ["travel-dealz"]})
                self.assertEqual(migrate_settings(st), [])
                self.assertEqual(AppSettings(st, {}).get("feed_presets"), ["travel-dealz"])

    def test_new_presets_are_defaults(self):
        for pid in NEW_PRESETS_V3:
            self.assertTrue(PRESETS_BY_ID[pid].enabled, pid)


class VerifyKeepsInsightTest(unittest.TestCase):
    def test_google_insight_lands_in_offer(self):
        insight = {"level": "low", "typical_low": 90, "typical_high": 150}

        class Google(ti.FakeGoogle):
            def verify(self, *args, **criteria):
                found = super().verify(*args, **criteria)
                return {**found, "insight": insight} if found else found

        d = ti.VerifyTest.deal(None, 89, max_price=200)
        self.assertEqual(verify_deal(d, Google(price=92)), "confirmed")
        self.assertEqual(d.offer.insight, insight)


if __name__ == "__main__":
    unittest.main()
