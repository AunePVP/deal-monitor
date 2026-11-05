"""Skizze (nicht im Projekt eingebaut): Preisniveau aus der Google-Flights-Ergebnisseite lesen.
Nur stdlib. Live getestet 05.11.2026 an 3 Seiten (ZRH-HAM, FRA-PEK, HAM-ZRH)."""
import json, re

DS1 = re.compile(r"AF_initDataCallback\(\{key: 'ds:1', hash: '\d+', data:(.*?), sideChannel", re.S)

def price_insight(html: str):
    m = DS1.search(html)
    if not m:
        return None
    j = json.loads(m.group(1))
    # Block liegt bei ds:1[5]: [code, [None,tiefster], [None,Normalpreis], [None,Differenz],
    #                          [None,typ_von], [None,typ_bis], 1, ..., verlauf bei [10]]
    for x in j:
        if (isinstance(x, list) and len(x) >= 6 and isinstance(x[0], int)
                and all(isinstance(v, list) and len(v) == 2 and v[0] is None for v in x[1:6])):
            low, normal, diff, lo, hi = (v[1] for v in x[1:6])
            level = "niedrig" if low < lo else "hoch" if low > hi else "typisch"  # selbst abgeleitet
            hist = x[10][0] if len(x) > 10 and x[10] else []
            return {"code": x[0], "lowest": low, "normal": normal, "saving": diff,
                    "typical_low": lo, "typical_high": hi, "level": level,
                    "history": [(ts // 1000, p) for ts, p in hist]}
    return None
