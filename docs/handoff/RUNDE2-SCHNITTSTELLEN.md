# Runde 2 – Schnittstellen (Paket 0)

Stand nach Paket 0. Grundlage: `RESEARCH.md` Abschnitt 3.0. Alles hier ist getestet in
`tests/test_foundation.py`. Nur additiv: ohne die neuen Schlüssel/Felder verhält sich alles wie vorher.

## 1. Neue Quelle einhängen (Loader-Preset)

```python
# dealmonitor/sources/<quelle>.py
from . import SourceError

def fetch_items(*, timeout: float = 20.0, state: dict | None = None) -> list[dict]:
    state = state if state is not None else {}
    ...                       # Fehler -> raise SourceError("kurzer Text")
    state["cursor"] = "…"     # Zustand ändern; JSON-tauglich (Datum als ISO-Text)
    return [ {"title": …, "link": …, "published": datetime_utc, "price_eur": 471.0, …} ]
```

Registrieren in `dealmonitor/sources/feed.py` → `FEED_PRESETS` (gehört **P3**):

```python
Preset("secretflying", "Secret Flying", (), "en", True, Profile(lang="en"),
       "https://www.secretflying.com/", "Kurzbeschreibung",
       loader="secretflying:fetch_items", lane="fast")
```

- `loader`: `"modul:funktion"`; ohne `dealmonitor.`-Präfix liegt das Modul unter `dealmonitor.sources`.
  Import erst beim Abruf. Modul fehlt / Funktion fehlt → `SourceError` nur für dieses Preset.
- `urls` bleibt `()`. `Preset.jobs` = URLs bzw. `(loader,)`.
- `lane`: `"fast"` | `"normal"` (Standard). `FeedSource(..., lanes={"fast"})` lädt nur schnelle
  Presets (ohne eigene Feeds und ohne Suche); `lanes=None` = alle. Den Takt baut **P6**.
- `SOURCE_LABELS["feed:<id>"]` ergänzen (sonst zeigt die UI die ID). Ein neues Preset
  ändert `test_feeds.PresetTest.test_presets_and_defaults` (Liste der IDs) – das ist gewollt.
- Achtung: Wer `feed_presets` schon gespeichert hat, bekommt neue Standard-Presets nicht
  automatisch (Einstellungs-Migration in `appsettings.migrate_settings`, falls gewünscht: P3/P6).

### Fehler und Zustand

- Jede Ausnahme des Loaders (auch unerwartete) wird gefangen: Preset gilt als fehlgeschlagen
  (`load_errors`, `unreliable_sources()`), andere Presets laufen normal weiter.
- `state` kommt aus `store.source_state(preset.id)` (Kopie). `FeedSource` speichert ihn **nur nach
  fehlerfreiem Abruf** und nur bei Änderung zurück (`store.set_source_state`). Ohne Store (`state_store=None`,
  z. B. in Tests oder der CLI) bekommt der Loader `{}`.
- `appsettings.sources()` übergibt den Lauf-Store als `FeedSource(..., state_store=store)`.

## 2. Item-Format

Pflicht (wie `FeedSource.parse`): `title, link, published (datetime UTC|None), categories (list[str]),
feed, description, description_text, merchant_price`. `feed.normalize_item(raw, feed, max_age_days=…)`
ergänzt Fehlendes, prüft Typen und wird für Loader-Items automatisch aufgerufen.

Optional (werten `normalize_item` und `analyze()` aus):

| Schlüssel | Typ | Wirkung |
|---|---|---|
| `price_eur` | float > 0 | Preis; Vorrang vor Titel-Regex; dann keine Kabinen-Nebenpreise (`alt_prices` leer) |
| `origin_codes` | set/list[str] IATA | Vorrang vor `detect_origin`: `home`, wenn ein Code in DACH liegt, sonst `foreign` |
| `dest_codes` | set/list[str] IATA | `ItemInfo.dest_codes`; im Alarm-Abgleich Treffer auch ohne Stichwort, wenn Ziel passt **und** Abflug belegt ist (`origin_codes ∩ alert.origins` oder DACH-Regionsalarm ohne Abflugangabe) |
| `cabin` | economy/premium/business/first | Vorrang vor Titel/Kategorie |
| `oneway` | bool | Vorrang vor `is_oneway(title)` |
| `expires` | datetime/ISO-Text (naiv = UTC) | abgelaufen → `normalize_item` verwirft, `analyze().flight = False` |
| `hot` | str (`ERROR FARE`, `CRAZY HOT`, …) | wird als Kategorie angehängt (für `radar.hot_reason`), zählt aber nicht für Flug-Erkennung/Stichwörter |
| `uid` | str | Dedup-Schlüssel, wenn kein Link da ist (`key = link or "uid:…" or title`); landet in `Offer.uid` |

Ungültige Werte werden still verworfen (nie Absturz). Unbekannte Schlüssel bleiben im Item.

`ItemInfo` hat zusätzlich: `dest_codes, expires, hot, uid`.

## 3. `FeedSource.stats` (für P6 Gesundheit)

Nach `load()` (und je Suche): `stats["feed:<id>"] = {"items": int, "parsed": int,
"newest": datetime|None, "error": str|None}`. `items` = geladene Artikel der Quelle (vor Dedup),
`parsed` = davon Flug-Deals mit €-Preis, `newest` = jüngstes `published`, `error` = Fehlertexte
(`; `-getrennt, auch bei teilweisem Ausfall mehrerer URLs).

## 4. `Offer`-Zusatzfelder (`models.py`)

Alle Default `None`, `compare=False`, ändern den Fingerprint nicht:
`insight: dict` (P2), `via_hub: str`, `surcharge: float` (P4), `expires: datetime`, `hot: str`, `uid: str`.

- `Offer.extras()` → Dict nur mit gesetzten Werten (datetime als ISO-Text). `models.EXTRA_FIELDS` listet sie.
- Durchgereicht in: Deal-Payload (`store.upsert_deal` → `store.deals()` → `/api/deals`) und
  Bestpreise (`monitor._entry` → `/api/alerts[].cheapest`). Alte Zeilen ohne die Felder laden
  unverändert (Schlüssel fehlt dann einfach).
- `FeedSource.fetch` und `radar._offer` setzen `expires`, `hot`, `uid` aus dem Item.
- Neue Felder: in `models.EXTRA_FIELDS` aufnehmen, JSON-tauglich halten.

## 5. Quellen-Zustand im Store

`store.source_state(name) -> dict` (leer, wenn nichts/unbrauchbar), `store.set_source_state(name, dict)`.
Schlüssel `kv["state:<name>"]` – kollidiert nie mit Einstellungen. Namenskonvention: Preset-ID
(`secretflying`, `travel-dealz-rest`); P6 nimmt `state:health`, Wechselkurse liegen in `kv["fx_rates"]`.

## 6. Währungen (`dealmonitor/currency.py`)

- `to_eur(amount, cur) -> float | None` (2 Nachkommastellen), feste EZB-Kurse vom 04.11.2026
  (`FIXED_RATES`): `to_eur(291, "USD") == 258.28`. `cur` darf ISO-Code oder Symbol sein (`$`, `US$`,
  `£`, `CHF`, `A$`, `zł` …; `code_for`). Unbekannt/ungültig → `None`.
- Optional Tageskurse: `refresh_rates(store)` lädt `eurofxref-daily.xml` höchstens alle 24 h, speichert in
  `kv["fx_rates"]`, Fehler → feste Kurse bleiben. `load_rates(store)` übernimmt gespeicherte Kurse ohne Netz.
  **Noch nirgends aufgerufen** – Einbau in den Takt bei Bedarf durch P6.

## 7. Dateibesitz in der parallelen Phase

| Paket | Dateien |
|---|---|
| P1 | `sources/secretflying.py` |
| P2 | `sources/google_insights.py`, `sources/googleflights.py` |
| P3 | `sources/feed.py` (Presets, `SOURCE_LABELS`, Parsing) |
| P4 | `regions.py`, `hubs.py`, `feed.origin_ok`, `radar.check`, `appsettings.py` (Hub-Specs) |
| P5 | `sources/traveldealz_rest.py` |
| P6 | `health.py`, `scheduler.py`, `notify.py`, `appsettings.py` (Takt), `web.py`-Route `/api/sources/health` |
| P10 | `sources/travelpayouts.py`, `sources/amadeus.py` |
| P11 | `sources/errorfarealerts.py` |

Jedes Paket bringt eigene Tests (`tests/test_<paket>.py`) mit. Loader-Pakete (P1, P5, P11) registrieren
**nicht** selbst in `feed.py`, sondern nennen P3 bzw. der Integration Preset-Zeile, Label und Lane.
`models.py`, `store.py`, `currency.py` und der Loader-Teil von `feed.py` (Preset, `load_loader`,
`normalize_item`, `analyze`, `stats`, `_fetch_loader`) sind nach Paket 0 eingefroren – Änderungswünsche
als Hinweis an die Integration.

## 8. Deal-Bewertung (Paket 8)

Getestet in `tests/test_scoring.py`. Alles zusätzlich; alte Zeilen ohne die Felder laden unverändert.

- **Bezugspreis** (`principles.reference`): eigene Historie ≥ `min_history_samples` → Median; sonst
  `Offer.insight` (Median von `history` ab 3 Punkten, sonst `typical`); sonst `baseline_price`.
  Herkunft in `Deal.reference_source` (`REF_GOOGLE_HISTORY`, `REF_GOOGLE_TYPICAL`, …).
- **Gründe** (`Deal.reasons`, Reihenfolge): Nachbar-Hub, Fehlerpreis-Hinweise
  (`Verdacht auf Fehlerpreis: …`, `Preissturz: …`), Grenze/Referenz, Googles Einordnung
  (`31% unter typischer Spanne (195 €–275 €)`, `Google: Preise derzeit niedrig (67 € unter Normalpreis)`),
  ggf. `Erstmeldung Secret Flying, 19 h vor ErrorFareAlerts`.
  `principles.suspected_error_fare(reasons)` → ntfy-Priorität 5, Webhook `error_fare: true`.
- **Kurzbegründung** `principles.why(reasons)` → Deal-Payload `why` (`/api/deals`), Meldungstext
  `Warum: …`, Webhook `deals[].why`.
- **Erstmeldung** (`dealmonitor/sightings.py`, Tabelle `sightings`): nur Artikel. Deal-Payload
  `sources` = `[{source, label, seen, price, link}]` (früheste zuerst, ab 2 Seiten) und `lead` (Text).
  Spätere Meldungen derselben Deals werden nicht gespeichert und nicht gemeldet.
- **Beinahe-Deals**: `/api/alerts[].near_deals` = Liste (höchstens 5, je Strecke einer) aus den
  Bestpreisen mit `near: [Gründe]`, z. B. `["61 € über deiner Grenze", "7% unter typischer Spanne (600 €–1.050 €)"]`.
  Nie gemeldet. Gespeichert als Feld `near` im Bestpreis-Eintrag (`store.cheapest`).
