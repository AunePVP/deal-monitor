# Übergabe – Stand 05.11.2026 (Review-Fixes fertig, nichts committet)

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
- **05.11.2026: alles auf `main` gemergt und gepusht** (Image wird per Actions gebaut). `wip/quellen-ausbau` = gleicher Stand.
- `wip/quellen-ausbau`: „Mehr Quellen“ nach
  `docs/handoff/SPEC-2-quellen.md` komplett: A Feeds, B Radar, C Google Flights, D Amadeus, E Integration
  (Agenten-Berichte: `docs/handoff/agent-results/bau-runde-1.json`).
- Review (Parsing, Backend, Sicherheit, Vollständigkeit) gelaufen, **alle 25 bestätigten Befunde behoben** (05.11.2026), u. a.:
  Google-Gegenprüfung nimmt nur Flüge, die zu den Alarm-Regeln passen (Umstiege/Dauer/Airline), meldet den Google-Preis
  und verwirft über der Grenze; nächstgünstiger Termin wird geprüft, wenn der günstigste durchfällt; aussortierte Deals
  gelten nicht mehr als gemeldet; Google-Budget: ¼ Reserve für Gegenprüfung, Rest fair auf alle Alarme verteilt;
  Feed-Erkennung (First Minute, Businessclass, Premium-Economy, Airline-Namen als Abflug, „Kanton Zürich“, „YES YOU CAN“,
  Abflug Schweiz bei @DE-Alarm, Meilen/Nächte-Titel, „1,210€“); Ablauf alter/abgewählter Feed-Deals, Migration
  `travel-dealz-feed` → `feed:travel-dealz`, einmalige Einstellungs-Migration von `main`; HTTP-Größen-/Zeitlimit;
  eigene Feeds nur öffentliche Adressen; Links nur http(s); `run --dry-run` arbeitet auf einer DB-Kopie;
  Amadeus-Texte („Self-Service eingestellt“); Unraid-Vorlage ohne Secret-Felder, `ALLOWED_HOSTS` nicht mehr `*`
  (dafür gehen Heimnetz-Namen immer: `tower`, `*.local`, `*.lan`, `*.home.arpa`, `*.internal`, `*.fritz.box`).
- Google pausiert nach 3 Netzwerkfehlern hintereinander für den Rest des Laufs (Lauf ohne Netz: 29 s statt alle Abfragen).
- Docker lokal gebaut + gestartet: `healthy`, erster Lauf endet sauber (hier ohne DNS im Container → Fehler je Quelle gemeldet).
- GitGuardian-Dummies erledigt (`123456:test-dummy`, `test-dummy-token`, `test-pw`, Amadeus-Fixture-Token).
- Tests: `python3 -m unittest discover -s tests` → **297 grün**; `node tests/e2e/ui.mjs` → **37 Schritte grün**
  (neue Tests: `tests/test_review_fixes.py`, Erweiterungen in `test_integration.py`, `test_google.py`, `test_web.py`).
- Live (05.11.2026): `run --dry-run --sources feeds`: 190 Artikel (≤ 14 Tage, dedupliziert), 92 Flug-Artikel,
  0 Alarm-Treffer (aktuell kein passender Artikel), 10 Radar-Treffer (Antalya 59 €, Paris 72 €, Stockholm 85 €,
  Izmir 125 €, Island 156 €, Cabo one-way 210 €, New York 298 €, Erbil „Business“ 390 €, Condor Premium NYC 640 €,
  Bangkok Business 1.208 €); keine Kreuzfahrten/Fremdabflüge. Google 1 Gegenprüf-Abfrage ZRH–HAM 15.–19.01.2027
  mit „nonstop, ohne KL“: 183 € SWISS (168 € KLM mit Umstieg korrekt übersprungen).

### Offene Punkte (bewusst nicht geändert / Entscheidung des Nutzers)
- Feed-Artikel älter als 14 Tage werden ignoriert (`feed_max_age_days`) – dadurch fehlt z. B. „Peking Business ab
  Frankfurt 1.123 €“ vom 30.09.; ob noch buchbar, ist unbekannt (RSS kennzeichnet abgelaufene Deals nicht).
- Travel-Dealz-Suche max. 8 Suchen/Lauf: Hangzhou, Chongqing, Xiamen werden nie gesucht – steht jetzt als Hinweis im
  Lauf, deren Such-Deals laufen dann nicht ab.
- Google liefert manchmal nur die Suchmaske (zählt als Fehlschlag, kein zweiter Versuch); Pause zwischen Abfragen
  2–4 s zufällig (laut Spec, kann < 3 s sein).
- Derselbe Deal von zwei Seiten wird nur im Radar zusammengeführt, bei Alarm-Deals nicht.
- Radar Economy 40–300 € meldet auch Kurz-/Mittelstrecke; Eurowings „Business“ zählt als Business.
- Mobil: Radar-Chip erst nach Wischen in der Chip-Leiste sichtbar.
- Amadeus nie live getestet (Self-Service laut Anbieter eingestellt); Amadeus-Budget wird (anders als Google) nicht
  auf die Alarme verteilt.
- SSRF-Schutz eigener Feeds: Die DNS-Prüfung entfällt, wenn ein HTTP-Proxy die Namen auflöst (wie in der Cloud-Session).
- Voller Lauf mit allen Quellen (Travelpayouts-Token fehlt hier) lief live nicht; zweiter Zeitplan-Takt nur per Test.

## Nächste Schritte (Reihenfolge)
Schritte 1–6 erledigt (Integration, Live-Lauf, Review + Fixes, GitGuardian-Dummies, Docker + Merge/Push auf `main`,
Abschlussmeldung). Offen nur noch: Runde 2 – **nur nach OK des Nutzers**.

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
