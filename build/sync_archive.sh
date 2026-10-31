#!/usr/bin/env bash
# Fetch month-end portfolios from the open fund-holdings-data archive for months we do not have yet,
# then compute splits. Usage: build/sync_archive.sh
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf /tmp/arc
git clone -q --depth 1 --filter=blob:none --sparse https://github.com/kushagra-agarwal-a/fund-holdings-data /tmp/arc
cd /tmp/arc
want=()
for d in $(git ls-tree --name-only HEAD portfolios/asof/ | sed 's#portfolios/asof/##'); do
  day="${d:8:2}"
  case "$day" in 28|29|30|31) ;; *) continue;; esac
  [ -f "$OLDPWD/data/splits/${d:0:7}.json" ] || want+=("portfolios/asof/$d")
done
if [ ${#want[@]} -eq 0 ]; then echo "nothing new"; exit 0; fi
git sparse-checkout set "${want[@]}"
cd "$OLDPWD"
python3 build/compute.py /tmp/arc/portfolios data
