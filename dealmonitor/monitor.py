"""Ein Monitor-Lauf: Quellen abfragen -> Prinzipien prüfen -> speichern -> melden.

Zusätzlich (seit "Mehr Quellen"):

* **Google-Verifikation**: Deals aus *gecachten* Quellen (Travelpayouts, Amadeus-Monatsscan)
  werden vor dem Speichern/Melden mit genau diesen Daten bei Google Flights gegengeprüft
  (``verifier``, die Google-Quelle des Laufs). Google ≤ Preis × 1,25 -> bestätigt (günstigerer
  Google-Preis wird übernommen); Google deutlich teurer -> Deal verworfen (``RunResult.rejected``);
  Google nicht erreichbar -> Deal trotzdem gemeldet, mit Hinweis "nicht gegengeprüft".
* **Radar**: nach den Alarmen werden alle Flug-Artikel der Deal-Seiten auf Hammer-Deals geprüft
  (``dealmonitor.radar``), Artikel, die schon einem Alarm zugeordnet wurden, nicht noch einmal.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from . import principles, radar
from .config import Config
from .models import Alert, Deal, Offer
from .principles import euro
from .sources import SourceError
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

    def add(self, other: "RunResult") -> None:
        self.offers += other.offers
        self.deals += other.deals
        self.notified += other.notified
        self.expired += other.expired
        self.errors += other.errors
        self.rejected += other.rejected
        self.radar += other.radar


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


def best_across_sources(items: list[tuple[Deal, str]]) -> list[tuple[Deal, str]]:
    """Dieselbe Strecke im selben Monat aus mehreren Quellen: nur den günstigsten *bestätigten*
    melden (Live-Preis oder Google-bestätigt), sonst den günstigsten. Artikel (mit Titel)
    bleiben einzeln."""
    best: dict[tuple, tuple[Deal, str]] = {}
    for d, status in items:
        key = _slot(d.offer, with_source=False)
        cur = best.get(key)
        if cur is None:
            best[key] = (d, status)
            continue
        rank_new = (0 if is_confirmed(d) else 1, d.offer.price)
        rank_cur = (0 if is_confirmed(cur[0]) else 1, cur[0].offer.price)
        if rank_new < rank_cur:
            best[key] = (d, status)
    return list(best.values())


def verify_deal(deal: Deal, verifier) -> str:
    """Einen Deal mit genau seinen Daten bei Google Flights gegenprüfen.

    Rückgabe ``"confirmed"`` (Grund + ggf. günstigerer Google-Preis übernommen),
    ``"rejected"`` (Google deutlich teurer) oder ``"unverified"`` (Google nicht
    erreichbar/blockiert/Budget - Hinweis in den Gründen, Deal bleibt).
    """
    o = deal.offer
    if not (o.origin and o.destination and o.departure):
        return "unverified"
    try:
        found = verifier.verify(o.origin, o.destination, o.departure, o.return_date,
                                o.cabin, o.roundtrip)
        reason = None if found else (getattr(verifier, "last_error", None)
                                     or "Google Flights nicht erreichbar")
    except Exception as e:  # noqa: BLE001 - Gegenprüfung darf den Lauf nie stoppen
        log.warning("Google-Gegenprüfung %s-%s %s fehlgeschlagen: %s", o.origin, o.destination,
                    o.departure, e)
        found, reason = None, f"Google Flights: {e}"
    if not found:
        deal.reasons.append(f"{UNVERIFIED_PREFIX} ({reason})")
        return "unverified"
    google_price = float(found["price"])
    if google_price > o.price * VERIFY_TOLERANCE:
        log.info("Deal verworfen: %s %s-%s %s für %s - Google Flights zeigt %s",
                 o.source, o.origin, o.destination, o.departure, euro(o.price),
                 euro(google_price))
        return "rejected"
    deal.reasons.append(f"{VERIFIED_PREFIX}: {euro(google_price)}")
    if google_price < o.price:
        o.price = google_price
        if found.get("airline") and not o.airline:
            o.airline = found["airline"]
        if deal.reference_price:
            deal.discount = 1 - o.price / deal.reference_price
        deal.headline = principles.headline(deal.alert, o)
    return "confirmed"


def run_alert(alert: Alert, sources: list, store: Store, cfg: Config,
              today: date | None = None, *, verifier=None) -> tuple[list[tuple[Deal, str]], RunResult]:
    s = cfg.settings
    result = RunResult()
    to_notify: list[tuple[Deal, str]] = []

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
        for route_key, route_offers in by_route.items():
            # Referenz VOR dem Speichern der neuen Preise bestimmen
            history = store.history(route_key, s.history_days) if source.records_history else []
            for o in route_offers:
                d = principles.evaluate(alert, o, history,
                                        min_samples=s.min_history_samples, today=today)
                if d:
                    deals.append(d)
            if source.records_history:
                store.add_prices(route_offers)

        # Alle Deals, die weiter die Kriterien erfüllen, gelten als "gesehen" - auch wenn
        # sie in diesem Lauf nicht die günstigsten ihrer Strecke/ihres Monats sind.
        seen = {d.fingerprint for d in deals}
        best = best_per_slot(deals)
        rejected_fps: set[str] = set()
        if verifier is not None and source.name in VERIFY_SOURCES and verifier is not source:
            kept = []
            for d in best:
                if verify_deal(d, verifier) == "rejected":
                    rejected_fps.add(d.fingerprint)
                    continue
                kept.append(d)
            best = kept
            result.rejected += len(rejected_fps)
            seen -= rejected_fps          # ein früher gespeicherter Deal läuft damit ab
        best_fps = {d.fingerprint for d in best}
        for d in deals:
            if d.fingerprint not in best_fps and d.fingerprint not in rejected_fps:
                store.refresh_deal(d)
        result.deals += len(best)
        for d in best:
            status = store.upsert_deal(d, s.renotify_drop)
            if status != "known":
                to_notify.append((d, status))
        # Eine Quelle kann mehrere Offer.source-Werte liefern (Feeds: "feed:<preset>")
        for name in getattr(source, "offer_sources", None) or [source.name]:
            if incomplete is None:
                result.expired += store.expire_missing(alert.id, name, seen)
            elif incomplete.get("routes"):
                result.expired += store.expire_missing(alert.id, name, seen,
                                                       skip_routes=set(incomplete["routes"]))

    if len(sources) > 1:
        to_notify = best_across_sources(to_notify)
    to_notify.sort(key=lambda x: x[0].offer.price)
    return to_notify[: s.max_deals_per_alert], result


def _feed_source(sources: list):
    """Die Deal-Seiten-Quelle des Laufs (liefert ``all_items`` für das Radar)."""
    return next((s for s in sources if hasattr(s, "all_items")), None)


def run_radar(sources: list, store: Store, cfg: Config,
              settings: radar.RadarSettings) -> tuple[list[tuple[Deal, str]], RunResult]:
    """Alle Flug-Artikel der Deal-Seiten auf Hammer-Deals prüfen (unabhängig von den Alarmen)."""
    s = cfg.settings
    result = RunResult()
    feed = _feed_source(sources)
    if feed is None or not settings.enabled:
        return [], result
    try:
        deals = radar.scan(feed, settings)
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
    to_notify: list[tuple[Deal, str]] = []
    for d in deals:
        status = store.upsert_deal(d, s.renotify_drop)
        if status != "known":
            to_notify.append((d, status))
    seen = {d.fingerprint for d in deals}
    partial = getattr(feed, "load_errors", None)
    if partial:
        result.errors.append(f"[{radar.RADAR_ALERT_ID}] {feed.name}: {partial}")
    else:
        for name in getattr(feed, "offer_sources", None) or [feed.name]:
            result.expired += store.expire_missing(radar.RADAR_ALERT_ID, name, seen)
    return to_notify[: s.max_deals_per_alert], result


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

    for alert in cfg.alerts:
        if not alert.enabled or (only and alert.id not in only):
            continue
        log.info("Prüfe Alarm %s (%d Strecken)", alert.id, len(alert.pairs))
        items, r = run_alert(alert, sources, store, cfg, today=today, verifier=verifier)
        total.add(r)
        notify(alert.name, items)

    # Radar: nur bei vollen Läufen (nicht bei "nur diesen Alarm prüfen")
    if radar_settings is not None and radar_settings.enabled and not only:
        items, r = run_radar(sources, store, cfg, radar_settings)
        total.add(r)
        notify(radar.RADAR_ALERT_NAME, items)
    return total
