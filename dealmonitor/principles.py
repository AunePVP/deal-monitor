"""Die Deal-Prinzipien - frei nach https://travel-dealz.de/prinzipien/.

Umgesetzte Regeln:

1. **Einheitliche Preisangabe** - Preise gelten immer *pro Person* und für
   *Hin- und Rückflug*. Einzelflüge werden in der Überschrift ausdrücklich
   mit "one-way" gekennzeichnet.
2. **Selbst vergleichen statt Werbeversprechen glauben** - "bis zu 50 %
   Rabatt" interessiert nicht. Der Rabatt wird ausschließlich gegen einen
   eigenen Referenzpreis berechnet: Median der selbst beobachteten Preise
   der Strecke (``history_days``), ersatzweise der konfigurierte Normalpreis.
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

from datetime import date
from statistics import median

from . import regions
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


def reference_price(alert: Alert, history: list[float], min_samples: int) -> float | None:
    """Eigener Vergleichspreis: Median der Historie, sonst Normalpreis."""
    if len(history) >= min_samples:
        return float(median(history))
    return alert.baseline_price


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


REF_MEDIAN = "Median der letzten Beobachtungen"
REF_BASELINE = "Normalpreis laut Konfiguration"


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
) -> Deal | None:
    """Prüft ein Angebot gegen die Prinzipien. Gibt einen Deal oder ``None`` zurück."""
    if quality_problems(alert, offer, today):
        return None

    ref = reference_price(alert, history, min_samples)
    ref_source = REF_MEDIAN if len(history) >= min_samples else REF_BASELINE
    result = assess(alert, offer.price, ref, ref_source)
    if result is None:
        return None
    rating, discount, reasons = result
    return Deal(
        alert=alert,
        offer=offer,
        rating=rating,
        reference_price=ref,
        discount=discount,
        reasons=reasons,
        headline=headline(alert, offer),
        reference_source=ref_source,
    )


def reprice(deal: Deal, price: float, today: date | None = None) -> bool:
    """Deal auf einen neuen (z. B. bei Google bestätigten) Preis umrechnen: Rabatt, Gründe,
    Bewertung und Überschrift neu. ``False``, wenn er damit kein Deal mehr ist (Grenze/Rabatt
    verfehlt oder Flug passt nicht mehr zu den Regeln) - der Deal bleibt dann unverändert."""
    o = deal.offer
    old = o.price
    o.price = price
    result = None if quality_problems(deal.alert, o, today) else assess(
        deal.alert, price, deal.reference_price, deal.reference_source or REF_BASELINE)
    if result is None:
        o.price = old
        return False
    deal.rating, deal.discount, deal.reasons = result[0], result[1], list(result[2])
    deal.headline = headline(deal.alert, o)
    return True
