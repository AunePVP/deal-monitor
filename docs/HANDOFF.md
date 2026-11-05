# Übergabe – Stand 05.11.2026 (Runde 2 fertig auf `wip/quellen-ausbau`, nicht auf `main`)

Wenn der Nutzer **„mach weiter“** schreibt: diese Datei lesen, Branch `wip/quellen-ausbau` auschecken
und ab **„Nächste Schritte“** weitermachen. Nicht fragen – der Nutzer hat alles vorab freigegeben.

## Nutzer & Vorlieben
- Deutsch, **kurze Antworten** (liest ungern lange Texte), will ein fertiges Produkt, keine Rückfragen.
- **Kein E-Mail-Versand.** Benachrichtigungen kommen später über einen eigenen Server → Webhook (`WEBHOOK_URL`/`WEBHOOK_TOKEN`).
- Läuft als Container auf **Unraid**: Image `ghcr.io/aunepvp/deal-monitor:latest` (baut `.github/workflows/docker.yml` bei Push auf `main`).
- Git-Autor: `AunePVP <aunepvp4@gmail.com>`. Projektabgabe ist am 05.11.2026.
- Wichtigstes Ziel: **keinen krassen Deal verpassen** (Error Fares, Business-Schnäppchen, günstige Langstrecke ab DACH).
- Alarme: Zürich⇄Hamburg (Hin/Rück + one-way), Deutschland→China (Economy ≤ 499 €, Business ≤ 1.999 €).

## Stand
- `main`: Runde 1 (Travelpayouts, Google Flights, Feeds, Radar, Bestpreise, Docker, Unraid). Image gebaut, öffentlich.
- `wip/quellen-ausbau`: **Runde 2 komplett** nach `docs/handoff/RESEARCH.md` Abschnitt 3 (Pakete 0–11 + Review-Fixes),
  Schnittstellen in `docs/handoff/RUNDE2-SCHNITTSTELLEN.md`. Lokal committet, **nicht gepusht, nicht auf `main`**.
  - Neue Quellen: Secret Flying (Bluesky), Travel-Dealz WP-REST (schnell, Ablaufdatum, Abflug/Ziel-Codes),
    reisetopia (Infobox), You have been upgraded, premium-flights China-Tags, ErrorFareAlerts (Standard aus).
  - Google: Preisniveau (`ds:1`) je Angebot, zweiter Abruf bei leerer Seite, Datumsrotation ZRH⇄HAM (jeder Tag),
    Kombi aus 2 One-way.
  - Nachbar-Hubs mit Zubringer-Aufschlag (+80/+180 €), mehr China-Codes.
  - Takt: schnelle Quellen alle 10 min, Sofort-Prüfung bei schnellem Treffer, Quellen-Gesundheit mit Meldung bei Wechsel.
  - Bewertung: Google-Bezugspreis, Fehlerpreis-Verdacht, Erstmeldung über Quellen, Beinahe-Deals.
  - UI: Preisverlauf, Badges, Beinahe-Deals, Seite „Quellen“; Demo mit allen neuen Feldern.
- Tests: `python3 -m unittest discover -s tests` → **627 grün**; `node tests/e2e/ui.mjs` → **43 Schritte grün**.

### Abnahme-Live-Lauf (05.11.2026, frisches Datenverzeichnis, alle 12 Presets an, Google max. 8 Abfragen)
Ein voller Lauf, 57 s: 38 Angebote, 3 Alarm-Deals, 15 Radar-Treffer, keine Abstürze, keine Konsolenfehler in der UI.
Danach ein Schnell-Check nach Zeitplan (10 min): 2 s, Secret Flying 61 Artikel (1 neu), Travel-Dealz REST 55
(nur Änderungen geholt), nichts fälschlich abgelaufen, keine Fehler.

| Quelle | Artikel | mit Preis | Fehler / Anmerkung |
|---|---|---|---|
| Travel-Dealz schnell (REST) | 55 | 47 | – (Peking Business 1.123 € läuft laut Seite bis 23.11.) |
| Travel-Dealz-Artikel (RSS) | 91 | 24 | – |
| Travel-Dealz-Suche | 64 | 30 | Limit 8 Suchen: Hangzhou, Chongqing, Xiamen nicht gesucht (bekannt) |
| Secret Flying (Bluesky) | 60 | 56 | – (£/$ korrekt umgerechnet) |
| mydealz | 30 | 17 | – |
| fly4free / fly4free Business | 50 / 2 | 26 / 2 | – |
| premium-flights (inkl. China-Tags) | 23 | 11 | – (US$/AU$-Titel bewusst ohne Preis) |
| reisetopia | 60 | 19 | – |
| You have been upgraded | 0 | 0 | Feed lesbar, neuester Artikel 18.10. (> 14 Tage) → 0 ist dort normal |
| ErrorFareAlerts | 10 | 10 | – |
| Urlaubspiraten | 48 | 6 | – |
| Google Flights | 5 Abrufe | 3 | 2 leer trotz zweitem Abruf (HAJ–HGH, FRA–SHA); FRA–SHA direkt danach mit 10 Flügen + Preisniveau |
| Travelpayouts | – | – | Token fehlt hier (Hinweis in der UI korrekt) |

Treffer: Deutschland→China Business: Peking ab FRA 1.123 € (TD), China Southern ab Budapest 1.440 € + 180 € (TD),
Budapest→China 1.520 € + 180 € (premium-flights). China Economy: Bestpreis Stockholm→Shanghai 430 € + 180 € = 610 €
(Secret Flying, über der Grenze). ZRH⇄HAM: Bestpreise Google 233 € (Hin/Rück), 151 € (one-way), kein Deal.
Behoben dabei (mit Test): „von Westkanada“ galt als Abflug ohne Angabe → landete im Radar
(`regions.place_kind` erkennt jetzt Himmelsrichtung + bekannter Ort).
`/api/alerts`, `/api/sources/health` und UI (Desktop/Mobil, Deals/Alarme/Quellen) per Screenshot geprüft.

### Offene Punkte (bewusst nicht geändert / Entscheidung des Nutzers)
- Feed-Artikel älter als 14 Tage werden ignoriert (`feed_max_age_days`) – fehlen z. B. „Luxemburg→China 1.640 €“
  (17.10.) und YHBU „Istanbul–Peking Business 900 €“ (27.09.); ob noch buchbar, ist unbekannt.
  YHBU zeigt deshalb oft „0 Artikel“ und gilt trotzdem als OK (kein „Veraltet“, weil alte Artikel vorher wegfallen).
- Travel-Dealz-Suche max. 8 Suchen/Lauf: Hangzhou, Chongqing, Xiamen werden nie gesucht.
- Google liefert manchmal nur die Suchmaske (auch beim zweiten Abruf; im Live-Lauf 2 von 5).
- Derselbe Deal mit > 5 % Preisunterschied (Turkmenistan Bangkok 1.208 € TD / 1.288 € EFA) erscheint zweimal im Radar.
- Radar Economy 40–300 € meldet auch Kurz-/Mittelstrecke und Blind Booking; Eurowings „Business“ zählt als Business.
- Zubringer-Aufschläge 80/180 € und Gesundheits-Schwellen (reisetopia 36 h) sind Annahmen.
- Amadeus nie live getestet (Self-Service eingestellt). SSRF-DNS-Prüfung eigener Feeds entfällt hinter HTTP-Proxy.

### Nicht live geprüft
- Travelpayouts-Kalender/`latest` (kein Token in der Session), Amadeus.
- Sofort-Prüfung nach schnellem Treffer und ntfy/Webhook-Zustellung (keine Kanäle eingerichtet; nur per Test).
- Wechselkurse von der EZB (`refresh_rates`), Datumsrotation über mehrere Läufe (nur 1 Lauf; per Test).
- Docker-Image mit Runde 2 nicht gebaut.

## Nächste Schritte (Reihenfolge)
1. Nutzer-OK für Runde 2 abwarten bzw. als gegeben nehmen → `wip/quellen-ausbau` auf `main` mergen und pushen
   (baut das Unraid-Image). Vorher Unit + E2E noch einmal laufen lassen.
2. Nach dem Ausrollen: im Container einen Lauf mit echtem Travelpayouts-Token prüfen.
3. Optional (RESEARCH.md Abschnitt 4): Feed-Alter je Quelle (YHBU länger), Suche über mehr Ziele rotieren.

## Umgebung (Cloud-Session)
- Netzwerk offen; `gh`-Token ungültig → GitHub nur per `git push` (Repo ist angebunden). Kein Repo-Erstellen möglich.
- Öffentliche Tunnel (cloudflared o. ä.) werden von der Sicherheitsprüfung blockiert → nicht versuchen.
- Klick-Demo als Artifact: https://claude.ai/artifact/4JJEwp32GUqj8N3rEBeyEn (Quelle wurde nicht ins Repo übernommen;
  baut sich aus `dealmonitor/static/*` + Mock-API, bei Bedarf neu erzeugen).
