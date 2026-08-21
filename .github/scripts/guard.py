#!/usr/bin/env python
"""Refuse to commit a scrape that came back smaller than the one it replaces.

A partial failure -- the site 500s on half the plants, a session drops, the
markup shifts -- still produces perfectly well-formed CSVs, just short ones.
Committing those overwrites good data with a subset and the loss is silent.
So: the row count may grow (new filings) and may wobble down a little
(a correction, a withdrawn row), but a real collapse stops the run.

Fails the job on shrinkage past the tolerance; prints and passes otherwise.
"""
import subprocess
import sys

TOLERANCE = 0.95          # a new file below 95% of the old one is a failure
FILES = ["rms_monthly.csv", "rms_summary.csv"]


def rows_in_head(path):
    """Row count of the committed version, or None if it isn't in HEAD yet."""
    p = subprocess.run(["git", "show", f"HEAD:{path}"],
                       capture_output=True)
    if p.returncode != 0:
        return None
    return p.stdout.count(b"\n")


def rows_on_disk(path):
    with open(path, "rb") as fh:
        return fh.read().count(b"\n")


failed = False
for path in FILES:
    try:
        new = rows_on_disk(path)
    except FileNotFoundError:
        print(f"FAIL {path}: the scrape did not write it at all")
        failed = True
        continue

    old = rows_in_head(path)
    if old is None:
        print(f"ok   {path}: {new:,} rows (new file, nothing to compare)")
        continue

    floor = int(old * TOLERANCE)
    verdict = "ok  " if new >= floor else "FAIL"
    print(f"{verdict} {path}: {new:,} rows against {old:,} committed "
          f"({new / old:.1%}, floor {floor:,})")
    if new < floor:
        failed = True

if failed:
    print("\nThe scrape came back short. Not committing -- the data in the "
          "repo is better than what this run produced. Check whether "
          "rmsdoed.gov.np is up and whether its markup has changed.")
    sys.exit(1)

print("\nAll files at or above the floor.")
