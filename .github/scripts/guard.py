#!/usr/bin/env python
"""Refuse to commit a scrape that lost something the committed one had.

Two checks, because they catch different failures.

VANISHED KEYS is the sharp one: every plant-year in the committed data must
still be present, and every plant in the register must still be there. A
row-count floor cannot see this. At 14,142 rows a 95% floor tolerates 707
missing rows, which is about twelve entire plant histories -- so a handful of
plants could evaporate on a bad run and the job would go green. Growth is
fine and expected; disappearance is not, and it is never routine.

SHRINKAGE is the coarse net, kept for the failures that change a file's shape
rather than its keys: a truncated write, a parse that collapses every row.

Deliberately checks the CLEANED files. The raw summary carries a placeholder
row for every plant-year with no filing, and how those are recorded has
already changed once, so counting them compares formats rather than data.

Fails the job on either; prints and passes otherwise.
"""
import io
import subprocess
import sys

import pandas as pd

TOLERANCE = 0.95
# file -> columns whose combined value identifies a row that must not disappear
KEYED = {
    "rms_monthly_clean.csv": ["PlantName", "FiscalYear"],
    "rms_summary_clean.csv": ["PlantId", "FiscalId"],
    "rms_plants_meta.csv":   ["UrlId"],
}


def committed(path):
    """The version in HEAD, or None if it isn't committed yet."""
    p = subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True)
    return p.stdout if p.returncode == 0 else None


def keys_of(raw, cols):
    df = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False, low_memory=False)
    if not set(cols) <= set(df.columns):
        return None
    return set(zip(*[df[c] for c in cols]))


failed = False
for path, cols in KEYED.items():
    old_raw = committed(path)
    try:
        with open(path, "rb") as fh:
            new_raw = fh.read()
    except FileNotFoundError:
        print(f"FAIL {path}: the scrape did not write it at all")
        failed = True
        continue

    if old_raw is None:
        print(f"ok   {path}: new file, nothing to compare")
        continue

    # --- keys present before must still be present ---------------------------
    old_keys, new_keys = keys_of(old_raw, cols), keys_of(new_raw, cols)
    if old_keys is None or new_keys is None:
        print(f"FAIL {path}: expected columns {cols} are missing")
        failed = True
        continue

    gone = old_keys - new_keys
    added = new_keys - old_keys
    label = "+".join(cols)
    if gone:
        print(f"FAIL {path}: {len(gone)} {label} disappeared, e.g. "
              + ", ".join(" ".join(k) for k in sorted(gone)[:4]))
        failed = True
    else:
        print(f"ok   {path}: {len(new_keys):,} {label} present, none lost"
              + (f", {len(added)} new" if added else ""))

    # --- and the file must not have collapsed in size ------------------------
    old_n, new_n = old_raw.count(b"\n"), new_raw.count(b"\n")
    floor = int(old_n * TOLERANCE)
    if new_n < floor:
        print(f"FAIL {path}: {new_n:,} rows against {old_n:,} committed "
              f"({new_n / old_n:.1%}, floor {floor:,})")
        failed = True

if failed:
    print("\nNot committing. The data in the repo is better than what this run "
          "produced. A row that exists must not vanish because a request failed; "
          "check whether the source is up and whether its markup has changed, "
          "then re-run a full scrape before committing anything.")
    sys.exit(1)

print("\nNothing lost.")
