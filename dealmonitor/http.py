"""Kleiner HTTP-Helfer auf Basis der Standardbibliothek (keine Abhängigkeiten)."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException

USER_AGENT = "deal-monitor/0.1 (+https://github.com/)"
#: Query-Parameter, deren Wert nie in Fehlermeldungen/Logs landen darf
SECRET_PARAMS = {"token", "apikey", "api_key", "key", "access_token", "auth", "secret",
                 "password", "sig", "signature"}


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


def request(
    url: str,
    *,
    params: dict | None = None,
    data: bytes | None = None,
    headers: dict | None = None,
    method: str | None = None,
    timeout: float = 20,
    retries: int = 2,
) -> bytes:
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{url}?{urllib.parse.urlencode(clean)}"
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    last: Exception | None = None
    status: int | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last, status = e, e.code
            # 4xx (außer 429) sind endgültig - nicht wiederholen
            if 400 <= e.code < 500 and e.code != 429:
                try:
                    body = e.read()[:300].decode(errors="replace")
                except (OSError, HTTPException):
                    body = ""
                raise HttpError(f"HTTP {e.code} für {_redact(url)}: {body}", e.code) from e
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
