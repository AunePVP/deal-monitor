export const meta = {
  name: 'deal-monitor-quellen',
  description: 'Deal-Monitor: mehr Quellen (Feeds, Google Flights, Amadeus), Radar, Integration, E2E, Review, Fix',
  phases: [
    { title: 'Quellen', detail: 'Feeds+Radar, Google Flights, Amadeus parallel bauen' },
    { title: 'Integration', detail: 'Settings, Scheduler, Monitor, Web, Frontend, Demo, README' },
    { title: 'E2E', detail: 'Tests, UI-Durchklick, Live-Lauf gegen echte Feeds' },
    { title: 'Review', detail: 'Reviewer: Korrektheit, Quellen/Parsing, Sicherheit, Vollständigkeit' },
    { title: 'Verify', detail: 'Findings adversarial prüfen' },
    { title: 'Fix', detail: 'Bestätigte Findings beheben, alle Tests erneut' },
  ],
}

const REPO = '/home/user/deal-monitor'
const SPEC = '/tmp/claude-0/-home-user/8771aaf7-3b29-551f-a287-c88013dcf7a7/scratchpad/SPEC2.md'
const SCRATCH = '/tmp/claude-0/-home-user/8771aaf7-3b29-551f-a287-c88013dcf7a7/scratchpad'

const COMMON = `Projekt: ${REPO}. Lies ZUERST vollständig die Spezifikation ${SPEC} und den bestehenden Code, den du anfasst. Regeln: nur Python-Standardbibliothek; Nutzertexte Deutsch; KEIN E-Mail; NIEMALS git commit/push/stash/checkout/reset (nur lesen mit git status/diff ist ok); keine __pycache__/Server-Prozesse hinterlassen. Netzwerk ist offen: Live-Abfragen sind erlaubt, aber sparsam (Feeds: jeder Feed höchstens 2× laden; Google Flights: höchstens 3 echte Abfragen mit ≥ 3 s Abstand; Amadeus: gar nicht, kein Key). Keine Scraping-Bibliotheken installieren. Wenn etwas nicht geht, ehrlich berichten statt zu erfinden.`

phase('Quellen')
const S = { type: 'object', properties: { summary: { type: 'string' }, files: { type: 'array', items: { type: 'string' } }, deviations: { type: 'array', items: { type: 'string' } }, tests: { type: 'string' }, live_check: { type: 'string' } }, required: ['summary', 'files', 'deviations', 'tests', 'live_check'] }

const built = await parallel([
  () => agent(`${COMMON}

DEINE AUFGABE: Spezifikation Abschnitte A (Feeds) und B (Radar). Du besitzt exklusiv: dealmonitor/sources/feed.py, dealmonitor/radar.py (neu), dealmonitor/regions.py, tests/test_feeds.py (neu), tests/test_radar.py (neu), tests/fixtures/feeds/* (neu). Du darfst tests/test_monitor.py und tests/test_fixes.py minimal anpassen, falls Signaturen sich ändern (bestehende Tests müssen weiter grün sein). Fasse NICHT an: monitor.py, appsettings.py, scheduler.py, web.py, static/*, demo.py (andere Agenten / nächste Phase). Die Integration in Settings/Monitor macht die nächste Phase – biete dafür klare Konstruktoren: FeedSource(presets=[ids], extra_feeds=[urls], search_enabled=True, max_age_days=14) und radar.scan(items_or_source, settings_dict) -> list[Offer-ähnlich] bzw. eine Funktion, die aus FeedSource-Items Radar-Offers macht (dokumentiere die Schnittstelle in deiner Zusammenfassung). Lade die Feeds live, speichere gekürzte Fixtures (≤ 8 Items je Feed, echte Titel), schreibe Tests gegen die Fixtures, inkl. Negativfällen (Kreuzfahrt, Bahn, $-Preis, fremder Abflugort, Airline-Name als Ziel). Führe am Ende \`python3 -m unittest discover -s tests\` aus.`, { label: 'build:feeds+radar', phase: 'Quellen', schema: S }),
  () => agent(`${COMMON}

DEINE AUFGABE: Spezifikation Abschnitt C (Google Flights), nur die Quelle selbst (kein monitor.py). Du besitzt exklusiv: dealmonitor/sources/googleflights.py (neu), tests/test_google.py (neu), tests/fixtures/google_flights_*.html (neu). Beispielseite: ${SCRATCH}/gf/page.html (2,4 MB, ZRH→HAM 15.–19.01.2027). Baue daraus eine gekürzte Fixture (≤ 150 kB; nur die Blöcke mit den Ergebnis-aria-labels/Preis-Spans, so dass dein Parser dieselben Ergebnisse liefert wie auf der Vollseite). Biete zusätzlich eine Funktion verify(origin, dest, departure, return_date, cabin, roundtrip) -> {price, airline, stops, link} | None für die spätere Verifikation (zählt auf max_requests, nutzt denselben Fetch/Parser) und build_url(...). Nutze dealmonitor/http.py (ggf. um einen Parameter für rohe HTML-Bytes/Headers erweitern – http.py darfst du minimal erweitern, nicht umbauen). Live-Test: höchstens 3 echte Abfragen (ZRH→HAM Januar 2027 Hin/Rück, FRA→SHA März 2027 one-way, FRA→BJS Business Hin/Rück) mit ≥ 3 s Abstand; berichte die Ergebnisse (Preise/Airlines) im live_check. Führe am Ende \`python3 -m unittest discover -s tests\` aus.`, { label: 'build:google', phase: 'Quellen', schema: S }),
  () => agent(`${COMMON}

DEINE AUFGABE: Spezifikation Abschnitt D (Amadeus), nur die Quelle selbst. Du besitzt exklusiv: dealmonitor/sources/amadeus.py (neu), tests/test_amadeus.py (neu), tests/fixtures/amadeus_*.json (neu). Kein Key vorhanden → keine Live-Aufrufe; du darfst aber die öffentliche Doku lesen (https://developers.amadeus.com/self-service/category/flights/api-doc/flight-offers-search und .../flight-cheapest-date-search sowie die GitHub-Repos amadeus4dev/amadeus-code-examples) per WebFetch/curl, um Request-Parameter und Antwortformat exakt zu treffen. Fixtures realistisch nach Doku. Token-Cache, Fehlerbehandlung (401/429/400 mit errors[].detail), Request-Zähler/Limit, incomplete-Meldung wie bei Travelpayouts. Tests mit mock von dealmonitor.http. Führe am Ende \`python3 -m unittest discover -s tests\` aus.`, { label: 'build:amadeus', phase: 'Quellen', schema: S }),
])
log('Quellen gebaut: ' + built.map(b => b ? 'ok' : 'FEHLT').join(', '))

phase('Integration')
const I = { type: 'object', properties: { summary: { type: 'string' }, files: { type: 'array', items: { type: 'string' } }, deviations: { type: 'array', items: { type: 'string' } }, unit_tests: { type: 'string' }, open_points: { type: 'array', items: { type: 'string' } } }, required: ['summary', 'files', 'deviations', 'unit_tests', 'open_points'] }
const integ = await agent(`${COMMON}

Drei Agenten haben die neuen Quellen gebaut. Ihre Berichte (Schnittstellen!):
--- FEEDS+RADAR ---
${JSON.stringify(built[0], null, 1)}
--- GOOGLE FLIGHTS ---
${JSON.stringify(built[1], null, 1)}
--- AMADEUS ---
${JSON.stringify(built[2], null, 1)}

DEINE AUFGABE: Spezifikation Abschnitt E komplett (Integration): appsettings.py (neue Settings/Secrets, Quellen-Fabrik, Alias feed→feeds), scheduler.py (zwei Takte), monitor.py (Radar-Lauf, Google-Verifikation, RunResult.rejected, best_per_slot über Quellen), web.py (Settings-API, /api/run mode, status counts/configured, optional /api/sources), Frontend static/* (Quellen-Karten, Radar-Einstellungen, Radar-Chip/Badge, Quellen-Klartext, Bestätigt-Hinweis, Status-Zeile), demo.py (Fixture-Quellen für google_flights + Radar-Beispiele), config.py falls [settings] neue Schlüssel akzeptieren muss, README (Quellen-Tabelle, ehrlich), .env.example, unraid/deal-monitor.xml, tests (test_web.py erweitern, neue Settings, run mode) und tests/e2e/ui.mjs (neue Schritte: Quellen-Einstellungen speichern, Radar-Chip sichtbar, Google-Schalter). Wenn die Quellen-Schnittstellen nicht zur Spezifikation passen, passe die Quellen-Module minimal an (du darfst jetzt alle Dateien ändern). Fertig heißt: \`python3 -m unittest discover -s tests\` grün, \`node tests/e2e/ui.mjs\` grün, \`python3 -m dealmonitor serve --demo --no-scheduler --port 18090\` zeigt Radar-Deals und die neuen Einstellungen (Server danach beenden). Berichte offene Punkte ehrlich.`, { label: 'integrate', phase: 'Integration', schema: I })
log('Integration: ' + (integ ? integ.unit_tests : 'FEHLT'))

phase('E2E')
const E = { type: 'object', properties: { unit_tests: { type: 'string' }, e2e: { type: 'string' }, live_run: { type: 'string' }, fixed: { type: 'array', items: { type: 'string' } }, remaining: { type: 'array', items: { type: 'string' } } }, required: ['unit_tests', 'e2e', 'live_run', 'fixed', 'remaining'] }
const e2e = await agent(`${COMMON}

Integrationsbericht:
${JSON.stringify(integ, null, 1)}

DEINE AUFGABE: Abnahme + Live-Lauf. (1) \`python3 -m unittest discover -s tests\` und \`node tests/e2e/ui.mjs\` ausführen; Fehler beheben. (2) Live-Lauf gegen echte Feeds: \`python3 -m dealmonitor --data ${SCRATCH}/live-e2e run --dry-run --sources feeds -v\` (nur Feeds+Radar, kein Google/Travelpayouts) – prüfe die Ausgabe kritisch: Werden echte Artikel sinnvoll zugeordnet? Keine Kreuzfahrten/Bahn/Hotels als Flug-Deal? Keine fremden Abflughäfen bei @DE-Alarmen? Radar-Treffer plausibel (keine 14,99€-Ryanair-Promos als Hammer-Deal, kein "China Eastern: Amsterdam–Tokio" als China-Deal)? Behebe Fehlzuordnungen im Parser/Radar mit Tests. (3) Ein kurzer Live-Lauf mit Google: \`python3 -m dealmonitor --data ${SCRATCH}/live-e2e run --dry-run --sources google_flights --alert zrh-ham\` mit google_max_requests auf 4 begrenzt (per Env GOOGLE_MAX_REQUESTS=4 falls unterstützt, sonst über die DB/Settings-Mechanik) – prüfe, dass echte Preise ankommen oder ein sauberer Fehler erscheint. (4) Demo-Server starten (Port 18091, --demo --no-scheduler), Screenshots Desktop 1280 + Mobil 390 von Deals (Radar-Chip), Einstellungen→Quellen und Radar nach ${SCRATCH}/e2e2-*.png, mit Read ansehen, offensichtliche Darstellungsfehler beheben. Server beenden, ${SCRATCH}/live-e2e darf bleiben. Berichte wörtlich die letzten Ergebniszeilen.`, { label: 'e2e+live', phase: 'E2E', schema: E })
log('E2E: ' + (e2e ? e2e.unit_tests + ' | ' + e2e.e2e : 'FEHLT'))

phase('Review')
const FIND = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: { title: { type: 'string' }, file: { type: 'string' }, line: { type: 'integer' }, severity: { type: 'string', enum: ['high', 'medium', 'low'] }, description: { type: 'string' }, failure_scenario: { type: 'string' }, fix: { type: 'string' } }, required: ['title', 'file', 'severity', 'description', 'failure_scenario', 'fix'] } } }, required: ['findings'] }
const LENSES = [
  { key: 'parsing', prompt: 'Quellen & Parsing: sources/feed.py, radar.py, sources/googleflights.py, sources/amadeus.py, regions.py. Suche echte Fehlzuordnungen und Lücken, die dazu führen, dass ein krasser Deal VERPASST oder ein falscher gemeldet wird: Preis-Regex (Cents, Tausender, "ab 1.299€", "€599", "from 1910€"), Währungen, Kabinenerkennung, Abflugort-Erkennung (von/ab/from), Synonyme, Dedup per Link, Kategorie-Filter zu streng/zu lax, Radar-Schwellen, Google-Parser bei Nonstop/1 Zwischenstopp/one-way, Amadeus-Parameter laut Doku. Teste mit eigenen Beispieltiteln aus den Fixtures und erfundenen Grenzfällen.' },
  { key: 'backend', prompt: 'Korrektheit Backend: monitor.py (Radar-Dedup, Verifikation, rejected, best_per_slot über Quellen, expire nur bei vollständigen Quellen), scheduler.py (zwei Takte, Lock, Intervalländerung, Absturzsicherheit), appsettings.py (Env>DB>Default für alle neuen Settings, Alias feed/feeds, Secrets maskiert), store.py, web.py (/api/run mode, Validierung neuer Settings), demo.py. Führe Code aus, um Vermutungen zu bestätigen.' },
  { key: 'security', prompt: 'Sicherheit & Robustheit: neue Secrets (Amadeus) nie in Logs/Fehlern/runs.errors/API; Google-Abfragen: Limits greifen wirklich (kein Endlos-Scan), Schutzschalter bei 429/Consent; Feed-URLs vom Nutzer (SSRF auf interne Adressen? mindestens http(s) und kein localhost/private IP, oder dokumentierte Entscheidung), XML-Parsing (keine Entity-Expansion-Risiken, Größenlimit), XSS im Frontend für neue Felder (Quellen-Namen, Feed-Titel englisch mit Emojis), Frontend-Validierung neuer Zahlenfelder (Komma), Content-Type/Origin-Prüfungen weiter intakt. Bestätige per curl/Code.' },
  { key: 'complete', prompt: 'Vollständigkeit gegen die Spezifikation SPEC2.md Punkt für Punkt (A–E): Presets, Suche-Feed, Synonyme, Auto-Stichwörter, Abflugort, Radar-Regeln/Settings, Google-Scan-Modus/Verifikation/Limits, Amadeus beide Endpunkte, Settings-UI-Karten, Radar-Chip, Status-Zeile, README-Quellen-Tabelle (ehrlich zu Google/Amadeus/Skyscanner), .env.example, Demo mit Radar-Beispielen, Tests, E2E. Nur konkrete, überprüfbare Lücken; keine Stilfragen.' },
]
const reviews = await parallel(LENSES.map(l => () => agent(`${COMMON}

Du bist Reviewer "${l.key}". Ändere KEINE Dateien (Server auf Port 183${LENSES.indexOf(l)}0 starten ist ok, am Ende beenden). ${l.prompt}
Melde nur echte, konkrete Probleme mit Datei/Zeile, reproduzierbarem Fehlerszenario und konkretem Fix. Lieber 8 echte als 30 vage.`, { label: 'review:' + l.key, phase: 'Review', schema: FIND })))
const findings = reviews.filter(Boolean).flatMap((r, i) => (r.findings || []).map(f => ({ ...f, lens: LENSES[i].key })))
log(findings.length + ' Findings')

phase('Verify')
const V = { type: 'object', properties: { confirmed: { type: 'array', items: { type: 'object', properties: { title: { type: 'string' }, file: { type: 'string' }, severity: { type: 'string' }, description: { type: 'string' }, fix: { type: 'string' } }, required: ['title', 'file', 'severity', 'description', 'fix'] } }, rejected: { type: 'array', items: { type: 'object', properties: { title: { type: 'string' }, reason: { type: 'string' } }, required: ['title', 'reason'] } } }, required: ['confirmed', 'rejected'] }
const verdict = findings.length ? await agent(`${COMMON}

Du bist skeptischer Prüfer. Ändere KEINE Dateien. Prüfe JEDES Finding gegen den echten Code (lesen, ausführen). Versuche es zu WIDERLEGEN. Bestätige nur, was wirklich zutrifft; führe Duplikate zusammen; bei Unsicherheit über echte Fehlzuordnungen (verpasster/falscher Deal), Sicherheits- oder Datenverlust-Bugs: bestätigen.

FINDINGS:
${JSON.stringify(findings, null, 1)}`, { label: 'verify', phase: 'Verify', schema: V }) : { confirmed: [], rejected: [] }
log((verdict ? verdict.confirmed.length : 0) + ' bestätigt')

phase('Fix')
const F = { type: 'object', properties: { fixed: { type: 'array', items: { type: 'string' } }, not_fixed: { type: 'array', items: { type: 'object', properties: { title: { type: 'string' }, reason: { type: 'string' } }, required: ['title', 'reason'] } }, unit_tests: { type: 'string' }, e2e: { type: 'string' }, live_feeds_summary: { type: 'string' } }, required: ['fixed', 'not_fixed', 'unit_tests', 'e2e', 'live_feeds_summary'] }
const fix = await agent(`${COMMON}

Behebe alle bestätigten Findings (minimal-invasiv, mit Tests). Danach: \`python3 -m unittest discover -s tests\`, \`node tests/e2e/ui.mjs\` und ein letzter Live-Lauf \`python3 -m dealmonitor --data ${SCRATCH}/live-final run --dry-run --sources feeds\` (Ausgabe kurz zusammenfassen: wie viele Artikel, wie viele Alarm-Treffer, wie viele Radar-Treffer, Beispiele). Am Ende: alle Server-Prozesse beenden, __pycache__ entfernen, \`git status --short\` darf nur gewollte Änderungen zeigen (data/ nur .gitkeep, keine Screenshots außer in ignorierten Ordnern). Ergebniszeilen wörtlich angeben.

BESTÄTIGTE FINDINGS:
${JSON.stringify(verdict ? verdict.confirmed : [], null, 1)}

Offene Punkte aus E2E (mit beheben, falls sinnvoll):
${JSON.stringify(e2e ? e2e.remaining : [], null, 1)}`, { label: 'fix', phase: 'Fix', schema: F })

return { built: built.map(b => b && b.summary), integ: integ && integ.summary, e2e, findings: findings.length, confirmed: verdict && verdict.confirmed.map(c => c.severity + ': ' + c.title), fix }
