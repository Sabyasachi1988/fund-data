import json,sys,urllib.request
u="https://www.amfiindia.com/api/average-aum-schemewise?strType=Categorywise&fyId=1&periodId=1&MF_ID=0"
raw=urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"}),timeout=120).read()
print("bytes",len(raw)); d=json.loads(raw)
print(type(d).__name__, list(d.keys()) if isinstance(d,dict) else len(d))
dd=d.get("data") if isinstance(d,dict) else d
print(type(dd).__name__, len(dd) if dd is not None else None)
if isinstance(dd,list) and dd:
    print(json.dumps(dd[0])[:700]); print(json.dumps(dd[len(dd)//2])[:700])
