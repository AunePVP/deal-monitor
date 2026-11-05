# Lücke 3: ZRH<->HAM Datumsabdeckung ohne TP-Token (getestet 2026-11-05)

## Google Flights one-way je Datum (live)
- URL: https://www.google.com/travel/flights?hl=de&curr=EUR&q=Flights%20from%20ZRH%20to%20HAM%20on%20YYYY-MM-DD%20one%20way
- HTTP 200, 0,65-1,0 s, 1,9-2,5 MB. 14 Abrufe: 12 ok, 2 mit ds:1 = ErrorResponse (data:[13,null,...]) -> transient, Retry nach 2 s ok (2026-12-05 bestätigt). Nicht bei Fehler als "kein Flug" werten.
- Regex: key: 'ds:1',(?: hash: '\d+',)? data:(.*?), sideChannel  -> json.loads. Fehler: d[0]==13.
- d[5] = [lvl, [null,min], [null,typisch], [null,delta], [null,spanne_low], [null,spanne_high], 1, ..., [[ [ms,preis],... ]]]  (62 Tagespunkte Preisverlauf, 04.09.-04.11.)
  Beispiel 21.01.27 HAM->ZRH: [2,70,79,9,55,135]; ZRH->HAM 14.01.27: lvl 1, min 82, typ 105, Spanne 90-150.
  lvl: 1 = niedrig, 2 = typisch gesichert; 3/4/5 beobachtet bei 121/130/174 EUR (Bedeutung UNBESTÄTIGT) -> nicht auf lvl verlassen, aus Preis vs. Spanne ableiten.
- ACHTUNG: d[3][0]-Angebotspfad o[1][0][1] fand 82 EUR nicht (nur 151); der Preis steht aber im HTML (aria-label="82 Euro", Text "82\xa0€"). Mindestpreis = min über alle (\d+)\xa0€ im Fließtext der Ergebnisliste bzw. d[5][1]. d[5][1] == HTML-Minimum bestätigt (82/82).
- Tagesstreuung ZRH->HAM one-way (10 Daten): 82..174 EUR -> Rotation lohnt.
## Rotation (Vorschlag, zustandslos)
items = [(dir, tag) für 90 Tage x 2 Richtungen] = 180; Lauf-Index n = int(epoch/10800); je Lauf 23 Items ab (n*23) % 180 -> alle 180 in 8 Läufen = 24 h. Plus feste Datumspaare (Budget 40 gesamt). 2 s Pause => ~1 min Laufzeit. Nahe 14 Tage doppelt gewichten. Alarm bei Preis <= Alarm-Max oder (min <= 0.6*typisch bzw. < Spanne_low).
## Verworfen
- GetCalendarGraph-RPC (POST): mein Body-Versuch -> 400. UNBESTÄTIGT/ungelöst, nicht weiter verfolgt.
- Kayak /flüge/Hamburg-HAM/Zuerich-ZRH: 200, JSON-LD AggregateOffer lowPrice 75.07, Block 'Aktuelle Angebote' = Hin+Rück mit Datum, aber "vor 3+ Tagen", 135-143 EUR -> veraltet/teuer, nur Plausibilität.
- eDreams: 200, nur Wochenbalken (18.-24. Jan, 61-86 EUR), 'Billigster Flug 56 EUR (72 h)'; Plausibilität, keine Datumszuordnung.
- Skyscanner 708 B Captcha-Stub; Momondo 404; Opodo 404; checkfelix 404; skiplagged 403; Eurowings 403; flightconnections 202/leer; fluege.de 200 ohne Preise; TP calendar 401 ohne Token.
