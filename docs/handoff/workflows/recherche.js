export const meta = {
  name: 'quellen-recherche',
  description: 'Tiefe Recherche: Welche Flug-Deal-Quellen fehlen noch? Jede Quelle live antesten, Bericht schreiben',
  phases: [
    { title: 'Recherche', detail: '5 Rechercheure mit verschiedenen Blickwinkeln, live-geprüft' },
    { title: 'Kritik', detail: 'Lücken-Kritiker: Was fehlt noch?' },
    { title: 'Bericht', detail: 'Synthese als RESEARCH.md' },
  ],
}

const SCRATCH = '/tmp/claude-0/-home-user/8771aaf7-3b29-551f-a287-c88013dcf7a7/scratchpad'
const OUT = SCRATCH + '/research'

const COMMON = `Kontext: Wir bauen einen Flug-Deal-Monitor (Python, nur Standardbibliothek, läuft als Docker-Container auf einem Heimserver) nach dem Vorbild von travel-dealz.de. Alarme des Nutzers: Zürich⇄Hamburg (Economy, Hin/Rück und one-way), Deutschland→China (Economy ≤ 499 €, Business ≤ 1.999 €). Ziel des Nutzers: KEINEN krassen Deal verpassen (Error Fares, Business-Class-Schnäppchen, sehr günstige Langstrecke) – Abflug DACH. Bereits angebunden: Travelpayouts/Aviasales Data API (prices_for_dates, v2/prices/latest), Google Flights per HTTP-GET der Ergebnisseite (inoffiziell), Amadeus Self-Service (flight-dates, flight-offers), RSS: travel-dealz.de (Hauptfeed, Ticker, Business-Class-Kategorie, Suche ?s=&feed=rss2), mydealz.de/rss/gruppe/fluege, fly4free.com/feed, premium-flights.com/feed, urlaubspiraten.de/feed, reisetopia.de/feed. Bekannt gesperrt/unbrauchbar von hier: secretflying.com (Cloudflare 403), urlaubsguru.de (401), reisereporter.de (403), flyertalk RSS (403), travel-dealz.com/.eu (= englische Kopie).
REGELN: Du darfst NUR lesen und im Netz recherchieren (WebSearch, WebFetch, curl). Du änderst NICHTS unter /home/user/deal-monitor (dort läuft gerade ein Build). Schreibe deine Rohnotizen/Heruntergeladenes nur nach ${OUT}/<dein-kuerzel>/ (mkdir -p). Jede Quelle, die du empfiehlst, musst du LIVE von dieser Maschine aus angetestet haben (curl mit Browser-User-Agent, max. 2 Abrufe pro URL): HTTP-Status, Format (RSS/JSON/HTML), Anzahl Einträge, Beispiel-Titel, Anteil echter Flug-Deals, Abflug DACH ja/nein, Preis im Titel ja/nein, Aktualität (Datum der neuesten Einträge). Keine Behauptungen ohne Test. Keine Scraping-Bibliotheken installieren. Keine Logins. Keine Massenabrufe. Ergebnis als strukturierte Liste zurückgeben: pro Quelle {name, url, status, format, items, flight_share, dach_origin, price_in_title, freshness, effort (niedrig/mittel/hoch), value (1-5), recommendation (ja/optional/nein), notes}.`

const SRC = { type: 'object', properties: { sources: { type: 'array', items: { type: 'object', properties: {
  name: { type: 'string' }, url: { type: 'string' }, status: { type: 'string' }, format: { type: 'string' }, items: { type: 'integer' },
  flight_share: { type: 'string' }, dach_origin: { type: 'string' }, price_in_title: { type: 'string' }, freshness: { type: 'string' },
  effort: { type: 'string' }, value: { type: 'integer' }, recommendation: { type: 'string' }, notes: { type: 'string' } },
  required: ['name', 'url', 'status', 'format', 'effort', 'value', 'recommendation', 'notes'] } }, summary: { type: 'string' } }, required: ['sources', 'summary'] }

phase('Recherche')
const ANGLES = [
  { key: 'dach-deal-seiten', prompt: 'Deutschsprachige Deal-Seiten und Blogs (DE/AT/CH) mit RSS/Atom-Feeds oder JSON: z. B. fliegen-sparen.de, frankfurtflyer.de, meilenoptimieren.com, yhbu.de (You Have Been Upgraded), firstclassandmore.de, boardingarea-Deutsch, travelzoo.com/de, ferienpiraten.ch, flugdeals.ch, holidaycheck, flugladen.de, billigfluege.de, nur-billigfluege, urlaubstracker.de, reisefieber, travelbook, chefkoch-nein. Prüfe systematisch: /feed/, /rss, /feed.xml, /atom.xml, /?feed=rss2, Kategorie-Feeds (WordPress: /kategorie/<slug>/feed/ bzw. /category/<slug>/feed/), Tag-Feeds. Für travel-dealz.de zusätzlich: ALLE Kategorie-Feeds ermitteln (Economy Class, First Class, Premium Economy, Error Fare/Fehlerpreise, Gabelflüge, Meilenschnäppchen, Strecken-News) – Slugs über die Kategorie-Links auf der Startseite oder Sitemap finden – und ob es Tag-Feeds (z. B. /tag/hamburg/feed/, /tag/china/feed/) gibt.' },
  { key: 'preis-apis', prompt: 'Flugpreis-APIs, die ohne Partnervertrag nutzbar sind (kostenlos oder mit kostenlosem Kontingent): Lufthansa Open API (developer.lufthansa.com – gibt es Lowest-Fare/Offers-Endpunkte? Kontingent? Registrierung?), Travelpayouts/Aviasales Data API: ALLE weiteren Endpunkte prüfen (v1/prices/calendar, v1/prices/monthly, v1/prices/cheap, aviasales/v3/grouped_prices, aviasales/v3/get_special_offers, v1/city-directions, v2/prices/month-matrix, v2/prices/week-matrix, v2/prices/nearest-places-matrix) – Doku unter https://support.travelpayouts.com/hc/en-us/articles/203956163 bzw. travelpayouts.github.io/slate – welche bringen mehr Abdeckung (ein Preis pro Tag statt 30 pro Monat) und welche liefern Airline-Sonderangebote? Amadeus Self-Service: aktuelle Free-Quota pro Monat je Endpunkt, Test vs. Production, Flight Cheapest Date Search Verfügbarkeit in Production. Außerdem: Ryanair fare finder (services-api.ryanair.com/farfnd/v4/...), easyJet, Wizz Air, Eurowings, Swiss/Edelweiss – gibt es öffentliche, loginfreie Preis-JSON-Endpunkte (live testen!)? SerpApi Google Flights (Free-Kontingent), Duffel (Test/Live), Kiwi Tequila (aktueller Status für neue Partner), Skyscanner Partner API (Zugangshürde), RapidAPI-Angebote (Kosten/Free-Tier), Trip.com/Ctrip (für China-Flüge relevant!) – gibt es dort Affiliate-APIs mit Preisen? Priorisiere, was für Deutschland→China und Zürich⇄Hamburg wirklich Preise liefert.' },
  { key: 'error-fares-premium', prompt: 'Error-Fare- und Premium-Cabin-Spezialisten (international, EN/DE) mit Feeds oder offenen JSON-Endpunkten: secretflying (gibt es alternative Zugänge, z. B. feedburner, Tag-Feeds, Telegram-Webpreview, nitter?), travelfree.info, errorfare-Seiten, Jack\'s Flight Club, Going (ehem. Scott\'s), Fly4free (Kategorien/Tags-Feeds für "Germany"/"Zurich"/"Hamburg"/"Business"), premium-flights (Kategorie-Feeds "From Europe"), YHBU, God Save The Points, One Mile at a Time (Deals-Kategorie-Feed), View from the Wing, Loyalty Lobby, The Flight Deal, Airfarewatchdog, FareDealAlert, Mighty Travels Premium, Flightdeal-Reddit (r/Flights, r/Flugdeals, r/travel – JSON/RSS: https://www.reddit.com/r/<sub>/.rss mit UA), Telegram-Kanäle mit Web-Vorschau (t.me/s/<kanal> – z. B. Travel-Dealz, Urlaubspiraten, Secret Flying). Teste alles live.' },
  { key: 'wie-findet-travel-dealz', prompt: 'Wie finden Travel-Dealz, Secret Flying & Co. ihre Deals, und welche davon sind automatisierbar? Recherchiere Interviews/Artikel/FAQ ("Wie wir Deals finden", "Prinzipien", "Hand Picked", Tools: Google Flights Explore/Price Graph/Date Grid, ITA Matrix, Skyscanner Everywhere, Momondo Flight Insight, Kayak Explore, Airline-Newsletter/Sales-Seiten, Fare-Alerts). Für jedes Tool: gibt es einen loginfreien HTTP-Zugang, den wir mit Standardbibliothek nachbauen könnten (z. B. Google Flights Explore-URL mit Region "China" und flexiblen Daten, Google Flights Price-Graph/Date-Grid als Server-HTML, ITA Matrix API-Endpunkt, Kayak Explore JSON)? Live testen (max. 3 Abrufe pro Tool, ≥ 3 s Abstand, Chrome-UA, Accept-Language de). Außerdem: Airline-Sale-Seiten mit festem Format (Lufthansa Angebote, Swiss Deals, Eurowings Angebote, Condor, Discover, Air China/China Eastern/Southern DE-Seiten, Cathay, Qatar, Emirates, Turkish, Etihad, Finnair, KLM/Air France Promo) – welche liefern maschinenlesbare Preise (JSON im HTML, RSS)?' },
  { key: 'china-schweiz-spezial', prompt: 'Spezialrecherche für die konkreten Alarme: (1) Deutschland→China: Welche Airlines/Routen (Air China, China Eastern, China Southern, Hainan, Juneyao, Sichuan, Xiamen, Lufthansa, Swiss, Austrian, Cathay via HKG, Qatar/Emirates/Turkish/Etihad, Aeroflot-Nein, Air Serbia, LOT, Finnair, KLM/AF) sind relevant, welche Stadt-Codes (BJS/SHA/CAN/SZX/CTU/HGH/CKG/XMN/NKG/WUH/TAO/…) müssen abgedeckt sein, gibt es China-spezifische Deal-Quellen (Trip.com Sale-Seiten, chinesische Airline-Promo-Seiten in DE/EN, "China Flüge" Deal-Blogs, Visa-frei-Infos die Deals treiben)? (2) Zürich⇄Hamburg: welche Airlines fliegen (Swiss, Eurowings, Edelweiss?), typische Preise, Alternativen (BSL/GVA, Bremen/Lübeck/Hannover als Nachbar-Flughäfen), gibt es Schweizer Deal-Quellen (ferienpiraten.ch, travel-dealz CH-Tag, flugdeals.ch, swissflightdeals, blick.ch Deals) mit Feeds? Welche Nachbar-Flughafen-Regionen sollten wir als Preset anbieten (@CH_ALL inkl. FDH/STR? @HAM_NEAR inkl. BRE/HAJ/LBC)? Alles live prüfen, was Feeds angeht.' },
]
const results = await parallel(ANGLES.map(a => () => agent(`${COMMON}

DEIN BLICKWINKEL "${a.key}": ${a.prompt}
Notizen nach ${OUT}/${a.key}/. Sei gründlich – nimm dir Zeit, viele Kandidaten anzutesten. Nur live geprüfte Quellen empfehlen.`, { label: 'research:' + a.key, phase: 'Recherche', schema: SRC })))

phase('Kritik')
const all = results.filter(Boolean).flatMap(r => r.sources)
const critic = await agent(`${COMMON}

Du bist der Vollständigkeits-Kritiker. Fünf Rechercheure haben folgende Quellen geprüft:
${JSON.stringify(all, null, 1)}

Frage: Was fehlt noch, damit der Nutzer KEINEN krassen Deal (Abflug DACH, insbesondere ZRH⇄HAM und DE→China, Economy+Business) verpasst? Welche Kategorie von Quelle wurde gar nicht betrachtet (z. B. Preis-Kalender statt Einzeltermine, Nachbarflughäfen, Gabelflüge, Meilen-Deals, Airline-Sales, Telegram-Webvorschauen, Google Flights Explore, Newsletter-Archive, Twitter/Nitter, Bluesky-Feeds (öffentliche AT-Proto-API: https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor=travel-dealz…), Mastodon-RSS)? Teste bis zu 12 zusätzliche konkrete Kandidaten live und gib sie im selben Format zurück. Notizen nach ${OUT}/kritik/.`, { label: 'critic', phase: 'Kritik', schema: SRC })

phase('Bericht')
const everything = all.concat(critic ? critic.sources : [])
const report = await agent(`${COMMON}

Schreibe den Abschlussbericht ${SCRATCH}/RESEARCH.md (Deutsch, Markdown) aus diesen live geprüften Quellen:
${JSON.stringify(everything, null, 1)}

Zusammenfassungen der Rechercheure:
${results.filter(Boolean).map((r, i) => '### ' + ANGLES[i].key + '\n' + r.summary).join('\n\n')}
### kritik
${critic ? critic.summary : ''}

Aufbau: 1) Executive Summary (10 Zeilen: was uns wirklich fehlt). 2) Tabelle ALLER geprüften Quellen, sortiert nach value, mit Empfehlung. 3) "Jetzt einbauen" – konkrete Liste mit URLs, Formatdetails (Preis-Regex-Beispiele, Kategorien, Besonderheiten wie Paging), geschätztem Aufwand. 4) "Optional/Später". 5) "Geprüft und verworfen" mit Grund. 6) Erkenntnisse zu Travelpayouts-Zusatzendpunkten, Lufthansa Open API, Amadeus-Quota, Google Flights Explore (mit exakten URLs/Parametern, wenn live bestätigt). 7) Empfehlungen zu Regionen/Nachbarflughäfen und China-Abdeckung. Nur Fakten aus den Rechercheergebnissen, keine Erfindungen; wo etwas nicht bestätigt werden konnte, das sagen. Gib am Ende den Pfad und das Executive Summary zurück.`, { label: 'report', phase: 'Bericht' })

return { sources: everything.length, report }
