"""Währungen in Euro umrechnen (für Deal-Quellen, die $, £ oder CHF nennen).

Schnittstelle::

    to_eur(291, "USD")        # -> 258.28 (feste EZB-Kurse, Stand siehe RATES_DATE)
    to_eur(29, "CHF")         # -> 31.56
    to_eur(100, "XYZ")        # -> None (unbekannte Währung)
    code_for("US$")           # -> "USD" (Symbol/Schreibweise -> ISO-Code)

Optional aktuelle Kurse (einmal am Tag reicht, die EZB veröffentlicht werktags gegen 16 Uhr)::

    refresh_rates(store)      # lädt eurofxref-daily.xml, merkt die Kurse in store.kv["fx_rates"]
    load_rates(store)         # beim Start: gespeicherte Kurse übernehmen (ohne Netz)

Fehler beim Abruf sind nie fatal - dann gelten weiter die festen Kurse.
"""

from __future__ import annotations

import logging
import math
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
#: so alt dürfen gespeicherte Kurse werden, bevor ``refresh_rates`` neu lädt
MAX_AGE = timedelta(hours=24)
KV_KEY = "fx_rates"

#: EZB-Referenzkurse (1 EUR = x Fremdwährung), Stand RATES_DATE
RATES_DATE = "2026-11-04"
FIXED_RATES: dict[str, float] = {
    "EUR": 1.0, "USD": 1.1267, "GBP": 0.83460, "CHF": 0.9188, "AUD": 1.6061, "CAD": 1.5701,
    "CNY": 7.5177, "HKD": 8.8399, "SGD": 1.4398, "NZD": 1.9988, "JPY": 178.30, "PLN": 4.3990,
    "CZK": 24.309, "HUF": 363.03, "SEK": 10.9718, "NOK": 10.3970, "DKK": 7.4738,
    "TRY": 56.3598, "THB": 37.902, "INR": 106.6318,
}

#: Schreibweisen in Überschriften -> ISO-Code ("$" allein gilt als US-Dollar)
SYMBOLS: dict[str, str] = {
    "€": "EUR", "euro": "EUR", "eur": "EUR",
    "$": "USD", "us$": "USD", "usd": "USD",
    "£": "GBP", "gbp": "GBP",
    "chf": "CHF", "fr.": "CHF", "sfr": "CHF", "sfr.": "CHF",
    "a$": "AUD", "au$": "AUD", "c$": "CAD", "ca$": "CAD", "hk$": "HKD", "s$": "SGD",
    "nz$": "NZD", "rmb": "CNY", "cny": "CNY", "yuan": "CNY",
    "zł": "PLN", "zl": "PLN", "kč": "CZK", "kc": "CZK",
}

_rates: dict[str, float] = dict(FIXED_RATES)
_rates_date: str = RATES_DATE


def code_for(currency: str | None) -> str | None:
    """ISO-Code für "USD", "usd", "$", "£", "CHF" … - ``None``, wenn unbekannt."""
    if not currency:
        return None
    c = str(currency).strip()
    if c.upper() in _rates:
        return c.upper()
    return SYMBOLS.get(c.lower())


def to_eur(amount, currency: str | None) -> float | None:
    """Betrag in Euro (2 Nachkommastellen) - ``None`` bei unbekannter Währung/ungültigem Betrag."""
    code = code_for(currency)
    rate = _rates.get(code) if code else None
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return None
    if not rate or not math.isfinite(value) or value < 0:
        return None
    return round(value / rate, 2)


def rates() -> tuple[str, dict[str, float]]:
    """(Stand, Kurse) der aktuell benutzten Kurse."""
    return _rates_date, dict(_rates)


def parse_ecb(xml: bytes | str) -> tuple[str, dict[str, float]]:
    """``eurofxref-daily.xml`` -> (Datum, {Code: Kurs}). Wirft ``ValueError`` bei unbrauchbarer Antwort."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ValueError(f"EZB-Kurse nicht lesbar: {e}") from None
    day, out = "", {}
    for cube in root.iter():
        if not cube.tag.endswith("Cube"):
            continue
        if cube.get("time"):
            day = cube.get("time")
        cur, rate = cube.get("currency"), cube.get("rate")
        if cur and rate:
            try:
                value = float(rate)
            except ValueError:
                continue
            if value > 0 and math.isfinite(value):
                out[cur.upper()] = value
    if not out:
        raise ValueError("EZB-Kurse: keine Kurse in der Antwort")
    return day, out


def _apply(day: str, new: dict[str, float]) -> None:
    global _rates, _rates_date
    _rates = {**FIXED_RATES, **new, "EUR": 1.0}
    _rates_date = day or _rates_date


def load_rates(store) -> bool:
    """Gespeicherte Kurse aus ``store.kv`` übernehmen (kein Netz). True, wenn welche da waren."""
    saved = store.kv_get(KV_KEY) if store is not None else None
    if not isinstance(saved, dict) or not isinstance(saved.get("rates"), dict):
        return False
    clean = {str(k).upper(): float(v) for k, v in saved["rates"].items()
             if isinstance(v, (int, float)) and v > 0}
    if not clean:
        return False
    _apply(str(saved.get("date") or ""), clean)
    return True


def refresh_rates(store=None, *, now: datetime | None = None, fetch=None,
                  timeout: float = 10.0) -> bool:
    """Tageskurse der EZB laden, wenn die gespeicherten älter als ``MAX_AGE`` sind.

    ``fetch(url) -> bytes`` ersetzt den Netzabruf (Tests). Rückgabe: True, wenn neu geladen.
    Fehler werden geloggt; es gelten dann weiter die bisherigen Kurse.
    """
    now = now or datetime.now()
    saved = store.kv_get(KV_KEY) if store is not None else None
    if isinstance(saved, dict) and saved.get("fetched_at"):
        try:
            if now - datetime.fromisoformat(saved["fetched_at"]) < MAX_AGE:
                load_rates(store)
                return False
        except (TypeError, ValueError):
            pass
    if fetch is None:
        from . import http

        def fetch(url):
            return http.request(url, timeout=timeout, error_body=False, max_bytes=200_000)
    try:
        day, new = parse_ecb(fetch(ECB_URL))
    except Exception as e:  # noqa: BLE001 - Kurse sind Zugabe, nie fatal
        log.warning("EZB-Kurse nicht abrufbar, feste Kurse vom %s bleiben: %s", _rates_date, e)
        return False
    _apply(day, new)
    if store is not None:
        store.kv_set(KV_KEY, {"date": day, "rates": new,
                              "fetched_at": now.isoformat(timespec="seconds")})
    return True


def reset_rates() -> None:
    """Zurück auf die festen Kurse (Tests)."""
    _apply(RATES_DATE, {})
