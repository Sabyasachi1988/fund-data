import json,sys,collections
sys.path.insert(0,'build')
import enrich
API=enrich.API
y=enrich.get(f"{API}/average-aum-fundwise")["data"][0]
d=enrich.get(f"{API}/average-aum-schemewise?strType=Categorywise&fyId={y['id']}&periodId=1&MF_ID=0")["data"]
rows=collections.defaultdict(list)
for g in d:
    for s in g.get("schemes",[]):
        rows[(g["Mfname"],enrich.plan_free(s["SchemeNAVName"]))].append(s["SchemeNAVName"])
single=[(k,v) for k,v in rows.items() if len(v)==1 and "direct" in v[0].lower()]
print("keys",len(rows),"single-row direct keys",len(single))
for k,v in single[:40]: print("SINGLE",k[0][:20],"|",v[0])
nodirect=[(k,v) for k,v in rows.items() if not any("direct" in x.lower() for x in v)]
print("keys with no direct row",len(nodirect))
# keys that look like fragments of one scheme: same first 25 chars but different keys
byfirst=collections.defaultdict(set)
for (mf,k) in rows: byfirst[(mf,k[:22])].add(k)
frag=[(a,b) for a,b in byfirst.items() if len(b)>1]
print("possible split keys",len(frag))
for a,b in frag[:25]: print("SPLIT",a[0][:18],sorted(b))
