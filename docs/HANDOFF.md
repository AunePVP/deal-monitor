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
- Tests: `python3 -m unittest discover -s tests` → **665 grün**; `node tests/e2e/ui.mjs` → **44 Schritte grün**.

### Altersregel der Deal-Seiten (05.11.2026, `feed.is_current`)
Vorher fiel jeder Artikel nach 14 Tagen weg – auch mit Ablaufdatum in der Zukunft – und lief damit sofort ab.
Jetzt gilt: als „EXPIRED/abgelaufen“ markiert → weg; **Ablaufdatum bekannt → bis dahin** (egal wie alt,
> 400 Tage voraus = unbekannt); sonst Höchstalter je Quelle: **YHBU 45, premium-flights 30**, alle anderen
`feed_max_age_days` (14; wirksam ist der größere Wert). Travel-Dealz REST holt beim vollen Abruf alle
aktiven Beiträge (ohne `modified_after`; die API liefert nur aktive, live 72 Deals + 21 Ticker).
Messung dazu (live 05.11.): TD `suspend_date` im Median 45,5 Tage nach Erscheinen, 50 von 72 aktiven Deals
älter als 14 Tage; premium-flights Ticketing-Frist Median 29 Tage, „EXPIRED“ Median 25 Tage; YHBU Fristen
zu 75 % ≤ 25 Tage, Postlücken bis 583 h.
- Artikel älter als `feed_max_age_days` werden **gespeichert, aber nicht gemeldet** (auch nicht „wieder da“,
  `monitor.quiet_old`) – kein Schwall alter Meldungen beim Rollout.
- Deal-Karte ohne Ablaufdatum, Artikel > 14 Tage: Badge „Artikel vom TT.MM. · Ablauf unbekannt“ (nie „gültig“).
- Gesundheit auf Rohwerten: „50 Artikel im Feed, 5 aktuell“; YHBU `Rule(720)` (0 Artikel = LEER).
- Live-Lauf danach (frische DB): neu und still gespeichert YHBU Budapest 1.670 € (33 T.), Istanbul–Peking
  1.080 € (39 T.), premium-flights Luxemburg→China 1.720 €, Luxemburg→Shanghai 1.595 € (dort hängt
  Travel-Dealz „Shanghai ab Luxemburg“ 1.566 € als zweite Quelle dran), Radar z. B. Kuala Lumpur Business
  1.148 € (TD, läuft bis 16.11.). Gemeldet nur Artikel ≤ 14 Tage. Zweiter Lauf: 0 Meldungen. Ob die YHBU-Deals
  noch buchbar sind, ist unbekannt (Seite per Cloudflare gesperrt) – daher nur „Ablauf unbekannt“.

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
| You have been upgraded | 0 | 0 | damals: neuester Artikel 18.10. (> 14 Tage) → 0; jetzt 50 im Feed, 5 aktuell (siehe Altersregel) |
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
- YHBU/premium-flights nennen nur selten ein Buchungsende (20 % bzw. 29 % der Artikel, im Text). Optional:
  Loader über WP-REST (`/wp-json/wp/v2/posts?categories=62` bzw. `?tags=`, mit `content`, HTTP 200 trotz
  Cloudflare auf den Artikelseiten) – „TICKETS MUST BE ISSUED ON/BEFORE ddMMMyy“ → `expires`, „EXPIRED“ im Text →
  verwerfen. Nicht gebaut (neuer Loader am Vortag der Abgabe).
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
3. Optional: YHBU/premium-flights-Fristen per WP-REST (siehe Offene Punkte), Suche über mehr Ziele rotieren.

## Umgebung (Cloud-Session)
- Netzwerk offen; `gh`-Token ungültig → GitHub nur per `git push` (Repo ist angebunden). Kein Repo-Erstellen möglich.
- Öffentliche Tunnel (cloudflared o. ä.) werden von der Sicherheitsprüfung blockiert → nicht versuchen.
- Klick-Demo als Artifact: https://claude.ai/artifact/4JJEwp32GUqj8N3rEBeyEn (Quelle wurde nicht ins Repo übernommen;
  baut sich aus `dealmonitor/static/*` + Mock-API, bei Bedarf neu erzeugen).
