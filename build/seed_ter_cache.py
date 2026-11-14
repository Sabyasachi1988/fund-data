"""One-off: fill data/ter_cache.json from an already published screener bundle (rows carry ter + terd), so the first
cached run does not have to download the previous month's full expense-ratio table.
Usage: python3 build/seed_ter_cache.py screener.json.gz"""
import gzip, json, sys
import enrich

rows = json.load(gzip.open(sys.argv[1], "rt"))["rows"]
best = {}
for r in rows:
    if r.get("ter") is None or not r.get("terd"):
        continue
    k = enrich.norm(r["name"])
    if k not in best or r["terd"] >= best[k][0]:
        best[k] = (r["terd"], float(r["ter"]))
print("seeded", enrich.save_ter_cache(enrich.TER_CACHE, best), "schemes of", len(rows), "rows")
