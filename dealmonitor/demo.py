"""Offline-Demo mit erfundenen Beispieldaten (tests/fixtures) - keine echten Preise.

Quellen der Demo (kein Netz):

* Travelpayouts: JSON-Fixtures (erfundene Preise ZRH-HAM, FRA-SHA)
* Google Flights: die gespeicherte Ergebnisseite ZRH-HAM (echte Preise vom 04.11.2026) für
  Zürich ⇄ Hamburg Economy hin/zurück, für alle anderen Abfragen eine "keine Flüge"-Seite
* Deal-Seiten: die Beispiel-Feed-Fixture (inkl. zwei Radar-Beispielen: Error Fare und
  Business Class ≤ 1.300 €)

Die Google-Gegenprüfung ist an (wie im Betrieb): Für den erfundenen 89-€-Deal
Zürich ⇄ Hamburg (15.–19.01.2027) antwortet die Demo mit einer Kopie der echten Seite, deren
Bestpreis auf 92 € gesetzt ist - so zeigt die Demo eine "Google Flights bestätigt"-Karte.
Alle anderen Gegenprüfungen bekommen "keine Flüge" und erscheinen als "nicht gegengeprüft".
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import config as config_mod
from . import notify, report
from .models import Alert, Offer
from .monitor import run
from .sources.feed import PRESETS_BY_ID, FeedSource
from .sources.googleflights import GoogleFlightsSource
from .sources.travelpayouts import TravelpayoutsSource
from .store import Store

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
DEMO_TODAY = date(2026, 11, 4)
GOOGLE_FIXTURE = "google_flights_zrh_ham.html"
#: Gegenprüfungen, die die Demo bestätigt: Suchanfrage -> (echter Bestpreis, Demo-Preis)
DEMO_VERIFIED = {"Flights from ZRH to HAM on 2027-01-15 through 2027-01-19": ("168", "92")}
NO_FLIGHTS_PAGE = ('<html><head><title>Google Flights</title></head><body>'
                   '<div>Für diese Suche wurden keine Flüge gefunden.</div></body></html>')


class FixtureSource:
    """Liefert Travelpayouts-Antworten aus JSON-Dateien statt aus dem Netz."""

    name = "travelpayouts"
    records_history = True

    def __init__(self, files: dict[tuple[str, str], str]) -> None:
        self.files = files
        self.parser = TravelpayoutsSource(token="demo")

    def fetch(self, alert: Alert) -> list[Offer]:
        offers = []
        for pair in alert.pairs:
            if pair in self.files and alert.cabin == "economy" and alert.roundtrip:
                data = json.loads((FIXTURES / self.files[pair]).read_text())
                offers += [o for o in (self.parser.parse_v3(r, alert) for r in data["data"]) if o]
        return offers


class FixtureFeed(FeedSource):
    """Deal-Seiten-Quelle (Preset Travel-Dealz) aus der Beispiel-Fixture statt aus dem Netz."""

    def __init__(self, **kw) -> None:
        kw.setdefault("presets", ["travel-dealz"])
        kw.setdefault("search_enabled", False)
        super().__init__(**kw)

    def _feed_jobs(self):
        preset = PRESETS_BY_ID["travel-dealz"]
        return [(preset.urls[0], preset)]

    def _fetch_one(self, url: str) -> list[dict]:
        xml = (FIXTURES / "travel-dealz-feed.xml").read_text()
        xml = xml.replace("PUBDATE", format_datetime(datetime.now().astimezone()))
        return self.parse(xml.encode(), url)


def google_fixture_page(url: str) -> str:
    """Demo-Antwort für eine Google-Flights-URL: die echte Beispielseite für Zürich ⇄ Hamburg
    (Economy, hin und zurück), sonst eine Seite ohne Flüge."""
    q = parse_qs(urlsplit(url).query).get("q", [""])[0]
    if q in DEMO_VERIFIED:
        real, fake = DEMO_VERIFIED[q]
        page = (FIXTURES / GOOGLE_FIXTURE).read_text(encoding="utf-8")
        return page.replace(f"{real} Euro", f"{fake} Euro").replace(f"{real} €", f"{fake} €")
    zrh_ham = q.startswith("Flights from ZRH to HAM") or q.startswith("Flights from HAM to ZRH")
    if zrh_ham and " through " in q and " class" not in q:
        return (FIXTURES / GOOGLE_FIXTURE).read_text(encoding="utf-8")
    return NO_FLIGHTS_PAGE


def fixture_google(**kw) -> GoogleFlightsSource:
    opts = dict(max_requests=100_000, delay=0, today=DEMO_TODAY, fetcher=google_fixture_page,
                seed=1)
    opts.update(kw)
    return GoogleFlightsSource(**opts)


DEMO_FILES = {("ZRH", "HAM"): "travelpayouts_zrh_ham.json",
              ("FRA", "SHA"): "travelpayouts_fra_sha.json"}
DEMO_HISTORY = {
    "ZRH-HAM-RT-economy": [169, 189, 199, 175, 210, 185, 195, 179, 205],
    "FRA-SHA-RT-economy": [690, 740, 810, 720, 765, 699, 780, 755],
}
#: Einstellungen, die die Demo beim ersten Start setzt (in der Oberfläche änderbar)
DEMO_SETTINGS: dict = {}


def check_fixtures() -> None:
    for name in ("travel-dealz-feed.xml", GOOGLE_FIXTURE):
        if not (FIXTURES / name).exists():
            raise FileNotFoundError(
                f"Demo-Daten fehlen: {FIXTURES / name} (Ordner tests/fixtures mitkopieren)")


def add_history(store: Store) -> None:
    """Etwas künstliche Preis-Historie, damit der Rabatt gegen den Median sichtbar wird."""
    past = datetime.now() - timedelta(days=10)
    for route, prices in DEMO_HISTORY.items():
        o, d, *_ = route.split("-")
        store.add_prices([Offer(source="travelpayouts", origin=o, destination=d, price=p,
                                departure=DEMO_TODAY + timedelta(days=i))
                          for i, p in enumerate(prices)], when=past)


def make_sources(cfg=None, names=("travelpayouts", "google_flights", "feeds")) -> list:
    """Fixture-Quellen statt echter APIs (frisch pro Lauf). Amadeus gibt es in der Demo nicht."""
    out = []
    for n in names:
        if n == "travelpayouts":
            out.append(FixtureSource(DEMO_FILES))
        elif n == "google_flights":
            out.append(fixture_google(scan_months=cfg.settings.scan_months if cfg else 6))
        elif n in ("feeds", "feed"):
            out.append(FixtureFeed())
    return out


def demo(config_path: str) -> Path:
    cfg = config_mod.load(config_path)
    cfg.settings.request_delay = 0
    store = Store(":memory:")
    add_history(store)
    from .radar import RadarSettings
    sources = make_sources(cfg)
    verifier = next((s for s in sources if s.name == "google_flights"), None)
    run(cfg, sources, [notify.Console()], store, today=DEMO_TODAY,
        radar_settings=RadarSettings(), verifier=verifier)
    out = Path(cfg.settings.report).with_name("demo-report.html")
    return report.write(cfg, store, str(out), demo=True)


def server_context(config_path=None):
    """Kontext für ``serve --demo``: temporäres Datenverzeichnis, Fixture-Quellen.

    Benachrichtigungs-Zugangsdaten aus der Umgebung werden bewusst NICHT
    übernommen - erfundene Demo-Deals sollen nie an echte Kanäle gehen.
    """
    import atexit
    import os
    import shutil
    import tempfile

    from .appsettings import SECRETS, VALUES, Context, env_name

    check_fixtures()
    tmp = tempfile.mkdtemp(prefix="dealmonitor-demo-")
    atexit.register(shutil.rmtree, tmp, True)
    blocked = {env_name(n) for n in [*SECRETS, *VALUES]}
    env = {k: v for k, v in os.environ.items() if k not in blocked and k != "DEALMONITOR_DATA"}
    return Context(tmp, config_path, demo=True, env=env,
                   source_factory=lambda cfg, names: make_sources(cfg, names),
                   today=DEMO_TODAY)


def prepare_server(ctx, scheduler) -> None:
    """Einmal mit künstlicher Preis-Historie prüfen, damit gleich Deals da sind."""
    with ctx.store() as store:
        add_history(store)
        for key, value in DEMO_SETTINGS.items():
            store.kv_set(key, value)
    scheduler.run_now(trigger="demo")
