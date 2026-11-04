"""Laden und Erweitern der Alarm-Konfiguration (TOML, nur Standardbibliothek)."""

from __future__ import annotations

import re
import tomllib
import unicodedata
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path

from .models import Alert


@dataclass
class Settings:
    currency: str = "eur"
    scan_months: int = 6              # wie viele Abflugmonate abgesucht werden
    history_days: int = 60            # Zeitraum für den Referenzpreis
    min_history_samples: int = 8      # ab so vielen Beobachtungen gilt der Median
    renotify_drop: float = 0.05       # erneut melden, wenn ≥5 % billiger
    max_deals_per_alert: int = 5      # pro Lauf und Alarm höchstens so viele Meldungen
    request_delay: float = 0.35       # Pause zwischen API-Anfragen (Sekunden)
    database: str = "data/deals.sqlite"
    report: str = "data/report.html"
    feeds: list[str] = field(default_factory=lambda: ["https://travel-dealz.de/feed/"])
    feed_max_age_days: int = 14


@dataclass
class Config:
    settings: Settings
    alerts: list[Alert]
    path: Path


def _pick(cls, data: dict, where: str) -> dict:
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"{where}: unbekannte Schlüssel {sorted(unknown)}")
    return data


def _settings(raw: dict) -> Settings:
    """``[settings]`` prüfen: Settings-Felder plus alle App-Einstellungen (interval_hours, ...)."""
    if not isinstance(raw, dict):
        raise ValueError("[settings] muss eine Tabelle sein")
    known = {f.name for f in fields(Settings)}
    extra = {k: v for k, v in raw.items() if k not in known}
    if extra:
        from .appsettings import VALUES, coerce   # spät importieren (Zirkelbezug)
        unknown = sorted(k for k in extra if k not in VALUES)
        if unknown:
            raise ValueError(f"[settings]: unbekannte Schlüssel {unknown}")
        for k, v in extra.items():
            try:
                coerce(k, v)
            except ValueError as e:
                raise ValueError(f"[settings] {k}: {e}") from None
    return Settings(**{k: v for k, v in raw.items() if k in known})


def load(path: str | Path) -> Config:
    path = Path(path)
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    settings = _settings(raw.get("settings", {}))
    alerts = []
    seen = set()
    for i, a in enumerate(raw.get("alert", [])):
        alert = Alert(**_pick(Alert, a, f"[[alert]] #{i + 1}"))
        if alert.id in seen:
            raise ValueError(f"Alarm-ID '{alert.id}' ist doppelt")
        seen.add(alert.id)
        alerts.append(alert)
    # relative Pfade relativ zur Konfigurationsdatei auflösen
    base = path.resolve().parent.parent if path.parent.name == "config" else path.resolve().parent
    for attr in ("database", "report"):
        p = Path(getattr(settings, attr))
        if not p.is_absolute():
            setattr(settings, attr, str(base / p))
    return Config(settings=settings, alerts=alerts, path=path)


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(type(v))


def append_alert(path: str | Path, data: dict) -> None:
    """Hängt einen neuen [[alert]]-Block an (vorhandene Kommentare bleiben erhalten)."""
    Alert(**data)  # validieren, bevor geschrieben wird
    existing = load(path)
    if any(a.id == data["id"] for a in existing.alerts):
        raise ValueError(f"Alarm-ID '{data['id']}' existiert bereits")
    lines = ["", "[[alert]]"]
    lines += [f"{k} = {_toml_value(v)}" for k, v in data.items() if v is not None]
    with Path(path).open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# -- Rohdaten für die Datenbank ---------------------------------------------

ALERT_DEFAULTS: dict = {
    f.name: (f.default_factory() if f.default_factory is not MISSING else f.default)
    for f in fields(Alert) if f.default is not MISSING or f.default_factory is not MISSING
}
#: Felder, die die API zusätzlich ausliefert - beim Speichern stillschweigend ignorieren
DERIVED_ALERT_FIELDS = {"origins_expanded", "destinations_expanded", "pairs",
                        "active_deals", "best_price"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
AIRLINE_RE = re.compile(r"^[A-Z0-9]{2}$")
#: verständliche Feldnamen für Fehlermeldungen
FIELD_LABELS = {
    "origins": "Von", "destinations": "Nach", "max_price": "Preisgrenze",
    "baseline_price": "Normalpreis", "min_discount": "Mindest-Rabatt",
    "max_stops": "Max. Umstiege", "max_duration_hours": "Max. Flugdauer",
    "min_nights": "Nächte von", "max_nights": "Nächte bis", "months_ahead": "Monate voraus",
    "exclude_airlines": "Ausgeschlossene Airlines", "feed_keywords": "Stichwörter",
}


def _label(field_name: str) -> str:
    return f"„{FIELD_LABELS[field_name]}“" if field_name in FIELD_LABELS else f"'{field_name}'"


def load_raw(path: str | Path) -> tuple[dict, list[dict]]:
    """TOML lesen, ohne Regionen aufzulösen: ``(settings, [alert-rohdaten])``.

    Wird vorher vollständig validiert (wie :func:`load`).
    """
    load(path)
    with Path(path).open("rb") as fh:
        raw = tomllib.load(fh)
    return dict(raw.get("settings", {})), [normalize_alert(a) for a in raw.get("alert", [])]


def slugify(text: str) -> str:
    text = text.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("→", "-"), ("⇄", "-")):
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:48].strip("-") or "alarm"


def _codes(value, field_name: str) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[\s,;]+", value)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{_label(field_name)} muss eine Liste von Codes sein")
    out = [v.strip().upper() for v in value if v.strip()]
    return out


def _de(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _number(value, field_name: str, *, integer: bool = False, optional: bool = True,
            minimum: float | None = None, maximum: float | None = None):
    label = _label(field_name)
    if value is None or (isinstance(value, str) and not value.strip()):
        if optional:
            return None
        raise ValueError(f"{label} fehlt")
    if isinstance(value, bool):
        raise ValueError(f"{label} muss eine Zahl sein")
    if isinstance(value, str):
        try:
            value = float(value.strip().replace(",", "."))
        except ValueError:
            raise ValueError(f"{label} muss eine Zahl sein") from None
    if not isinstance(value, (int, float)) or value != value:  # NaN
        raise ValueError(f"{label} muss eine Zahl sein")
    if integer:
        if float(value) != int(value):
            raise ValueError(f"{label} muss eine ganze Zahl sein")
        value = int(value)
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} muss mindestens {_de(minimum)} sein")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} darf höchstens {_de(maximum)} sein")
    return value


def _keywords(value) -> list[list[str]]:
    """Stichwort-Gruppen: [["Zürich","Hamburg"],["China"]].

    Auch erlaubt: "Zürich, Hamburg\\nChina" oder ["Zürich, Hamburg", "China"].
    """
    if value is None:
        return []
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        raise ValueError("'feed_keywords' muss eine Liste von Stichwort-Gruppen sein")
    groups = []
    for g in value:
        if isinstance(g, str):
            g = g.split(",")
        if not isinstance(g, list) or not all(isinstance(w, str) for w in g):
            raise ValueError("'feed_keywords' muss eine Liste von Stichwort-Gruppen sein")
        words = [w.strip() for w in g if w.strip()]
        if words:
            groups.append(words)
    return groups


def normalize_alert(data: dict, existing: dict | None = None) -> dict:
    """Prüft und vervollständigt Alarm-Rohfelder (z. B. aus API oder TOML).

    ``existing`` wird bei Teil-Updates zugrunde gelegt. Ergebnis enthält alle
    Alarm-Felder; Regionen (``@DE``) bleiben unaufgelöst. Wirft ``ValueError``
    mit deutscher Meldung.
    """
    if not isinstance(data, dict):
        raise ValueError("Alarm-Daten müssen ein JSON-Objekt sein")
    data = {k: v for k, v in data.items() if k not in DERIVED_ALERT_FIELDS}
    known = {f.name for f in fields(Alert)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unknown))}")
    out = dict(ALERT_DEFAULTS)
    if existing:
        out.update({k: v for k, v in existing.items() if k in known})
    out.update(data)

    if not isinstance(out.get("id"), str) or not ID_RE.match(out["id"]):
        raise ValueError("Die ID darf nur Buchstaben, Ziffern, '-' und '_' enthalten "
                         "(max. 64 Zeichen)")
    name = out.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Bitte einen Namen angeben")
    out["name"] = name.strip()[:200]
    for key, label in (("origins", "Abflughafen"), ("destinations", "Ziel")):
        out[key] = _codes(out.get(key) or [], key)
        if not out[key]:
            raise ValueError(f"Bitte mindestens einen {label} angeben")
    out["exclude_airlines"] = _codes(out.get("exclude_airlines") or [], "exclude_airlines")
    bad = [c for c in out["exclude_airlines"] if not AIRLINE_RE.match(c)]
    if bad:
        raise ValueError(f"„Ausgeschlossene Airlines“: bitte 2-stellige IATA-Codes wie FR, W6 "
                         f"angeben (nicht {', '.join(bad)})")
    out["feed_keywords"] = _keywords(out.get("feed_keywords"))
    out["max_price"] = _number(out["max_price"], "max_price", minimum=1)
    out["baseline_price"] = _number(out["baseline_price"], "baseline_price", minimum=1)
    out["min_discount"] = _number(out["min_discount"], "min_discount", optional=False)
    if not 0.01 <= out["min_discount"] <= 0.95:
        raise ValueError("„Mindest-Rabatt“ muss zwischen 1 und 95 % liegen")
    out["max_stops"] = _number(out["max_stops"], "max_stops", integer=True, optional=False,
                               minimum=0, maximum=3)
    out["max_duration_hours"] = _number(out["max_duration_hours"], "max_duration_hours",
                                        minimum=1)
    out["min_nights"] = _number(out["min_nights"], "min_nights", integer=True, minimum=0)
    out["max_nights"] = _number(out["max_nights"], "max_nights", integer=True, minimum=0)
    out["months_ahead"] = _number(out["months_ahead"], "months_ahead", integer=True,
                                  minimum=1, maximum=12)
    if (out["min_nights"] is not None and out["max_nights"] is not None
            and out["min_nights"] > out["max_nights"]):
        raise ValueError("Mindestens-Nächte dürfen nicht größer als Höchst-Nächte sein")
    if not isinstance(out["enabled"], bool):
        raise ValueError("'enabled' muss true oder false sein")
    for key in ("trip", "cabin"):
        if not isinstance(out[key], str):
            raise ValueError(f"'{key}' muss ein Text sein")
    out = {f.name: out[f.name] for f in fields(Alert)}  # feste Reihenfolge
    Alert(**out)  # restliche Prüfungen (Regionen, IATA-Codes, trip, cabin, Preise)
    return out
