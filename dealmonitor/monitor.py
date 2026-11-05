"""Ein Monitor-Lauf: Quellen abfragen -> Prinzipien prüfen -> speichern -> melden.

Zusätzlich (seit "Mehr Quellen"):

* **Google-Verifikation**: Deals aus *gecachten* Quellen (Travelpayouts, Amadeus-Monatsscan)
  werden vor dem Speichern/Melden mit genau diesen Daten bei Google Flights gegengeprüft
  (``verifier``, die Google-Quelle des Laufs). Günstigster *passender* Google-Flug (Umstiege,
  Dauer, Airlines des Alarms) ≤ Preis × 1,25 und unter der Alarm-Grenze -> bestätigt, der Deal
  trägt dann Google-Preis und -Angaben; sonst verworfen (``RunResult.rejected``) und der
  nächstgünstige Termin desselben Monats wird geprüft; Google nicht erreichbar -> Deal trotzdem
  gemeldet, mit Hinweis "nicht gegengeprüft".
* Erst auswählen (Quellen-Abgleich, ``max_deals_per_alert``), dann speichern: Aussortierte
  Deals werden ohne "gemeldet"-Vermerk gespeichert und später gemeldet.
* **Bewertung** (``principles``): Bezugspreis auch aus Googles Preisniveau, Fehlerpreis-Verdacht
  (``_market``: eigene Preise der letzten 30 Tage, Economy-Median), Beinahe-Deals landen in den
  Bestpreisen (``near``).
* **Erstmeldung über alle Quellen** (``sightings``): Hat eine andere Deal-Seite denselben Artikel-
  Deal schon gemeldet, wird er nicht noch einmal gemeldet, sondern ergänzt die Erstmeldung.
* **Radar**: nach den Alarmen werden alle Flug-Artikel der Deal-Seiten auf Hammer-Deals geprüft
  (``dealmonitor.radar``), Artikel, die schon einem Alarm zugeordnet wurden, nicht noch einmal.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import median

from . import principles, radar, sightings
from .config import Config
from .models import Alert, Deal, Offer
from .principles import euro
from .sources import SourceError
from .sources.feed import SAFE_LINK_RE, analyze, normalize_link
from .store import Store

log = logging.getLogger(__name__)

#: Quellen mit gecachten Preisen - ihre Deals werden bei Google Flights gegengeprüft
VERIFY_SOURCES = frozenset({"travelpayouts", "amadeus-dates"})
#: Quellen mit echten Live-Preisen - gelten ohne Gegenprüfung als bestätigt
LIVE_SOURCES = frozenset({"google_flights", "amadeus-offers"})
#: Google darf höchstens so viel teurer sein, damit der gecachte Preis als bestätigt gilt
VERIFY_TOLERANCE = 1.25
VERIFIED_PREFIX = "Google Flights bestätigt"
UNVERIFIED_PREFIX = "nicht gegengeprüft"


@dataclass
class RunResult:
    offers: int = 0
    deals: int = 0
    notified: int = 0
    expired: int = 0
    errors: list[str] = field(default_factory=list)
    rejected: int = 0      # bei Google Flights deutlich teurer -> verworfen
    radar: int = 0         # Radar-Treffer (Hammer-Deals der Deal-Seiten)
    #: normalisierte Links der Feed-Artikel, die in diesem Lauf Deal eines Alarms wurden -
    #: das Radar meldet sie nicht noch einmal (passende Stichwörter allein reichen nicht)
    article_links: set[str] = field(default_factory=set)

    def add(self, other: "RunResult") -> None:
        self.offers += other.offers
        self.deals += other.deals
        self.notified += other.notified
        self.expired += other.expired
        self.errors += other.errors
        self.rejected += other.rejected
        self.radar += other.radar
        self.article_links |= other.article_links


def is_confirmed(deal: Deal) -> bool:
    """Live-Preis oder von Google bestätigt."""
    return deal.offer.source in LIVE_SOURCES or any(
        r.startswith(VERIFIED_PREFIX) for r in deal.reasons)


def _slot(o: Offer, with_source: bool = True) -> tuple:
    month = o.departure.strftime("%Y-%m") if o.departure else None
    return ((o.source,) if with_source else ()) + (o.origin, o.destination, month, o.title)


def best_per_slot(deals: list[Deal]) -> list[Deal]:
    """Pro Quelle, Strecke und Abflugmonat nur den günstigsten Deal behalten.

    Sonst würden 30 fast identische Termine derselben Strecke gemeldet.
    """
    best: dict[tuple, Deal] = {}
    for d in deals:
        key = _slot(d.offer)
        if key not in best or d.offer.price < best[key].offer.price:
            best[key] = d
    return sorted(best.values(), key=lambda d: d.offer.price)


def best_across_sources(deals: list[Deal]) -> list[Deal]:
    """Dieselbe Strecke im selben Monat aus mehreren Quellen: nur den günstigsten *bestätigten*
    melden (Live-Preis oder Google-bestätigt), sonst den günstigsten. Artikel (mit Titel)
    bleiben einzeln."""
    best: dict[tuple, Deal] = {}
    for d in deals:
        key = _slot(d.offer, with_source=False)
        cur = best.get(key)
        if cur is None:
            best[key] = d
            continue
        rank_new = (0 if is_confirmed(d) else 1, d.offer.price)
        rank_cur = (0 if is_confirmed(cur) else 1, cur.offer.price)
        if rank_new < rank_cur:
            best[key] = d
    return list(best.values())


#: so viele Termine je Strecke/Monat werden höchstens gegengeprüft, wenn die günstigeren bei
#: Google durchfallen (veralteter Cache-Preis) - der nächstgünstige echte Deal soll nicht fehlen
MAX_VERIFY_ATTEMPTS_PER_SLOT = 3


def _criteria(alert: Alert) -> dict:
    return {"max_stops": alert.max_stops,
            "max_minutes": alert.max_duration_hours * 60 if alert.max_duration_hours else None,
            "exclude_airlines": list(alert.exclude_airlines)}


def verify_deal(deal: Deal, verifier, today: date | None = None) -> str:
    """Einen Deal mit genau seinen Daten bei Google Flights gegenprüfen.

    Gesucht wird der günstigste Google-Flug, der zu den Regeln des Alarms passt (Umstiege,
    Dauer, ausgeschlossene Airlines). Rückgabe:

    * ``"confirmed"``: Google-Preis ≤ Deal-Preis × 1,25 *und* weiter unter der Grenze des
      Alarms (bzw. genug Rabatt) - der Deal trägt danach den Google-Preis samt Airline,
      Umstiegen und Dauer dieses Fluges (Überschrift, Rabatt und Bewertung neu berechnet),
    * ``"rejected"``: Google deutlich teurer, über der Grenze oder kein passender Flug,
    * ``"unverified"``: Google nicht erreichbar/blockiert/Budget - Hinweis in den Gründen,
      Deal bleibt unverändert.
    """
    o = deal.offer
    if not (o.origin and o.destination and o.departure):
        return "unverified"
    try:
        found = verifier.verify(o.origin, o.destination, o.departure, o.return_date,
                                o.cabin, o.roundtrip, **_criteria(deal.alert))
        reason = None if found else (getattr(verifier, "last_error", None)
                                     or "Google Flights nicht erreichbar")
    except Exception as e:  # noqa: BLE001 - Gegenprüfung darf den Lauf nie stoppen
        log.warning("Google-Gegenprüfung %s-%s %s fehlgeschlagen: %s", o.origin, o.destination,
                    o.departure, e)
        found, reason = None, f"Google Flights: {e}"
    if not found:
        deal.reasons.append(f"{UNVERIFIED_PREFIX} ({reason})")
        return "unverified"
    what = f"{o.source} {o.origin}-{o.destination} {o.departure} für {euro(o.price)}"
    if found.get("price") is None:
        log.info("Deal verworfen: %s - Google Flights: %s", what,
                 found.get("reason") or "kein passender Flug")
        return "rejected"
    google_price = float(found["price"])
    if google_price > o.price * VERIFY_TOLERANCE:
        log.info("Deal verworfen: %s - Google Flights zeigt %s", what, euro(google_price))
        return "rejected"
    # Ab hier gilt der Google-Flug: Preis und Angaben aus derselben Ergebniszeile
    o.ident = o.ident or o.fingerprint          # bleibt derselbe Deal (Fingerprint)
    old = (o.price, o.airline, o.stops, o.duration_out_min, o.duration_back_min)
    o.airline = found.get("airline") or found.get("airline_code") or o.airline
    o.stops = found.get("stops")
    o.duration_out_min = found.get("duration_min")
    o.duration_back_min = None                  # Google nennt nur die Hinflug-Dauer
    old_insight = o.insight
    o.insight = found.get("insight") or o.insight   # Preisniveau der Google-Strecke
    if not principles.reprice(deal, google_price, today):
        o.price, o.airline, o.stops, o.duration_out_min, o.duration_back_min = old
        o.insight = old_insight
        log.info("Deal verworfen: %s - bei Google Flights %s, damit kein Deal mehr", what,
                 euro(google_price))
        return "rejected"
    details = [x for x in (o.airline, None if o.stops is None else
                           ("direkt" if o.stops == 0 else f"{o.stops} Umstieg"
                            + ("e" if o.stops > 1 else ""))) if x]
    deal.reasons.append(f"{VERIFIED_PREFIX}: {euro(google_price)}"
                        + (f" ({', '.join(details)})" if details else ""))
    return "confirmed"


def _slots(deals: list[Deal]) -> list[list[Deal]]:
    """Deals je Quelle/Strecke/Abflugmonat, jeweils nach Preis sortiert (günstigster zuerst)."""
    groups: dict[tuple, list[Deal]] = defaultdict(list)
    for d in deals:
        groups[_slot(d.offer)].append(d)
    return [sorted(g, key=lambda d: d.offer.price) for g in groups.values()]


def _verified_best(deals: list[Deal], verifier, today: date | None) -> tuple[list[Deal], set[str]]:
    """Pro Slot den günstigsten Deal, der die Google-Gegenprüfung besteht (oder nicht prüfbar
    ist). Fällt der günstigste durch, wird der nächstgünstige Termin desselben Monats geprüft
    (höchstens ``MAX_VERIFY_ATTEMPTS_PER_SLOT``). Rückgabe: (behaltene, verworfene Fingerprints)."""
    kept: list[Deal] = []
    rejected: set[str] = set()
    for group in _slots(deals):
        for d in group[:MAX_VERIFY_ATTEMPTS_PER_SLOT]:
            fp = d.fingerprint
            if verify_deal(d, verifier, today) == "rejected":
                rejected.add(fp)
                continue
            kept.append(d)
            break
    return sorted(kept, key=lambda d: d.offer.price), rejected


ARTICLE_ROUTE = "artikel"
#: Zusatz am Streckenschlüssel für den günstigsten *bei Google bestätigten* Cache-Preis
CONFIRMED_SUFFIX = "|bestätigt"
#: Felder eines Alarms, die die Bestpreise bestimmen (Preisgrenze/Normalpreis gehören nicht dazu)
CHEAPEST_RULES = ("origins", "destinations", "trip", "cabin", "max_stops", "max_duration_hours",
                  "min_nights", "max_nights", "exclude_airlines", "feed_keywords")


def _entry(o: Offer, confirmed: bool, near: list[str] | None = None) -> dict:
    return {
        "price": o.price,
        "origin": o.origin or None, "destination": o.destination or None,
        "departure": o.departure.isoformat() if o.departure else None,
        "return_date": o.return_date.isoformat() if o.return_date else None,
        "nights": o.nights, "airline": o.airline, "stops": o.stops,
        "duration_min": o.duration_out_min, "cabin": o.cabin, "roundtrip": o.roundtrip,
        "link": o.link if o.link and SAFE_LINK_RE.match(o.link) else None,
        "title": o.title,
        "published": o.found_at.isoformat(timespec="minutes") if o.title and o.found_at else None,
        "kind": "article" if o.title else ("live" if o.source in LIVE_SOURCES else "cache"),
        "confirmed": confirmed,
        **({"near": near} if near else {}),
        **o.extras(),
    }


def cheapest_entries(alert: Alert, offers: list[Offer], deals: list[Deal],
                     rejected_fps: set[str], today: date | None = None, *,
                     near: dict[int, list[str]] | None = None) -> dict[str, dict[str, dict]]:
    """Bestpreise: je Offer-Quelle und Strecke das günstigste Angebot, das zu den Regeln des
    Alarms passt (Kabine, Reiseart, Umstiege, Dauer, Nächte, Airlines) - *ohne* Preisgrenze.
    Artikel der Deal-Seiten zählen je Seite als eine Strecke ``artikel``.

    Cache-Preise (Travelpayouts, Amadeus-Monatsscan) werden ehrlich behandelt: Hat Google in
    einem Abflugmonat einer Strecke gegengeprüft, zählen dort nur die bestätigten Preise (mit
    dem Google-Preis) - ungeprüfte Nachbartermine desselben Monats sind dann nicht glaubwürdig.
    Zusätzlich wird je Strecke der günstigste bestätigte Preis gesondert gespeichert
    (``<strecke>|bestätigt``), damit er neben einem billigeren ungeprüften sichtbar bleibt.

    ``near``: Beinahe-Deals (``id(offer)`` -> Gründe aus ``principles.near_deal``) - ihr Eintrag
    trägt dann ``near`` (für die Liste "Beinahe-Deals" je Alarm).

    Rückgabe: ``{offer.source: {strecke: angaben}}``.
    """
    near = near or {}
    confirmed = {id(d.offer) for d in deals if is_confirmed(d)}
    rejected = {id(o) for o in offers if f"{alert.id}:{o.fingerprint}" in rejected_fps}
    checked = {_slot(o) for o in offers if id(o) in confirmed or id(o) in rejected}
    out: dict[str, dict[str, dict]] = defaultdict(dict)

    def put(source: str, route: str, o: Offer, ok: bool) -> None:
        cur = out[source].get(route)
        if cur is None or o.price < cur["price"]:
            out[source][route] = _entry(o, ok, near.get(id(o)))

    for o in offers:
        if id(o) in rejected or principles.quality_problems(alert, o, today):
            continue
        if o.title:
            route = ARTICLE_ROUTE
        elif o.origin and o.destination:
            route = f"{o.origin}-{o.destination}"
        else:
            continue
        ok = o.source in LIVE_SOURCES or id(o) in confirmed
        if o.source in VERIFY_SOURCES and not ok and _slot(o) in checked:
            continue        # Google hat diesen Monat geprüft und den Preis nicht bestätigt
        put(o.source, route, o, ok)
        if ok and o.source in VERIFY_SOURCES and not o.title:
            put(o.source, route + CONFIRMED_SUFFIX, o, ok)
    for routes in out.values():     # bestätigt = günstigster ohnehin -> nicht doppelt
        for key in [k for k in routes if k.endswith(CONFIRMED_SUFFIX)]:
            base = routes.get(key[: -len(CONFIRMED_SUFFIX)])
            if base is not None and base["confirmed"]:
                del routes[key]
    return out


def _rules_changed(store: Store, alert: Alert) -> bool:
    """Wurde der Alarm während des Laufs gelöscht oder in seinen Regeln geändert? Dann keine
    Bestpreise nach den alten Regeln speichern (Alarme nur aus der Konfiguration: egal)."""
    if not store.has_alert(alert.id):
        return bool(store.alert_rows())
    raw = store.get_alert(alert.id) or {}
    try:
        now = Alert(**raw)
    except (TypeError, ValueError):
        return True
    return any(getattr(now, f) != getattr(alert, f) for f in CHEAPEST_RULES)


def _save_cheapest(store: Store, alert: Alert, source, entries: dict[str, dict[str, dict]],
                   incomplete: dict | None) -> None:
    """Bestpreise speichern: Hat die Quelle vollständig geantwortet, ersetzen sie die alten.
    Fehlten nur einzelne Strecken (``incomplete["routes"]``), bleiben deren alte Einträge; die
    übrigen Strecken gelten als vollständig abgefragt. Fehlten ganze Teilquellen
    (``incomplete["sources"]``, Feeds), bleiben deren Einträge unverändert."""
    if _rules_changed(store, alert):
        log.info("[%s] Alarm während des Laufs geändert - Bestpreise nicht gespeichert", alert.id)
        return
    partial = incomplete.get("sources") if incomplete else None
    routes = incomplete.get("routes") if incomplete else None
    names = set(getattr(source, "offer_sources", None) or [source.name]) | set(entries)
    for name in sorted(names):
        new = entries.get(name, {})
        if incomplete is None or (partial is not None and name not in partial):
            store.save_cheapest(alert.id, name, new, replace=True)
        elif routes and partial is None:
            store.save_cheapest(alert.id, name, new, replace=True,
                                keep_routes={f"{o}-{d}" for o, d in routes})
        elif new:
            store.save_cheapest(alert.id, name, new, replace=False)


def run_alert(alert: Alert, sources: list, store: Store, cfg: Config,
              today: date | None = None, *, verifier=None) -> tuple[list[tuple[Deal, str]], RunResult]:
    s = cfg.settings
    result = RunResult()
    candidates: list[Deal] = []      # günstigster Deal je Quelle/Strecke/Monat

    for source in sources:
        try:
            offers: list[Offer] = source.fetch(alert)
        except SourceError as e:
            msg = f"[{alert.id}] {source.name}: {e}"
            log.warning(msg)
            result.errors.append(msg)
            continue  # Ohne Daten nichts als abgelaufen markieren!
        except Exception as e:  # noqa: BLE001 - eine kaputte Quelle darf den Lauf nicht stoppen
            msg = f"[{alert.id}] {source.name}: unerwarteter Fehler: {e}"
            log.exception(msg)
            result.errors.append(msg)
            continue

        # Teilweise fehlgeschlagen? Dann nur Deals der erfolgreich abgefragten Strecken ablaufen
        # lassen - sonst würden sie beim nächsten Lauf erneut als "WIEDER DA" gemeldet.
        incomplete = getattr(source, "incomplete", None)
        if incomplete:
            msg = f"[{alert.id}] {source.name}: {incomplete.get('message') or 'unvollständig'}"
            log.warning(msg)
            result.errors.append(msg)

        result.offers += len(offers)
        by_route: dict[str, list[Offer]] = defaultdict(list)
        for o in offers:
            by_route[o.route_key].append(o)

        deals: list[Deal] = []
        near: dict[int, list[str]] = {}     # Beinahe-Deals: id(offer) -> Gründe
        for route_key, route_offers in by_route.items():
            # Referenz VOR dem Speichern der neuen Preise bestimmen
            history = store.history(route_key, s.history_days) if source.records_history else []
            market = _market(store, route_key, s) if source.records_history else None
            for o in route_offers:
                d = principles.evaluate(alert, o, history, min_samples=s.min_history_samples,
                                        today=today, market=market)
                if d:
                    deals.append(d)
                elif (close := principles.near_deal(alert, o, history, today=today,
                                                    min_samples=s.min_history_samples)):
                    near[id(o)] = close
            if source.records_history:
                store.add_prices(_seen_today(route_offers))

        result.article_links |= {normalize_link(d.offer.link) for d in deals
                                 if d.offer.title and d.offer.link}
        rejected_fps: set[str] = set()
        if verifier is not None and source.name in VERIFY_SOURCES and verifier is not source:
            best, rejected_fps = _verified_best(deals, verifier, today)
            result.rejected += len(rejected_fps)
        else:
            best = best_per_slot(deals)
        # Alle Deals, die weiter die Kriterien erfüllen, gelten als "gesehen" - auch wenn
        # sie in diesem Lauf nicht die günstigsten ihrer Strecke/ihres Monats sind; bei
        # Google verworfene nicht (ein früher gespeicherter läuft damit ab)
        seen = {d.fingerprint for d in deals} - rejected_fps
        best_fps = {d.fingerprint for d in best}
        for d in deals:
            if d.fingerprint not in best_fps and d.fingerprint not in rejected_fps:
                store.refresh_deal(d)
        try:
            _save_cheapest(store, alert, source,
                           cheapest_entries(alert, offers, deals, rejected_fps, today, near=near),
                           incomplete)
        except Exception:  # noqa: BLE001 - Anzeige darf den Lauf nie stoppen
            log.exception("[%s] %s: Bestpreise nicht gespeichert", alert.id, source.name)
        result.deals += len(best)
        candidates += best
        # Eine Quelle kann mehrere Offer.source-Werte liefern (Feeds: "feed:<preset>")
        partial = incomplete.get("sources") if incomplete else None
        for name in getattr(source, "offer_sources", None) or [source.name]:
            if incomplete is None or (partial is not None and name not in partial):
                result.expired += store.expire_missing(alert.id, name, seen)
            elif incomplete.get("routes"):
                result.expired += store.expire_missing(alert.id, name, seen,
                                                       skip_routes=set(incomplete["routes"]))

    # Erst auswählen, dann speichern: was hier aussortiert wird (gleiche Strecke/Monat bei
    # einer anderen Quelle günstiger bestätigt, über max_deals_per_alert), wird ohne
    # "gemeldet"-Vermerk gespeichert - sonst käme es nie in Inbox/Webhook
    picked = best_across_sources(candidates) if len(sources) > 1 else list(candidates)
    picked_ids = {id(d) for d in picked}
    # Artikel, die eine andere Deal-Seite schon gemeldet hat, ergänzen nur die Erstmeldung
    checked, merged = first_reports(
        store, [(d, store.deal_status(d, s.renotify_drop)) for d in picked], sources)
    pending: list[tuple[Deal, str]] = []
    for d, status in checked:
        if status == "known":
            store.upsert_deal(d, s.renotify_drop)       # Preis/last_seen aktualisieren
        elif status == "quiet":
            store.upsert_deal(d, s.renotify_drop, notify=False)
        else:
            pending.append((d, status))
    pending.sort(key=lambda x: x[0].offer.price)
    to_notify: list[tuple[Deal, str]] = []
    for d, _ in pending[: s.max_deals_per_alert]:
        to_notify.append((d, store.upsert_deal(d, s.renotify_drop)))
    for d, _ in pending[s.max_deals_per_alert:]:
        store.upsert_deal(d, s.renotify_drop, notify=False)
    for d in candidates:
        if id(d) not in picked_ids:
            store.upsert_deal(d, s.renotify_drop, notify=False)
    _refresh_sources(store, merged, [d for d, _ in checked])
    return to_notify, result


def _seen_today(offers: list[Offer]) -> list[Offer]:
    """Für den Preisverlauf nur heute gesehene Preise: Angebote aus dem Gedächtnis einer Quelle
    (Google-Rotation: ``found_at`` = Zeitpunkt der Abfrage) stehen dort schon - sonst zählte
    derselbe Preis jeden Tag neu und verschöbe den Median."""
    today = datetime.now().date()
    return [o for o in offers if not o.found_at or o.found_at.date() >= today]


def _market(store: Store, route_key: str, settings) -> principles.Market:
    """Eigene Preise der Strecke für den Fehlerpreis-Verdacht: Bestpreis je Tag der letzten
    30 Tage (Preissturz) und - bei Business/First - der Economy-Median derselben Strecke."""
    try:
        recent = store.daily_best(route_key, principles.DROP_DAYS)
        market = principles.Market(
            recent=recent if len(recent) >= settings.min_history_samples else [])
        base, _, cabin = route_key.rpartition("-")
        if cabin in ("business", "first"):
            eco = store.history(f"{base}-economy", settings.history_days)
            if len(eco) >= settings.min_history_samples:
                market.economy = float(median(eco))
        return market
    except Exception:  # noqa: BLE001 - Zusatzbewertung darf den Lauf nie stoppen
        log.exception("Preisverlauf %s nicht lesbar", route_key)
        return principles.Market()


def _article_codes(sources: list):
    """Abflug-/Ziel-Codes der Artikel, wie die Quelle sie nennt (Loader: Secret Flying,
    ErrorFareAlerts, Travel-Dealz) - je normalisiertem Link. Erst bei Bedarf geladen."""
    cache: dict[str, tuple[set, set]] | None = None

    def lookup(link: str | None) -> tuple[set, set]:
        nonlocal cache
        if cache is None:
            cache = {}
            feed = _feed_source(sources)
            try:
                items = feed.all_items() if feed is not None else []
            except Exception:  # noqa: BLE001 - ohne Codes zählt der Titel
                items = []
            for it in items:
                try:
                    info = analyze(it)
                except Exception:  # noqa: BLE001
                    continue
                if info.origin_codes or info.dest_codes:
                    cache[info.link] = (info.origin_codes, info.dest_codes)
        return cache.get(normalize_link(link or ""), (set(), set()))
    return lookup


def first_reports(store: Store, pending: list[tuple[Deal, str]], sources: list
                  ) -> tuple[list[tuple[Deal, str]], dict[str, list[Deal]]]:
    """Neue Artikel-Deals gegen die Meldungen anderer Quellen prüfen (``sightings``): Hat eine
    andere Deal-Seite denselben Deal schon gemeldet, wird er nicht erneut gemeldet, sondern
    der Erstmeldung zugeordnet. Früher veröffentlichte Artikel zuerst, damit im selben Lauf
    die älteste Meldung gewinnt. Rückgabe: (weiter zu meldende, {Erstmeldung: [Deals]}).
    Status "quiet": Erstmeldung abgelaufen - speichern, aber nicht melden."""
    codes = _article_codes(sources)
    kept: list[tuple[Deal, str]] = []
    merged: dict[str, list[Deal]] = defaultdict(list)
    fresh: set[str] = set()       # Erstmeldungen dieses Laufs (noch nicht gespeichert)
    for d, status in sorted(pending, key=lambda x: x[0].offer.found_at or datetime.min):
        if not d.offer.title:
            kept.append((d, status))
            continue
        try:
            info = sightings.parts(d.offer, *codes(d.offer.link))
            # schon gespeicherte Deals (auch aus der Zeit vor dem Abgleich) zählen als Erstmeldung
            first = sightings.first_report(store, d, info, fresh) if status == "new" else None
            sightings.record(store, d.offer, info, first or d.fingerprint)
        except Exception:  # noqa: BLE001 - im Zweifel lieber doppelt melden als gar nicht
            log.exception("Abgleich über Quellen fehlgeschlagen: %s", d.headline)
            first = None
        if first is None:
            fresh.add(d.fingerprint)
            kept.append((d, status))
        elif first not in fresh and not store.deal_active(first):
            # Erstmeldung schon abgelaufen, hier läuft der Deal noch: still speichern (er war
            # ja gemeldet), damit er aktiv bleibt und mit dieser Quelle abläuft
            kept.append((d, "quiet"))
        else:
            log.info("Schon gemeldet (andere Quelle): %s", d.headline)
            merged[first].append(d)
    return kept, merged


def _record_twins(store: Store, twins: list[tuple[Offer, Deal]], merged: dict[str, list[Deal]],
                  sources: list) -> None:
    """Radar: derselbe Deal bei zwei Seiten im selben Lauf ("Auch bei …") - beide Meldungen
    der Erstmeldung zuordnen, damit Quellenliste und Vorsprung stimmen."""
    group = {d.fingerprint: fp for fp, ds in merged.items() for d in ds}
    codes = _article_codes(sources)
    for twin, primary in twins:
        fp = group.get(primary.fingerprint, primary.fingerprint)
        try:
            if abs(twin.found_at - primary.offer.found_at) > sightings.WINDOW:
                continue            # alter Artikel, neu aufgelegt: kein Vorsprung

            for o in (primary.offer, twin):
                sightings.record(store, o, sightings.parts(o, *codes(o.link)), fp)
        except Exception:  # noqa: BLE001
            log.exception("Quellenliste von %s nicht ergänzt", primary.headline)
            continue
        merged.setdefault(fp, [])


def _refresh_sources(store: Store, merged: dict[str, list[Deal]], deals: list[Deal]) -> None:
    """Quellenliste und Vorsprung der Erstmeldungen aktualisieren (gespeichert und - falls
    im selben Lauf gemeldet - in der Meldung)."""
    by_fp = {d.fingerprint: d for d in deals}
    for fp in merged:
        try:
            sightings.refresh(store, fp, by_fp.get(fp))
        except Exception:  # noqa: BLE001
            log.exception("Quellenliste von %s nicht aktualisiert", fp)


def _feed_source(sources: list):
    """Die Deal-Seiten-Quelle des Laufs (liefert ``all_items`` für das Radar)."""
    return next((s for s in sources if hasattr(s, "all_items")), None)


def run_radar(sources: list, store: Store, cfg: Config, settings: radar.RadarSettings,
              exclude_links: set[str] | None = None) -> tuple[list[tuple[Deal, str]], RunResult]:
    """Alle Flug-Artikel der Deal-Seiten auf Hammer-Deals prüfen (unabhängig von den Alarmen).

    ``exclude_links``: Artikel, die in diesem Lauf schon Deal eines Alarms wurden (``None`` =
    alle Artikel, deren Stichwörter zu einem Alarm passten - ``FeedSource.matched_links``).
    """
    s = cfg.settings
    result = RunResult()
    feed = _feed_source(sources)
    if feed is None or not settings.enabled:
        return [], result
    twins: list[tuple[Offer, Deal]] = []
    try:
        deals = radar.scan(feed, settings, exclude_links=exclude_links, twins=twins)
    except SourceError as e:
        msg = f"[{radar.RADAR_ALERT_ID}] {feed.name}: {e}"
        log.warning(msg)
        result.errors.append(msg)
        return [], result
    except Exception as e:  # noqa: BLE001
        msg = f"[{radar.RADAR_ALERT_ID}] {feed.name}: unerwarteter Fehler: {e}"
        log.exception(msg)
        result.errors.append(msg)
        return [], result
    result.radar = len(deals)
    # Schon von einer anderen Deal-Seite gemeldet (früherer Lauf): nur der Erstmeldung zuordnen
    kept, merged = first_reports(
        store, [(d, store.deal_status(d, s.renotify_drop)) for d in deals], sources)
    to_notify: list[tuple[Deal, str]] = []
    for d, quiet in kept:
        if quiet == "quiet":
            store.upsert_deal(d, s.renotify_drop, notify=False)
            continue
        status = store.upsert_deal(d, s.renotify_drop)
        if status != "known":
            to_notify.append((d, status))
    _record_twins(store, twins, merged, sources)
    _refresh_sources(store, merged, [d for d, _ in kept])
    seen = {d.fingerprint for d, _ in kept}
    partial = getattr(feed, "load_errors", None)
    if partial:
        result.errors.append(f"[{radar.RADAR_ALERT_ID}] {feed.name}: {partial}")
    # Deals von Feeds/Suchen, die gerade nicht lesbar waren, nicht ablaufen lassen
    unreliable = feed.unreliable_sources() if hasattr(feed, "unreliable_sources") else (
        None if partial else set())
    if unreliable is not None:
        for name in getattr(feed, "offer_sources", None) or [feed.name]:
            if name not in unreliable:
                result.expired += store.expire_missing(radar.RADAR_ALERT_ID, name, seen)
    # Bewusst ohne max_deals_per_alert: jeder Radar-Treffer wird genau einmal gespeichert -
    # was jetzt nicht gemeldet wird, würde nie gemeldet (keinen Hammer-Deal verpassen)
    return to_notify, result


def run(cfg: Config, sources: list, notifiers: list, store: Store,
        only: list[str] | None = None, today: date | None = None, *,
        radar_settings: radar.RadarSettings | None = None, verifier=None) -> RunResult:
    total = RunResult()
    # Deals mit Abflug in der Vergangenheit ablaufen lassen - auch bei pausierten Alarmen
    # und Quellen, die gerade nicht funktionieren
    total.expired += store.expire_departed(today or date.today())
    sent_articles: set[str] = set()   # ein Artikel passt evtl. auf mehrere Alarme

    def notify(name: str, items: list[tuple[Deal, str]]) -> None:
        items = [(d, st) for d, st in items
                 if not (d.offer.title and d.offer.link in sent_articles)]
        sent_articles.update(d.offer.link for d, _ in items if d.offer.title and d.offer.link)
        if not items:
            return
        for n in notifiers:
            try:
                n.send(name, items)
            except Exception as e:  # ein kaputter Kanal darf den Lauf nicht stoppen
                msg = f"Benachrichtigung über {n.name} fehlgeschlagen: {e}"
                log.error(msg)
                total.errors.append(msg)
        total.notified += len(items)

    # Artikel-Deals, die seit feed_max_age_days in keinem Lauf mehr auftauchten (abgewählte
    # Deal-Seite, Radar aus, Quelle umbenannt), ablaufen lassen - sie haben kein Abflugdatum
    total.expired += store.expire_stale_articles(cfg.settings.feed_max_age_days)
    alerts = [a for a in cfg.alerts if a.enabled and not (only and a.id not in only)]
    for source in sources:          # z. B. Google: Abfrage-Budget fair auf die Alarme verteilen
        if hasattr(source, "begin_run"):
            try:
                source.begin_run(alerts)
            except Exception:  # noqa: BLE001 - Planung darf den Lauf nicht stoppen
                log.exception("%s: Budget-Planung fehlgeschlagen", getattr(source, "name", "?"))

    for alert in alerts:
        log.info("Prüfe Alarm %s (%d Strecken)", alert.id, len(alert.pairs))
        items, r = run_alert(alert, sources, store, cfg, today=today, verifier=verifier)
        total.add(r)
        notify(alert.name, items)

    # Radar: nur bei vollen Läufen (nicht bei "nur diesen Alarm prüfen")
    if radar_settings is not None and radar_settings.enabled and not only:
        items, r = run_radar(sources, store, cfg, radar_settings, total.article_links)
        total.add(r)
        notify(radar.RADAR_ALERT_NAME, items)
    return total
