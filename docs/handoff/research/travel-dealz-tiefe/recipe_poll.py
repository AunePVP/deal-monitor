#!/usr/bin/env python3
"""Referenz (nur Recherche, nicht im Produktivcode): Travel-Dealz-REST-Poll. Nur stdlib."""
import json, re, html, urllib.request, urllib.parse, datetime as dt
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
BASE = "https://travel-dealz.de/wp-json/wp/v2/"
FIELDS = "id,date,modified,link,title,categories,origins,destinations,airline,tags,meta.suspend_date"
CAT = {"economy": 1757, "business": 884, "premium": 3225, "first": 1047}
PRICE = re.compile(r"(?:ab|für|nur)\s*(\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+)\s*(€|CHF|\$)", re.I)

def get(kind, **q):
    q.setdefault("_fields", FIELDS); q.setdefault("per_page", 100)
    req = urllib.request.Request(BASE + kind + "?" + urllib.parse.urlencode(q, safe=","),
                                 headers={"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9", "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r), int(r.headers.get("X-WP-Total", 0))

def price(title):
    m = PRICE.search(title)
    return (float(m.group(1).replace(".", "").replace(",", ".")), m.group(2)) if m else None

def alive(x, now):
    s = (x.get("meta") or {}).get("suspend_date") or ""
    return not s or s.replace("T", " ")[:16] >= now.strftime("%Y-%m-%d %H:%M")

if __name__ == "__main__":
    now = dt.datetime.now()
    for kind in ("deal", "ticker"):
        rows, total = get(kind, categories="24")   # 24 = Flüge inkl. Kinder (Eco/Biz/Prem/First)
        print(kind, "total", total)
        for x in rows[:6]:
            t = html.unescape(x["title"]["rendered"])
            print(" ", x["id"], x["date"][:10], alive(x, now), price(t), t[:70])
