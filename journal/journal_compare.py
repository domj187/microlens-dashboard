#!/usr/bin/env python3
"""Compare a live trade journal against a backtester run.

Matches journal rows to backtest trades on pair + entry time (within
--tolerance-hours) and reports where you and the system agreed, where you
traded alone, and which signals you passed on — with the R attached to
each bucket.

Journal rows you skipped (taken=no) carry their hypothetical outcome in
`result` as skipped-would-win / -loss / -scratch, so passed setups stay
measurable.

Stdlib only.  Usage:

  python3 journal/journal_compare.py \
      --journal journal/journal.csv \
      --trades backtest/results/origin-swing-partial/trades.csv
"""

from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime, timedelta, timezone

TF = "%Y-%m-%d %H:%M"


def parse_t(s: str):
    s = (s or "").strip()
    if not s:
        return None
    for f in (TF, "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def fnum(s):
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return 0.0


def truthy(s):
    return str(s).strip().lower() in ("yes", "y", "true", "1", "taken")


def stat(rows, key="r_multiple"):
    rs = [fnum(r.get(key)) for r in rows]
    n = len(rs)
    wins = sum(1 for r in rs if r > 0)
    decided = sum(1 for r in rs if r != 0)
    return {
        "n": n,
        "total_r": round(sum(rs), 2),
        "avg_r": round(sum(rs) / n, 3) if n else None,
        "wins": wins,
        "win_rate_pct": round(100.0 * wins / decided, 1) if decided else None,
    }


def line(label, s):
    return (f"  {label:<26}{s['n']:>4} trades   "
            f"win rate {str(s['win_rate_pct']) + '%' if s['win_rate_pct'] is not None else '—':>7}   "
            f"avg {str(s['avg_r']) if s['avg_r'] is not None else '—':>7}R   "
            f"total {s['total_r']:>7}R")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--journal", required=True, help="your journal CSV (see journal/README.md)")
    ap.add_argument("--trades", required=True, help="a backtester trades.csv to compare against")
    ap.add_argument("--tolerance-hours", type=float, default=6.0,
                    help="entry-time window for calling a journal row and a "
                         "backtest trade the same setup (default 6)")
    ap.add_argument("--out", default=None, help="write the matched rows here as CSV")
    args = ap.parse_args()

    with open(args.journal, newline="", encoding="utf-8-sig") as f:
        jrows = [r for r in csv.DictReader(f) if (r.get("pair") or "").strip()]
    with open(args.trades, newline="") as f:
        trows = [r for r in csv.DictReader(f)
                 if r["result"] in ("win", "loss", "scratch", "partial")]

    if not jrows:
        raise SystemExit(f"{args.journal}: no rows with a pair — nothing to compare")

    # only compare over the window the journal actually covers
    jt = [t for t in (parse_t(r.get("entry_date_utc")) or parse_t(r.get("signal_time_utc"))
                      for r in jrows) if t]
    if not jt:
        raise SystemExit(f"{args.journal}: no usable entry_date_utc / signal_time_utc values")
    lo, hi = min(jt), max(jt)
    tol = timedelta(hours=args.tolerance_hours)
    system = [r for r in trows if (parse_t(r["entry_date_utc"]) or lo) >= lo - tol
              and (parse_t(r["entry_date_utc"]) or hi) <= hi + tol]

    used = set()
    both, you_only = [], []
    for r in jrows:
        jtime = parse_t(r.get("entry_date_utc")) or parse_t(r.get("signal_time_utc"))
        match = None
        for i, s in enumerate(system):
            if i in used or s["pair"] != r["pair"].strip():
                continue
            st = parse_t(s["entry_date_utc"])
            if jtime and st and abs(st - jtime) <= tol:
                match = (i, s)
                break
        if match:
            used.add(match[0])
            both.append((r, match[1]))
        else:
            you_only.append(r)
    system_only = [s for i, s in enumerate(system) if i not in used]

    taken = [r for r in jrows if truthy(r.get("taken"))]
    skipped = [r for r in jrows if not truthy(r.get("taken"))]
    both_taken = [(j, s) for j, s in both if truthy(j.get("taken"))]
    both_skipped = [(j, s) for j, s in both if not truthy(j.get("taken"))]
    disagree_dir = [(j, s) for j, s in both_taken
                    if j["direction"].strip().lower() != s["direction"]]

    print(f"Journal : {args.journal}  ({len(jrows)} rows, "
          f"{lo:%Y-%m-%d} to {hi:%Y-%m-%d})")
    print(f"System  : {args.trades}  ({len(system)} trades in that window)\n")

    print("YOUR JOURNAL")
    print(line("taken", stat(taken)))
    print(line("skipped (hypothetical)", stat(skipped)))
    print()
    print("OVERLAP WITH THE SYSTEM")
    print(f"  both traded the setup     {len(both_taken)}")
    print(f"  system signalled, you passed  {len(both_skipped)}")
    print(f"  you traded, no system signal  {len(you_only)}")
    print(f"  system signalled, not in journal at all  {len(system_only)}")
    if disagree_dir:
        print(f"  !! direction disagreements: {len(disagree_dir)}")
        for j, s in disagree_dir[:5]:
            print(f"     {j['pair']} {j.get('entry_date_utc','')}: "
                  f"you {j['direction']} / system {s['direction']}")
    print()
    print("R COMPARISON")
    print(line("you, on shared setups", stat([j for j, _ in both_taken])))
    print(line("system, same setups", stat([s for _, s in both_taken])))
    print(line("you, discretionary only", stat(you_only)))
    print(line("passed-on signals", stat([s for _, s in both_skipped])))
    print(line("system, all signals", stat(system)))

    edge = stat([j for j, _ in both_taken])["total_r"] - stat([s for _, s in both_taken])["total_r"]
    print(f"\n  your execution vs the system on the same setups: "
          f"{edge:+.2f}R")
    passed = stat([s for _, s in both_skipped])["total_r"]
    print(f"  R left on the table by passing on signals:       {passed:+.2f}R")

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["bucket", "pair", "journal_entry", "system_entry", "direction",
                        "taken", "journal_result", "journal_r", "system_result",
                        "system_r", "reason"])
            for j, s in both:
                w.writerow(["both", j["pair"], j.get("entry_date_utc", ""),
                            s["entry_date_utc"], j["direction"], j.get("taken", ""),
                            j.get("result", ""), j.get("r_multiple", ""),
                            s["result"], s["r_multiple"], j.get("reason", "")])
            for j in you_only:
                w.writerow(["you_only", j["pair"], j.get("entry_date_utc", ""), "",
                            j["direction"], j.get("taken", ""), j.get("result", ""),
                            j.get("r_multiple", ""), "", "", j.get("reason", "")])
            for s in system_only:
                w.writerow(["system_only", s["pair"], "", s["entry_date_utc"],
                            s["direction"], "", "", "", s["result"], s["r_multiple"], ""])
        print(f"\n  matched rows written to {args.out}")


if __name__ == "__main__":
    main()
