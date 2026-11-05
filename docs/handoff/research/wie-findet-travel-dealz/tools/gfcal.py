# Baut f.req fuer GetCalendarGraph (Preis je Abflugtag). usage: gfcal.py ORIG DEST FROM TO TRIP(1=rt,2=ow) SEAT(1 eco,3 biz) [DURATION]
import json,sys,urllib.parse
o,d,fr,to,trip,seat=sys.argv[1:7]; dur=int(sys.argv[7]) if len(sys.argv)>7 else 7
def seg(a,b,date): return [[[[a,0]]],[[[b,0]]],None,0,None,None,date,None,None,None,None,None,None,None,3]
segs=[seg(o,d,fr)]
if trip=='1': segs.append(seg(d,o,fr))
f=[None,[None,None,int(trip),None,[],int(seat),[1,0,0,0],None,None,None,None,None,None,segs,None,None,None,1],[fr,to]]
if trip=='1': f+= [None,[dur,dur]]
print("f.req="+urllib.parse.quote(json.dumps([None,json.dumps(f,separators=(',',':'))],separators=(',',':'))))
