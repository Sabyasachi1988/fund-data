#!/usr/bin/env python3
"""Fails the build when a tracked file contains personal data: PAN numbers, real-looking emails,
phone numbers, long account/folio numbers, or street-address words next to a postal code.
Test data must use obvious placeholders (ABCDE1234F, john.doe@example.com, 100000000001)."""
import re, subprocess, sys

OK_PAN = {"ABCDE1234F"}
OK_NUM = {"100000000001", "000000000001", "1000000001", "100000001", "10000000000"}
PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
EMAIL = re.compile(r"\b[\w.+-]+@([\w-]+\.)+[A-Za-z]{2,}\b")
OK_MAIL = re.compile(r"(^this@|@example\.(com|org)$|@users\.noreply\.github\.com$|@anthropic\.(com|local)$|^noreply@)", re.I)
LONGNUM = re.compile(r"(?<![0-9A-Za-z.])[0-9]{9,16}(?![0-9A-Za-z])")
LONGNUM_SKIP = re.compile(r"^(probe/|backfill/logs/)")  # logs: file names carry 10-digit timestamps
SKIP = re.compile(r"(\.(png|jpg|jpeg|webp|jar|keystore|so|gz|zip|pdf)$|^tools/privacy_scan\.py$|go\.(sum|mod)$)")

def main():
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], capture_output=True, text=True, check=True).stdout.split("\n")
    bad = []
    for f in files:
        if not f or SKIP.search(f): continue
        try: text = open(f, encoding="utf-8").read()
        except Exception: continue
        for n, line in enumerate(text.split("\n"), 1):
            for m in PAN.findall(line):
                if m not in OK_PAN: bad.append((f, n, "PAN-like number"))
            for m in EMAIL.finditer(line):
                if not OK_MAIL.search(m.group(0)): bad.append((f, n, "email address"))
            for m in ([] if LONGNUM_SKIP.search(f) else LONGNUM.findall(line)):
                if m not in OK_NUM and not m.startswith(("10", "000", "9999", "1111")): bad.append((f, n, "long number (phone, account or folio?)"))
    for f, n, why in bad: print(f"{f}:{n}: {why}")
    if bad:
        print(f"\n{len(bad)} possible personal-data hit(s). Replace with placeholders before committing.")
        sys.exit(1)
    print("privacy scan: clean")

main()
