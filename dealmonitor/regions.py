"""Regionen, Flughäfen und Ortsnamen.

In der Alarm-Konfiguration kann statt eines einzelnen IATA-Codes eine Region
mit ``@`` angegeben werden, z. B. ``origins = ["@DE"]``.

Codes sind - wo vorhanden - *Stadt*-Codes (BJS = alle Pekinger Flughäfen,
SHA = Pudong + Hongqiao, BER, ...), weil die Preis-API damit alle Flughäfen
einer Stadt in einer Abfrage abdeckt.

Außerdem liegen hier die Ortsnamen für den Stichwort-Abgleich der Deal-Feeds:
deutsch/englische Synonyme (``CITY_SYNONYMS``), die DACH-Heimatorte
(``HOME_PLACES``) und bekannte fremde Abflugorte (``FOREIGN_PLACES``).
"""

from __future__ import annotations

import re
import unicodedata

# -- Flughäfen ------------------------------------------------------------------

#: Deutschland
DE_AIRPORTS: dict[str, str] = {
    "FRA": "Frankfurt", "MUC": "München", "BER": "Berlin", "DUS": "Düsseldorf",
    "HAM": "Hamburg", "STR": "Stuttgart", "CGN": "Köln/Bonn", "HAJ": "Hannover",
    "NUE": "Nürnberg", "LEJ": "Leipzig", "BRE": "Bremen", "DRS": "Dresden",
    "DTM": "Dortmund", "FMM": "Memmingen", "FKB": "Karlsruhe/Baden-Baden",
    "PAD": "Paderborn", "MSR": "Münster/Osnabrück", "FMO": "Münster/Osnabrück",
    "NRN": "Weeze", "SCN": "Saarbrücken", "FDH": "Friedrichshafen", "ERF": "Erfurt",
    "RLG": "Rostock", "LBC": "Lübeck", "KSF": "Kassel",
}
#: Schweiz (+ Basel/EuroAirport)
CH_AIRPORTS: dict[str, str] = {
    "ZRH": "Zürich", "GVA": "Genf", "BSL": "Basel", "BRN": "Bern",
}
#: Österreich
AT_AIRPORTS: dict[str, str] = {
    "VIE": "Wien", "SZG": "Salzburg", "INN": "Innsbruck", "GRZ": "Graz",
    "LNZ": "Linz", "KLU": "Klagenfurt",
}
#: China (Festland) und Sonderverwaltungszonen
CN_AIRPORTS: dict[str, str] = {
    "BJS": "Peking", "PEK": "Peking Capital", "PKX": "Peking Daxing",
    "SHA": "Shanghai", "PVG": "Shanghai Pudong", "CAN": "Guangzhou",
    "SZX": "Shenzhen", "CTU": "Chengdu", "TFU": "Chengdu Tianfu",
    "CKG": "Chongqing", "HGH": "Hangzhou", "XMN": "Xiamen", "KMG": "Kunming",
    "XIY": "Xi'an", "NKG": "Nanjing", "TAO": "Qingdao", "WUH": "Wuhan",
    "CSX": "Changsha", "SHE": "Shenyang", "TSN": "Tianjin", "DLC": "Dalian",
    "CGO": "Zhengzhou", "HAK": "Haikou", "SYX": "Sanya",
    "HKG": "Hongkong", "MFM": "Macau",
}
#: Häufige fremde Abflug-/Zielorte (Stadtcodes), damit Alarme sie als Von/Nach nutzen
#: können und Feed-Titel wie "ab Lissabon" einem Code zugeordnet werden
WORLD_AIRPORTS: dict[str, str] = {
    "AMS": "Amsterdam", "LIS": "Lissabon", "MAD": "Madrid", "PAR": "Paris", "LON": "London",
    "MIL": "Mailand", "ROM": "Rom", "ATH": "Athen", "BUD": "Budapest", "WAW": "Warschau",
    "PRG": "Prag", "BRU": "Brüssel", "CPH": "Kopenhagen", "STO": "Stockholm", "OSL": "Oslo",
    "DUB": "Dublin", "BCN": "Barcelona", "OPO": "Porto", "IST": "Istanbul", "LUX": "Luxemburg",
    "MAN": "Manchester", "HEL": "Helsinki", "VCE": "Venedig", "LYS": "Lyon", "NCE": "Nizza",
    "KRK": "Krakau", "GDN": "Danzig", "EDI": "Edinburgh", "RIX": "Riga", "VNO": "Vilnius",
    "TLL": "Tallinn", "SOF": "Sofia", "BUH": "Bukarest", "BEG": "Belgrad", "ZAG": "Zagreb",
    "LJU": "Ljubljana", "BTS": "Bratislava", "KEF": "Reykjavik", "GOT": "Göteborg",
    "TYO": "Tokio", "OSA": "Osaka", "SEL": "Seoul", "BKK": "Bangkok", "SIN": "Singapur",
    "KUL": "Kuala Lumpur", "HAN": "Hanoi", "SGN": "Ho-Chi-Minh-Stadt", "MNL": "Manila",
    "JKT": "Jakarta", "DPS": "Bali", "TPE": "Taipeh", "DEL": "Delhi", "BOM": "Mumbai",
    "DXB": "Dubai", "AUH": "Abu Dhabi", "DOH": "Doha", "TLV": "Tel Aviv", "CAI": "Kairo",
    "NYC": "New York", "LAX": "Los Angeles", "SFO": "San Francisco", "MIA": "Miami",
    "CHI": "Chicago", "WAS": "Washington", "BOS": "Boston", "SEA": "Seattle", "HOU": "Houston",
    "ATL": "Atlanta", "YTO": "Toronto", "YMQ": "Montreal", "YVR": "Vancouver",
    "SYD": "Sydney", "MEL": "Melbourne", "AKL": "Auckland", "JNB": "Johannesburg",
    "CPT": "Kapstadt", "NBO": "Nairobi", "SAO": "São Paulo", "BUE": "Buenos Aires",
    "MEX": "Mexiko-Stadt",
}

# Code -> Anzeigename (deutsch)
AIRPORT_NAMES: dict[str, str] = {**DE_AIRPORTS, **CH_AIRPORTS, **AT_AIRPORTS,
                                 **CN_AIRPORTS, **WORLD_AIRPORTS}
#: DACH-Flughäfen ("Heimat": Abflug ab Deutschland, Schweiz oder Österreich)
HOME_CODES: frozenset[str] = frozenset({*DE_AIRPORTS, *CH_AIRPORTS, *AT_AIRPORTS})

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

#: Region -> Namen, die in Deal-Überschriften für die ganze Region stehen
REGION_NAMES: dict[str, list[str]] = {
    "DE": ["Deutschland"], "DE_HUBS": ["Deutschland"], "CH": ["Schweiz"], "AT": ["Österreich"],
    "DACH": ["Deutschland", "Schweiz", "Österreich"],
    "CN": ["China"], "CN_MAJOR": ["China"], "CN_HK": ["China", "Hongkong"],
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


# -- Ortsnamen: Synonyme, Heimat, Ausland ----------------------------------------

#: Gleichbedeutende Ortsnamen (deutsch ↔ englisch ↔ Landessprache). Jede Gruppe ist
#: ein Stichwort: "Peking" im Alarm trifft auch "Beijing" im englischen Feed.
CITY_SYNONYMS: list[tuple[str, ...]] = [
    # DACH
    ("Zürich", "Zurich", "Zuerich"), ("München", "Munich", "Muenchen"), ("Köln", "Cologne", "Koeln"),
    ("Wien", "Vienna"), ("Genf", "Geneva", "Genève"), ("Nürnberg", "Nuremberg", "Nuernberg"),
    ("Hannover", "Hanover"), ("Basel", "Basle", "Mulhouse", "EuroAirport"),
    ("Düsseldorf", "Dusseldorf", "Duesseldorf"), ("Frankfurt", "Frankfurt am Main"),
    ("Deutschland", "Germany", "German"), ("Schweiz", "Switzerland"),
    ("Österreich", "Austria", "Austrian"),
    # China
    ("Peking", "Beijing"), ("Kanton", "Guangzhou", "Canton"), ("Hongkong", "Hong Kong"),
    ("Xi'an", "Xian", "Xi’an"), ("Nanjing", "Nanking"), ("Chengdu",), ("Shanghai",),
    ("Shenzhen",), ("Hangzhou",), ("Chongqing",), ("Xiamen",), ("Kunming",), ("Tianjin",),
    ("Qingdao", "Tsingtao"), ("Macau", "Macao"), ("China", "Volksrepublik China", "PRC"),
    # Asien / Pazifik
    ("Tokio", "Tokyo"), ("Osaka",), ("Seoul", "Südkorea", "South Korea", "Korea"),
    ("Bangkok",), ("Singapur", "Singapore"), ("Kuala Lumpur",), ("Hanoi",),
    ("Ho-Chi-Minh-Stadt", "Ho Chi Minh City", "Saigon"), ("Manila",), ("Jakarta",),
    ("Bali", "Denpasar"), ("Taipeh", "Taipei"), ("Delhi", "Neu-Delhi", "New Delhi"),
    ("Mumbai", "Bombay"), ("Dubai",), ("Abu Dhabi",), ("Doha",), ("Tel Aviv",),
    ("Kairo", "Cairo"), ("Sydney",), ("Melbourne",), ("Auckland", "Neuseeland", "New Zealand"),
    ("Japan",), ("Thailand",), ("Vietnam",), ("Indien", "India"), ("Indonesien", "Indonesia"),
    ("Malaysia",), ("Philippinen", "Philippines"), ("Australien", "Australia"),
    # Amerika / Afrika
    ("New York", "NYC", "New York City"), ("Los Angeles",), ("San Francisco",), ("Miami",),
    ("Chicago",), ("Washington",), ("Boston",), ("Seattle",), ("Houston",), ("Atlanta",),
    ("Toronto",), ("Montreal",), ("Vancouver",), ("USA", "Vereinigte Staaten", "United States"),
    ("Kanada", "Canada"), ("Mexiko", "Mexico"), ("Mexiko-Stadt", "Mexico City"),
    ("Johannesburg",), ("Kapstadt", "Cape Town"), ("Nairobi",), ("Südafrika", "South Africa"),
    ("São Paulo", "Sao Paulo"), ("Buenos Aires",), ("Brasilien", "Brazil"),
    ("Argentinien", "Argentina"),
    # Europa
    ("Amsterdam",), ("Lissabon", "Lisbon", "Lisboa"), ("Madrid",), ("Paris",), ("London",),
    ("Mailand", "Milan", "Milano"), ("Rom", "Rome", "Roma"), ("Athen", "Athens"), ("Budapest",),
    ("Warschau", "Warsaw", "Warszawa"), ("Prag", "Prague", "Praha"), ("Brüssel", "Brussels", "Bruxelles"),
    ("Kopenhagen", "Copenhagen", "København"), ("Stockholm",), ("Oslo",), ("Dublin",),
    ("Barcelona",), ("Porto",), ("Istanbul",), ("Luxemburg", "Luxembourg"), ("Manchester",),
    ("Helsinki",), ("Venedig", "Venice", "Venezia"), ("Lyon",), ("Nizza", "Nice"),
    ("Krakau", "Krakow", "Kraków", "Cracow"), ("Danzig", "Gdansk", "Gdańsk"),
    ("Breslau", "Wroclaw", "Wrocław"), ("Edinburgh",), ("Riga",), ("Vilnius",), ("Tallinn",),
    ("Sofia",), ("Bukarest", "Bucharest"), ("Belgrad", "Belgrade"), ("Zagreb",),
    ("Ljubljana",), ("Bratislava",), ("Reykjavik", "Reykjavík"), ("Göteborg", "Gothenburg"),
    ("Neapel", "Naples", "Napoli"), ("Bologna",), ("Turin", "Torino"), ("Florenz", "Florence"),
    ("Sevilla", "Seville"), ("Valencia",), ("Málaga", "Malaga"), ("Palma", "Mallorca", "Majorca"),
    ("Eindhoven",), ("Rotterdam",), ("Glasgow",), ("Birmingham",), ("Bergen",), ("Marseille",),
    ("Spanien", "Spain", "Spanish"), ("Italien", "Italy", "Italian"),
    ("Frankreich", "France", "French"), ("Niederlande", "Netherlands", "Holland", "Dutch"),
    ("Polen", "Poland", "Polish"),
    ("Tschechien", "Czech Republic", "Czechia"), ("Belgien", "Belgium"), ("Portugal",),
    ("Großbritannien", "UK", "United Kingdom", "England", "Britain", "British"),
    ("Dänemark", "Denmark"),
    ("Schweden", "Sweden"), ("Norwegen", "Norway"), ("Finnland", "Finland"), ("Irland", "Ireland"),
    ("Ungarn", "Hungary"), ("Griechenland", "Greece"), ("Türkei", "Turkey", "Türkiye"),
    ("Island", "Iceland"), ("Skandinavien", "Scandinavia", "Scandinavian"),
    ("Baltikum", "Baltics", "Baltic"),
    ("Kroatien", "Croatia"), ("Rumänien", "Romania"), ("Bulgarien", "Bulgaria"),
    ("Slowakei", "Slovakia"), ("Slowenien", "Slovenia"), ("Serbien", "Serbia"),
    ("Litauen", "Lithuania"), ("Lettland", "Latvia"), ("Estland", "Estonia"),
]

#: Orte, die als Abflug "zu Hause" (DACH) gelten - ergänzt um die Flughafennamen oben
HOME_PLACES: tuple[str, ...] = (
    "Deutschland", "Schweiz", "Österreich", "DACH", "Bonn", "Baden-Baden", "Münster",
    "Osnabrück", "Mulhouse", "EuroAirport",
)
#: Bekannte fremde Abflugorte/-länder (nur Namen; Codes kommen aus ``WORLD_AIRPORTS``)
FOREIGN_PLACES: tuple[str, ...] = (
    "Amsterdam", "Lissabon", "Madrid", "Paris", "London", "Mailand", "Rom", "Athen", "Budapest",
    "Warschau", "Prag", "Brüssel", "Kopenhagen", "Stockholm", "Oslo", "Dublin", "Barcelona",
    "Porto", "Istanbul", "Luxemburg", "Manchester", "Helsinki", "Venedig", "Lyon", "Nizza",
    "Krakau", "Danzig", "Breslau", "Edinburgh", "Riga", "Vilnius", "Tallinn", "Sofia", "Bukarest",
    "Belgrad", "Zagreb", "Ljubljana", "Bratislava", "Reykjavik", "Göteborg", "Neapel", "Bologna",
    "Turin", "Florenz", "Sevilla", "Valencia", "Málaga", "Palma", "Eindhoven", "Rotterdam",
    "Glasgow", "Birmingham", "Bergen", "Marseille", "Spanien", "Italien", "Frankreich",
    "Niederlande", "Polen", "Tschechien", "Belgien", "Portugal", "Großbritannien", "Dänemark",
    "Schweden", "Norwegen", "Finnland", "Irland", "Ungarn", "Griechenland", "Türkei", "Island",
    "Skandinavien", "Baltikum", "Kroatien", "Rumänien", "Bulgarien", "Slowakei", "Slowenien",
    "Serbien", "Litauen", "Lettland", "Estland",
    "Tokio", "Osaka", "Seoul", "Bangkok", "Singapur", "Kuala Lumpur", "Hanoi", "Ho-Chi-Minh-Stadt",
    "Manila", "Jakarta", "Bali", "Taipeh", "Delhi", "Mumbai", "Dubai", "Abu Dhabi", "Doha",
    "Tel Aviv", "Kairo", "Sydney", "Melbourne", "Auckland", "Japan", "Thailand", "Vietnam", "Indien",
    "Indonesien", "Malaysia", "Philippinen", "Australien", "China", "Peking", "Shanghai",
    "Hongkong", "Kanton", "Kuwait", "Riad", "Dschidda",
    "New York", "Los Angeles", "San Francisco", "Miami", "Chicago", "Washington", "Boston",
    "Seattle", "Houston", "Atlanta", "Toronto", "Montreal", "Vancouver", "USA", "Kanada", "Mexiko",
    "Mexiko-Stadt", "Johannesburg", "Kapstadt", "Nairobi", "Südafrika", "São Paulo", "Buenos Aires",
    "Brasilien", "Argentinien",
)

#: IATA-Codes, die zugleich gewöhnliche (englische/deutsche) Wörter sind - in Großbuchstaben-
#: Titeln ("YES YOU CAN!", "WAS IST LOS") zählen sie nur im Strecken-Kontext ("FRA-CAN")
AMBIGUOUS_CODES: frozenset[str] = frozenset("""
    CAN MAN SEA SIN DEL BUD WAS IST MAD GOT SHE INN LAX PAR BEG DUB MEL MIA BOS BTS LUX PAD
    SOF HAN TAO CAI MIL LON VIE ATL BOM SEL OSA
""".split())

_AIRPORT_SUFFIXES = ("Capital", "Daxing", "Pudong", "Hongqiao", "Tianfu")


def normalize_name(text: str) -> str:
    """Kleinbuchstaben, Umlaute vereinfacht: "Zürich" == "zurich" == "zuerich"."""
    text = text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def _city_names(code: str) -> list[str]:
    """"Köln/Bonn" -> ["Köln", "Bonn"]; "Peking Capital" -> ["Peking"]."""
    out = []
    for part in AIRPORT_NAMES.get(code, "").split("/"):
        part = part.strip()
        for suffix in _AIRPORT_SUFFIXES:
            if part.endswith(" " + suffix):
                part = part[: -len(suffix) - 1]
        if part:
            out.append(part)
    return out


def _build_index() -> tuple[dict[str, int], list[list[str]]]:
    """Normalisierter Name -> Gruppennummer; Gruppe = alle Schreibweisen + IATA-Codes."""
    groups: list[list[str]] = [list(g) for g in CITY_SYNONYMS]
    index: dict[str, int] = {}
    for i, g in enumerate(groups):
        for n in g:
            index.setdefault(normalize_name(n), i)
    for code in AIRPORT_NAMES:
        names = _city_names(code)
        gi = next((index[normalize_name(n)] for n in names if normalize_name(n) in index), None)
        if gi is None:
            gi = len(groups)
            groups.append(list(names))
            for n in names:
                index.setdefault(normalize_name(n), gi)
        else:
            for n in names:
                if n not in groups[gi]:
                    groups[gi].append(n)
                index.setdefault(normalize_name(n), gi)
        if code not in groups[gi]:
            groups[gi].append(code)
        index.setdefault(normalize_name(code), gi)
    return index, groups


_INDEX, _GROUPS = _build_index()
_CODE_RE = re.compile(r"^[A-Z]{3}$")


def synonyms(word: str) -> list[str]:
    """Alle Schreibweisen eines Orts inkl. IATA-Codes; unbekannte Wörter unverändert.

    >>> synonyms("Peking")   # -> ['Peking', 'Beijing', 'BJS', 'PEK', 'PKX']
    """
    key = normalize_name(word)
    gi = _INDEX.get(key)
    if gi is None:
        return [word]
    group = _GROUPS[gi]
    return [word] + [n for n in group if normalize_name(n) != key]


def codes_for(place: str) -> set[str]:
    """IATA-Codes eines Ortsnamens ("Lissabon" -> {"LIS"}, "Peking" -> {"BJS","PEK","PKX"})."""
    gi = _INDEX.get(normalize_name(place))
    if gi is None:
        return set()
    return {n for n in _GROUPS[gi] if _CODE_RE.match(n) and n in AIRPORT_NAMES}


def _place_set(names: tuple[str, ...], codes: frozenset[str] | set[str] = frozenset()) -> frozenset[str]:
    out: set[str] = set()
    for n in names:
        out.update(normalize_name(s) for s in synonyms(n))
    for c in codes:
        out.update(normalize_name(s) for s in synonyms(c))
    return frozenset(out)


_HOME_SET = _place_set(HOME_PLACES, HOME_CODES)
_FOREIGN_SET = _place_set(FOREIGN_PLACES) - _HOME_SET


def place_kind(place: str) -> str:
    """"home" (DACH), "foreign" (bekannter fremder Ort) oder "unknown"."""
    key = normalize_name(place)
    if key in _HOME_SET:
        return "home"
    if key in _FOREIGN_SET:
        return "foreign"
    return "unknown"


def is_home(code: str) -> bool:
    return code.upper() in HOME_CODES


def country(code: str) -> str | None:
    """"DE", "CH" oder "AT" für DACH-Flughäfen, sonst None."""
    code = code.upper()
    for name, table in (("DE", DE_AIRPORTS), ("CH", CH_AIRPORTS), ("AT", AT_AIRPORTS)):
        if code in table:
            return name
    return None


_TOKEN_RE = re.compile(r"[^\W\d_][\w'’-]*")


def places_in(text: str) -> set[str]:
    """Bekannte Orte in einem Text (kanonischer Name je Synonym-Gruppe, z. B. {"Bangkok", "Frankfurt"}).

    IATA-Codes zählen nur in Großschreibung ("MAN" ja, "man" nein).
    """
    tokens = _TOKEN_RE.findall(text or "")
    out: set[str] = set()
    i = 0
    while i < len(tokens):
        for n in (3, 2, 1):
            words = tokens[i:i + n]
            if len(words) < n:
                continue
            cand = " ".join(words)
            gi = _INDEX.get(normalize_name(cand))
            if gi is not None and n == 1 and cand in AMBIGUOUS_CODES:
                continue
            if gi is None or (n == 1 and len(cand) == 3 and not cand.isupper()
                              and cand.upper() in AIRPORT_NAMES
                              and normalize_name(cand) not in
                              {normalize_name(x) for x in _GROUPS[gi] if not _CODE_RE.match(x)}):
                continue
            out.add(_GROUPS[gi][0])
            i += n - 1
            break
        i += 1
    return out


def region_names(codes: list[str]) -> list[str]:
    """Namen der Regionen, die in ``codes`` vollständig enthalten sind (z. B. "Deutschland")."""
    present = set(c.upper() for c in codes)
    out: list[str] = []
    for region, members in REGIONS.items():
        if set(members) <= present:
            for n in REGION_NAMES.get(region, []):
                if n not in out:
                    out.append(n)
    return out


def place_words(codes: list[str]) -> list[str]:
    """Stichwörter für eine Code-Liste: Stadtnamen + Regionsnamen (z. B. "Deutschland")."""
    out: list[str] = []
    for c in codes:
        for n in _city_names(c.upper()) or [c.upper()]:
            if n not in out:
                out.append(n)
    for n in region_names(codes):
        if n not in out:
            out.append(n)
    return out
