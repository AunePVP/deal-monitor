# Deal-Monitor – Ausbau "Mehr Quellen, keinen Deal verpassen"

Repo: `/home/user/deal-monitor` (git, Branch `main`, Remote GitHub `AunePVP/deal-monitor`; **niemals selbst committen/pushen**).
Python ≥ 3.11, **nur Standardbibliothek** (Docker-Image ist `python:3.12-slim`, kein pip). Nutzertexte Deutsch. Kein E-Mail.
Netzwerk nach außen ist in dieser Session **offen** – Quellen dürfen und sollen live getestet werden (sparsam, siehe Limits).
Bestehende Architektur (erst lesen!): `dealmonitor/models.py` (Alert/Offer/Deal), `principles.py` (Bewertung),
`monitor.py` (`run`, `run_alert`, `best_per_slot`), `sources/__init__.py` (Protokoll `Source`: `name`, `records_history`,
`fetch(alert) -> list[Offer]`, optional `incomplete`), `sources/travelpayouts.py`, `sources/feed.py`, `store.py`,
`appsettings.py` (Settings Env > DB > Default, `SECRETS`, `VALUES`, `coerce`, `Context.sources()`), `scheduler.py`,
`web.py` (API), `static/{index.html,app.js,style.css}`, `demo.py`, `config/alerts.toml`, `config.py`,
`tests/test_monitor.py`, `tests/test_web.py`, `tests/test_fixes.py`, `tests/e2e/ui.mjs` (Playwright; `node tests/e2e/ui.mjs`).
Alle bestehenden 78 Tests müssen grün bleiben (bei API-Änderungen anpassen).

Erkenntnisse aus der Live-Prüfung (04.11.2026):
- Travel-Dealz: `https://travel-dealz.de/feed/` (20 Artikel), `…/feed/?paged=2|3` (weitere 20 je Seite),
  `https://travel-dealz.de/ticker/feed/` (**100** Einträge, alle Kategorien), `https://travel-dealz.de/kategorie/business-class/feed/`,
  Suche: `https://travel-dealz.de/?s=<Wort>&feed=rss2` (20 Treffer, entspricht deren "Schlagwortalarm").
  Kategorien: "Economy Class", "Business Class", "First Class", "Premium Economy Class", "Flüge", "Gabelflüge",
  "Kreuzfahrten", "Pauschalreisen", "Bahn", "Meilen, Punkte & Status", "Allgemein", "Bewertungen" …
  Links tragen `?utm_source=feed&utm_medium=rss&utm_campaign=rss-feed` → **utm_* beim Deduplizieren entfernen**.
- mydealz Flüge: `https://www.mydealz.de/rss/gruppe/fluege` (30), Kategorie immer "Urlaub & Reisen", Preis steht meist NICHT
  im Titel, aber am Anfang der `description` ("297€ - …", "Schon ab 298€"). Titel wie "Flüge USA / New York von München, Berlin, Hamburg…".
- Urlaubspiraten: `https://www.urlaubspiraten.de/feed` (49), keine Kategorien, Preise nicht im Titel, teils in description; meist Pauschal/Hotel.
- fly4free: `https://www.fly4free.com/feed/` (50, Englisch), Preise "from €599", "for €65", Kategorien "Europe", "Hotel", "asia deal",
  "cheap flights from copenhagen". Abflugorte meist nicht DACH ("from Prague", "from Amsterdam").
- premium-flights: `https://www.premium-flights.com/feed/` (12, Englisch, Business Class), Preise "from 1910€", "from $3,650", "from £1610";
  Kategorien "Cheap Business Class flights", "From Europe", "From USA & Canada", "Active". Nur €-Preise verwenden, $/£ ignorieren.
- reisetopia `https://reisetopia.de/feed/` (meist Hotels/Meilen; Kategorie "Deals" + "Flüge" selten) – als optionales Preset.
- travel-dealz.com / .eu = englische Kopie von .de → NICHT als Preset (Duplikate). secretflying/urlaubsguru/reisereporter: gesperrt (403/401).
- Google Flights per schlichtem HTTP-GET (kein JS nötig, kein Consent-Redirect über diese Leitung):
  `https://www.google.com/travel/flights?hl=de&curr=EUR&q=Flights%20from%20ZRH%20to%20HAM%20on%202027-01-15%20through%202027-01-19`
  (one-way: `… on 2027-01-15 one way`; Kabine: `… business class`). Mit Chrome-User-Agent und `Accept-Language: de-DE`.
  Die Antwort (~2,4 MB) enthält die Ergebniszeilen serverseitig gerendert, u. a. als `aria-label`:
  `Ab 183 Euro für Hin- und Rückflug.In diesem Preis ist kein großes Handgepäck enthalten. Nonstop-Flug mit SWISS. Abflug von Flughafen Zürich am Freitag, Januar 15, 07:00 und Ankunft an Flughafen Hamburg am Freitag, Januar 15, 08:35. Gesamtdauer: 1 Std. 35 Min..   Flug auswählen`
  und Preis-Spans `<span … aria-label="183 Euro" role="text">183 €</span>`. Umstiege: "Nonstop-Flug", "1 Zwischenstopp", "2 Zwischenstopps".
  Beispielseite gespeichert: `/tmp/claude-0/-home-user/8771aaf7-3b29-551f-a287-c88013dcf7a7/scratchpad/gf/page.html`.
  Das ist inoffiziell und kann brechen → defensiv parsen, bei 0 Treffern/Blockade (HTTP 429/302 consent) sauber `SourceError`.
- Skyscanner/Kiwi: keine frei zugängliche API (nur Partnerprogramme) → nicht umsetzen, in README ehrlich erwähnen.
- Amadeus Self-Service (https://developers.amadeus.com, kostenloser Key, Test- und Produktionsumgebung) → umsetzen, aber hier
  nicht live testbar (kein Key). Strikt nach Doku, mit Fixture-Tests, in UI als "ungetestet, Key eintragen" kennzeichnen.

## A. Feeds (sources/feed.py umbauen, abwärtskompatibel für Tests)
1. Feed-Presets (`FEED_PRESETS`, Reihenfolge = Anzeige): id, name, url(s), language ("de"/"en"), enabled-Default, Parser-Profil:
   - `travel-dealz` (Standard an): `https://travel-dealz.de/feed/`, `…/feed/?paged=2`, `…/feed/?paged=3`, `https://travel-dealz.de/ticker/feed/`,
     `https://travel-dealz.de/kategorie/business-class/feed/`
   - `travel-dealz-suche` (Standard an): pro Alarm für jedes erste Wort jeder Stichwort-Gruppe `https://travel-dealz.de/?s=<wort>&feed=rss2`
     (URL-kodiert; max. 8 Suchen pro Lauf gesamt, Cache pro Lauf; nur Wörter ≥ 4 Zeichen).
   - `mydealz-fluege` (an), `fly4free` (an), `premium-flights` (an, nur Business/First), `urlaubspiraten` (aus), `reisetopia` (aus).
   - Eigene Feeds: Setting `feeds` (Liste URLs, Profil "generisch").
2. Parser-Profil pro Preset: Preisquelle (Titel / Titel+Beschreibung), Sprache, Währungsfilter (nur €/EUR; "€599", "from €599", "1910€",
   "599 Euro", "1.299€", "ab 89,- €"; `$`, `US$`, `£`, `CHF` → kein Preis → kein Angebot), Kategorie-Logik (bestehendes `is_flight_item`
   + englische Begriffe: "Hotel", "Cruise", "Train", "Package" → kein Flug; "flight", "flights", "Business Class" → Flug).
   mydealz: Titel muss "Flug"/"Flüge"/"Flights"/Airline-Namen enthalten oder description "Flug", sonst kein Flug-Deal; Preis = erster €-Betrag
   aus description (nach "ab"/"Schon ab" bevorzugt), sonst Titel.
3. Stichwort-Abgleich mehrsprachig: `regions.py` bekommt `CITY_SYNONYMS` (de↔en): Zürich/Zurich, Peking/Beijing, München/Munich,
   Köln/Cologne, Wien/Vienna, Genf/Geneva, Nürnberg/Nuremberg, Hannover/Hanover, Deutschland/Germany, Schweiz/Switzerland,
   Österreich/Austria, Hongkong/Hong Kong, Kanton/Guangzhou, Chengdu, Shanghai, Shenzhen, Hangzhou, Chongqing, Xiamen, Kunming, Xi'an/Xian,
   Nanjing/Nanking, Tianjin, Tokio/Tokyo, New York, Bangkok … (sinnvoll erweitern). `matches()` gleicht jedes Stichwort inkl. Synonyme ab.
   Außerdem: Stichwörter matchen auch IATA-Codes im Titel ("ZRH", "HAM") und die Alarm-Codes/Regionen selbst (ein Alarm ohne
   `feed_keywords` bekommt automatische Stichwörter aus `regions.name(code)` seiner Von/Nach-Codes bzw. Regionsnamen → `auto_keywords(alert)`;
   Von-Wort UND Nach-Wort müssen vorkommen, bei Regionen reicht ein Mitglied). Bestehendes Airline-Rauschen (`AIRLINE_NOISE_RE`) beibehalten.
4. Abflugort-Erkennung (`detect_origin(title, lang)`): "von <Stadt>", "ab <Stadt>", "from <City>" → Stadt; DACH-Städte/Länder = `home`,
   bekannte fremde Städte (Amsterdam, Lissabon/Lisbon, Madrid, Paris, London, Mailand/Milan, Rom/Rome, Athen/Athens, Budapest, Warschau/Warsaw,
   Prag/Prague, Brüssel/Brussels, Kopenhagen/Copenhagen, Stockholm, Oslo, Dublin, Barcelona, Porto, Istanbul, Luxemburg, Manchester, Helsinki …)
   = `foreign`, sonst `unknown` (Travel-Dealz-Prinzip: ohne Angabe = Abflug Deutschland → für travel-dealz-Profile `unknown` ⇒ `home`).
   Ein Feed-Angebot für einen Alarm mit DACH-Abflughäfen wird verworfen, wenn `foreign` erkannt wurde (z. B. "Bonaire ab 416€ von Amsterdam"
   passt nicht zu `@DE`), es sei denn der Alarm hat den Ort selbst als Origin.
5. Dedup über alle Feeds per normalisiertem Link (ohne utm_*, ohne Fragment, ohne trailing slash). Offer.source = `feed:<preset-id>`
   (z. B. `feed:travel-dealz`); Anzeigename in UI/Report via `SOURCE_LABELS` ("Travel-Dealz-Artikel", "mydealz", "fly4free", …).
   `records_history=False`. Ein Feed-Fehler darf andere Feeds nicht stoppen (wie bisher `incomplete`).
6. Tests mit echten Fixture-Ausschnitten (die Agenten laden die Feeds live und speichern gekürzte XML-Fixtures mit ≤ 8 Items je Feed
   unter `tests/fixtures/feeds/<preset>.xml`; Beispiel-Deals-Fixture `travel-dealz-feed.xml` bleibt für Demo/Tests).

## B. Radar – "keinen krassen Deal verpassen" (neues Modul `dealmonitor/radar.py`)
Unabhängig von den Alarmen werden ALLE Flug-Artikel aller aktiven Feeds geprüft. Treffer werden wie Deals gespeichert (alert_id = `radar`,
alert_name "Radar: Hammer-Deals") und gemeldet. Regeln (Einstellungen, Env `RADAR_*`):
- `radar_enabled` (Default true)
- Immer melden: Titel/Kategorie enthält `Error Fare|Fehlerpreis|Errorfare|Hammer|Kracher|Knaller|Wahnsinn|Preisfehler|Mistake fare|price error`.
- `radar_economy_max` (Default 300 €): Flug-Artikel Economy mit Preis ≤ Grenze (nur Langstrecke ist nicht erkennbar → Grenze bewusst niedrig;
  zusätzlich: Preise ≤ 40 € für Economy ignorieren, das sind Kurzstrecken-Promos wie "Ryanair ab 14,99€", außer der Nutzer hebt `radar_economy_min` an/ab).
- `radar_business_max` (Default 1.300 €), `radar_first_max` (Default 2.500 €), `radar_premium_max` (Default 700 €).
- `radar_home_only` (Default true): nur Abflug `home` oder `unknown` (siehe A4); false = auch fremde Abflughäfen.
- Keine Dopplung: Ein Artikel, der bereits einem Alarm zugeordnet wurde, erscheint nicht zusätzlich als Radar-Treffer (gleicher Lauf).
- Radar-Deals bekommen `rating` "Radar" (Badge eigene Farbe, orange/amber) und `reasons` wie "Error Fare laut Überschrift" / "Business Class ≤ 1.300 €".

## C. Google Flights (neues `sources/googleflights.py`)
- `GoogleFlightsSource(max_requests=40, delay=3.0, verify_only=False)`; `records_history=True` (echte Live-Preise → beste Referenz).
- URL wie oben (q=…), Headers: Chrome-UA, `Accept-Language: de-DE,de;q=0.9`, `Accept: text/html`. Timeout 30 s. **Zwischen Anfragen 2–4 s**
  (zufällig), pro Lauf höchstens `max_requests` Anfragen (Setting `google_max_requests`, Default 40). Bei HTTP 429/403 oder Consent-/Captcha-Seite:
  sofort `SourceError("Google Flights blockiert gerade – später erneut")` und `_down` für den Lauf (wie Travelpayouts-Schutzschalter).
- Scan-Modus pro Alarm: pro Strecke und Monat (`scan_months`) **2 Datumspaare**: ein Freitag-Abflug und ein Dienstag-Abflug jeweils Mitte des
  Monats; Rückflug = Abflug + Aufenthalt (Mitte aus min/max_nights, Default 7 Nächte Kurzstrecke/14 Langstrecke (Strecke > 3.000 km? →
  einfach: Ziel außerhalb Europa-Regionen = 12 Nächte), one-way ohne Rückflug). Die Zahl der Anfragen = Strecken×Monate×2 → wird über
  `max_requests` gedeckelt: Strecken in zufälliger Reihenfolge, Monate nah → fern; nicht abgefragte Kombinationen in `incomplete` melden
  (Message "Google Flights: X von Y Abfragen (Limit)"), **nichts** davon als abgelaufen markieren (bestehende Logik `incomplete.routes`).
- Parser (`parse_results(html) -> list[dict]`): aus den `aria-label`-Texten `Ab (\d[\d.]*) Euro`, "Nonstop"/"(\d) Zwischenstopp", Airline
  ("Flug mit (.+?)\." bzw. "Nonstop-Flug mit X"), Gesamtdauer "(\d+) Std. (\d+) Min.", Abflugzeit. Preis auch aus `<span aria-label="183 Euro">`.
  Ergebnisse dedupen, nach Preis sortieren, günstigste 10 je Abfrage als Offers (origin/destination/departure/return/price/airline/stops/
  duration_out_min; return-Dauer unbekannt → None; link = die Google-URL (Gegenprüfen = Buchen-Link)).
  Fixture: gekürzter Ausschnitt der Beispielseite (nur die relevanten `<span aria-label=…>`/Ergebnisblöcke, ≤ 150 kB) → `tests/fixtures/google_flights_zrh_ham.html`.
- Live-Test im Build: höchstens 3 echte Abfragen (ZRH→HAM Januar, FRA→SHA März one-way, FRA→BJS Business).
- Verifikation (Setting `google_verify`, Default true, wenn Google-Quelle aktiv): In `monitor.run_alert` werden Deals aus **gecachten** Quellen
  (`travelpayouts`, `amadeus-dates`) vor dem Speichern/Melden mit genau diesen Daten bei Google gegengeprüft (zählt auf `max_requests`):
  Google-Preis vorhanden und ≤ Deal-Preis × 1,25 → Deal bestätigt (`reasons` + "Google Flights bestätigt: 92 €", Preis = Google-Preis,
  wenn Google günstiger: Google-Preis übernehmen); Google-Preis > × 1,25 → Deal verwerfen (Log + `runs.errors` zählt nicht als Fehler, sondern
  `RunResult.rejected` neu); Google nicht erreichbar/blockiert → Deal trotzdem melden mit Hinweis "nicht gegengeprüft".
  Verifikationen pro Lauf ≤ `google_max_requests / 2`.

## D. Amadeus (neues `sources/amadeus.py`, ungetestet live)
- Secrets `amadeus_api_key`, `amadeus_api_secret` (Env `AMADEUS_API_KEY/SECRET`), Setting `amadeus_env` ("test"|"production", Default "test"),
  Basis `https://test.api.amadeus.com` / `https://api.amadeus.com`. OAuth2: `POST /v1/security/oauth2/token`
  (`grant_type=client_credentials&client_id=…&client_secret=…`, form-urlencoded) → `access_token`, `expires_in` (cache im Objekt).
- Monats-Scan: `GET /v1/shopping/flight-dates?origin=FRA&destination=PEK&departureDate=2027-03-01,2027-03-31&oneWay=false&duration=5,30&nonStop=false&viewBy=DATE`
  → `data[]: {departureDate, returnDate, price.total}`; Antwort-`meta.currency` beachten (EUR verlangen; sonst Offer verwerfen).
  Quelle `amadeus-dates`, `records_history=True`, Kabine: nur Economy (Endpunkt kennt keine Kabine) → für business/first-Alarme stattdessen
  `GET /v2/shopping/flight-offers?originLocationCode=&destinationLocationCode=&departureDate=&returnDate=&adults=1&travelClass=BUSINESS&nonStop=false&currencyCode=EUR&max=10`
  für 2 Datumspaare pro Monat (wie Google) → `data[].price.grandTotal`, `itineraries[].segments[]` (carrierCode, numberOfStops, departure.at),
  `itineraries[].duration` ("PT12H30M" → Minuten). Quelle `amadeus-offers`.
- Fehler: 401 (Key) → `SourceError("Amadeus lehnt den Key ab")` sofort; 429 → Schutzschalter; `errors[].detail` in Meldung.
- Setting `amadeus_max_requests` (Default 100 pro Lauf). Fixtures: realistische JSON-Antworten nach Doku unter `tests/fixtures/amadeus_*.json`.

## E. Integration
1. `appsettings.py`: neue VALUES/SECRETS (A–D), `sources`-Liste erweitert um `google_flights`, `amadeus`, `feeds` (bisher `feed`; Alias beibehalten),
   Default `["travelpayouts","google_flights","feeds"]`; `feed_presets` (Liste aktiver Preset-IDs, Default = Presets mit enabled-Default),
   `feed_interval_minutes` (Default 30, min 10), `radar_*`, `google_*`, `amadeus_*`. `Context.sources(names)` baut die Quellen; Feed-Quelle
   bekommt Presets + eigene Feeds + Suche. Demo (`demo.py`) liefert Fixture-Quellen auch für `google_flights` (kleine Fixture-Antwort für ZRH-HAM)
   und Feeds (bestehende Beispiel-Fixture + 2 Radar-Beispiele: ein Error-Fare-Artikel, ein Business ≤ 1.300 €).
2. `scheduler.py`: zwei Takte – voller Lauf alle `interval_hours` (alle Quellen), Feed-Lauf alle `feed_interval_minutes` (nur Feeds + Radar,
   `trigger="feeds"`), beide nie gleichzeitig (Lock), Status `next_run` + `next_feed_run`. `POST /api/run` Body `{alert_id?, mode?: "full"|"feeds"}`.
3. `monitor.py`: Radar-Lauf nach den Alarmen (B6 Dedup), Google-Verifikation (C), `RunResult.rejected`, Radar-Zahlen in Lauf-Statistik.
   `best_per_slot` berücksichtigt Quelle; Deals derselben Strecke/Monat aus verschiedenen Quellen: nur den günstigsten **bestätigten** melden.
4. `web.py`/Frontend:
   - Einstellungen → "Quellen": Karten je Quelle mit Schalter + Status: Travelpayouts (Token), **Google Flights** (Schalter, max. Abfragen/Lauf,
     Gegenprüfen-Schalter, Hinweis "inoffiziell, kann zeitweise blockiert sein"), **Amadeus** (Key/Secret als Secrets, Umgebung test/production,
     Hinweis "kostenlos registrieren, hier noch nicht live getestet"), **Deal-Seiten** (Checkbox je Preset mit Name + Link, eigene Feed-URLs,
     Feed-Intervall Minuten).
   - Einstellungen → "Radar": Schalter, Grenzen €, "nur Abflug DACH".
   - Deals: Filter-Chip "Radar" mit Zähler; Badge "Radar"; Quelle als Klartext (`SOURCE_LABELS`); bei bestätigten Deals Hinweis "✓ Google Flights bestätigt".
   - Status: "nächster Feed-Check in …", `configured.google_flights/amadeus/feeds`.
   - `/api/status.counts.radar_deals`, `/api/sources` (Liste: id, name, enabled, configured, last_error aus letztem Lauf) optional.
5. README: Abschnitt "Quellen" neu (Tabelle: Quelle, was sie liefert, Kosten/Key, Stand), ehrlich zu Google (inoffiziell) und Amadeus (ungetestet),
   Skyscanner/Kiwi (keine freie API). `.env.example`, `unraid/deal-monitor.xml` (keine neuen Pflichtfelder; Secrets gehören in die UI).
6. Tests: `tests/test_feeds.py`, `tests/test_google.py`, `tests/test_amadeus.py`, `tests/test_radar.py`, Erweiterung `test_web.py`
   (neue Settings, /api/run mode), E2E-Schritte (Quellen-Einstellungen, Radar-Chip). Alles: `python3 -m unittest discover -s tests` grün,
   `node tests/e2e/ui.mjs` grün.
7. Abschließender Live-Lauf (nicht im Test, nur Prüfung): `python3 -m dealmonitor --data <tmp> run --dry-run --sources feeds` mit den
   Standard-Alarmen + Radar → Ausgabe prüfen, dass echte Artikel sinnvoll zugeordnet werden (keine Kreuzfahrten, keine Fremdabflüge bei @DE).
