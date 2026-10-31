"""Build the app-facing files from the monthly split/sector data.

Output (all under pub/, served to the app via jsDelivr/raw):
  pub/manifest.json      {"version": "...", "latest": "YYYY-MM", "funds": N, "months": N}
  pub/codes.json         {amfi_scheme_code: portfolio_id}   (every plan/option of a fund -> one id)
  pub/funds/<pid>.json   {"pid","name","amc","months":{"YYYY-MM":{"s":[large,mid,small,unclassified,foreign,debt,cash,other],"x":{sector:pct},"e":1?}}}

Inputs: data/splits + data/sectors (monthly pipeline, keyed by portfolio id) and data/targeted/ (the backfilled
months for specific funds, keyed by fund key -> mapped to portfolio id below). The monthly pipeline wins if a
month exists in both.
"""
import glob, hashlib, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_TO_PID = {"ppfas_flexi": "122639", "kotak_multicap": "149182", "nippon_multicap": "101161", "bandhan_small": "147943",
              "nippon_mid": "100375", "nippon_small": "113177", "nippon_momentum50": "152881"}


def load(p):
    return json.load(open(p))


def main():
    cat = load(os.path.join(ROOT, "data/catalog.json"))
    names = load(os.path.join(ROOT, "data/sector_names.json"))
    months = {}  # pid -> month -> {s, x, e}

    def ingest(base, mapper, source):
        for f in sorted(glob.glob(os.path.join(ROOT, base, "splits", "*.json"))):
            mk = os.path.basename(f)[:-5]
            d = load(f)
            est = d.get("estimated", {})
            sec = {}
            sp = os.path.join(ROOT, base, "sectors", mk + ".json")
            if os.path.exists(sp):
                sec = load(sp).get("funds", {})
            for k, v in d["funds"].items():
                pid = mapper(k)
                if not pid:
                    continue
                slot = months.setdefault(pid, {})
                if mk in slot and source == "targeted":
                    continue  # pipeline wins
                x = {}
                for sk, val in (sec.get(k) or {}).items():
                    if val >= 0.05:
                        x[names.get(sk, sk)] = round(val, 2)
                rec = {"s": [round(n, 2) for n in v], "x": x}
                if k in est:
                    rec["e"] = 1
                slot[mk] = rec

    ingest("data", lambda k: k, "pipeline")
    ingest("data/targeted", lambda k: KEY_TO_PID.get(k), "targeted")

    out = os.path.join(ROOT, "pub")
    os.makedirs(os.path.join(out, "funds"), exist_ok=True)
    h = hashlib.sha1()
    latest = ""
    for pid, ms in sorted(months.items()):
        c = cat.get(pid, {})
        body = {"pid": pid, "name": c.get("name", pid), "amc": c.get("amc", ""), "months": dict(sorted(ms.items()))}
        s = json.dumps(body, separators=(",", ":"), sort_keys=True)
        open(os.path.join(out, "funds", pid + ".json"), "w").write(s)
        h.update(s.encode())
        latest = max(latest, max(ms))
    codes = {}
    for pid, c in cat.items():
        if pid in months:
            for m in c.get("members", []):
                codes[str(m)] = pid
    open(os.path.join(out, "codes.json"), "w").write(json.dumps(codes, separators=(",", ":"), sort_keys=True))
    allm = sorted({m for ms in months.values() for m in ms})
    man = {"version": f"{latest}.{h.hexdigest()[:8]}", "latest": latest, "funds": len(months), "months": len(allm)}
    open(os.path.join(out, "manifest.json"), "w").write(json.dumps(man))
    print(man, "codes", len(codes))


if __name__ == "__main__":
    main()
