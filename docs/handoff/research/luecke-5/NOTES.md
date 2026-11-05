# Luecke 5 Canary (Live-Test 2026-11-05 ~10:36 UTC)
Alle 4 Kandidaten HTTP 200. Luecken-Messungen (Posts-Abstaende):
- TD-REST (40 Posts, 115 h): Median 1,0 h, MAX 17,3 h (Nachtluecke) -> "<12 h" feuert nachts falsch. Nutze date_gmt (date ist Berlin-Ortszeit!). Schwelle 20 h oder tageszeitabhaengig. X-WP-Total=431 aendert sich nur bei Neu/Loeschen -> nur Zusatzsignal.
- Bluesky-SF RSS (30 Items, 25 h): Median 0,58 h, max 8,8 h -> <3 h feuert oft falsch; Schwelle 10 h. title leer, Text in <description>, pubDate "05 Nov 2026 10:27 +0000" (kein Komma-Wochentag, email.utils parst es).
- premium-flights (12 Items, 34 h): Median 0,75 h, max 9,4 h; <72 h ok (nur Grobalarm).
- EFA HTML (10 Eintraege): bursty, Abstaende bis 25,3 h -> 48 h ok. Zeitstempel "DD.MM.YYYY HH:MM" (Ortszeit), Preis "€ 1059 ab" im Text, Titel mit "ab 295 €".
