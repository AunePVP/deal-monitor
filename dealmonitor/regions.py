"""Regionen und Flughäfen.

In der Alarm-Konfiguration kann statt eines einzelnen IATA-Codes eine Region
mit ``@`` angegeben werden, z. B. ``origins = ["@DE"]``.

Codes sind - wo vorhanden - *Stadt*-Codes (BJS = alle Pekinger Flughäfen,
SHA = Pudong + Hongqiao, BER, ...), weil die Preis-API damit alle Flughäfen
einer Stadt in einer Abfrage abdeckt.
"""

from __future__ import annotations

# Code -> Anzeigename (deutsch)
AIRPORT_NAMES: dict[str, str] = {
    # Deutschland
    "FRA": "Frankfurt", "MUC": "München", "BER": "Berlin", "DUS": "Düsseldorf",
    "HAM": "Hamburg", "STR": "Stuttgart", "CGN": "Köln/Bonn", "HAJ": "Hannover",
    "NUE": "Nürnberg", "LEJ": "Leipzig", "BRE": "Bremen", "DRS": "Dresden",
    "DTM": "Dortmund", "FMM": "Memmingen", "FKB": "Karlsruhe/Baden-Baden",
    "PAD": "Paderborn", "MSR": "Münster/Osnabrück", "FMO": "Münster/Osnabrück",
    # Schweiz (+ Basel/EuroAirport)
    "ZRH": "Zürich", "GVA": "Genf", "BSL": "Basel", "BRN": "Bern",
    # Österreich
    "VIE": "Wien", "SZG": "Salzburg", "INN": "Innsbruck", "GRZ": "Graz",
    # China (Festland)
    "BJS": "Peking", "PEK": "Peking Capital", "PKX": "Peking Daxing",
    "SHA": "Shanghai", "PVG": "Shanghai Pudong", "CAN": "Guangzhou",
    "SZX": "Shenzhen", "CTU": "Chengdu", "TFU": "Chengdu Tianfu",
    "CKG": "Chongqing", "HGH": "Hangzhou", "XMN": "Xiamen", "KMG": "Kunming",
    "XIY": "Xi'an", "NKG": "Nanjing", "TAO": "Qingdao", "WUH": "Wuhan",
    "CSX": "Changsha", "SHE": "Shenyang", "TSN": "Tianjin", "DLC": "Dalian",
    "CGO": "Zhengzhou", "HAK": "Haikou", "SYX": "Sanya",
    # Sonderverwaltungszonen
    "HKG": "Hongkong", "MFM": "Macau",
}

REGIONS: dict[str, list[str]] = {
    # Die großen deutschen Abflughäfen mit Langstrecken-/Umsteigeverbindungen
    "DE": ["FRA", "MUC", "BER", "DUS", "HAM", "STR", "CGN", "HAJ", "NUE", "LEJ", "BRE"],
    # Nur die Langstrecken-Hubs (weniger API-Abfragen)
    "DE_HUBS": ["FRA", "MUC", "BER", "DUS", "HAM"],
    "CH": ["ZRH", "GVA", "BSL"],
    "AT": ["VIE", "SZG", "INN", "GRZ"],
    "DACH": ["FRA", "MUC", "BER", "DUS", "HAM", "STR", "CGN", "ZRH", "GVA", "BSL", "VIE"],
    # Festland-China - Städte mit Direkt- oder guten Umsteigeverbindungen aus Europa
    "CN": ["BJS", "SHA", "CAN", "SZX", "CTU", "CKG", "HGH", "XMN", "KMG",
           "XIY", "NKG", "TAO", "WUH", "CSX", "SHE", "TSN"],
    # Nur die großen Gateways (weniger API-Abfragen)
    "CN_MAJOR": ["BJS", "SHA", "CAN", "SZX", "CTU", "HGH"],
    "CN_HK": ["BJS", "SHA", "CAN", "SZX", "CTU", "CKG", "HGH", "XMN", "KMG",
              "XIY", "NKG", "TAO", "WUH", "CSX", "SHE", "TSN", "HKG", "MFM"],
}


def expand(codes: list[str]) -> list[str]:
    """Löst ``@REGION``-Einträge auf und entfernt Duplikate (Reihenfolge bleibt)."""
    out: list[str] = []
    for code in codes:
        code = code.strip().upper()
        if code.startswith("@"):
            region = code[1:]
            if region not in REGIONS:
                raise ValueError(
                    f"Unbekannte Region '{code}'. Verfügbar: "
                    + ", ".join("@" + r for r in sorted(REGIONS))
                )
            members = REGIONS[region]
        else:
            if len(code) != 3 or not code.isalpha():
                raise ValueError(f"Ungültiger IATA-Code '{code}'")
            members = [code]
        for m in members:
            if m not in out:
                out.append(m)
    return out


def name(code: str) -> str:
    return AIRPORT_NAMES.get(code.upper(), code.upper())
