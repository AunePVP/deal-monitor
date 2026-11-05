# ✈️ Deal-Monitor

Dein eigener Flug-Deal-Alarm im Stil von Travel-Dealz – mit Weboberfläche. Prüft automatisch alle 3 Stunden
alle Quellen, alle 30 Minuten die Deal-Seiten und alle 10 Minuten die schnellen Deal-Quellen
(Radar für Error Fares und Hammer-Deals).

**So startest du es:**
1. `docker compose up -d` (ohne Docker: `./start.sh`)
2. http://localhost:8080 öffnen
3. Optional unter **Einstellungen → Quellen** den kostenlosen Travelpayouts-Token eintragen → [travelpayouts.com](https://www.travelpayouts.com/) (unten links aufs Profil → Reiter „API token“, vorher unter Programs das Aviasales-Programm beitreten).
   Google Flights und die Deal-Seiten laufen ohne Key.

Fertig. Alarme legst du in der Oberfläche unter **Alarme** an, Treffer landen unter **Meldungen**.
Unter **Deals → Bestpreise jetzt** (und auf jeder Alarm-Karte) steht je Alarm das aktuell günstigste passende
Angebot – auch wenn es noch über deiner Grenze liegt, mit Abstand zur Grenze, Quelle und Stand.

**Unraid:** Fertiges Image `ghcr.io/aunepvp/deal-monitor:latest`. Im Terminal einmal
`wget -O /boot/config/plugins/dockerMan/templates-user/my-deal-monitor.xml https://raw.githubusercontent.com/AunePVP/deal-monitor/main/unraid/deal-monitor.xml`,
dann *Docker → Add Container → Template: deal-monitor → Apply* und über *WebUI* öffnen.
Tokens/Keys bitte in der Oberfläche eintragen (nicht in der Vorlage). Aufruf per IP oder Heimnetz-Name
(`http://tower:8080`, `tower.local`, `*.fritz.box`, `*.lan`) geht direkt.

---

## Demo ansehen

```bash
./start-demo.sh        # → http://localhost:8081, Beispieldaten, kein Token nötig
```

## Passwortschutz

Erreichbar im Netz? Dann Passwort setzen: `cp .env.example .env`, dort `DEALMONITOR_PASSWORD=…` eintragen, neu starten (`docker compose up -d`). Benutzername beim Login ist egal.

Aufruf über eine echte Domain (z. B. Reverse-Proxy `deals.example.de`)? Dann den Namen in `.env` unter `DEALMONITOR_ALLOWED_HOSTS=deals.example.de` eintragen. IP-Adressen und Heimnetz-Namen (`tower`, `*.local`, `*.lan`, `*.fritz.box`) gehen immer – sonst antwortet der Server mit „Unbekannter Host“ (Schutz vor fremden Webseiten).

Alle weiteren Einstellungen stehen kommentiert in `.env.example`. Was dort gesetzt ist, ist in der Oberfläche gesperrt – meist ist die Oberfläche bequemer.

## Benachrichtigungsserver (Webhook)

In den Einstellungen (oder per `.env`) `WEBHOOK_URL` und optional `WEBHOOK_TOKEN` eintragen. Pro Alarm und Lauf kommt ein `POST` mit JSON, bei gesetztem Token mit `Authorization: Bearer <token>` (wer die URL ändert, muss den Token neu eingeben – so kann er nicht an einen fremden Server umgeleitet werden):

```json
{
  "alert": "Zürich → Hamburg (und zurück)",
  "text": "✈️ Deal-Alarm … (fertiger Text, z. B. zum Weiterleiten)",
  "content": "dasselbe, max. 1990 Zeichen (Discord)",
  "deals": [{
    "status": "new",            // new | cheaper | revived
    "rating": "Top-Deal",       // Top-Deal | Sehr gut | Deal | Radar
    "headline": "Zürich nach Hamburg ab 89 € hin und zurück (LX, direkt, 4 Nächte)",
    "price": 89.0, "currency": "EUR",
    "origin": "ZRH", "destination": "HAM",
    "departure": "2027-01-15", "return": "2027-01-19",   // bei one-way: null
    "link": "https://www.aviasales.com/search/…",
    "compare": "https://www.google.com/travel/flights?…",
    "source": "travelpayouts",  // google_flights, amadeus-dates, amadeus-offers, feed:travel-dealz, feed:mydealz-fluege …
    "source_label": "Travelpayouts (Preis-API)",
    "reasons": ["unter deiner Grenze von 110 €", "Google Flights bestätigt: 92 €"]
  }]
}
```

Fehler (4xx/5xx) werden geloggt, der Lauf geht weiter; 5xx und Zeitüberschreitungen werden zweimal wiederholt. Weitere Kanäle: ntfy (Push aufs Handy) und Telegram. Die Meldungen in der Oberfläche sind immer aktiv. E-Mail ist bewusst nicht vorgesehen.

## Quellen

Jede Quelle lässt sich unter **Einstellungen → Quellen** einzeln ein- und ausschalten.

| Quelle | Was sie liefert | Key / Kosten | Stand |
|---|---|---|---|
| **Travelpayouts** (Aviasales) | Gecachte Preise aus Suchen der letzten Tage, alle Alarm-Strecken × Monate | kostenloser Token | getestet, Standard an |
| **Google Flights** | Echte Live-Preise je Strecke (2 Termine pro Monat) und **Gegenprüfung** der Travelpayouts-/Amadeus-Monatspreise: Der günstigste Google-Flug, der zu deinen Regeln passt (Umstiege, Dauer, Airlines), darf höchstens 25 % teurer sein und muss unter deiner Grenze bleiben = „✓ Google Flights bestätigt“ (gemeldet wird dann der Google-Preis mit Airline/Umstiegen dieses Flugs), sonst verworfen – dann wird der nächstgünstige Termin im selben Monat geprüft; nicht erreichbar = „nicht gegengeprüft“ | kein Key | **inoffiziell** (liest die normale Ergebnisseite) – kann jederzeit brechen oder zeitweise blockiert sein; dann pausiert die Quelle bis zum nächsten Lauf. Live getestet am 04./05.11.2026. Max. 40 Abfragen pro Lauf (¼ davon bleibt für Gegenprüfungen, der Rest wird gleichmäßig auf die Alarme verteilt), 2–4 s Pause. „Jeden Tag einzeln prüfen“ (Standard bei Zürich ⇄ Hamburg one-way): reihum jeder Tag der nächsten 90 Tage; bei 40 Abfragen pro Lauf dauert eine Runde knapp 4 Tage, bei 120 etwa einen Tag. Aus zwei billigen One-ways wird auch ein Hin- und Rückflug („2× One-way“) |
| **Amadeus Self-Service** | Monats-Scan Economy (`flight-dates`), Business/First/Premium als Angebote (`flight-offers`) | API Key + Secret – **Self-Service laut Anbieter am 17.07.2026 eingestellt** (neue Keys nur noch für Enterprise-Kunden) | **ungetestet** – nach offizieller Doku gebaut, nie live gelaufen; nur sinnvoll, wenn du noch einen gültigen Key hast. Standard aus |
| **Deal-Seiten (Feeds)** | Artikel von Travel-Dealz (Artikel, Ticker, Business-Class, Suche nach deinen Stichwörtern), mydealz Flüge, fly4free, premium-flights (inkl. China-Seiten), reisetopia (Preis aus dem Artikeltext), You have been upgraded (Business/First); optional Urlaubspiraten, fly4free Business, eigene RSS-Feeds | kein Key | getestet, Standard an; nur €-Preise, keine Kreuzfahrten/Bahn/Hotels, Fremdabflüge (z. B. „von Amsterdam“) zählen bei DACH-Alarmen nur als Nachbar-Hub (siehe unten) |
| **Schnelle Deal-Quellen** | Secret Flying (Error Fares, oft Stunden vor allen anderen), Travel-Dealz (schnell, direkt von der Seite statt über den Feed); optional ErrorFareAlerts | kein Key | Standard an (ErrorFareAlerts aus), alle 10 Minuten; fällt eine aus, laufen die anderen weiter. Meldet eine schnelle Quelle einen Deal, prüft Google Flights den Alarm sofort nach (höchstens 5 Abfragen) |
| Skyscanner, Kiwi | – | keine frei zugängliche API (nur Partnerprogramme) | nicht eingebaut |

Stichwörter: Ein Alarm ohne eigene Stichwörter bekommt sie automatisch aus Von/Nach (Städte-, Länder-, Regionsnamen, deutsch und englisch, z. B. Zürich/Zurich, Peking/Beijing).

**Nachbar-Hubs:** Viele Business-Deals nach China starten in Budapest, Luxemburg oder Rom. Bei Alarmen ab DACH in Business/First oder auf die Langstrecke zählen solche Abflüge mit einem geschätzten Zubringer-Aufschlag: +80 € (Zug/Auto: Luxemburg, Brüssel, Amsterdam, Straßburg, Mailand, Prag, Kopenhagen) bzw. +180 € (Zubringerflug: Budapest, Warschau, Oslo, Stockholm, Rom, Madrid, Barcelona, Athen, Istanbul, Helsinki). Die Grenze gilt für Ticket + Aufschlag, die Meldung nennt beides („Abflug Nachbar-Hub Budapest (+180 € Zubringer): Ticket 1.440 €, mit Zubringer 1.620 €“). Abschaltbar und änderbar unter **Einstellungen → Quellen → Deal-Seiten** (oder per `HUBS_ENABLED`, `HUB_SURCHARGE_A/B` in `.env`).

**Quellen-Gesundheit:** Nach jedem Lauf wird jede Deal-Seite eingestuft (OK, Leer, Veraltet, Fehler). Ändert sich der Zustand, kommt eine Meldung per ntfy/Webhook/Telegram, dazu einmal täglich ein Lebenszeichen. Abruf: `GET /api/sources/health`.

### Radar – keinen Hammer-Deal verpassen

Unabhängig von deinen Alarmen prüft das Radar **alle** Flug-Artikel der aktiven Deal-Seiten (bei jedem Lauf und zusätzlich alle 30 Minuten):

- **Immer gemeldet:** Error Fare, Fehlerpreis, Preisfehler, Hammer, Kracher, Knaller, Wahnsinn, Mistake Fare in Überschrift/Kategorie.
- **Sonst nach Preisgrenze:** Economy 40–300 € (darunter sind es meist Kurzstrecken-Promos wie „ab 14,99 €“), Premium Economy ≤ 700 €, Business ≤ 1.300 €, First ≤ 2.500 €.
- Standardmäßig nur Abflug aus Deutschland/Österreich/Schweiz (oder ohne Angabe).
- Treffer erscheinen unter **Deals** mit Badge „Radar“ (Filter-Chip „Radar“) und werden gemeldet wie Alarm-Deals. Ein Artikel, der schon zu einem Alarm passt, kommt nicht doppelt.
- Derselbe Deal auf zwei Seiten (gleiche Kabine, gleiche Zielstadt, Preis ± 5 %) wird einmal gemeldet – mit Hinweis „Auch bei premium-flights: 1.210 €“.
- Nennt eine Überschrift zwei Kabinen („Wien ab 99 € (Business Class ab 229 €)“), gilt jeder Preis für seine Kabine.

Alles unter **Einstellungen → Radar** änderbar (oder per `RADAR_*` in `.env`).

## Prinzipien (wie Travel-Dealz)

- Preise pro Person, hin und zurück – one-way steht in der Überschrift.
- Rabatt nur gegen eigenen Referenzpreis (Median der beobachteten Preise, 60 Tage), nie gegen Werbeangaben.
- Nur was man selbst buchen würde: Umstiege, Flugdauer, Nächte, Airlines filterbar.
- Ehrliche Überschriften, Quelle immer genannt, Link zum Gegenprüfen bei Google Flights.
- Abgelaufene Deals werden durchgestrichen; fällt eine Quelle aus, läuft nichts ab.
- Kein Spam: jeder Deal einmal, erneut erst bei ≥ 5 % billiger.
- Bewertung: **Top-Deal** ab 45 % unter Referenz, **Sehr gut** ab 30 %, sonst **Deal**; Radar-Treffer heißen **Radar**.
- Gecachte Preise werden vor dem Melden bei Google Flights gegengeprüft (siehe Quellen).

## Kommandozeile

```bash
python3 -m dealmonitor serve [--port 8080] [--demo]   # Weboberfläche (das macht start.sh)
python3 -m dealmonitor run [--alert zrh-ham]          # einmal prüfen und melden
python3 -m dealmonitor run --mode feeds --dry-run     # Probelauf: Deal-Seiten + Radar, nur Konsole,
                                                      # auf einer Kopie der Datenbank (nichts gespeichert)
python3 -m dealmonitor alerts                         # Alarme anzeigen
python3 -m dealmonitor deals --all                    # gespeicherte Deals
python3 -m dealmonitor report                         # data/report.html erzeugen
python3 -m dealmonitor test-notify                    # Testmeldung an alle Kanäle
python3 -m dealmonitor import-config [--replace]      # Alarme aus config/alerts.toml übernehmen
```

Start-Alarme kommen beim ersten Start aus `config/alerts.toml`. Daten liegen in `data/deals.sqlite`.
Ohne Server geht es auch per GitHub Actions (`.github/workflows/deal-monitor.yml`, Secrets wie in `.env.example`).

## Tests

```bash
python3 -m unittest discover -s tests   # Python-Tests
node tests/e2e/ui.mjs                   # Oberfläche im Browser (Node + Playwright, startet selbst eine Demo; Screenshots in tests/e2e/screenshots/)
```

## Probleme?

- Logs: `docker compose logs -f`
- Port belegt: in `docker-compose.yml` z. B. `"9000:8080"` eintragen (bzw. `./start.sh --port 9000`).
- Nur Python ≥ 3.11 nötig, keine weiteren Pakete.
