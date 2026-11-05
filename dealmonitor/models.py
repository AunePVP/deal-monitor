"""Datenmodelle: Alarm, Angebot (Offer) und bewerteter Deal."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime

from . import regions


@dataclass
class Alert:
    """Ein Deal-Alarm, z. B. "Zürich ⇄ Hamburg unter 120 €"."""

    id: str
    name: str
    origins: list[str]
    destinations: list[str]
    trip: str = "roundtrip"            # "roundtrip" | "oneway"
    cabin: str = "economy"             # "economy" | "business" | "first"
    max_price: float | None = None     # absolute Preisgrenze pro Person (EUR)
    min_discount: float = 0.30         # oder: so viel unter dem Referenzpreis
    baseline_price: float | None = None  # Normalpreis, solange noch keine Historie existiert
    max_stops: int = 1
    max_duration_hours: float | None = None   # pro Richtung
    min_nights: int | None = None
    max_nights: int | None = None
    months_ahead: int | None = None    # überschreibt settings.scan_months
    exclude_airlines: list[str] = field(default_factory=list)
    feed_keywords: list[list[str]] = field(default_factory=list)
    enabled: bool = True
    #: Google Flights prüft reihum jeden Abflugtag einzeln (one-way), siehe ``rotation.py``
    rotation: bool = False

    def __post_init__(self) -> None:
        self.origins = regions.expand(self.origins)
        self.destinations = regions.expand(self.destinations)
        if self.trip not in ("roundtrip", "oneway"):
            raise ValueError(f"[{self.id}] trip muss 'roundtrip' oder 'oneway' sein")
        if self.cabin not in ("economy", "premium", "business", "first"):
            raise ValueError(f"[{self.id}] unbekannte Kabine '{self.cabin}'")
        if self.max_price is None and self.baseline_price is None:
            raise ValueError(
                f"[{self.id}] bitte max_price und/oder baseline_price angeben"
            )
        self.exclude_airlines = [a.upper() for a in self.exclude_airlines]
        if not isinstance(self.rotation, bool):
            raise ValueError(f"[{self.id}] rotation muss true oder false sein")

    @property
    def roundtrip(self) -> bool:
        return self.trip == "roundtrip"

    @property
    def pairs(self) -> list[tuple[str, str]]:
        return [(o, d) for o in self.origins for d in self.destinations if o != d]


#: optionale Offer-Felder, die ``Offer.extras`` ausgibt (Reihenfolge = Ausgabe)
EXTRA_FIELDS = ("insight", "via_hub", "surcharge", "expires", "hot", "uid", "legs")


@dataclass
class Offer:
    """Ein konkretes Preisangebot aus einer Quelle (pro Person, in EUR)."""

    source: str                 # "travelpayouts", "travel-dealz-feed", ...
    origin: str
    destination: str
    price: float
    currency: str = "EUR"
    roundtrip: bool = True
    cabin: str = "economy"
    departure: date | None = None
    return_date: date | None = None
    airline: str | None = None
    stops: int | None = None             # max. Umstiege pro Richtung
    duration_out_min: int | None = None
    duration_back_min: int | None = None
    link: str | None = None
    title: str | None = None             # bei Feed-Deals: Originalüberschrift
    found_at: datetime = field(default_factory=datetime.now)
    #: feste Identität (Fingerprint), wenn Angaben nachträglich geändert wurden - z. B. Airline
    #: und Umstiege aus der Google-Gegenprüfung -, damit der Deal derselbe bleibt
    ident: str | None = field(default=None, repr=False, compare=False)
    # -- optionale Zusatzangaben (Runde 2); alle mit Default, ändern weder Vergleich noch
    # Fingerprint. Gespeichert/ausgeliefert werden nur gesetzte Werte (siehe ``extras``).
    #: Preisniveau der Google-Seite: {"lowest", "typical", "typical_low", "typical_high", …}
    insight: dict | None = field(default=None, repr=False, compare=False)
    #: Abflug ab Nachbar-Hub (IATA), z. B. "BUD"; ``surcharge`` = angenommener Zubringer (€)
    via_hub: str | None = field(default=None, compare=False)
    surcharge: float | None = field(default=None, compare=False)
    #: Ende des Buchungszeitraums laut Quelle (aware oder naiv = UTC)
    expires: datetime | None = field(default=None, compare=False)
    #: Marker der Quelle wie "ERROR FARE", "CRAZY HOT", "PRICE DROP"
    hot: str | None = field(default=None, compare=False)
    #: stabile ID der Quelle (Bluesky ``post.uri``, Travel-Dealz ``id:modified``)
    uid: str | None = field(default=None, repr=False, compare=False)
    #: Hin/Rück aus zwei One-ways (Google-Rotation): je Teilstrecke ``{origin, destination,
    #: departure, price, airline, stops, link, seen}`` - JSON-tauglich
    legs: list | None = field(default=None, repr=False, compare=False)

    @property
    def nights(self) -> int | None:
        if self.departure and self.return_date:
            return (self.return_date - self.departure).days
        return None

    def extras(self) -> dict:
        """Gesetzte Zusatzangaben als JSON-taugliches Dict (für Store-Payload und API).
        Fehlende Werte fehlen auch im Dict - alte Daten ohne diese Felder bleiben gleich."""
        out: dict = {}
        for name in EXTRA_FIELDS:
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, datetime):
                value = value.isoformat(timespec="minutes")
            out[name] = value
        return out

    @property
    def route_key(self) -> str:
        return f"{self.origin}-{self.destination}-{'RT' if self.roundtrip else 'OW'}-{self.cabin}"

    @property
    def fingerprint(self) -> str:
        """Identifiziert "denselben" Deal über mehrere Läufe hinweg."""
        if self.ident:
            return self.ident
        if self.title:  # Feed-Artikel: Überschrift + Link reichen
            raw = f"{self.source}|{self.link}|{self.title}"
        else:
            raw = "|".join(
                str(x) for x in (
                    self.source, self.origin, self.destination, self.roundtrip,
                    self.cabin, self.departure, self.return_date, self.airline,
                )
            )
        return hashlib.sha1(raw.encode()).hexdigest()[:16]


@dataclass
class Deal:
    """Ein Angebot, das die Deal-Prinzipien erfüllt."""

    alert: Alert
    offer: Offer
    rating: str                   # "Top-Deal" | "Sehr gut" | "Deal"
    reference_price: float | None
    discount: float | None        # 0.35 = 35 % unter Referenz
    reasons: list[str]
    headline: str
    #: Herkunft des Referenzpreises (Median der Beobachtungen / Normalpreis) - für ``reprice``
    reference_source: str | None = None

    @property
    def fingerprint(self) -> str:
        return f"{self.alert.id}:{self.offer.fingerprint}"
