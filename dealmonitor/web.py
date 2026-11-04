"""Web-Oberfläche + JSON-API (nur Standardbibliothek: ``http.server``).

Start: ``python -m dealmonitor serve`` -> http://localhost:8080
"""

from __future__ import annotations

import base64
import binascii
import hmac
import ipaddress
import json
import logging
import re
import signal
import sqlite3
import sys
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__, notify, regions
from .appsettings import Context
from .config import normalize_alert, slugify
from .models import Alert
from .report import _gf_link, _merge_articles
from .scheduler import Scheduler

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY = 1_000_000
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
}
REGION_NAMES = {
    "DE": "Deutschland", "DE_HUBS": "Deutschland (große Flughäfen)", "CH": "Schweiz",
    "AT": "Österreich", "DACH": "Deutschland, Österreich, Schweiz", "CN": "China",
    "CN_MAJOR": "China (große Städte)", "CN_HK": "China inkl. Hongkong & Macau",
}
RATING_RANK = {"Top-Deal": 0, "Sehr gut": 1, "Deal": 2}
PLACEHOLDER = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Deal-Monitor</title>
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px}</style>
</head><body><h1>✈️ Deal-Monitor</h1><p>Der Server läuft, aber die Oberfläche
(<code>dealmonitor/static/index.html</code>) fehlt. Die API ist unter
<a href="/api/status">/api/status</a> erreichbar.</p></body></html>"""


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# -- JSON-Formen ----------------------------------------------------------------

def alert_json(raw: dict, stats: dict | None = None) -> dict:
    out = dict(raw)
    try:
        a = Alert(**raw)
        out["origins_expanded"], out["destinations_expanded"] = a.origins, a.destinations
        out["pairs"] = len(a.pairs)
    except (TypeError, ValueError):
        out["origins_expanded"], out["destinations_expanded"], out["pairs"] = [], [], 0
    st = (stats or {}).get(raw["id"], {})
    out["active_deals"] = st.get("active_deals", 0)
    out["best_price"] = st.get("best_price")
    return out


def deal_list(store, status: str = "active", alert_id: str | None = None) -> list[dict]:
    alerts = store.alert_rows()
    names = {a["id"]: a["name"] for a in alerts}
    position = {a["id"]: i for i, a in enumerate(alerts)}
    statuses = ["active", "expired"] if status == "all" else [status]
    out = []
    for st in statuses:
        rows = store.deals(status=st, alert_id=alert_id, limit=1000)
        rows = [d for d in rows if d["alert_id"] in names]   # Reste gelöschter Alarme ausblenden
        rows.sort(key=lambda d: (d["price"], position[d["alert_id"]]))
        for d in rows:
            d["alert_name"] = names[d["alert_id"]]
            d["compare"] = _gf_link(d)
            d["alert_ids"] = [d["alert_id"]]
        if not alert_id:
            merged = _merge_articles(rows)
            by_link = {}
            for d in rows:
                if d["source"].endswith("-feed") and d.get("link"):
                    by_link.setdefault(d["link"], []).append(d["alert_id"])
            for d in merged:
                if d["source"].endswith("-feed") and d.get("link"):
                    d["alert_ids"] = by_link[d["link"]]
            rows = merged
        if st == "active":
            rows.sort(key=lambda d: (RATING_RANK.get(d["rating"], 3), d["price"]))
        else:
            rows.sort(key=lambda d: d.get("expired_at") or "", reverse=True)
        out += rows
    return out


def _int_param(query: dict, name: str, default: int, lo: int = 1, hi: int = 1000) -> int:
    raw = query.get(name, [None])[0]
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(400, f"'{name}' muss eine ganze Zahl sein") from None
    return max(lo, min(hi, value))


# -- Request-Handler ----------------------------------------------------------------

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def host_allowed(host_header: str | None, extra: set[str]) -> bool:
    """Schutz gegen DNS-Rebinding: nur bekannte Hostnamen oder IP-Adressen zulassen."""
    if not host_header:
        return True            # HTTP/1.0 ohne Host - Browser senden immer einen
    host = host_header.strip().lower()
    if host.startswith("["):   # [::1]:8080
        host = host[1:].partition("]")[0]
    elif host.count(":") == 1:
        host = host.partition(":")[0]
    host = host.rstrip(".")
    if host in LOCAL_HOSTS or host.endswith(".localhost") or "*" in extra or host in extra:
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _origin_host(origin: str) -> str:
    try:
        return urlsplit(origin).netloc.lower()
    except ValueError:
        return ""


class Handler(BaseHTTPRequestHandler):
    server_version = "DealMonitor/" + __version__
    timeout = 30            # hängende Verbindungen nicht ewig offen halten
    ctx: Context            # werden von make_server gesetzt
    scheduler: Scheduler

    # ---- Grundgerüst ----
    def log_message(self, fmt, *args):  # Polling nicht ins Log schreiben
        log.debug("%s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data, extra: dict | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8",
                   {"Cache-Control": "no-store", **(extra or {})})

    def _authorized(self) -> bool:
        password = self.ctx.password
        if not password:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return False
        _, _, given = decoded.partition(":")
        return hmac.compare_digest(given.encode(), password.encode())

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "Ungültige Content-Length") from None
        if length < 0:
            raise ApiError(400, "Ungültige Content-Length")
        if length > MAX_BODY:
            self.close_connection = True   # Rest nicht lesen
            raise ApiError(413, "Anfrage zu groß")
        raw = self.rfile.read(length) if length else b""
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "Ungültiges JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "Erwartet wird ein JSON-Objekt")
        return data

    def _check_request(self, method: str) -> None:
        """Host-Header (DNS-Rebinding) und bei Schreibzugriffen Herkunft (CSRF) prüfen."""
        if not host_allowed(self.headers.get("Host"), self.ctx.allowed_hosts):
            raise ApiError(403, "Unbekannter Host – DEALMONITOR_ALLOWED_HOSTS setzen")
        if method in ("POST", "PUT", "DELETE"):
            origin = self.headers.get("Origin")
            host = (self.headers.get("Host") or "").strip().lower()
            if origin is not None:
                o_host = _origin_host(origin)
                # Reverse-Proxy ohne Host-Weitergabe: in DEALMONITOR_ALLOWED_HOSTS genannte
                # Hostnamen gelten ebenfalls als eigene Seite
                o_name = o_host.rpartition("@")[2].partition(":")[0].strip("[]")
                # "*" = hinter einem vertrauenswürdigen Proxy (Vorschau/Cloud) alles zulassen
                if o_host != host and "*" not in self.ctx.allowed_hosts \
                        and not (o_name and o_name in self.ctx.allowed_hosts):
                    raise ApiError(403, "Anfrage von fremder Seite abgelehnt")
            if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
                raise ApiError(403, "Anfrage von fremder Seite abgelehnt")

    def _dispatch(self, method: str) -> None:
        try:
            self._check_request(method)
            if not self._authorized():
                self._json(401, {"error": "Anmeldung erforderlich"},
                           {"WWW-Authenticate": 'Basic realm="Deal-Monitor", charset="UTF-8"'})
                return
            url = urlsplit(self.path)
            if url.path == "/api" or url.path.startswith("/api/"):
                self._api(method, url.path, parse_qs(url.query))
            elif method == "GET":
                self._static(url.path)
            else:
                raise ApiError(405, "Methode nicht erlaubt")
        except ApiError as e:
            if e.status in (403, 413, 415):
                self.close_connection = True   # evtl. ungelesenen Body nicht als Anfrage deuten
            self._json(e.status, {"error": e.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            log.error("Fehler bei %s %s:\n%s", method, self.path, traceback.format_exc())
            try:
                self._json(500, {"error": f"Interner Fehler: {e}"})
            except OSError:
                pass

    # ---- statische Dateien ----
    def _static(self, path: str) -> None:
        rel = unquote(path).lstrip("/") or "index.html"
        if "\x00" in rel or "\\" in rel:
            raise ApiError(404, "Nicht gefunden")
        root = STATIC_DIR.resolve()
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            raise ApiError(404, "Nicht gefunden")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            if rel == "index.html":
                self._send(200, PLACEHOLDER.encode("utf-8"), "text/html; charset=utf-8",
                           {"Cache-Control": "no-cache"})
                return
            raise ApiError(404, "Nicht gefunden")
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self._send(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    # ---- API ----
    ROUTES = [
        ("GET", r"/api/status", "status"),
        ("GET", r"/api/deals", "deals"),
        ("GET", r"/api/alerts", "alerts"),
        ("POST", r"/api/alerts", "create_alert"),
        ("PUT", r"/api/alerts/(?P<id>[^/]+)", "update_alert"),
        ("DELETE", r"/api/alerts/(?P<id>[^/]+)", "delete_alert"),
        ("GET", r"/api/regions", "regions"),
        ("GET", r"/api/settings", "get_settings"),
        ("PUT", r"/api/settings", "put_settings"),
        ("POST", r"/api/run", "run"),
        ("GET", r"/api/runs", "runs"),
        ("GET", r"/api/notifications", "notifications"),
        ("POST", r"/api/notifications/read", "read_notifications"),
        ("POST", r"/api/test-notify", "test_notify"),
    ]

    def _api(self, method: str, path: str, query: dict) -> None:
        path = path.rstrip("/") or path
        path_known = False
        for m, pattern, name in self.ROUTES:
            match = re.fullmatch(pattern, path)
            if not match:
                continue
            path_known = True
            if m != method:
                continue
            body = {}
            if method in ("POST", "PUT"):
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    raise ApiError(415, "Content-Type muss application/json sein")
                body = self._body()
            kwargs = {k: unquote(v) for k, v in match.groupdict().items()}
            with self.ctx.store() as store:
                status, data = getattr(self, "api_" + name)(store, query=query, body=body,
                                                             **kwargs)
            self._json(status, data)
            return
        if path_known:
            raise ApiError(405, "Methode nicht erlaubt")
        raise ApiError(404, "Unbekannter API-Pfad")

    def api_status(self, store, **_):
        st = self.scheduler.status()
        app = self.ctx.settings(store)
        alerts = store.alert_rows()
        return 200, {
            "version": __version__,
            "demo": self.ctx.demo,
            "running": st["running"],
            "last_run": st["last_run"],
            "next_run": st["next_run"],
            "interval_hours": st["interval_hours"],
            "configured": app.configured(self.ctx.demo),
            "counts": {
                "alerts": len(alerts),
                "alerts_enabled": sum(1 for a in alerts if a.get("enabled", True)),
                "active_deals": len(deal_list(store, "active")),
                "unread": store.unread_count(),
            },
        }

    def api_deals(self, store, query, **_):
        status = query.get("status", ["active"])[0] or "active"
        if status not in ("active", "expired", "all"):
            raise ApiError(400, "status muss active, expired oder all sein")
        alert_id = query.get("alert_id", [None])[0] or None
        return 200, {"deals": deal_list(store, status, alert_id)}

    def api_alerts(self, store, **_):
        stats = store.deal_stats()
        return 200, {"alerts": [alert_json(a, stats) for a in store.alert_rows()]}

    def api_create_alert(self, store, body, **_):
        data = dict(body)
        if not data.get("id"):
            name = data.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ApiError(400, "Bitte einen Namen angeben")
            base = slugify(name)
            candidate, n = base, 2
            while store.has_alert(candidate):
                candidate, n = f"{base}-{n}", n + 1
            data["id"] = candidate
        try:
            raw = normalize_alert(data)
        except (TypeError, ValueError) as e:
            raise ApiError(400, str(e)) from None
        if store.has_alert(raw["id"]):
            raise ApiError(409, f"Ein Alarm mit der ID '{raw['id']}' existiert bereits")
        try:
            store.insert_alert(raw)
        except KeyError:
            raise ApiError(409, f"Ein Alarm mit der ID '{raw['id']}' existiert bereits") from None
        return 201, {"alert": alert_json(raw, store.deal_stats())}

    def api_update_alert(self, store, body, id, **_):
        existing = store.get_alert(id)
        if existing is None:
            raise ApiError(404, f"Alarm '{id}' nicht gefunden")
        if "id" in body and body["id"] != id:
            raise ApiError(400, "Die ID eines Alarms kann nicht geändert werden")
        try:
            raw = normalize_alert({k: v for k, v in body.items() if k != "id"}, existing)
        except (TypeError, ValueError) as e:
            raise ApiError(400, str(e)) from None
        store.update_alert(raw)
        return 200, {"alert": alert_json(raw, store.deal_stats())}

    def api_delete_alert(self, store, id, **_):
        if not store.delete_alert(id):
            raise ApiError(404, f"Alarm '{id}' nicht gefunden")
        return 200, {"deleted": id}

    def api_regions(self, store, **_):
        return 200, {"regions": regions.REGIONS, "airports": regions.AIRPORT_NAMES,
                     "region_names": REGION_NAMES}

    def api_get_settings(self, store, **_):
        return 200, self.ctx.settings(store).public()

    def api_put_settings(self, store, body, **_):
        unknown = set(body) - {"values", "secrets"}
        if unknown:
            raise ApiError(400, f"Unbekannte Felder: {', '.join(sorted(unknown))}")
        app = self.ctx.settings(store)
        try:
            app.update(body.get("values"), body.get("secrets"))
        except (TypeError, ValueError) as e:
            raise ApiError(400, str(e)) from None
        return 200, app.public()

    def api_run(self, store, body, **_):
        alert_id = body.get("alert_id") or None
        if alert_id is not None:
            if not isinstance(alert_id, str):
                raise ApiError(400, "alert_id muss ein Text sein")
            if not store.has_alert(alert_id):
                raise ApiError(404, f"Alarm '{alert_id}' nicht gefunden")
        if not self.scheduler.trigger(alert_id, "manual"):
            raise ApiError(409, "Es läuft bereits eine Prüfung")
        return 202, {"started": True}

    def api_runs(self, store, query, **_):
        return 200, {"runs": store.runs(_int_param(query, "limit", 20, hi=500))}

    def api_notifications(self, store, query, **_):
        limit = _int_param(query, "limit", 100, hi=1000)
        return 200, {"notifications": store.notifications(limit), "unread": store.unread_count()}

    def api_read_notifications(self, store, body, **_):
        ids = body.get("ids")
        if ids is not None and (not isinstance(ids, list) or not all(
                isinstance(i, int) and not isinstance(i, bool) for i in ids)):
            raise ApiError(400, "ids muss eine Liste von Zahlen sein")
        return 200, {"unread": store.mark_read(ids)}

    def api_test_notify(self, store, **_):
        return 200, {"channels": notify.send_test(self.ctx.notifiers(store))}


# -- Server ----------------------------------------------------------------------------

def make_server(ctx: Context, scheduler: Scheduler, host: str = "127.0.0.1",
                port: int = 8080) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"ctx": ctx, "scheduler": scheduler})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(ctx: Context, host: str = "127.0.0.1", port: int = 8080, *,
          scheduler_enabled: bool = True, prepare=None) -> int:
    """Server starten (blockiert bis Strg+C / SIGTERM)."""
    try:
        ctx.seed()
        with ctx.store() as store:
            store.abort_unfinished_runs()
    except (sqlite3.OperationalError, PermissionError) as e:
        print(f"Fehler: Datenbank in {ctx.db_path} ist nicht beschreibbar – Dateirechte prüfen "
              f"({e})", file=sys.stderr)
        return 2
    scheduler = Scheduler(ctx, enabled=scheduler_enabled)
    if prepare:
        prepare(ctx, scheduler)
    try:
        server = make_server(ctx, scheduler, host, port)
    except OSError as e:
        print(f"Fehler: Port {port} auf {host} nicht verfügbar ({e})", file=sys.stderr)
        return 2
    shown = "localhost" if host in ("0.0.0.0", "::", "") else host
    real_port = server.server_address[1]
    extra = " (DEMO – Beispieldaten)" if ctx.demo else ""
    print(f"Deal-Monitor läuft auf http://{shown}:{real_port}{extra}", flush=True)
    if not scheduler_enabled:
        print("Zeitplan ist abgeschaltet (--no-scheduler) – nur manuelle Prüfungen.", flush=True)

    def _term(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGTERM, _term)
    except ValueError:  # nicht im Haupt-Thread
        pass
    scheduler.start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.stop(timeout=2)
        server.server_close()
        print("Deal-Monitor beendet.", flush=True)
    return 0
