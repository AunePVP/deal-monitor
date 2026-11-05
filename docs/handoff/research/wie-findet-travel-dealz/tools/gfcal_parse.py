import json,sys
for f in sys.argv[1:]:
    t=open(f,encoding='utf-8').read()
    t=t[t.index('\n')+1:] if t.startswith(")]}'") else t
    outer=json.loads(t.strip())
    for blk in outer:
        if blk[0]=='wrb.fr' and blk[2]:
            data=json.loads(blk[2])
            rows=data[-1] or []
            pr=[(r[0],r[1],r[2][0][1]) for r in rows if r[2] and r[2][0]]
            print(f, 'Tage mit Preis:',len(pr), 'min:',min(pr,key=lambda x:x[2]) if pr else None)
            print('  ',pr[:6],'...',pr[-3:])
