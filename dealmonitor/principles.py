"""Die Deal-Prinzipien - frei nach https://travel-dealz.de/prinzipien/.

Umgesetzte Regeln:

1. **Einheitliche Preisangabe** - Preise gelten immer *pro Person* und für
   *Hin- und Rückflug*. Einzelflüge werden in der Überschrift ausdrücklich
   mit "one-way" gekennzeichnet.
2. **Selbst vergleichen statt Werbeversprechen glauben** - "bis zu 50 %
   Rabatt" interessiert nicht. Der Rabatt wird ausschließlich gegen einen
   eigenen Referenzpreis berechnet: Median der selbst beobachteten Preise
   der Strecke (``history_days``); solange die Historie zu kurz ist, Googles
   Preisniveau der Strecke (Median des Preisverlaufs, sonst Normalpreis),
   ersatzweise der konfigurierte Normalpreis. Dazu: Beinahe-Deals (über der
   Grenze, aber deutlich unter dem Üblichen - nur anzeigen) und Hinweise auf
   Fehlerpreise (weit unter der Spanne, Business kaum teurer als Economy,
   Preissturz).
3. **Nur Deals, die man selbst buchen würde** - zu viele Umstiege, absurde
   Reisezeiten, unpassende Aufenthaltsdauer oder ausgeschlossene Airlines
   fliegen raus, egal wie billig.
4. **Ehrliche Überschriften** - Startpreis, Strecke, Kabine, Airline und
   Umstiege stehen in der Überschrift, nichts wird geschönt.
5. **Quellen nennen** - jeder Deal trägt seine Quelle (API oder Artikel).
6. **Abgelaufene Deals kennzeichnen** - taucht ein gemeldeter Deal nicht mehr
   auf oder ist er teurer geworden, wird er als "abgelaufen" markiert.
7. **Kein Spam** - ein Deal wird nur einmal gemeldet; erneut nur, wenn der
   Preis spürbar weiter gefallen ist (``renotify_drop``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from statistics import median

from . import hubs, regions, rotation
from .models import Alert, Deal, Offer

CABIN_LABEL = {
    "economy": "Economy",
    "premium": "Premium Economy",
    "business": "Business Class",
    "first": "First Class",
}

TOP_DEAL = 0.45
VERY_GOOD = 0.30


def euro(value: float) -> str:
    """Deutsches Preisformat: 1.799 € / 89 €."""
    return f"{value:,.0f}".replace(",", ".") + " €"


REF_MEDIAN = "Median der letzten Beobachtungen"
REF_BASELINE = "Normalpreis laut Konfiguration"
REF_GOOGLE_HISTORY = "Median des Google-Preisverlaufs"
REF_GOOGLE_TYPICAL = "Normalpreis laut Google"

#: Beinahe-Deal: über der Grenze, aber mindestens so viel unter dem Median (oder unter
#: Googles typischer Spanne) - wird angezeigt, aber nicht gemeldet
NEAR_DISCOUNT = 0.25
#: Verdacht auf Fehlerpreis: Preis unter diesem Anteil der typischen Spanne bzw. des Medians
ERROR_SHARE = 0.40
#: Business/First höchstens so viel teurer als Economy derselben Strecke -> verdächtig
#: (normal ist das 3- bis 4-Fache: FRA-PEK 2.898/720 €)
CABIN_RATIO = 1.6
#: Preissturz: so viel unter dem bisherigen Bestpreis bzw. dem Median der letzten Tage
DROP = 0.25
DROP_DAYS = 30
#: so viele Punkte braucht der Google-Preisverlauf, damit sein Median zählt
MIN_GOOGLE_POINTS = 3

ERROR_PREFIX = "Verdacht auf Fehlerpreis"
DROP_PREFIX = "Preissturz"
RANGE_TEXT = "unter typischer Spanne"


@dataclass
class Market:
    """Was der Monitor zusätzlich über die Strecke weiß (alles optional)."""

    #: eigene Preise der Strecke aus den letzten ``DROP_DAYS`` Tagen (vor diesem Lauf)
    recent: list[float] = field(default_factory=list)
    #: Median der Economy-Preise derselben Strecke (für Business/First)
    economy: float | None = None


def _amount(value) -> float | None:
    """Positive endliche Zahl oder ``None`` (Google-Werte sind abgeleitet: nie vertrauen)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def google_levels(insight) -> dict:
    """Bereinigtes Preisniveau aus ``Offer.insight``: ``typical``, ``typical_low``,
    ``typical_high``, ``median`` (des Verlaufs), ``level``, ``saving``. Fehlende oder kaputte
    Werte sind ``None`` - nie eine Ausnahme."""
    out = dict.fromkeys(("typical", "typical_low", "typical_high", "median", "level", "saving"))
    if not isinstance(insight, dict):
        return out
    for key in ("typical", "typical_low", "typical_high", "saving"):
        out[key] = _amount(insight.get(key))
    if not (out["typical_low"] and out["typical_high"]) \
            or out["typical_high"] < out["typical_low"]:
        out["typical_low"] = out["typical_high"] = None
    history = insight.get("history")
    points = [v for p in (history if isinstance(history, list) else ())
              if isinstance(p, (list, tuple)) and len(p) == 2 and (v := _amount(p[1]))]
    if len(points) >= MIN_GOOGLE_POINTS:
        out["median"] = float(median(points))
    level = insight.get("level")
    out["level"] = level if isinstance(level, str) else None
    return out


def reference(alert: Alert, history: list[float], min_samples: int,
              insight: dict | None = None) -> tuple[float | None, str]:
    """Vergleichspreis und seine Herkunft: Median der eigenen Historie; solange sie zu kurz
    ist, Googles Preisniveau der Strecke (Median des Verlaufs, sonst Normalpreis); zuletzt der
    konfigurierte Normalpreis."""
    if len(history) >= min_samples:
        return float(median(history)), REF_MEDIAN
    g = google_levels(insight)
    if g["median"]:
        return g["median"], REF_GOOGLE_HISTORY
    if g["typical"]:
        return g["typical"], REF_GOOGLE_TYPICAL
    return alert.baseline_price, REF_BASELINE


def reference_price(alert: Alert, history: list[float], min_samples: int,
                    insight: dict | None = None) -> float | None:
    """Eigener Vergleichspreis (siehe :func:`reference`)."""
    return reference(alert, history, min_samples, insight)[0]


def _pct(value: float) -> str:
    return f"{value:.0%}"


def market_notes(offer: Offer) -> list[str]:
    """Einordnung nach Googles Preisniveau: "31% unter typischer Spanne (195–275 €)",
    "Google: Preise derzeit niedrig (67 € unter Normalpreis)"."""
    g = google_levels(offer.insight)
    notes = []
    lo, hi = g["typical_low"], g["typical_high"]
    if lo and 0 < offer.price < lo:
        notes.append(f"{_pct(1 - offer.price / lo)} {RANGE_TEXT} ({euro(lo)}–{euro(hi)})")
    if g["level"] == "niedrig":
        notes.append("Google: Preise derzeit niedrig"
                     + (f" ({euro(g['saving'])} unter Normalpreis)" if g["saving"] else ""))
    return notes


def warning_signs(offer: Offer, *, median_price: float | None = None,
                  market: Market | None = None) -> list[str]:
    """Hinweise auf einen Fehlerpreis: weit unter der typischen Spanne (kennt Google sie, zählt
    nur sie - der Median liegt höher, auf ZRH⇄HAM wäre sonst jeder 39-€-Sale verdächtig) oder
    sonst dem Median, Business kaum teurer als Economy, Preissturz gegenüber den eigenen
    Beobachtungen."""
    p = offer.price
    if not p or p <= 0:
        return []
    signs = []
    lo = google_levels(offer.insight)["typical_low"]
    if lo and p < ERROR_SHARE * lo:
        signs.append(f"{ERROR_PREFIX}: {_pct(1 - p / lo)} {RANGE_TEXT}")
    elif not lo and median_price and p < ERROR_SHARE * median_price:
        signs.append(f"{ERROR_PREFIX}: {_pct(1 - p / median_price)} unter dem Median "
                     f"{euro(median_price)}")
    market = market or Market()
    eco = _amount(market.economy)
    if eco and offer.cabin in ("business", "first") and p <= CABIN_RATIO * eco:
        ratio = f"{p / eco:.1f}".replace(".", ",")
        signs.append(f"{ERROR_PREFIX}: {CABIN_LABEL[offer.cabin]} nur {ratio}-mal so teuer "
                     f"wie Economy ({euro(eco)})")
    recent = [v for v in market.recent if _amount(v)]
    if recent:
        best, mid = min(recent), float(median(recent))
        if p <= (1 - DROP) * best:
            signs.append(f"{DROP_PREFIX}: {_pct(1 - p / best)} unter dem bisherigen Bestpreis "
                         f"{euro(best)} – vermuteter Fehlerpreis")
        elif p <= (1 - DROP) * mid and p < best:
            signs.append(f"{DROP_PREFIX}: {_pct(1 - p / mid)} unter dem Median der letzten "
                         f"{DROP_DAYS} Tage ({euro(mid)}) – vermuteter Fehlerpreis")
    return signs


def suspected_error_fare(reasons: list[str] | None) -> bool:
    """Trägt der Deal einen Fehlerpreis-Verdacht (auch Preissturz)?"""
    return any(isinstance(r, str) and r.startswith((ERROR_PREFIX, DROP_PREFIX))
               for r in reasons or ())


#: Reihenfolge der Kurzbegründung: das Aussagekräftigste zuerst
WHY_ORDER = (ERROR_PREFIX, DROP_PREFIX, RANGE_TEXT, "Erstmeldung", "Abflug Nachbar-Hub",
             "unter Referenz", "Google: Preise", "unter deiner Grenze")


def why(reasons: list[str] | None, parts: int = 2) -> str | None:
    """Kurze Begründung für Meldung und Oberfläche aus den Gründen eines Deals, z. B.
    "31% unter typischer Spanne (195–275 €) · Erstmeldung Secret Flying, 19 h vor
    ErrorFareAlerts". ``None``, wenn nichts Erklärendes dabei ist."""
    picked: list[str] = []
    for key in WHY_ORDER:
        if len(picked) >= parts:
            break
        if any(key in p for p in picked):
            continue        # "Verdacht …: 65% unter typischer Spanne" nennt die Spanne schon
        hit = next((r for r in reasons or () if isinstance(r, str) and key in r), None)
        if hit:
            picked.append(hit.split(": Ticket")[0])     # Nachbar-Hub ohne Rechnung
    return " · ".join(picked) or None


def near_deal(alert: Alert, offer: Offer, history: list[float], *, min_samples: int = 5,
              today: date | None = None) -> list[str] | None:
    """Beinahe-Deal: verfehlt Grenze bzw. Mindestrabatt, liegt aber unter Googles typischer
    Spanne oder mindestens ``NEAR_DISCOUNT`` unter dem Median - Gründe oder ``None``.
    Wird angezeigt, aber nie gemeldet."""
    if quality_problems(alert, offer, today):
        return None
    ref, source = reference(alert, history, min_samples, offer.insight)
    if assess(alert, offer.price, ref, source) is not None:
        return None                     # ein echter Deal, kein Beinahe-Deal
    mid = float(median(history)) if len(history) >= min_samples \
        else google_levels(offer.insight)["median"]
    notes = [n for n in market_notes(offer) if RANGE_TEXT in n]
    if mid and offer.price <= (1 - NEAR_DISCOUNT) * mid:
        notes.append(f"{_pct(1 - offer.price / mid)} unter dem Median {euro(mid)}")
    if not notes:
        return None
    if alert.max_price is not None and offer.price > alert.max_price:
        notes.insert(0, f"{euro(offer.price - alert.max_price)} über deiner Grenze")
    return notes


def quality_problems(alert: Alert, offer: Offer, today: date | None = None) -> list[str]:
    """Gründe, warum man diesen Flug *nicht* buchen würde (leer = ok)."""
    today = today or date.today()
    problems: list[str] = []

    if offer.price <= 0:
        problems.append("Preis ungültig")
    if offer.roundtrip != alert.roundtrip:
        problems.append("falscher Reisetyp (one-way/Hin- und Rückflug)")
    if offer.cabin != alert.cabin:
        problems.append(f"falsche Kabine ({offer.cabin})")
    if offer.departure and offer.departure <= today:
        problems.append("Abflug liegt in der Vergangenheit")
    if offer.stops is not None and offer.stops > alert.max_stops:
        problems.append(f"{offer.stops} Umstiege (max. {alert.max_stops})")
    if alert.max_duration_hours is not None:
        limit = alert.max_duration_hours * 60
        for label, minutes in (("Hinflug", offer.duration_out_min),
                               ("Rückflug", offer.duration_back_min)):
            if minutes is not None and minutes > limit:
                problems.append(f"{label} dauert {minutes / 60:.1f} h "
                                f"(max. {alert.max_duration_hours:g} h)")
    nights = offer.nights
    if nights is not None:
        if alert.min_nights is not None and nights < alert.min_nights:
            problems.append(f"nur {nights} Nächte (min. {alert.min_nights})")
        if alert.max_nights is not None and nights > alert.max_nights:
            problems.append(f"{nights} Nächte (max. {alert.max_nights})")
    if offer.airline and offer.airline.upper() in alert.exclude_airlines:
        problems.append(f"Airline {offer.airline} ausgeschlossen")
    return problems


def headline(alert: Alert, offer: Offer) -> str:
    """Überschrift im Travel-Dealz-Stil - ehrlich und vollständig."""
    if offer.title:  # Artikel aus einem Feed: Originaltitel behalten
        return offer.title
    parts = []
    if offer.cabin != "economy":
        parts.append(f"{CABIN_LABEL[offer.cabin]}:")
    parts.append(f"{regions.name(offer.origin)} nach {regions.name(offer.destination)}")
    parts.append(f"ab {euro(offer.price)}")
    parts.append("hin und zurück" if offer.roundtrip else "one-way")
    details = []
    if offer.airline:
        details.append(offer.airline)
    if offer.stops is not None:
        details.append("direkt" if offer.stops == 0 else
                       f"{offer.stops} Umstieg" + ("e" if offer.stops > 1 else ""))
    if offer.nights is not None:
        details.append(f"{offer.nights} Nächte")
    text = " ".join(parts)
    if details:
        text += " (" + ", ".join(details) + ")"
    return text


def assess(alert: Alert, price: float, ref: float | None,
           ref_source: str = REF_BASELINE) -> tuple[str, float | None, list[str]] | None:
    """Preis gegen Grenze/Referenz prüfen: ``(rating, discount, reasons)`` oder ``None``."""
    discount = (1 - price / ref) if ref else None
    reasons: list[str] = []

    if alert.max_price is not None:
        # Die vom Nutzer gesetzte Grenze ist hart.
        if price > alert.max_price:
            return None
        reasons.append(f"unter deiner Grenze von {euro(alert.max_price)}")
    else:
        if discount is None or discount < alert.min_discount:
            return None

    if discount is not None and ref:
        if discount > 0:
            reasons.append(f"{discount:.0%} unter Referenz {euro(ref)} ({ref_source})")
        else:
            reasons.append(f"Referenz {euro(ref)} ({ref_source}) - kein Rabatt, aber im Budget")

    if discount is not None and discount >= TOP_DEAL:
        rating = "Top-Deal"
    elif discount is not None and discount >= VERY_GOOD:
        rating = "Sehr gut"
    elif discount is None and alert.max_price and price <= 0.75 * alert.max_price:
        rating = "Sehr gut"
    else:
        rating = "Deal"
    return rating, discount, reasons


def evaluate(
    alert: Alert,
    offer: Offer,
    history: list[float],
    *,
    min_samples: int = 5,
    today: date | None = None,
    market: Market | None = None,
) -> Deal | None:
    """Prüft ein Angebot gegen die Prinzipien. Gibt einen Deal oder ``None`` zurück.

    ``market``: eigene Preise der letzten Tage und Economy-Median (Fehlerpreis-Verdacht)."""
    if quality_problems(alert, offer, today):
        return None

    ref, ref_source = reference(alert, history, min_samples, offer.insight)
    result = assess(alert, offer.price, ref, ref_source)
    if result is None:
        return None
    rating, discount, reasons = result
    mid = ref if ref_source in (REF_MEDIAN, REF_GOOGLE_HISTORY) else None
    return Deal(
        alert=alert,
        offer=offer,
        rating=rating,
        reference_price=ref,
        discount=discount,
        reasons=_with_hub(offer, reasons, warning_signs(offer, median_price=mid, market=market)),
        headline=headline(alert, offer),
        reference_source=ref_source,
    )


def _with_hub(offer: Offer, reasons: list[str], signs: list[str] | None = None) -> list[str]:
    """Abflug ab Nachbar-Hub zuerst nennen (Ticketpreis + Zubringer = gemeldeter Preis), ebenso
    Hin/Rück aus zwei One-ways (Google-Rotation); dann Fehlerpreis-Hinweise, die Gründe und
    Googles Einordnung der Strecke."""
    notes = [n for n in (hubs.note(offer), rotation.note(offer)) if n]
    return notes + list(signs or []) + list(reasons) + market_notes(offer)


def reprice(deal: Deal, price: float, today: date | None = None) -> bool:
    """Deal auf einen neuen (z. B. bei Google bestätigten) Preis umrechnen: Rabatt, Gründe,
    Bewertung und Überschrift neu. ``False``, wenn er damit kein Deal mehr ist (Grenze/Rabatt
    verfehlt oder Flug passt nicht mehr zu den Regeln) - der Deal bleibt dann unverändert."""
    o = deal.offer
    old = o.price
    o.price = price
    ref, source = deal.reference_price, deal.reference_source or REF_BASELINE
    if source == REF_BASELINE:      # inzwischen kennt Google die Strecke: dessen Niveau zählt
        g_ref, g_source = reference(deal.alert, [], 1, o.insight)
        if g_source != REF_BASELINE:
            ref, source = g_ref, g_source
    result = None if quality_problems(deal.alert, o, today) else assess(
        deal.alert, price, ref, source)
    if result is None:
        o.price = old
        return False
    mid = ref if source in (REF_MEDIAN, REF_GOOGLE_HISTORY) else None
    deal.reference_price, deal.reference_source = ref, source
    deal.rating, deal.discount = result[0], result[1]
    deal.reasons = _with_hub(o, result[2], warning_signs(o, median_price=mid))
    deal.headline = headline(deal.alert, o)
    return True
