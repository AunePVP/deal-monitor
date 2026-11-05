# Deal-Monitor – Spezifikation "fertiges Projekt"

Repo: `/home/user/deal-monitor` (Python ≥3.11, **nur Standardbibliothek**, keine pip-Abhängigkeiten).
Ziel: Ein nicht-technischer Nutzer startet `docker compose up -d` (oder `./start.sh`), öffnet
http://localhost:8080 und hat ein fertiges Flug-Deal-Monitoring im Stil von travel-dealz.de:
Deals ansehen, Deal-Alarme in der Oberfläche anlegen/bearbeiten/löschen, Token eintragen,
"Jetzt prüfen" klicken, Meldungen (Inbox) lesen. Ein Zeitplan im Server prüft automatisch alle N Stunden.
Alle Texte für Nutzer auf **Deutsch**. Kein E-Mail-Versand (bewusst entfernt – nicht wieder einbauen).
Ein externer Benachrichtigungsserver kommt später über den vorhandenen Webhook (`WEBHOOK_URL`, `WEBHOOK_TOKEN`).
**Niemals `git commit`/`git push` ausführen.** Netzwerk nach außen ist in dieser Umgebung gesperrt
(nur pypi/npm) – Live-APIs können nicht getestet werden; nichts erfinden, Fixtures nur in Tests/Demo.

## Bestehender Code (lesen, nicht neu schreiben)
- `dealmonitor/models.py` – `Alert`, `Offer`, `Deal` (Alert-Felder: id, name, origins, destinations, trip,
  cabin, max_price, min_discount, baseline_price, max_stops, max_duration_hours, min_nights, max_nights,
  months_ahead, exclude_airlines, feed_keywords, enabled). `Alert.__post_init__` expandiert `@REGION`.
- `dealmonitor/principles.py` – Deal-Bewertung (nicht verändern außer bei echten Bugs).
- `dealmonitor/sources/travelpayouts.py`, `dealmonitor/sources/feed.py` – Quellen.
- `dealmonitor/store.py` – SQLite (Tabellen `prices`, `deals`).
- `dealmonitor/monitor.py` – `run(cfg, sources, notifiers, store, only=None, today=None) -> RunResult`.
- `dealmonitor/notify.py` – Console, Telegram, Ntfy, Webhook, `from_env()`, `compare_link(deal)`.
- `dealmonitor/config.py` – `Settings`-Dataclass, `load(path)` für `config/alerts.toml`, `append_alert`.
- `dealmonitor/report.py` – statischer HTML-Bericht (bleibt für CLI `report`).
- `dealmonitor/demo.py` – Fixture-Quellen (`FixtureSource`, `FixtureFeed`), `DEMO_TODAY`.
- `dealmonitor/cli.py` – Befehle run, alerts, add, deals, report, test-notify, demo.
- `tests/test_monitor.py` (25 Tests, müssen grün bleiben – bei API-Änderungen anpassen).
- `config/alerts.toml` – Start-Alarme (zrh-ham, ham-zrh, zrh-ham-oneway, de-cn-economy, de-cn-business).

## 1. Datenhaltung (store.py erweitern)
- SQLite mit `PRAGMA journal_mode=WAL`, `busy_timeout=5000`. Jeder Thread/Request öffnet eigene `Store`-Instanz
  (oder `check_same_thread=False` + Lock) – keine geteilte Verbindung ohne Lock über Threads.
- Neue Tabellen:
  - `alerts(id TEXT PK, data TEXT JSON, position INTEGER, created_at, updated_at)` – `data` = rohe Alert-Felder
    wie in TOML (Regionen unaufgelöst, z. B. `"@DE"`).
  - `kv(key TEXT PK, value TEXT JSON)` – Einstellungen + interne Flags (`seeded`).
  - `notifications(id INTEGER PK AUTOINCREMENT, created_at, alert_id, alert_name, status, rating, headline,
    price REAL, link, compare, read INTEGER DEFAULT 0, fingerprint)`.
  - `runs(id INTEGER PK AUTOINCREMENT, trigger TEXT, alert_id, started_at, finished_at, offers, deals, notified,
    expired, errors TEXT JSON)`.
- Seeding: Beim Start, falls `kv.seeded` fehlt: Alarme **und** `[settings]` aus `config/alerts.toml` importieren,
  dann `seeded=true`. Löscht der Nutzer später alle Alarme, wird NICHT neu importiert.
- CLI `python -m dealmonitor import-config [--replace]` importiert TOML erneut (ohne --replace: nur neue IDs).

## 2. Einstellungen (neues Modul `dealmonitor/appsettings.py`)
Auflösung: **Umgebungsvariable > DB (`kv`) > Default**. Per Env gesetzte Werte sind in der UI gesperrt.
- Geheimnisse: `travelpayouts_token` (Env `TRAVELPAYOUTS_TOKEN`), `telegram_bot_token` (`TELEGRAM_BOT_TOKEN`),
  `webhook_token` (`WEBHOOK_TOKEN`).
- Normale Werte (Env = Großschreibung des Namens): `travelpayouts_marker`, `webhook_url`, `ntfy_topic`,
  `ntfy_server` (Default `https://ntfy.sh`), `telegram_chat_id`, `interval_hours` (float, Default 3, min 0.5),
  `sources` (Liste, Default `["travelpayouts","feed"]`), sowie alle Felder der `Settings`-Dataclass
  (`currency, scan_months, history_days, min_history_samples, renotify_drop, max_deals_per_alert,
  request_delay, feeds, feed_max_age_days`). `database`/`report` werden aus dem Datenverzeichnis abgeleitet.
- Funktion, die aus DB + Env ein `Config`-Objekt (`config.Config(settings, alerts, path)`) baut, sowie die
  Notifier-Liste (InboxNotifier immer + Console + Telegram/Ntfy/Webhook wenn konfiguriert) und die Quellen.
- Datenverzeichnis: Env `DEALMONITOR_DATA` oder `--data`, Default `<repo>/data`. DB = `<data>/deals.sqlite`.

## 3. Inbox-Notifier (notify.py)
`Inbox(store_path)`: `name="inbox"`, `send(alert_name, items)` schreibt pro Deal eine Zeile in `notifications`
(inkl. `compare_link`). Immer aktiv – die Web-UI ist der Benachrichtigungskanal bis der Server kommt.

## 4. Scheduler (neues Modul `dealmonitor/scheduler.py`)
- Hintergrund-Thread. Läuft beim Start sofort, wenn der letzte Lauf älter als `interval_hours` ist, sonst
  wartet er bis `last_run + interval`. Liest `interval_hours` bei jedem Zyklus neu (Änderungen in der UI greifen).
- `trigger(alert_id=None, trigger="manual") -> bool` startet einen Lauf in einem Thread; False wenn schon einer läuft
  (Lock). Jeder Lauf schreibt eine Zeile in `runs` (start, ende, Zahlen, Fehler).
- Fehler im Lauf dürfen den Thread nie beenden (loggen, in `runs.errors` schreiben).
- `status()` → running, last_run, next_run.
- Kann mit `--no-scheduler` abgeschaltet werden (manuelle Läufe gehen trotzdem).

## 5. Web-Server (neues Modul `dealmonitor/web.py`)
`http.server.ThreadingHTTPServer`, JSON-API + statische Dateien aus `dealmonitor/static/`.
CLI: `python -m dealmonitor serve [--host 127.0.0.1] [--port 8080] [--data DIR] [--demo] [--no-scheduler]`
(Env-Defaults `DEALMONITOR_HOST`, `DEALMONITOR_PORT`). Beim Start eine Zeile "Deal-Monitor läuft auf http://…".
- Optionaler Schutz: Env `DEALMONITOR_PASSWORD` gesetzt → HTTP Basic Auth (beliebiger Benutzername) für ALLES.
- Statische Dateien: nur aus `static/`, Pfad-Traversal verhindern, korrekte Content-Types, `/` → `index.html`.
- Alle Antworten JSON UTF-8 (`ensure_ascii=False`). Fehler: `{"error": "deutscher Text"}` mit 400/404/409/500.
  Validierungsfehler aus `Alert(...)` (ValueError) → 400 mit der Meldung.
- Header `Cache-Control: no-store` für API.

### Endpunkte
| Methode | Pfad | Antwort |
|---|---|---|
| GET | `/api/status` | `{version, demo, running, last_run: Run\|null, next_run: iso\|null, interval_hours, configured: {travelpayouts, feed, webhook, ntfy, telegram}, counts: {alerts, alerts_enabled, active_deals, unread}}` |
| GET | `/api/deals?status=active\|expired\|all&alert_id=` | `{deals: [Deal]}` (Default active) |
| GET | `/api/alerts` | `{alerts: [Alert]}` (in `position`-Reihenfolge) |
| POST | `/api/alerts` | Body = Alert-Rohfelder → 201 `{alert: Alert}`; 409 bei doppelter ID; fehlt `id`, aus `name` slugifizieren |
| PUT | `/api/alerts/{id}` | Body = Alert-Rohfelder (vollständig oder teilweise, wird mit vorhandenem gemerged) → `{alert}`; ID-Änderung nicht erlaubt |
| DELETE | `/api/alerts/{id}` | `{deleted: id}`; zugehörige Deals des Alarms werden mit gelöscht, 404 wenn unbekannt |
| GET | `/api/regions` | `{regions: {"DE": [...], ...}, airports: {"FRA": "Frankfurt", ...}}` |
| GET | `/api/settings` | `{values: {name: wert}, secrets: {name: {set: bool, hint: "…abcd"\|null}}, env_locked: [namen]}` |
| PUT | `/api/settings` | Body `{values?: {...teilweise}, secrets?: {name: "neu" \| ""}}` ("" = löschen, fehlend = unverändert) → wie GET. Werte validieren (Typen, interval_hours ≥ 0.5) → 400 |
| POST | `/api/run` | Body `{alert_id?}` → 202 `{started: true}` oder 409 `{error: "Es läuft bereits eine Prüfung"}` |
| GET | `/api/runs?limit=20` | `{runs: [Run]}` neueste zuerst |
| GET | `/api/notifications?limit=100` | `{notifications: [Notification], unread: n}` neueste zuerst |
| POST | `/api/notifications/read` | Body `{ids?: [..]}` (ohne ids = alle) → `{unread: 0}` |
| POST | `/api/test-notify` | sendet Testmeldung über alle Kanäle inkl. Inbox → `{channels: [{name, ok, error?}]}` |

### JSON-Formen
- **Deal**: alle Spalten von `store.deals()` (fingerprint, alert_id, status, rating, headline, price, notified_price,
  source, link, first_seen, last_seen, expired_at, origin, destination, departure, return_date, airline, stops,
  cabin, roundtrip, nights, reference_price, discount, reasons, alert_name) **plus** `compare` (Google-Flights-Link
  oder null, wie `report._gf_link`). Feed-Artikel, die mehreren Alarmen zugeordnet sind, wie im Report zu einer Karte
  zusammenführen (`alert_name` mit " · " verbunden) – nur wenn kein alert_id-Filter gesetzt ist.
- **Alert**: Rohfelder (wie gespeichert) + abgeleitet: `origins_expanded`, `destinations_expanded` (Listen),
  `pairs` (Anzahl Strecken), `active_deals` (Anzahl), `best_price` (günstigster aktiver Deal oder null).
- **Run**: `{id, trigger, alert_id, started_at, finished_at, offers, deals, notified, expired, errors: [str]}`.
- **Notification**: `{id, created_at, alert_id, alert_name, status, rating, headline, price, link, compare, read}`.
- Zeitstempel ISO 8601 lokal ohne TZ (`2026-11-04T16:20:00`), Datumsfelder `YYYY-MM-DD`.

### Demo-Modus (`serve --demo`)
Temporäres Datenverzeichnis, Alarme aus TOML geseedet, Fixture-Quellen aus `demo.py` statt echter Quellen
(auch für "Jetzt prüfen"), `today=DEMO_TODAY`, initial ein Lauf mit künstlicher Preis-Historie wie in `demo.demo()`.
`/api/status.demo = true` → UI zeigt gelben Hinweis "DEMO – Beispieldaten, keine echten Preise".

## 6. Frontend (`dealmonitor/static/index.html`, `app.js`, `style.css`)
Vanilla JS, keine CDNs/Frameworks, funktioniert offline. Deutsch. Hash-Routing `#deals` (Default), `#alarme`,
`#meldungen`, `#einstellungen`. Responsive (≥360 px, kein horizontales Scrollen), Hell/Dunkel via
`prefers-color-scheme`. XSS-sicher: Inhalte per `textContent`/escapen, Links nur `http(s)`.
- **Kopf**: Logo "✈️ Deal-Monitor", Navigation mit Badge (ungelesene Meldungen), Button "Jetzt prüfen"
  (disabled + Spinner während `running`), Statuszeile "Letzte Prüfung … · nächste …".
- **Onboarding-Hinweis** wenn `configured.travelpayouts=false`: "Für Preise brauchst du einen kostenlosen
  Travelpayouts-Token → Einstellungen" (Link). Demo-Banner wenn `demo`.
- **Deals**: Filter-Chips pro Alarm + "Alle", Umschalter "Abgelaufene zeigen". Karten wie Travel-Dealz:
  Bewertungs-Badge (Top-Deal grün, Sehr gut blau, Deal lila, Abgelaufen grau + durchgestrichen), Überschrift,
  großer Preis, Reisedaten (TT.MM.JJJJ), Gründe als Liste, Quelle, "gefunden am", Buttons "Zum Angebot" und
  "Bei Google Flights gegenprüfen". Leerer Zustand freundlich erklären.
- **Alarme**: Liste (Name, Von → Nach mit Anzahl Strecken, Preisgrenze, Kabine, Hin+Rück/one-way, aktiver
  Deals/Bestpreis, Schalter aktiv/pausiert, Bearbeiten, Löschen mit Bestätigung, "Nur diesen prüfen").
  Formular (Dialog oder Panel) für Neu/Bearbeiten: Name; Von/Nach als Chip-Eingabe (IATA-Codes mit
  Vorschlagsliste aus `/api/regions` airports + Regionen-Buttons wie "@DE Deutschland", "@CN China");
  Checkbox "Gegenrichtung auch anlegen" (nur bei Neu → zweiter POST mit getauschten Feldern, ID-Suffix `-retour`);
  Hin+Rück / one-way; Kabine; Preisgrenze €; Normalpreis €; max. Umstiege; max. Flugdauer h; Nächte von/bis;
  Monate voraus; Stichwörter für Travel-Dealz-Feed (kommagetrennt = eine UND-Gruppe; mehrere Gruppen per Zeile);
  ausgeschlossene Airlines. API-Fehler im Formular anzeigen. Kurze Hilfetexte.
- **Meldungen**: Liste neueste zuerst, ungelesene hervorgehoben, "Alle als gelesen markieren", beim Öffnen der Seite
  nicht automatisch alles gelesen setzen. Optional: Browser-Benachrichtigung (Button "Desktop-Benachrichtigungen
  aktivieren"; bei neuen ungelesenen Meldungen `new Notification(...)` während der Tab offen ist).
- **Einstellungen**: Abschnitt "Preisquelle" (Travelpayouts-Token als Passwortfeld mit Hinweis "gesetzt …abcd",
  Marker, Link zur Anleitung, Quellen-Checkboxen travelpayouts/feed); "Zeitplan" (Intervall Stunden, Monate voraus);
  "Benachrichtigungen" (Webhook-URL + Token für den späteren Benachrichtigungsserver, ntfy Topic/Server,
  Telegram Bot-Token/Chat-ID; Button "Testmeldung senden" mit Ergebnis pro Kanal); "Erweitert" (history_days,
  min_history_samples, renotify_drop in %, max_deals_per_alert, feeds). Per Env gesperrte Felder disabled +
  Hinweis "per Umgebungsvariable gesetzt". "Letzte Läufe"-Tabelle aus `/api/runs` mit Fehlern.
- Polling `/api/status` alle 15 s (schneller, 3 s, solange `running`); nach Laufende Deals/Meldungen neu laden.
- Toasts für Erfolg/Fehler.

## 7. Paketierung
- `Dockerfile` (python:3.12-slim, `WORKDIR /app`, Code kopieren, `ENV DEALMONITOR_DATA=/data DEALMONITOR_HOST=0.0.0.0`,
  `VOLUME /data`, `EXPOSE 8080`, nicht-root User, HEALTHCHECK über `/api/status` mit python urllib,
  `CMD ["python","-m","dealmonitor","serve"]`).
- `docker-compose.yml` (Service `deal-monitor`, Port `8080:8080`, Volume `./data:/data`, `env_file: .env` optional
  via `required: false`, `restart: unless-stopped`).
- `.dockerignore`, `.env.example` (alle Variablen kommentiert, deutsch), `start.sh` (ohne Docker: prüft python3 ≥3.11,
  startet `python3 -m dealmonitor serve`, öffnet Browser falls möglich), `start-demo.sh`.
- `pyproject.toml`: package-data für `dealmonitor/static/*`.
- `README.md`: GANZ OBEN max. 10 Zeilen "So startest du es" (3 Schritte). Der Nutzer liest ungern – Rest knapp.
- `.github/workflows/deal-monitor.yml` bleibt (CLI-Modus, nutzt DB, seedet aus TOML).
- `.gitignore` bleibt (`data/*`).

## 8. Tests
- `tests/test_monitor.py` grün halten.
- `tests/test_web.py`: Server in Thread auf freiem Port mit Temp-Datenverzeichnis, `--no-scheduler`,
  Fixture-Quellen injizierbar. Testen: Seeding, CRUD Alarme inkl. Validierungsfehler/409/404, Settings
  (Secrets maskiert, ""=löschen, Env-Lock), Run starten → runs + notifications + deals, Notifications lesen,
  Basic-Auth wenn Passwort gesetzt, statische Dateien + Traversal-Schutz.
- `tests/e2e/ui.mjs` (Node + Playwright, `require('playwright')` bzw. import, Chromium vorinstalliert):
  startet `python3 -m dealmonitor serve --demo --port <frei>` und klickt alle Flows durch.
  Lauf: `node tests/e2e/ui.mjs`. Nicht Teil von `unittest`.
- Alles: `python3 -m unittest discover -s tests` muss grün sein.
