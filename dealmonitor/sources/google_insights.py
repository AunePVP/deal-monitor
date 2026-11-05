"""Preisniveau einer Google-Flights-Ergebnisseite ("Die Preise sind derzeit niedrig").

Die Seite bringt im Skriptblock ``AF_initDataCallback({key: 'ds:1', hash: '…', data:[…], sideChannel…})``
ein JSON mit Googles Einschätzung der Suche mit - ohne zusätzlichen Abruf:

    [code, [None, tiefstpreis], [None, normalpreis], [None, differenz],
     [None, typisch_von], [None, typisch_bis], 1, …, [[[ts_ms, preis], …]]]

Die Feldbedeutung ist **abgeleitet**, nicht dokumentiert (Messungen 05.11.2026). Belegt ist
nur Code 1 = "niedrig"; 2, 3 und 4 erschienen als "normal". Deshalb wird ``level`` aus Preis und
Spanne berechnet, nicht aus dem Code. Das Element wird über seine Form gesucht, nicht über einen
festen Index. Ändert Google den Aufbau, liefert ``parse`` ``None`` - nie eine Ausnahme.

``data:[13, …]`` ist eine ErrorResponse (vorübergehend, ein zweiter Abruf derselben URL lohnt). Echte
Fehlerseiten haben eine andere Form als Ergebnisseiten - kein ``hash``, zwei Leerzeichen und
``errorHasStatus`` statt ``sideChannel`` (gemessen 05.11.2026):
``AF_initDataCallback({key: 'ds:1',  data:[13,null,[["type.googleapis.com/…ErrorResponse",…]]],
errorHasStatus: true,})``.
Ohne ``ds:1`` bleibt als Rückfallebene der sichtbare Satz "Die Preise sind derzeit
<span>normal</span>" (den Text für "hoch" hat noch niemand gesehen).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

DS1_RE = re.compile(r"AF_initDataCallback\(\{key: 'ds:1',\s*(?:hash: '\d+',\s*)?data:(.*?),"
                    r"\s*(?:sideChannel|errorHasStatus)", re.S)
#: ``ds:0`` = die Suche, wie Google sie verstanden hat (Strecken, Daten, Kabine)
DS0_RE = re.compile(DS1_RE.pattern.replace("'ds:1'", "'ds:0'"), re.S)
#: ``data:[13, …]`` - Google konnte die Suche gerade nicht beantworten
ERROR_CODE = 13
TEXT_RE = re.compile(r"Die Preise sind (?:aktuell|derzeit)\s*(?:<[^>]*>\s*)*"
                     r"(niedrig|normal|hoch)\b")
SAVING_RE = re.compile(r"(\d[\d.]*)\s*(?:€|Euro)\s+(unter|über) dem Normalpreis")
TEXT_LEVELS = {"niedrig": "niedrig", "normal": "typisch", "hoch": "hoch"}
#: so tief wird im ds:1-Baum nach dem Element gesucht
MAX_DEPTH = 6


def ds0_data(html: str):
    """Der ``ds:0``-Datenblock (Suchanfrage) als JSON (``None``, wenn nicht vorhanden/kaputt)."""
    return _json_block(DS0_RE, html)


def ds1_data(html: str):
    """Der ``ds:1``-Datenblock als JSON (``None``, wenn nicht vorhanden oder kaputt)."""
    return _json_block(DS1_RE, html)


def _json_block(pattern, html: str):
    m = pattern.search(html or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def is_error_response(html: str) -> bool:
    """``ds:1`` ist eine ErrorResponse (``data:[13, …]``, auf echten Fehlerseiten mit
    ``errorHasStatus`` und ohne ``hash``) - lohnt einen zweiten Abruf derselben URL."""
    data = ds1_data(html)
    return isinstance(data, list) and bool(data) and data[0] == ERROR_CODE


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_insight(x) -> bool:
    return (isinstance(x, list) and len(x) >= 6 and isinstance(x[0], int)
            and not isinstance(x[0], bool)
            and all(isinstance(v, list) and len(v) == 2 and v[0] is None and _num(v[1])
                    for v in x[1:6]))


def _find(node):
    """Erstes Element mit der Form des Preisniveaus (Breitensuche, damit oben gewinnt)."""
    level = [node]
    for _ in range(MAX_DEPTH):
        nxt = []
        for x in level:
            if _is_insight(x):
                return x
            if isinstance(x, list):
                nxt += [c for c in x if isinstance(c, list)]
        level = nxt
    return None


def _day(ts_ms) -> str:
    """Tagespunkt (Mitternacht Ortszeit) als ISO-Datum; +12 h fängt die Zeitzone ab."""
    moment = datetime.fromtimestamp(ts_ms / 1000, timezone.utc) + timedelta(hours=12)
    return moment.date().isoformat()


def _history(rest: list) -> list[list]:
    """Preisverlauf ``[[ts_ms, preis], …]`` (ggf. eine Ebene tiefer) als ``[[ISO-Datum, preis]]``."""
    for x in rest:
        pts = x[0] if isinstance(x, list) and x and isinstance(x[0], list) \
            and x[0] and isinstance(x[0][0], list) else x
        if (isinstance(pts, list) and pts
                and all(isinstance(p, list) and len(p) == 2 and _num(p[0]) and _num(p[1])
                        for p in pts)):
            try:
                return [[_day(ts), price] for ts, price in pts]
            except (OverflowError, OSError, ValueError):
                return []
    return []


def level_of(lowest, typical_low, typical_high) -> str:
    """"niedrig" unter der typischen Spanne, "hoch" darüber, sonst "typisch"."""
    if lowest < typical_low:
        return "niedrig"
    if lowest > typical_high:
        return "hoch"
    return "typisch"


def from_ds1(data) -> dict | None:
    if not isinstance(data, list) or not data or data[0] == ERROR_CODE:
        return None
    x = _find(data)
    if x is None:
        return None
    lowest, typical, saving, lo, hi = (v[1] for v in x[1:6])
    if lowest <= 0 or typical <= 0 or lo <= 0 or hi < lo:
        return None
    return {"level_code": x[0], "lowest": lowest, "typical": typical, "saving": saving,
            "typical_low": lo, "typical_high": hi, "history": _history(x[6:]),
            "level": level_of(lowest, lo, hi)}


def from_text(html: str) -> dict | None:
    """Rückfallebene: nur das Niveau (und ggf. die Differenz) aus dem sichtbaren Satz."""
    m = TEXT_RE.search(html or "")
    if not m:
        return None
    saving = None
    if (s := SAVING_RE.search(html, m.end(), m.end() + 2000)):
        value = int(s.group(1).replace(".", ""))
        saving = value if s.group(2) == "unter" else -value
    return {"level_code": None, "lowest": None, "typical": None, "saving": saving,
            "typical_low": None, "typical_high": None, "history": [],
            "level": TEXT_LEVELS[m.group(1)]}


def parse(html: str) -> dict | None:
    """Preisniveau der Seite oder ``None``.

    ``{level_code, lowest, typical, saving, typical_low, typical_high, history, level}`` -
    Preise in der Währung der Seite (bei uns EUR), ``saving`` ≈ Normalpreis − Tiefstpreis
    (negativ = teurer als normal), ``history`` = ``[[ISO-Datum, preis], …]``, ``level`` =
    "niedrig" | "typisch" | "hoch". JSON-tauglich (landet in ``Offer.insight``).
    """
    try:
        return from_ds1(ds1_data(html)) or from_text(html)
    except Exception as e:  # noqa: BLE001 - Zusatzinfo, darf die Abfrage nie stoppen
        log.warning("Google-Preisniveau nicht lesbar: %s", e)
        return None
