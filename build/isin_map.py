"""Download AMFI's NAV file (all schemes INCLUDING exchange-traded funds) and keep ISIN -> scheme code.
Run in CI (the file is not reachable from every network). Writes data/isin_codes.json."""
import json, os, sys, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
req = urllib.request.Request("https://www.amfiindia.com/spages/NAVAll.txt", headers={"User-Agent": "Mozilla/5.0"})
txt = urllib.request.urlopen(req, timeout=120).read().decode("utf-8", "ignore")
out = {}
for line in txt.splitlines():
    p = line.split(";")
    if len(p) < 4 or not p[0].strip().isdigit():
        continue
    for isin in (p[1].strip(), p[2].strip()):
        if len(isin) == 12 and isin.startswith("IN"):
            out[isin] = p[0].strip()
if len(out) < 5000:
    sys.exit(f"suspiciously small ISIN map ({len(out)}); not writing")
json.dump(out, open(os.path.join(ROOT, "data/isin_codes.json"), "w"), separators=(",", ":"), sort_keys=True)
print("isin->code", len(out))
