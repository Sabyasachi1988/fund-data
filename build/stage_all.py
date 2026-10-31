"""Backfill step for ONE AMC and ONE month: turn every parsed scheme into a cap/sector split and attach it to a
fund-data portfolio id, with no per-fund configuration.

Matching: a scheme is matched to the fund (portfolio id) whose holdings it most resembles. Resemblance is the Jaccard overlap of
ISIN sets against the fund's most recently matched month (seeded from the open archive at Jun 2026, then chained backwards one
month at a time, so normal portfolio turnover never breaks the chain). Matching is one-to-one (greedy by score) and restricted
to the same AMC when possible. A small bonus for name similarity breaks ties between funds that hold identical stocks
(e.g. an index fund and its ETF).

Usage: stage_all.py seed <archive_asof_dir> <out_dir>      -> out_dir/refs.json
       stage_all.py month <parsed_root> <out_dir> <amc_id>  -> adds to out_dir/<amc_id>.json, updates refs.json
"""
import collections, glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import compute  # noqa: E402
import stage_target as st  # noqa: E402

ROOT = os.path.dirname(HERE)
MIN_SCORE = 0.5


def alnum(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower().replace("mutual fund", "").replace("mutual-fund", ""))


def toks(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower())) - {"fund", "the", "of", "and", "an", "open", "ended", "scheme"}


def seed(arch, out):
    refs = {}
    for f in glob.glob(os.path.join(arch, "*.json")):
        d = json.load(open(f))
        refs[str(d["portfolio_id"])] = sorted({(h.get("isin") or "").strip() for h in d["holdings"] if (h.get("isin") or "").strip()})
    os.makedirs(out, exist_ok=True)
    json.dump(refs, open(os.path.join(out, "refs.json"), "w"), separators=(",", ":"))
    print("seeded", len(refs))


def month(parsed, out, amc):
    cat = json.load(open(os.path.join(ROOT, "data/catalog.json")))
    refs = json.load(open(os.path.join(out, "refs.json")))
    mine = [p for p, c in cat.items() if alnum(c.get("amc")) == alnum(amc) and p in refs]
    pool = mine or list(refs)
    changes = compute.load_changes()
    caps = {}
    schemes = []
    for f in glob.glob(os.path.join(parsed, "monthly", "*", "*", "*", "portfolio.json")):
        d = json.load(open(f))
        m = d["meta"]
        asof = (m.get("as_of") or "")[:10] or f.split("/monthly/")[1][:10]
        key = compute.list_key_for(asof)
        if key not in caps:
            caps[key] = compute.load_amfi(key, changes)
        cap = caps[key]
        if cap is None:
            continue
        rows, isins = [], set()
        for r in d["holdings"]:
            p = st.fnum(r.get("pct_nav"))
            if p is None:
                continue
            row = {"holding_type": st.htype(r), "instrument": r.get("instrument"), "isin": (r.get("isin") or "").strip() or None,
                   "industry": r.get("industry") or None, "pct_nav": p}
            rows.append(row)
            if row["isin"]:
                isins.add(row["isin"])
        tot = sum(abs(x["pct_nav"]) for x in rows)
        if 0 < tot < 1.6:
            for x in rows:
                x["pct_nav"] *= 100
            tot *= 100
        if not rows or not isins or not (50 < tot < 130):
            continue
        b = dict.fromkeys(compute.BUCKETS, 0.0)
        sec = collections.defaultdict(float)
        names = collections.defaultdict(collections.Counter)
        for h in rows:
            bucket, s = compute.classify(h, cap)
            if bucket is None:
                continue
            b[bucket] += h["pct_nav"]
            if s and bucket in ("large", "mid", "small", "unclassified"):
                sec[s] += h["pct_nav"]
                if h.get("industry"):
                    names[s][h["industry"]] += 1
        schemes.append({"asof": asof, "name": m.get("scheme_name") or "", "isins": isins,
                        "s": [round(b[k], 2) for k in compute.BUCKETS],
                        "x": {k: round(v, 2) for k, v in sec.items() if round(v, 2) != 0}, "names": names})
    if not schemes:
        print("no usable schemes")
        return
    pairs = []
    for i, s in enumerate(schemes):
        for p in pool:
            r = set(refs[p])
            inter = len(s["isins"] & r)
            if not inter:
                continue
            j = inter / len(s["isins"] | r)
            if j < MIN_SCORE:
                continue
            nm = toks(s["name"]) & toks(cat.get(p, {}).get("name"))
            bonus = 0.02 * len(nm) / max(1, len(toks(cat.get(p, {}).get("name"))))
            pairs.append((j + bonus, i, p))
    pairs.sort(reverse=True)
    used_s, used_p, got = set(), set(), {}
    for sc, i, p in pairs:
        if i in used_s or p in used_p:
            continue
        used_s.add(i)
        used_p.add(p)
        got[p] = (i, sc)
    path = os.path.join(out, amc + ".json")
    data = json.load(open(path)) if os.path.exists(path) else {"months": {}, "names": {}, "stats": {}}
    for p, (i, sc) in got.items():
        s = schemes[i]
        mk = s["asof"][:7]
        data["months"].setdefault(mk, {})[p] = {"s": s["s"], "x": s["x"]}
        refs[p] = sorted(s["isins"])
        for k, c in s["names"].items():
            for n, v in c.items():
                data["names"].setdefault(k, {}).setdefault(n, 0)
                data["names"][k][n] += v
    mk = schemes[0]["asof"][:7]
    data["stats"][mk] = {"schemes": len(schemes), "matched": len(got)}
    json.dump(data, open(path, "w"), separators=(",", ":"))
    json.dump(refs, open(os.path.join(out, "refs.json"), "w"), separators=(",", ":"))
    print(f"{amc} {mk}: schemes {len(schemes)} matched {len(got)}")


if __name__ == "__main__":
    if sys.argv[1] == "seed":
        seed(sys.argv[2], sys.argv[3])
    else:
        month(sys.argv[2], sys.argv[3], sys.argv[4])
