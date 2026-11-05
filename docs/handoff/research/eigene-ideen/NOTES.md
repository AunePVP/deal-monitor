# eigene-ideen - Live-Tests 05.11.2026 (curl, Chrome-UA, de)
Google Flights ds:1 Preis-Insights (Parser: ../tools-automatisierbar/price_insights_parser.py) erneut bestaetigt:
 FRA-PEK Eco 10.-24.3.27: niedrigster 720, Normal 723, typisch 600-1050, 62 Tage Verlauf. Business: 2898, typisch 2600-3750.
 ZRH-HAM 15.-19.1.27: 168, Normal 235, typisch 195-275, code 1 = niedrig (Parser-Level stimmt).
Google-Seite ohne Ergebnisse ("Shell", ~1,95 MB statt 2,3-2,45 MB): 4 von 16 Erstabfragen (AMS/PRG/WAW/MXP-PVG), 2. Versuch lieferte alle 4.
 Bei nicht bedienten Strecken (ZRH-LBC, FDH-HAM, FMM-HAM) bleibt es auch beim 2. Versuch Shell.
Nicht moeglich: q mit mehreren Abflughaefen ("HAM,BRE,BER") = 0 Ergebnisse; /travel/explore = nur JS; &gl=IN/CN/TR aendert nichts (gleiche Preise).
Positionierung FRA-PVG-Muster (RT 10.-24.3.27, Google, Eco niedrigster Preis): HEL 568, BRU 580, BUD 607, MXP 613, PRG 617, FRA 658, WAW 669, AMS 671, DUS 695, HAM 705, MUC 715, VIE 718, CPH 698, CDG 667, IST 729, ZRH 804.
Nachbarn ZRH->HAM Jan: kein Nachbar guenstiger (BER 155 nonstop SWISS Insight-low 105-240).
Kayak.de/.ch Laenderseiten: JSON-LD Flight[] (40) + HTML-Block "Guenstigste Preise ... letzten 5 Tagen" je Zielstadt + Angebote mit Datum/Airline/"vor 1 Stunde". kayak.at 503.
nager.at/openholidays: 200 JSON ohne Key. TP data/airports.json (10.378), routes.json (64.964) ohne Token 200.
Ryanair farfnd: NUE/VIE oneWayFares 200, priceUpdated = Epoch-ms, heute. cheapestPerDay HAM-BUD leer (Route nicht bedient).
Feeds: travel-dealz Veroeffentlichung 05-17 UTC; If-Modified-Since -> 200 (kein 304), no-store. premium-flights www -> 301 -> premium-flights.com.
Negativ: Google News RSS (Rauschen), mydealz /rss/search + /rss/tag 404, Bluesky traveldealz/travel-dealz.de 404, frankfurtflyer.de/feed 200 aber 0 Byte, Travelzoo-Atom nur Pakete, edreams.ch 404.
