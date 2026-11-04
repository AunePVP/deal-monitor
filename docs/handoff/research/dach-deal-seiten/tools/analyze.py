#!/usr/bin/env python3
"""Analyze a saved feed body: detect format, count items, print titles + dates.
Usage: python3 -I analyze.py <bodyfile> [max_titles]
"""
import sys, re, json, html
import xml.etree.ElementTree as ET

path = sys.argv[1]
maxt = int(sys.argv[2]) if len(sys.argv) > 2 else 12
data = open(path, 'rb').read()
head = data[:400].decode('utf-8', 'replace').lower()

def strip(s):
    return html.unescape(re.sub(r'<[^>]+>', '', s or '')).strip()

def show(fmt, items):
    print(f"FORMAT={fmt} ITEMS={len(items)}")
    for i, (t, d, l) in enumerate(items[:maxt]):
        print(f"  [{i+1}] {d[:31]:<31} | {t[:110]} | {l[:90]}")

if b'<rss' in data[:2000] or b'<feed' in data[:2000] or b'<rdf:RDF' in data[:2000]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        print("FORMAT=XML(parse-error)", e)
        sys.exit(0)
    ns = {'a': 'http://www.w3.org/2005/Atom', 'dc': 'http://purl.org/dc/elements/1.1/'}
    items = []
    if root.tag.endswith('feed'):
        for e in root.findall('a:entry', ns):
            t = strip(e.findtext('a:title', default='', namespaces=ns))
            d = e.findtext('a:updated', default='', namespaces=ns) or e.findtext('a:published', default='', namespaces=ns)
            l = ''
            ln = e.find('a:link', ns)
            if ln is not None:
                l = ln.get('href', '')
            items.append((t, d, l))
        show('ATOM', items)
    else:
        for it in root.iter('item'):
            t = strip(it.findtext('title', default=''))
            d = it.findtext('pubDate', default='') or it.findtext('{http://purl.org/dc/elements/1.1/}date', default='')
            l = (it.findtext('link') or '').strip()
            items.append((t, d, l))
        show('RSS', items)
elif head.lstrip().startswith('{') or head.lstrip().startswith('['):
    try:
        j = json.loads(data)
    except Exception as e:
        print("FORMAT=JSON(parse-error)", e)
        sys.exit(0)
    items = []
    if isinstance(j, dict) and 'items' in j:  # JSON Feed
        for it in j['items']:
            items.append((it.get('title', ''), it.get('date_published', '') or it.get('date_modified', ''), it.get('url', '')))
        show('JSONFEED', items)
    elif isinstance(j, list):
        for it in j:
            if isinstance(it, dict):
                t = it.get('title', {})
                t = t.get('rendered') if isinstance(t, dict) else t
                items.append((strip(str(t)), str(it.get('date', '') or it.get('date_gmt', '')), str(it.get('link', ''))))
        show('JSON', items)
    else:
        print("FORMAT=JSON keys=", list(j.keys())[:20] if isinstance(j, dict) else type(j))
else:
    txt = data.decode('utf-8', 'replace')
    title = re.search(r'<title[^>]*>(.*?)</title>', txt, re.S | re.I)
    print("FORMAT=HTML title=", strip(title.group(1))[:100] if title else '-', "len=", len(data))
    # discover feed links
    for m in re.finditer(r'<link[^>]+(?:application/(?:rss|atom)\+xml|application/feed\+json)[^>]*>', txt, re.I):
        print("  feedlink:", m.group(0)[:200])
    cats = sorted(set(re.findall(r'href="(https?://[^"]+/(?:category|kategorie|tag|thema|themen|topics?)/[^"]+)"', txt)))
    for c in cats[:60]:
        print("  cat:", c)
