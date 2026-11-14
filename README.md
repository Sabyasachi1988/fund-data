# fund-data

Public, free data for a personal portfolio app. No personal data lives here.

For every Indian mutual fund portfolio and month-end: how much sits in large / mid / small cap stocks, foreign equity, debt, cash and other, plus the sector split.

- `data/splits/YYYY-MM.json`: per fund `[large, mid, small, unclassified, foreign, debt, cash, other]` in % of NAV.
- `data/sectors/YYYY-MM.json`: per fund `{sector_key: % of NAV}`; names in `data/sector_names.json`.
- `data/catalog.json`: fund portfolio id -> name, AMC, member AMFI scheme codes.
- `amfi/`: AMFI half-yearly large/mid/small lists (six months ended Jun/Dec).

Method: each stock's cap tag comes from the AMFI list in force that month (Jan-Jun uses the list for the six months ended the previous 31 Dec; Jul-Dec uses the list ended 30 Jun). This timing rule is my reading of how AMFI applies the lists and still needs to be checked against AMFI's own notice.

Source of holdings: the open fund-holdings-data archive (parsed AMC disclosures). Run monthly by `.github/workflows/monthly.yml`.


## Screener bundle (what the phone downloads)

`screener.json.gz` (one row per open-ended Direct-Growth fund: returns, risk, composition, expense ratio, size) and `navd.json.gz` (daily NAVs) live on the **`screener-data` branch**, which always holds exactly one commit: each run force-pushes a fresh one, so old copies are dropped and storage stays at about 6 MB. The app downloads them from `raw.githubusercontent.com/Sabyasachi1988/fund-data/screener-data/`.

Monthly job (`screener.yml`, 20th): unit tests -> `build/screener.py` (NAV histories, about 11 min) -> `build/enrich.py` (expense ratio, size, risk) -> `build/validate_bundle.py` (refuses a bundle that looks broken, leaving the live one alone) -> publish -> save `data/ter_cache.json`.

Expense ratios change rarely, so `enrich.py` keeps the last result in `data/ter_cache.json` and fetches only the month in progress from AMFI (about 2 min). The previous month's full table (about 650 pages, nearly an hour) is only fetched when there is no cache. `build/seed_ter_cache.py` fills the cache from a published bundle.

Composition split per fund: `[large, mid, small, other Indian equity, foreign, debt, cash, other, REIT/InvIT]` in % of NAV (older months have the first eight). REIT and InvIT units are told apart by their ISIN security-type code (characters 8-9: 23 = InvIT, 25 = REIT), not by name.
