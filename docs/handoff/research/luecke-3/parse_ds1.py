import re,json,sys
t=open(sys.argv[1],encoding='utf8').read()
m=re.search(r"key: 'ds:1',(?: hash: '\d+',)? data:(.*?), sideChannel",t,re.S)
d=json.loads(m.group(1))
if d[0]==13: print(sys.argv[1][-14:],'ERR13');sys.exit()
b=d[5]; offs=d[3][0] if d[3] else []
pr=[]
for o in offs:
    try: pr.append(o[1][0][1])
    except Exception: pass
print(sys.argv[1][-14:],'lvl',b[0],'min',b[1][1],'typ',b[2][1],'range',b[4][1],b[5][1],'offers',len(offs),'offer-prices',sorted(pr)[:3])
