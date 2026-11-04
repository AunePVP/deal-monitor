"""Offline-Demo mit erfundenen Beispieldaten (tests/fixtures) - keine echten Preise."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

from . import config as config_mod
from . import notify, report
from .models import Alert, Offer
from .monitor import run
from .sources.feed import FeedSource
from .sources.travelpayouts import TravelpayoutsSource
from .store import Store

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
DEMO_TODAY = date(2026, 11, 4)


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
    def load(self):
        if self._items is None:
            xml = (FIXTURES / "travel-dealz-feed.xml").read_text()
            xml = xml.replace("PUBDATE", format_datetime(datetime.now().astimezone()))
            self._items = self.parse(xml.encode(), "demo")
        return self._items


DEMO_FILES = {("ZRH", "HAM"): "travelpayouts_zrh_ham.json",
              ("FRA", "SHA"): "travelpayouts_fra_sha.json"}
DEMO_HISTORY = {
    "ZRH-HAM-RT-economy": [169, 189, 199, 175, 210, 185, 195, 179, 205],
    "FRA-SHA-RT-economy": [690, 740, 810, 720, 765, 699, 780, 755],
}


def check_fixtures() -> None:
    if not (FIXTURES / "travel-dealz-feed.xml").exists():
        raise FileNotFoundError(f"Demo-Daten fehlen: {FIXTURES} (Ordner tests/fixtures mitkopieren)")


def add_history(store: Store) -> None:
    """Etwas künstliche Preis-Historie, damit der Rabatt gegen den Median sichtbar wird."""
    past = datetime.now() - timedelta(days=10)
    for route, prices in DEMO_HISTORY.items():
        o, d, *_ = route.split("-")
        store.add_prices([Offer(source="travelpayouts", origin=o, destination=d, price=p,
                                departure=DEMO_TODAY + timedelta(days=i))
                          for i, p in enumerate(prices)], when=past)


def make_sources(cfg=None, names=("travelpayouts", "feed")) -> list:
    """Fixture-Quellen statt echter APIs (frisch pro Lauf)."""
    out = []
    if "travelpayouts" in names:
        out.append(FixtureSource(DEMO_FILES))
    if "feed" in names:
        out.append(FixtureFeed())
    return out


def demo(config_path: str) -> Path:
    cfg = config_mod.load(config_path)
    cfg.settings.request_delay = 0
    store = Store(":memory:")
    add_history(store)
    run(cfg, make_sources(), [notify.Console()], store, today=DEMO_TODAY)
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
    scheduler.run_now(trigger="demo")
