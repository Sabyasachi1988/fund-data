import json,sys
sys.path.insert(0,'build')
import enrich
API=enrich.API
y=enrich.get(f"{API}/average-aum-fundwise")["data"][0]
d=enrich.get(f"{API}/average-aum-schemewise?strType=Categorywise&fyId={y['id']}&periodId=1&MF_ID=0")["data"]
print("groups",len(d)); print("group keys",list(d[0].keys()))
n=0
for g in d:
    for s in g.get("schemes",[]):
        nm=s.get("SchemeNAVName","")
        if any(k in nm.lower() for k in ("axis aggressive hybrid","dsp aggressive hybrid","uti nifty 50 index")):
            print(json.dumps({"mf":g.get("Mfname"),"nm":nm,"code":s.get("AMFI_Code"),"aum":s.get("AverageAumForTheMonth"),"keys":[k for k in s.keys()]}))
tot=sum(len(g.get("schemes",[])) for g in d)
print("total scheme rows",tot)
import collections
names=collections.Counter(enrich.plan_free(s.get("SchemeNAVName","")) for g in d for s in g.get("schemes",[]))
print("distinct plan-free keys",len(names),"rows per key dist",collections.Counter(names.values()))
