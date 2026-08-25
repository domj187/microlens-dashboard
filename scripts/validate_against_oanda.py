#!/usr/bin/env python3
"""Regression check for the timeframe-aggregation convention in fetch_dukascopy.py.

Aggregates the OANDA 1H exports in the repo root into 4H/8H/Daily using the
17:00 America/New_York session anchoring and compares the result against the
actual OANDA 4H/8H/Daily exports. Only buckets fully covered by the 1H data
are compared. Expected output: 0 mismatches everywhere (OANDA's own feed has
one daily high off by 0.00001 on AUDCHF 2026-08-20, which is reported but
tolerated).
"""

import csv
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_dukascopy import aggregate, aggregate_daily, NY, LON  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOLERANCE = 2e-5  # one pipette of slack for OANDA feed rounding artifacts


def load(path):
    with open(path, newline="") as f:
        return [
            (datetime.fromisoformat(r["time"]), float(r["open"]), float(r["high"]),
             float(r["low"]), float(r["close"]))
            for r in csv.DictReader(f)
        ]


def main():
    failures = 0
    for pair in ["AUDCHF", "AUDUSD", "EURCHF", "EURUSD"]:
        h1 = load(os.path.join(REPO_ROOT, f"OANDA_{pair}, 60.csv"))
        t0, t1 = h1[0][0], h1[-1][0]

        for tf, hours in [("240", 4), ("480", 8)]:
            ref = {t: v for t, *v in load(os.path.join(REPO_ROOT, f"OANDA_{pair}, {tf}.csv"))}
            ok = bad = 0
            for key, *v in aggregate(h1, hours):
                if key < t0 or key + timedelta(hours=hours) > t1 + timedelta(hours=1):
                    continue  # partial bucket at the edge of the 1H data
                r = ref.get(key.astimezone(LON))
                if r and all(abs(a - b) <= TOLERANCE for a, b in zip(v, r)):
                    ok += 1
                else:
                    bad += 1
                    print(f"  MISMATCH {pair} {tf} {key.astimezone(LON)}: got {v}, ref {r}")
            print(f"{pair} {tf}: {ok} match, {bad} mismatch")
            failures += bad

        refd = {t.date(): v for t, *v in load(os.path.join(REPO_ROOT, f"OANDA_{pair}, 1D.csv"))}
        ok = bad = 0
        for key, *v in aggregate_daily(h1):
            ss = datetime(key.year, key.month, key.day, 17, tzinfo=NY) - timedelta(days=1)
            if ss < t0 or ss + timedelta(hours=24) > t1 + timedelta(hours=1):
                continue
            r = refd.get(key)
            if r and all(abs(a - b) <= TOLERANCE for a, b in zip(v, r)):
                ok += 1
            else:
                bad += 1
                print(f"  MISMATCH {pair} 1D {key}: got {v}, ref {r}")
        print(f"{pair} 1D: {ok} match, {bad} mismatch")
        failures += bad

    print("PASS" if failures == 0 else f"FAIL ({failures} mismatches)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
