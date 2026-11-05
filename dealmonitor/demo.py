"""Offline-Demo mit erfundenen Beispieldaten (tests/fixtures) - keine echten Preise.

Quellen der Demo (kein Netz):

* Travelpayouts: JSON-Fixtures (erfundene Preise ZRH-HAM, FRA-SHA)
* Google Flights: die gespeicherte Ergebnisseite ZRH-HAM (echte Preise vom 04.11.2026) für
  Zürich ⇄ Hamburg Economy hin/zurück, für alle anderen Abfragen eine "keine Flüge"-Seite
* Deal-Seiten: die Beispiel-Feed-Fixture (inkl. zwei Radar-Beispielen: Error Fare und
  Business Class ≤ 1.300 €), dazu erfundene Artikel von Secret Flying, ErrorFareAlerts und
  premium-flights (:data:`DEMO_ARTICLES`): derselbe China-Deal bei drei Seiten (Erstmeldung,
  Quellenliste), ein Abflug ab Nachbar-Hub Budapest mit Ablaufdatum und eine veraltete Quelle
  für die Ampel der Quellen-Gesundheit

Die Google-Gegenprüfung ist an (wie im Betrieb): Für den erfundenen 89-€-Deal
Zürich ⇄ Hamburg (15.–19.01.2027) antwortet die Demo mit einer Kopie der echten Seite, deren
Bestpreis auf 92 € gesetzt ist - so zeigt die Demo eine "Google Flights bestätigt"-Karte.
Alle anderen Gegenprüfungen bekommen "keine Flüge" und erscheinen als "nicht gegengeprüft".
Diese Seite trägt zusätzlich ein erfundenes Google-Preisniveau ("niedrig", 62 Tage Verlauf).

Google-Scan: Zürich → Hamburg one-way (Tagesrotation) bekommt die echte Seite vom 05.11.2026
mit Preisniveau; am ersten abgefragten Tag (:func:`oneway_deal_day`) ist der Bestpreis auf
39 € gesetzt.
Frankfurt → Peking (Economy) bekommt die echte Seite mit 559 € statt 720 € - knapp über der
Grenze, aber unter Googles typischer Spanne: ein Beinahe-Deal.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import config as config_mod
from . import notify, report
from .models import Alert, Offer
from .monitor import run
from .sources.feed import PRESETS_BY_ID, FeedSource, normalize_item
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


def demo_articles(now: datetime) -> dict[str, list[dict]]:
    """Erfundene Artikel weiterer Deal-Seiten (Loader-Format), Zeiten relativ zu ``now``."""
    ago = lambda hours: now - timedelta(hours=hours)   # noqa: E731
    business = {"cabin": "business", "oneway": False}
    return {
        "secretflying": [
            {"title": "Frankfurt, Germany to Beijing, China for only €1749 roundtrip in "
                      "Business Class", "link": "https://bsky.app/profile/secretflying.bsky.social"
                      "/post/demo-fra-pek", "published": ago(27), "price_eur": 1749.0,
             "origin_codes": ["FRA"], "dest_codes": ["PEK"], **business},
            {"title": "Budapest, Hungary to Shanghai, China for only €1490 roundtrip in "
                      "Business Class", "link": "https://bsky.app/profile/secretflying.bsky.social"
                      "/post/demo-bud-sha", "published": ago(5), "price_eur": 1490.0,
             "origin_codes": ["BUD"], "dest_codes": ["SHA"], "expires": now + timedelta(days=6),
             **business},
        ],
        "errorfarealerts": [
            {"title": "Business Class: Frankfurt – Peking ab 1.759 €",
             "link": "https://errorfarealerts.com/deals/flights/demo-fra-pek-business",
             "published": ago(2), "price_eur": 1759.0, "origin_codes": ["FRA"],
             "dest_codes": ["PEK"], **business},
            {"title": "Air China: Frankfurt – Peking ab 439 €",
             "link": "https://errorfarealerts.com/deals/flights/demo-fra-pek",
             "published": ago(1), "price_eur": 439.0, "origin_codes": ["FRA"],
             "dest_codes": ["PEK"], "cabin": "economy", "oneway": False},
        ],
        # neuester Artikel 30 h alt (Grenze 24 h): zeigt "Veraltet" in der Quellen-Gesundheit
        "premium-flights": [
            {"title": "Business Class nach Bangkok ab 1.390 €",
             "link": "https://www.premium-flights.de/demo/bangkok-business/",
             "published": ago(30), "price_eur": 1390.0, "cabin": "business", "oneway": False},
        ],
    }


#: Deal-Seiten der Demo (Server und ``demo``-Befehl)
DEMO_PRESETS = ["secretflying", "travel-dealz", "errorfarealerts", "premium-flights"]
#: So viele Stunden vor dem Start "erschienen" die Travel-Dealz-Artikel der Demo
DEMO_FEED_AGE_HOURS = 20


class FixtureFeed(FeedSource):
    """Deal-Seiten-Quelle aus Fixtures statt aus dem Netz: Travel-Dealz aus der Beispiel-Feed-
    Fixture, weitere Presets aus :func:`demo_articles` (Standard: nur Travel-Dealz)."""

    def __init__(self, *, age_hours: float = 0, **kw) -> None:
        kw.setdefault("presets", ["travel-dealz"])
        kw.setdefault("search_enabled", False)
        super().__init__(**kw)
        self.age_hours = age_hours

    def _feed_jobs(self):
        return [(p.jobs[0], p) for p in self.presets if p.jobs]

    def _fetch_job(self, url: str, preset) -> list[dict]:
        if preset is not None and preset.id != "travel-dealz":
            now = datetime.now(timezone.utc)
            raw = demo_articles(now).get(preset.id, [])
            return [it for it in (normalize_item(r, f"loader:{preset.id}", now=now,
                                                 max_age_days=self.max_age_days) for r in raw)
                    if it]
        return self._fetch_one(PRESETS_BY_ID["travel-dealz"].urls[0])

    def _fetch_one(self, url: str) -> list[dict]:
        xml = (FIXTURES / "travel-dealz-feed.xml").read_text()
        when = datetime.now().astimezone() - timedelta(hours=self.age_hours)
        xml = xml.replace("PUBDATE", format_datetime(when))
        return self.parse(xml.encode(), url)


#: One-way Zürich → Hamburg: an diesem Tag zeigt die Demo 39 € statt 126 € (siehe unten)
_ONEWAY_DAY: list[str] = []
#: echte Seiten mit Preisniveau (ds:1) und die Demo-Änderung: (Datei, echter, Demo-Preis,
#: echte, Demo-Differenz zum Normalpreis)
ONEWAY_PAGE = ("google_flights_zrh_ham_oneway_ds1.html", 126, 39, -22, 66)
PEK_PAGE = ("google_flights_fra_pek_ds1.html", 720, 559, 3, 164)


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _with_price(page: str, real: int, fake: int) -> str:
    return page.replace(f"{real} Euro", f"{fake} Euro").replace(f"{real} €", f"{fake} €")


def _ds1_page(spec: tuple, change: bool = True) -> str:
    """Echte Seite mit Preisniveau; ``change``: Bestpreis und Googles Tiefstpreis ersetzt."""
    name, real, fake, real_saving, fake_saving = spec
    page = _page(name)
    if not change:
        return page
    page = page.replace(f"[null,{real}],", f"[null,{fake}],")
    page = page.replace(f"[null,{real_saving}],", f"[null,{fake_saving}],", 1)
    return _with_price(page, real, fake)


def demo_insight_script(lowest: int, typical: int, low: int, high: int,
                        until: date = DEMO_TODAY, days: int = 62) -> str:
    """Erfundenes Google-Preisniveau als ``ds:1``-Block (Form wie auf der echten Seite):
    Verlauf über ``days`` Tage im typischen Bereich, in den letzten Tagen fällt der Preis."""
    start = datetime(until.year, until.month, until.day, tzinfo=timezone.utc)
    points = []
    for i in range(days):
        price = round(typical + 14 * math.sin(i / 6.5) + 6 * math.sin(i / 2.1))
        price = {days - 3: round((typical + lowest) / 1.7), days - 2: round(lowest * 1.25),
                 days - 1: lowest}.get(i, price)
        ts = int((start - timedelta(days=days - 1 - i)).timestamp() * 1000)
        points.append([ts, price])
    data = [1, [None, lowest], [None, typical], [None, typical - lowest], [None, low],
            [None, high], 1, points]
    return ("<script>AF_initDataCallback({key: 'ds:1', hash: '1', data:"
            f"{json.dumps(data, separators=(',', ':'))}, sideChannel: {{}}}});</script>")


def oneway_deal_day(day: str) -> str:
    """Der Tag mit dem 39-€-Preis: der erste, den die Rotation abfragt (welche Tage ein Lauf
    prüft, hängt von der Uhrzeit ab) - danach fest, solange der Demo-Server läuft."""
    if not _ONEWAY_DAY:
        _ONEWAY_DAY.append(day)
    return _ONEWAY_DAY[0]


def google_fixture_page(url: str) -> str:
    """Demo-Antwort für eine Google-Flights-URL: die echten Beispielseiten für Zürich ⇄ Hamburg
    (Economy, hin und zurück bzw. one-way) und Frankfurt → Peking (Economy, hin und zurück),
    sonst eine Seite ohne Flüge."""
    q = parse_qs(urlsplit(url).query).get("q", [""])[0]
    if q in DEMO_VERIFIED:
        real, fake = DEMO_VERIFIED[q]
        page = _with_price(_page(GOOGLE_FIXTURE), int(real), int(fake))
        return page.replace("</body>", demo_insight_script(int(fake), 189, 160, 230) + "</body>")
    economy = " class" not in q
    zrh_ham = q.startswith("Flights from ZRH to HAM") or q.startswith("Flights from HAM to ZRH")
    if zrh_ham and " through " in q and economy:
        return _page(GOOGLE_FIXTURE)
    if q.startswith("Flights from ZRH to HAM on ") and q.endswith(" one way"):
        day = q.split(" on ")[1].split(" ")[0]
        return _ds1_page(ONEWAY_PAGE, change=day == oneway_deal_day(day))
    if q.startswith(("Flights from FRA to BJS", "Flights from FRA to PEK")) \
            and " through " in q and economy:
        return _ds1_page(PEK_PAGE)
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


class StateStore:
    """Quellen-Zustand (Rotations-Gedächtnis) der Demo-Quellen in der Datenbank - je Zugriff
    eine kurze eigene Verbindung, wie die Quellen sie im Betrieb über den Lauf-Store nutzen."""

    def __init__(self, db_path) -> None:
        self.db_path = db_path

    def source_state(self, name: str) -> dict:
        with Store(self.db_path) as store:
            return store.source_state(name)

    def set_source_state(self, name: str, value: dict) -> None:
        with Store(self.db_path) as store:
            store.set_source_state(name, value)


def make_sources(cfg=None, names=("travelpayouts", "google_flights", "feeds"),
                 state_store=None) -> list:
    """Fixture-Quellen statt echter APIs (frisch pro Lauf). Amadeus gibt es in der Demo nicht."""
    out = []
    for n in names:
        if n == "travelpayouts":
            out.append(FixtureSource(DEMO_FILES))
        elif n == "google_flights":
            out.append(fixture_google(scan_months=cfg.settings.scan_months if cfg else 6,
                                      state_store=state_store))
        elif n in ("feeds", "feed"):
            out.append(FixtureFeed(presets=DEMO_PRESETS, age_hours=DEMO_FEED_AGE_HOURS))
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
    ctx = Context(tmp, config_path, demo=True, env=env, today=DEMO_TODAY)
    states = StateStore(ctx.db_path)
    ctx.source_factory = lambda cfg, names: make_sources(cfg, names, states)
    return ctx


def prepare_server(ctx, scheduler) -> None:
    """Einmal mit künstlicher Preis-Historie prüfen, damit gleich Deals da sind."""
    with ctx.store() as store:
        add_history(store)
        for key, value in DEMO_SETTINGS.items():
            store.kv_set(key, value)
    scheduler.run_now(trigger="demo")
