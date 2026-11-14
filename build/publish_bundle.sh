#!/usr/bin/env bash
# Publish the screener bundle: check it, then replace the single copy on the `screener-data` branch.
#
# Why a branch that is overwritten: the phone downloads two files (about 6 MB) from a fixed URL. Keeping them in the main
# history would add a full copy every month for ever; a release attachment is replaced but needs release rights. Here the
# branch always holds exactly ONE commit with the current files: each run makes a fresh root commit and force-pushes it,
# so old copies become unreachable and GitHub discards them. Nothing else lives on this branch.
#
# Usage: build/publish_bundle.sh DIR      (DIR holds screener.json.gz and navd.json.gz; needs GH_TOKEN and GITHUB_REPOSITORY)
set -euo pipefail
DIR="$1"; BRANCH=screener-data
RAW="https://raw.githubusercontent.com/${GITHUB_REPOSITORY}/${BRANCH}"

# the copy that is live now, to compare against (absent on the very first run)
PREV=$(mktemp -d)
if curl -sfL -o "$PREV/screener.json.gz" "$RAW/screener.json.gz"; then
  python3 build/validate_bundle.py "$DIR" "$PREV"
else
  echo "no live copy yet"; python3 build/validate_bundle.py "$DIR"
fi

W=$(mktemp -d)
cp "$DIR/screener.json.gz" "$DIR/navd.json.gz" "$W/"
cd "$W"
git init -q -b "$BRANCH"
git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
git add screener.json.gz navd.json.gz
git commit -q -m "Screener bundle $(date -u +%Y-%m-%d)"
git push -q --force "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" "$BRANCH"
echo "published to $BRANCH"
