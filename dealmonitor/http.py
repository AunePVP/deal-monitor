"""Kleiner HTTP-Helfer auf Basis der Standardbibliothek (keine Abhängigkeiten)."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.client import HTTPException

USER_AGENT = "deal-monitor/0.1 (+https://github.com/)"
#: Query-Parameter, deren Wert nie in Fehlermeldungen/Logs landen darf
SECRET_PARAMS = {"token", "apikey", "api_key", "key", "access_token", "auth", "secret",
                 "password", "sig", "signature"}
#: Obergrenze für Antworten (Bytes) und Gesamtdauer einer Anfrage (Sekunden) - ein kaputter
#: oder böswilliger Server darf den Lauf nicht beliebig lange aufhalten
MAX_BYTES = 10_000_000
MAX_TIME = 90.0
_CHUNK = 65536


class HttpError(RuntimeError):
    """Fehler bei einer Anfrage.

    ``status`` ist der HTTP-Statuscode (z. B. 404, 429, 503) oder ``None`` bei
    Netzwerkfehlern (DNS, Zeitüberschreitung, Verbindung abgebrochen) und
    ungültigen Antworten.
    """

    def __init__(self, message: str, status: int | None = None, *, network: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.network = network   # True: Server nicht erreichbar / keine Antwort


@dataclass
class Response:
    """Rohe Antwort (``request(..., full=True)``): Body, Status, Header, endgültige Adresse."""

    body: bytes
    status: int
    headers: dict[str, str]
    url: str              # nach Weiterleitungen (z. B. consent.google.com erkennen)

    def header(self, name: str, default: str = "") -> str:
        for k, v in self.headers.items():
            if k.lower() == name.lower():
                return v
        return default


def _private_reason(host: str) -> str | None:
    """Grund, warum ``host`` (Name oder IP) intern ist - None = öffentlich."""
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None
    if not ip.is_global or ip.is_multicast:
        return f"interne Adresse {ip}"
    return None


def internal_host(host: str | None) -> str | None:
    """Hostname, der offensichtlich ins eigene Netz zeigt (localhost, private/Link-Local-IP,
    ``.local``/``.internal``, Namen ohne Punkt) - ohne DNS. None = unbedenklich."""
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return "keine Adresse"
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan",
                                              ".home", ".intranet")):
        return f"interner Name {host}"
    reason = _private_reason(host)
    if reason:
        return reason
    if "." not in host and ":" not in host:
        return f"interner Name {host}"
    return None


def check_public(url: str) -> None:
    """``HttpError``, wenn ``url`` auf ein internes Ziel zeigt - auch nach DNS-Auflösung
    (entfällt, wenn ein Proxy die Adresse auflöst)."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as e:
        raise HttpError(f"Ungültige Adresse: {_redact(url)} ({e})") from e
    if parts.scheme.lower() not in ("http", "https"):
        raise HttpError(f"Nur http(s) erlaubt: {_redact(url)}")
    host = parts.hostname or ""
    reason = internal_host(host)
    if reason:
        raise HttpError(f"Adresse nicht erlaubt ({reason}): {_redact(url)}")
    proxies = urllib.request.getproxies()
    if proxies.get(parts.scheme.lower()) and not urllib.request.proxy_bypass(host):
        return          # der Proxy löst den Namen auf - lokal nicht prüfbar
    try:
        infos = socket.getaddrinfo(host, parts.port or None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError) as e:
        raise HttpError(f"Anfrage fehlgeschlagen: {_redact(url)} ({e})", network=True) from e
    for info in infos:
        reason = _private_reason(info[4][0])
        if reason:
            raise HttpError(f"Adresse nicht erlaubt ({host} → {reason}): {_redact(url)}")


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    """Weiterleitungen nur auf öffentliche Adressen folgen."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read(resp, max_bytes: int, deadline: float, url: str) -> bytes:
    """Antwort stückweise lesen - mit Größen- und Gesamtzeit-Grenze."""
    chunks, size = [], 0
    read = getattr(resp, "read1", None) or resp.read
    while True:
        if time.monotonic() > deadline:
            raise HttpError(f"Antwort von {_redact(url)} dauert zu lange (abgebrochen)",
                            network=True)
        chunk = read(_CHUNK)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > max_bytes:
            raise HttpError(f"Antwort von {_redact(url)} zu groß (über "
                            f"{max_bytes // 1_000_000} MB, abgebrochen)")
        chunks.append(chunk)


def request(
    url: str,
    *,
    params: dict | None = None,
    data: bytes | None = None,
    headers: dict | None = None,
    method: str | None = None,
    timeout: float = 20,
    retries: int = 2,
    full: bool = False,
    max_bytes: int = MAX_BYTES,
    max_time: float | None = None,
    error_body: bool = True,
    public_only: bool = False,
) -> bytes | Response:
    """GET/POST mit Wiederholungen. ``full=True`` liefert statt der Bytes ein ``Response``
    (Body + Status + Header + endgültige URL) - für Quellen, die Weiterleitungen oder
    ``Content-Encoding`` selbst auswerten müssen.

    ``timeout`` gilt je Netzwerk-Operation, ``max_time`` (Standard ``max(MAX_TIME, 3 ×
    timeout)``) für die ganze Antwort, ``max_bytes`` begrenzt ihre Größe. ``error_body=False``:
    keinen Antworttext in Fehlermeldungen (fremde Server). ``public_only=True``: nur
    öffentliche Ziele, auch nach Weiterleitungen (eigene Feed-URLs)."""
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{url}?{urllib.parse.urlencode(clean)}"
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    total = max_time if max_time is not None else max(MAX_TIME, 3 * timeout)
    opener = urllib.request.build_opener(_PublicRedirects) if public_only else None
    last: Exception | None = None
    status: int | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            if public_only:
                check_public(url)
            deadline = time.monotonic() + total
            with (opener.open(req, timeout=timeout) if opener
                  else urllib.request.urlopen(req, timeout=timeout)) as resp:
                body = _read(resp, max_bytes, deadline, url)
                if full:
                    return Response(body, resp.status, dict(resp.headers.items()), resp.geturl())
                return body
        except urllib.error.HTTPError as e:
            last, status = e, e.code
            # 4xx (außer 429) sind endgültig - nicht wiederholen
            if 400 <= e.code < 500 and e.code != 429:
                body = ""
                if error_body:
                    try:
                        body = e.read()[:300].decode(errors="replace")
                    except (OSError, HTTPException):
                        body = ""
                raise HttpError(f"HTTP {e.code} für {_redact(url)}" + (f": {body}" if body else ""),
                                e.code) from e
        except (urllib.error.URLError, HTTPException, OSError, ValueError) as e:
            # URLError/TimeoutError/ConnectionError sind OSError; HTTPException z. B.
            # IncompleteRead/RemoteDisconnected; ValueError bei kaputter URL
            last, status = e, None
            if isinstance(e, ValueError) and not isinstance(e, HTTPException):
                raise HttpError(f"Ungültige Adresse: {_redact(url)} ({e})") from e
        if attempt < retries:
            time.sleep(2 ** attempt)
    raise HttpError(f"Anfrage fehlgeschlagen: {_redact(url)} ({last})", status,
                    network=status is None)


def get_json(url: str, **kw) -> dict:
    raw = request(url, **kw)
    try:
        return json.loads(raw)
    except ValueError as e:   # JSONDecodeError, UnicodeDecodeError
        raise HttpError(f"Ungültige JSON-Antwort von {_redact(url)}: {e}") from e


def redact(url: str) -> str:
    """Adresse ohne Zugangsdaten (Token in Query/Pfad, ``user:pass@``) - für Logs/Meldungen."""
    return _redact(url)


def _redact(url: str) -> str:
    """Token nicht in Logs/Fehlermeldungen schreiben (Query, Pfad, Benutzerdaten)."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "<ungültige Adresse>"
    q = [(k, "***" if k.lower() in SECRET_PARAMS else v)
         for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)]
    # Telegram: https://api.telegram.org/bot<TOKEN>/sendMessage
    path = re.sub(r"/bot[^/]+", "/bot***", parts.path)
    netloc = parts.netloc.rpartition("@")[2]   # user:passwort@ entfernen
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc, path=path,
                                                  query=urllib.parse.urlencode(q)))
