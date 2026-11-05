import re,html,sys
for f in sys.argv[1:]:
    t=open(f,encoding='utf-8',errors='replace').read()
    s=re.sub(r'<script.*?</script>|<style.*?</style>','',t,flags=re.S)
    txt=html.unescape(re.sub(r'<[^>]+>','\n',s))
    lines=[l.strip() for l in txt.split('\n') if l.strip()]
    title=re.search(r'<title>([^<]*)',t)
    print('=====',f,title and title.group(1), len(lines))
    for l in lines:
        if re.search(r'kostet aktuell|Übliche Preise|beginnen bei|Normalpreis', l): print('  ',l[:220])
    # popular dest cards: name, price, dates
    for i,l in enumerate(lines):
        if re.fullmatch(r'[\d.]+ €',l) and i+1<len(lines) and re.search(r'\d+\. \w+\.? – \d+\. \w+',lines[i+1]):
            print('   CARD',lines[i-1],l,lines[i+1],lines[i+2])
