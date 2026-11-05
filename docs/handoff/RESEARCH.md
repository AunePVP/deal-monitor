# Recherche-Bericht: Quellen für den Deal-Monitor

Stand: 05.11.2026. Grundlage sind `agent-results/recherche.json` (Blickwinkel preis-apis und dach-deal-seiten) und `agent-results/recherche-teil2.json`. Teil 2 umfasst 8 Blickwinkel, die Kritik und 6 Nachrecherchen zu den Lücken.

Alle Status-Angaben stammen aus Live-Tests aus der Cloud-Umgebung (Proxy-IP, curl mit Chrome-User-Agent, höchstens 2 Abrufe pro URL). Vom Unraid-Heimnetz aus kann einzelnes anders antworten. Die Rohdaten liegen unter `docs/handoff/research/<kürzel>/`.

Vor dem Schreiben habe ich die Hauptempfehlungen noch einmal abgerufen (05.11., ca. 10:50 UTC). Alle lieferten HTTP 200: Bluesky-API Secret Flying, TD-REST mit China-Blatt-IDs, `premium-flights.com/tag/china/feed/`, `reisetopia.de/feed/`, YHBU-Kategorie und ErrorFareAlerts.

Kennzeichnung: **UNBESTÄTIGT** heißt, nicht live gesehen oder nur abgeleitet.

---

## 1. Kurzfassung: was uns wirklich fehlt

1. **Erstmelder fehlt.** Secret Flying ist über die öffentliche **Bluesky-API** erreichbar, ohne Login und ohne Cloudflare. Seine DACH-Deals (ZRH–Mauritius 471 €, FRA–Phuket 496 €, ZRH–Johannesburg 471 €) standen dort am 04.11., ErrorFareAlerts brachte sie erst **19–20 h später**. Bei travel-dealz fehlen sie ganz. → Paket 1
2. **Bezugspreis fehlt.** Jede Google-Flights-Seite, die wir ohnehin laden, enthält in `ds:1` Preisniveau, Normalpreis, typische Spanne und **62 Tage Preisverlauf**. Das gilt auch für One-way und Business. Damit lösen wir den Kaltstart und können „X % unter typisch“ angeben. → Paket 2
3. **Google verliert heute 14–25 % der Abrufe.** Gründe sind die leere Suchmaske (ca. 1,95 MB) und `ds:1`-ErrorResponse. Der zweite Versuch nach 2–4 s klappt. Ohne Retry werden diese Abrufe als Fehler gezählt. → Paket 2
4. **China-Business-Deals werden stumm verworfen.** Fast alle Treffer unter 1.999 € starten in BUD, LUX, ROM, OSL oder BCN. `feed.origin_ok()` wirft `origin="foreign"` weg. Abhilfe ist eine Klasse „Nachbar-Hub“ mit Zubringer-Aufschlag. → Paket 4
5. **Vorhandene Seiten werden falsch gelesen.** Bei reisetopia steht der Preis nur in `content:encoded` (das Preset ist aus). Bei YHBU verwirft das Standardprofil 11 von 20 China/Asien-Titeln. Die premium-flights-Tag-Feeds `china`, `pek` und `shanghai` fehlen, ebenso der Host ohne `www`. → Paket 3
6. **Für ZRH⇄HAM gibt es keine redaktionelle Deal-Quelle.** Die Abdeckung kommt nur aus Preisdaten. Tokenfrei geht das über eine Google-Rotation: 90 Tage × 2 Richtungen in 24 h, 23 Abrufe pro Lauf. Mit Token des Nutzers kommt der Travelpayouts-Kalender dazu. → Paket 7 und Paket 10
7. **Der Takt ist zu träge, Ausfälle bleiben unsichtbar.** Feeds laufen alle 30 min, Preise alle 3 h. Leichte Quellen (TD-REST 1–8 KB, Bluesky 14–100 KB) können alle 10 min laufen. Eine Quelle mit HTTP 200 und 0 Treffern sieht heute aus wie „keine Deals“. → Paket 5 (TD-REST) und Paket 6 (Takt und Gesundheit)

Ehrliche Grenze: 100 % gibt es nicht. Business-Deals von Secret Flying sind Premium und erscheinen nur als Teaser ohne Preis. Die Angebotsseiten der Airlines sind alle 403 oder Captcha. 7 Airline-Seiten waren von hier gar nicht prüfbar.

---

## 2. Alle geprüften Quellen (nach Wert sortiert)

Die Spalte **Wert** zeigt die abschließende Bewertung von 1 bis 5. 5 heißt: fängt Deals, die wir heute verpassen. Wo Kritik oder Nachrecherche die Bewertung geändert haben, steht der ursprüngliche Wert dahinter („war X“).

Mehrfachnennungen sind Absicht. Dieselbe Quelle wurde aus verschiedenen Blickwinkeln geprüft, die Zeilen unterscheiden sich in Status und Details. Alle Felder in voller Länge stehen in den JSON-Dateien.

| Wert | Empf. | Quelle | URL | Status (live 05.11.) | Format | DACH | Preis | Aktualität | Aufwand | Blickwinkel |
|---|---|---|---|---|---|---|---|---|---|---|
| 5 | ja | Google Flights Ergebnisseite – Preisinformationen / Preisniveau + typische Spanne + Preis… | `https://www.google.com/travel/flights?hl=de&curr=EUR&q=Flights%20from%20ZRH%20to%20HAM%20on%202027-01-15%20th…` | HTTP 200, 265 KB gzip (2,4 MB entpackt), 3 von 3 Abrufen ok, kein Consent/Captcha | HTML mit eingebettetem JSON (AF_initDataCallback key 'ds:1') | ja, beliebiger IATA-Code, frei wählbar | ja, aria-label 'Ab 183 Euro für Hin- un… | live; Preisverlauf 04.09.-04.11.2026 (62 Tag… | niedrig | tools-automatisierbar |
| 5 | ja | Google Flights Preis-Insights (ds:1) und 62-Tage-Verlauf | `https://www.google.com/travel/flights?hl=de&curr=EUR&q=Flights%20from%20FRA%20to%20PEK%20on%202027-03-10%20th…` | HTTP 200, live am 05.11.2026 getestet (FRA-PEK Economy und Business, ZRH-HAM, 10 Nachbar-/Positionierungsstre… | HTML mit AF_initDataCallback key ds:1, JSON-Block bei ds:1[… | ja, beliebiger IATA-Abflug | ja, im Block selbst: niedrigster Preis,… | live; Verlauf 62 Tage bis heute | mittel | eigene-ideen |
| 5 | ja | Google Flights one-way je Datum (ds:1, Rotation) | `https://www.google.com/travel/flights?hl=de&curr=EUR&q=Flights%20from%20ZRH%20to%20HAM%20on%202027-01-14%20on…` | 200 (12/14 ok; 2/14 ds:1 ErrorResponse, Retry ok) | HTML mit AF_initDataCallback; key 'ds:1' JSON, Preise zusät… | ja, frei wählbar (ZRH/HAM, beide Richtungen) | nein (Feld d[5][1] und HTML) | live (Preise zum Abrufzeitpunkt), Preisverla… | mittel | luecke-3 |
| 5 | ja | Secret Flying Bluesky AuthorFeed API (JSON) | `https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor=secretflying.bsky.social&limit=50&filter=p…` | 200 (2 Abrufe, ohne Login, 103 KB bei limit=50) | JSON (AT-Protokoll). feed[].post.record: createdAt (ISO UTC… | 8 von 150 Posts in 6 Tagen (ca. 5 %, ca. 1,3 pro Tag): ZRH-… | Ja, im Text: 'for only €496 roundtrip' … | Neuester Post 05.11.2026 10:27 UTC (Amsterda… | niedrig | luecke-1 |
| 5 (war 4) | ja | Secret Flying via Bluesky (öffentliche API) | `https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor=secretflying.bsky.social&limit=100&filter=…` | HTTP 200 (aktuell funktionierend; frühere Notiz '403' ist überholt), 207 KB bei limit=100 | JSON (feed[].post.record.{text,createdAt,facets[].features[… | ca. 9% DACH-Abflug (9 von 100), plus Ziel-DACH-Posts (z. B.… | ja, 97/100 | neuester Post 05.11.2026 09:46 UTC; ca. 28 P… | niedrig | social-telegram |
| 5 | ja | Travel-Dealz WP-REST /deal (Haupt-Endpunkt) | `https://travel-dealz.de/wp-json/wp/v2/deal?categories=24&per_page=100&_fields=id,date,modified,link,title,cat…` | 200 (Cloudflare, gzip, kein Auth, 12 Abrufe in 10 s ohne Limit, 0,6 s/Abruf, cf-cache DYNAMIC) | JSON-Array. Header X-WP-Total und X-WP-TotalPages. Felder: … | Ja, über das origins-Feld: ZRH 2971, BSL 2972, GVA 2973, HA… | Nur im Titel, kein Preisfeld. Regex: (?… | Neuester Post 2026-11-05T10:27 (zum Abrufzei… | niedrig | travel-dealz-tiefe |
| 5 (war 4) | ja (mit Token) – einzige Tageskalender-Quelle, Token beim Nutzer | Travelpayouts/Aviasales Data API – weitere Endpunkte (calendar, grouped_prices, month-mat… | `https://api.travelpayouts.com/v1/prices/calendar?origin=HAM&destination=ZRH&depart_date=2026-12&currency=eur` | HTTP 401 'Unauthorized' ohne Token (alle 9 geprüften Endpunkte existieren; in dieser Umgebung liegt kein TRAV… | JSON (v2/prices/special-offers: XML) | ja – beliebige IATA-Stadt/Flughafen, v2/prices/latest zusät… | ja (Feld price/value) | Cache aus Nutzersuchen; v2/prices/latest = l… | niedrig | preis-apis |
| 5 | ja | premium-flights Tag-Feeds china/pek/shanghai | `https://premium-flights.com/tag/china/feed/` | 200 (auch /tag/pek/feed/, /tag/shanghai/feed/, /category/cheap-business-class-flights/business-class-deals-fr… | RSS 2.0, 12 Items je Seite, ?paged=N geht | selten: 2 von 12 in tag/china (FRA 20.07, MUC-HKG 2640); fa… | ja, ca. 85 % in EUR ('1520€'); USD/GBP/… | neuester 02.11.2026; tag/china deckt rund 3 … | niedrig | luecke-6 |
| 5 | ja | travel-dealz WP-REST /deal (Deals, leicht) | `https://travel-dealz.de/wp-json/wp/v2/deal?categories=24&per_page=100&orderby=modified&order=desc&modified_af…` | HTTP 200, 0,9 s, kein Cache (cf DYNAMIC) | JSON-Array; Header X-WP-Total/X-WP-TotalPages | gemischt; origins sind Term-IDs (z.B. 2940, 2971), Auflösun… | 18/20 | neuester 2026-11-05T11:19:39 (Test ~11:30 UT… | niedrig | luecke-4 |
| 5 | ja | travel-dealz.de Kategorie-Feed Business Class | `https://travel-dealz.de/kategorie/fluge/business-class/feed/` | 200 | RSS | gemischt (FRA, HAM; viele ex LIS/ATH/MXP/BUD/LUX) | ja (17/20) | neuester Eintrag 2026-10-29; 20 Items reiche… | niedrig | dach-deal-seiten |
| 5 | ja | travel-dealz.de Kategorie-Feed Economy Class | `https://travel-dealz.de/kategorie/fluge/economy-class/feed/` | 200 | RSS | überwiegend ja (FRA/MUC/BER/HAM; auch ex AMS/BRU/WAW) | ja (19/20, Muster 'ab 306€') | neuester Eintrag 2026-11-04 12:41 UTC; ?page… | niedrig | dach-deal-seiten |
| 5 | ja | travel-dealz.de Kategorie-Feed Flüge (Parent, alle Klassen) | `https://travel-dealz.de/kategorie/fluge/feed/` | 200 | RSS | überwiegend ja | ja (18/20) | neuester Eintrag 2026-11-04 17:30 UTC; 20 It… | niedrig | dach-deal-seiten |
| 5 | ja | travel-dealz.de WP-REST JSON-API deal/ticker mit Taxonomie-Filtern | `https://travel-dealz.de/wp-json/wp/v2/deal?per_page=20&categories=1757,884,3225&after=2026-11-01T00:00:00&_fi…` | 200 | JSON | per origins-ID filterbar (HAM 2945, ZRH 2971, FRA 2940, MUC… | ja (title.rendered wie im Feed) | neuester 2026-11-04 13:41; after= liefert nu… | mittel | dach-deal-seiten |
| 4 | ja | Google Flights Ergebnisseite (Live-Messung ZRH⇄HAM) | `https://www.google.com/travel/flights?hl=de&curr=EUR&q=Flights%20from%20HAM%20to%20ZRH%20on%202026-12-10%20on…` | HTTP 200 (3 + 6 Abrufe) | HTML, über vorhandenen Parser dealmonitor.sources.googlefli… | ja, beliebig | ja (Label 'Ab 70 Euro.') | live | niedrig | schweiz-hamburg |
| 4 | ja | Google-Flights-Retry bei leerer Suchmaske | `https://www.google.com/travel/flights` | live beobachtet: 4 von 16 Erstabfragen ohne Ergebnisse, alle 4 beim zweiten Versuch (4 s später) mit Ergebnis… | HTML | n/a | n/a | n/a | niedrig | eigene-ideen |
| 4 | ja (Fallback) – Fallback zur API | Secret Flying Bluesky RSS | `https://bsky.app/profile/secretflying.bsky.social/rss` | 302 auf https://bsky.app/profile/did:plc:ffurylltuiy7da64xuf5lgne/rss, nach Redirect 200 (14 KB). Mit curl -L… | RSS 2.0, item: link (bsky.app Post-Link), description (Voll… | in den 30 Items 3 bis 4 (ZRH, FRA, ZRH, VIE) | Ja, in description (kein title-Feld je … | Neuester Eintrag 05.11.2026 10:27 UTC | niedrig | luecke-1 |
| 4 (war 3) | ja (Fallback) | Secret Flying via Bluesky RSS | `https://bsky.app/profile/secretflying.bsky.social/rss` | 200, 14 KB | RSS 2.0 (kein lastBuildDate, kein ttl) | selten (Beispiele: Amsterdam, Paris, Chennai); DACH nur ver… | title leer! Preis steht in <description… | neuester 05 Nov 2026 10:27 +0000 (ca. 9 min … | niedrig | luecke-5 |
| 4 | ja (Fallback) | Secret Flying via Bluesky-RSS | `https://bsky.app/profile/secretflying.bsky.social/rss` | HTTP 302 -> /profile/did:plc:ffurylltuiy7da64xuf5lgne/rss, dort 200 (Redirect folgen oder DID-URL direkt nutz… | RSS 2.0, 14 KB | 3/30 (Zürich, Frankfurt); Rest Weltweit, aber Error Fares a… | 23/30 im <description> (Format 'für onl… | neuester 05 Nov 2026 10:27 +0000, ältester 0… | niedrig | luecke-4 |
| 4 (war 5) | ja (Fallback) – API ist Primärweg | Secret Flying über Bluesky-RSS | `https://bsky.app/profile/secretflying.bsky.social/rss` | HTTP 200 application/xml, 14 KB, 2 Abrufe | RSS 2.0 ohne <title>; Text in <description> mit Hashtags un… | selten (ca. 2 bis 3 von 30), dafür Europa-weit | ja, im Text (29 von 30), € nur bei Euro… | 05.11.2026 09:46 | niedrig | error-fares-premium |
| 4 | ja | SerpApi – Google Flights / Google Flights Calendar / Google Flights Deals | `https://serpapi.com/search.json?engine=google_flights_calendar&departure_id=ZRH&arrival_id=HAM&date_range_sta…` | HTTP 401 JSON 'Invalid API key' (Endpunkt lebt, Key nötig); Pricing-Seite 200; Doku-Seiten google-flights-api… | JSON | ja (departure_id frei, gl=de, hl=de, currency=EUR) | ja (price-Felder) | Live-Abfrage von Google Flights; Cache 1 h (… | niedrig | preis-apis |
| 4 | ja | Travel-Dealz Taxonomie-Endpunkte (ID-Auflösung) | `https://travel-dealz.de/wp-json/wp/v2/origins?search=hamburg&_fields=id,name,slug,count,parent` | 200 für origins, destinations, categories, tags, airline, region, period, visibility | JSON-Array mit id, name, slug, count, parent. Namen enthalt… | Ja. Zürich [ZRH] = 2971, Basel [BSL,MLH,EAP] = 2972, Genf [… | n/a | count-Felder sind Gesamtzahlen seit 2010, ni… | niedrig | travel-dealz-tiefe |
| 4 | ja | Travel-Dealz WP-REST /ticker (Kurzmeldungen, Error-Fare-nah) | `https://travel-dealz.de/wp-json/wp/v2/ticker?categories=24&per_page=100&_fields=id,date,modified,link,title,c…` | 200, gleiche Struktur wie /deal | JSON wie /deal (gleiche Felder, link-Muster /ticker/<id>/) | Teilweise. origins ist bei vielen leer oder enthält ausländ… | Ja, meist 'ab 416€'. Gleiche Regex wie … | Neuester Eintrag 2026-11-05T09:43; die jüngs… | niedrig | travel-dealz-tiefe |
| 4 | ja | Travelpayouts prices/calendar (Token des Nutzers) | `https://api.travelpayouts.com/v1/prices/calendar?origin=ZRH&destination=HAM&depart_date=2026-12&currency=eur` | 401 Unauthorized ohne Token | JSON (laut Doku, hier nicht testbar) | ja (origin=ZRH) | n/a | unbekannt | niedrig | luecke-3 |
| 4 | ja | You Have Been Upgraded (YHBU) – Kategorie 'Billige Premium Tickets' | `https://youhavebeenupgraded.boardingarea.com/category/billige-premium-tickets/feed/` | 200 | RSS | teilweise (China Southern Business Frankfurt–Asien 1.720 Eu… | ja (48/50) | neuester 2026-10-18; ca. 5-8 Posts/Monat (50… | niedrig | dach-deal-seiten |
| 4 | ja | YouHaveBeenUpgraded Kategorie Billige Premium Tickets | `https://youhavebeenupgraded.boardingarea.com/category/billige-premium-tickets/feed/` | 200 (auch ?paged=2 funktioniert; Hauptfeed /feed/ 200 ist gemischt) | RSS 2.0, 50 Items je Seite | nur 5 von 50 (10 %), 90 % Nachbar-Hubs wie BUD, OSL, ARN, C… | 45 von 50 ('1.490 Euro') | neuester 18.10.2026 (18 Tage alt), 50 Items … | mittel | luecke-6 |
| 4 (war 3) | ja – Host ohne www, sonst 301 | premium-flights.com Tag-Feeds und Suche | `https://www.premium-flights.com/tag/germany/feed/` | HTTP 200, getestet: tag/germany, zurich, switzerland, austria, vienna, hamburg, berlin, munich, frankfurt; ?s… | RSS 2.0, 12 Items je Feed, <category> mit 'Active' (Deal no… | ja, über Tag-Feeds gezielt | ja (11 von 12) | 05.11.2026 07:00 | niedrig | error-fares-premium |
| 4 | ja | reisetopia Hauptfeed (content:encoded) | `https://reisetopia.de/feed/` | 200 application/rss+xml, 596 KB (Seite 1); ?paged=2 und ?paged=3 ebenfalls 200 | RSS 2.0 mit content:encoded (xmlns content), category-Tags … | gemischt: Frankfurt, Berlin, 'Deutschland' vorhanden, aber … | nein (nur in content:encoded) | neuester Eintrag 05.11.2026 10:02 (Abruf 10:… | niedrig | luecke-2 |
| 4 | ja | travel-dealz WP-REST (Canary) | `https://travel-dealz.de/wp-json/wp/v2/deal?per_page=1&_fields=id,date_gmt` | 200 (2 Abrufe, Cloudflare DYNAMIC, kein Cache) | JSON + Header X-WP-Total | ja, überwiegend DE/CH/AT | ja, meist (z. B. 'ab 903€') | neuester Post 2026-11-05 11:19 Ortszeit = 10… | niedrig | luecke-5 |
| 4 | ja (nur Blatt-IDs!) – Elternterm 745 + categories = 0 Treffer | travel-dealz WP-REST /deal mit destinations/airline/origins-Filter (China-Spezial) | `https://travel-dealz.de/wp-json/wp/v2/deal?destinations=745&per_page=100&_fields=id,date,title,link,origins,d…` | 200 application/json, live getestet; destinations=745 (China, inkl. Kinder), 112 Peking, 292 Shanghai; airlin… | JSON-Array | gemischt: Frankfurt/Hamburg/Wien/Basel direkt als origins-I… | ja (ab 1.123€ / ab 716€) | neuester Treffer 03.11.2026; China-Deals 23.… | niedrig | china |
| 4 | ja | travel-dealz WP-REST /ticker (Kurzmeldungen, leicht) | `https://travel-dealz.de/wp-json/wp/v2/ticker?categories=24&per_page=100&orderby=modified&order=desc&modified_…` | HTTP 200, 0,6 s | JSON-Array | gemischt (Düsseldorf, Amsterdam im Test) | 3/3 | neuester 2026-11-05T11:18:05 | niedrig | luecke-4 |
| 3 | ja | Travel-Dealz REST /deal mit Blatt-IDs (Kandidat 1) | `https://travel-dealz.de/wp-json/wp/v2/deal?categories=884,1757,3225&destinations=112,292,2808,2537,3097,3195,…` | 200, X-WP-Total 5 (mit 884 allein: 4) | JSON | 2 von 5 (FRA); 3 Nachbar-Hubs (BUD, LUX, AMS). origins-Term… | ja, 5 von 5 | neuester 23.10.2026 | niedrig | luecke-6 |
| 3 | ja | Vielfliegertreff Forum – Economy Class Tarife (XenForo-RSS) | `https://www.vielfliegertreff.de/forum/forums/economy-class-tarife.54/index.rss` | 200 | RSS | ja, gemischt (DE: HAM-BKK in Y+ ab 773, Aer Lingus Deals vo… | teilweise (9/25) | neuester 2026-11-03 22:04 | niedrig | dach-deal-seiten |
| 3 | ja | mydealz Gruppe Flüge (Preisfeld pepper:merchant) | `https://www.mydealz.de/rss/gruppe/fluege` | 200 text/xml; mydealz /rss/tag/china, /rss/suche?q=, /search/rss?q= alle 404; ?q= wird ignoriert | RSS 2.0 + pepper-Namespace | ja (meist FRA/MUC/DUS/HAM/BER) | nein, Preis in <pepper:merchant price="… | 05.11.2026 09:29 | niedrig | china |
| 3 | ja | premium-flights.com Feed | `https://premium-flights.com/feed/` | 200, 21 KB, LiteSpeed-Cache hit, ETag + Last-Modified gesetzt | RSS 2.0 (sy:updatePeriod hourly, lastBuildDate Wed 04 Nov 1… | gemischt: 'Germany → Brazil Business 1890€', Paris, Amsterd… | ja: '... from 1890€' | neuester Thu, 05 Nov 2026 07:00:04 +0000 (3,… | niedrig | luecke-5 |
| 3 | ja | travel-dealz Volltextsuche (Zürich / Schweiz / Hamburg) als RSS | `https://travel-dealz.de/?s=Z%C3%BCrich&feed=rss2` | HTTP 200 | RSS 2.0 (454 KB, 20 Items) | gemischt; ZRH-/CH-Bezüge im Titel | 14 von 20 | 04.11.2026 | niedrig | schweiz-hamburg |
| 3 | optional | ErrorFareAlerts Deal-Liste (DE) | `https://errorfarealerts.com/deals/flights?fromIata=FRA&toIata=PEK` | 200 (HTML 194 KB gefiltert, 305 KB ungefiltert), kein Cloudflare. /rss und /feed leiten per 302 auf die Start… | HTML (Server-Rendering, Bootstrap). Karte: <h3 class="mb-1"… | 10 von 10 (alle DACH-Abflüge: FRA, ZRH, VIE, BER, GVA, deut… | Ja: 'ab 496 €', 'ab 1.959 €' (deutsches… | Ungefiltert neueste 05.11.2026 05:46. Gefilt… | mittel | luecke-1 |
| 3 (war 5) | optional – Kritiker: ca. 19–20 h nach Secret Flying, nur Business-Preise/Backfill | ErrorFareAlerts Webseite (Flugdeals DACH) | `https://errorfarealerts.com/deals/flights` | HTTP 200, kein Cloudflare, robots.txt Allow: / (nur /downloads/ gesperrt) | HTML (serverseitig gerendert), Detailseiten mit JSON-LD; Si… | ja, ca. 100% DACH-Abflug (FRA/ZRH/VIE/BER/Karlsruhe etc.) –… | ja, meist (z. B. 'ab 475 €') plus struk… | neuester Deal 05.11.2026 05:46 UTC; Takt ca.… | mittel | social-telegram |
| 3 | optional | ErrorFareAlerts.com Flugdeals | `https://errorfarealerts.com/deals/flights` | 200, 305 KB HTML (/rss und /feed -> 302; /sitemap.xml 200, 557 Byte) | HTML (serverseitig gerendert, kein Feed/JSON gefunden) | ja, deutschsprachig: FRA, BER, VIE, ZRH, GVA | ja: 'ab 295 €' im Titel und '€ 1059 ab'… | neuester 05.11.2026 05:46 (Ortszeit, ca. 5 h… | mittel | luecke-5 |
| 3 | nein | Google Flights Explore (Frankfurt nach China) | `https://www.google.com/travel/explore?hl=de&curr=EUR&q=Flights%20from%20Frankfurt%20to%20China` | HTTP 200, 233 KB gzip, 1 Abruf | HTML-Hülle, Ergebnisse per JavaScript/RPC nachgeladen | ja, aber nicht nutzbar | nein (kein '€' im Text, nur 'Ergebnisse… | n/a | hoch | tools-automatisierbar |
| 3 | nein | Google Flights Kalender / Preisgrafik (Date-Grid) | `https://www.google.com/travel/flights?hl=de&curr=EUR&q=Flights%20from%20ZRH%20to%20HAM%20on%202027-01-15%20th…` | HTTP 200 (gleiche Seite wie oben) | Schaltflächen 'Kalender' und 'Preisgrafik' im HTML, aber oh… | ja | nein | n/a | hoch | tools-automatisierbar |
| 3 | nein | Google Kalender-RPC GetCalendarGraph (POST) | `https://www.google.com/_/FlightsFrontendUi/data/travel.frontend.flights.FlightsFrontendService/GetCalendarGra…` | 400 bei meinem Body-Versuch | batchexecute-artig (f.req) | n/a | n/a | n/a | hoch | luecke-3 |
| 3 | optional | Ryanair Fare Finder API (farfnd v4 oneWayFares / roundTripFares) | `https://services-api.ryanair.com/farfnd/v4/oneWayFares?departureAirportIataCode=HAM&outboundDepartureDateFrom…` | HTTP 200 (HAM one-way, HAM round-trip, BSL one-way, HAM->STN Routenfilter alle 200; ZRH round-trip 200 aber l… | JSON | ja – Ryanair-DACH-Flughäfen laut Airportliste: BER BRE BSL … | ja (price.value, currencyCode) | priceUpdated 04.11.2026 (gleicher Tag, Zeits… | niedrig | preis-apis |
| 3 | optional | Travel-Dealz Schlagwortalarm und Newsletter (Funktionen zum Nachbauen) | `https://travel-dealz.de/schlagwortalarm/` | 200 (Seite), /newsletter/ 200 | HTML-Seiten, keine API. Alarm läuft über Telegram-Bot und B… | Region wählbar: Deutschland, Österreich, Schweiz (Newslette… | n/a | n/a | niedrig | travel-dealz-tiefe |
| 3 | optional | Travelpayouts offene Stammdaten (airports.json, routes.json) | `https://api.travelpayouts.com/data/en/airports.json` | HTTP 200 ohne Token (airports 375 KB, routes 648 KB, cities 342 KB) | JSON | alle | nein | statisch, Aktualität des Streckennetzes unbe… | niedrig | eigene-ideen |
| 3 (war 4) | optional – Kritiker: ca. 1 Thread/Woche, viel Nicht-DACH | Vielfliegertreff Business-Class-Forum RSS (HAM-Routen) | `https://www.vielfliegertreff.de/forum/forums/first-business-class-tarife.53/index.rss` | HTTP 200 | RSS 2.0 (XenForo) | gemischt, viele HAM-/DUS-/FRA-Routen; kein ZRH-Treffer in 2… | 20 von 25 (Business), 8 von 25 (Economy… | 04.11.2026 09:57 (Business), 03.11.2026 (Eco… | niedrig | schweiz-hamburg |
| 3 (war 4) | optional – Kritiker: ca. 1 Thread/Woche | Vielfliegertreff Forum – First & Business Class Tarife (XenForo-RSS) | `https://www.vielfliegertreff.de/forum/forums/first-business-class-tarife.53/index.rss` | 200 | RSS | gemischt, viel DACH (HAM-LIS-PDL in C 463 EUR, PEK-FRA ab 6… | meist ja (18/25, IATA-Codes + 'in C'/'E… | neuester 2026-11-04 09:57; 25 Threads reiche… | niedrig | dach-deal-seiten |
| 3 | optional | ferienpiraten.ch (HolidayPirates Schweiz) Feed | `https://www.ferienpiraten.ch/feed` | HTTP 200 | RSS 2.0 (application/xml) | ja, Schweiz (ZRH/BSL/GVA), Flugdeals oft 'ab Zürich' | 1 von 8 im Titel, Preis fast immer in d… | 05.11.2026 08:30 (neuester Eintrag, Feed dec… | niedrig | schweiz-hamburg |
| 3 | optional | preisjaeger.at – Gruppe Flüge (mydealz-Schwester Österreich) | `https://www.preisjaeger.at/rss/gruppe/fluege` | 200 | RSS | ja (AT: Wien, Linz, Klagenfurt, Bratislava) | meist ja (20/30) | neuester 2026-11-04 15:40; aber nur ~3-5 Dea… | niedrig | dach-deal-seiten |
| 3 | optional | preispirat.ch (Schweizer Deal-Portal) – Such-Feed für Airline-Sales | `https://www.preispirat.ch/?s=SWISS&feed=rss2` | HTTP 200 (Hauptfeed /feed/ ebenfalls 200, Kategorie-Feeds 404) | RSS 2.0 (WordPress) | ja, Schweiz (CHF-Preise, ZRH/GVA) | 2 von 30 (Hauptfeed), Suchfeed 5 von 30 | 05.11.2026 09:10 (Hauptfeed); Suche: 04.11.2… | niedrig | schweiz-hamburg |
| 3 | optional – Tag-Feeds vollständiger | premium-flights Suchfeed (china/beijing/shanghai) | `https://www.premium-flights.com/?s=china&feed=rss2` | 200 application/rss+xml, live; gefiltert (Beijing/Shanghai/China-Treffer), ?s=beijing, ?s=shanghai, ?s=frankf… | RSS 2.0 | selten: Luxemburg, Brüssel, Budapest, Oslo, Warschau, Prag;… | ja (z. B. 'Oslo → Beijing Business Clas… | neuester Treffer 04.11.2026 | niedrig | china |
| 3 | optional | premium-flights Suchfeed ?s=china | `https://premium-flights.com/?s=china&feed=rss2` | 200 | RSS 2.0 | 0 von 12 DACH als Abflug (Beijing→Vienna Rueckrichtung) | 11 von 12 | neuester 04.11.2026, aeltester 14.10. | niedrig | luecke-6 |
| 3 | optional | reisetopia Business-Class-Tag-Feed | `https://reisetopia.de/tag/business-class/feed/` | 200 (?paged=2 200; alle anderen Pfade /tag/china, /category/deals, ?s=, wp-json = 403 WAF) | RSS 2.0 mit content:encoded, 20 Items, 3 Tage Spanne | gemischt: ab Frankfurt/Deutschland/Berlin ueberwiegend, daz… | nein, 0 von 20; Preis nur in content:en… | neuester 05.11.2026 (tagesaktuell) | mittel | luecke-6 |
| 3 | optional | travel-dealz.de Gesamt-Deal-Feed (post_type=deal, 100 Items) | `https://travel-dealz.de/feed/?post_type=deal` | 200 | RSS | überwiegend ja | ja (93/100) | 2026-11-04 bis 2026-10-22 | niedrig | dach-deal-seiten |
| 3 | optional | travel-dealz.de Kategorie-Feed Premium Economy | `https://travel-dealz.de/kategorie/fluge/premium-economy/feed/` | 200 | RSS | gemischt (FRA, AMS, WAW, NYC) | teilweise (8/20) | neuester 2026-11-04, aber nur 6 Einträge in … | niedrig | dach-deal-seiten |
| 3 | optional | travel-dealz.de Reiseziel-Feed China (destinations-Taxonomie) | `https://travel-dealz.de/reiseziel/china/feed/` | 200 | RSS | gemischt (FRA, AMS, BUD, LUX) | teilweise (8/15) | neuester 2026-11-03 | niedrig | dach-deal-seiten |
| 3 | optional | travelfree.info (EU-weiter Flugdeal-Blog, englisch; außerhalb DACH-Blickwinkel) | `https://travelfree.info/feed/` | 200 | RSS | teilweise (Berlin–Seattle €406; überwiegend Skandinavien/Ba… | ja (50/50, 'from €406') | neuester 2026-11-04 20:07; ~15 Posts/Tag (50… | niedrig | dach-deal-seiten |
| 2 | ja | fly4free.com Hauptfeed (DACH-Treffer) | `https://www.fly4free.com/feed/` | HTTP 200 application/rss+xml, 64 KB | RSS 2.0, <category> mit Städten und Ländern (germany, Austr… | ja (13 von 50) | ja (46 von 50) | 05.11.2026 09:00 | niedrig | error-fares-premium |
| 2 | nein | Air China DE Sonderangebote | `https://www.airchina.de/DE/DE/promotion/flights/` | HTTP 200, 163 KB; /promotion/DiscountsforDestinations 200, 145 KB | HTML-Shell, Preise per JS | n/a | nein ('Sonderangebote') | n/a | hoch | airline-sales |
| 2 | nein | Duffel API | `https://api.duffel.com/air/airlines?limit=1` | HTTP 401 JSON 'missing_authorization_header' (lebt); Pricing-Seite 200 | JSON | ja | ja | Live-NDC/GDS-Angebote (nur Live-Modus; Test-… | mittel | preis-apis |
| 2 | optional | ErrorFareAlerts Telegram-Kanal | `https://t.me/s/errorfarealerts` | HTTP 200, 114 KB, Web-Vorschau vorhanden | HTML (div.tgme_widget_message_wrap, Datum in <time datetime… | ja, überwiegend (ZRH, FRA, BER, Karlsruhe) | ja | neuester Post 05.11.2026 06:10 UTC (Newslett… | niedrig | social-telegram |
| 2 | optional | Feiertage und Schulferien (nager.at, OpenHolidays) | `https://date.nager.at/api/v3/PublicHolidays/2027/DE` | HTTP 200 ohne Key (nager DE 19 / CH 33 Einträge; openholidaysapi.org SchoolHolidays DE 127 Einträge, PublicHo… | JSON | DE, CH, AT | nein | 2027 verfügbar | niedrig | eigene-ideen |
| 2 | optional | Frankfurtflyer.de (Hauptfeed, rss2-Variante) | `https://frankfurtflyer.de/feed/rss2/` | 200 | RSS | selten (Deals meist ex Skandinavien/AMS) | selten (1-3/18) | neuester 2026-11-04 12:00; ~5 Posts/Tag | niedrig | dach-deal-seiten |
| 2 | optional | Google News RSS site:reisetopia.de (Fallback, nur Titel) | `https://news.google.com/rss/search?q=site:reisetopia.de+Business+Class+when:3d&hl=de&gl=DE&ceid=DE:de` | 200 application/xml, 76 KB | RSS 2.0, Items mit title '<Titel> - reisetopia', link = ver… | nur aus Titel ableitbar ('ab Deutschland', 'ab Frankfurt') | nein | neuester 05.11.2026 01:13 (Abruf 10:31) | niedrig | luecke-2 |
| 2 | optional | Google News RSS-Suche (Frühwarnung, Quellenentdeckung) | `https://news.google.com/rss/search?q=Business+Class+Schn%C3%A4ppchen+Flug+when:3d&hl=de&gl=DE&ceid=DE:de` | HTTP 200 (ohne Login erreichbar) | RSS 2.0 (title mit ' - Quelle' am Ende, pubDate, <source>; … | ja bei deutschen Seiten (Frankfurt/Deutschland), nicht stru… | selten | neuester Treffer 04.11.2026 20:18 UTC | mittel | social-telegram |
| 2 | nein | ITA Matrix | `https://matrix.itasoftware.com/` | HTTP 200, 3,8 KB, 2 Abrufe (/ und /search liefern dieselbe Hülle) | HTML-SPA-Hülle (Google-Client-App), Suche per JS/RPC | ja, aber nicht nutzbar | nein | n/a | hoch | tools-automatisierbar |
| 2 (war 3) | optional – Cache-Preise, teils 36 h Reisedauer, nicht gegengeprüft | KAYAK Länder-/Strecken-Seite (Deutschland→China, ZRH→China, HAM→ZRH, Weltweit) | `https://www.kayak.de/fl%C3%BCge/Deutschland-DE0/China-CN0` | HTTP 200, text/html, 1,2-1,7 MB, ohne Login. Wien-VIE/China-CN0 gab 404 (Slug unbestätigt) | HTML mit JSON-LD (<script type="application/ld+json">): Arr… | ja, je nach URL: Deutschland-DE0, Hamburg-HAM, Zuerich-ZRH … | ja, Seitentitel '283€ Günstige Flüge na… | Nutzer-Suchcache der letzten 72 h bzw. 5 Tag… | mittel | airline-sales |
| 2 (war 3) | optional | Kayak Länderseiten (Deutschland/Zürich nach China, Asien) | `https://www.kayak.de/fl%C3%BCge/Deutschland-DE0/China-CN0` | HTTP 200 (de China 204 KB, ZRH-CN kayak.ch 179 KB, Deutschland-Asien 212 KB, Deutschland-Weltweit 184 KB); ka… | HTML mit JSON-LD Flight[] (40 Einträge) plus Textblöcke | ja: HAM, BER, MUC, FRA, DUS (de); ZRH (ch) | JSON-LD offers[0].price, Titel '284€ Gü… | Nutzerpreise der letzten 5 Tage, Angebote mi… | mittel | eigene-ideen |
| 2 | nein | Momondo Explore | `https://www.momondo.de/explore/FRA` | HTTP 200, 217 KB gzip (1 MB entpackt), 1 Abruf | HTML mit großem JSON-Zustand, aber leere Ergebnislisten | Eingabe FRA, Preise nicht enthalten | nein | n/a | hoch | tools-automatisierbar |
| 2 | nein | RapidAPI-Angebote (Sky-Scrapper, Google Flights2, Booking.com15) | `https://rapidapi.com/apiheya/api/sky-scrapper/pricing` | Listing- und Pricing-Seiten HTTP 200 (258–349 KB), Preisstaffeln aber nur per JavaScript gerendert – Free-Tie… | JSON (per X-RapidAPI-Key) | ja | ja | nicht geprüft | niedrig | preis-apis |
| 2 | optional | Ryanair farfnd (Zubringer-Preise) | `https://services-api.ryanair.com/farfnd/v4/oneWayFares?departureAirportIataCode=VIE&outboundDepartureDateFrom…` | HTTP 200 (NUE 20 Fares, VIE 43 Fares), priceUpdated vom 05.11.2026 | JSON fares[].outbound | ja (VIE, NUE, HAM, BSL, BER, ...) | ja: outbound.price.value (EUR) | priceUpdated = Epoch-Millisekunden, heute | mittel | eigene-ideen |
| 2 | optional | Ryanair farfnd (für HAM-Nachbarn / Kontrolle) | `https://services-api.ryanair.com/farfnd/v4/oneWayFares?departureAirportIataCode=HAM&outboundDepartureDateFrom…` | HTTP 200 | JSON | ja, HAM (BSL→HAM: 0 Fares, Ryanair fliegt BSL/ZRH–HAM nicht) | ja, fares[].outbound.price.value (z. B.… | live | niedrig | schweiz-hamburg |
| 2 | nein | Secret Flying Europe-Seite (HTML, Best Effort) | `https://www.secretflying.com/europe-flight-deals/` | 200 (beide Abrufe, 400 KB), aber alle anderen Pfade 403 Cloudflare 'Just a moment' (/feed/, /wp-json/wp/v2/po… | HTML, WordPress-Karten: <article class="... post-<ID> ... c… | 1 von 9 (nur Zurich-Mauritius). Frankfurt-Phuket und Zurich… | Ja, im title-Attribut: 'Zurich, Switzer… | Neuester 05.11.2026 (nur Datum ohne Uhrzeit) | mittel | luecke-1 |
| 2 (war 4) | nein – Nachrecherche: unvollständig, Slug-Preise falsch, Cloudflare | Secret Flying HTML: Europa-Deals | `https://www.secretflying.com/europe-flight-deals/` | HTTP 200 (zweimal, auch über /euro-deals/ mit 301-Weiterleitung); andere Pfade 403 (Cloudflare, teils zufälli… | HTML, WordPress-Karten: <article class='...post...'> mit cl… | ja, vereinzelt (2 von 9) | ja | 04.11.2026 | mittel | error-fares-premium |
| 2 | optional | Skyscanner-Deals Telegram (Secret-Flying-Spiegel, inoffiziell) | `https://t.me/s/skyscanner` | HTTP 200, Web-Vorschau vorhanden | HTML (tgme_widget_message_wrap) | nein (im Fenster 0) | ja, 20/20 | neuester Post 04.11.2026 23:57 UTC | niedrig | social-telegram |
| 2 | optional | Travel-Dealz Forum (Discourse) RSS | `https://forum.travel-dealz.de/c/deal/6.rss` | HTTP 200, 62 KB (auch latest.rss 91 KB, posts.rss 75 KB, categories.json 32 KB alle 200) | RSS 2.0 (Discourse); categories.json mit id/slug (deal=6, f… | gemischt, nicht strukturiert | teilweise (ca. 45%) | neuester Post 04.11.2026 11:42 UTC (Kategori… | niedrig | social-telegram |
| 2 | optional | Travel-Dealz Kategorie-/Zielfeeds (reiseziel/china, abflug/<Hub>) | `https://travel-dealz.de/reiseziel/china/feed/` | 200 (abflug/budapest-bud/feed/ 200 mit nur 2 Items, kategorie/business-class/feed/ 200 mit 20) | RSS 2.0, 15-20 Items | gemischt; Nachbar-Hubs sind im Business-Feed bereits enthal… | ca. 85-90 % | neuester 05.11.2026 (Business-Feed) | niedrig | luecke-6 |
| 2 | optional | Travel-Dealz RSS-Kombifilter (post_type=deal&origins=<slug>&category_name=<slug>) | `https://travel-dealz.de/feed/?post_type=deal&origins=hamburg-ham&category_name=economy-class` | 200 (Slug funktioniert; numerische ID: 404; categories=ID wird ignoriert) | RSS 2.0, <category> enthält nur Klassen- oder Anbieternamen… | Ja, serverseitig über den origins-Slug. Zürich-Feed: 6 Item… | Ja, im Titel wie bei JSON | Neuester Post im Hamburg-Economy-Feed 2026-0… | niedrig | travel-dealz-tiefe |
| 2 | nein | Wizz Air interne API (be.wizzair.com/29.23.1/Api) | `https://be.wizzair.com/29.23.1/Api/asset/map?languageCode=en-gb` | asset/map HTTP 200 JSON (697 KB, 198 Städte); POST Api/search/timetable HTTP 400 {"validationCodes":["Invalid… | JSON | ja (HAM 14 Verbindungen: SKP, TIA, SOF, OTP, PRN, TGD…; BSL… | nein (nur Routen) | operationStartDate/rescueEndDate 04./05.11.2… | hoch | preis-apis |
| 2 | optional | ferienpiraten.ch (HolidayPirates Schweiz) | `https://www.ferienpiraten.ch/feed` | 200 | RSS | ja (CH) | nein (0/7, Clickbait-Titel mit Emojis) | neuester 2026-11-04 15:00 | niedrig | dach-deal-seiten |
| 2 | optional | fly4free.com Business-Class-Kategorie | `https://www.fly4free.com/flight-deals/business-class/feed/` | HTTP 200 application/rss+xml, 58 KB | RSS 2.0 | selten | ja (50 von 50) | 02.11.2026 | niedrig | error-fares-premium |
| 2 | nein | travel-dealz Abflug-Feed Hamburg | `https://travel-dealz.de/abflug/hamburg-ham/feed/` | HTTP 200 | RSS 2.0 (814 KB, 20 Items) | ja, HAM | 20 von 20 | neuester 04.11.2026, ältester 21.10.2026 | niedrig | schweiz-hamburg |
| 2 | optional | travel-dealz Abflug-Feed Zürich | `https://travel-dealz.de/abflug/zuerich-zrh/feed/` | HTTP 200 | RSS 2.0 | ja, ZRH (aber Mehr-Flughafen-Deals mit ZRH als einem Abflug… | 7 von 10 | neuester 03.11.2026; ältester 10.12.2017 (Fe… | niedrig | schweiz-hamburg |
| 2 | nein | travel-dealz Reiseziel-Feed China / Peking / Shanghai | `https://travel-dealz.de/reiseziel/china/feed/` | 200 application/rss+xml (Peking/Shanghai/Asien ebenfalls 200; /reiseziel/hongkong/feed/ 404; /category/flug, … | RSS 2.0 | nein, überwiegend Budapest/Luxemburg/Amsterdam; nur Peking … | ja | 03.11.2026 (aber Flying-Blue-Müll), echte Ch… | niedrig | china |
| 2 | optional | travel-dealz Tag-Feed Gabelflüge / Oneway (ZRH-Relevanz) | `https://travel-dealz.de/thema/gabelflug/feed/` | HTTP 200 (auch /thema/oneway/feed/ 200) | RSS 2.0 | nein/selten – Gabelflug-Beispiele starten in Warschau, Amst… | 10 von 10 | Gabelflug 04.11.2026; Oneway 30.10.2026 | niedrig | schweiz-hamburg |
| 2 | optional | travel-dealz WP-REST mit origins-Taxonomie (ZRH/BSL/GVA) | `https://travel-dealz.de/wp-json/wp/v2/deal?origins=2971&per_page=30&_fields=id,date,title,link,categories,ori…` | HTTP 200 | JSON (WP-REST) | ja, ZRH (origins=2971), BSL 2972, GVA 2973 | 5 von 6 (title.rendered, HTML-Entities … | neuester 03.11.2026 | mittel | schweiz-hamburg |
| 2 | optional | travel-dealz.de Abflughafen-Feed Hamburg (origins-Taxonomie) | `https://travel-dealz.de/abflug/hamburg-ham/feed/` | 200 | RSS | ja (HAM) | ja (20/20) | neuester 2026-11-04 07:10 | niedrig | dach-deal-seiten |
| 2 | optional | travel-dealz.de Abflughafen-Feed Zürich | `https://travel-dealz.de/abflug/zuerich-zrh/feed/` | 200 | RSS | ja (ZRH) | teilweise (8/11) | neuester 2026-11-04; enthält aber alte Posts… | niedrig | dach-deal-seiten |
| 2 | optional | travel-dealz.de Kategorie-Feed Meilen, Punkte & Status | `https://travel-dealz.de/kategorie/milespoints/feed/` | 200 | RSS | gemischt | teilweise (10/20, oft Meilen+€) | neuester Eintrag 2026-11-03 | niedrig | dach-deal-seiten |
| 2 | optional | travel-dealz.de Tag-Feeds (/thema/<slug>/feed/) – Gabelflüge / Oneway | `https://travel-dealz.de/thema/gabelflug/feed/` | 200 | RSS | ja (Rückflug nach FRA/MUC/Deutschland) | ja (4/4) | neuester 2026-11-04 12:41 | niedrig | dach-deal-seiten |
| 2 | optional | travel-dealz.de Visibility-Feed 'Hot' (redaktionell markierte Top-Deals) | `https://travel-dealz.de/visibility/hot/feed/` | 200 | RSS | gemischt | ja (17/20) | neuester 2026-11-04 17:43 | niedrig | dach-deal-seiten |
| 1 | nein | AZair.eu (Billigflug-Suchmaschine, azfin.php) | `https://www.azair.eu/azfin.php?searchtype=flexi&srcap0=HAM&dstap0=XXX&depdate=10.11.2026&arrdate=31.12.2026&c…` | HTTP 200 HTML (28 KB), Ergebnisliste nicht serverseitig enthalten (nur Datums-Kalender, Share-Links) | HTML | ja (konzeptionell) | nein | n/a | hoch | preis-apis |
| 1 | nein | Alles geprüft und verworfen (Negativliste) | `https://news.google.com/rss/search?q=Fehlerpreis+Flug&hl=de&gl=DE&ceid=DE:de` | Google News 200 (Rauschen, keine Deals); mydealz /rss/search und /rss/tag 404; Bluesky traveldealz.bsky.socia… | verschieden | n/a | n/a | n/a | hoch | eigene-ideen |
| 1 | nein | Alternative Wege (Telegram, Reddit, Nitter, Bluesky-Suche, andere Bluesky-Konten) | `https://t.me/s/secretflying / https://www.reddit.com/r/ErrorFares/new.rss / https://nitter.net/secretflying/r…` | t.me/s/secretflying 302 (kein öffentlicher Kanal), Reddit-RSS 302 auf Suche, nitter.net 410, bsky searchPosts… | - | - | - | - | hoch | luecke-1 |
| 1 | nein | Amadeus Self-Service APIs (Flight Cheapest Date Search, Flight Offers Search) | `https://test.api.amadeus.com/v1/shopping/flight-dates` | test.api.amadeus.com: CONNECT 502 (Host antwortet nicht mehr); api.amadeus.com: HTTP 401 'Missing mandatory a… | JSON (historisch) | n/a | n/a | ABGESCHALTET am 17.07.2026 | hoch | preis-apis |
| 1 | nein | Aviasales öffentliche Karten-/Min-Price-Endpunkte (map.aviasales.ru, min-prices.aviasales… | `https://map.aviasales.ru/prices.json?origin_iata=HAM&period=2026-12-01:month` | HTTP 302 auf aviasales.ru-Startseite; min-prices.aviasales.ru/calendar_preload 404; map.aviasales.com TLS-Zer… | HTML/– | n/a | n/a | n/a | hoch | preis-apis |
| 1 | nein | Bluesky-Accounts anderer Deal-Seiten (mydealz, fly4free, flightdeals, holidaypirates, bus… | `https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor=<handle>` | API 200, aber Feeds leer (11 Byte, feed=[]) bzw. Handle nicht existent | JSON | nein | n/a | n/a | niedrig | social-telegram |
| 1 | nein | Chinesische Airlines / OTA-Angebotsseiten (Air China DE, China Eastern DE, Hainan DE, Cat… | `https://de.ceair.com/` | airchina.de/DE/DE/offers 404; de.ceair.com 200 (8 KB Shell, keine Preise); hainanairlines.com DE-Portal 200 (… | HTML | ja (DE→China), aber keine Preise im HTML | nein | n/a | hoch | preis-apis |
| 1 | optional | EZB-Wechselkurse / Frankfurter | `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml` | HTTP 200 (ecb.xml 1,5 KB, api.frankfurter.dev/v1/latest 200 JSON) | XML bzw. JSON | n/a | n/a | täglich, Stand 04.11.2026 | niedrig | eigene-ideen |
| 1 | nein | Emirates Sonderangebote DE | `https://www.emirates.com/de/german/special-offers/` | HTTP 200, 387 KB; /de/german/book/featured-fares/ ebenfalls 200, 3,3 MB | HTML mit __NEXT_DATA__ (284 KB JSON), darin nur Übersetzung… | Abflughäfen wären clientseitig; unbestätigt | nein | n/a | hoch | airline-sales |
| 1 | nein | Eurowings (Bestpreis-API / Billigflüge-Seite) | `https://www.eurowings.com/api/bestprice/v1/bestprice?origin=HAM&destination=ZRH` | HTTP 308 (Cloudflare) bzw. HTTP 403 'Just a moment...' (Cloudflare-Challenge) auf /de/fluege/billigfluege/ham… | HTML (Challenge) | n/a | n/a | n/a | hoch | preis-apis |
| 1 | nein | First Class & More (firstclassandmore.de) | `https://www.firstclassandmore.de/feed/` | 302 -> app.rebrandly.com/broken-links (kein Feed) | HTML | n/a | n/a | n/a | hoch | dach-deal-seiten |
| 1 | nein | Gesperrt/unbrauchbar (nicht integrieren) | `https://de.trip.com/sale/` | trip.com /sale 403, /flights/china-airfares 200 aber 'Challenge Validation'; secretflying /feed 403; Lufthans… | HTML/Challenge | n/a | nein | n/a | hoch | china |
| 1 | nein | Google Flights statische Seiten flights-from-*.html | `https://www.google.com/travel/flights/flights-from-hamburg-to-zurich.html` | HTTP 200, 2 MB (flights-from-zurich.html ebenfalls 200, 2 MB) | HTML mit AF_initDataCallback-Blöcken (ds:0..ds:6) | n/a | nein (Titel 'Günstige Flüge von Hamburg… | n/a | hoch | airline-sales |
| 1 | nein | Google News RSS (Deal-Schlagzeilen China Business) | `https://news.google.com/rss/search?q=g%C3%BCnstig+fliegen+China+Business+Class+Deal&hl=de&gl=DE&ceid=DE:de` | 200 application/xml | RSS 2.0 | gemischt | teils (Business Class Deal: Flüge nach … | schlecht: Treffer von 2023-2025, neuester 28… | niedrig | china |
| 1 | nein | Kayak Explore | `https://www.kayak.de/explore/FRA-CN` | HTTP 404 (Seite nicht gefunden), 1 Abruf | HTML-404 | n/a | nein | n/a | hoch | tools-automatisierbar |
| 1 | nein | Kayak Explore API (kayak.de/h/explore/api; gleiches Backend wie momondo/swoodoo/checkfeli… | `https://www.kayak.de/h/explore/api?airport=HAM` | HTTP 302 -> /s/ident/forbidden HTTP 403 | HTML | n/a | n/a | n/a | hoch | preis-apis |
| 1 | nein | Kayak Strecken-Seite HAM-ZRH | `https://www.kayak.de/fl%C3%BCge/Hamburg-HAM/Zuerich-ZRH` | 200 | HTML + JSON-LD (Product/AggregateOffer lowPrice 75.07, Flig… | ja (HAM) | ja (Title '75€ Flug von Hamburg nach Zü… | vor 3+ Tagen | mittel | luecke-3 |
| 1 | nein | Kiwi.com Tequila API | `https://tequila.kiwi.com/` | HTTP 200, aber nur Login-Seite ('Tequila by Kiwi.com', 3,9 KB); partners.kiwi.com 200 mit offizieller Mitteil… | HTML | n/a | n/a | Programm seit 30.05.2024 geschlossen | hoch | preis-apis |
| 1 | nein | Lufthansa / SWISS / Austrian / Eurowings / Edelweiss / Condor / Finnair / Air Serbia Ange… | `https://www.lufthansa.com/de/de/angebote` | HTTP 403 (Lufthansa: Cloudflare/Block-Seite, SWISS 403 13 KB, Austrian 403, Eurowings 403 'Just a moment...' … | nicht lesbar | n/a | n/a | n/a | hoch | airline-sales |
| 1 | nein | Lufthansa Open API (developer.lufthansa.com) | `https://api.lufthansa.com/v1/offers/lowestfares?origin=HAM&destination=ZRH&travelDate=2026-12-10` | Doku 200; api.lufthansa.com/v1/offers/lowestfares -> HTTP 596 'Service Not Found'; /v1/operations/schedules/H… | JSON | ja (Flugpläne/Status) | nein | n/a | mittel | preis-apis |
| 1 | nein | Mastodon travel-dealz (@traveldealz / @traveldealzde) | `https://mastodon.social/@traveldealz.rss` | HTTP 200, 15,8 KB (@traveldealzde.rss 17 KB) | RSS 2.0 (Mastodon) | unbekannt | unbekannt | neuester Post 05.10.2026 (zuletzt aktiv vor … | niedrig | social-telegram |
| 1 | nein | Mastodon travel-dealz und Hashtags | `https://mastodon.social/@traveldealzde.rss` | HTTP 200 application/rss+xml (@traveldealzde und @traveldealz); /tags/errorfare.rss 200; /tags/flightdeals.rs… | RSS 2.0, Text in <description> | ja, aber selten | teilweise im Text | Jun bis Okt 2026 | niedrig | error-fares-premium |
| 1 | nein | Nicht brauchbar: fly4free.pl/.cz/.hu, holidaypirates, Vielfliegertreff-Hauptfeed, reiseto… | `https://www.fly4free.pl/feed/` | fly4free.pl 200; .cz 302; .hu und holidaypirates.de Verbindungsfehler; holidaypirates.com 200; vielfliegertre… | RSS | nein | PLN, kein EUR | 05.11.2026 | hoch | luecke-6 |
| 1 | nein | Nicht erreichbar / gesperrt (Sammeleintrag) | `https://www.blick.ch/deals/` | blick.ch /deals/ und /reisen/rss.xml 403; 20min.ch RSS 404; watson.ch /api/1.0/rss/index 200 aber HTML (kein … | - | - | - | - | hoch | schweiz-hamburg |
| 1 | nein | Nicht erreichbar oder tot (Jack's Flight Club, Going, Mighty Travels, travelfree.info, Ai… | `https://www.jacksflightclub.com/feed/` | jacksflightclub 404, going.com/feed 404, mightytravels /feed liefert HTML-Premium-Seite, travelfree.info 429,… | verschieden | n/a | n/a | n/a | hoch | error-fares-premium |
| 1 | nein | Nitter / X-Spiegel | `https://nitter.net/secretflying/rss` | nitter.net 410 Gone, xcancel.com 451, nitter.poast.org und nitter.privacydev.net 502 (Proxy-Tunnel) | n/a | n/a | n/a | n/a | hoch | social-telegram |
| 1 | nein | Norwegian Fare-Calendar API | `https://www.norwegian.com/api/fare-calendar/calendar?originAirportCode=HAM&destinationAirportCode=OSL&outboun…` | HTTP 403 'Are you human? / Norwegian' | HTML (Bot-Check) | n/a | n/a | n/a | hoch | preis-apis |
| 1 | nein | Points-Blogs (OMAAT, View from the Wing, Loyalty Lobby, God Save The Points, Thrifty Trav… | `https://onemileatatime.com/feed/` | HTTP 200 (alle); godsavethepoints seit Jul 2025 tot | RSS 2.0 / Atom | nein | selten | 04.11.2026 | niedrig | error-fares-premium |
| 1 | nein | Reddit (r/Flights, r/awardtravel, r/flightdeals, r/Flugdeals, r/ErrorFare) | `https://www.reddit.com/r/flightdeals/new/.rss` | r/Flights/.rss und r/awardtravel/new/.rss 200 (Atom); r/flightdeals .rss und /new/.rss 429 (3 Versuche); /new… | Atom | n/a | nein | 05.11.2026 (aber ohne Deals) | hoch | error-fares-premium |
| 1 | nein | SWISS / Edelweiss / Lufthansa.com (Angebotsseiten & Website-APIs) | `https://www.swiss.com/ch/de/angebote/flugangebote` | HTTP 403 (Akamai-Sperrseite 'SWISS'); swiss.com/api/bestprice 403; flyedelweiss.com/CH/DE/angebote 403; lufth… | HTML (Sperrseite) | n/a | n/a | n/a | hoch | preis-apis |
| 1 | nein | Skyscanner / Momondo / Opodo / checkfelix / skiplagged / Eurowings / flightconnections / … | `https://www.skyscanner.de/transport/fluge/ham/zrh/261205/` | Skyscanner 708 B Captcha-Stub; Momondo 404; Opodo 404; checkfelix 404; skiplagged 403; Eurowings 403; flightc… | - | n/a | nein | n/a | hoch | luecke-3 |
| 1 | nein | Skyscanner Everywhere / Suchseite | `https://www.skyscanner.de/transport/fluge-von/fra/` | HTTP 307 auf /sttc/px/captcha-v2/ (2 Abrufe, beide Captcha) | Captcha-Weiterleitung (PerimeterX) | n/a | nein | n/a | hoch | tools-automatisierbar |
| 1 | nein | Skyscanner Travel API (Partner) | `https://skyscannerpartnersupport.zendesk.com/hc/en-us/articles/10881149122717-What-is-the-acceptance-criteria…` | developers.skyscanner.net/docs -> HTTP 403; Partner-Support-Artikel HTTP 200 | HTML (Doku) | n/a | n/a | n/a | hoch | preis-apis |
| 1 | nein | Telegram-Kanäle (t.me/s/...) | `https://t.me/s/flugdeals` | HTTP 200, aber tot oder ohne Vorschau: 16 Kanäle getestet | HTML (tgme_widget_message_wrap) | n/a | n/a | 2025 bzw. 2023 (veraltet) | mittel | error-fares-premium |
| 1 | nein | Telegram-Kanäle der Hauptseiten (travel_dealz, urlaubspiraten, fly4free, secretflying, my… | `https://t.me/s/travel_dealz` | HTTP 302 (kein öffentlicher Kanal / keine Web-Vorschau), ebenso für 30+ weitere Namen | n/a | n/a | n/a | n/a | hoch | social-telegram |
| 1 | nein | The Flight Deal | `https://theflightdeal.com/feed/` | HTTP 200 application/rss+xml, 28 KB | RSS 2.0 | nein (nur USA) | ja | 04.11.2026 | niedrig | error-fares-premium |
| 1 | nein | Tote/gesperrte Kandidaten (Sammeleintrag) | `https://www.fliegen-sparen.de/feed/` | 000 (TLS-Zertifikat abgelaufen; http -> DNS-Fehler) | n/a | n/a | n/a | n/a | hoch | dach-deal-seiten |
| 1 | nein | Travelzoo Deutschland (Atom-Feed) | `https://www.travelzoo.com/de/feed/` | 200 | Atom | ja (DE-Abflüge bei Paketen) | ja (116/135, 'ab 299 € p.P. -- ...') | neuester 2026-11-04; 135 Items bis 2026-10-21 | niedrig | dach-deal-seiten |
| 1 | nein | Trip.com / Ctrip (Affiliate & Partner-API) | `https://www.trip.com/partners/affiliate/` | HTTP 200, aber JS-Shell (nur Titel 'earn up to 7% commission' lesbar); pages.trip.com/intl/en/partner 404; de… | HTML | n/a | nein | n/a | hoch | preis-apis |
| 1 | nein | Turkish / Qatar / Etihad / KLM / Air France / Discover / China Southern | `https://www.turkishairlines.com/de-de/flights/cheap-flights/` | nicht erreichbar: Verbindungsabbruch (HTTP/2 INTERNAL_ERROR bzw. Timeout nach 30 s, auch mit --http1.1); disc… | unbekannt | unbekannt | unbekannt | unbekannt | hoch | airline-sales |
| 1 | nein | Urlaubspiraten / Holidaypirates / reisetopia / meilenoptimieren | `https://www.urlaubspiraten.de/feed` | 200 application/xml; holidaypirates.com/feed 200; meilenoptimieren.com/feed 200; ?q=china wird ignoriert | RSS 2.0 | ja, aber irrelevant | selten | 05.11.2026 | niedrig | china |
| 1 | nein | Vielfliegertreff Forum RSS | `https://www.vielfliegertreff.de/forum/forums/-/index.rss` | 200 application/rss+xml (Forum 37 'Asien' ebenfalls 200) | RSS 2.0 | n/a | nein | 05.11.2026 | niedrig | china |
| 1 | nein | aerotelegraph.com (Luftfahrt-News, CH) | `https://www.aerotelegraph.com/feed` | 200 | RSS | n/a | nein | neuester 2026-11-04 20:14; ~20 Posts/Tag | niedrig | dach-deal-seiten |
| 1 | nein | airguru.de | `https://airguru.de/feed/` | 500 (WordPress 'kritischer Fehler', RSS-Body abgebrochen) | RSS (defekt) | n/a | n/a | wp-json posts: letzter Beitrag 2024-09-27 | hoch | dach-deal-seiten |
| 1 | nein | dealdoktor.de / sparhamster.at (Allgemein-Deal-Seiten) | `https://www.dealdoktor.de/feed/` | 200 | RSS | n/a | ja | neuester 2026-11-04 | niedrig | dach-deal-seiten |
| 1 | nein | eDreams Strecke HAM-ZRH | `https://www.edreams.de/flug/hamburg-zurich/HAM/ZRH/` | 200 | HTML mit Wochenbalken (18.-24. Jan, 61-86 €), JSON-LD lowPr… | ja (HAM) | ja | letzte 72 h | mittel | luecke-3 |
| 1 | nein | eDreams Strecken-Seite Hamburg–Zürich | `https://www.edreams.de/flug/hamburg-zurich/HAM/ZRH/` | HTTP 200, text/html, 241 KB | HTML, Titel + schema.org-FAQ-Text + Monatsbalken (<div clas… | ja (Strecke frei wählbar, HAM→ZRH getestet) | ja: 'Ab 56€ - Billige Flüge von Hamburg… | unbekannt, kein Datum im HTML | niedrig | airline-sales |
| 1 | nein | easyJet (Website-/Availability-API) | `https://www.easyjet.com/ejavailability/api/v79/availability/query?Origin=ZRH&Destination=HAM` | HTTP 403 'Access Denied' (Akamai) – ebenso /ejcms/cache15m/api/routes 404 und /en/cheap-flights/germany/hambu… | HTML (Fehlerseite) | n/a | n/a | n/a | hoch | preis-apis |
| 1 | nein | fly4free.com (Suche/Tag/Kategorie-Feeds) | `https://www.fly4free.com/?s=china&feed=rss2` | 403 (auch /tag/china/feed/, /flights/flight-deals/feed/); nur https://www.fly4free.com/feed/ liefert 200 mit … | RSS 2.0 (nur Hauptfeed) | nein, London/Dublin/Barcelona, gelegentlich 'Germany to Tha… | ja (€/£) | 04.11.2026 | niedrig | china |
| 1 | nein | holidayguru.ch / urlaubsguru.at (Urlaubsguru-Schwestern) | `https://www.holidayguru.ch/feed/` | 401 | HTML | n/a | n/a | n/a | hoch | dach-deal-seiten |
| 1 | nein | idealo Flug (flug.idealo.de) | `https://flug.idealo.de/` | HTTP 200 HTML (143 KB) mit serverseitigen Teaser-Deals, keine API | HTML | ja (z. B. BER→SKP) | ja ('85 € ab 58 €') | nicht datiert | mittel | preis-apis |
| 1 | nein | insideflyer.de | `https://insideflyer.de/feed/` | 200 | RSS | n/a | nein | neuester 2026-11-04 | niedrig | dach-deal-seiten |
| 1 | nein | meilenoptimieren.com | `https://meilenoptimieren.com/feed/` | 200 | RSS | n/a | selten (4/15, meist Meilen-Preise) | neuester 2026-11-04 09:30; sehr aktiv (15 It… | niedrig | dach-deal-seiten |
| 1 | nein | meilenoptimieren.com und insideflyer.de RSS | `https://meilenoptimieren.com/feed/` | HTTP 200 application/rss+xml 25 KB (insideflyer.de/feed/ 200, 123 KB); lufthansaflyer.boardingarea.com/feed/ … | RSS 2.0 | gemischt DE/CH, aber keine Abflug-Deals in den 25 Beispielen | nein (0 von 25) | meilenoptimieren neueste 05.11.2026 09:00, i… | niedrig | airline-sales |
| 1 | nein | reisetopia.ch / reisetopia.de Kategorie-Feeds & wp-json | `https://www.reisetopia.ch/feed/` | 403 (Cloudflare 'blocked') | HTML | n/a | n/a | n/a | hoch | dach-deal-seiten |
| 1 | nein | travel-dealz Region-Feed Schweiz | `https://travel-dealz.de/region/schweiz/feed/` | HTTP 200 | RSS 2.0 (612 KB) | gemischt | 18 von 20 | 03.11.2026 | niedrig | schweiz-hamburg |
| 1 | nein | travel-dealz Reiseziel-Feed Hamburg | `https://travel-dealz.de/reiseziel/hamburg/feed/` | HTTP 200 | RSS 2.0 (633 KB) | nein, Ziel Hamburg | 13 von 16 | 02.11.2026; ältester 01.11.2014 | niedrig | schweiz-hamburg |
| 1 | nein | travel-dealz.com (englisch) REST und Feeds | `https://travel-dealz.com/wp-json/wp/v2/deal?search=china&per_page=20` | 200 (Feeds /category/business-class/feed/ 200, 600 KB) | JSON / RSS | wie .de; origins-Terme anders nummeriert und oft leer | ja, mit €-Praefix ('€1,440') | neuester 03.11.2026 | niedrig | luecke-6 |
| 1 | nein | travel-dealz.de Kategorie-Feed First Class | `https://travel-dealz.de/kategorie/fluge/first-class/feed/` | 200 | RSS | n/a | nein (0/15) | neuester Eintrag 2025-12-14 | niedrig | dach-deal-seiten |
| 1 | nein | travel-dealz.de Region-Feeds (Schweiz / Deutschland) | `https://travel-dealz.de/region/schweiz/feed/` | 200 | RSS | ja | ja (18/20) | neuester 2026-11-04 13:41 | niedrig | dach-deal-seiten |
| 1 | nein | travel-dealz.de Tag-Feed Error Fares / Eilmeldungen | `https://travel-dealz.de/thema/errorfares/feed/` | 200 | RSS | n/a | nein | neuester 2021-12-31 | niedrig | dach-deal-seiten |
| 1 | nein | urlaubstracker.de | `https://www.urlaubstracker.de/feed/` | 200 | RSS | ja (DE) | ja (13/16) | neuester 2026-11-04 21:30; ~12 Posts/Tag | niedrig | dach-deal-seiten |

(152 Zeilen; Details je Quelle inkl. Feldnamen/Regex in den JSON-Dateien unter `notes`.)

---

## 3. Bauplan Runde 2

### 3.0 Spielregeln für parallele Agenten

- **Paket 0 zuerst und allein.** Es legt die gemeinsamen Schnittstellen an. Danach laufen die Pakete 1–6 und 10–11 parallel. Paket 7 setzt Paket 2 voraus. Die Pakete 8 und 9 bauen gegen die hier festgelegten Felder und werden zuletzt zusammengeführt.
- Jede neue Quelle bekommt **eine eigene Datei** unter `dealmonitor/sources/` und eigene Tests unter `tests/`. Fixtures bleiben unter 500 KB, also Rohdaten aus `docs/handoff/research/` kürzen.
- Es gilt nur die Standardbibliothek. Tests: `python3 -m unittest discover -s tests`, UI: `node tests/e2e/ui.mjs`. Auf `main` kommen nur getestete Stände, weil ein Push das Unraid-Image baut.
- **Item-Format** für alle neuen Feed-artigen Quellen ist dasselbe Dict wie aus `FeedSource.parse`: `title, link, published (datetime UTC), categories (list[str]), feed, description, description_text, merchant_price`. Dazu kommen optionale neue Schlüssel, die Paket 0 in `feed.analyze()` auswertet:
  - `price_eur: float`: fertig normalisierter Preis, hat Vorrang vor der Titel-Regex.
  - `origin_codes: set[str]`: IATA-Codes des Abflugs. Hat Vorrang vor `detect_origin()`. Das ist wichtig, weil Titel wie „Zurich, Switzerland to Mauritius for only €471“ heute `origin="unknown"` ergeben und `unknown` jeden Alarm passiert.
  - `dest_codes: set[str]`, `cabin: str`, `oneway: bool`
  - `expires: datetime | None`: abgelaufene Items überspringen.
  - `hot: str | None`: Marker wie ERROR FARE, CRAZY HOT oder PRICE DROP. Er wird zusätzlich als Kategorie angehängt, damit `radar.hot_reason` greift.
  - `uid: str`: stabile ID für Dedupe, z. B. Bluesky `post.uri` oder TD `id:modified`.

### Paket 0 – Fundament (allein, zuerst)

- **Ziel:** Neue Quellen sollen sich in `FeedSource` einhängen können, ohne dass `feed.py` mehrfach umgebaut wird.
- **Umsetzung:**
  - `Preset` bekommt `loader: str | None`, z. B. `"dealmonitor.sources.secretflying:fetch_items"`, importiert erst beim Aufruf. Signatur: `fetch_items(*, timeout: float = 20.0, state: dict | None = None) -> list[dict]`. Bei Fehlern wirft die Funktion `SourceError`.
  - Das Feld `state` ist ein Dict aus `store.kv`, z. B. für Cursor. Der Aufrufer speichert es nach dem Lauf zurück.
  - Dazu kommen `Preset.lane: "fast" | "normal"` und `FeedSource.stats: dict[source, {"items": int, "parsed": int, "newest": datetime | None, "error": str | None}]` für die Gesundheitsanzeige in Paket 6.
  - `analyze()` wertet die optionalen Item-Schlüssel aus 3.0 aus.
  - Neues Modul `dealmonitor/currency.py` mit `to_eur(amount, cur) -> float | None` und festen Kursen (EZB 04.11.2026: USD 1,1267, CHF 0,9188, GBP, AUD, CAD, CNY 7,5177). Optional ein täglicher Abruf von `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml` (Cube `currency`/`rate`).
- **Dateien:** `dealmonitor/sources/feed.py` (nur Preset, `_fetch_one`, `analyze`, `stats`), `dealmonitor/currency.py` (neu), `dealmonitor/sources/__init__.py`
- **Aufwand:** niedrig (1–1,5 h)
- **Test:** ein Dummy-Loader liefert ein Item mit `origin_codes={"ZRH"}` und `price_eur=471`, `analyze()` muss `origin="home"` und Preis 471 ergeben. Ein Item mit abgelaufenem `expires` wird übersprungen. `to_eur(291, "USD")` liegt bei ca. 258.

### Paket 1 – Secret Flying über Bluesky (Wert 5, Erstmelder)

- **Ziel:** Error Fares und Langstrecken-Deals ab DACH Stunden früher melden als alle anderen Quellen.
- **Primärweg:** `GET https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor=secretflying.bsky.social&limit=50&filter=posts_no_replies`. Antwort ca. 100 KB, gzip, ohne Login. Gelesen werden:
  - `feed[].post.record.text`
  - `record.createdAt` (ISO UTC)
  - `record.facets[].features[]` mit `$type == "app.bsky.richtext.facet#link"`, darin `uri` mit dem vollen Link `www.secretflying.com/posts/<slug>`; im Text ist der Link gekürzt
  - `post.uri` als `uid`
  - `post.indexedAt` (5–60 s nach createdAt)

  Beim ersten Lauf holt ein Backfill per `&cursor=` bis zu 150 Posts, das entspricht 6 Tagen.
- **Fallback:** `https://bsky.app/profile/did:plc:ffurylltuiy7da64xuf5lgne/rss`. Das ist die DID-URL direkt; der Handle-Link liefert 302 auf diese Adresse. Antwort 14 KB mit 30 Items. Ein `<title>` fehlt, der Text steht in `<description>`. pubDate hat die Form `05 Nov 2026 10:27 +0000`; `parsedate_to_datetime` kann es laut Test, sonst `strptime('%d %b %Y %H:%M %z')`.
- **Parser:**
  - Präfixe abtrennen und als `hot` merken: `Non-stop from`, `SUMMER:`, `XMAS & NEW YEAR:`, `PRICE DROP`, `CRAZY HOT`, `⚠️ ERROR FARE ⚠️`, `#ErrorFare`.
  - `#` entfernen, dann die Regex `^(?P<orig>.+?) to (?P<dest>.+?) (?:for only|from only) (?P<cur>[€$£])\s?(?P<amt>\d[\d.,]*)\s?(?P<iso>USD|CAD|AUD|EUR)?\s?(?P<trip>roundtrip|one-way)?` anwenden.
  - Die Airline steht nach `with `. `returning to …` bedeutet Open-Jaw. Mehrere Abflüge kommen vor („Stockholm or Gothenburg“).
  - Den Abflug über `regions.codes_for()` auf den Teil vor dem Komma abbilden (englische Namen Zurich, Munich, Vienna, Cologne, Geneva, Germany, Switzerland, Austria sind in `CITY_SYNONYMS` vorhanden).
  - Preise in $ oder £ über `currency.to_eur` umrechnen. **Den Preis nie aus dem Slug lesen:** belegt ist ein Slug mit `e213` bei 471 € im Text und einer mit `e323` bei 496 €.
- **Filter:** Hotels raus (`per night`, `5*`).
  - Abflug DACH: normales Item.
  - Abflug Nachbar-Hub: Item mit `origin_codes`, wird in Paket 4 bewertet.
  - Abflug außerhalb Europas: nur behalten, wenn `hot` gesetzt ist (Radar-Hinweis), sonst verwerfen.
  - Business-Teaser („A new Business Class deal from Europe has just been posted … Premium members can log in“): Item ohne Preis mit Kategorie `Business-Teaser`. Der Radar zeigt es als Hinweis „Business-Deal bei Secret Flying, Preis folgt“.
- **Takt:** Lane `fast` (10 min). Der Kanal postet ca. 28 Mal am Tag, davon ca. 1,3 DACH-Deals (8 von 150 Posts). Auch nachts posten (00:55 UTC gesehen), also keine Nachtpause.
- **Dateien:** `dealmonitor/sources/secretflying.py` (neu), `tests/test_secretflying.py`, `tests/fixtures/bsky_secretflying.json` (gekürzt aus `research/luecke-1/bsky_api.json` bzw. `p2.json`), `tests/fixtures/bsky_secretflying.xml` (aus `research/luecke-1/bsky_rss.xml`)
- **Aufwand:** mittel (3–4 h)
- **Test:**
  - Aus der Fixture kommen ZRH→Mauritius 471 € (home), FRA→Phuket 496 € (home) und ZRH→Johannesburg 471 € (home).
  - Ein USD-Post wird umgerechnet oder ohne Preis markiert.
  - Der Teaser liefert kein Preis-Item.
  - „Paris to Mauritius €498“ ergibt `origin_codes={"PAR"}` und wird nicht zu `unknown`.
  - Die RSS- und die API-Fixture liefern für dieselben Posts dieselben Items.

### Paket 2 – Google Flights: Preisniveau (`ds:1`) und Retry (Wert 5)

- **Ziel:** Ein echter Bezugspreis ab dem ersten Lauf und keine verlorenen Gegenprüfungen mehr.
- **Parser:** neue Datei `dealmonitor/sources/google_insights.py`, Vorlagen sind `research/tools-automatisierbar/price_insights_parser.py` und `research/luecke-3/parse_ds1.py`.
  - Regex: `AF_initDataCallback\(\{key: 'ds:1',(?: hash: '\d+',)? data:(.*?), sideChannel` mit `re.S`, danach `json.loads`.
  - Wenn `d[0] == 13`, ist das eine **ErrorResponse** (transient). Dann einen Retry nach 2 s.
  - Das Insight-Element **per Struktur suchen**, nicht fest über `d[5]`. Gesucht ist eine Liste `[code, [None,tiefst], [None,normal], [None,diff], [None,typ_von], [None,typ_bis], 1, …, [[ts_ms, preis], …]]`.
  - Ausgabe: `{level_code, lowest, typical, saving, typical_low, typical_high, history: [(date, price)…], level}`.
  - `level` selbst ableiten: `lowest < typical_low` ergibt „niedrig“, `> typical_high` ergibt „hoch“, sonst „typisch“. Bei den Codes ist nur 1 = niedrig belegt; 2 und 4 erschienen beide als „normal“, 3 und 5 sind **UNBESTÄTIGT**.
  - Fallback ist der Text `Die Preise sind (aktuell|derzeit) <span>niedrig|normal</span>` mit „– 67 € unter dem Normalpreis“. Den Text für „hoch“ hat niemand gesehen.
- **Messwerte als Testdaten:**

  | Strecke | Datum | Messwert |
  |---|---|---|
  | ZRH–HAM RT | 15.–19.01.27 | `[1,168,235,67,195,275]` |
  | FRA–PEK RT | 10.–24.03.27 | `[2,720,723,3,600,1050]` |
  | HAM–ZRH RT | 23.–27.12.26 | `[4,220,181,-40,155,250]` |
  | HAM→ZRH one-way | 21.01.27 | `[2,70,79,9,55,135]` |
  | ZRH→HAM one-way | 14.01.27 | Code 1, Minimum 82, typisch 105, Spanne 90–150 |
  | FRA–PEK Business | – | 2.898 / 3.061 / Spanne 2.600–3.750 |

  `insight.lowest` ist verlässlicher als die sichtbaren Zeilen: Die Seite zeigte 225 €, das Insight 168 €. Der Pfad `d[3][0]` hat 82 € verpasst.
- **Retry bei leerer Seite:** Wenn `parse_results()` leer ist, `NO_FLIGHTS_RE` nicht greift und die Seite kleiner als ca. 2,1 MB ist (leere Maske ca. 1,95 MB, Ergebnisse 2,3–2,45 MB), genau ein Retry nach 4 s.
  - Ist auch die zweite Seite leer, gilt das Ergebnis als „Abruf leer“. Das zählt **nicht** zum Netzfehler-Zähler `_consecutive_failures` und wird in der Statistik getrennt geführt.
  - Der Retry geht vom Budget ab.
  - Gemessen: 4 von 16 Erstabrufen leer, alle beim zweiten Versuch ok; 2 von 14 ErrorResponse, Retry ok. Das 0-Zeilen-Problem bei ZRH→HAM ist sehr wahrscheinlich derselbe Effekt (**UNBESTÄTIGT**).
- **Model:** `Offer.insight: dict | None = field(default=None, compare=False)` in `models.py`. Jede Offer aus `googleflights.query`/`to_offers` und jede Verify-Antwort bekommt das Insight der Seite. Das kostet keinen zusätzlichen Abruf.
- **Dateien:** `dealmonitor/sources/google_insights.py` (neu), `dealmonitor/sources/googleflights.py` (`query`, `fetch_html`-Aufruf, `to_offers`, `verify`), `dealmonitor/models.py` (ein Feld), `tests/test_google_insights.py`, eine neue Fixture mit `ds:1` (unter 500 KB gekürzt)
- **Aufwand:** mittel (3 h)
- **Test:**
  - Die alte Fixture `tests/fixtures/google_flights_zrh_ham.html` liefert `insight=None`, weil sie den Block nicht hat.
  - Die neue Fixture liefert die Messwerte oben.
  - Ein Fake-Fetcher liefert erst die leere Maske und dann Ergebnisse: Das Ergebnis ist ok und zählt 2 Abrufe.
  - Ein Fake mit `d[0]==13` und danach ok verhält sich genauso.

### Paket 3 – Ausbau der vorhandenen Feeds (Wert 4–5, `feed.py` gehört diesem Paket)

- **Ziel:** Deals lesen, die heute in vorhandenen oder bekannten Feeds stumm durchfallen.
- **content:encoded:** `parse()` liest `{http://purl.org/rss/1.0/modules/content/}encoded` und speichert es als `content_text` (`html.unescape`, Tags entfernen, auf ca. 20 KB kürzen). `Profile` bekommt `price_from` mit dem Teil `"content"` und `price_regex: str | None`.
- **reisetopia**, Preset einschalten:
  - URLs: `https://reisetopia.de/feed/`, `https://reisetopia.de/feed/?paged=2` und `https://reisetopia.de/tag/business-class/feed/`. Alle anderen Pfade liefern 403 (WAF).
  - Preis-Regex: `Buchbar ab (?:nur )?([\d.]+)\s*Euro pro Person`, Treffer 7 von 7. **Nicht** die erste €-Zahl im Text nehmen: Bei Condor nach Sansibar steht 2.200 im Fließtext, der Preis ist 2.129; bei Etihad nach Phuket stehen Gebühren von 520 € und 130 € im Text.
  - Strecke: Blockregex `Class von (.{3,40}?) nach (.{3,40}?)\s+Buchbar ab` auf das **letzte** „Class von“ vor dem Treffer anwenden (`rfind`; sonst greift bei LATAM die falsche Stelle). Alternativ Abflug und Ziel aus den `<category>`-Tags (Stadt, Land, Airline, Kabine).
  - Flug ist ein Eintrag nur, wenn die Kategorie „Flüge“ gesetzt ist **und** der Preisblock vorhanden ist. Ausschluss: Kategorien „Aviation“, „News“, „Award Flights“.
  - Ablaufdatum aus `Buchungszeitraum: Bis 12. November 2026` als `expires` lesen.
  - Lane `normal` (30 min), weil eine Seite 0,5–0,65 MB groß ist.
  - Testfälle: LATAM FRA–GRU 2.019, Qatar BER–Salalah 1.819, Condor FRA–Sansibar 2.129, China Eastern BCN–PVG 1.919 (Nachbar-Hub), Air India FRA–KTM 1.759, Etihad FRA–SIN 2.269, Condor nach Calgary 2.399.
- **YHBU**, neues Preset `yhbu`:
  - URL: `https://youhavebeenupgraded.boardingarea.com/category/billige-premium-tickets/feed/`, Profil `Profile(lang="de", price_from="title", use_categories=False)`.
  - Mit diesem Profil sind 47 von 50 Einträgen Flüge, 45 davon mit Preis.
  - Titelmuster: `DEAL: Hainan Business Budapest – China (Return): 1.490 Euro`. `parse_price` kann das schon (geprüft: 1490, Abflug BUD).
  - Titel ohne Preis werden verworfen. Ein Preis im Link-Slug zählt nicht.
- **premium-flights:**
  - Den Host auf `https://premium-flights.com/feed/` umstellen; mit `www` gibt es eine 301-Weiterleitung.
  - Neu: `https://premium-flights.com/tag/china/feed/`, `/tag/pek/feed/` und `/tag/shanghai/feed/`. Dort war ROM→Guangzhou 1.560 € zu finden, das `?s=china` **nicht** lieferte.
  - Optional die DACH-Tags `/tag/germany/feed/`, `frankfurt`, `munich`, `zurich`, `vienna`, `hamburg` (je 12 Items, 4–24 KB).
  - Weitere Tags wie `can`, `ctu` oder `hkg` sind **UNBESTÄTIGT**.
  - Die Kategorie „Active“ zeigt Gültigkeit an. Wie abgelaufene Deals aussehen, hat niemand gesehen, also zunächst nur loggen.
- **Neue Quellen registrieren** (über den Loader aus Paket 0, Module kommen aus den Paketen 1, 5 und 11):

  | Preset | Loader | Lane | Standard |
  |---|---|---|---|
  | `secretflying` | `secretflying:fetch_items` | fast | an |
  | `travel-dealz-rest` | `traveldealz_rest:fetch_items` | fast | an |
  | `errorfarealerts` | `errorfarealerts:fetch_items` | normal | aus |
  | `fly4free-business` | `https://www.fly4free.com/flight-deals/business-class/feed/` (RSS) | – | aus |

  `SOURCE_LABELS` und `README.md` (Quellenliste) entsprechend ergänzen.
- **Dateien:** `dealmonitor/sources/feed.py`, `tests/test_feed_presets.py` (neu), Fixtures aus `research/kritiker/rt.xml` (auf unter 500 KB kürzen), `research/luecke-6/*` und `research/kritiker/pf.xml`
- **Aufwand:** mittel (3–4 h)
- **Test:** die 7 reisetopia-Preise oben; YHBU-Fixture mit mindestens 40 Flug-Items mit Preis; der premium-flights-Tag-Feed liefert ROM–CAN 1.560 als Business mit `origin_codes={"ROM"}`.

### Paket 4 – Nachbar-Hubs und China-Abdeckung (Wert 5 für den China-Business-Alarm)

- **Ziel:** Business-Schnäppchen nach China ab BUD, LUX, ROM und ähnlichen Hubs als Deal mit Zubringer-Aufschlag zeigen, statt sie stumm zu verwerfen.
- **Umsetzung:**
  - `regions.NEIGHBOR_HUBS = {"A": {...}, "B": {...}}`:
    - Stufe A, mit Zug oder Auto in etwa 4 h erreichbar: LUX, BRU, AMS, SXB (neu: Straßburg), MIL, PRG, CPH. Aufschlag 80 €.
    - Stufe B, mit Zubringerflug: BUD, WAW, OSL, STO, ROM, MAD, BCN, ATH, IST, HEL. Aufschlag 180 €.
    - Die Aufschläge sind **Annahmen**. Sie werden über `appsettings` einstellbar (`hub_surcharge_a`, `hub_surcharge_b`, `hubs_enabled`).
  - Neues Modul `dealmonitor/hubs.py` mit `classify(codes) -> ("home"|"hub_a"|"hub_b"|"foreign", surcharge)`.
  - In `feed.origin_ok()` (nur diese Funktion) gilt: Ein Nachbar-Hub passt, wenn der Alarm Business, First oder Langstrecke ist und `price + Aufschlag <= max_price`. Das Offer bekommt `via_hub` und `surcharge` (zwei optionale Felder in `models.py`).
  - Bei Mehrfach-Abflug („Madrid & Amsterdam“) zählt der günstigste Hub.
  - In `radar.check` lässt `home_only` Hub-Treffer als Hinweis „Abflug Nachbar-Hub“ durch.
- **Rechenbeispiele aus Live-Daten:**
  - BUD 1.440 / 1.490 / 1.520 € + 180 € ergibt 1.620–1.700 €. Treffer.
  - LUX 1.515 € + 80 € ergibt 1.595 €. Treffer.
  - ROM–CAN 1.560 € + 180 € ergibt 1.740 €. Treffer.
  - OSL 1.875 € + 180 € ergibt 2.055 €. Kein Treffer, nur Radar-Hinweis.
  - BCN–PVG 1.919 € + 180 € ergibt 2.099 €. Kein Treffer, nur Hinweis.
- **China-Codes:** siehe Abschnitt 6.
- **Dateien:** `dealmonitor/regions.py`, `dealmonitor/hubs.py` (neu), `dealmonitor/sources/feed.py` (nur `origin_ok`, nach Paket 3 mergen), `dealmonitor/radar.py` (nur `check`), `dealmonitor/models.py` (2 Felder), `dealmonitor/appsettings.py` (3 Specs), `tests/test_hubs.py`
- **Aufwand:** mittel (3 h)
- **Test:**
  - Der YHBU-Titel „Hainan Business Budapest – China (Return): 1.490 Euro“ gegen Alarm `de-cn-business` ergibt einen Deal mit effektiv 1.670 € und `via_hub="BUD"`.
  - „Oslo → Beijing Business Class Round Trip 1875€“ ergibt keinen Deal.
  - Ein Economy-Kurzstreckenalarm lässt keine Hubs zu.
  - `regions.expand(["@CN"])` enthält die neuen Codes.

### Paket 5 – Travel-Dealz WP-REST (Wert 5, schmal und nachholfähig)

- **Ziel:** Deals und Ticker in 1–8 KB statt bis zu 2 MB RSS. Nach Ausfällen wird über `modified_after` nachgeholt, Ablaufdaten kommen aus `meta.suspend_date`.
- **Endpunkte:**
  - `https://travel-dealz.de/wp-json/wp/v2/deal?categories=24&per_page=100&orderby=modified&order=desc&modified_after=<ISO>&_fields=id,date_gmt,modified_gmt,link,title,categories,origins,destinations,airline,tags,meta.suspend_date`
  - `https://travel-dealz.de/wp-json/wp/v2/ticker?categories=24&per_page=100&orderby=modified&order=desc&modified_after=<ISO>&_fields=id,date_gmt,modified_gmt,link,title,categories,origins,destinations,meta.suspend_date`
  - Ticker-Einträge stehen **nicht** in `/deal` und sind damit zusätzliche Abdeckung.
- **Cursor:** in `store.kv` (`state`) das letzte `modified_gmt` minus 1 min ablegen. Beim ersten Lauf jetzt minus 3 Tage.
- **Zeit:** `date` ist Berliner Ortszeit, deshalb immer `*_gmt` verwenden.
- **Paging:** per_page höchstens 100, sonst 400. Eine Seite über `X-WP-TotalPages` liefert 400, also vorher abbrechen.
- **Ablauf:** `meta.suspend_date` normalisieren mit `.replace('T',' ')[:16]`; leer heißt Evergreen. Abgelaufene Deals verschwinden ohnehin aus der API (431 aktive Posts).
- **Ausgabe pro Post:**
  - `title` = `html.unescape(title.rendered)`
  - `categories` aus den IDs zu Namen gemappt, damit `is_flight_item`/`detect_cabin` greifen: 1757 Economy Class, 884 Business Class, 3225 Premium Economy, 1047 First Class, 24 Flüge
  - `origin_codes` aus den `origins`-IDs. Bei 54 % der Posts ist das Feld leer, dann zählt der Titel.
  - `uid` = `f"{id}:{modified_gmt}"`. Eine Titeländerung mit neuem Preis ergibt ein neues Item.
- **IDs:**
  - origins: ZRH 2971, BSL 2972, GVA 2973, HAM 2945, FRA 2940, MUC 2941, BER 2942, DUS 2939, VIE 2965, STR 2946, CGN 2938, HAJ 2957, BRE 2959, NUE 2947, SZG 2966, GRZ 2967, INN 2968, LEJ 2948, DRS 2949
  - China-Blätter: PEK 112, PVG 292, CAN 2808, CTU 2537, CKG 3097, SZX 3195, XIY 3169, HGH 3193, DLC 14472, KMG 14291, NKG 3017, URC 3749, HKG 293
  - tags: Error Fares 94, Eilmeldungen 3091, Gabelflüge 763, Oneway 39972
  - airline: Air China 1596, China Eastern 2463, China Southern 2504, Hainan 3192
  - Die Taxonomien einmal täglich über `/wp-json/wp/v2/origins?search=<name>&_fields=id,name,slug,count,parent` auflösen und cachen.
  - **Falle:** Der Elternterm `destinations=745` liefert zusammen mit `categories=` 0 Treffer, deshalb immer Blatt-IDs verwenden.
- **Fallback:** Bei 403 oder 429 bleibt der RSS-Preset `travel-dealz` aktiv. Doppelte Links aus REST und RSS über `normalize_link` entfernen.
- **Dateien:** `dealmonitor/sources/traveldealz_rest.py` (neu), `tests/test_traveldealz_rest.py`, Fixtures aus `research/travel-dealz-tiefe/sample-deal-flights.json` und `research/kritiker/td.json`; als Rezept `research/travel-dealz-tiefe/recipe_poll.py`
- **Aufwand:** mittel (2–3 h)
- **Test:**
  - Die Fixture ergibt Items mit Preis aus dem Titel. Die Preis-Regex: `(?:ab|für|nur)\s*(\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+)\s*(€|CHF|\$)`; bei zwei Preisen zählt der erste.
  - Ein abgelaufenes `suspend_date` wird übersprungen.
  - Der Cursor wandert weiter.
  - Eine Seite über TotalPages wird nicht abgerufen.

### Paket 6 – Takt und Quellen-Gesundheit (Wert 4)

- **Ziel:** Kurzlebige Fehlerpreise erwischen und stille Ausfälle sichtbar machen.
- **Takt:**
  - Neue Einstellung `fast_interval_minutes` (Standard 10, mindestens 5) für Presets mit `lane="fast"`.
  - Schwere RSS-Feeds bleiben bei `feed_interval_minutes` (30), Preise bei `interval_hours` (3).
  - Nachts nicht drosseln.
  - Ein Item mit `hot` oder mit einem Preis unter einer Alarmgrenze stößt sofort `scheduler.trigger(alert_id, "hot")` an. Dafür eine Budget-Reserve für Google von 5 Abfragen pro Lauf.
- **Gesundheit:** neues Modul `dealmonitor/health.py`, Zustände in `store.kv`.
  - Pro Quelle gibt es die Zustände OK, LEER (HTTP 200 mit 0 Items oder 0 geparsten), VERALTET und FEHLER.
  - Schwellen aus Messungen (Alter des neuesten Items; nicht `lastBuildDate`, das bei premium-flights 14 h nachhinkt):

    | Quelle | Schwelle | Messung |
    |---|---|---|
    | TD-REST | 20 h | größte Lücke 17,3 h nachts, `date_gmt` |
    | Bluesky Secret Flying | 10 h | größte Lücke 8,8 h |
    | premium-flights | 24 h | – |
    | ErrorFareAlerts | 48 h | Schübe mit Lücken bis 25 h |
    | reisetopia | 36 h | **Annahme** |
    | Google | Anteil „Abruf leer“ > 50 % in einem Lauf | – |

  - ntfy meldet nur den **Zustandswechsel**, dazu einmal täglich ein Heartbeat mit Items pro Quelle.
  - Das Format über die XML-Wurzel prüfen, nicht über `Content-Type`: TD `/feed/` liefert RSS als `text/html`.
- **Dateien:** `dealmonitor/health.py` (neu), `dealmonitor/scheduler.py`, `dealmonitor/appsettings.py`, `dealmonitor/notify.py`, API-Endpunkt `/api/sources/health` in `web.py` (nur die Route; die UI baut Paket 9), `tests/test_health.py`
- **Aufwand:** mittel (3 h)
- **Test:**
  - Eine Quelle mit 0 Items ergibt LEER und genau eine Meldung; beim zweiten Lauf kommt keine neue.
  - Ein Item, das 21 h alt ist, ergibt bei TD VERALTET.
  - Die schnelle Lane läuft im Fake-Takt dreimal je normalem Lauf.

### Paket 7 – Google-Datumsrotation für ZRH⇄HAM (Wert 5 für diesen Alarm, nach Paket 2)

- **Ziel:** Kein einzelner billiger Eurowings-Tag geht verloren. Die Tagesstreuung ZRH→HAM lag bei 82–174 € über 10 Stichproben.
- **Umsetzung:**
  - Neues Modul `dealmonitor/rotation.py` mit `slice_for_run(now, routes, horizon_days=90, per_run=23)`.
  - Lauf-Index `n = int(epoch/10800)`. Die Items sind (Route, Tag), bei 90 Tagen × 2 Richtungen also 180. Pro Lauf werden 23 Items ab `(n*23) % 180` abgefragt; nach 8 Läufen (24 h) ist alles einmal dran. Das kommt ohne Datenbankzustand aus.
  - Die nächsten 14 Tage sowie Freitag und Sonntag zählen doppelt.
  - Jede Abfrage ist one-way je Datum: `…q=Flights from ZRH to HAM on 2027-01-14 one way`. Sie dauert 0,65–1,0 s, die Seite ist 1,9–2,5 MB groß.
  - Treffer gelten nach dem Insight aus Paket 2: `min <= max_price` **oder** `min < typical_low` **oder** `min <= 0,6 * typical`.
  - Hin/Rück wird aus den günstigsten One-ways beider Richtungen mit `min_nights..max_nights` kombiniert und als „kombiniert aus 2 One-way“ gekennzeichnet (Vorschlag).
  - Budget: 40 pro Lauf, davon 23 für die Rotation, der Rest für Verify und Hot-Prüfung.
- **Dateien:** `dealmonitor/rotation.py` (neu), `dealmonitor/sources/googleflights.py` (nur `plan()`, Option `rotation` am Alarm), `dealmonitor/config.py`/`models.py` (Feld `rotation: bool`), `config/alerts.toml` (`zrh-ham-oneway` mit `rotation = true`), `tests/test_rotation.py`
- **Aufwand:** mittel (2–3 h)
- **Test:** In 8 aufeinanderfolgenden Läufen sind alle 180 Items mindestens einmal dran. Kein Lauf hat mehr als 23 Items. Die Kombination aus 70 € (HAM→ZRH) und 82 € (ZRH→HAM) ergibt Hin/Rück 152 €.

### Paket 8 – Deal-Bewertung: relativ, Error-Fare-Verdacht, quellenübergreifend (Wert 4)

- **Ziel:** Deals ohne eigene Historie bewerten. Die erste Meldung alarmiert, spätere Meldungen ergänzen sie nur.
- **Umsetzung:**
  - `principles.reference_price`: Solange die eigene Historie kleiner als `min_history_samples` ist und `offer.insight` vorhanden ist, gilt der Median von `insight.history`, sonst `insight.typical`.
  - Gründe in der Meldung: „31 % unter typischer Spanne (195–275 €)“ und „Google: Preise niedrig“.
  - Ein **Beinahe-Deal** liegt über der Grenze, aber unter `typical_low` oder mindestens 25 % unter dem Median. Er wird angezeigt, aber nicht gepusht. Für China ist das wichtig: 499 € liegt 17 % und 1.999 € 23 % unter `typical_low`.
  - **Error-Fare-Verdacht:**
    - Preis unter 40 % von `typical_low` oder vom Median
    - Business höchstens 1,6 × Economy auf derselben Strecke (real FRA–PEK 2.898/720 = 4,0)
    - Hot-Wort im Titel ohne Preis löst eine Google-Prüfung aus
  - `radar.HOT_RE` ergänzen um `CRAZY HOT`, `PRICE DROP`/`Preissturz` und `glitch`. Fehlerpreis, Mistake Fare und Preisfehler sind schon drin. `Sale`/`Flash` zählen nur zusammen mit einem Preis unter der Grenze.
  - **Dedupe über alle Quellen:** Schlüssel ist Abflug-IATA, Ziel-IATA, Kabine, Preis ±5 % und 7 Tage. Eine neue Tabelle `sightings(deal_key, source, first_seen, price)` in `store.py`.
  - Die erste Quelle alarmiert, weitere erweitern die Quellenliste und den Zeitvorsprung („Erstmeldung: Secret Flying, 19 h vor ErrorFareAlerts“). Heute gibt es Dedupe nur im Radar (`radar.deal_key`).
  - **Preissturz:** Jeder Quellenpreis wird pro Strecke gespeichert. Liegt ein Preis mindestens 25 % unter dem bisherigen Bestpreis oder dem 30-Tage-Median, gilt er als „vermuteter Error Fare“, auch ohne Stichwort.
- **Dateien:** `dealmonitor/principles.py`, `dealmonitor/radar.py`, `dealmonitor/monitor.py`, `dealmonitor/store.py` (Tabelle mit Migration), `tests/test_scoring.py`
- **Aufwand:** hoch (4 h)
- **Test:**
  - Ein Offer mit Insight 168/235/195–275 ohne Historie ergibt den Bezugspreis aus dem Median und den Grund „unter typischer Spanne“.
  - Derselbe Deal von zwei Quellen ergibt einen Alarm mit 2 Quellen und einer korrekten Vorsprungsangabe.
  - „CRAZY HOT“ mit Preis unter der Grenze ergibt einen Radar-Treffer.

### Paket 9 – UI und Demo (Wert 4 für die Abgabe)

- **Ziel:** Auf einen Blick sichtbar machen, warum etwas ein Deal ist und dass das System robust ist.
- **Umsetzung:**
  - **Deal-Karte** mit Badges:
    - „Google: Preise niedrig – 67 € unter Normalpreis“, typische Spanne
    - **62-Tage-Sparkline** als Inline-SVG ohne Bibliothek, mit Band „typisch von–bis“ und Marke für das Angebot
    - „läuft ab 12.11.“ (aus `expires`)
    - „Abflug Nachbar-Hub Budapest (+180 € Zubringer)“
    - „Erstmeldung Secret Flying, 19 h früher“
    - „3 Quellen“
  - **Seite Quellen-Gesundheit** aus `/api/sources/health`: Ampel, Alter des neuesten Eintrags, Items im letzten Lauf und Anteil „Google leer“. Dazu eine Liste „bewusst nicht genutzt“ mit Grund (Lufthansa, SWISS, Eurowings, Condor: 403; secretflying.com: Cloudflare; Amadeus: abgeschaltet), als Robustheitsnachweis.
  - Abschnitt **Beinahe-Deals** pro Alarm.
  - Hinweis bei ZRH⇄HAM: „Keine Deal-Seite meldet diese Strecke, Abdeckung über Google-Rotation (alle 90 Tage täglich geprüft)“.
- **Dateien:** `dealmonitor/web.py` (API-Felder durchreichen), `dealmonitor/static/*`, `dealmonitor/demo.py` (Demo-Daten mit Insight und Hub), `tests/e2e/ui.mjs`
- **Aufwand:** mittel (3 h)
- **Test:** E2E prüft Badge, Sparkline und die Statusseite mit Demo-Daten. Unit-Test: Die API liefert `insight` und `sources` pro Deal.

### Paket 10 – Pflege der Preis-APIs (Wert 3–5 mit Token)

- **Ziel:** Mit dem Token des Nutzers mehr Abdeckung holen und tote Quellen ehrlich kennzeichnen.
- **Travelpayouts** (Header `X-Access-Token`; alle Endpunkte liefern ohne Token 401, Inhalt **UNBESTÄTIGT**, nur aus der Doku):
  - `v2/prices/latest` mit `show_to_affiliates=false`, `period_type=year`, `limit=1000` sowie Ländercodes `origin=DE&destination=CN`. Heute nutzen wir `month`, `30` und `true`.
  - `v1/prices/calendar?origin=ZRH&destination=HAM&depart_date=2026-12&currency=eur` für Tagespreise. Mit Token ist das die Hauptquelle für ZRH⇄HAM, die Google-Rotation dient dann der Verifikation.
  - `v2/prices/month-matrix` als Alternative.
- **Amadeus:** Self-Service ist seit 17.07.2026 abgeschaltet (`test.api.amadeus.com` liefert CONNECT 502). In UI und Settings als „nicht mehr verfügbar“ sperren; den Code nicht löschen, solange Tests ihn nutzen.
- **Dateien:** `dealmonitor/sources/travelpayouts.py`, `dealmonitor/sources/amadeus.py` (nur Status und Docstring), `tests/test_travelpayouts.py`
- **Aufwand:** niedrig bis mittel (2 h)
- **Test:** Die URL-Bauer erzeugen die neuen Parameter. Fixture-Parser für `calendar` (Struktur aus der Doku).

### Paket 11 (optional) – ErrorFareAlerts (Wert 3)

- **Ziel:** Business-Preise aus dem Premium-Bereich von Secret Flying nachziehen, deutschsprachig und zu 100 % ab DACH. **Keine Frühwarnung:** Die Quelle ist 19–20 h langsamer als Secret Flying und Tage langsamer als travel-dealz.
- **Abruf:** `https://errorfarealerts.com/deals/flights?page=0` alle 1–2 h. Antwort ca. 300 KB HTML ohne Feed (`/rss` und `/feed` liefern 302). Kein Cloudflare, robots.txt erlaubt den Abruf.
- **Karte:**
  - Titel per `<h3 class="mb-1">([^<]*)</h3>`
  - Zeit per `(\d\d\.\d\d\.\d{4} \d\d:\d\d)`. Die Zeitzone ist widersprüchlich: Laut Detailseite ist es UTC, laut Nachrecherche Ortszeit (**UNBESTÄTIGT**).
  - Link `/deals/flights/<slug>` mit Emoji, vor dem Abruf über `quote(safe='/%')` kodieren
  - IATA per `\(([A-Z]{3})\)`
  - Preis per `ab\s+([\d.]+)\s*€` bzw. `</sup>\s*(\d[\d.]*)`
  - Das Präfix „✈️“ entfernen.
- **Backfill:** `?fromIata=FRA&toIata=PEK&page=N` liefert die Historie (Business 1.335–1.790 €).
- **Dateien:** `dealmonitor/sources/errorfarealerts.py` (neu), `tests/test_errorfarealerts.py`, Fixture gekürzt aus `research/luecke-1/efa_all.html`
- **Aufwand:** mittel (2 h)
- **Test:** 10 Karten mit DACH-IATA; der Preis aus „ab 1.959 €“ ergibt 1959.

### Abschluss – Integration und Abnahme (allein, zuletzt)

Alle Pakete werden gemergt. Dann laufen `python3 -m unittest discover -s tests` und `node tests/e2e/ui.mjs` sowie ein Live-Dry-Run (Feeds und 3 Google-Abfragen). Danach README (Quellenliste, Einstellungen) und `.env.example` aktualisieren. Erst dann geht der Stand auf `main`.

---

## 4. Optional und später

| Idee/Quelle | Nutzen | Warum nicht jetzt |
|---|---|---|
| SerpApi (`google_flights`, `google_flights_calendar`, `google_flights_deals`) | offizieller Google-Kalender, 250 Suchen pro Monat gratis | Key nötig (live 401 ohne Key) |
| Kayak-Länderseiten (`kayak.de/fl%C3%BCge/Deutschland-DE0/China-CN0`, JSON-LD `@type=Flight`, `AggregateOffer.lowPrice`) | Breitenradar DE/CH→China (Kandidaten danach per Google prüfen) | Cache-Preise, teils 36 h Reisedauer (Filter ≤ 24 h nötig), kein Datum, nur Economy, kayak.ch in CHF. Der Wert DE→CN 283 € ist **nicht gegengeprüft** |
| Ryanair farfnd (`services-api.ryanair.com/farfnd/v4/oneWayFares`) | Zubringerpreise zu Nachbar-Hubs (VIE–MXP 17 €) | bedient ZRH⇄HAM nicht, nur ein Baustein |
| Travelpayouts `airports.json`/`routes.json` (200 ohne Token) | Nachbarflughäfen per Entfernung, Nonstop-Plausibilität | einmalig gebündelt ablegen, kein Laufzeitabruf |
| nager.at / OpenHolidays | Ferienhinweise (Weihnachten HH 21.12.–01.01.) | 2027 nur 1 Brückentag, geringer Nutzen |
| Vielfliegertreff Business-/Economy-Tarife-RSS | Business-Tarife ab HAM/DUS/FRA | ca. 1 Thread pro Woche, viel Gegenrichtung und Nicht-DACH. Ob nach letzter Antwort sortiert wird, ist **UNBESTÄTIGT**, Dedupe über den Link nötig |
| travel-dealz-Forum `forum.travel-dealz.de/c/deal/6.rss` | Nutzer-Sondertarife (BUD–BKK 57 €) | ca. 1 Deal pro Woche |
| ferienpiraten.ch / preispirat.ch (`?s=SWISS&feed=rss2`) | CH-Deals in CHF, SWISS/Edelweiss-Sales als Info | kaum Strecken-Preise, ZRH⇄HAM nie |
| Google-News-RSS `site:reisetopia.de Business Class when:3d` | Titel von reisetopia-Deals, die im Feed fehlen könnten | kein Preis, Links sind verschlüsselte Redirects |
| ErrorFareAlerts-Telegram `t.me/s/errorfarealerts` | Fallback zur EFA-Webseite | Teilmenge und später als die Webseite |
| travel-dealz Economy-/Flüge-Kategorie-Feeds, `visibility/hot`, `/feed/?post_type=deal` | Fallback, falls WP-REST gesperrt wird | REST ist schmaler und gleichwertig |
| preisjaeger.at, travelfree.info, frankfurtflyer `/feed/rss2/` | AT- bzw. EU-Deals | geringer DACH-Anteil |
| Airline-getriebene Alarme (Air China, China Eastern, China Southern ab DACH, unabhängig vom Ziel) und China-Stopover-Hinweise | Asien-Deals mit Umstieg in PEK/PVG | eigene Alarm-Logik, nach der Abgabe |
| Preis-pro-km-Heuristik (< 4 ct/km Economy, < 9 ct/km Business) | fängt Deals ohne passenden Alarm | Koordinatentabelle nötig |
| Nachtest vom Unraid-Heimnetz: Turkish, Qatar, Etihad, KLM, Air France, Discover, China Southern, flugdeals.ch, swissflightdeals, premium-flights.de, r/flightdeals (429) | eventuell andere Antworten von einer Heim-IP | von hier nicht prüfbar |
| Google Explore (RPC `GetExploreDestinations`) und Kalender-RPC `GetCalendarGraph` | ein Abruf für alle Ziele bzw. 60 Tage | Format unbekannt, Versuch ergab 400, fragil |
| Air-China-Aktionsliste (`airchina.de/DE/DE/promotion/<Kampagne>`) | Hinweis „neue Aktion“ | keine Preise im HTML |

---

## 5. Geprüft und verworfen

| Quelle | Grund |
|---|---|
| secretflying.com direkt (`/feed/`, `/wp-json/`, `/error-fares/`, Einzelposts) | Cloudflare 403. Nur `/europe-flight-deals/` kam durch, ist aber unvollständig (Phuket und Johannesburg fehlten), ohne Uhrzeit und mit falschen Slug-Preisen. Der Ersatz ist Bluesky |
| Amadeus Self-Service | seit 17.07.2026 abgeschaltet |
| Lufthansa Open API, Duffel, Kiwi Tequila, Skyscanner-API, Trip.com, RapidAPI | Partnerstatus, KYC, Einladung oder nicht prüfbar |
| Angebotsseiten von Lufthansa, SWISS, Austrian, Eurowings, Edelweiss, Condor, Finnair, Air Serbia, easyJet, Norwegian | 403 oder Captcha (Akamai, Cloudflare) |
| Emirates, Cathay, LOT, China Eastern, Air China, Hainan, Google `flights-from-*.html`, Google `/travel/flights/deals`, ITA Matrix, Momondo Explore | HTTP 200, aber keine Preise im HTML (nur JavaScript) |
| Skyscanner (Captcha), Kayak Explore (404/403), Opodo, checkfelix, skiplagged, AZair, idealo, fluege.de, flightconnections | gesperrt oder ohne serverseitige Preise |
| eDreams/Opodo-Streckenseiten | nur Monatsbalken ohne Tag, nur zur Plausibilität |
| Telegram-Kanäle (travel_dealz, urlaubspiraten, fly4free, secretflying, mydealz, reisetopia u. a., 40+ Namen) | keine Web-Vorschau (302). flugdeals, flightdeals und errorfares sind tot |
| Skyscanner-Telegram (Spiegel von Secret Flying) | kein DACH, kein Originallink |
| Mastodon (@traveldealz, @traveldealzde, #errorfare) | veraltet, doppelt zu travel-dealz |
| Nitter/X | 410, 451, 502 |
| Bluesky-Konten anderer Deal-Seiten | leer oder nicht vorhanden. getflydown ist nur Ryanair-Kurzstrecke, escape.flights nur USA |
| Reddit | Deal-Sub 429/403, die übrigen Subs sind Frage-Foren |
| Google-News-RSS allgemein | veraltet und Rauschen, „error fare when:7d“ lieferte 0 Treffer |
| The Flight Deal, FareDealAlert, Thrifty Traveler, Points-Blogs (OMAAT, VFTW, Loyalty Lobby, God Save The Points, TPG), Travelzoo | nur USA oder keine Preis-Deals |
| Jack's Flight Club, Going, Mighty Travels | Bezahl- oder Newsletter-Dienste ohne Feed |
| travelfree.info | 429 (in Teil 1 noch 200) |
| fly4free Error-Fare-Feed | seit 22.08.2025 tot |
| fly4free Such- und Tag-Feeds | 403 |
| fly4free.pl/.cz/.hu, holidaypirates | PLN bzw. Pauschalreisen |
| travel-dealz.com (EN) | übersetzte Kopie von .de |
| travel-dealz Reiseziel-Feed Hamburg, Region-Feeds, Abflug-Feed HAM, First-Class-Feed, `thema/errorfares` | Kreuzfahrten, Pauschalreisen und Bewertungen, keine Flugdeals |
| mydealz Such- und Tag-RSS, `/rss/gruppe/preisfehler` | 404 bzw. Produkte statt Flüge |
| reisetopia Kategorie-, Such- und wp-json-Pfade, reisetopia.ch | 403 (WAF) |
| urlaubsguru, holidayguru.ch, reisereporter, travelbook, reiseuhu, flynous, blick.ch, 20min, watson, travelnews.ch, airguru, firstclassandmore, fliegen-sparen, flugdeals.de/.at, nur-billigfluege | 401, 403, 404, 500, tot oder ohne Feed |
| urlaubspiraten, meilenoptimieren, insideflyer, aerotelegraph, dealdoktor, sparhamster, urlaubstracker | News, Meilen oder Pauschalreisen, keine Flugpreise |
| Mehrere Abflughäfen in einer Google-Abfrage („HAM,BRE“), Verkaufsland-Arbitrage (`gl=IN/CN/TR`) | 0 Ergebnisse bzw. keine Preisänderung |
| Aviasales-Karten-Endpunkte | 302, 404 oder TLS-Fehler |

---

## 6. Regionen, Nachbarflughäfen, China-Abdeckung

### Lücken in `dealmonitor/regions.py` (gehört zu Paket 4)

- **@CN / @CN_HK:** DLC, CGO, HAK und SYX stehen in `CN_AIRPORTS`, fehlen aber in den Regionen. Ganz fehlen FOC, HRB, CGQ, URC, WNZ, NNG, TNA, HFE und KHN. Vorschlag: eine neue Region `@CN_ALL` mit allen genannten Codes. `@CN_MAJOR` bleibt für die Preis-APIs, weil weniger Abfragen nötig sind.
- **Taipeh (TPE)** gehört nicht zu China, wird aber oft mit Air China gebündelt. Vorschlag: eine optionale Region `@CN_TW`.
- **Synonyme fehlen** (alle von travel-dealz als Reiseziele geführt):
  - Fuzhou
  - Harbin
  - Ürümqi/Urumqi
  - Wenzhou
  - Nanning
  - Changchun
  - Jinan
  - Hefei
  - Sanya
  - Haikou
  - Dalian
  - Zhengzhou
  - Shenyang
- **Xi'an:** Der Stadtcode ist SIA (travel-dealz schreibt `[SIA,XIY]`), im Repo steht XIY. Ob Travelpayouts SIA braucht, ist **UNBESTÄTIGT**.
- **Abflug China-Alarme:** `de-cn-economy` nutzt `@DE`, `de-cn-business` nutzt `@DE_HUBS` (FRA, MUC, BER, DUS, HAM). ZRH und VIE sind nicht dabei, obwohl Google und Kayak dort Treffer zeigen (VIE–PVG Google 718 €; ZRH–PEK Kayak 274 CHF, **UNBESTÄTIGT**). Laut `routes.json` gibt es Nonstop-Verbindungen DUS-PEK, FRA-NKG/PEK/PVG, MUC-PEK/PVG, VIE-PEK und ZRH-PEK/PVG (**UNBESTÄTIGT**). Vorschlag: Der Nutzer entscheidet; als Option `@DACH` für Business.
- **`@DE`** deckt DTM, FMM, DRS und weitere nicht ab. Für China ist das egal, weil `@DE_HUBS` korrekt ist.
- **Nachbar-Hubs:** `WORLD_AIRPORTS` braucht zusätzlich SXB (Straßburg), sonst sind alle Hubs aus Paket 4 vorhanden. Basel-Mulhouse (BSL) zählt schon als Heimat.

### ZRH⇄HAM

- Direktflüge gibt es mit Eurowings und SWISS (Helvetic), ca. 80–90 min. Edelweiss ist nicht bestätigt. Ryanair fliegt die Strecke nicht.
- **Nachbarn bringen auf dieser Strecke wenig:**
  - BSL→HAM ohne Nonstop ab 221 €
  - STR→HAM Eurowings 157 € RT (nicht billiger)
  - BER→ZRH 155 € (SWISS nonstop)
  - MUC→HAM 170 € (LH nonstop)

  Die Regionen klein halten, sonst verschwendet man Budget.
- **Preisbild Google (live gemessen):**
  - HAM→ZRH one-way 10.12.: Eurowings 70/90/110 €
  - SWISS 173 €
  - RT 14.–17.01.27: Eurowings 166 €, SWISS 205–236 €
  - Typische Spanne one-way 55–135 €, RT 195–275 €
- **Abgleich mit den Alarmen:** Die konfigurierten Grenzen (RT 110 €, one-way 45 €) liegen unter `typical_low`. Treffer kommen also nur bei Sales oder Fehlerpreisen. Mit Paket 8 entsteht dazwischen der Bereich „Beinahe-Deal“.
- **Ehrlich dokumentieren:** Für diese Strecke gibt es keine redaktionelle Deal-Quelle. travel-dealz hatte in 12 Monaten keinen einzigen ZRH⇄HAM-Deal, `origins=2971` ergab nur 6 Treffer.

### Deutschland→China

- **Realistisches Preisbild:**
  - Economy FRA–PEK normal 723 €, typisch 600–1.050 €
  - Business normal 3.061 €, typisch 2.600–3.750 €
- **Was die Alarmgrenzen bedeuten:**
  - Economy unter 499 € gibt es nur bei Sales oder Fehlerpreisen. Ab Nachbar-Hubs gab es Premium Economy AMS→Shanghai für 716 €. Kayak meldet „283 €“ (Cache-Preis mit 36 h Reisedauer, **nicht bestätigt**).
  - Business unter 1.999 € gab es in 6 Wochen ab DACH genau einmal: FRA–PEK 1.123 € mit Turkmenistan Airlines. Sonst kamen alle Treffer ab Nachbar-Hubs (BUD, LUX, ROM, OSL, BCN). Paket 4 ist deshalb Pflicht.
- **Viele „China“-Deals sind keine China-Ziele.** Air China und China Eastern verkaufen Tickets nach Bangkok (448–482 €), Hongkong (469–484 €) und Taipeh (411 €) mit Umstieg in PEK oder PVG. Die Rauschfilterung über die Airline (`AIRLINE_NOISE_RE`) ist hier richtig.
- **Nicht als Fakt in die UI übernehmen:** Visumfreiheit bis 31.12.2026, 240-h-Transit und die Flugfrequenzen von Air China und China Eastern stammen aus Suchsnippets.

---

## 7. Kritiker-Zweifel und Unbestätigtes (bei der Umsetzung beachten)

- **ErrorFareAlerts:** Die Bewertung ist von 5 auf 3 gesenkt. Die Quelle ist ein deutschsprachiges Echo und taugt nur für Business-Preise und Backfill.
- **TD-REST:** Der Elternterm 745 liefert zusammen mit `categories` 0 Treffer, deshalb nur Blatt-IDs (bestätigt). `tags=94` und `3091` liefern aktuell 0 aktive Einträge. Die Sofort-Priorität lässt sich deshalb nur per Fixture testen, nicht live vorführen.
- **TD-REST „kein Rate-Limit“:** belegt nur mit 12 Abrufen in 10 s. Freundlich pollen (alle 10 min, `_fields`, User-Agent).
- **Google `ds:1`:** Die Feldbedeutung ist abgeleitet. Die Codes 3 und 5 sowie der Text für „hoch“ sind ungesehen. Der Parser muss bei einer Strukturänderung `None` liefern, nicht abstürzen.
- **Secret Flying:** Ob jeder Deal auch auf Bluesky landet, ist **UNBESTÄTIGT**. Die Stichprobe vom 04.11. war vollständig. Ob DACH-Preise auch in CHF erscheinen, ist ebenfalls offen.
- **Neuester Bluesky-Post 09:46:** Das war Paris–Mauritius, nicht Zürich. Am DACH-Anteil von 3 aus 30 ändert das nichts.
- **reisetopia:** Der Feed ist möglicherweise unvollständig. Finnair HKG und LH Bogotá standen in Google News, aber nicht auf den Feed-Seiten 1–3.
- **premium-flights:** Wie die Kategorie „Active“ bei abgelaufenen Deals aussieht, ist **UNBESTÄTIGT**. Feeds immer ohne `www` abrufen.
- **Zubringer-Aufschläge** von 80 € und 180 € sind Annahmen.
- **Canary-Schwelle für reisetopia** von 36 h ist eine Annahme.
