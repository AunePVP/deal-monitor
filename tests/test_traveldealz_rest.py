"""Travel-Dealz über WP-REST (``sources/traveldealz_rest.py``) - ohne Netz.

Fixtures unter ``tests/fixtures/traveldealz_rest/``: ``deal.json``, ``ticker.json``,
``origins.json``, ``destinations.json`` sind echte Antworten vom 05.11.2026 11:40 UTC
(Fenster 14 Tage, ``_fields`` wie im Modul); ``sample-deal-flights.json`` und
``td-kritiker.json`` stammen aus der Recherche (ältere Form ohne ``*_gmt``/``link``).
"""

import copy
import json
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from dealmonitor import currency, http, radar
from dealmonitor.models import Alert
from dealmonitor.sources import SourceError
from dealmonitor.sources import feed, traveldealz_rest as td
from dealmonitor.sources.feed import (
    PRESETS_BY_ID, TRAVEL_DEALZ_PROFILE, FeedSource, Preset, analyze, is_current, normalize_item,
)
from dealmonitor.store import Store

FIX = Path(__file__).parent / "fixtures" / "traveldealz_rest"
#: Zeitpunkt der Aufnahme von deal.json/ticker.json
FIXTURE_NOW = datetime(2026, 11, 5, 11, 40, 43, tzinfo=timezone.utc)
PEKING = 459024            # "Peking: Business Class von Frankfurt ab 1.123€ …"
TIME_KEYS = ("date", "date_gmt", "modified", "modified_gmt")


def fixture(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def shifted(posts, delta):
    """Alle Zeitangaben um ``delta`` verschieben (Fixture "jetzt" erleben)."""
    out = copy.deepcopy(posts)
    for p in out:
        for k in TIME_KEYS:
            if p.get(k):
                p[k] = (datetime.fromisoformat(p[k]) + delta).strftime("%Y-%m-%dT%H:%M:%S")
        s = (p.get("meta") or {}).get("suspend_date")
        if s:
            p["meta"]["suspend_date"] = (datetime.fromisoformat(s.replace("T", " ")) + delta) \
                .strftime("%Y-%m-%d %H:%M:%S")
    return out


def post(pid=1, title="Peking ab 499€ mit Air China von Frankfurt", **kw):
    base = {"id": pid, "date_gmt": "2026-11-05T08:00:00", "modified_gmt": "2026-11-05T09:00:00",
            "link": f"https://travel-dealz.de/deal/test-{pid}/", "title": {"rendered": title},
            "meta": {"suspend_date": "2026-11-20 23:59:00"}, "categories": [1757],
            "origins": [2940], "destinations": [112], "tags": []}
    base.update(kw)
    return base


def _modified(row):
    value = row.get("modified_gmt")
    return value if isinstance(value, str) else ""


class FakeAPI:
    """Ersetzt ``http.request``: liefert Beiträge je Endpunkt, filtert ``modified_after``,
    blättert wie WordPress (Seite über TotalPages -> 400) und merkt sich die Abrufe."""

    def __init__(self, deal=(), ticker=(), origins=(), destinations=(), errors=None):
        self.data = {"deal": list(deal), "ticker": list(ticker), "origins": list(origins),
                     "destinations": list(destinations)}
        self.errors = errors or {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, **kw):
        parts = urllib.parse.urlsplit(url)
        kind = parts.path.rsplit("/", 1)[-1]
        q = dict(urllib.parse.parse_qsl(parts.query))
        self.calls.append((kind, q))
        if kind in self.errors:
            err = self.errors[kind]
            if isinstance(err, Exception):
                raise err
            return http.Response(err, 200, {}, url)
        rows = self.data[kind]
        if "modified_after" in q:
            rows = [r for r in rows if not isinstance(r, dict) or _modified(r) > q["modified_after"]
                    or not _modified(r)]
            rows.sort(key=lambda r: _modified(r) if isinstance(r, dict) else "", reverse=True)
        if "include" in q:
            wanted = {int(i) for i in q["include"].split(",")}
            rows = [r for r in rows if r["id"] in wanted]
        per_page, page = int(q.get("per_page", 10)), int(q.get("page", 1))
        if per_page > 100:
            raise http.HttpError("HTTP 400", 400)
        pages = max(1, -(-len(rows) // per_page))
        if page > pages:
            raise http.HttpError("HTTP 400 rest_post_invalid_page_number", 400)
        body = json.dumps(rows[(page - 1) * per_page:page * per_page]).encode()
        return http.Response(body, 200, {"x-wp-total": str(len(rows)),
                                         "x-wp-totalpages": str(pages)}, url)

    def kinds(self):
        return [k for k, _ in self.calls]


def live_api(delta=timedelta(0)):
    return FakeAPI(shifted(fixture("deal.json"), delta), shifted(fixture("ticker.json"), delta),
                   fixture("origins.json"), fixture("destinations.json"))


def run(api, state=None, now=FIXTURE_NOW):
    state = {} if state is None else state
    with mock.patch.object(td.http, "request", api):
        return td.fetch_items(state=state, now=now), state


def info_of(item):
    it = normalize_item(item, "loader:travel-dealz-rest", now=FIXTURE_NOW)
    it["profile"] = TRAVEL_DEALZ_PROFILE
    return it, analyze(it)


class HelperTest(unittest.TestCase):
    def test_title_price(self):
        self.assertEqual(td.title_price("Peking: Business Class von Frankfurt ab 1.123€"),
                         (1123.0, "€"))
        self.assertEqual(td.title_price("Zürich – Bangkok für 450 CHF"), (450.0, "CHF"))
        self.assertEqual(td.title_price("Dubai nur 1.299,50 € retour"), (1299.5, "€"))
        # zwei Preise: der erste zählt
        self.assertEqual(td.title_price("New York ab 640€ (540€ mit Amex Platinum)"),
                         (640.0, "€"))
        self.assertIsNone(td.title_price("Aegean Jugendrabatte: 50% Rabatt"))
        self.assertIsNone(td.title_price(None))

    def test_suspend_date_berlin_time(self):
        self.assertEqual(td.suspend_date({"meta": {"suspend_date": "2026-11-16 23:59:00"}}),
                         datetime(2026, 11, 16, 22, 59, tzinfo=timezone.utc))
        self.assertEqual(td.suspend_date({"meta": {"suspend_date": "2026-11-09T23:59:00"}}),
                         datetime(2026, 11, 9, 22, 59, tzinfo=timezone.utc))
        for bad in ({}, {"meta": None}, {"meta": []}, {"meta": {"suspend_date": ""}},
                    {"meta": {"suspend_date": 123}}, {"meta": {"suspend_date": "bald"}}):
            self.assertIsNone(td.suspend_date(bad), bad)

    def test_term_codes(self):
        self.assertEqual(td.term_codes("Basel [BSL,MLH,EAP]"), ["BSL", "MLH", "EAP"])
        self.assertEqual(td.term_codes("Hong Kong [HK,HKG]"), ["HKG"])
        self.assertEqual(td.term_codes("China [CN]"), [])
        self.assertEqual(td.term_codes("Schweiz"), [])


@mock.patch.object(feed, "utcnow", lambda: FIXTURE_NOW)
class FetchTest(unittest.TestCase):
    def test_full_fetch_from_live_fixture(self):
        api = live_api()
        items, state = run(api)
        self.assertEqual(api.kinds(), ["deal", "ticker", "origins", "destinations"])
        for kind, q in api.calls[:2]:
            self.assertEqual((q["categories"], q["per_page"], q["orderby"], q["order"]),
                             ("24", "100", "modified", "desc"))
            # voller Abruf: die Schnittstelle liefert nur aktive Beiträge -> alle holen
            self.assertNotIn("modified_after", q)
            self.assertIn("meta.suspend_date", q["_fields"])
        # 45 + 12 Beiträge; 2 davon seit über 14 Tagen nicht geändert, laufen aber bis 16.11.
        # (suspend_date) - die bleiben
        self.assertEqual(len(items), 57)
        self.assertTrue(any(i["title"].startswith("12 Ziele in (Süd-)Ostasien") for i in items))
        self.assertEqual(state["full_at"], "2026-11-05T11:40:43")
        self.assertEqual(state["cursor"], {"deal": "2026-11-05T10:18:57",
                                           "ticker": "2026-11-05T10:17:07"})
        self.assertEqual(state["terms"]["origins"]["2993"], ["WAW"])        # nachgeschlagen
        json.dumps(state)                                                  # JSON-tauglich

        peking = next(i for i in items if i["uid"].startswith(f"{PEKING}:"))
        self.assertEqual(peking["uid"], f"{PEKING}:2026-11-03T10:38:50")
        self.assertEqual(peking["link"], "https://travel-dealz.de/deal/turkmenistan-airlines-business-peking/")
        it, info = info_of(peking)
        self.assertTrue(info.flight)
        self.assertEqual((info.price, info.cabin, info.origin), (1123.0, "business", "home"))
        self.assertEqual(info.origin_codes, {"FRA"})
        self.assertEqual(info.dest_codes, {"PEK", "BJS"})
        self.assertEqual(info.expires, datetime(2026, 11, 23, 22, 59, tzinfo=timezone.utc))
        # erschienen am 30.09., aber am 03.11. geändert -> zählt als aktuell
        self.assertEqual(it["published"], datetime(2026, 11, 3, 10, 38, 50, tzinfo=timezone.utc))

    def test_items_price_cabin_origin_from_fixture(self):
        items, _ = run(live_api())
        by_title = {i["title"][:30]: info_of(i)[1] for i in items}
        ny = by_title["New York: Nonstop in der Condo"]       # "ab 640€ (540€ mit Amex …)"
        self.assertEqual((ny.price, ny.cabin), (640.0, "premium"))
        sas = by_title["Oneway: SAS Business Class Tha"]     # "… ab 806€ (Economy ab 233€)"
        self.assertEqual((sas.price, sas.cabin, sas.oneway), (806.0, "business", True))
        self.assertEqual(sas.alt_prices, {"economy": 233.0})
        self.assertEqual(sas.origin, "foreign")               # Abflug Bangkok laut origins
        budapest = by_title["China ab 1.440€ in der China S"]
        self.assertEqual((budapest.origin, budapest.origin_codes), ("foreign", {"BUD"}))
        self.assertTrue({"CTU", "CKG"} <= budapest.dest_codes)
        warsaw = by_title["Qatar Airways: Gabelflüge nach"]
        self.assertEqual(warsaw.origin, "foreign")
        zrh = by_title["Nordamerika ab 903€ in der Ice"]
        self.assertEqual((zrh.origin, zrh.origin_codes), ("home", {"FRA", "ZRH"}))
        # nur Kategorie "Flüge" ohne Klasse: Versicherung/Rabatt, kein Flug-Deal
        self.assertFalse(by_title["AirHelp+: Bis zu 200€ Entschäd"].flight)
        self.assertFalse(by_title["Lufthansa: 5% Rabatt auf Gesch"].flight)
        # Ticker sind dabei (stehen nicht in /deal)
        self.assertIn("Curacao: Nonstop von Düsseldor", by_title)
        priced = [i for i in by_title.values() if i.flight and i.price is not None]
        self.assertGreaterEqual(len(priced), 45)

    def test_foreign_currency_and_hot_tags(self):
        api = FakeAPI([post(1, "Zürich – Bangkok ab 450 CHF mit Swiss", origins=[2971], tags=[94]),
                       post(2, "New York nur 299$ ab Frankfurt", tags=[3091]),
                       post(3, "Peking ab 499€ mit Air China von Frankfurt")])
        items, _ = run(api)
        by_id = {i["uid"].split(":")[0]: i for i in items}
        chf = by_id["1"]
        self.assertEqual(chf["price_eur"], currency.to_eur(450, "CHF"))
        self.assertEqual(chf["hot"], "ERROR FARE")
        it, info = info_of(chf)
        self.assertEqual((info.price, info.origin_codes), (currency.to_eur(450, "CHF"), {"ZRH"}))
        self.assertEqual(radar.hot_reason(it["title"], it["categories"]),
                         "Error Fare laut Überschrift")
        self.assertEqual(by_id["2"]["price_eur"], currency.to_eur(299, "$"))
        self.assertEqual(by_id["2"]["hot"], "EILMELDUNG")
        self.assertNotIn("price_eur", by_id["3"])            # €-Preis liest feed.analyze
        self.assertEqual(info_of(by_id["3"])[1].price, 499.0)

    def test_expired_suspend_date_is_skipped(self):
        api = FakeAPI([post(1, meta={"suspend_date": "2026-11-05 10:00:00"}),   # 09:00 UTC
                       post(2, meta={"suspend_date": "2026-11-05T13:00:00"}),
                       post(3, meta={"suspend_date": ""}),                     # Evergreen
                       post(4, meta={})])
        items, state = run(api)
        self.assertEqual(sorted(i["uid"][0] for i in items), ["2", "3", "4"])
        self.assertEqual(sorted(state["posts"]), ["deal:2", "deal:3", "deal:4"])
        evergreen = next(i for i in items if i["uid"].startswith("3:"))
        self.assertNotIn("expires", evergreen)
        # läuft er später ab, verschwindet er auch aus dem Bestand
        items, state = run(api, state, FIXTURE_NOW + timedelta(hours=2))
        self.assertEqual(sorted(i["uid"][0] for i in items), ["3", "4"])
        self.assertNotIn("deal:2", state["posts"])

    def test_cursor_moves_and_stock_is_kept(self):
        api = live_api()
        items, state = run(api)
        first = len(items)
        later = FIXTURE_NOW + timedelta(minutes=10)
        api.data["deal"].append(post(9, "Shanghai ab 444€ von München", origins=[2941],
                                     destinations=[292], date_gmt="2026-11-05T11:45:00",
                                     modified_gmt="2026-11-05T11:46:00"))
        api.calls.clear()
        items, state = run(api, state, later)
        self.assertEqual(api.kinds(), ["deal", "ticker"])           # Begriffe bekannt
        self.assertEqual(api.calls[0][1]["modified_after"], "2026-11-05T10:18:57")
        self.assertEqual(api.calls[1][1]["modified_after"], "2026-11-05T10:17:07")
        self.assertEqual(len(items), first + 1)                    # Bestand + neu
        self.assertEqual(state["cursor"]["deal"], "2026-11-05T11:45:00")   # minus 1 min
        self.assertEqual(state["cursor"]["ticker"], "2026-11-05T10:17:07")
        self.assertEqual(state["full_at"], "2026-11-05T11:40:43")  # kein Vollabruf
        shanghai = next(i for i in items if i["uid"].startswith("9:"))
        self.assertEqual(shanghai["dest_codes"], ["SHA", "PVG"])
        # ohne Änderung bleibt der Zustand gleich (FeedSource speichert dann nicht)
        before = copy.deepcopy(state)
        run(api, state, later + timedelta(minutes=10))
        self.assertEqual(state, before)

    def test_daily_full_fetch_replaces_stock(self):
        api = live_api()
        _, state = run(api)
        api.data["deal"] = [p for p in api.data["deal"] if p["id"] != PEKING]   # gelöscht
        api.calls.clear()
        items, state = run(api, state, FIXTURE_NOW + timedelta(hours=25))
        self.assertNotIn("modified_after", api.calls[0][1])
        self.assertFalse(any(i["uid"].startswith(f"{PEKING}:") for i in items))
        self.assertEqual(state["full_at"], "2026-11-06T12:40:43")

    def test_incremental_fetch_follows_cursor(self):
        # alter Cursor: ab dort (die Schnittstelle liefert ohnehin nur aktive Beiträge);
        # ohne Cursor: ab jetzt - WINDOW
        state = {"cursor": {"deal": "2026-01-01T00:00:00"}, "full_at": "2026-11-05T11:00:00"}
        api = FakeAPI()
        run(api, state)
        self.assertEqual(api.calls[0][1]["modified_after"], "2026-01-01T00:00:00")
        self.assertEqual(api.calls[1][1]["modified_after"], "2026-10-22T11:40:43")

    def test_old_post_with_future_suspend_date_stays(self):
        # vor 20 Tagen zuletzt geändert: mit Ablaufdatum in der Zukunft bleibt er, ohne fällt
        # er weg (Evergreen/Info), mit Ablaufdatum in der Vergangenheit ebenfalls
        old = "2026-10-16T10:00:00"
        api = FakeAPI([post(1, "Shanghai ab 1.486€ in der Business Class von Luxemburg",
                            date_gmt=old, modified_gmt=old,
                            meta={"suspend_date": "2026-11-10 23:59:00"}),
                       post(2, "Peking ab 499€ von Frankfurt", date_gmt=old, modified_gmt=old,
                            meta={"suspend_date": ""}),
                       post(3, "Tokio ab 599€ von Frankfurt", date_gmt=old, modified_gmt=old,
                            meta={"suspend_date": "2026-11-01 23:59:00"})])
        items, state = run(api)
        self.assertEqual([i["uid"][0] for i in items], ["1"])
        self.assertEqual(items[0]["expires"], datetime(2026, 11, 10, 22, 59, tzinfo=timezone.utc))
        self.assertEqual(sorted(state["posts"]), ["deal:1"])
        # ... und übersteht den Abruf über FeedSource (Altersregel: expires geht vor)
        self.assertTrue(is_current(normalize_item(items[0], now=FIXTURE_NOW), FIXTURE_NOW, 14))

    def test_page_beyond_total_pages_is_never_fetched(self):
        posts = [post(i, modified_gmt=f"2026-11-0{1 + i % 4}T10:{i % 60:02d}:00")
                 for i in range(1, 151)]
        api = FakeAPI(posts)
        items, _ = run(api)
        deal_pages = [q.get("page", "1") for k, q in api.calls if k == "deal"]
        self.assertEqual(deal_pages, ["1", "2"])
        self.assertEqual(len(items), 150)
        # sehr viele Seiten: höchstens MAX_PAGES
        api = FakeAPI([post(i, modified_gmt="2026-11-05T10:00:00") for i in range(1, 801)])
        run(api)
        self.assertEqual(len([k for k in api.kinds() if k == "deal"]), td.MAX_PAGES)

    def test_errors_raise_source_error_and_keep_state(self):
        state = {"cursor": {"deal": "2026-11-05T10:00:00"}, "full_at": "2026-11-05T11:00:00"}
        before = copy.deepcopy(state)
        cases = [(http.HttpError("HTTP 403", 403), "gesperrt (HTTP 403)"),
                 (http.HttpError("HTTP 429", 429), "gesperrt (HTTP 429)"),
                 (http.HttpError("Zeitüberschreitung", network=True), "nicht erreichbar"),
                 (b"<html>Cloudflare</html>", "keine gültige JSON"),
                 (b'{"code": "rest_invalid"}', "unerwartete Antwort")]
        for error, text in cases:
            for kind in ("deal", "ticker"):
                with self.subTest(error=error, kind=kind):
                    with self.assertRaises(SourceError) as cm:
                        run(FakeAPI(errors={kind: error}), state)
                    self.assertIn(text, str(cm.exception))
                    self.assertEqual(state, before)

    def test_term_lookup_failure_is_harmless(self):
        api = live_api()
        api.errors = {"origins": http.HttpError("HTTP 500", 500)}
        items, state = run(api)
        self.assertEqual(len(items), 57)
        self.assertEqual(state["terms"]["origins"], {})            # nichts gemerkt -> neu fragen
        by_title = {i["title"][:30]: i for i in items}
        self.assertEqual(by_title["Peking: Business Class von Fra"]["origin_codes"], ["FRA"])
        self.assertNotIn("origin_codes", by_title["Qatar Airways: Gabelflüge nach"])  # Titel
        api.errors = {}
        api.calls.clear()
        run(api, state, FIXTURE_NOW + timedelta(minutes=5))
        self.assertEqual(api.kinds(), ["deal", "ticker", "origins"])

    def test_broken_state_and_posts_never_crash(self):
        state = {"posts": "kaputt", "cursor": 5, "full_at": "gestern", "terms": []}
        broken = [{"id": "x"}, {"id": 1, "title": None}, {"id": True, "title": {"rendered": "A"}},
                  "kein dict", post(2, categories="1757", origins=None, destinations=[None, "a"],
                                    tags={}, meta=None, link=None, date_gmt=5, modified_gmt=None)]
        items, state = run(FakeAPI(broken), state)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual((it["link"], it["categories"]), ("", ["Allgemein"]))
        self.assertIsNone(it["published"])
        self.assertIsNotNone(normalize_item(it, "x"))
        json.dumps(state)
        # kaputter Bestand im Zustand (nicht vollständiger Lauf): unbrauchbares fällt weg
        good = dict(state["posts"]["deal:2"], origins=[2971])
        state.update(full_at="2026-11-05T11:00:00", terms={"origins": {"1": 7}, "destinations": 3},
                     posts={"deal:2": good, "deal:3": dict(good, key="deal:3", id=3, origins=7),
                            "deal:4": {"kind": "deal"}, "x": "y"})
        items, state = run(FakeAPI(), state)
        self.assertEqual([i["uid"] for i in items], ["2:"])
        self.assertEqual(items[0]["origin_codes"], ["ZRH"])

    def test_older_research_shapes(self):
        """Ohne ``*_gmt`` (Berliner Zeit) und ohne ``link`` - wie in den Recherche-Rohdaten."""
        posts = [td._slim(p, "deal") for p in fixture("td-kritiker.json")]
        posts += [td._slim(p, "deal") for p in fixture("sample-deal-flights.json")]
        items = [td.to_item(p, {}) for p in posts if p]
        self.assertEqual(len(items), 104)
        infos = {i["title"][:30]: info_of(i)[1] for i in items}
        flights = [t for t, i in infos.items() if i.flight]
        # Kreuzfahrten, Pauschalreisen, Meilen, Hotels, Bahn: keine Flüge
        self.assertFalse(infos["Costa Diadema: 26 Nächte Trans"].flight)
        self.assertFalse(infos["Flying Blue: 10.000 Bonusmeile"].flight)
        nord = infos["Nordamerika ab 903€ in der Ice"]
        self.assertEqual((nord.price, nord.cabin, nord.origin_codes), (903.0, "premium",
                                                                       {"FRA", "ZRH"}))
        first = next(i for i in items if i["title"].startswith("Nordamerika"))
        self.assertEqual(first["published"], datetime(2026, 11, 5, 10, 19, 57, tzinfo=timezone.utc))
        self.assertEqual(first["link"], "")
        self.assertGreaterEqual(len(flights), 20)


class FeedSourceTest(unittest.TestCase):
    """Als Loader-Preset in ``FeedSource`` (registriert wird in feed.py - hier nachgestellt)."""

    def setUp(self):
        preset = Preset("travel-dealz-rest", "Travel-Dealz (Schnittstelle)", (), "de", True,
                        TRAVEL_DEALZ_PROFILE, "https://travel-dealz.de/",
                        loader="traveldealz_rest:fetch_items", lane="fast")
        patcher = mock.patch.dict(PRESETS_BY_ID, {"travel-dealz-rest": preset})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        delta = timedelta(seconds=int((datetime.now(timezone.utc) - FIXTURE_NOW).total_seconds()))
        self.api = live_api(delta)

    def test_alert_offer_state_and_dedupe(self):
        src = FeedSource(presets=["travel-dealz-rest", "travel-dealz"], state_store=self.store,
                         max_age_days=14)
        peking_rss = (b"<rss><channel><item><title>Peking: Business Class von Frankfurt ab "
                      b"1.123\xe2\x82\xac</title><link>https://travel-dealz.de/deal/turkmenistan-"
                      b"airlines-business-peking?utm_source=rss</link><category>Business "
                      b"Class</category></item></channel></rss>")
        with mock.patch.object(td.http, "request", self.api), \
                mock.patch.object(src, "_fetch_one", side_effect=lambda u: src.parse(peking_rss)):
            items = src.load()
        peking = [i for i in items if "Peking: Business" in i["title"]]
        self.assertEqual(len(peking), 1)                        # REST + RSS: ein Artikel
        self.assertEqual(peking[0]["source"], "feed:travel-dealz-rest")
        st = src.stats["feed:travel-dealz-rest"]
        self.assertEqual((st["items"], st["current"], st["error"]), (57, 57, None))
        self.assertGreaterEqual(st["parsed"], 45)
        saved = self.store.source_state("travel-dealz-rest")
        self.assertEqual(set(saved), {"cursor", "full_at", "posts", "terms"})

        alert = Alert(id="de-cn-business", name="DE → China Business", origins=["FRA", "MUC"],
                      destinations=["BJS", "SHA"], cabin="business", max_price=1999)
        offers = src.fetch(alert)
        self.assertEqual(len(offers), 1)
        o = offers[0]
        self.assertEqual((o.source, o.price, o.cabin, o.roundtrip), ("feed:travel-dealz-rest",
                                                                   1123.0, "business", True))
        self.assertTrue(o.uid.startswith(f"{PEKING}:"))
        self.assertIsNotNone(o.expires)

    def test_blocked_api_keeps_rss_and_state(self):
        self.store.set_source_state("travel-dealz-rest", {"cursor": {"deal": "x"}})
        self.api.errors = {"deal": http.HttpError("HTTP 403", 403)}
        src = FeedSource(presets=["travel-dealz-rest", "travel-dealz"], state_store=self.store)
        rss = (b"<rss><channel><item><title>Tokio ab 599\xe2\x82\xac von Frankfurt</title>"
               b"<link>https://travel-dealz.de/deal/tokio/</link><category>Economy Class"
               b"</category></item></channel></rss>")
        with mock.patch.object(td.http, "request", self.api), \
                mock.patch.object(src, "_fetch_one", side_effect=lambda u: src.parse(rss)):
            with self.assertLogs("dealmonitor.sources.feed", "WARNING"):
                items = src.load()
        self.assertEqual({i["source"] for i in items}, {"feed:travel-dealz"})
        self.assertIn("gesperrt (HTTP 403)", src.stats["feed:travel-dealz-rest"]["error"])
        self.assertEqual(src.unreliable_sources(), {"feed:travel-dealz-rest"})
        self.assertEqual(self.store.source_state("travel-dealz-rest"), {"cursor": {"deal": "x"}})


if __name__ == "__main__":
    unittest.main()
