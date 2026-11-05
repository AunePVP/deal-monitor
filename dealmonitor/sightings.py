"""Derselbe Artikel-Deal bei mehreren Deal-Seiten: einmal melden, Quellen sammeln.

Secret Flying meldet ZRH–Mauritius für 471 €, ErrorFareAlerts 19 h später denselben Deal.
Die **erste** Quelle alarmiert. Spätere Meldungen desselben Deals werden nicht erneut
gemeldet, sondern ergänzen den ersten: Quellenliste (Payload ``sources``) und Vorsprung
(``lead``, als Grund "Erstmeldung Secret Flying, 19 h vor ErrorFareAlerts").

Derselbe Deal heißt: andere Quelle und :func:`dealmonitor.dedupe.match` (gleiches Ziel,
gleiche Kabine und Reiseart, Abflug gleich oder bei einer Seite unbekannt, höchstens 7 Tage
auseinander, Preis ± 5 % - ± 15 %, wenn beide dieselbe Airline nennen; verschiedene Airlines
nie). Gespeichert in der Tabelle ``sightings`` (mit den Airlines der Überschrift); jede Zeile
zeigt auf die Deal-Zeile der Erstmeldung.

Nur Artikel (Deals mit Überschrift): Preis-Quellen haben Reisedaten und werden schon im Lauf
über ``monitor.best_across_sources`` zusammengefasst. Fehler hier stoppen nie den Lauf.
"""

from __future__ import annotations

from datetime import datetime

from . import dedupe
from .models import Deal, Offer
from .sources import feed

WINDOW = dedupe.WINDOW
TOLERANCE = dedupe.TOLERANCE
LEAD_PREFIX = "Erstmeldung"


def parts(o: Offer, origins=(), dests=()) -> tuple[list[str], set[str]] | None:
    """``(Schlüssel, Abflug-Codes)`` eines Artikels - ``None`` ohne Überschrift, Preis oder
    erkennbares Ziel. Ziel und Abflug: :func:`dedupe.route` (Codes der Quelle, Orte im Titel
    ohne Airline-Namen, Wort nach "nach"/"to")."""
    if not o.title or not o.price or o.price <= 0:
        return None
    r = dedupe.route(o.title, set(origins) | {c for c in (o.via_hub, o.origin) if c}, dests,
                     o.destination)
    if r is None:
        return None
    dest_set, origin_set = r
    trip = "RT" if o.roundtrip else "OW"
    return sorted(f"{d}-{o.cabin}-{trip}" for d in dest_set), origin_set


def _seen(o: Offer) -> datetime:
    return o.found_at or datetime.now()


def first_report(store, deal: Deal, info: tuple | None, valid=None) -> str | None:
    """Fingerprint des Deals, der denselben Deal schon von einer anderen Quelle gemeldet hat
    (``None``: dies ist die Erstmeldung). ``info`` = Ergebnis von :func:`parts`; ``valid``:
    zusätzliche Fingerprints, die noch nicht gespeichert sind (gleicher Lauf)."""
    if info is None:
        return None
    keys, origins = info
    o = deal.offer
    if o.link:
        # schon früher zugeordnet: bleibt so, auch wenn sich ``found_at`` verschoben hat
        # (Artikel aktualisiert, Artikel ohne Datum) und die Erstmeldung aus dem Fenster fällt
        for row in store.sightings(keys):
            fp = row["fingerprint"]
            if row["source"] == o.source and row["link"] == o.link \
                    and fp != deal.fingerprint and (fp in (valid or ()) or store.has_deal(fp)):
                return fp
    when = _seen(o)
    mine = _deal_key(keys, origins, o.price, dedupe.airlines(o.title or ""), when, o.link)
    for row in store.sightings(keys, since=when - WINDOW, until=when + WINDOW):
        if row["source"] == o.source or row["fingerprint"] == deal.fingerprint:
            continue
        theirs = _deal_key([row["deal_key"]], set(filter(None, (row["origin"] or "").split(","))),
                           row["price"], _airline_set(row.get("airline")),
                           datetime.fromisoformat(row["first_seen"]), row["link"])
        if not dedupe.match(mine, theirs):
            continue
        fp = row["fingerprint"]
        if fp in (valid or ()) or store.has_deal(fp):
            return fp
    return None


def _deal_key(keys: list[str], origins, price: float, airlines, published, link
              ) -> dedupe.DealKey | None:
    """Schlüssel wie "BKK-business-RT" (siehe :func:`parts`) -> :class:`dedupe.DealKey`."""
    split = [k.rsplit("-", 2) for k in keys]
    split = [x for x in split if len(x) == 3]
    if not split:
        return None
    _, cabin, trip = split[0]
    return dedupe.DealKey(cabin=cabin, oneway=trip == "OW",
                          dests=frozenset(d for d, c, t in split if (c, t) == (cabin, trip)),
                          price=float(price or 0), origins=frozenset(origins or ()),
                          airlines=frozenset(airlines or ()), published=published,
                          link=link or "")


def _airline_set(value: str | None) -> frozenset[str]:
    """Spalte ``sightings.airline`` ("turkmenistan,lufthansa") -> Menge; leer = keine Angabe
    (auch alte Zeilen vor der Spalte: dann gilt die 5-%-Regel)."""
    return frozenset(filter(None, (value or "").split(",")))


def record(store, o: Offer, info: tuple | None, fingerprint: str) -> None:
    """Meldung dieser Quelle merken, zugeordnet dem Deal ``fingerprint`` (der Erstmeldung)."""
    if info is None:
        return
    keys, origins = info
    store.add_sighting(keys, ",".join(sorted(origins)), o.source, _seen(o), o.price,
                       fingerprint, o.link,
                       airline=",".join(sorted(dedupe.airlines(o.title or ""))))


def sources_of(store, fingerprint: str) -> list[dict]:
    """Quellen eines Deals, früheste Meldung zuerst: ``[{source, label, seen, price, link}]``.
    Eine Seite zählt einmal, auch über zwei Wege (Travel-Dealz RSS und REST: gleicher Host)."""
    out: dict[str, dict] = {}
    for row in store.sightings(fingerprint=fingerprint):
        site = dedupe.site(row["link"]) or row["source"]
        if site not in out:
            out[site] = {"source": row["source"], "label": feed.source_label(row["source"]),
                         "seen": row["first_seen"], "price": row["price"], "link": row["link"]}
    return list(out.values())


def lead_text(sources: list[dict]) -> str | None:
    """"Erstmeldung Secret Flying, 19 h vor ErrorFareAlerts" (ab zwei Quellen)."""
    if len(sources) < 2:
        return None
    first, second = sources[0], sources[1]
    try:
        hours = (datetime.fromisoformat(second["seen"])
                 - datetime.fromisoformat(first["seen"])).total_seconds() / 3600
    except (TypeError, ValueError):
        return None
    if hours < 1:
        gap = "kurz vor"
    elif hours < 48:
        gap = f"{round(hours)} h vor"
    else:
        gap = f"{round(hours / 24)} Tage vor"
    text = f"{LEAD_PREFIX} {first['label']}, {gap} {second['label']}"
    return text + (f" (insgesamt {len(sources)} Quellen)" if len(sources) > 2 else "")


def _with_lead(reasons: list[str], lead: str | None) -> list[str]:
    out = [r for r in reasons or () if not (isinstance(r, str) and r.startswith(LEAD_PREFIX))]
    return out + ([lead] if lead else [])


def refresh(store, fingerprint: str, deal: Deal | None = None) -> str | None:
    """Quellenliste und Vorsprung des Deals ``fingerprint`` neu berechnen: im gespeicherten
    Deal (Payload ``sources``, ``lead``, Gründe) und im Deal-Objekt dieses Laufs."""
    sources = sources_of(store, fingerprint)
    if len(sources) < 2:
        return None
    lead = lead_text(sources)
    if deal is not None:
        deal.reasons[:] = _with_lead(deal.reasons, lead)
    payload = store.deal_payload(fingerprint)
    if payload is not None:
        store.update_deal_payload(fingerprint, sources=sources, lead=lead,
                                  reasons=_with_lead(payload.get("reasons"), lead))
    return lead
