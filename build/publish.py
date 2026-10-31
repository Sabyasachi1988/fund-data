"""Build the app-facing files from the monthly split/sector data.

Output (all under pub/, served to the app via jsDelivr/raw):
  pub/manifest.json      {"version": "...", "latest": "YYYY-MM", "funds": N, "months": N}
  pub/codes.json         {amfi_scheme_code: portfolio_id}   (every plan/option of a fund -> one id)
  pub/funds/<pid>.json   {"pid","name","amc","months":{"YYYY-MM":{"s":[large,mid,small,unclassified,foreign,debt,cash,other],"x":{sector:pct},"e":1?}}}

Inputs: data/splits + data/sectors (monthly pipeline, keyed by portfolio id) and data/targeted/ (the backfilled
months for specific funds, keyed by fund key -> mapped to portfolio id below). The monthly pipeline wins if a
month exists in both.
"""
import glob, hashlib, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_TO_PID = {"ppfas_flexi": "122639", "kotak_multicap": "149182", "nippon_multicap": "101161", "bandhan_small": "147943",
              "nippon_mid": "100375", "nippon_small": "113177", "nippon_momentum50": "152881"}


def load(p):
    return json.load(open(p))


def backfill(months, names):
    """backfill/<amc>.json (stage_all.py): {months:{YYYY-MM:{pid:{s,x}}}, names:{sector_key:{display:count}}}.
    Never overrides a month already present from the pipeline or the targeted backfill."""
    for f in sorted(glob.glob(os.path.join(ROOT, "backfill", "*.json"))):
        d = load(f)
        for k, c in d.get("names", {}).items():
            if k not in names:
                mixed = [(n, v) for n, v in c.items() if not n.isupper()] or list(c.items())
                names[k] = max(mixed, key=lambda kv: kv[1])[0].replace(" and ", " & ")
        for mk, funds in d["months"].items():
            for pid, v in funds.items():
                slot = months.setdefault(pid, {})
                if mk in slot:
                    continue
                x = {names.get(k, k): round(val, 2) for k, val in v["x"].items() if val >= 0.05}
                slot[mk] = {"s": [round(n, 2) for n in v["s"]], "x": x}


NOISE = re.compile(r"\b(index|etf|fund|fof|funds|direct|regular|growth|plan|bees|junior|idcw|option|the|of|and|exchange|traded|open|ended|scheme|tracking|passive)\b")
IDX = re.compile(r"\b((?:nifty|sensex|bse|s p|msci|nasdaq)[a-z0-9 ]*)")


def index_key(name):
    """Normalised index name for a pure index tracker (fund or ETF), else None. 'Nippon India ETF Nifty 50 BeES',
    'Navi Nifty 50 Index Fund' and 'Nippon India Index Fund - Nifty 50' all give 'nifty 50'."""
    n = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower().replace("&", " "))
    if not re.search(r"\b(index|etf|bees)\b", n):
        return None
    m = IDX.search(n)
    if not m:
        return None
    k = re.sub(r"\s+", " ", NOISE.sub(" ", m.group(1))).strip()
    return k or None


def proxy(months, cat):
    """A pure index tracker with no data for a month borrows the same month from another tracker of the SAME index
    (same constituents, so the split is the same to within tracking differences). Marked estimated ('e'), with the donor
    recorded in 'px'. Never touches a month the fund has its own data for."""
    groups = {}
    for pid in cat:
        k = index_key(cat[pid].get("name"))
        if k:
            groups.setdefault(k, []).append(pid)
    n = 0
    for k, pids in groups.items():
        donors = [p for p in pids if p in months]
        if not donors:
            continue
        for pid in pids:
            for dpid in sorted(donors, key=lambda p: -len(months[p])):
                if dpid == pid:
                    continue
                slot = months.setdefault(pid, {})
                for mk, rec in months[dpid].items():
                    if mk not in slot and not rec.get("e"):
                        slot[mk] = {"s": rec["s"], "x": rec["x"], "e": 1, "px": dpid}
                        n += 1
    print("proxied fund-months:", n)


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
    backfill(months, names)
    proxy(months, cat)

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
