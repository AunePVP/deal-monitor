# usage: python3 -I an.py <file> [n]  -> feed analysis: items, newest/oldest date, price-in-title share, DACH share, flight share
import sys, re, xml.etree.ElementTree as ET, email.utils, datetime as dt
p=sys.argv[1]; n=int(sys.argv[2]) if len(sys.argv)>2 else 10
raw=open(p,'rb').read()
try: root=ET.fromstring(raw)
except Exception as e: print("PARSE ERROR",e, raw[:300]); sys.exit()
A='{http://www.w3.org/2005/Atom}'
items=root.findall('.//item'); atom=False
if not items: items=root.findall(f'.//{A}entry'); atom=True
rows=[]
for it in items:
    if atom:
        t=(it.findtext(A+'title') or '').strip(); d=it.findtext(A+'published') or it.findtext(A+'updated') or ''
        l=it.find(A+'link'); l=l.get('href') if l is not None else ''
        cats=[c.get('term') or '' for c in it.findall(A+'category')]
        try: ts=dt.datetime.fromisoformat(d.replace('Z','+00:00'))
        except: ts=None
    else:
        t=(it.findtext('title') or '').strip(); d=it.findtext('pubDate') or it.findtext('{http://purl.org/dc/elements/1.1/}date') or ''
        l=(it.findtext('link') or '').strip(); cats=[c.text or '' for c in it.findall('category')]
        try: ts=email.utils.parsedate_to_datetime(d)
        except:
            try: ts=dt.datetime.fromisoformat(d.replace('Z','+00:00'))
            except: ts=None
    rows.append((ts,t,l,cats))
PRICE=re.compile(r'(€|EUR|\$|USD|£|GBP|CHF|zł|PLN|kr\b|SEK|NOK|DKK)\s?\d|\d[\d.,]*\s?(€|EUR|\$|USD|£|GBP|CHF|zł|PLN|SEK|NOK|DKK|Euro)',re.I)
DACH=re.compile(r'\b(German|Deutschland|Germany|Frankfurt|Munich|München|Muenchen|Berlin|Hamburg|Düsseldorf|Dusseldorf|Duesseldorf|Cologne|Köln|Koeln|Stuttgart|Hannover|Hanover|Nuremberg|Nürnberg|Bremen|Leipzig|Dresden|Dortmund|Zurich|Zürich|Zuerich|Geneva|Genf|Basel|Switzerland|Schweiz|Swiss|Vienna|Wien|Austria|Österreich|Salzburg|Innsbruck|Graz|Linz|FRA|MUC|BER|HAM|DUS|ZRH|GVA|VIE|BSL|STR|CGN)\b')
FL=re.compile(r'(flight|fl[üu]g|fly|fares?|round.?trip|one.?way|business class|economy|premium economy|first class|nonstop|return|\bto\b.*\bfrom\b|→|->|✈|Hin.*R[üu]ck|Gabelflug)',re.I)
N=len(rows); print("ITEMS",N,"atom" if atom else "rss")
if not N: sys.exit()
tss=[r[0] for r in rows if r[0]]
if tss: print("NEWEST",max(tss).isoformat(),"OLDEST",min(tss).isoformat())
pr=sum(1 for r in rows if PRICE.search(r[1])); da=sum(1 for r in rows if DACH.search(r[1])); fl=sum(1 for r in rows if FL.search(r[1]))
print(f"PRICE_IN_TITLE {pr}/{N}  DACH_IN_TITLE {da}/{N}  FLIGHTLIKE {fl}/{N}")
for r in rows[:n]: print("-",(r[0].strftime('%Y-%m-%d %H:%M') if r[0] else '?'),"|",r[1][:150],"|",",".join(r[3][:3])[:60])
dl=[r for r in rows if DACH.search(r[1])]
if dl: print("DACH examples:"); [print("  *",r[1][:150]) for r in dl[:6]]
