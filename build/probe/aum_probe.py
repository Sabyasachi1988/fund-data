import json,sys,re
sys.path.insert(0,'build')
import enrich
API=enrich.API
fy=enrich.get(f"{API}/average-aum-fundwise")
years=(fy or {}).get("data") or []
print("years",[(y.get('id'),y.get('financial_year')) for y in years[:3]])
for y in years[:1]:
    per=enrich.get(f"{API}/average-aum-fundwise?fyId={y['id']}")
    ps=((per or {}).get("data") or {}).get("periods",[])
    print("periods",ps)
    for p in ps[:2]:
        d=enrich.get(f"{API}/average-aum-schemewise?strType=Categorywise&fyId={y['id']}&periodId={p['id']}&MF_ID=0")
        groups=(d or {}).get("data") or []
        print("period",p, "groups",len(groups))
        for g in groups:
            for s in g.get("schemes",[]):
                if "flexi" in s.get("SchemeNAVName","").lower() and "hdfc" in g.get("Mfname","").lower():
                    print(json.dumps({"mf":g.get("Mfname"),"cat":g.get("CategoryName") or g.get("Category"),"s":s}))
