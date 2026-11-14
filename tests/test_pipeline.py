"""Tests for the screener pipeline pieces that must not silently break. Made-up data only.
Run: python3 -m unittest discover -s tests"""
import datetime as dt, gzip, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "build"))
import enrich
import validate_bundle as vb
import compute


def write_bundle(d, rows, navs=None, asof="2026-11-09"):
    with gzip.open(os.path.join(d, "screener.json.gz"), "wt") as f:
        json.dump({"asof": asof, "rows": rows}, f)
    with gzip.open(os.path.join(d, "navd.json.gz"), "wt") as f:
        json.dump({"f": navs if navs is not None else {str(r["c"]): [0, 0, [], []] for r in rows}}, f)


def good_rows(n=1600):
    return [{"c": i, "pid": str(i), "ter": 0.5, "aum": 100.0, "r": {"1y": 1.0}, "s8": [20, 10, 10, 1, 1, 40, 10, 3, 5]} for i in range(n)]


class TerCache(unittest.TestCase):
    def page(self, rows):
        return {"meta": {"pageCount": 1, "total": len(rows)}, "data": rows}

    def row(self, name, ter, when):
        return {"Scheme_Name": name, "D_TER": str(ter), "TER_Date": when, "SchemeType_Desc": "Open Ended"}

    def test_cached_run_fetches_only_current_month_and_keeps_unreported_schemes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ter.json")
            json.dump({"updated": "x", "ter": {enrich.norm("Alpha Fund - Direct Plan"): ["2026-10-31", 0.40],
                                               enrich.norm("Beta Fund - Direct Plan"): ["2026-10-31", 0.90]}}, open(path, "w"))
            asked = []

            def fake(url):
                asked.append(url)
                return self.page([self.row("Alpha Fund - Direct Plan", 0.35, "2026-11-05")])
            best = enrich.fetch_ter(path, today=dt.date(2026, 11, 14), fetch_page=fake)
            self.assertTrue(all("Month=11-2026" in u for u in asked), asked)  # the previous month is not downloaded
            self.assertEqual(best[enrich.norm("Alpha Fund")], ("2026-11-05", 0.35))   # newer figure wins
            self.assertEqual(best[enrich.norm("Beta Fund")], ("2026-10-31", 0.90))    # not reported again: keeps the cached one

    def test_older_figure_never_replaces_a_newer_cached_one(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ter.json")
            json.dump({"updated": "x", "ter": {enrich.norm("Alpha Fund"): ["2026-11-05", 0.35]}}, open(path, "w"))
            best = enrich.fetch_ter(path, today=dt.date(2026, 11, 14),
                                    fetch_page=lambda u: self.page([self.row("Alpha Fund - Direct Plan", 0.40, "2026-10-01")]))
            self.assertEqual(best[enrich.norm("Alpha Fund")], ("2026-11-05", 0.35))

    def test_no_cache_fetches_both_months(self):
        with tempfile.TemporaryDirectory() as d:
            asked = []
            enrich.fetch_ter(os.path.join(d, "none.json"), today=dt.date(2026, 11, 14), fetch_page=lambda u: asked.append(u) or None)
            self.assertTrue(any("Month=11-2026" in u for u in asked) and any("Month=10-2026" in u for u in asked))

    def test_cache_drops_schemes_that_stopped_reporting(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ter.json")
            n = enrich.save_ter_cache(path, {"old": ("2024-01-01", 1.0), "new": ("2026-10-01", 0.5)}, today=dt.date(2026, 11, 14))
            self.assertEqual(n, 1)
            self.assertEqual(list(enrich.load_ter_cache(path)), ["new"])


class Validate(unittest.TestCase):
    def check(self, rows, **kw):
        with tempfile.TemporaryDirectory() as d:
            write_bundle(d, rows, kw.get("navs"), kw.get("asof", "2026-11-09"))
            return vb.check(d, kw.get("prev"), today=dt.date(2026, 11, 14))

    def test_good_bundle_passes(self):
        self.assertEqual(self.check(good_rows()), [])

    def test_too_few_funds_fails(self):
        self.assertTrue(any("only 10 funds" in p for p in self.check(good_rows(10))))

    def test_missing_expense_ratios_fail(self):
        rows = good_rows()
        for r in rows[:200]:
            r.pop("ter")
        self.assertTrue(any("'ter'" in p for p in self.check(rows)))

    def test_old_eight_number_splits_fail(self):
        rows = good_rows()
        for r in rows:
            r["s8"] = r["s8"][:8]
        self.assertTrue(any("composition" in p for p in self.check(rows)))

    def test_stale_data_fails(self):
        self.assertTrue(any("days old" in p for p in self.check(good_rows(), asof="2026-08-01")))

    def test_a_few_odd_splits_are_tolerated_but_a_wild_share_is_not(self):
        rows = good_rows()
        for r in rows[:50]:
            r["s8"] = [20, 10, 10, 1, 1, 60, 30, 8, 5]   # hedged: adds to 145
        self.assertEqual(self.check(rows), [])
        rows[0]["s8"] = [20, 10, 10, 1, 1, 40, -80, 3, 5]
        self.assertTrue(any("below -50%" in p for p in self.check(rows)))

    def test_losing_more_than_ten_percent_of_funds_fails(self):
        with tempfile.TemporaryDirectory() as prev:
            write_bundle(prev, good_rows(2000))
            self.assertTrue(any("fewer than" in p for p in self.check(good_rows(1600), prev=prev)))


class Classify(unittest.TestCase):
    cap = {"INE090A01021": "large", "INE0NDH25011": "small"}

    def test_reit_and_invit_units_are_their_own_bucket(self):
        reit = {"holding_type": "equity", "isin": "INE0NDH25011", "industry": "Realty", "pct_nav": 3.0, "instrument": "Some REIT"}
        invit = {"holding_type": "equity", "isin": "INE0NR623014", "industry": "Transport Infrastructure", "pct_nav": 2.0, "instrument": "Some InvIT"}
        share = {"holding_type": "equity", "isin": "INE090A01021", "industry": "Banks", "pct_nav": 2.0, "instrument": "Some Bank"}
        bond = {"holding_type": "debt", "isin": "INE0KXY07059", "industry": "CRISIL AAA", "pct_nav": 1.0, "instrument": "Some Trust NCD"}
        self.assertEqual(compute.classify(reit, self.cap)[0], "reit")
        self.assertEqual(compute.classify(invit, self.cap)[0], "reit")
        self.assertEqual(compute.classify(share, self.cap)[0], "large")
        self.assertEqual(compute.classify(bond, self.cap)[0], "debt")   # a trust's bond is debt, not a unit

    def test_nine_buckets_with_reit_last(self):
        self.assertEqual(len(compute.BUCKETS), 9)
        self.assertEqual(compute.BUCKETS[-1], "reit")


if __name__ == "__main__":
    unittest.main()
