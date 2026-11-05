import sys, re, xml.etree.ElementTree as ET, json
p = sys.argv[1]; n = int(sys.argv[2]) if len(sys.argv) > 2 else 15
raw = open(p, 'rb').read()
try:
    root = ET.fromstring(raw)
except Exception as e:
    print("PARSE ERROR:", e); print(raw[:600]); sys.exit(0)
ns = {'a': 'http://www.w3.org/2005/Atom', 'dc': 'http://purl.org/dc/elements/1.1/'}
items = root.findall('.//item')
atom = False
if not items:
    items = root.findall('.//a:entry', ns); atom = True
print("ITEMS:", len(items), "(atom)" if atom else "(rss)")
for it in items[:n]:
    if atom:
        t = (it.findtext('a:title', default='', namespaces=ns) or '').strip()
        d = it.findtext('a:published', default='', namespaces=ns) or it.findtext('a:updated', default='', namespaces=ns)
        l = it.find('a:link', ns); l = l.get('href') if l is not None else ''
        cats = [c.get('term') for c in it.findall('a:category', ns)]
    else:
        t = (it.findtext('title') or '').strip()
        d = it.findtext('pubDate') or it.findtext('dc:date', namespaces=ns) or ''
        l = (it.findtext('link') or '').strip()
        cats = [c.text for c in it.findall('category')]
    print(f"- [{d[:25]}] {t[:140]}  <{l[:90]}> cats={cats[:4]}")
