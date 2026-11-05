# Recherche-Notizen "dach-deal-seiten" (Stand 2026-11-04, alle Abrufe live von dieser Maschine, curl + Chrome-UA, max. 2 Abrufe/URL)

Rohdaten: raw/*.body + raw/*.hdr, Logs raw/_log_batch*.txt, Analysen raw/_analysis_*.txt, raw/_stats.txt

## 1. travel-dealz.de – Struktur (WordPress, Custom Post Types + Custom Taxonomies)
- Post-Types (wp-json/wp/v2/types): post, article, news, **deal**, **ticker**, coupon (URL /gutschein/), podcast
- Taxonomien: category (/kategorie/), post_tag (**/thema/**, NICHT /tag/), airline (/airline/), region (/region/ = DE/AT/CH-Sichtbarkeit),
  destinations (**/reiseziel/**), origins (**/abflug/** = Abflughafen), period, visibility (hot/highlight/special)
- Kategorie-IDs: fluge=24 (Parent), economy-class=1757, business-class=884, premium-economy=3225, first-class=1047, milespoints=12,
  kreuzfahrten=2480, hotels=41, pauschalreisen=13, kreditkarten=1645
- Abflughafen-IDs (origins): FRA 2940, MUC 2941, HAM 2945, DUS 2939, BER 2942, VIE 2965, STR 2946, ZRH 2971, CGN 2938, HAJ 2957,
  NUE 2947, BSL 2972, GVA 2973, BRE 2959, LEJ 2948, DRS 2949, DTM 2954, SZG 2966, FMO 2956, FMM 2952, GRZ 2967, "deutschland" 2937
- Reiseziel-IDs (destinations): china=745 (Parent), shanghai=292, peking=112, guangzhou=2808, chengdu=2537, xian=3169, chongqing=3097,
  hangzhou=3193, nanjing=3017, kunming=14291, shenzhen=3195, xiamen=3194, macau=2800; hamburg=192(dest) slug hamburg; zurich=25 Posts
- visibility: hot=39997 (321 Posts), highlight=39741, special=39998
- Feeds liefern 20 Items/Seite, ?paged=2 funktioniert (getestet economy-class). /feed/?post_type=deal liefert 100 Items (3,5 MB).
- Tag-Feeds (/thema/<slug>/feed/) zeigen nur AKTIVE (nicht abgelaufene) Deals -> errorfares nur 3 alte Anleitungen (2017-2021), eilmeldungen 0 Items.
- /tag/<slug>/feed/ ist KEIN Tag-Feed (hamburg -> 404, china -> Kommentar-Feed eines Podcasts).
- Kein Tag "hamburg"/"china" (Tags sind Programme/Marken); Orte laufen über origins/destinations.
- RSS-Items: <title> mit Preis ("ab 306€"), <category> nur Kategorie-Name, kein Abflughafen -> Abflughafen über JSON-API (origins) oder Titel-Parsing.
- JSON-API (/wp-json/wp/v2/deal, /ticker): Filter categories=, origins=, destinations=, visibility=, after=ISO8601, _fields=..., meta.suspend_date = Ablaufdatum.
  Getestet: deal?categories=1757,884,3225&after=2026-11-01 -> 5 Items; deal?categories=884 -> 10 Business-Deals mit origins; deal?origins=2945 -> 10;
  deal?categories=1757&origins=2945 -> 4; ticker?destinations=745 -> [] (Ticker tragen oft keine destinations).
- Sitemaps: post-, article-, news-, deal-sitemap1..8, coupon-, podcast-, category-, region-sitemap.xml

## 2. Getestete URLs (Status) – Details in raw/_log_batch*.txt
siehe StructuredOutput / Tabelle unten
td_home | 200 https://travel-dealz.de/ text/html; charset=utf-8 253815
td_wpcats | 200 https://travel-dealz.de/wp-json/wp/v2/categories?per_page=100 application/json; charset=UTF-8 38228
td_wptags | 200 https://travel-dealz.de/wp-json/wp/v2/tags?per_page=100&orderby=count&order=desc application/json; charset=UTF-8 161568
td_sitemapidx | 200 https://travel-dealz.de/sitemap_index.xml text/xml; charset=UTF-8 2276
td_tag_hamburg | 404 https://travel-dealz.de/tag/hamburg/feed/ application/rss+xml; charset=UTF-8 22192
td_tag_china | 200 https://travel-dealz.de/tag/china/feed/ application/rss+xml; charset=UTF-8 4634
fliegensparen_feed | curl: (60) SSL certificate problem: certificate has expired
More details here: https://curl.se/docs/sslcerts.html

curl failed to verify the legitimacy of the server and therefore could not
establish a secure connection to it. To learn more about this situation and
how to fix it, please visit the web page mentioned above.
000 https://www.fliegen-sparen.de/feed/  0
frankfurtflyer_feed | 200 https://frankfurtflyer.de/feed/ application/octet-stream; charset=UTF-8 0
meilenoptimieren_feed | 200 https://meilenoptimieren.com/feed/ application/rss+xml; charset=UTF-8 24975
yhbu_feed | curl: (60) SSL: no alternative certificate subject name matches target host name 'yhbu.de'
More details here: https://curl.se/docs/sslcerts.html

curl failed to verify the legitimacy of the server and therefore could not
establish a secure connection to it. To learn more about this situation and
how to fix it, please visit the web page mentioned above.
000 https://yhbu.de/feed/  0
fcam_feed | 200 https://app.rebrandly.com/broken-links text/html 8052
travelzoo_de_home | 200 https://www.travelzoo.com/de/ text/html; charset=utf-8 236775
ferienpiraten_feed | 200 https://www.ferienpiraten.ch/feed application/xml 7965
flugdeals_ch_feed | curl: (35) Recv failure: Connection reset by peer
000 https://www.flugdeals.ch/feed/  0
urlaubstracker_feed | 200 https://www.urlaubstracker.de/feed/ application/rss+xml; charset=UTF-8 167345
travelbook_feed | 403 https://www.travelbook.de/feed text/html 373
insideflyer_feed | 200 https://insideflyer.de/feed/ application/rss+xml; charset=UTF-8 160201
reiseuhu_feed | 403 https://www.reiseuhu.de/feed/ application/json; charset=UTF-8 162
urlaubsheld_feed | curl: (35) Recv failure: Connection reset by peer
000 https://urlaubsheld.de/ text/html; charset=utf-8 0
holidayguru_ch_feed | 401 https://www.holidayguru.ch/feed/ text/html 590
urlaubsguru_at_feed | 401 https://www.urlaubsguru.at/feed/ text/html 590
preisjaeger_fluege | 200 https://www.preisjaeger.at/rss/gruppe/fluege text/xml; charset=utf-8 158852
airguru_de_feed | 500 https://airguru.de/feed/ application/json; charset=UTF-8 4931
dealdoktor_feed | 200 https://www.dealdoktor.de/feed/ application/rss+xml; charset=UTF-8 69667
schnaeppchenfuchs_feed | 404 https://schnaeppchenfuchs.com/feed/ application/json 51
td_region_sitemap | 200 https://travel-dealz.de/region-sitemap.xml text/xml; charset=UTF-8 1074
td_cat_sitemap | 200 https://travel-dealz.de/category-sitemap.xml text/xml; charset=UTF-8 3473
td_wptypes | 200 https://travel-dealz.de/wp-json/wp/v2/types application/json; charset=UTF-8 13679
td_wptax | 200 https://travel-dealz.de/wp-json/wp/v2/taxonomies application/json; charset=UTF-8 5789
td_cat_economy | 200 https://travel-dealz.de/kategorie/fluge/economy-class/feed/ application/rss+xml; charset=UTF-8 449855
td_cat_business | 200 https://travel-dealz.de/kategorie/fluge/business-class/feed/ application/rss+xml; charset=UTF-8 561573
td_cat_premeco | 200 https://travel-dealz.de/kategorie/fluge/premium-economy/feed/ application/rss+xml; charset=UTF-8 671324
td_cat_first | 200 https://travel-dealz.de/kategorie/fluge/first-class/feed/ application/rss+xml; charset=UTF-8 698267
td_cat_fluge | 200 https://travel-dealz.de/kategorie/fluge/feed/ application/rss+xml; charset=UTF-8 438386
td_cat_miles | 200 https://travel-dealz.de/kategorie/milespoints/feed/ application/rss+xml; charset=UTF-8 284850
td_thema_errorfares | 200 https://travel-dealz.de/thema/errorfares/feed/ application/rss+xml; charset=UTF-8 53500
td_thema_gabelflug | 200 https://travel-dealz.de/thema/gabelflug/feed/ application/rss+xml; charset=UTF-8 78032
td_thema_oneway | 200 https://travel-dealz.de/thema/oneway/feed/ application/rss+xml; charset=UTF-8 217517
td_thema_eilmeldungen | 200 https://travel-dealz.de/thema/eilmeldungen/feed/ application/rss+xml; charset=UTF-8 1259
td_thema_mileagerun | 200 https://travel-dealz.de/thema/mileage-run/feed/ application/rss+xml; charset=UTF-8 14714
td_tagsearch_hamburg | 200 https://travel-dealz.de/wp-json/wp/v2/tags?search=hamburg application/json; charset=UTF-8 1155
td_tagsearch_china | 200 https://travel-dealz.de/wp-json/wp/v2/tags?search=china application/json; charset=UTF-8 1573
td_origins | 200 https://travel-dealz.de/wp-json/wp/v2/origins?per_page=100&orderby=count&order=desc application/json; charset=UTF-8 134452
td_dest_china | 200 https://travel-dealz.de/wp-json/wp/v2/destinations?search=china application/json; charset=UTF-8 4915
td_dest_hamburg | 200 https://travel-dealz.de/wp-json/wp/v2/destinations?search=hamburg application/json; charset=UTF-8 2155
td_dest_zuerich | 200 https://travel-dealz.de/wp-json/wp/v2/destinations?search=z%C3%BCrich application/json; charset=UTF-8 1319
td_json_deal | 200 https://travel-dealz.de/wp-json/wp/v2/deal?per_page=5 application/json; charset=UTF-8 126143
td_json_ticker | 200 https://travel-dealz.de/wp-json/wp/v2/ticker?per_page=5 application/json; charset=UTF-8 29338
td_region_schweiz | 200 https://travel-dealz.de/region/schweiz/feed/ application/rss+xml; charset=UTF-8 583496
td_region_de | 200 https://travel-dealz.de/region/deutschland/feed/ application/rss+xml; charset=UTF-8 554050
td_feed_posttype_deal | 200 https://travel-dealz.de/feed/?post_type=deal application/rss+xml; charset=UTF-8 3562105
td_abflug_ham | 200 https://travel-dealz.de/abflug/hamburg-ham/feed/ application/rss+xml; charset=UTF-8 815722
td_abflug_zrh | 200 https://travel-dealz.de/abflug/zuerich-zrh/feed/ application/rss+xml; charset=UTF-8 292572
td_reiseziel_china | 200 https://travel-dealz.de/reiseziel/china/feed/ application/rss+xml; charset=UTF-8 468616
td_reiseziel_hamburg | 200 https://travel-dealz.de/reiseziel/hamburg/feed/ application/rss+xml; charset=UTF-8 637947
td_visibility | 200 https://travel-dealz.de/wp-json/wp/v2/visibility application/json; charset=UTF-8 8863
td_json_deal_ham | 200 https://travel-dealz.de/wp-json/wp/v2/deal?per_page=10&origins=2945&_fields=id,date,title,link,categories,origins,destinations,airline,meta.suspend_date application/json; charset=UTF-8 4193
td_json_ticker_china | 200 https://travel-dealz.de/wp-json/wp/v2/ticker?per_page=10&destinations=745&_fields=id,date,title,link,categories,origins,destinations application/json; charset=UTF-8 2
td_json_deal_eco_ham | 200 https://travel-dealz.de/wp-json/wp/v2/deal?per_page=10&categories=1757&origins=2945&_fields=id,date,title,link,categories,origins,destinations,meta.suspend_date application/json; charset=UTF-8 1422
td_json_deal_business | 200 https://travel-dealz.de/wp-json/wp/v2/deal?per_page=10&categories=884&_fields=id,date,title,link,categories,origins,destinations,meta.suspend_date application/json; charset=UTF-8 3378
travelzoo_de_feed | 200 https://www.travelzoo.com/de/feed/ application/atom+xml; charset=utf-8 106689
travelzoo_ch_feed | 404 https://www.travelzoo.com/ch/feed/ text/html; charset=utf-8 126651
fcam_home | 200 https://first-class-and-more.de:443/ text/html; charset=UTF-8 105255
frankfurtflyer_feed2 | 200 https://frankfurtflyer.de/feed/rss2/ application/rss+xml; charset=UTF-8 35286
frankfurtflyer_json | 200 https://frankfurtflyer.de/wp-json/wp/v2/posts?per_page=10&_fields=id,date,title,link,categories application/json; charset=UTF-8 2582
yhbu_feed2 | 200 https://youhavebeenupgraded.boardingarea.com/feed/ application/rss+xml; charset=UTF-8 68607
fliegensparen_http | curl: (6) Could not resolve host: www.fliegen-sparen.de
000 http://www.fliegen-sparen.de/feed/  0
flugdeals_ch_home | curl: (35) Recv failure: Connection reset by peer
000 https://flugdeals.ch/  0
reisetopia_cats | 403 https://reisetopia.de/wp-json/wp/v2/categories?per_page=100 text/html; charset=UTF-8 4910
meilenoptimieren_cats | 200 https://meilenoptimieren.com/wp-json/wp/v2/categories?per_page=100 application/json; charset=UTF-8 274031
urlaubspiraten_fluege_feed | 404 https://www.urlaubspiraten.de/fluege/feed text/html 431367
ferienpiraten_fluege_feed | 404 https://www.ferienpiraten.ch/fluege/feed text/html 340181
vielfliegertreff_home | 200 https://www.vielfliegertreff.de/forum/ text/html; charset=utf-8 198118
flugladen_home | 200 https://www.flugladen.de/ text/html; charset=utf-8 269596
billigfluege_home | 200 https://go.billigfluege.de/?user-id=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1aWQiOiIzMTViYjA2Ni0xZTQwLTQ2OTEtYjM2Ny01Zjc2ZThkM2NlMmEiLCJpc3MiOiJ3ZWItYXBwIiwidiI6MywiaXAiOiI6OmZmZmY6MTAwLjEyMi4xMTAuMjIzIiwidXNlckFnZW50IjoiTW96aWxsYS81LjAgKFdpbmRvd3MgTlQgMTAuMDsgV2luNjQ7IHg2NCkgQXBwbGVXZWJLaXQvNTM3LjM2IChLSFRNTCwgbGlrZSBHZWNrbykgQ2hyb21lLzEzMC4wLjAuMCBTYWZhcmkvNTM3LjM2IiwiaWF0IjoxNzkzODI5MDcxfQ.81zD4EM8IiQv1kP16F3d1sbMN21vS49YBf-53SnuSdI text/html; charset=utf-8 29909
nurbilligfluege_home | curl: (56) CONNECT tunnel failed, response 502
000 https://www.nur-billigfluege.de/  0
reisefieber_feed | 200 https://www.reisefieber.de/feed/ application/rss+xml; charset=UTF-8 162643
fluegede_magazin_feed | 200 https://www.fluege.de/ text/html; charset=UTF-8 154544
holidaycheck_home | 200 https://www.holidaycheck.de/ text/html; charset=utf-8 669522
travelfree_feed | 200 https://travelfree.info/feed/ application/rss+xml; charset=UTF-8 101936
flynous_feed | 403 https://www.flynous.com/feed/ text/plain 25
reisetopia_ch_feed | 403 https://www.reisetopia.ch/feed/ text/html; charset=UTF-8 6024
flugdeals_at_feed | curl: (56) CONNECT tunnel failed, response 502
000 https://www.flugdeals.at/feed/  0
urlaubspiraten_at_feed | 200 https://www.urlaubspiraten.at/feed application/xml 12148
flugdeals_de_feed | curl: (60) SSL certificate problem: self-signed certificate
More details here: https://curl.se/docs/sslcerts.html

curl failed to verify the legitimacy of the server and therefore could not
establish a secure connection to it. To learn more about this situation and
how to fix it, please visit the web page mentioned above.
000 https://www.flugdeals.de/feed/  0
airguru_json | 200 https://airguru.de/wp-json/wp/v2/posts?per_page=10&_fields=id,date,title,link,categories application/json; charset=UTF-8 2007
meilenfuchs_feed | 404 https://www.meilenfuchs.de/feed/ text/html; charset=iso-8859-1 236
reisedeals_ch_feed | 200 https://www.reisedeals.ch/feed/ application/rss+xml; charset=UTF-8 1131
td_visibility_hot_feed | 200 https://travel-dealz.de/visibility/hot/feed/ application/rss+xml; charset=UTF-8 544293
td_json_deal_hot | 200 https://travel-dealz.de/wp-json/wp/v2/deal?per_page=10&visibility=39997&_fields=id,date,title,link,categories,origins,visibility,meta.suspend_date application/json; charset=UTF-8 3141
td_cat_economy_p2 | 200 https://travel-dealz.de/kategorie/fluge/economy-class/feed/?paged=2 application/rss+xml; charset=UTF-8 414515
meilenoptimieren_flugangebote_feed | 200 https://meilenoptimieren.com/category/flugangebote/feed/ application/rss+xml; charset=UTF-8 25680
frankfurtflyer_cats | 200 https://frankfurtflyer.de/wp-json/wp/v2/categories?per_page=100&orderby=count&order=desc application/json; charset=UTF-8 102491
yhbu_cats | 200 https://youhavebeenupgraded.boardingarea.com/wp-json/wp/v2/categories?per_page=100&orderby=count&order=desc application/json; charset=UTF-8 500525
vft_firstbusiness_rss | 200 https://www.vielfliegertreff.de/forum/forums/first-business-class-tarife.53/index.rss application/rss+xml; charset=utf-8 42320
vft_economy_rss | 200 https://www.vielfliegertreff.de/forum/forums/economy-class-tarife.54/index.rss application/rss+xml; charset=utf-8 42738
vft_bestpreis_rss | 200 https://www.vielfliegertreff.de/forum/forums/fliegen-zum-besten-preis.4/index.rss application/rss+xml; charset=utf-8 805
reisetopia_deals_feed | 403 https://reisetopia.de/deals/feed/ text/html; charset=UTF-8 4909
fcam_feed2 | curl: (6) Could not resolve host: first-class-and-more.de
301 http://first-class-and-more.de/feed text/html; charset=iso-8859-1 0
aerotelegraph_feed | 200 https://www.aerotelegraph.com/feed application/xml; charset=utf-8 55849
frankfurtflyer_deal_feed | 200 https://frankfurtflyer.de/category/deal/feed/ application/octet-stream; charset=UTF-8 0
yhbu_premium_feed | 200 https://youhavebeenupgraded.boardingarea.com/category/billige-premium-tickets/feed/ application/rss+xml; charset=UTF-8 65535
vft_mileageruns_rss | 200 https://www.vielfliegertreff.de/forum/forums/mileage-runs.5/index.rss application/rss+xml; charset=utf-8 31676
td_dest_china_children | 200 https://travel-dealz.de/wp-json/wp/v2/destinations?parent=745&per_page=50&_fields=id,slug,name,count application/json; charset=UTF-8 1941
td_json_flights_after | 200 https://travel-dealz.de/wp-json/wp/v2/deal?per_page=20&categories=1757,884,3225&after=2026-11-01T00:00:00&_fields=id,date,title,link,categories,origins,destinations,meta.suspend_date application/json; charset=UTF-8 1763
sparhamster_feed | 200 https://www.sparhamster.at/feed/ application/rss+xml; charset=UTF-8 35102
frankfurtflyer_deal_rss2 | 200 https://frankfurtflyer.de/category/deal/feed/rss2/ application/rss+xml; charset=UTF-8 34408
