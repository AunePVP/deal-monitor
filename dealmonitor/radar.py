"""Radar - "keinen krassen Deal verpassen".

Unabhängig von den Alarmen werden *alle* Flug-Artikel aller aktiven Deal-Feeds
geprüft. Treffer werden wie Deals behandelt (``alert_id = "radar"``,
Alarmname "Radar: Hammer-Deals", ``rating = "Radar"``).

Regeln (Einstellungen ``radar_*``, Umgebungsvariablen ``RADAR_*``):

* Immer melden: Überschrift/Kategorie nennt Error Fare, Fehlerpreis, Hammer, Kracher,
  Knaller, Wahnsinn, Preisfehler, Mistake fare, price error, Glitch, CRAZY HOT, PRICE DROP
  oder Preissturz ("Sale"/"Flash" zählen nicht - nur mit Preis unter der Grenze).
* Economy ≤ ``radar_economy_max`` (Standard 300 €) - aber ≥ ``radar_economy_min``
  (Standard 40 €), damit Kurzstrecken-Promos wie "Ryanair ab 14,99€" nicht alarmieren.
* Premium Economy ≤ 700 €, Business ≤ 1.300 €, First ≤ 2.500 €.
* ``radar_home_only`` (Standard an): nur Abflug aus DACH oder ohne Angabe - und ab
  Nachbar-Hub (``dealmonitor.hubs``) in Premium/Business/First oder als Error Fare: dann
  zählt Ticket + Zubringer-Aufschlag gegen die Grenze, mit Hinweis "Abflug Nachbar-Hub".
* Keine Dopplung: Artikel, die im selben Lauf schon einem Alarm zugeordnet wurden
  (``FeedSource.matched_links``), erscheinen nicht noch einmal als Radar-Treffer.

Schnittstelle für Monitor/Settings::

    settings = radar.RadarSettings.from_values(app.values())   # oder from_values({"radar_economy_max": 250})
    deals = radar.scan(feed_source, settings)                  # list[Deal], alert = radar.RADAR_ALERT
    # Speichern/Melden wie bei Alarm-Deals: store.upsert_deal(d, ...), notifier.send(radar.RADAR_ALERT_NAME, ...)
    # Ablauf: store.expire_missing("radar", d.offer.source, {d.fingerprint ...}) je Quelle in radar.sources_used(deals)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from datetime import datetime

from . import hubs, regions
from .models import Alert, Deal, Offer
from .principles import CABIN_LABEL, euro
from .sources import feed as feed_mod

RADAR_ALERT_ID = "radar"
RADAR_ALERT_NAME = "Radar: Hammer-Deals"
RATING = "Radar"

#: Standardwerte der Einstellungen (Name -> Wert); Beschriftungen für die Oberfläche in LABELS
DEFAULTS: dict[str, object] = {
    "radar_enabled": True,
    "radar_economy_max": 300.0,
    "radar_economy_min": 40.0,
    "radar_business_max": 1300.0,
    "radar_first_max": 2500.0,
    "radar_premium_max": 700.0,
    "radar_home_only": True,
}
LABELS: dict[str, str] = {
    "radar_enabled": "Radar: alle Feed-Artikel auf Hammer-Deals prüfen",
    "radar_economy_max": "Radar: Economy bis (€)",
    "radar_economy_min": "Radar: Economy ab (€, darunter Kurzstrecken-Promos)",
    "radar_business_max": "Radar: Business Class bis (€)",
    "radar_first_max": "Radar: First Class bis (€)",
    "radar_premium_max": "Radar: Premium Economy bis (€)",
    "radar_home_only": "Radar: nur Abflug aus Deutschland/Schweiz/Österreich",
}

HOT_RE = re.compile(
    r"\b(?:error[\s-]?fares?|errorfares?|fehlerpreis\w*|preisfehler|"
    r"hammer(?:[\s-]?(?:deal|preis|angebot)s?)?\b|kracher|knaller|wahnsinn\w*|"
    r"mistake[\s-]?fares?|price[\s-]?errors?|glitch\w*|crazy[\s-]?hot\b|"
    r"price[\s-]?drops?\b|preisst(?:urz|ürze)\w*)", re.I)
HOT_LABELS = [
    (re.compile(r"error[\s-]?fares?|errorfares?|mistake[\s-]?fares?|price[\s-]?errors?|glitch",
                re.I), "Error Fare laut Überschrift"),
    (re.compile(r"fehlerpreis|preisfehler", re.I), "Fehlerpreis laut Überschrift"),
    (re.compile(r"price[\s-]?drop|preisst(?:urz|ürze)", re.I), "Preissturz laut Überschrift"),
    (re.compile(r"hammer|kracher|knaller|wahnsinn|crazy[\s-]?hot", re.I),
     "Als Hammer-Deal angekündigt"),
]


@dataclass
class RadarSettings:
    enabled: bool = True
    economy_max: float = 300.0
    economy_min: float = 40.0
    business_max: float = 1300.0
    first_max: float = 2500.0
    premium_max: float = 700.0
    home_only: bool = True
    #: Nachbar-Hubs (aus ``hubs_enabled``, ``hub_surcharge_a/b`` desselben Dicts)
    hub_settings: hubs.HubSettings = hubs.DEFAULT

    @classmethod
    def from_values(cls, values: dict | None) -> "RadarSettings":
        """Aus einem Einstellungs-Dict (``radar_economy_max`` oder ``economy_max``) bauen."""
        kw = {"hub_settings": hubs.HubSettings.from_values(values)}
        for f in fields(cls):
            if f.name == "hub_settings":
                continue
            for key in (f"radar_{f.name}", f.name):
                if values and key in values and values[key] is not None:
                    kw[f.name] = _coerce(f.name, values[key])
                    break
        return cls(**kw)

    def limit(self, cabin: str) -> float:
        return {"economy": self.economy_max, "premium": self.premium_max,
                "business": self.business_max, "first": self.first_max}.get(cabin, self.economy_max)


def _coerce(name: str, value):
    if name in ("enabled", "home_only"):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "ja", "yes", "on", "an")
        return bool(value)
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
    return float(value)


#: Der Pseudo-Alarm, dem Radar-Deals zugeordnet werden (Strecken spielen keine Rolle)
RADAR_ALERT = Alert(id=RADAR_ALERT_ID, name=RADAR_ALERT_NAME, origins=["@DACH"],
                    destinations=["@DACH"], max_price=float(DEFAULTS["radar_economy_max"]))


def hot_reason(title: str, categories: list[str] | None = None) -> str | None:
    """Grund, wenn der Artikel ausdrücklich als Error Fare/Hammer-Deal angekündigt ist."""
    text = title + " " + " ".join(categories or [])
    if not HOT_RE.search(text):
        return None
    for rx, label in HOT_LABELS:
        if rx.search(text):
            return label
    return "Error Fare laut Überschrift"


def check(item: dict, settings: RadarSettings | None = None,
          info: feed_mod.ItemInfo | None = None) -> list[str] | None:
    """Gründe, warum dieser Artikel ein Radar-Treffer ist - ``None`` = kein Treffer.

    ``item`` ist ein Eintrag aus :meth:`FeedSource.parse`/``load`` (mit ``profile``).
    Trifft nicht der Hauptpreis, sondern ein weiterer Kabinenpreis der Überschrift zu
    ("Tokio ab 650€ (Business ab 1.199€)"), werden ``info.cabin``/``info.price`` darauf gesetzt.
    """
    settings = settings or RadarSettings()
    info = info or feed_mod.analyze(item)
    if not info.flight:
        return None
    hot = hot_reason(item["title"], item.get("categories"))
    hub = radar_hub(info, settings)
    if settings.home_only and info.origin == "foreign" and hub is None:
        return None
    reasons: list[str] = []
    if hot:
        reasons.append(hot)
    price = info.price
    # Hauptpreis, dann weitere Kabinen mit eigenem Preis ("Tokio ab 650€ (Business ab 1.199€)")
    options = [(info.cabin, price)] + list(info.alt_prices.items())
    for cabin, p in options:
        if p is None or p <= 0:
            continue
        if hub and cabin == "economy":
            continue     # Economy ab Nachbar-Hub zählt nicht (siehe radar_hub)
        limit = settings.limit(cabin)
        total = p + (hub[1] if hub else 0)
        if total <= limit and (cabin != "economy" or p >= settings.economy_min):
            reasons.append(f"{CABIN_LABEL.get(cabin, cabin)} ≤ {euro(limit)}")
            if (cabin, p) != (info.cabin, price):
                info.cabin, info.price = cabin, p   # der Treffer gilt für diese Kabine
            break
    if hot and not any(p is not None and p > 0 for _, p in options):
        reasons.append("Preis nicht in der Überschrift" if not info.foreign_currency
                       else "Preis nur in Fremdwährung")
    if not reasons:
        return None
    if hub:
        reasons.append(f"Abflug Nachbar-Hub {regions.name(hub[0])} (+{euro(hub[1])} Zubringer)")
    if info.oneway:
        reasons.append("one-way")
    return reasons


def radar_hub(info: feed_mod.ItemInfo, settings: RadarSettings) -> tuple[str, float] | None:
    """Nachbar-Hub eines Artikels mit fremdem Abflug, wenn ``home_only`` ihn sonst verwerfen
    würde: ``(Hub-Stadtcode, Aufschlag)`` oder ``None``. Nur mit Preis in Premium/Business/
    First - Economy ab Amsterdam & Co. ist meist Kurzstrecke, da lohnt kein Zubringer."""
    if not settings.home_only or info.origin != "foreign":
        return None
    if all(c == "economy" for c in [info.cabin, *info.alt_prices]):
        return None
    return hubs.cheapest(info.origin_codes, settings.hub_settings)


def _offer(item: dict, info: feed_mod.ItemInfo, hub: tuple[str, float] | None = None) -> Offer:
    published = item.get("published")
    price = info.price if info.price is not None else 0.0
    if hub and price > 0:
        price = round(price + hub[1], 2)        # wie bei Alarmen: Ticket + Zubringer
    return Offer(
        source=info.source, origin=hub[0] if hub else "", destination="",
        price=price,
        roundtrip=not info.oneway, cabin=info.cabin, link=item["link"], title=item["title"],
        found_at=(published.astimezone().replace(tzinfo=None) if published else datetime.now()),
        expires=info.expires, hot=info.hot, uid=info.uid,
        via_hub=hub[0] if hub else None, surcharge=hub[1] if hub else None,
    )


def scan(items_or_source, settings: RadarSettings | dict | None = None, *,
         exclude_links: set[str] | None = None,
         twins: list[tuple[Offer, Deal]] | None = None) -> list[Deal]:
    """Alle Flug-Artikel prüfen und Radar-Deals liefern (günstigste zuerst, Error Fares vorn).

    ``items_or_source``: eine :class:`FeedSource` (``load``/``all_items`` wird aufgerufen)
    oder eine Liste von Artikel-Dicts. ``exclude_links``: normalisierte Links, die nicht
    gemeldet werden (der Monitor übergibt die Artikel, die Deal eines Alarms wurden); ohne
    Angabe werden die Artikel aus ``FeedSource.matched_links`` ausgelassen. ``settings``: :class:`RadarSettings` oder Dict mit
    ``radar_*``-Werten. Fehler beim Laden (``SourceError``) werden durchgereicht.
    ``twins``: Liste, in die derselbe Deal von einer zweiten Seite als ``(Offer, Deal)``
    eingetragen wird (für die Quellenliste über ``dealmonitor.sightings``).
    """
    if isinstance(settings, dict) or settings is None:
        settings = RadarSettings.from_values(settings)
    if not settings.enabled:
        return []
    exclude = set(exclude_links or ())
    use_matched = exclude_links is None
    if hasattr(items_or_source, "all_items"):
        items = items_or_source.all_items()
        if use_matched:
            exclude |= getattr(items_or_source, "matched_links", set())
    elif hasattr(items_or_source, "load"):
        items = items_or_source.load()
        if use_matched:
            exclude |= getattr(items_or_source, "matched_links", set())
    else:
        items = list(items_or_source)
    out: list[Deal] = []
    kept: list[tuple[Deal, tuple | None]] = []
    seen: set[str] = set()
    analyzed = [(it, feed_mod.analyze(it)) for it in items]
    keys = [info.link or it.get("key") or it.get("link") or it["title"] for it, info in analyzed]
    # Derselbe Deal auf einer anderen Seite wie ein Alarm-Deal: auch nicht melden
    taken = [deal_key(it["title"], info) for (it, info), k in zip(analyzed, keys) if k in exclude]
    for (it, info), key in zip(analyzed, keys):
        if key in exclude or key in seen:
            continue
        reasons = check(it, settings, info)
        if not reasons:
            continue
        seen.add(key)
        dkey = deal_key(it["title"], info)
        if any(same_deal(dkey, t) for t in taken):
            continue
        twin = next((d for d, k in kept if same_deal(dkey, k)), None)
        if twin is not None:      # gleicher Deal bei einer zweiten Seite: einmal melden
            twin.reasons.append(f"Auch bei {feed_mod.source_label(info.source)}: "
                                f"{euro(info.price)}")
            if twins is not None:
                twins.append((_offer(it, info, radar_hub(info, settings)), twin))
            continue
        deal = Deal(alert=RADAR_ALERT, offer=_offer(it, info, radar_hub(info, settings)),
                    rating=RATING,
                    reference_price=None, discount=None, reasons=reasons, headline=it["title"])
        kept.append((deal, dkey))
        out.append(deal)
    out.sort(key=lambda d: (0 if hot_reason(d.offer.title or "") else 1,
                            d.offer.price if d.offer.price > 0 else float("inf")))
    return out


#: Zwei Artikel gelten als derselbe Deal, wenn Kabine, Reisetyp und mindestens eine Zielstadt
#: übereinstimmen und die Preise höchstens so weit auseinanderliegen (Travel-Dealz 1.208 € /
#: premium-flights 1.210 € für Frankfurt–Bangkok Business)
DUPLICATE_TOLERANCE = 0.05


def deal_key(title: str, info: feed_mod.ItemInfo) -> tuple | None:
    """(Kabine, one-way, Ziele, Preis, Abflug-Codes) eines Artikels - ``None``, wenn kein
    Ziel/Preis erkennbar."""
    origins = set(getattr(info, "origin_codes", None) or ())
    origins |= feed_mod.origin_codes(title, "de") | feed_mod.origin_codes(title, "en")
    places = frozenset(p for p in regions.places_in(title) if regions.place_kind(p) != "home")
    if not places or not info.price or info.price <= 0:
        return None
    codes = set()
    for c in origins:      # Flughafen und Stadt: "FRA" und "Frankfurt" sind derselbe Abflug
        codes |= {c.upper()} | regions.codes_for(regions.name(c.upper()))
    return info.cabin, info.oneway, places, info.price, frozenset(codes)


def same_deal(a: tuple | None, b: tuple | None) -> bool:
    """Gleiche Kabine/Reisetyp, ein gemeinsames Ziel mit Flughafen (Stadt, nicht nur Land:
    "Antalya / Türkei" und "Izmir / Türkei" bleiben getrennt), kein verschiedener bekannter
    Abflug ("Bangkok ab Athen" ist nicht "Frankfurt – Bangkok") und fast gleicher Preis."""
    if a is None or b is None or a[:2] != b[:2]:
        return False
    if not any(regions.codes_for(p) for p in a[2] & b[2]):
        return False
    if a[4] and b[4] and not a[4] & b[4]:
        return False
    lo, hi = sorted((a[3], b[3]))
    return hi <= lo * (1 + DUPLICATE_TOLERANCE)


def sources_used(deals: list[Deal]) -> set[str]:
    """``Offer.source``-Werte der Radar-Deals (für ``store.expire_missing`` je Quelle)."""
    return {d.offer.source for d in deals}
