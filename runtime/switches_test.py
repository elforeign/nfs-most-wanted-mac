#!/usr/bin/env python3
"""Keep the environment switch registry in sync with runtime code."""
# Proof: 291 code names match switches.tsv (PASS); deleting BOOT_AUTODRIVE makes it FAIL.
import glob
import re
import sys


def check(table_path):
    code = set()
    for path in glob.glob("runtime/*.c") + glob.glob("runtime/*.m") + glob.glob("runtime/*.cpp"):
        with open(path, encoding="utf-8") as source:
            code.update(re.findall(r'"((?:NFS|BOOT)_[A-Z0-9_]+)', source.read()))
    with open(table_path, encoding="utf-8") as table:
        rows = [line.split("\t", 1)[0] for line in table if line.strip() and not line.startswith("#")]
    registered = set(rows)
    missing = sorted(code - registered)
    stale = sorted(registered - code)
    duplicates = sorted(name for name in registered if rows.count(name) > 1)
    if missing or stale or duplicates:
        print(f"switch registry FAIL: missing={missing[:5]} stale={stale[:5]} duplicate={duplicates[:5]}")
        return 1
    print(f"switch registry PASS ({len(code)} names)")
    return 0


if __name__ == "__main__":
    path = "runtime/switches.tsv"
    if len(sys.argv) == 3 and sys.argv[1] == "--table":
        path = sys.argv[2]
    elif len(sys.argv) != 1:
        raise SystemExit("usage: switches_test.py [--table path]")
    raise SystemExit(check(path))
