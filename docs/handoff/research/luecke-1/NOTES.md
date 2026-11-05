# Lücke 1: Secret-Flying-Erstmelder (live geprüft 05.11.2026)
- Bluesky-API getAuthorFeed (public.api.bsky.app, ohne Login): 200, 50/100 Posts je Call, Cursor=createdAt des letzten Posts, 150 Posts = 6 Tage (30.10.-05.11.). ~142 mit Preis, 8 ab DACH (ZRH 2, FRA 2, VIE 3, BER 1). Neuester Post 05.11. 10:27 UTC.
- Bluesky-RSS bsky.app/profile/secretflying.bsky.social/rss: 302 auf /profile/did:plc:ffurylltuiy7da64xuf5lgne/rss (Redirect folgen), dann 200, 14 KB, 30 Items, description enthält vollen Link.
- Secret-Flying-Europe-Seite: 200 (2x), 9 Karten, nur Teilmenge (FRA-Phuket und ZRH-JNB fehlen). /feed/, wp-json, /zurich-flight-deals/, /posts/<slug> = 403 Cloudflare.
- Slug-Preis unzuverlässig (zurich-switzerland-mauritius-e213 gegen Titel 471 EUR), Beitrag 52303 ist recycelter Altpost.
- EFA /deals/flights: 200, 10 Karten, ?page=N, alle DACH, Datum dd.mm.yyyy hh:mm, Preis im Titel. /rss und /feed 302 auf Startseite. sitemap deals-flights-sitemap.xml 200.
- Telegram t.me/s/secretflying 302 (nicht vorhanden), Reddit-RSS 302, nitter 410, bsky searchPosts 403. Andere Bluesky-Konten (fly4free, holidaypirates, flightdeals) praktisch leer.
