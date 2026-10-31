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


def main(parsed, out):
    n = 0
    for f in glob.glob(os.path.join(parsed, "monthly", "*", "*", "*", "portfolio.json")):
        d = json.load(open(f))
        m = d["meta"]
        name = m.get("scheme_name") or ""
        if True:
            os.makedirs(os.path.join(out, "names"), exist_ok=True)
            open(os.path.join(out, "names", f.split("/monthly/")[1][:7] + ".txt"), "a").write(str(m.get("amc_id"))[:6] + " | " + " ".join(name.split())[:70] + " | " + str(len(d["holdings"])) + "\n")
        key = None if re.search(r"index|etf|nifty ?(mid|small|next)|large|fof|fund of", name, re.I) and not re.search(r"momentum", name, re.I) else next((k for k, rx in TARGETS if re.search(rx, name, re.I)), None)
        if not key:
            continue
        rows = []
        for r in d["holdings"]:
            p = fnum(r.get("pct_nav"))
            if p is None:
                continue
            rows.append({"holding_type": htype(r), "instrument": r.get("instrument"), "isin": (r.get("isin") or "").strip() or None,
                         "industry": r.get("industry") or None, "pct_nav": p})
        tot = sum(abs(x["pct_nav"]) for x in rows)
        if 0 < tot < 1.6:  # stored as fractions
            for x in rows:
                x["pct_nav"] *= 100
        asof = (m.get("as_of") or "")[:10] or f.split("/monthly/")[1][:10]
        dst = os.path.join(out, "asof", asof)
        os.makedirs(dst, exist_ok=True)
        json.dump({"portfolio_id": key, "member_amfi_codes": [], "scheme": {"parent_name": name, "amc_name": m.get("amc_id")},
                   "meta": {"as_of": asof, "source_file": m.get("source_file")}, "holdings": rows},
                  open(os.path.join(dst, f"{key}.json"), "w"), separators=(",", ":"))
        n += 1
        print(f"staged {key} {asof} <- {name} ({len(rows)} rows, sum {sum(x['pct_nav'] for x in rows):.1f})")
    print("staged", n)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
