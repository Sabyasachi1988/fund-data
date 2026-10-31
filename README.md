# fund-data

Public, free data for a personal portfolio app. No personal data lives here.

For every Indian mutual fund portfolio and month-end: how much sits in large / mid / small cap stocks, foreign equity, debt, cash and other, plus the sector split.

- `data/splits/YYYY-MM.json`: per fund `[large, mid, small, unclassified, foreign, debt, cash, other]` in % of NAV.
- `data/sectors/YYYY-MM.json`: per fund `{sector_key: % of NAV}`; names in `data/sector_names.json`.
- `data/catalog.json`: fund portfolio id -> name, AMC, member AMFI scheme codes.
- `amfi/`: AMFI half-yearly large/mid/small lists (six months ended Jun/Dec).

Method: each stock's cap tag comes from the AMFI list in force that month (Jan-Jun uses the list for the six months ended the previous 31 Dec; Jul-Dec uses the list ended 30 Jun). This timing rule is my reading of how AMFI applies the lists and still needs to be checked against AMFI's own notice.

Source of holdings: the open fund-holdings-data archive (parsed AMC disclosures). Run monthly by `.github/workflows/monthly.yml`.
