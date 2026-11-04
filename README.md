# ✈️ Deal-Monitor

Dein eigener Flug-Deal-Alarm im Stil von Travel-Dealz – mit Weboberfläche, prüft automatisch alle 3 Stunden.

**So startest du es:**
1. `docker compose up -d` (ohne Docker: `./start.sh`)
2. http://localhost:8080 öffnen
3. Unter **Einstellungen** den kostenlosen Travelpayouts-Token eintragen → [travelpayouts.com](https://www.travelpayouts.com/) (Tools → API)

Fertig. Alarme legst du in der Oberfläche unter **Alarme** an, Treffer landen unter **Meldungen**.

**Unraid:** Fertiges Image `ghcr.io/aunepvp/deal-monitor:latest`. Im Terminal einmal
`wget -O /boot/config/plugins/dockerMan/templates-user/my-deal-monitor.xml https://raw.githubusercontent.com/AunePVP/deal-monitor/main/unraid/deal-monitor.xml`,
dann *Docker → Add Container → Template: deal-monitor → Apply* und über *WebUI* öffnen.

---

## Demo ansehen

```bash
./start-demo.sh        # → http://localhost:8081, Beispieldaten, kein Token nötig
```

## Passwortschutz

Erreichbar im Netz? Dann Passwort setzen: `cp .env.example .env`, dort `DEALMONITOR_PASSWORD=…` eintragen, neu starten (`docker compose up -d`). Benutzername beim Login ist egal.

Aufruf über einen Namen statt `localhost`/IP-Adresse (z. B. `http://nas.local:8080` oder Reverse-Proxy)? Dann den Namen in `.env` unter `DEALMONITOR_ALLOWED_HOSTS=nas.local` eintragen – sonst antwortet der Server mit „Unbekannter Host“ (Schutz vor fremden Webseiten).

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
    "rating": "Top-Deal",       // Top-Deal | Sehr gut | Deal
    "headline": "Zürich nach Hamburg ab 89 € hin und zurück (LX, direkt, 4 Nächte)",
    "price": 89.0, "currency": "EUR",
    "origin": "ZRH", "destination": "HAM",
    "departure": "2027-01-15", "return": "2027-01-19",   // bei one-way: null
    "link": "https://www.aviasales.com/search/…",
    "compare": "https://www.google.com/travel/flights?…",
    "source": "travelpayouts"   // oder travel-dealz-feed
  }]
}
```

Fehler (4xx/5xx) werden geloggt, der Lauf geht weiter; 5xx und Zeitüberschreitungen werden zweimal wiederholt. Weitere Kanäle: ntfy (Push aufs Handy) und Telegram. Die Meldungen in der Oberfläche sind immer aktiv. E-Mail ist bewusst nicht vorgesehen.

## Prinzipien (wie Travel-Dealz)

- Preise pro Person, hin und zurück – one-way steht in der Überschrift.
- Rabatt nur gegen eigenen Referenzpreis (Median der beobachteten Preise, 60 Tage), nie gegen Werbeangaben.
- Nur was man selbst buchen würde: Umstiege, Flugdauer, Nächte, Airlines filterbar.
- Ehrliche Überschriften, Quelle immer genannt, Link zum Gegenprüfen bei Google Flights.
- Abgelaufene Deals werden durchgestrichen; fällt eine Quelle aus, läuft nichts ab.
- Kein Spam: jeder Deal einmal, erneut erst bei ≥ 5 % billiger.
- Bewertung: **Top-Deal** ab 45 % unter Referenz, **Sehr gut** ab 30 %, sonst **Deal**.

Quellen: Travelpayouts/Aviasales (Cache-Preise, keine Live-Verfügbarkeit) und der Travel-Dealz-RSS-Feed.

## Kommandozeile

```bash
python3 -m dealmonitor serve [--port 8080] [--demo]   # Weboberfläche (das macht start.sh)
python3 -m dealmonitor run [--alert zrh-ham]          # einmal prüfen und melden
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
