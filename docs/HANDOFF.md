# Übergabe – Stand 04.11.2026, abends (Limit fast erreicht, Arbeit pausiert)

Wenn der Nutzer **„mach weiter“** schreibt: diese Datei lesen, Branch `wip/quellen-ausbau` auschecken
und ab **„Nächste Schritte“** weitermachen. Nicht fragen – der Nutzer hat alles vorab freigegeben
(Ausnahme: siehe „Runde 2“).

## Nutzer & Vorlieben
- Deutsch, **kurze Antworten** (liest ungern lange Texte), will ein fertiges Produkt, keine Rückfragen.
- **Kein E-Mail-Versand.** Benachrichtigungen kommen später über einen eigenen Server → Webhook (`WEBHOOK_URL`/`WEBHOOK_TOKEN`).
- Läuft als Container auf **Unraid**: Image `ghcr.io/aunepvp/deal-monitor:latest` (baut `.github/workflows/docker.yml` bei Push auf `main`).
- Git-Autor: `AunePVP <aunepvp4@gmail.com>`. Projektabgabe ist am 05.11.2026.
- Wichtigstes Ziel: **keinen krassen Deal verpassen** (Error Fares, Business-Schnäppchen, günstige Langstrecke ab DACH).
- Alarme: Zürich⇄Hamburg (Hin/Rück + one-way), Deutschland→China (Economy ≤ 499 €, Business ≤ 1.999 €).

## Stand
- `main` (Commit 684b115): fertige Web-App mit Travelpayouts + Travel-Dealz-Feed, Docker, Unraid-Vorlage. Image gebaut, öffentlich.
- `wip/quellen-ausbau` (dieser Branch): Bau-Runde 1 „mehr Quellen“ nach `docs/handoff/SPEC-2-quellen.md`, **mittendrin gestoppt**.
  `python3 -m unittest discover -s tests` → **219 Tests grün** zum Zeitpunkt des Stopps. E2E (`node tests/e2e/ui.mjs`) NICHT gelaufen.

### Fertig (Agenten-Berichte in `docs/handoff/agent-results/bau-runde-1.json`)
- **A Feeds** (`sources/feed.py`, `regions.py`): Presets travel-dealz (5 Feeds), Suche, mydealz, fly4free, premium-flights,
  urlaubspiraten/reisetopia optional; Synonyme de/en, Auto-Stichwörter, Abflugort-Erkennung, Dedup per Link. Tests `tests/test_feeds.py`.
- **B Radar** (`radar.py`, `tests/test_radar.py`).
- **C Google Flights** (`sources/googleflights.py`, `tests/test_google.py`, 3 Live-Fixtures).
- **D Amadeus** (`sources/amadeus.py`, `tests/test_amadeus.py`; ohne Key, nicht live getestet).

### Abgebrochen mitten in E (Integration) – unbedingt prüfen
Der Integrations-Agent hat bereits geändert: `appsettings.py, scheduler.py, monitor.py, web.py, store.py, config.py, demo.py,
notify.py, report.py, http.py, static/app.js, static/index.html, config/alerts.toml, tests/test_web.py, tests/test_fixes.py`.
Unklar, was davon vollständig ist. Gegen SPEC-2 Abschnitt E Punkt für Punkt prüfen und vervollständigen, besonders:
Quellen-Fabrik in `appsettings.Context.sources()` (google_flights, amadeus, feeds/Alias feed), zweiter Scheduler-Takt
(`feed_interval_minutes`), Radar-Lauf + Google-Verifikation + `RunResult.rejected` in `monitor.py`, `/api/run` mit `mode`,
Frontend-Karten „Quellen“ + „Radar“, Radar-Chip/Badge, `style.css` (noch unverändert!), README-Quellen-Tabelle,
`.env.example`, `unraid/deal-monitor.xml`, E2E-Schritte in `tests/e2e/ui.mjs`.

## Nächste Schritte (Reihenfolge)
1. Integration E fertigstellen (s. o.), Unit-Tests + `node tests/e2e/ui.mjs` grün.
2. Live-Lauf: `python3 -m dealmonitor --data /tmp/x run --dry-run --sources feeds -v` → Zuordnung kritisch prüfen
   (keine Kreuzfahrten/Bahn, keine Fremdabflüge bei @DE, keine 14,99 €-Promos im Radar, „China Eastern: Amsterdam–Tokio“ kein China-Deal).
   Google kurz live: max. 4 Abfragen.
3. Review (Parsing, Backend, Sicherheit, Vollständigkeit) → verifizieren → fixen. Workflow-Vorlage: `docs/handoff/workflows/bau-runde-1.js`
   (Phasen E2E/Review/Verify/Fix ab Zeile „phase('E2E')“ wiederverwendbar).
4. GitGuardian-Fehlalarme entschärfen: Testwerte `999999:SECRETTOKENabcdefgh` (tests/test_fixes.py), `tp-e2e-geheim-9f3a`
   (tests/e2e/ui.mjs), `sesam` (tests/test_web.py) durch offensichtliche Dummies ersetzen (z. B. `123456:test-dummy`, `test-dummy`).
5. Docker-Image lokal bauen + starten (`docker compose up -d --build`, Health prüfen), dann auf `main` mergen, pushen,
   GitHub-Actions-Lauf „Container-Image“ abwarten und Ergebnis melden.
6. Kurze Abschlussmeldung an den Nutzer (was live getestet ist, was nicht).

## Runde 2 (Recherche-Ergebnisse einbauen) – NUR nach OK des Nutzers
Der Nutzer hat Runde 2 wegen Limit ausdrücklich zurückgestellt. Erst nach Schritt 6 anbieten.
Recherche wurde gestoppt: fertig sind `preis-apis` und `dach-deal-seiten` (`docs/handoff/agent-results/recherche.json`),
Rohnotizen aller begonnenen Blickwinkel unter `docs/handoff/research/` (große Rohdownloads entfernt).
Nicht fertig: error-fares-premium, wie-findet-travel-dealz, china-schweiz-spezial, Kritiker, Abschlussbericht.
Wichtiger Fund (dach-deal-seiten): travel-dealz.de hat Kategorie-Feeds, z. B. `/kategorie/fluge/economy…` – siehe Bericht.
Skript zum Fortsetzen: `docs/handoff/workflows/recherche.js` (Pfade darin zeigen auf das alte Scratchpad → anpassen).

## Umgebung (Cloud-Session)
- Netzwerk offen; `gh`-Token ungültig → GitHub nur per `git push` (Repo ist angebunden). Kein Repo-Erstellen möglich.
- Öffentliche Tunnel (cloudflared o. ä.) werden von der Sicherheitsprüfung blockiert → nicht versuchen.
- Klick-Demo als Artifact: https://claude.ai/artifact/4JJEwp32GUqj8N3rEBeyEn (Quelle wurde nicht ins Repo übernommen;
  baut sich aus `dealmonitor/static/*` + Mock-API, bei Bedarf neu erzeugen).
