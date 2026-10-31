"""Collect the target funds' holdings from toolkit-parsed portfolios into the archive layout.

Usage: stage_target.py <parsed_root> <out_root>
Reads <parsed_root>/monthly/<YYYY-MM-DD>/<amc>/<scheme>/portfolio.json, keeps only schemes matching TARGETS,
writes <out_root>/asof/<YYYY-MM-DD>/<fund_key>.json in the same shape compute.py reads
(portfolio_id, scheme, meta, holdings with holding_type / isin / industry / pct_nav).
"""
import glob, json, os, re, sys

TARGETS = [
    ("ppfas_flexi", r"parag parikh flexi"),
    ("kotak_multicap", r"kotak multi ?cap"),
    ("nippon_multicap", r"nippon india multi ?cap"),
    ("bandhan_small", r"bandhan small ?cap"),
    ("nippon_mid", r"nippon india (growth|mid ?cap)"),
    ("nippon_small", r"nippon india small ?cap"),
    ("nippon_momentum50", r"nippon india nifty ?500 momentum"),
]
CASH = re.compile(r"treps|tri-?party|reverse repo|net current|net receivable|receivable|payable|cash|margin|cblo", re.I)
RATING = re.compile(r"\b(AAA|AA|A1|SOV|ICRA|CRISIL|CARE|FITCH|IND\s?A|UNRATED)\b", re.I)


def fnum(x):
    if x is None:
        return None
    s = str(x).replace(",", "").replace("%", "").strip()
    if s in ("", "-", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def htype(r):
    isin = (r.get("isin") or "").strip().upper()
    inst = r.get("instrument") or ""
    if r.get("position_side") or re.search(r"future|option|derivative", (r.get("section") or "") + inst, re.I):
        return "derivative"
    if r.get("coupon") or r.get("maturity_date") or r.get("rating") or RATING.search(r.get("industry") or ""):
        return "debt"
    if not isin or not isin.startswith(("IN", "US", "GB", "IE", "KY", "SG", "NL", "JP", "DE", "CH")):
        return "cash" if CASH.search(inst) else "other"
    if isin.startswith("INF"):
        return "fund_unit"
    if isin.startswith("INE") and isin[8:10] in ("01", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "23"):
        return "equity"
    if isin.startswith("IN"):
        return "debt" if isin[8:10] in ("07", "08", "09") else "equity"
    return "equity"  # foreign-listed: bucketed as foreign by compute.py (non-IN isin)


REF_KEYS = ("nippon_multicap", "nippon_mid")


def isin_set(d):
    return {(r.get("isin") or "").strip() for r in d["holdings"] if (r.get("isin") or "").strip().startswith("INE")}


def best_ref(d, out):
    """Nameless Nippon sheets: match by equity-ISIN overlap (Jaccard) with the last known holdings of each target."""
    s = isin_set(d)
    best = (None, 0.0)
    for k in REF_KEYS:
        fp = os.path.join(out, "refs", k + ".json")
        if not os.path.exists(fp) or not s:
            continue
        ref = set(json.load(open(fp)))
        j = len(s & ref) / max(1, len(s | ref))
        if j > best[1]:
            best = (k, j)
    return best


def write(key, d, f, out, name):
    m = d["meta"]
    rows = []
    for r in d["holdings"]:
        p = fnum(r.get("pct_nav"))
        if p is None:
            continue
        rows.append({"holding_type": htype(r), "instrument": r.get("instrument"), "isin": (r.get("isin") or "").strip() or None,
                     "industry": r.get("industry") or None, "pct_nav": p})
    tot = sum(abs(x["pct_nav"]) for x in rows)
    if 0 < tot < 1.6:
        for x in rows:
            x["pct_nav"] *= 100
    asof = (m.get("as_of") or "")[:10] or f.split("/monthly/")[1][:10]
    dst = os.path.join(out, "asof", asof)
    os.makedirs(dst, exist_ok=True)
    json.dump({"portfolio_id": key, "member_amfi_codes": [], "scheme": {"parent_name": name, "amc_name": m.get("amc_id")},
               "meta": {"as_of": asof, "source_file": m.get("source_file")}, "holdings": rows},
              open(os.path.join(dst, f"{key}.json"), "w"), separators=(",", ":"))
    if key in REF_KEYS:
        os.makedirs(os.path.join(out, "refs"), exist_ok=True)
        json.dump(sorted(isin_set(d)), open(os.path.join(out, "refs", key + ".json"), "w"))
    print(f"staged {key} {asof} <- {' '.join(name.split())[:60]} ({len(rows)} rows, sum {sum(x['pct_nav'] for x in rows):.1f})")


def main(parsed, out):
    n = 0
    cand = {}
    for f in glob.glob(os.path.join(parsed, "monthly", "*", "*", "*", "portfolio.json")):
        d = json.load(open(f))
        name = d["meta"].get("scheme_name") or ""
        os.makedirs(os.path.join(out, "names"), exist_ok=True)
        open(os.path.join(out, "names", f.split("/monthly/")[1][:7] + ".txt"), "a").write(
            str(d["meta"].get("amc_id"))[:6] + " | " + " ".join(name.split())[:70] + " | " + str(len(d["holdings"])) + "\n")
        if name.startswith("NIMF-MONTHLY"):
            k, j = best_ref(d, out)
            if k and j >= 0.5 and (k not in cand or j > cand[k][0]):
                cand[k] = (j, f, d, name)
            continue
        excl = re.search(r"index|etf|nifty ?(mid|small|next)|large|fof|fund of", name, re.I) and not re.search(r"momentum", name, re.I)
        key = None if excl else next((k for k, rx in TARGETS if re.search(rx, name, re.I) and k not in REF_KEYS), None)
        if key:
            write(key, d, f, out, name)
            n += 1
    for k, (j, f, d, name) in cand.items():
        print(f"ref-match {k} jaccard {j:.2f} rows {len(d['holdings'])}")
        write(k, d, f, out, name)
        n += 1
    print("staged", n)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
