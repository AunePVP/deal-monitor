"""Derselbe Artikel-Deal bei mehreren Deal-Seiten: einmal melden, Quellen sammeln.

Secret Flying meldet ZRH–Mauritius für 471 €, ErrorFareAlerts 19 h später denselben Deal.
Die **erste** Quelle alarmiert. Spätere Meldungen desselben Deals werden nicht erneut
gemeldet, sondern ergänzen den ersten: Quellenliste (Payload ``sources``) und Vorsprung
(``lead``, als Grund "Erstmeldung Secret Flying, 19 h vor ErrorFareAlerts").

Derselbe Deal heißt: andere Quelle, gleiches Ziel (ein gemeinsamer IATA-Code), gleiche Kabine
und Reiseart, Abflug gleich oder bei einer Seite unbekannt, Preis höchstens 5 % auseinander
und höchstens 7 Tage zwischen den Veröffentlichungen. Gespeichert in der Tabelle
``sightings``; jede Zeile zeigt auf die Deal-Zeile der Erstmeldung.

Nur Artikel (Deals mit Überschrift): Preis-Quellen haben Reisedaten und werden schon im Lauf
über ``monitor.best_across_sources`` zusammengefasst. Fehler hier stoppen nie den Lauf.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from . import regions
from .models import Deal, Offer
from .sources import feed

WINDOW = timedelta(days=7)
TOLERANCE = 0.05
LEAD_PREFIX = "Erstmeldung"
#: erstes Ziel nach "nach"/"to": ein oder zwei großgeschriebene Wörter ("New York", "Phuket")
DEST_WORD_RE = re.compile(r"\b(?:nach|to)\s+([A-ZÄÖÜ][\w'’.-]+(?:\s+[A-ZÄÖÜ][\w'’.-]+)?)")


def _expand(codes) -> set[str]:
    """Codes samt Stadt-/Flughafen-Gruppe ("PEK" -> {"BJS", "PEK", "PKX"})."""
    out: set[str] = set()
    for c in codes or ():
        c = str(c).strip().upper()
        if not c:
            continue
        out.add(c)
        out |= regions.codes_for(regions.name(c))
    return out


def dest_word(title: str) -> str | None:
    """Ziel nach "nach"/"to" als Schlüssel-Wort, auch wenn der Ort unbekannt ist ("Frankfurt,
    Germany to Phuket, Thailand" und "Von Frankfurt nach Phuket ab 496 €" -> "~phuket")."""
    m = DEST_WORD_RE.search(title or "")
    if not m:
        return None
    word = m.group(1)
    if regions.place_kind(word) == "home":
        return None
    return "~" + regions.normalize_name(word)


def parts(o: Offer, origins=(), dests=()) -> tuple[list[str], set[str]] | None:
    """``(Schlüssel, Abflug-Codes)`` eines Artikels - ``None`` ohne Überschrift, Preis oder
    erkennbares Ziel. Ziel = Codes der Quelle (Loader), Orte im Titel und das Wort nach
    "nach"/"to"; so finden "SEL" (Titel "Seoul") und "ICN" (Loader) zusammen."""
    if not o.title or not o.price or o.price <= 0:
        return None
    origin_set = _expand(set(origins) | {c for c in (o.via_hub, o.origin) if c})
    origin_set |= _expand(feed.origin_codes(o.title, "de") | feed.origin_codes(o.title, "en"))
    dest_set = _expand(dests)
    # Ziel in DACH ("Zürich nach Hamburg"): ein Heimat-Ort ist Ziel, wenn die Quelle ihn als
    # Ziel nennt oder er nach "nach"/"to" steht - sonst gilt er als Abflug
    m = DEST_WORD_RE.search(o.title)
    hints = dest_set | (regions.codes_for(m.group(1)) if m else set())
    home_dest: set[str] = set()
    for place in regions.places_in(o.title):
        codes = regions.codes_for(place)
        if regions.place_kind(place) != "home":
            dest_set |= codes
        elif codes and codes <= hints:
            home_dest |= codes
        else:
            origin_set |= codes
    origin_set -= home_dest
    dest_set = (dest_set - origin_set) | home_dest
    if (word := dest_word(o.title)):
        dest_set |= regions.codes_for(word[1:]) - origin_set or {word}
    if not dest_set and o.destination:     # Alarm mit genau einem Ziel
        dest_set = _expand({o.destination})
    if not dest_set:
        return None
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
    for row in store.sightings(keys, since=when - WINDOW, until=when + WINDOW):
        if row["source"] == o.source or row["fingerprint"] == deal.fingerprint:
            continue
        lo, hi = sorted((row["price"], o.price))
        if hi > lo * (1 + TOLERANCE):
            continue
        theirs = set(filter(None, (row["origin"] or "").split(",")))
        if origins and theirs and not origins & theirs:
            continue
        fp = row["fingerprint"]
        if fp in (valid or ()) or store.has_deal(fp):
            return fp
    return None


def record(store, o: Offer, info: tuple | None, fingerprint: str) -> None:
    """Meldung dieser Quelle merken, zugeordnet dem Deal ``fingerprint`` (der Erstmeldung)."""
    if info is None:
        return
    keys, origins = info
    store.add_sighting(keys, ",".join(sorted(origins)), o.source, _seen(o), o.price,
                       fingerprint, o.link)


def sources_of(store, fingerprint: str) -> list[dict]:
    """Quellen eines Deals, früheste Meldung zuerst: ``[{source, label, seen, price, link}]``.
    Eine Seite zählt einmal, auch über zwei Wege (Travel-Dealz RSS und REST: gleicher Host)."""
    out: dict[str, dict] = {}
    for row in store.sightings(fingerprint=fingerprint):
        site = _site(row["link"]) or row["source"]
        if site not in out:
            out[site] = {"source": row["source"], "label": feed.source_label(row["source"]),
                         "seen": row["first_seen"], "price": row["price"], "link": row["link"]}
    return list(out.values())


def _site(link: str | None) -> str | None:
    host = urlsplit(link or "").hostname or ""
    return host.removeprefix("www.") or None


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
