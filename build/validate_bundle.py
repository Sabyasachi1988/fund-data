"""Refuse to publish a screener bundle that looks broken, so one bad run can never replace good live data.

Usage: python3 build/validate_bundle.py DIR [PREVIOUS_DIR]
DIR holds screener.json.gz and navd.json.gz. PREVIOUS_DIR (optional) holds the bundle currently live; the new one may not
have fewer than 90% of its funds. Exit status 0 = fine, 1 = problems (each printed)."""
import datetime as dt, gzip, json, os, sys

MIN_ROWS = 1500
MIN_SHARE = {"ter": 0.95, "aum": 0.95, "r": 0.95}
SPLIT_LEN = 9          # large, mid, small, unclassified, foreign, debt, cash, other, reit/invit
MAX_AGE_DAYS = 45
MIN_COMPLETE = 0.90


def load(d, name):
    with gzip.open(os.path.join(d, name), "rt") as f:
        return json.load(f)


def check(d, prev=None, today=None):
    """Return a list of problems (empty = fine)."""
    today = today or dt.date.today()
    bad = []
    try:
        b = load(d, "screener.json.gz")
        navs = load(d, "navd.json.gz")["f"]
    except Exception as e:
        return [f"cannot read the bundle: {e}"]
    rows = b.get("rows") or []
    n = len(rows)
    if n < MIN_ROWS:
        bad.append(f"only {n} funds (need at least {MIN_ROWS})")
    if len({r.get("c") for r in rows}) != n:
        bad.append("repeated scheme codes")
    try:
        age = (today - dt.date.fromisoformat(b["asof"])).days
        if age > MAX_AGE_DAYS:
            bad.append(f"data is {age} days old")
    except Exception:
        bad.append("no valid 'asof' date")
    for key, share in MIN_SHARE.items():
        have = sum(1 for r in rows if r.get(key) not in (None, {}, []))
        if n and have / n < share:
            bad.append(f"only {have}/{n} funds have '{key}' (need {share:.0%})")
    with_split = [r for r in rows if r.get("pid")]
    good = [r for r in with_split if isinstance(r.get("s8"), list) and len(r["s8"]) == SPLIT_LEN and 98.0 <= sum(r["s8"]) <= 102.0]
    # A fund's split need not add to 100: arbitrage / equity-savings funds list hedged equity (over 100), and some
    # funds of funds or sparse disclosures are partial. So the check is on the bulk, set from the live data
    # (94% complete in Nov 2026), not on every fund.
    if with_split and len(good) / len(with_split) < MIN_COMPLETE:
        bad.append(f"only {len(good)}/{len(with_split)} composition splits are complete ({SPLIT_LEN} numbers adding to 100); need {MIN_COMPLETE:.0%}")
    if any(len(r["s8"]) != SPLIT_LEN for r in with_split if isinstance(r.get("s8"), list)) and \
       sum(1 for r in with_split if isinstance(r.get("s8"), list) and len(r["s8"]) != SPLIT_LEN) / len(with_split) > 0.05:
        bad.append("composition splits have the wrong number of values")
    if any(x < -50.0 for r in with_split if isinstance(r.get("s8"), list) for x in r["s8"]):
        bad.append("a composition share below -50% (net cash can be negative - the live minimum is about -26% - but not this much)")
    miss = sum(1 for r in rows if str(r.get("c")) not in navs)
    if n and miss / n > 0.05:
        bad.append(f"{miss}/{n} funds have no daily NAV history")
    if prev:
        try:
            pn = len(load(prev, "screener.json.gz").get("rows") or [])
            if pn and n < 0.9 * pn:
                bad.append(f"{n} funds is more than 10% fewer than the {pn} currently live")
        except Exception:
            pass  # nothing to compare with
    return bad


if __name__ == "__main__":
    problems = check(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    for p in problems:
        print("PROBLEM:", p)
    print("bundle OK" if not problems else f"{len(problems)} problem(s) - not publishing")
    sys.exit(1 if problems else 0)
