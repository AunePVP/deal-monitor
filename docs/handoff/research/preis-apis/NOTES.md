# Rohnotizen "preis-apis" (Stand 2026-11-04, alle Tests live per curl aus dieser Umgebung)

## Live-Tests (HTTP-Status / Format)
- Ryanair farfnd v4 oneWayFares HAM: 200 JSON, 7 Fares (1 je Ziel), z.B. HAM->ALC 16.11.2026 20,99 EUR, priceUpdated 04.11.2026 (Dateien ryanair_*.json)
- Ryanair roundTripFares HAM: 200, 7 Fares (HAM<->GDN 37,98 EUR total); ZRH: 200 aber leer (nicht bedient); BSL oneWay: 3 Fares (BSL->ZAG 18,99)
- Ryanair Airports-Liste: 222, DACH: BER BRE BSL CGN FDH FKB FMM FMO HAM HHN KLU KSF LBC LNZ NRN NUE PAD SCN SZG VIE
- Travelpayouts api.travelpayouts.com: v1/prices/calendar, v1/prices/monthly, v1/prices/cheap, aviasales/v3/grouped_prices, aviasales/v3/get_special_offers, v1/city-directions, v2/prices/month-matrix, v2/prices/week-matrix, v2/prices/nearest-places-matrix -> alle HTTP 401 "Unauthorized" (Endpunkte existieren; kein Token in dieser Umgebung)
- Amadeus: test.api.amadeus.com -> CONNECT 502 (Host antwortet nicht); api.amadeus.com -> 401 (Enterprise-Gateway). Self-Service am 17.07.2026 abgeschaltet (PhocusWire 09.02.2026, dev.to, GitHub-Org archiviert)
- Lufthansa Open API: developer.lufthansa.com/docs 200; Fares nur im "Partner API" (BestPrice Lowest Fares etc.); api.lufthansa.com/v1/offers/lowestfares -> 596 Service Not Found; /v1/operations/schedules -> 401 Account Inactive
- SerpApi: pricing 200 (Free 250 Suchen/Monat, 50/h); search.json?engine=google_flights -> 401 Invalid API key (lebt); Engines google_flights, google_flights_calendar, google_flights_deals
- Duffel: pricing 200 (Suchen gratis nur bis 1500:1 Such/Buchungs-Verhaeltnis, sonst 0,005 USD/Suche; Live-Modus erst nach Verifizierung); api.duffel.com -> 401 (lebt)
- Kiwi Tequila: tequila.kiwi.com 200 (nur Login), partners.kiwi.com: seit 30.05.2024 nur auf Einladung
- Skyscanner: developers.skyscanner.net 403; Partner-Support-Artikel: >100k monatl. Traffic, keine Privatpersonen
- RapidAPI sky-scrapper / google-flights2 / booking-com15: 200, Preisstaffeln nur per JS (nicht lesbar)
- Trip.com: partners/affiliate 200 (JS-Shell, "bis 7% Provision"), pages.trip.com/intl/en/partner 404, de.trip.com/flights/deals 200 (1,9 KB JS-Shell)
- Wizz Air: be.wizzair.com/29.23.1/Api/asset/map 200 JSON (198 Staedte; HAM 14 Verbindungen, ZRH/VIE nicht); POST Api/search/timetable -> 400 {"validationCodes":["InvalidMarket"]}
- easyJet: 403 Akamai; Eurowings: 308/403 Cloudflare; SWISS/Edelweiss/LH.com: 403 Akamai; Norwegian: 403 "Are you human?"; Kayak explore: 302 -> /s/ident/forbidden 403
- map.aviasales.ru/prices.json: 302 auf Startseite; map.aviasales.com: TLS-Zertifikat passt nicht
- azair.eu azfin.php: 200 HTML (28 KB) aber nur Kalender, keine Ergebnisse serverseitig
- flug.idealo.de: 200 HTML, Teaser-Deals (BER->SKP ab 58 EUR), keine API
- airchina.de/offers 404; de.ceair.com 200 Shell; hainanairlines.com 200 Shell; cathaypacific Angebote 404

## Travelpayouts-Doku (travelpayouts.github.io/slate + readthedocs; support.travelpayouts.com 403 von hier)
- v1/prices/calendar: "cheapest non-stop, one-stop, two-stop flights for the selected route for each day of the selected month" (ohne return_date = one way) -> ein Preis pro Tag
- v2/prices/month-matrix: Preise je Tag eines Monats, gruppiert nach Umstiegen (month=YYYY-MM-01)
- v2/prices/week-matrix: Preise fuer Nachbardaten; v2/prices/nearest-places-matrix: Nachbarflughaefen (limit 1-20)
- v1/prices/monthly: guenstigster je Monat; v1/prices/cheap: destination "-" = alle Ziele ab origin (100/Seite)
- v1/city-directions: beliebte Ziele ab Stadt mit je einem Preis
- v2/prices/latest: origin/destination = IATA-Stadt ODER Laendercode (2 Zeichen, z.B. DE/CN)! limit bis 1000, period_type=year = alles aus 48h, show_to_affiliates=false = ALLE Preise (true = nur mit Partner-Marker). Projekt nutzt derzeit month/30/true.
- v2/prices/special-offers: Airline-Sonderangebote (XML); neuere JSON-Variante aviasales/v3/get_special_offers (401 -> existiert)
- Alle Daten aus Cache (Nutzersuchen, 48h), expires_at beachten, Default-Waehrung RUB -> currency=eur setzen
