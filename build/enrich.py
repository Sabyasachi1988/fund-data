"""Adds to the screener bundle, without refetching any NAVs:
  ter    Direct-plan total expense ratio, % per year  (AMFI "TER of MF Schemes", latest published date)
  aum    scheme size, Rs crore (AMFI average AUM scheme-wise, latest quarter, all plans of the scheme summed)
  sortino, alpha, beta, upc, dnc, beat   computed from daily NAVs against the CATEGORY MEDIAN (peers = same category in this bundle)
Usage: python3 build/enrich.py DIR     (DIR holds screener.json.gz + navd.json.gz; screener.json.gz is rewritten)
"""
import datetime as dt, gzip, json, math, os, re, statistics, sys, time, urllib.request
import concurrent.futures as cf

RF = 0.065
EPOCH = dt.date(1970, 1, 1).toordinal()
API = "https://www.amfiindia.com/api"


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"})
            return json.loads(urllib.request.urlopen(req, timeout=120).read().decode("utf-8", "ignore"))
        except Exception as e:
            err = e
            time.sleep(2 * (i + 1))
    print("GET failed", url, err, flush=True)
    return None


def norm(name):
    s = name.lower()
    s = re.sub(r"\s*-\s*(direct|regular|retail|institutional)\b.*$", "", s)
    s = s.replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", s)


# ---------------------------------------------------------------- expense ratio
def fetch_ter():
    today = dt.date.today()
    months = [today, (today.replace(day=1) - dt.timedelta(days=1))]
    best = {}  # normalized name -> (date, D_TER, R_TER)

    def page(args):
        m, p = args
        return get(f"{API}/populate-te-rdata-revised?MF_ID=All&Month={m}&strCat=-1&strType=-1&page={p}&pageSize=100")

    for d in months:
        m = f"{d.month:02d}-{d.year}"
        first = page((m, 1))
        meta = (first or {}).get("meta") if isinstance(first, dict) else None
        if not meta:
            print("TER: no data for", m, flush=True)
            continue
        pages = meta["pageCount"]
        print("TER", m, "rows", meta["total"], "pages", pages, flush=True)
        results = [first]
        with cf.ThreadPoolExecutor(max_workers=6) as ex:
            results += list(ex.map(page, [(m, p) for p in range(2, pages + 1)]))
        for res in results:
            for r in (res or {}).get("data", []) or []:
                try:
                    key = norm(r["Scheme_Name"])
                    d_ter = float(r["D_TER"])
                    when = r["TER_Date"][:10]
                except Exception:
                    continue
                if (r.get("SchemeType_Desc") or "").lower().startswith("close"):
                    continue
                if key not in best or when >= best[key][0]:
                    best[key] = (when, d_ter)
    print("TER schemes", len(best), flush=True)
    return best


# ---------------------------------------------------------------- AUM
def fetch_aum():
    fy = get(f"{API}/average-aum-fundwise")
    years = (fy or {}).get("data") or []
    for y in years[:2]:
        per = get(f"{API}/average-aum-fundwise?fyId={y['id']}")
        for p in ((per or {}).get("data") or {}).get("periods", []):
            d = get(f"{API}/average-aum-schemewise?strType=Categorywise&fyId={y['id']}&periodId={p['id']}&MF_ID=0")
            groups = (d or {}).get("data") or []
            if not groups:
                continue
            by_code, by_base = {}, {}
            for g in groups:
                for s in g.get("schemes", []):
                    a = s.get("AverageAumForTheMonth") or {}
                    v = sum(float(x or 0) for x in a.values())
                    base = (g.get("Mfname", "") + "|" + norm(s.get("SchemeNAVName", "")))
                    by_code[int(s["AMFI_Code"])] = base
                    by_base[base] = by_base.get(base, 0.0) + v
            print("AUM period", y["financial_year"], p["period"], "schemes", len(by_code), flush=True)
            return by_code, by_base
    print("AUM: nothing found", flush=True)
    return {}, {}


# ---------------------------------------------------------------- NAV-derived metrics
def decode(entry):
    d0, n0, gaps, diffs = entry
    day, nav = d0, n0
    out = [(day, nav / 10000.0)]
    for g, df in zip(gaps, diffs):
        day += g
        nav += df
        out.append((day, nav / 10000.0))
    return out


def month_end(series):
    me = {}
    for day, nav in series:
        d = dt.date.fromordinal(day + EPOCH)
        me[d.year * 12 + d.month - 1] = nav
    return me


def ols(y, x):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx <= 0:
        return None, None
    b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / sxx
    return my - b * mx, b


def category_metrics(members):
    """members: {code: {monthIndex: nav}} for one category (>=5 funds). Returns {code: {...}}."""
    all_months = sorted({m for me in members.values() for m in me})
    last = all_months[-1]
    # monthly returns and 12m returns per fund
    mret = {c: {m: me[m] / me[m - 1] - 1 for m in me if (m - 1) in me and me[m - 1] > 0} for c, me in members.items()}
    r12 = {c: {m: me[m] / me[m - 12] - 1 for m in me if (m - 12) in me and me[m - 12] > 0} for c, me in members.items()}
    cat_m, cat_12 = {}, {}
    for m in all_months:
        v = [r[m] for r in mret.values() if m in r]
        if len(v) >= 5:
            cat_m[m] = statistics.median(v)
        v = [r[m] for r in r12.values() if m in r]
        if len(v) >= 5:
            cat_12[m] = statistics.median(v)
    rfm = RF / 12
    out = {}
    for c in members:
        res = {}
        ms = [m for m in range(last - 35, last + 1) if m in mret[c] and m in cat_m]
        if len(ms) >= 30:
            f = [mret[c][m] for m in ms]
            k = [cat_m[m] for m in ms]
            a, b = ols([x - rfm for x in f], [x - rfm for x in k])
            if a is not None:
                res["alpha"] = round(a * 12 * 100, 2)
                res["beta"] = round(b, 2)
            up = [(x, y) for x, y in zip(f, k) if y > 0]
            dn = [(x, y) for x, y in zip(f, k) if y < 0]
            if len(up) >= 6 and sum(y for _, y in up) > 0:
                res["upc"] = round(sum(x for x, _ in up) / sum(y for _, y in up) * 100, 1)
            if len(dn) >= 6 and sum(y for _, y in dn) < 0:
                res["dnc"] = round(sum(x for x, _ in dn) / sum(y for _, y in dn) * 100, 1)
            dd = math.sqrt(sum(min(x - rfm, 0) ** 2 for x in f) / len(f)) * math.sqrt(12)
            if dd > 0:
                res["sortino"] = round((sum(f) / len(f) - rfm) * 12 / dd, 2)
        ws = [m for m in range(last - 59, last + 1) if m in r12[c] and m in cat_12]
        if len(ws) >= 24:
            res["beat"] = round(sum(1 for m in ws if r12[c][m] > cat_12[m]) / len(ws) * 100)
        if res:
            out[c] = res
    return out


def main(d):
    t0 = time.time()
    with gzip.open(os.path.join(d, "screener.json.gz"), "rt") as f:
        bundle = json.load(f)
    with gzip.open(os.path.join(d, "navd.json.gz"), "rt") as f:
        navs = json.load(f)["f"]
    best = {}
    for r in bundle["rows"]:  # the published bundle may still hold repeated scheme codes; keep the freshest of each
        if r["c"] not in best or r.get("d", "") >= best[r["c"]].get("d", ""):
            best[r["c"]] = r
    rows = bundle["rows"] = sorted(best.values(), key=lambda r: (r["cat"], r["name"]))
    print("rows", len(rows), flush=True)

    # NAV-derived
    by_cat = {}
    for r in rows:
        e = navs.get(str(r["c"]))
        if e:
            by_cat.setdefault(r["cat"], {})[r["c"]] = month_end(decode(e))
    extra = {}
    for cat, members in by_cat.items():
        if len(members) >= 5:
            extra.update(category_metrics(members))
    print("derived metrics for", len(extra), "funds", round(time.time() - t0), "s", flush=True)

    ter = fetch_ter()
    by_code, by_base = fetch_aum()
    matched_t = matched_a = 0
    for r in rows:
        for k in ("ter", "terd", "aum", "sortino", "alpha", "beta", "upc", "dnc", "beat"):
            r.pop(k, None)
        r.update(extra.get(r["c"], {}))
        key = norm(r["name"])
        if key in ter:
            r["terd"], r["ter"] = ter[key]
            matched_t += 1
        base = by_code.get(int(r["c"]))
        if base is None:
            base = next((b for b in by_base if b.endswith("|" + key) and b.split("|")[0].split()[0].lower() == (r.get("amc") or "x").split()[0].lower()), None)
        if base is not None and base in by_base:
            r["aum"] = round(by_base[base] / 100.0, 1)  # AMFI reports Rs lakh -> Rs crore
            matched_a += 1
    print(f"TER matched {matched_t}/{len(rows)}  AUM matched {matched_a}/{len(rows)}", flush=True)
    top = sorted((r for r in rows if r.get("aum")), key=lambda r: -r["aum"])[:5]
    for r in top:
        print("  largest:", r["name"], r["aum"], "cr", flush=True)
    low = [r["name"] for r in rows if "ter" not in r][:15]
    print("  sample TER unmatched:", low, flush=True)
    bundle["fields"] = 2
    with gzip.open(os.path.join(d, "screener.json.gz"), "wt", compresslevel=9) as f:
        json.dump(bundle, f, separators=(",", ":"))
    print("done", round(time.time() - t0), "s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
