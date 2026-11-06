"""Einstellungen der App und Laufzeit-Kontext (Datenverzeichnis, Quellen, Kanäle).

Auflösung jeder Einstellung: **Umgebungsvariable > Datenbank (``kv``) > Standard**.
Per Umgebungsvariable gesetzte Werte sind in der Oberfläche gesperrt.
"""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from . import config as config_mod
from . import http, hubs, notify, radar
from .config import Config, Settings
from .models import Alert
from .sources.feed import FEED_PRESETS, default_preset_ids
from .store import Store

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "alerts.toml"
DEFAULT_DATA = ROOT / "data"
#: Reihenfolge = Reihenfolge im Lauf: gecachte Quellen zuerst (ihre Deals werden bei Google
#: gegengeprüft, solange das Google-Budget reicht), dann Google selbst, zuletzt die Deal-Seiten
SOURCE_NAMES = ("travelpayouts", "amadeus", "google_flights", "feeds")
SOURCE_ALIASES = {"feed": "feeds"}            # alter Name aus früheren Versionen
DEFAULT_SOURCES = ["travelpayouts", "google_flights", "feeds"]
#: Anteil des Google-Budgets, den der Scan für die Gegenprüfung übrig lässt (1/4)
VERIFY_RESERVE_DIVISOR = 4
SOURCE_LABELS = {"travelpayouts": "Travelpayouts (Preis-API)", "google_flights": "Google Flights",
                 "amadeus": "Amadeus (abgeschaltet seit 17.07.2026)", "feeds": "Deal-Seiten (Feeds)"}

#: Geheimnisse -> Umgebungsvariable. Werden nie im Klartext ausgeliefert.
SECRETS: dict[str, str] = {
    "travelpayouts_token": "TRAVELPAYOUTS_TOKEN",
    "amadeus_api_key": "AMADEUS_API_KEY",
    "amadeus_api_secret": "AMADEUS_API_SECRET",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "webhook_token": "WEBHOOK_TOKEN",
}


@dataclass(frozen=True)
class Spec:
    kind: str                      # str | int | float | bool | list
    default: object
    label: str
    minimum: float | None = None
    maximum: float | None = None
    url: bool = False              # str/list: nur http(s)-Adressen
    choices: tuple | None = None   # str/list: erlaubte Einträge
    required: bool = False         # str: darf nicht leer sein
    percent: bool = False          # float: Anteil, Fehlermeldungen in Prozent


_D = Settings()
_R = radar.DEFAULTS
_H = hubs.DEFAULTS
VALUES: dict[str, Spec] = {
    "travelpayouts_marker": Spec("str", "", "Travelpayouts-Marker"),
    "webhook_url": Spec("str", "", "Webhook-URL", url=True),
    "ntfy_topic": Spec("str", "", "ntfy-Topic"),
    "ntfy_server": Spec("str", "https://ntfy.sh", "ntfy-Server", url=True, required=True),
    "telegram_chat_id": Spec("str", "", "Telegram-Chat-ID"),
    "interval_hours": Spec("float", 3.0, "Prüfintervall (Stunden)", 0.5, 168),
    "feed_interval_minutes": Spec("int", 30, "Feed-Intervall (Minuten)", 10, 1440),
    # schnelle Deal-Seiten (Preset.lane == "fast", z. B. Secret Flying): leicht, kurzlebige Deals
    "fast_interval_minutes": Spec("int", 10, "Schnell-Intervall (Minuten)", 5, 1440),
    "sources": Spec("list", list(DEFAULT_SOURCES), "Quellen", choices=SOURCE_NAMES),
    "currency": Spec("str", _D.currency, "Währung", required=True),
    "scan_months": Spec("int", _D.scan_months, "Monate voraus", 1, 12),
    "history_days": Spec("int", _D.history_days, "Preis-Historie (Tage)", 1, 3650),
    "min_history_samples": Spec("int", _D.min_history_samples, "Mindestanzahl Preisbeobachtungen",
                                1, 1000),
    "renotify_drop": Spec("float", _D.renotify_drop, "Erneut melden ab Preisrückgang", 0, 0.9,
                          percent=True),
    "max_deals_per_alert": Spec("int", _D.max_deals_per_alert, "Meldungen pro Alarm und Lauf",
                                1, 100),
    "request_delay": Spec("float", _D.request_delay, "Pause zwischen Anfragen (Sekunden)", 0, 10),
    # Deal-Seiten (Feeds)
    "feed_presets": Spec("list", default_preset_ids(), "Deal-Seiten",
                         choices=tuple(p.id for p in FEED_PRESETS)),
    "feeds": Spec("list", list(_D.feeds), "Eigene Feeds", url=True),
    "feed_max_age_days": Spec("int", _D.feed_max_age_days, "Max. Alter von Feed-Artikeln ohne Ablaufdatum "
                              "(Tage) – YHBU/premium-flights mind. 45/30",
                              1, 365),
    # Google Flights (inoffiziell)
    "google_max_requests": Spec("int", 40, "Google Flights: Abfragen pro Lauf", 2, 500),
    "google_verify": Spec("bool", True, "Google Flights: Deals gegenprüfen"),
    # Amadeus Self-Service
    "amadeus_env": Spec("str", "test", "Amadeus-Umgebung", choices=("test", "production"),
                        required=True),
    "amadeus_max_requests": Spec("int", 100, "Amadeus: Abfragen pro Lauf", 1, 2000),
    # Radar
    "radar_enabled": Spec("bool", _R["radar_enabled"], radar.LABELS["radar_enabled"]),
    "radar_economy_max": Spec("float", _R["radar_economy_max"], radar.LABELS["radar_economy_max"],
                              0, 100000),
    "radar_economy_min": Spec("float", _R["radar_economy_min"], radar.LABELS["radar_economy_min"],
                              0, 100000),
    "radar_premium_max": Spec("float", _R["radar_premium_max"], radar.LABELS["radar_premium_max"],
                              0, 100000),
    "radar_business_max": Spec("float", _R["radar_business_max"],
                               radar.LABELS["radar_business_max"], 0, 100000),
    "radar_first_max": Spec("float", _R["radar_first_max"], radar.LABELS["radar_first_max"],
                            0, 100000),
    "radar_home_only": Spec("bool", _R["radar_home_only"], radar.LABELS["radar_home_only"]),
    "radar_long_haul_only": Spec("bool", _R["radar_long_haul_only"],
                                 radar.LABELS["radar_long_haul_only"]),
    # Nachbar-Hubs (Abflug Amsterdam, Budapest, Rom … mit Zubringer-Aufschlag)
    "hubs_enabled": Spec("bool", _H["hubs_enabled"], hubs.LABELS["hubs_enabled"]),
    "hub_surcharge_a": Spec("float", _H["hub_surcharge_a"], hubs.LABELS["hub_surcharge_a"],
                            0, 5000),
    "hub_surcharge_b": Spec("float", _H["hub_surcharge_b"], hubs.LABELS["hub_surcharge_b"],
                            0, 5000),
}
SETTINGS_FIELDS = [f.name for f in fields(Settings) if f.name not in ("database", "report")]
URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.I)
#: Zahl mit Tausenderpunkten ("1.300", "12.500"); "0.35" oder "1.5" sind Dezimalzahlen
THOUSANDS_RE = re.compile(r"-?[1-9]\d{0,2}(?:\.\d{3})+")
TRUE_WORDS = {"1", "true", "ja", "yes", "on", "an", "wahr"}
FALSE_WORDS = {"0", "false", "nein", "no", "off", "aus", "falsch", ""}


def env_name(name: str) -> str:
    return SECRETS.get(name) or name.upper()


def _de(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def coerce(name: str, value):
    """Wert prüfen und in den richtigen Typ bringen. ``ValueError`` mit deutscher Meldung."""
    spec = VALUES.get(name)
    if spec is None:
        raise ValueError(f"Unbekannte Einstellung '{name}'")
    label = spec.label
    if spec.kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            v = value.strip().lower()
            if v in TRUE_WORDS:
                return True
            if v in FALSE_WORDS:
                return False
        raise ValueError(f"{label}: bitte an oder aus (true/false) angeben")
    if spec.kind in ("int", "float"):
        if isinstance(value, str):
            value = value.strip()
            if "," in value:                       # deutsch: "1.299,50" / "1,5"
                value = value.replace(".", "").replace(",", ".")
            elif THOUSANDS_RE.fullmatch(value):     # "1.300" = 1300 € (nicht 1,3)
                value = value.replace(".", "")
            try:
                value = float(value)
            except ValueError:
                raise ValueError(f"{label}: bitte eine Zahl angeben") from None
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value):          # NaN, ±Infinity
            raise ValueError(f"{label}: bitte eine Zahl angeben")
        if spec.kind == "int":
            if float(value) != int(value):
                raise ValueError(f"{label}: bitte eine ganze Zahl angeben")
            value = int(value)
        else:
            value = float(value)
        if spec.percent:   # intern Anteil (0.05), für Menschen Prozent (5 %)
            if spec.minimum is not None and value < spec.minimum:
                raise ValueError(f"{label}: mindestens {_de(spec.minimum * 100)} %")
            if spec.maximum is not None and value > spec.maximum:
                raise ValueError(f"{label}: höchstens {_de(spec.maximum * 100)} %")
            return value
        if spec.minimum is not None and value < spec.minimum:
            raise ValueError(f"{label}: mindestens {_de(spec.minimum)}")
        if spec.maximum is not None and value > spec.maximum:
            raise ValueError(f"{label}: höchstens {_de(spec.maximum)}")
        return value
    if spec.kind == "str":
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError(f"{label}: bitte einen Text angeben")
        value = value.strip()
        if spec.required and not value:
            raise ValueError(f"{label}: darf nicht leer sein")
        if value and spec.url and not URL_RE.match(value):
            raise ValueError(f"{label}: bitte eine Adresse mit http:// oder https:// angeben")
        if name == "currency":
            if not re.fullmatch(r"[A-Za-z]{3}", value):
                raise ValueError(f"{label}: dreistelliger Währungscode, z. B. eur")
            value = value.lower()
        if spec.choices:
            value = value.lower()
            if value not in spec.choices:
                raise ValueError(f"{label}: unbekannter Wert '{value}' "
                                 f"(erlaubt: {', '.join(spec.choices)})")
        return value
    # Liste
    if value is None:
        value = []
    if isinstance(value, str):
        value = re.split(r"[\s,;]+", value)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{label}: bitte eine Liste angeben")
    out = []
    for v in (v.strip() for v in value):
        if name == "sources":
            v = SOURCE_ALIASES.get(v, v)
        if not v or v in out:
            continue
        if spec.choices and v not in spec.choices:
            raise ValueError(f"{label}: unbekannter Eintrag '{v}' "
                             f"(erlaubt: {', '.join(spec.choices)})")
        if spec.url and not URL_RE.match(v):
            raise ValueError(f"{label}: '{v}' ist keine http(s)-Adresse")
        if name == "feeds":
            # Eigene Feeds lädt der Server selbst - nicht ins eigene Netz (localhost, NAS, Router)
            try:
                reason = http.internal_host(urlsplit(v).hostname)
            except ValueError:
                reason = "ungültige Adresse"
            if reason:
                raise ValueError(f"{label}: '{v}' ist nicht erlaubt ({reason}) - nur öffentliche "
                                 "Feeds aus dem Internet")
        out.append(v)
    return out


def hint(secret: str | None) -> str | None:
    if not secret:
        return None
    return "…" + secret[-4:] if len(secret) >= 8 else "…"


class AppSettings:
    """Lesen/Schreiben der Einstellungen (eine Instanz pro Store/Thread)."""

    def __init__(self, store: Store, env: dict | None = None) -> None:
        self.store = store
        self.env = os.environ if env is None else env
        self._db = store.kv_all()

    # -- Umgebungsvariablen -------------------------------------------------
    def env_value(self, name: str):
        raw = self.env.get(env_name(name))
        if raw is None or raw == "":
            return None
        if name in SECRETS:
            return raw.strip()
        try:
            return coerce(name, raw)
        except ValueError as e:
            log.warning("Umgebungsvariable %s ignoriert: %s", env_name(name), e)
            return None

    def env_locked(self) -> list[str]:
        locked = [n for n in [*VALUES, *SECRETS] if self.env_value(n) is not None]
        # Ist der Webhook-Token per Env gesetzt, darf die URL nicht umgebogen werden
        # (sonst ginge der Token an einen fremden Server)
        if "webhook_token" in locked and "webhook_url" not in locked:
            locked.append("webhook_url")
        return locked

    # -- Werte ------------------------------------------------------------------
    def get(self, name: str):
        env = self.env_value(name)
        if env is not None:
            return env
        if name in SECRETS:
            return self._db.get(name) or None
        if name in self._db:
            try:
                return coerce(name, self._db[name])
            except ValueError as e:
                log.warning("Gespeicherte Einstellung %s ungültig: %s", name, e)
        d = VALUES[name].default
        return list(d) if isinstance(d, list) else d

    def values(self) -> dict:
        return {n: self.get(n) for n in VALUES}

    def public(self) -> dict:
        """Antwort für ``GET /api/settings`` - Geheimnisse nur maskiert."""
        secrets = {}
        for n in SECRETS:
            v = self.get(n)
            secrets[n] = {"set": bool(v), "hint": hint(v)}
        return {"values": self.values(), "secrets": secrets, "env_locked": self.env_locked(),
                "presets": [{"id": p.id, "name": p.name, "homepage": p.homepage,
                             "description": p.description, "language": p.language,
                             "default": p.enabled, "search": p.search} for p in FEED_PRESETS],
                "source_names": list(SOURCE_NAMES),
                "env_names": {n: env_name(n) for n in [*VALUES, *SECRETS]}}

    def update(self, values: dict | None = None, secrets: dict | None = None) -> None:
        """Teil-Update. Erst alles prüfen, dann speichern. Gesperrte Felder werden ignoriert."""
        if values is not None and not isinstance(values, dict):
            raise ValueError("'values' muss ein Objekt sein")
        if secrets is not None and not isinstance(secrets, dict):
            raise ValueError("'secrets' muss ein Objekt sein")
        locked = set(self.env_locked())
        new_values = {}
        for name, value in (values or {}).items():
            v = coerce(name, value)
            if name not in locked:
                new_values[name] = v
        new_secrets = {}
        for name, value in (secrets or {}).items():
            if name not in SECRETS:
                raise ValueError(f"Unbekanntes Geheimnis '{name}'")
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"'{name}' muss ein Text sein")
            if name not in locked:
                new_secrets[name] = value.strip()
        # Webhook-URL geändert, gespeicherter Token nicht neu eingegeben? -> ablehnen,
        # damit niemand den Token per URL-Änderung an einen fremden Server schicken kann
        if ("webhook_url" in new_values and new_values["webhook_url"] != self.get("webhook_url")
                and self.get("webhook_token") and "webhook_token" not in new_secrets):
            raise ValueError("Beim Ändern der Webhook-URL bitte den Webhook-Token neu eingeben "
                             "(oder den gespeicherten Token löschen)")
        for name, v in new_values.items():
            self.store.kv_set(name, v)
        for name, v in new_secrets.items():
            if v:
                self.store.kv_set(name, v)
            else:
                self.store.kv_delete(name)
        self._db = self.store.kv_all()

    def configured(self, demo: bool = False) -> dict:
        """Welche Quellen/Kanäle alles haben, was sie brauchen (Token, Key, Feed-Auswahl).

        Ob eine Quelle *eingeschaltet* ist, steht in ``sources``; die Oberfläche zeigt beides.
        """
        feeds_ready = bool(self.get("feed_presets") or self.get("feeds"))
        return {
            "travelpayouts": demo or bool(self.get("travelpayouts_token")),
            "google_flights": True,       # braucht keinen Key (inoffiziell, kann blockiert sein)
            "amadeus": bool(self.get("amadeus_api_key") and self.get("amadeus_api_secret")),
            "feeds": feeds_ready,
            "feed": feeds_ready,          # alter Name (Alias)
            # Radar braucht aktive Deal-Seiten (es prüft deren Artikel)
            "radar": feeds_ready and bool(self.get("radar_enabled"))
                     and "feeds" in (self.get("sources") or []),
            "webhook": bool(self.get("webhook_url")),
            "ntfy": bool(self.get("ntfy_topic")),
            "telegram": bool(self.get("telegram_bot_token") and self.get("telegram_chat_id")),
        }

    def radar_settings(self) -> radar.RadarSettings:
        return radar.RadarSettings.from_values({n: self.get(n) for n in (*radar.DEFAULTS,
                                                                         *hubs.DEFAULTS)})

    def hub_settings(self) -> hubs.HubSettings:
        return hubs.HubSettings.from_values({n: self.get(n) for n in hubs.DEFAULTS})


# -- Seeding aus der TOML-Datei ------------------------------------------------

def import_config(store: Store, path: str | Path, *, replace: bool = False) -> tuple[int, int]:
    """Alarme und [settings] aus der TOML importieren. Rückgabe: (neu, überschrieben).

    Ohne ``replace`` werden nur neue Alarm-IDs und noch nicht gesetzte
    Einstellungen übernommen; mit ``replace`` werden gleiche IDs und alle
    Einstellungen aus der Datei überschrieben.
    """
    settings_raw, alerts = config_mod.load_raw(path)
    existing = store.kv_all()
    for name, value in settings_raw.items():
        if name not in VALUES:
            continue  # database/report ergeben sich aus dem Datenverzeichnis
        if replace or name not in existing:
            store.kv_set(name, coerce(name, value))
    added = updated = 0
    for raw in alerts:
        if store.has_alert(raw["id"]):
            if replace:
                store.update_alert(raw)
                updated += 1
        else:
            store.insert_alert(raw)
            added += 1
    return added, updated


def ensure_seeded(store: Store, path: str | Path) -> bool:
    """Beim allerersten Start Alarme + Einstellungen aus der TOML übernehmen."""
    if store.kv_get("seeded"):
        return False
    if not Path(path).exists():
        log.warning("Start-Konfiguration %s nicht gefunden - starte ohne Alarme", path)
        return False
    import_config(store, path)
    store.kv_set("seeded", True)
    return True


#: Stand der gespeicherten Einstellungen; ältere Datenbanken werden einmal angepasst
SETTINGS_VERSION = 4
#: Standard-Alarme, die mit Version 4 die Google-Tagesrotation bekommen (``rotation.py``)
ROTATION_ALERTS_V4 = ("zrh-ham-oneway",)
#: Deal-Seiten, die mit Version 3 neu und standardmäßig an sind: wer seine Auswahl schon
#: gespeichert hat, bekommt sie einmal dazu (bestehende Presets bleiben, wie gewählt)
NEW_PRESETS_V3 = ("travel-dealz-rest", "secretflying", "yhbu")


def migrate_settings(store: Store) -> list[str]:
    """Gespeicherte Einstellungen älterer Versionen einmalig anpassen. Rückgabe: Änderungen.

    Version 1 (bis 0.1, nur Travelpayouts + Travel-Dealz-Feed): Die alte Oberfläche speicherte
    ``sources = ["travelpayouts", "feed"]`` (= alles, was es gab) und die TOML
    ``feeds = ["https://travel-dealz.de/feed/"]``. Ohne Anpassung bliebe Google Flights aus
    (keine Gegenprüfung) und der Travel-Dealz-Hauptfeed würde neben dem Preset doppelt geladen.

    Version 2: gespeicherte ``feed_presets`` kennen die neuen Deal-Seiten :data:`NEW_PRESETS_V3`
    noch nicht -> einmal ergänzen.

    Version 3: die Standard-Alarme :data:`ROTATION_ALERTS_V4` kennen ``rotation`` noch nicht ->
    einmal einschalten (nur, wenn das Feld noch fehlt).
    """
    from .sources.feed import PRESETS_BY_ID, normalize_link
    version = store.kv_get("settings_version") or 1
    if version >= SETTINGS_VERSION:
        return []
    changes = []
    saved = store.kv_get("feed_presets")
    if isinstance(saved, list):
        added = [p for p in NEW_PRESETS_V3 if p in PRESETS_BY_ID and p not in saved]
        if added:
            store.kv_set("feed_presets", saved + added)
            changes.append("feed_presets")
    raw = store.kv_get("sources")
    if isinstance(raw, list) and {SOURCE_ALIASES.get(x, x) for x in raw} == {"travelpayouts", "feeds"}:
        store.kv_set("sources", list(DEFAULT_SOURCES))
        changes.append("sources")
    feeds = store.kv_get("feeds")
    if isinstance(feeds, list) and feeds:
        presets = store.kv_get("feed_presets")
        if not isinstance(presets, list):
            presets = default_preset_ids()
        known = {normalize_link(u) for pid in presets if pid in PRESETS_BY_ID
                 for u in PRESETS_BY_ID[pid].urls}
        kept = [u for u in feeds if not (isinstance(u, str) and normalize_link(u) in known)]
        if kept != feeds:
            store.kv_set("feeds", kept)
            changes.append("feeds")
    for alert_id in ROTATION_ALERTS_V4:
        raw = store.get_alert(alert_id)
        if isinstance(raw, dict) and "rotation" not in raw:
            store.update_alert({**raw, "rotation": True})
            changes.append(f"rotation:{alert_id}")
    store.kv_set("settings_version", SETTINGS_VERSION)
    if changes:
        log.info("Einstellungen aus älterer Version übernommen: %s", ", ".join(changes))
    return changes


# -- Laufzeit-Kontext -------------------------------------------------------------

SourceFactory = Callable[[Config, list[str]], list]


def order_sources(names: list[str]) -> list[str]:
    """Quellen in Lauf-Reihenfolge (``SOURCE_NAMES``), Aliasse aufgelöst, Doppelte entfernt."""
    wanted = []
    for n in names:
        n = SOURCE_ALIASES.get(n, n)
        if n not in SOURCE_NAMES:
            raise ValueError(f"Unbekannte Quelle '{n}' ({', '.join(SOURCE_NAMES)})")
        if n not in wanted:
            wanted.append(n)
    return [n for n in SOURCE_NAMES if n in wanted]


class Context:
    """Alles, was ein Lauf/Request braucht: Pfade, Umgebung, Quellen-Fabrik."""

    def __init__(self, data_dir: str | Path | None = None, config_path: str | Path | None = None,
                 *, demo: bool = False, env: dict | None = None,
                 source_factory: SourceFactory | None = None, today: date | None = None) -> None:
        self.env = os.environ if env is None else env
        self.data_dir = Path(data_dir or self.env.get("DEALMONITOR_DATA") or DEFAULT_DATA)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:   # Datenbank enthält Tokens im Klartext: nur für den eigenen Benutzer
            os.chmod(self.data_dir, 0o700)
        except OSError:
            pass
        self.db_path = self.data_dir / "deals.sqlite"
        self.report_path = self.data_dir / "report.html"
        self.config_path = Path(config_path or DEFAULT_CONFIG)
        self.demo = demo
        self.source_factory = source_factory
        self.today = today

    @property
    def password(self) -> str | None:
        return self.env.get("DEALMONITOR_PASSWORD") or None

    @property
    def allowed_hosts(self) -> set[str]:
        """Zusätzlich erlaubte Hostnamen (``DEALMONITOR_ALLOWED_HOSTS``, kommagetrennt)."""
        raw = self.env.get("DEALMONITOR_ALLOWED_HOSTS") or ""
        return {h.strip().lower().strip("[]") for h in raw.split(",") if h.strip()}

    def store(self) -> Store:
        return Store(self.db_path)

    def settings(self, store: Store) -> AppSettings:
        return AppSettings(store, self.env)

    def seed(self) -> bool:
        with self.store() as store:
            seeded = ensure_seeded(store, self.config_path)
            migrate_settings(store)
            return seeded

    def build_config(self, store: Store, app: AppSettings | None = None) -> Config:
        app = app or self.settings(store)
        settings = Settings(**{n: app.get(n) for n in SETTINGS_FIELDS},
                            database=str(self.db_path), report=str(self.report_path))
        alerts = []
        for raw in store.alert_rows():
            try:
                alerts.append(Alert(**raw))
            except (TypeError, ValueError) as e:
                log.error("Alarm %s ungültig, übersprungen: %s", raw.get("id"), e)
        return Config(settings=settings, alerts=alerts, path=self.config_path)

    def notifiers(self, store: Store, app: AppSettings | None = None) -> list:
        app = app or self.settings(store)
        out: list = [notify.Inbox(self.db_path), notify.Console()]
        if app.get("telegram_bot_token") and app.get("telegram_chat_id"):
            out.append(notify.Telegram(app.get("telegram_bot_token"), app.get("telegram_chat_id")))
        if app.get("ntfy_topic"):
            out.append(notify.Ntfy(app.get("ntfy_topic"), app.get("ntfy_server")))
        if app.get("webhook_url"):
            out.append(notify.Webhook(app.get("webhook_url"), app.get("webhook_token")))
        return out

    def sources(self, cfg: Config, store: Store, names: list[str] | None = None,
                app: AppSettings | None = None, *, lanes: set[str] | None = None,
                google_max: int | None = None) -> list:
        """Quellen für einen Lauf bauen (frische Objekte: Zähler/Schutzschalter pro Lauf).

        ``lanes``: nur Deal-Seiten dieser Takte (``{"fast"}``); ``google_max``: Google-Budget
        für diesen Lauf höchstens so groß (Sofort-Prüfung)."""
        app = app or self.settings(store)
        names = order_sources(list(names) if names is not None else app.get("sources"))
        if self.source_factory:
            return self.source_factory(cfg, names)
        from .sources import amadeus as amadeus_mod
        from .sources.feed import FeedSource
        from .sources.googleflights import GoogleFlightsSource
        from .sources.travelpayouts import TravelpayoutsSource
        s = cfg.settings
        out = []
        for n in names:
            if n == "travelpayouts":
                out.append(TravelpayoutsSource(
                    token=app.get("travelpayouts_token"), currency=s.currency,
                    scan_months=s.scan_months, request_delay=s.request_delay,
                    marker=app.get("travelpayouts_marker") or None, today=self.today))
            elif n == "amadeus":
                out += amadeus_mod.build_sources(
                    app.get("amadeus_api_key"), app.get("amadeus_api_secret"),
                    env=app.get("amadeus_env"), max_requests=app.get("amadeus_max_requests"),
                    scan_months=s.scan_months, request_delay=s.request_delay, today=self.today)
            elif n == "google_flights":
                max_requests = app.get("google_max_requests")
                if google_max is not None:
                    max_requests = min(max_requests, google_max)
                # Ein Teil des Budgets bleibt der Gegenprüfung gecachter Preise vorbehalten
                verifies = app.get("google_verify") and bool({"travelpayouts", "amadeus"} & set(names))
                out.append(GoogleFlightsSource(
                    max_requests=max_requests, delay=3.0, scan_months=s.scan_months,
                    today=self.today,
                    verify_reserve=max_requests // VERIFY_RESERVE_DIVISOR if verifies else 0,
                    state_store=store, period_hours=app.get("interval_hours")))
            elif n == "feeds":
                presets = app.get("feed_presets")
                if presets or s.feeds:   # ohne Deal-Seiten und eigene Feeds gibt es nichts zu lesen
                    feed = FeedSource(presets=presets, extra_feeds=s.feeds,
                                      max_age_days=s.feed_max_age_days, state_store=store,
                                      lanes=lanes)
                    feed.hub_settings = app.hub_settings()
                    out.append(feed)
        return out

    def verifier(self, sources: list, app: AppSettings):
        """Die Google-Quelle des Laufs, wenn Deals gecachter Quellen gegengeprüft werden sollen."""
        if not app.get("google_verify"):
            return None
        return next((s for s in sources if getattr(s, "name", "") == "google_flights"
                     and hasattr(s, "verify")), None)
