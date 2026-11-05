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
  - Google: Preisniveau (`ds:1`) je Angebot, Datumsrotation ZRH⇄HAM (jeder Tag), Kombi aus 2 One-way.
    Leere Seiten werden eingeordnet und gezielt nachgefragt (siehe „Google: leere Seiten“ unten).
  - Nachbar-Hubs mit Zubringer-Aufschlag (+80/+180 €), mehr China-Codes.
  - Takt: schnelle Quellen alle 10 min, Sofort-Prüfung bei schnellem Treffer, Quellen-Gesundheit mit Meldung bei Wechsel.
  - Bewertung: Google-Bezugspreis, Fehlerpreis-Verdacht, Erstmeldung über Quellen, Beinahe-Deals.
  - UI: Preisverlauf, Badges, Beinahe-Deals, Seite „Quellen“; Demo mit allen neuen Feldern.
- Tests: `python3 -m unittest discover -s tests` → **650 grün**; `node tests/e2e/ui.mjs` → **43 Schritte grün**.

### Google: leere Seiten (Messung 05.11.2026, 42 Abrufe + Gegenprobe 8 Abrufe)
Eine leere Seite ist kein Zufall, sondern einer von drei Fällen (`googleflights.empty_kind`):
- **Suche nicht verstanden** (`unparsed`, kommt für denselben Text jedes Mal): Titel „Günstige Flüge weltweit
  finden …“, `ds:0` ohne Strecke. Betraf „… business class“-Texte. → Business/Premium/First fragen jetzt per
  **tfs-Link** an (`build_tfs_url`, Protobuf wie Googles eigene URLs), Economy per Suchtext; der andere Stil ist
  der Rückfall.
- **Hin/Rück ohne Verbindungen** (`no_itineraries`): gültige Antwort, Ergebnislisten leer. Seltene Paare mit
  langem Aufenthalt (HAJ–HGH, HAJ–PEK, FRA–HGH bei 12/17 Nächten). → einmal mit 7 Nächten; sonst „keine Flüge“
  (`no_results`), kein Fehler. Aufenthalt im Scan jetzt 7/12 Nächte begrenzt auf min/max (China: 12 statt 17).
- **ErrorResponse 13** (`error`, vorübergehend) → dieselbe URL nach 4–6 s. Das alte Muster erkannte echte
  Fehlerseiten nie (`errorHasStatus`, kein `hash`) – behoben.
- Neu gesehen in der Gegenprobe: Seite mit allen Verbindungen in `ds:1`, aber **ohne Ergebniszeilen im HTML**
  (FRA–PEK Business). → `ds1_results` liest sie (auf 20 echten Seiten deckungsgleich mit den HTML-Zeilen).
- Seite „Quellen“: Zahl „ohne Flüge laut Google“, „erst beim zweiten Abruf mit Flügen“ und Hinweise je Lauf.

Gegenprobe live (05.11.2026, 14:50–14:51 UTC, 8 Abrufe, 5 s Pause, alte Fehlfälle):
| Abfrage | Ergebnis |
|---|---|
| HAJ–HGH 13.–30.11. (17 N.) | ohne Verbindungen, **auch mit 7 Nächten** (13.–20.11.) ohne → „keine Flüge“ |
| FRA–SHA Business 13.–30.11. (17 N.) | tfs-Link: **9 Flüge ab 4.620 €** (per Suchtext vorher leer) |
| FRA–BJS Business 15.–27.01. | tfs-Link mit Stadtcode BJS: **11 Flüge ab 2.806 €** |
| HAJ–PEK 15.–27.01. (12 N.) | ohne Verbindungen; 7 Nächte → diesmal ErrorResponse 13 (vorher 8 Flüge ab 728 €) |
| FRA–PEK Business 15.–27.01. | tfs-Link: Daten nur in `ds:1` (jetzt lesbar: 11 Flüge ab 3.210 €); zweiter Abruf → **HTTP 429** |

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
- Google: **erstes HTTP 429** am 05.11. beim 8. Abruf der Gegenprobe (heute ~50 Abrufe in 35 min, 5–10 s
  Abstand). Der Schutzschalter pausiert Google dann für den Lauf. Ob 40 Abrufe/Lauf (Standard) alle 6 h
  dauerhaft gehen, ist **nicht** gemessen – im Container beobachten, ggf. `max_requests` senken.
- HAJ–HGH Hin/Rück liefert bei Google weder mit 17 noch mit 7 Nächten Verbindungen (one-way einmal Fehler 13).
  Andere seltene Paare können ebenso leer bleiben. Tageszeit nicht untersucht.
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
