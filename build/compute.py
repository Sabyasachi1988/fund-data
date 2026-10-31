"""Turn parsed monthly fund holdings into per-fund cap and sector splits.

Input : an archive folder with asof/<YYYY-MM-DD>/<portfolio_id>.json (one JSON per fund portfolio,
        holdings with isin, holding_type, industry, pct_nav) - the open fund-holdings-data layout.
Output: data/splits/<YYYY-MM>.json   per fund: [large, mid, small, unclassified_equity, foreign, debt, cash, other] in % of NAV
        data/sectors/<YYYY-MM>.json  per fund: {sector_key: % of NAV}
        data/catalog.json            per fund: name, AMC, member AMFI scheme codes
        data/sector_names.json       sector_key -> display name

Cap tag of a stock = AMFI list in force that month (Jan-Jun uses the list for the six months ended the
previous 31 Dec; Jul-Dec uses the list for the six months ended 30 Jun of the same year).
"""
import glob, json, os, re, sys, collections
import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RATING = re.compile(r"\b(AAA|AA|A1|A2|SOV|ICRA|CRISIL|CARE|FITCH|IND\s?A|BWR|IVR|UNRATED)\b", re.I)
BUCKETS = ["large", "mid", "small", "unclassified", "foreign", "debt", "cash", "other"]


def list_key_for(month_end: str) -> str:
    y, m = int(month_end[:4]), int(month_end[5:7])
    return f"{y}-06" if m >= 7 else f"{y-1}-12"


def load_amfi(key: str, changes: dict):
    path = os.path.join(ROOT, "amfi", key + ".xlsx")
    if not os.path.exists(path):
        return None
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    cap = {}
    for row in ws.iter_rows(values_only=True):
        isin, cat = row[2], row[10]
        if not isin or not cat or not str(isin).startswith("IN"):
            continue
        c = str(cat).strip().lower()
        tag = "large" if c.startswith("large") else "mid" if c.startswith("mid") else "small" if c.startswith("small") else None
        if tag:
            cap[str(isin).strip()] = tag
        new = row[11]
        if tag and new and str(new).startswith("IN"):
            cap.setdefault(str(new).strip(), tag)
    for old, new in changes.items():
        if old in cap and new not in cap:
            cap[new] = cap[old]
        elif new in cap and old not in cap:
            cap[old] = cap[new]
    return cap


def load_changes():
    out = {}
    p = os.path.join(ROOT, "amfi", "isin_changes.csv")
    if os.path.exists(p):
        import csv
        for r in csv.DictReader(open(p, encoding="utf-8-sig")):
            o, n = (r.get("old") or "").strip(), (r.get("new") or "").strip()
            if o and n:
                out[o] = n
    return out


def norm_sector(s: str) -> str:
    s = s.lower().replace("&", " and ").replace("-", " ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(and|the|of|etc)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def classify(h, cap):
    """Return (bucket, sector_key_or_None) for one holding."""
    t = h.get("holding_type")
    pct = h.get("pct_nav")
    isin = (h.get("isin") or "").strip()
    if pct is None:
        return None, None
    # The upstream parser types some holdings from their section/name rather than what they are. Trust the ISIN:
    # an INE... line with an industry is a listed Indian company whatever it was typed as (e.g. MCX typed "commodity",
    # an AMC's own shares typed "fund_unit"); a foreign-ISIN fund unit or share is foreign exposure, not "other".
    if t in ("commodity", "fund_unit", "other") and isin.startswith("INE") and (h.get("industry") or "").strip():
        t = "equity"
    elif t == "fund_unit" and isin and not isin.startswith("IN"):
        return "foreign", None
    elif t == "fund_unit" and re.search(r"corporate debt market", (h.get("instrument") or ""), re.I):
        return "debt", None
    if t == "equity":
        ind = h.get("industry") or ""
        if RATING.search(ind):
            return "debt", None
        if isin.startswith("IN"):
            tag = cap.get(isin)
            sec = norm_sector(ind) if ind else None
            return (tag or "unclassified"), sec
        if isin:
            return "foreign", None
        return "unclassified", norm_sector(ind) if ind else None
    if t in ("debt", "money_market"):
        return "debt", None
    if t == "cash":
        return "cash", None
    if t == "fund_unit":
        return ("other", None) if isin else (None, None)
    if t in ("derivative",):
        return None, None  # hedges: not additive to NAV mix
    return "other", None


def run(archive: str, out_root: str, only_months=None):
    changes = load_changes()
    lists = {}
    names = collections.defaultdict(collections.Counter)
    catalog = {}
    for d in sorted(glob.glob(os.path.join(archive, "asof", "*"))):
        asof = os.path.basename(d)
        month = asof[:7]
        if only_months and month not in only_months:
            continue
        files = glob.glob(os.path.join(d, "*.json"))
        if not files:
            continue
        # fortnightly files land mid-month; the month-end file wins
        if not (asof[8:] in ("28", "29", "30", "31")):
            continue
        key = list_key_for(asof)
        if key not in lists:
            lists[key] = load_amfi(key, changes)
        cap = lists[key]
        if cap is None:
            print(f"skip {asof}: no AMFI list {key}", file=sys.stderr)
            continue
        splits, sectors = {}, {}
        for f in files:
            p = json.load(open(f))
            pid = str(p["portfolio_id"])
            b = dict.fromkeys(BUCKETS, 0.0)
            sec = collections.defaultdict(float)
            for h in p["holdings"]:
                bucket, s = classify(h, cap)
                if bucket is None:
                    continue
                b[bucket] += h["pct_nav"]
                if s and bucket in ("large", "mid", "small", "unclassified"):
                    sec[s] += h["pct_nav"]
                    if h.get("industry"):
                        names[s][h["industry"]] += 1
            splits[pid] = [round(b[k], 2) for k in BUCKETS]
            sectors[pid] = {k: round(v, 2) for k, v in sorted(sec.items(), key=lambda kv: -kv[1]) if round(v, 2) != 0}
            sc = p.get("scheme") or {}
            catalog[pid] = {"name": sc.get("parent_name") or sc.get("name"), "amc": sc.get("amc_name"), "members": p.get("member_amfi_codes") or []}
        os.makedirs(os.path.join(out_root, "splits"), exist_ok=True)
        os.makedirs(os.path.join(out_root, "sectors"), exist_ok=True)
        json.dump({"as_of": asof, "amfi_list": key, "buckets": BUCKETS, "funds": splits},
                  open(os.path.join(out_root, "splits", month + ".json"), "w"), separators=(",", ":"))
        json.dump({"as_of": asof, "funds": sectors}, open(os.path.join(out_root, "sectors", month + ".json"), "w"), separators=(",", ":"))
        print(f"{asof}: {len(splits)} funds (AMFI list {key}, {len(cap)} stocks)")
    # display name: most common mixed-case spelling
    disp = {}
    for k, c in names.items():
        mixed = [(n, v) for n, v in c.items() if not n.isupper()] or list(c.items())
        disp[k] = max(mixed, key=lambda kv: kv[1])[0].replace(" and ", " & ")
    p = os.path.join(out_root, "sector_names.json")
    old = json.load(open(p)) if os.path.exists(p) else {}
    old.update(disp)
    json.dump(old, open(p, "w"), separators=(",", ":"), sort_keys=True)
    p = os.path.join(out_root, "catalog.json")
    old = json.load(open(p)) if os.path.exists(p) else {}
    old.update(catalog)
    json.dump(old, open(p, "w"), separators=(",", ":"), sort_keys=True)


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "data"))
