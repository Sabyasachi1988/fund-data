"""Build the all-funds screener bundle: every open-ended Direct-Growth scheme on mfapi.in, with returns/risk metrics
and a weekly NAV series, so the phone can download everything once and screen/compare locally.

Outputs (in OUT dir):
  screener.json.gz   {"asof","count","rows":[{c,isin,name,cat,amc,pid,nav,d,age,r:{...},roll:{...},vol,mdd,sharpe,cap:[l,m,s]}]}
  navd.json.gz       DAILY NAVs, delta-encoded: {"fmt":"daily-delta-1","f":{code:[d0,n0,[dayGaps...],[navDiffs...]]}}
                     d0 = first date as days since 1970-01-01, n0 = first NAV x 10000 (int); each later point adds a day gap and a NAV diff.
Usage: python3 build/screener.py OUT
"""
import concurrent.futures as cf, datetime as dt, glob, gzip, json, math, os, re, statistics, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RF = 0.065  # risk-free rate used for Sharpe (constant, stated in the app)


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore"))
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def pdate(s):
    return dt.datetime.strptime(s, "%d-%m-%Y").date()


def nav_on_or_before(days, navs, target):
    # days sorted ascending list of date ordinals
    lo, hi = 0, len(days) - 1
    if target < days[0]:
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if days[mid] <= target:
            lo = mid
        else:
            hi = mid - 1
    return navs[lo]


def cagr(a, b, years):
    if a is None or b is None or a <= 0 or years <= 0:
        return None
    return ((b / a) ** (1 / years) - 1) * 100


def metrics(series):
    """series: ascending list of (date, nav)."""
    days = [d.toordinal() for d, _ in series]
    navs = [n for _, n in series]
    last_d, last_n = series[-1]
    out = {}
    for label, months in (("1m", 1), ("3m", 3), ("6m", 6), ("1y", 12), ("3y", 36), ("5y", 60), ("10y", 120)):
        t = (last_d - dt.timedelta(days=round(months * 30.4375))).toordinal()
        base = nav_on_or_before(days, navs, t)
        if base is None:
            out[label] = None
            continue
        yrs = months / 12
        out[label] = round(((last_n / base - 1) * 100) if months <= 12 else cagr(base, last_n, yrs), 2)
    age = (last_d - series[0][0]).days / 365.25
    # month-end series for rolling / vol
    me = {}
    for d, n in series:
        me[(d.year, d.month)] = n
    mnav = [me[k] for k in sorted(me)]
    roll = {}
    for label, m in (("1y", 12), ("3y", 36), ("5y", 60)):
        vals = [cagr(mnav[i], mnav[i + m], m / 12) for i in range(0, len(mnav) - m)]
        vals = [v for v in vals if v is not None]
        roll[label] = [round(statistics.median(vals), 2), round(min(vals), 2), round(max(vals), 2)] if len(vals) >= 6 else None
    rets = [mnav[i + 1] / mnav[i] - 1 for i in range(len(mnav) - 1) if mnav[i] > 0]
    r36 = rets[-36:]
    vol = round(statistics.pstdev(r36) * math.sqrt(12) * 100, 2) if len(r36) >= 24 else None
    peak, mdd = 0.0, 0.0
    for n in navs:
        peak = max(peak, n)
        if peak > 0:
            mdd = min(mdd, n / peak - 1)
    sharpe = None
    if vol and out.get("3y") is not None and vol > 0:
        sharpe = round((out["3y"] - RF * 100) / vol, 2)
    return out, roll, vol, round(mdd * 100, 2), sharpe, round(age, 2)


EPOCH = dt.date(1970, 1, 1).toordinal()


def daily_delta(series):
    """series ascending [(date, nav)] -> [d0, n0, [gaps], [diffs]] (NAV in 1/10000 units, exact to 4 decimals)."""
    days = [d.toordinal() - EPOCH for d, _ in series]
    navs = [int(round(n * 10000)) for _, n in series]
    return [days[0], navs[0], [days[i] - days[i - 1] for i in range(1, len(days))], [navs[i] - navs[i - 1] for i in range(1, len(navs))]]


def main(out):
    os.makedirs(out, exist_ok=True)
    lst = get("https://api.mfapi.in/mf")
    if not lst:
        sys.exit("could not fetch mfapi list")
    cand = []
    for s in lst:
        n = (s.get("schemeName") or "")
        l = n.lower()
        if "direct" in l and "growth" in l and not re.search(r"idcw|dividend|bonus|payout|segregated|fmp|fixed (term|maturity)|interval|capital protection", l):
            cand.append((s["schemeCode"], n))
    print("candidates", len(cand), flush=True)
    # published cap mix for funds we have
    codes = json.load(open(os.path.join(ROOT, "pub/codes.json")))
    latest_cap = {}

    def cap_for(code):
        pid = codes.get(str(code))
        if not pid:
            return "", None
        if pid not in latest_cap:
            try:
                d = json.load(open(os.path.join(ROOT, "pub/funds", pid + ".json")))["months"]
                m = d[sorted(d)[-1]]["s"]
                latest_cap[pid] = [m[0], m[1], m[2]]
            except Exception:
                latest_cap[pid] = None
        return pid, latest_cap[pid]

    rows, navs, bad = [], {}, 0
    today = dt.date.today()

    def work(item):
        code, name = item
        d = get(f"https://api.mfapi.in/mf/{code}")
        if not d or not d.get("data"):
            return None
        meta = d.get("meta", {})
        if "open" not in (meta.get("scheme_type") or "").lower():
            return None
        try:
            series = sorted((pdate(x["date"]), float(x["nav"])) for x in d["data"] if float(x["nav"]) > 0)
        except Exception:
            return None
        if len(series) < 30 or (today - series[-1][0]).days > 20:
            return None  # too short or no longer live
        r, roll, vol, mdd, sharpe, age = metrics(series)
        pid, cap = cap_for(code)
        row = {"c": code, "isin": meta.get("isin_growth") or "", "name": meta.get("scheme_name") or name,
               "cat": (meta.get("scheme_category") or "").replace("Equity Scheme - ", "").replace("Debt Scheme - ", "").replace("Hybrid Scheme - ", "Hybrid - ").replace("Other Scheme - ", ""),
               "amc": meta.get("fund_house") or "", "pid": pid, "nav": series[-1][1], "d": series[-1][0].isoformat(),
               "age": age, "r": r, "roll": roll, "vol": vol, "mdd": mdd, "sharpe": sharpe, "cap": cap}
        return row, daily_delta(series)

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        for i, res in enumerate(ex.map(work, cand)):
            if res is None:
                bad += 1
            else:
                row, enc = res
                rows.append(row)
                navs[str(row["c"])] = enc
            if i % 200 == 0:
                print(i, len(rows), round(time.time() - t0), "s", flush=True)
    rows.sort(key=lambda r: (r["cat"], r["name"]))
    asof = dt.date.today().isoformat()
    with gzip.open(os.path.join(out, "screener.json.gz"), "wt", compresslevel=9) as f:
        json.dump({"asof": asof, "rf": RF, "count": len(rows), "rows": rows}, f, separators=(",", ":"))
    with gzip.open(os.path.join(out, "navd.json.gz"), "wt", compresslevel=9) as f:
        json.dump({"asof": asof, "fmt": "daily-delta-1", "f": navs}, f, separators=(",", ":"))
    cats = {}
    for r in rows:
        cats[r["cat"]] = cats.get(r["cat"], 0) + 1
    print("rows", len(rows), "skipped", bad)
    print(sorted(cats.items(), key=lambda kv: -kv[1])[:25])
    for fn in ("screener.json.gz", "navd.json.gz"):
        print(fn, os.path.getsize(os.path.join(out, fn)) // 1024, "KB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/screener")
