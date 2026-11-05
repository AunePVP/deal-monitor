"""Quellen-Gesundheit: stille Ausfälle sichtbar machen.

Nach jedem Lauf wird jede gelesene Quelle eingestuft:

* **OK**
* **LEER** - Abruf klappte, aber 0 Artikel oder kein einziger Flug-Deal mit Preis
  (sieht sonst aus wie "keine Deals")
* **VERALTET** - der neueste Artikel ist älter als die Schwelle der Quelle (Alter des Artikels,
  nicht ``lastBuildDate``: das hinkt z. B. bei premium-flights 14 h nach)
* **FEHLER** - Quelle nicht lesbar

Google Flights gilt als LEER, wenn in einem Lauf mehr als die Hälfte der Abrufe leer blieb
(``empty_fetches``), und als FEHLER, wenn der Schutzschalter ausgelöst hat (``down``). Die
Sofort-Prüfung (höchstens 5 Abfragen) stuft Google nur bei FEHLER neu ein - für "leer" sind
das zu wenige Abrufe.

Der Zustand liegt in ``store.kv["state:health"]``. Gemeldet (ntfy/Webhook/Telegram) wird nur
ein **Wechsel** des Zustands, dazu einmal täglich ein Lebenszeichen mit Artikeln je Quelle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from . import notify
from .sources.feed import source_label

log = logging.getLogger(__name__)

OK, EMPTY, STALE, ERROR = "ok", "leer", "veraltet", "fehler"
LABELS = {OK: "OK", EMPTY: "Leer", STALE: "Veraltet", ERROR: "Fehler"}
#: Schlüssel im Store (``kv["state:health"]``)
STATE_NAME = "health"
#: Google: mehr als dieser Anteil "Abruf leer" in einem Lauf -> LEER
GOOGLE_EMPTY_SHARE = 0.5
GOOGLE = "google_flights"
#: Lebenszeichen frühestens ab dieser Stunde (Ortszeit), einmal pro Tag
HEARTBEAT_HOUR = 8
ERROR_CHARS = 200


@dataclass(frozen=True)
class Rule:
    """Wann eine Quelle als LEER/VERALTET gilt."""

    stale_hours: float | None = 72     # neuester Artikel älter -> VERALTET (None = nie)
    need_items: bool = True            # 0 Artikel -> LEER
    need_parsed: bool = True           # Artikel, aber kein Flug-Deal mit Preis -> LEER


#: Schwellen aus Messungen (größte beobachtete Lücke zwischen zwei Artikeln plus Puffer).
#: Unbekannte Quellen: 72 h. Schlüssel = ``Offer.source``; ``feed:premium-flights`` gilt auch
#: für weitere Feeds derselben Seite (``feed:premium-flights-…``).
RULES: dict[str, Rule] = {
    "feed:travel-dealz-rest": Rule(20),      # größte Lücke 17,3 h (nachts)
    # Lücken je Nacht 12-15 h (Abflug Europa, 6 Nächte bis 05.11.2026)
    "feed:secretflying": Rule(20),
    "feed:premium-flights": Rule(120),       # Lücken 34 h, 39 h, 98,5 h in 10 Tagen
    "feed:errorfarealerts": Rule(48),        # Schübe mit Lücken bis 25 h
    "feed:reisetopia": Rule(36, need_parsed=False),   # Annahme; meist Hotels/Meilen
    # YHBU postet etwa einmal pro Woche: 0 Artikel in 14 Tagen ist dort normal
    "feed:yhbu": Rule(240, need_items=False),
    "feed:fly4free-business": Rule(None, need_items=False),   # selten neue Artikel
    "feed:urlaubspiraten": Rule(72, need_parsed=False),  # Preise selten im Titel
    # Suche: 0 Treffer ist normal (Stichwort ohne aktuellen Deal)
    "feed:travel-dealz-suche": Rule(None, need_items=False, need_parsed=False),
    "feed:custom": Rule(None, need_parsed=False),       # eigene Feeds: unbekannt
}


#: Geprüft und bewusst nicht genutzt (Recherche 11/2026, ``docs/handoff/RESEARCH.md`` Abschnitt 5)
NOT_USED: tuple[dict, ...] = (
    {"name": "Lufthansa, SWISS, Eurowings, Condor",
     "reason": "Airline-Seiten sperren automatische Abrufe (403)"},
    {"name": "secretflying.com", "reason": "Cloudflare-Sperre – Secret Flying kommt über Bluesky"},
    {"name": "Amadeus Self-Service", "reason": "vom Anbieter am 17.07.2026 abgeschaltet"},
    {"name": "Skyscanner, Kayak", "reason": "Captcha bzw. gesperrt"},
    {"name": "fly4free Error-Fare-Feed", "reason": "seit 22.08.2025 eingestellt"},
    {"name": "Telegram-Kanäle der Deal-Seiten", "reason": "keine Web-Vorschau abrufbar"},
)


def rule_for(source: str) -> Rule:
    if source in RULES:
        return RULES[source]
    for key, rule in RULES.items():
        if source.startswith(key + "-"):
            return rule
    return Rule()


def _aware(value) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.astimezone()   # naiv = Ortszeit


def _local(dt: datetime | None) -> str | None:
    """Zeitstempel wie im Rest der App: Ortszeit, ohne Zeitzone."""
    if dt is None:
        return None
    return dt.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def _hours(h: float) -> str:
    return f"{h:.0f} Std." if h >= 10 else f"{h:.1f} Std.".replace(".", ",", 1)


def classify(stat: dict, rule: Rule, now: datetime) -> tuple[str, str | None]:
    """Zustand einer Deal-Seite aus ``FeedSource.stats[...]``: (zustand, kurzer Grund)."""
    items = int(stat.get("items") or 0)
    parsed = int(stat.get("parsed") or 0)
    error = stat.get("error")
    if error and items == 0:
        return ERROR, str(error)[:ERROR_CHARS]
    if rule.need_items and items == 0:
        return EMPTY, "0 Artikel geladen"
    if rule.need_parsed and items and parsed == 0:
        return EMPTY, f"{items} Artikel, aber kein Flug-Deal mit Preis erkannt"
    newest = _aware(stat.get("newest"))
    if rule.stale_hours is not None and newest is not None:
        age = (now - newest).total_seconds() / 3600
        if age > rule.stale_hours:
            return STALE, (f"neuester Artikel {_hours(age)} alt "
                           f"(Grenze {_hours(rule.stale_hours)})")
    return OK, None


def google_stat(src) -> dict | None:
    """Kennzahlen der Google-Quelle eines Laufs (None = in diesem Lauf nichts abgefragt)."""
    made = int(getattr(src, "requests_made", 0) or 0)
    down = getattr(src, "down", None)
    if made <= 0 and not down:
        return None
    retries = int(getattr(src, "retries", 0) or 0)       # zweite Abrufe zählen nicht doppelt
    queries = max(made - retries, 0)
    empty = int(getattr(src, "empty_fetches", 0) or 0)   # nur mit "Abruf leer"-Zähler
    return {"items": queries, "empty": min(empty, queries), "error": down}


def classify_google(stat: dict) -> tuple[str, str | None]:
    if stat.get("error"):
        return ERROR, str(stat["error"])[:ERROR_CHARS]
    queries, empty = stat.get("items") or 0, stat.get("empty") or 0
    if queries and empty / queries > GOOGLE_EMPTY_SHARE:
        return EMPTY, f"{empty} von {queries} Abrufen leer"
    return OK, None


def collect(sources: list, now: datetime, *, quick: bool = False) -> dict[str, dict]:
    """Einstufung aller Quellen eines Laufs: ``{source: {state, detail, items, parsed, …}}``.
    Quellen ohne Kennzahlen (Preis-APIs, nicht geladene Feeds) fehlen; ``quick``
    (Sofort-Prüfung): Google nur, wenn der Schutzschalter ausgelöst hat."""
    out: dict[str, dict] = {}
    for src in sources:
        stats = getattr(src, "stats", None)
        if getattr(src, "name", "") == GOOGLE and hasattr(src, "requests_made"):
            g = google_stat(src)
            if g is not None and not (quick and not g["error"]):
                state, detail = classify_google(g)
                out[GOOGLE] = {"state": state, "detail": detail, "items": g["items"],
                               "parsed": g["items"] - g["empty"], "newest": None,
                               "error": g["error"]}
        elif isinstance(stats, dict):
            for name, stat in stats.items():
                if not isinstance(stat, dict):
                    continue
                state, detail = classify(stat, rule_for(name), now)
                out[name] = {"state": state, "detail": detail,
                             "items": int(stat.get("items") or 0),
                             "parsed": int(stat.get("parsed") or 0),
                             "newest": _local(_aware(stat.get("newest"))),
                             "error": (str(stat["error"])[:ERROR_CHARS]
                                       if stat.get("error") else None)}
    return out


def _change_line(name: str, old: str | None, new: dict) -> str:
    label = source_label(name)
    if new["state"] == OK:
        return f"{label}: wieder OK"
    line = f"{label}: {LABELS[new['state']]}"
    if old and old != OK:
        line += f" (vorher {LABELS.get(old, old)})"
    return line + (f" – {new['detail']}" if new.get("detail") else "")


def _heartbeat_text(sources: dict[str, dict]) -> str:
    lines = []
    for name in sorted(sources, key=lambda n: source_label(n).lower()):
        e = sources[name]
        unit = "Abrufe" if name == GOOGLE else "Artikel"
        line = f"{source_label(name)}: {e.get('items', 0)} {unit}"
        if e.get("state") != OK:
            line += f" ({LABELS.get(e.get('state'), e.get('state'))})"
        lines.append(line)
    return "\n".join(lines)


def _names(sources: list) -> set[str]:
    """Alle Gesundheits-Namen, die diese Quellen liefern können."""
    out: set[str] = set()
    for src in sources:
        if getattr(src, "name", "") == GOOGLE:
            out.add(GOOGLE)
            continue
        out |= set(getattr(src, "offer_sources", None) or ())
        stats = getattr(src, "stats", None)
        out |= set(stats) if isinstance(stats, dict) else set()
    return out


def update(store, sources: list, notifiers: list | None = None, *,
           now: datetime | None = None, complete: bool = False,
           quick: bool = False) -> list[str]:
    """Zustände nach einem Lauf fortschreiben und Wechsel melden. Rückgabe: Fehler beim Melden.

    Nur Quellen, die in diesem Lauf gelesen wurden, werden neu eingestuft; die übrigen
    behalten ihren letzten Stand (z. B. ein Lauf nur der schnellen Deal-Seiten).
    ``complete``: ``sources`` sind alle gewählten Quellen (voller Lauf) - abgewählte oder
    abgeschaltete fallen dann heraus. ``quick``: Sofort-Prüfung (siehe :func:`collect`)."""
    now = now or datetime.now(timezone.utc)
    current = collect(sources, now, quick=quick)
    state = store.source_state(STATE_NAME)
    known = state.get("sources") if isinstance(state.get("sources"), dict) else {}
    if complete:
        wanted = _names(sources)
        known = {name: e for name, e in known.items() if name in wanted}
    checked = _local(now)
    changes = []
    for name, entry in current.items():
        prev = known.get(name) if isinstance(known.get(name), dict) else {}
        old = prev.get("state")
        if old == entry["state"]:
            entry["since"] = prev.get("since") or checked
        else:
            entry["since"] = checked
            # Erster Blick auf eine Quelle: nur melden, wenn sie nicht OK ist
            if old is not None or entry["state"] != OK:
                changes.append((_change_line(name, old, entry), entry["state"] == OK))
        entry["checked"] = checked
        known[name] = entry
    state["sources"] = known
    errors: list[str] = []
    if changes:
        bad = not all(ok for _, ok in changes)
        title = (f"Quellen: {len(changes)} Änderungen" if len(changes) > 1
                 else "Quelle " + ("gestört" if bad else "wieder OK"))
        errors += notify.send_status(notifiers or [], title,
                                     "\n".join(line for line, _ in changes),
                                     priority=3 if bad else 2)
    local = now.astimezone()
    today = local.date().isoformat()
    if known and local.hour >= HEARTBEAT_HOUR and state.get("heartbeat") != today:
        state["heartbeat"] = today
        errors += notify.send_status(notifiers or [], "Deal-Monitor läuft",
                                     _heartbeat_text(known), priority=2, heartbeat=True)
    store.set_source_state(STATE_NAME, state)
    return errors


def report(store, now: datetime | None = None) -> dict:
    """Antwort für ``GET /api/sources/health``."""
    now = now or datetime.now(timezone.utc)
    state = store.source_state(STATE_NAME)
    known = state.get("sources") if isinstance(state.get("sources"), dict) else {}
    out = []
    for name, e in known.items():
        if not isinstance(e, dict):
            continue
        newest = _aware(e.get("newest"))
        rule = rule_for(name) if name != GOOGLE else Rule(None)
        st = e.get("state") if e.get("state") in LABELS else ERROR
        items, parsed = e.get("items"), e.get("parsed")
        share = None    # Google: Anteil der Abrufe ohne Ergebnis
        if name == GOOGLE and isinstance(items, int) and isinstance(parsed, int) and items > 0:
            share = round(min(max(items - parsed, 0) / items, 1.0), 3)
        out.append({
            "id": name, "name": source_label(name), "state": st, "label": LABELS[st],
            "detail": e.get("detail"), "items": e.get("items"), "parsed": e.get("parsed"),
            "newest": e.get("newest"),
            "age_hours": round((now - newest).total_seconds() / 3600, 1) if newest else None,
            "threshold_hours": rule.stale_hours, "error": e.get("error"),
            "since": e.get("since"), "checked": e.get("checked"), "empty_share": share,
        })
    order = {ERROR: 0, EMPTY: 1, STALE: 2, OK: 3}
    out.sort(key=lambda x: (order[x["state"]], x["name"].lower()))
    return {"sources": out, "heartbeat": state.get("heartbeat"),
            "labels": LABELS, "not_used": [dict(x) for x in NOT_USED]}
