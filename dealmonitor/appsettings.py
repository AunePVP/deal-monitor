"""Einstellungen der App und Laufzeit-Kontext (Datenverzeichnis, Quellen, Kanäle).

Auflösung jeder Einstellung: **Umgebungsvariable > Datenbank (``kv``) > Standard**.
Per Umgebungsvariable gesetzte Werte sind in der Oberfläche gesperrt.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path
from typing import Callable

from . import config as config_mod
from . import notify
from .config import Config, Settings
from .models import Alert
from .store import Store

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "alerts.toml"
DEFAULT_DATA = ROOT / "data"
SOURCE_NAMES = ("travelpayouts", "feed")

#: Geheimnisse -> Umgebungsvariable. Werden nie im Klartext ausgeliefert.
SECRETS: dict[str, str] = {
    "travelpayouts_token": "TRAVELPAYOUTS_TOKEN",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "webhook_token": "WEBHOOK_TOKEN",
}


@dataclass(frozen=True)
class Spec:
    kind: str                      # str | int | float | list
    default: object
    label: str
    minimum: float | None = None
    maximum: float | None = None
    url: bool = False              # str/list: nur http(s)-Adressen
    choices: tuple | None = None   # list: erlaubte Einträge
    required: bool = False         # str: darf nicht leer sein
    percent: bool = False          # float: Anteil, Fehlermeldungen in Prozent


_D = Settings()
VALUES: dict[str, Spec] = {
    "travelpayouts_marker": Spec("str", "", "Travelpayouts-Marker"),
    "webhook_url": Spec("str", "", "Webhook-URL", url=True),
    "ntfy_topic": Spec("str", "", "ntfy-Topic"),
    "ntfy_server": Spec("str", "https://ntfy.sh", "ntfy-Server", url=True, required=True),
    "telegram_chat_id": Spec("str", "", "Telegram-Chat-ID"),
    "interval_hours": Spec("float", 3.0, "Prüfintervall (Stunden)", 0.5, 168),
    "sources": Spec("list", list(SOURCE_NAMES), "Quellen", choices=SOURCE_NAMES),
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
    "feeds": Spec("list", list(_D.feeds), "Feeds", url=True),
    "feed_max_age_days": Spec("int", _D.feed_max_age_days, "Max. Alter von Feed-Artikeln (Tage)",
                              1, 365),
}
SETTINGS_FIELDS = [f.name for f in fields(Settings) if f.name not in ("database", "report")]
URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.I)


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
    if spec.kind in ("int", "float"):
        if isinstance(value, str):
            value = value.strip().replace(",", ".")
            try:
                value = float(value)
            except ValueError:
                raise ValueError(f"{label}: bitte eine Zahl angeben") from None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
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
        if not v or v in out:
            continue
        if spec.choices and v not in spec.choices:
            raise ValueError(f"{label}: unbekannter Eintrag '{v}' "
                             f"(erlaubt: {', '.join(spec.choices)})")
        if spec.url and not URL_RE.match(v):
            raise ValueError(f"{label}: '{v}' ist keine http(s)-Adresse")
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
        return {"values": self.values(), "secrets": secrets, "env_locked": self.env_locked()}

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
        src = self.get("sources")
        return {
            "travelpayouts": demo or bool(self.get("travelpayouts_token")),
            "feed": "feed" in src and bool(self.get("feeds")),
            "webhook": bool(self.get("webhook_url")),
            "ntfy": bool(self.get("ntfy_topic")),
            "telegram": bool(self.get("telegram_bot_token") and self.get("telegram_chat_id")),
        }


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


# -- Laufzeit-Kontext -------------------------------------------------------------

SourceFactory = Callable[[Config, list[str]], list]


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
            return ensure_seeded(store, self.config_path)

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
                app: AppSettings | None = None) -> list:
        app = app or self.settings(store)
        names = list(names) if names is not None else app.get("sources")
        for n in names:
            if n not in SOURCE_NAMES:
                raise ValueError(f"Unbekannte Quelle '{n}' (travelpayouts, feed)")
        if self.source_factory:
            return self.source_factory(cfg, names)
        from .sources.feed import FeedSource
        from .sources.travelpayouts import TravelpayoutsSource
        s = cfg.settings
        out = []
        for n in names:
            if n == "travelpayouts":
                out.append(TravelpayoutsSource(
                    token=app.get("travelpayouts_token"), currency=s.currency,
                    scan_months=s.scan_months, request_delay=s.request_delay,
                    marker=app.get("travelpayouts_marker") or None, today=self.today))
            elif s.feeds:  # leere Liste hieße sonst "Standard-Feed"
                out.append(FeedSource(s.feeds, max_age_days=s.feed_max_age_days))
        return out
