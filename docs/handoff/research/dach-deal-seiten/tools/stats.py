import sys, re, html, xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
FL = re.compile(r'(flug|flüge|fluege|fliegen|airline|airways|business class|economy|premium|first class|oneway|gabelfl|nonstop|roundtrip|return|r/t|o/w|\bin c\b|\by\+|lufthansa|swiss|condor|eurowings|ryanair|etihad|qatar|emirates|air |[A-Z]{3}-[A-Z]{3})', re.I)
EXCL = re.compile(r'(kreuzfahrt|hotel|pauschal|all.?inclusive|nächte|kreditkarte|status|punkte|meilen kaufen|abo|lounge|bewertung|review|gutschein)', re.I)
PRICE = re.compile(r'(\d[\d.]*\s?(€|eur|euro|chf|usd|\$|gbp)|ab \d)', re.I)
def strip(s): return html.unescape(re.sub(r'<[^>]+>','',s or '')).strip()
for path in sys.argv[1:]:
    data=open(path,'rb').read()
    try: root=ET.fromstring(data)
    except Exception as e: print(path,'parse-error'); continue
    items=[]
    ns={'a':'http://www.w3.org/2005/Atom'}
    if root.tag.endswith('feed'):
        for e in root.findall('a:entry',ns):
            items.append((strip(e.findtext('a:title',default='',namespaces=ns)), e.findtext('a:updated',default='',namespaces=ns) or e.findtext('a:published',default='',namespaces=ns)))
    else:
        for it in root.iter('item'):
            items.append((strip(it.findtext('title',default='')), it.findtext('pubDate',default='')))
    n=len(items)
    fl=sum(1 for t,_ in items if FL.search(t) and not EXCL.search(t))
    pr=sum(1 for t,_ in items if PRICE.search(t))
    dates=[d for _,d in items if d]
    print(f"{path.split('/')[-1]:<40} items={n:<4} flightish={fl:<3} price_in_title={pr:<3} newest={dates[0][:25] if dates else '-':<26} oldest={dates[-1][:25] if dates else '-'}")
