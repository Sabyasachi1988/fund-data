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


def get(url, tries=8):
    err = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"})
            return json.loads(urllib.request.urlopen(req, timeout=120).read().decode("utf-8", "ignore"))
        except Exception as e:
            err = e
            # AMFI rate-limits (HTTP 429): back off harder each time
            time.sleep((8 if "429" in str(e) else 2) * (i + 1))
    print("GET failed", url, err, flush=True)
    return None


def norm(name):
    s = name.lower()
    s = re.sub(r"\s*-\s*(direct|regular|retail|institutional)\b.*$", "", s)
    s = s.replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", s)


# ---------------------------------------------------------------- categories and peer groups
def canon(raw):
    """One spelling per fund category (AMFI/mfapi use several for the same thing)."""
    s0 = (raw or "").strip()
    l = s0.lower()
    if not s0:
        return "Other"
    if l.startswith("index funds"):
        return "Debt Index Funds" if "debt" in l else "Index Funds"
    if "etf" in l or l.startswith("exchange traded"):
        return "Gold ETF" if "gold" in l else "Silver ETF" if "silver" in l else "Debt ETF" if "debt" in l else "Other ETFs" if "other" in l else "Equity ETF"
    if "overseas" in l and "fund" in l or l.startswith("fof overseas"):
        return "FoF Overseas"
    if l.startswith("fund of funds") or l.startswith("fof domestic"):
        return "FoF Domestic"
    if "sectoral" in l or "thematic" in l:
        return "Sectoral / Thematic"
    t = re.sub(r"^(income/debt oriented schemes|equity schemes?|debt schemes?|hybrid schemes?|solution oriented schemes?( \*\*)?|other schemes?)\s*-\s*", "", l)
    t = re.sub(r"^hybrid\s*-\s*", "", t).strip()
    rules = [
        ("balanced advantage", "Balanced Advantage / Dynamic Asset Allocation"), ("dynamic asset allocation", "Balanced Advantage / Dynamic Asset Allocation"),
        ("retirement", "Retirement Fund"), ("children", "Children's Fund"), ("life cycle", "Life Cycle Fund"),
        ("elss", "ELSS"), ("ultra short", "Ultra Short Duration Fund"), ("medium to long", "Medium to Long Duration Fund"),
        ("medium term", "Medium Duration Fund"), ("medium duration", "Medium Duration Fund"), ("long term", "Long Duration Fund"),
        ("short term", "Short Duration Fund"), ("short duration", "Short Duration Fund"), ("dynamic term", "Dynamic Bond"), ("dynamic bond", "Dynamic Bond"),
        ("floating", "Floater Fund"), ("banking and psu", "Banking and PSU Fund"), ("constant maturity", "Gilt Fund with 10-year Constant Duration"),
        ("gilt", "Gilt Fund"), ("corporate bond", "Corporate Bond Fund"), ("credit risk", "Credit Risk Fund"), ("money market", "Money Market Fund"),
        ("liquid", "Liquid Fund"), ("overnight", "Overnight Fund"), ("balanced hybrid", "Balanced Hybrid Fund"), ("conservative hybrid", "Conservative Hybrid Fund"),
        ("aggressive hybrid", "Aggressive Hybrid Fund"), ("equity savings", "Equity Savings Fund"), ("multi asset", "Multi Asset Allocation Fund"),
        ("arbitrage", "Arbitrage Fund"), ("contra", "Contra Fund"),
    ]
    for key, label in rules:
        if key in t:
            return label
    return t.title().replace("&", "&") if t.islower() else t


THEMES = [
    (r"bank|financial|finserv|\bbfsi\b|insurance", "Banking and Financial Services"), (r"pharma|health|medic", "Pharma and Healthcare"),
    (r"tech|\bit\b|digital|software|internet", "Technology"), (r"infra|construction|real estate|housing", "Infrastructure and Real Estate"),
    (r"consum|fmcg|retail|lifestyle", "Consumption"), (r"\bpsu\b|public sector", "PSU"), (r"manufactur|make in india|industrial", "Manufacturing"),
    (r"energy|power|oil|gas|utilit", "Energy and Power"), (r"\bauto|mobility|transport|logistic", "Auto and Transport"), (r"defen[cs]e|aerospace", "Defence"),
    (r"\besg\b|sustain|green|climate", "ESG"), (r"innovation|new age|emerging|disrupt|business cycle|opportunit", "Innovation and Opportunities"),
    (r"commodit|metal|material|mining|chemical", "Commodities and Materials"), (r"global|international|world|china|us |nasdaq", "International themes"),
]
INDEX_DROP = r"\b(index|fund|funds|direct|plan|growth|option|idcw|dividend|etf|fof|of|the|india|nse|bse|tri|scheme|an|open|ended|passive)\b"



def clean(series):
    """Fix NAV-feed glitches: one-off spikes are dropped; permanent re-bases (face-value changes such as 100 -> 1000)
    are removed by scaling the earlier history. Returns (series, changed)."""
    pts = list(series)
    changed = False
    i = 1
    while i < len(pts):
        a, b = pts[i - 1][1], pts[i][1]
        if a > 0 and (b / a > 2.5 or b / a < 0.4):
            changed = True
            ratio = b / a
            back = next((j for j in range(i + 1, min(i + 6, len(pts))) if 0.8 < pts[j][1] / a < 1.25), None)
            if back is not None:  # spike: drop the bad points
                del pts[i:back]
                continue
            pts[:i] = [(d, v * ratio) for d, v in pts[:i]]
        i += 1
    return pts, changed


NO_RISK = ("Overnight Fund", "Liquid Fund", "Ultra Short Duration Fund", "Money Market Fund", "Low Duration Fund", "Debt ETF",
           "Arbitrage Fund")


def fof_peer(name, cat):
    n = name.lower()
    for pat, label in (("gold", "FoF: Gold"), ("silver", "FoF: Silver"), (r"fang|nasdaq|s&p 500|us equity|world|global|international|overseas|china|japan|europe|emerging", "FoF: International"),
                       (r"debt|bond|gilt|liquid|income|credit|duration|short term|corporate|target maturity|\bsdl\b|psu", "FoF: Debt"),
                       (r"hybrid|balanced|asset allocation|multi asset|arbitrage|equity savings", "FoF: Hybrid / Multi-asset"),
                       ("elss|tax", "FoF: Equity")):
        if re.search(pat, n):
            return label
    return "FoF: Equity"


def peer_key(row):
    c = canon(row.get("cat"))
    name = (row.get("name") or "").lower()
    if c in ("Index Funds", "Debt Index Funds"):
        n = name.replace("&", "and")
        for w in re.split(r"\s+", (row.get("amc") or "").lower().replace("mutual fund", "").replace("mf", "").strip()):
            if w:
                n = re.sub(r"\b" + re.escape(w) + r"\b", " ", n)
        n = re.sub(INDEX_DROP, " ", n)
        n = re.sub(r"[^a-z0-9 ]", " ", n)
        n = re.sub(r"\s+", " ", n).strip()
        if not n:
            return c
        return ("Debt index: " if c == "Debt Index Funds" else "Index: ") + n.title()
    if c == "Sectoral / Thematic":
        for pat, label in THEMES:
            if re.search(pat, name):
                return "Theme: " + label
        return "Theme: Other"
    if c == "FoF Domestic":
        return fof_peer(name, c)
    return c


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
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            results += list(ex.map(page, [(m, p) for p in range(2, pages + 1)]))
        missing = [p for p, res in zip(range(2, pages + 1), results[1:]) if res is None]
        print("TER", m, "pages missing after first pass:", len(missing), flush=True)
        for p in missing:  # second pass, one at a time
            time.sleep(3)
            res = page((m, p))
            if res is not None:
                results[p - 1] = res
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
def plan_free(name):
    """Scheme name with plan/option words removed, so Direct, Regular and IDCW variants of one scheme share a key."""
    n = re.sub(r"\b(direct|regular|retail|institutional|growth|idcw|dividend|payout|reinvestment|bonus|option|plan|plans|annual|monthly|quarterly|weekly|daily|fortnightly)\b", " ", name.lower().replace("&", "and"))
    return re.sub(r"[^a-z0-9]", "", n)


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
                    base = (g.get("Mfname", "") + "|" + plan_free(s.get("SchemeNAVName", "")))
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
    print("rows deduped", len(rows), flush=True)
    print("rows", len(rows), flush=True)

    # NAV-derived
    by_cat = {}
    fixed = 0
    import screener as scr
    for r in rows:
        r["cat0"] = r.get("cat0") or r["cat"]  # keep the source spelling once
        r["cat"] = canon(r["cat0"])
        r["peer"] = peer_key({"cat": r["cat0"], "name": r["name"], "amc": r.get("amc")})
        e = navs.get(str(r["c"]))
        if e:
            ser, changed = clean(decode(e))
            if changed:
                fixed += 1
                sr = [(dt.date.fromordinal(d + EPOCH), v) for d, v in ser]
                m = scr.metrics(sr)
                r["r"], r["roll"], r["vol"], r["mdd"], r["sharpe"], r["age"] = m
                navs[str(r["c"])] = scr.daily_delta(sr)
                print("  cleaned NAV glitch:", r["name"], flush=True)
            by_cat.setdefault(r["peer"], {})[r["c"]] = month_end(ser)
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
            base = next((b for b in by_base if b.endswith("|" + plan_free(r["name"])) and b.split("|")[0].split()[0].lower() == (r.get("amc") or "x").split()[0].lower()), None)
        if base is not None and base in by_base:
            r["aum"] = round(by_base[base] / 100.0, 1)  # AMFI reports Rs lakh -> Rs crore
            matched_a += 1
    for r in rows:
        if r["cat"] in NO_RISK:  # a fixed 6.5% risk-free rate makes these meaningless for cash-like funds
            for k in ("sharpe", "sortino", "alpha", "beta", "upc", "dnc"):
                r.pop(k, None)
            r["sharpe"] = None
            r["cap"] = r.get("cap")
        for k in ("upc", "dnc"):
            if k in r and not (-50 <= r[k] <= 300):
                r.pop(k)
        if r.get("sharpe") is not None and abs(r["sharpe"]) > 10:
            r["sharpe"] = None
        for lab in ("1y", "3y", "5y"):
            ro = (r.get("roll") or {}).get(lab)
            if ro and (ro[2] > 400 or ro[1] < -95):
                r["roll"][lab] = None
    print("NAV series cleaned:", fixed, flush=True)
    print(f"TER matched {matched_t}/{len(rows)}  AUM matched {matched_a}/{len(rows)}", flush=True)
    top = sorted((r for r in rows if r.get("aum")), key=lambda r: -r["aum"])[:5]
    for r in top:
        print("  largest:", r["name"], r["aum"], "cr", flush=True)
    low = [r["name"] for r in rows if "ter" not in r][:15]
    print("  sample TER unmatched:", low, flush=True)
    cats = {}
    for r in rows:
        cats[r["peer"]] = cats.get(r["peer"], 0) + 1
    print("peer groups", len(cats), "with >=5 funds:", sum(1 for v in cats.values() if v >= 5), flush=True)
    print("  biggest:", sorted(cats.items(), key=lambda kv: -kv[1])[:25], flush=True)
    bundle["fields"] = 3
    bundle["v"] = 5
    if fixed:
        with gzip.open(os.path.join(d, "navd.json.gz"), "rt") as f:
            nb = json.load(f)
        nb["f"] = navs
        with gzip.open(os.path.join(d, "navd.json.gz"), "wt", compresslevel=9) as f:
            json.dump(nb, f, separators=(",", ":"))
    with gzip.open(os.path.join(d, "screener.json.gz"), "wt", compresslevel=9) as f:
        json.dump(bundle, f, separators=(",", ":"))
    print("done", round(time.time() - t0), "s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
