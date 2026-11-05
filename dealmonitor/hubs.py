"""Nachbar-Hubs: Abflug knapp außerhalb von DACH mit Zubringer-Aufschlag.

Viele Business-Schnäppchen nach China starten in Budapest, Luxemburg oder Rom. Statt sie
als "fremder Abflug" zu verwerfen, zählen sie mit einem angenommenen Zubringer-Aufschlag
(Zug/Auto bzw. Zubringerflug ab DACH) - aber nur für Alarme in Business/First oder auf die
Langstrecke. Die Preisgrenze gilt für Ticket + Aufschlag.

Die Stufen stehen in :data:`dealmonitor.regions.NEIGHBOR_HUBS`, die Aufschläge sind
**Annahmen** und über die Einstellungen ``hubs_enabled``, ``hub_surcharge_a`` und
``hub_surcharge_b`` änderbar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import regions

#: Standardwerte der Einstellungen (Name -> Wert); Beschriftungen in LABELS
DEFAULTS: dict[str, object] = {
    "hubs_enabled": True,
    "hub_surcharge_a": 80.0,
    "hub_surcharge_b": 180.0,
}
LABELS: dict[str, str] = {
    "hubs_enabled": "Nachbar-Hubs: Abflug ab Amsterdam, Budapest, Rom … mit Zubringer zulassen",
    "hub_surcharge_a": "Nachbar-Hubs: Aufschlag Zug/Auto (€)",
    "hub_surcharge_b": "Nachbar-Hubs: Aufschlag Zubringerflug (€)",
}
#: Kabinen, für die sich der Weg zum Nachbar-Hub immer lohnen kann (sonst nur Langstrecke)
PREMIUM_CABINS = ("business", "first")


@dataclass(frozen=True)
class HubSettings:
    enabled: bool = True
    surcharge_a: float = 80.0
    surcharge_b: float = 180.0

    @classmethod
    def from_values(cls, values: dict | None) -> "HubSettings":
        """Aus einem Einstellungs-Dict (``hubs_enabled``, ``hub_surcharge_a/b``) bauen.
        Fehlende oder ungültige Werte -> Standard (nie eine Ausnahme)."""
        values = values or {}
        enabled = values.get("hubs_enabled")
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in ("1", "true", "ja", "yes", "on", "an")
        return cls(enabled=cls.enabled if enabled is None else bool(enabled),
                   surcharge_a=_amount(values.get("hub_surcharge_a"), cls.surcharge_a),
                   surcharge_b=_amount(values.get("hub_surcharge_b"), cls.surcharge_b))

    def surcharge(self, tier: str) -> float:
        return self.surcharge_a if tier == "A" else self.surcharge_b


DEFAULT = HubSettings()


def _amount(value, default: float) -> float:
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value >= 0 else default


def hub_code(code: str) -> str | None:
    """Stadtcode des Nachbar-Hubs ("FCO" -> "ROM", "BUD" -> "BUD") oder ``None``."""
    code = str(code or "").strip().upper()
    code = regions.HUB_AIRPORTS.get(code, code)
    return code if regions.hub_tier(code) else None


def classify(codes, settings: HubSettings | None = None) -> tuple[str, float]:
    """Abflug-Codes einordnen: ``("home" | "hub_a" | "hub_b" | "foreign", Aufschlag)``.

    DACH gewinnt immer (Aufschlag 0). Bei mehreren Hubs ("Madrid & Amsterdam") zählt der
    günstigste. Ohne Codes oder ausgeschaltet: ``("foreign", 0.0)``."""
    settings = settings or DEFAULT
    codes = {str(c).strip().upper() for c in (codes or ()) if c}
    if any(regions.is_home(c) for c in codes):
        return "home", 0.0
    best = cheapest(codes, settings)
    if best is None:
        return "foreign", 0.0
    return "hub_" + regions.hub_tier(best[0]).lower(), best[1]


def cheapest(codes, settings: HubSettings | None = None) -> tuple[str, float] | None:
    """Günstigster Nachbar-Hub unter den Codes: ``(Stadtcode, Aufschlag)`` oder ``None``."""
    settings = settings or DEFAULT
    if not settings.enabled:
        return None
    found = []
    for c in codes or ():
        hub = hub_code(c)
        if hub:
            found.append((settings.surcharge(regions.hub_tier(hub)), hub))
    if not found:
        return None
    surcharge, hub = min(found)
    return hub, surcharge


def long_haul(destinations) -> bool:
    """Alle Ziele bekannt und außerhalb Europas (unbekannte Codes zählen nicht)."""
    dests = [str(d).upper() for d in destinations or ()]
    return bool(dests) and all(d in regions.AIRPORT_NAMES and d not in regions.EUROPE_CODES
                               for d in dests)


def allowed(alert) -> bool:
    """Darf dieser Alarm Nachbar-Hubs nutzen? Abflug (auch) aus DACH und Business/First
    oder Langstrecke - für Kurzstrecken in Economy lohnt kein Zubringer."""
    if not any(regions.is_home(o) for o in alert.origins):
        return False
    return alert.cabin in PREMIUM_CABINS or long_haul(alert.destinations)


def for_alert(alert, codes, settings: HubSettings | None = None) -> tuple[str, float] | None:
    """Nachbar-Hub für einen Artikel mit fremdem Abflug (``codes``) und diesen Alarm:
    ``(Stadtcode, Aufschlag)`` oder ``None``. Die Preisgrenze prüft danach der Aufrufer
    gegen Ticketpreis + Aufschlag."""
    if not allowed(alert):
        return None
    return cheapest(codes, settings)


def note(offer) -> str | None:
    """Hinweis für Meldung und Oberfläche, z. B. "Abflug Nachbar-Hub Budapest (+180 €
    Zubringer): Ticket 1.490 €, mit Zubringer 1.670 €". ``None`` ohne Hub."""
    hub = getattr(offer, "via_hub", None)
    if not hub:
        return None
    from .principles import euro
    surcharge = getattr(offer, "surcharge", None) or 0.0
    text = f"Abflug Nachbar-Hub {regions.name(hub)} (+{euro(surcharge)} Zubringer)"
    price = getattr(offer, "price", None)
    if price and price > 0:
        text += f": Ticket {euro(price - surcharge)}, mit Zubringer {euro(price)}"
    return text
