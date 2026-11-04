"""Ein Monitor-Lauf: Quellen abfragen -> Prinzipien prüfen -> speichern -> melden."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from . import principles
from .config import Config
from .models import Alert, Deal, Offer
from .sources import SourceError
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    offers: int = 0
    deals: int = 0
    notified: int = 0
    expired: int = 0
    errors: list[str] = field(default_factory=list)


def best_per_slot(deals: list[Deal]) -> list[Deal]:
    """Pro Strecke und Abflugmonat nur den günstigsten Deal behalten.

    Sonst würden 30 fast identische Termine derselben Strecke gemeldet.
    """
    best: dict[tuple, Deal] = {}
    for d in deals:
        o = d.offer
        month = o.departure.strftime("%Y-%m") if o.departure else None
        key = (o.source, o.origin, o.destination, month, o.title)
        if key not in best or o.price < best[key].offer.price:
            best[key] = d
    return sorted(best.values(), key=lambda d: d.offer.price)


def run_alert(alert: Alert, sources: list, store: Store, cfg: Config,
              today: date | None = None) -> tuple[list[tuple[Deal, str]], RunResult]:
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
        best_fps = {d.fingerprint for d in best}
        for d in deals:
            if d.fingerprint not in best_fps:
                store.refresh_deal(d)
        result.deals += len(best)
        for d in best:
            status = store.upsert_deal(d, s.renotify_drop)
            if status != "known":
                to_notify.append((d, status))
        if incomplete is None:
            result.expired += store.expire_missing(alert.id, source.name, seen)
        elif incomplete.get("routes"):
            result.expired += store.expire_missing(alert.id, source.name, seen,
                                                   skip_routes=set(incomplete["routes"]))

    to_notify.sort(key=lambda x: x[0].offer.price)
    return to_notify[: s.max_deals_per_alert], result


def run(cfg: Config, sources: list, notifiers: list, store: Store,
        only: list[str] | None = None, today: date | None = None) -> RunResult:
    total = RunResult()
    # Deals mit Abflug in der Vergangenheit ablaufen lassen - auch bei pausierten Alarmen
    # und Quellen, die gerade nicht funktionieren
    total.expired += store.expire_departed(today or date.today())
    sent_articles: set[str] = set()   # ein Artikel passt evtl. auf mehrere Alarme
    for alert in cfg.alerts:
        if not alert.enabled or (only and alert.id not in only):
            continue
        log.info("Prüfe Alarm %s (%d Strecken)", alert.id, len(alert.pairs))
        items, r = run_alert(alert, sources, store, cfg, today=today)
        total.offers += r.offers
        total.deals += r.deals
        total.expired += r.expired
        total.errors += r.errors
        items = [(d, st) for d, st in items
                 if not (d.offer.title and d.offer.link in sent_articles)]
        sent_articles.update(d.offer.link for d, _ in items if d.offer.title and d.offer.link)
        if not items:
            continue
        for n in notifiers:
            try:
                n.send(alert.name, items)
            except Exception as e:  # ein kaputter Kanal darf den Lauf nicht stoppen
                msg = f"Benachrichtigung über {n.name} fehlgeschlagen: {e}"
                log.error(msg)
                total.errors.append(msg)
        total.notified += len(items)
    return total
