# Lücke 6: Business nach China ab Nachbar-Hubs (live 05.11.2026)
- TD-REST /wp-json/wp/v2/deal: 431 aktive Deals, 234 (54 %) OHNE origins-Term -> Abflug nur aus Titel. destinations=745 (Eltern) allein 9 Treffer, +categories=884 -> 0. Blatt-IDs + 884: 4 Treffer. China-Blatt-IDs: PEK 112, PVG 292, CAN 2808, CTU 2537, CKG 3097, SZX 3195, XIY 3169, HGH 3193, DLC 14472, KMG 14291, NKG 3017, URC 3749. Business 884, Economy 1757, Premium Economy 3225. suspend_date in meta.
- Letzte 6 Wochen China-Business <1999 EUR: nur FRA-PEK 1123 (TD) ab DACH; BUD 1440/1490/1520, LUX 1486/1515/1640, OSL 1875, BCN 1919, ROM-CAN 1560 = Nachbar-Hubs.
- premium-flights Tag-Feeds /tag/china|pek|shanghai/feed/ (12 Items, ?paged=N): fangen ROM-Guangzhou 1560, die ?s=china nicht lieferte.
- YHBU Kategorie: mit Standardprofil nur 9/20 China/Asien-Titel als Flug erkannt (Kategorie 'Billige Premium Tickets' + Airline-Kategorien); mit use_categories=False 47/50 Flug, 45/50 Preis, nur 5/50 Abflug home.
- reisetopia /tag/business-class/feed/: Preis nicht im Titel, nur content:encoded 'Buchbar ab (nur) X Euro'; alle anderen Tag-/Kategorie-/Such-/wp-json-Pfade 403.
