#!/usr/bin/env python3
"""Split a backtest run into calendar sub-periods to see whether its edge is
spread across the window or concentrated in a few regimes.

For each period it reports trade count, wins/partials/losses, avg R, total R
and P&L, plus the underlying price move over the same period — so a run of
profit can be checked against whether the instrument was simply trending.

Trades are bucketed by ENTRY date (when the decision was made, which is what
attributes a result to a market regime), not by exit.

Stdlib only.  Usage:

  python3 backtest/period_breakdown.py \
      --trades backtest/results/partial-usdjpy/trades.csv --pair USDJPY
"""

from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime, timezone

from backtest import load_candles, pip_size, set_data_dir


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def price_move(pair, start, end):
    """(net pips, direction word) for the pair between two dates."""
    try:
        d1 = load_candles(pair, "1D")
    except FileNotFoundError:
        return None, ""
    inside = [c for c in d1 if start <= c.close_time.date() < end]
    if len(inside) < 2:
        return None, ""
    net = (inside[-1].c - inside[0].o) / pip_size(pair)
    word = "rising" if net > 0 else "falling"
    return net, word


def bucket_rows(rows, key):
    out = {}
    for r in rows:
        out.setdefault(key(r), []).append(r)
    return out


def stats(rows):
    rs = [num(r["r_multiple"]) for r in rows]
    n = len(rs)
    return {
        "n": n,
        "wins": sum(1 for r in rows if r["result"] == "win"),
        "partials": sum(1 for r in rows if r["result"] == "partial"),
        "losses": sum(1 for r in rows if r["result"] == "loss"),
        "avg_r": sum(rs) / n if n else None,
        "total_r": sum(rs),
        "pnl": sum(num(r["pnl"]) for r in rows),
    }


def report(title, buckets, order, pair, bounds):
    print(f"\n{title}")
    print(f"  {'period':<12}{'trades':>7}{'W/P/L':>10}{'avg R':>9}{'total R':>9}"
          f"{'P&L':>10}   {'price move':<22}")
    for k in order:
        s = stats(buckets.get(k, []))
        if not s["n"]:
            print(f"  {k:<12}{0:>7}{'-':>10}{'—':>9}{'—':>9}{'—':>10}")
            continue
        wpl = f"{s['wins']}/{s['partials']}/{s['losses']}"
        net, word = price_move(pair, *bounds[k])
        move = f"{net:+.0f}p {word}" if net is not None else ""
        print(f"  {k:<12}{s['n']:>7}{wpl:>10}{s['avg_r']:>+9.3f}{s['total_r']:>+9.1f}"
              f"{s['pnl']:>+10.0f}   {move:<22}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trades", required=True)
    ap.add_argument("--pair", default=None, help="restrict to one pair")
    ap.add_argument("--data-dir", default=None,
                    help="directory holding the candle CSVs, used for the "
                         "price-move column (default: data/)")
    cfg = ap.parse_args()
    if cfg.data_dir:
        set_data_dir(cfg.data_dir)

    with open(cfg.trades, newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if r["result"] in ("win", "loss", "partial", "scratch")
                and (not cfg.pair or r["pair"] == cfg.pair)]
    if not rows:
        raise SystemExit(f"no closed trades in {cfg.trades}"
                         + (f" for {cfg.pair}" if cfg.pair else ""))

    D = lambda r: datetime.strptime(r["entry_date_utc"], "%Y-%m-%d %H:%M").date()
    lo, hi = min(D(r) for r in rows), max(D(r) for r in rows)
    pair = cfg.pair or rows[0]["pair"]
    tot = stats(rows)
    print(f"{pair}  ·  {len(rows)} closed trades  ·  {lo} to {hi}")
    print(f"overall: avg R {tot['avg_r']:+.3f}  total {tot['total_r']:+.1f}R  "
          f"P&L {tot['pnl']:+.0f}")

    from datetime import date
    years = sorted({D(r).year for r in rows})
    ybounds = {str(y): (date(y, 1, 1), date(y + 1, 1, 1)) for y in years}
    report("BY CALENDAR YEAR", bucket_rows(rows, lambda r: str(D(r).year)),
           [str(y) for y in years], pair, ybounds)

    halves, hbounds = [], {}
    for y in years:
        for h in (1, 2):
            k = f"{y} H{h}"
            halves.append(k)
            hbounds[k] = ((date(y, 1, 1), date(y, 7, 1)) if h == 1
                          else (date(y, 7, 1), date(y + 1, 1, 1)))
    report("BY SIX-MONTH PERIOD",
           bucket_rows(rows, lambda r: f"{D(r).year} H{1 if D(r).month <= 6 else 2}"),
           halves, pair, hbounds)

    # concentration: how much of the total R comes from the best single period?
    hb = bucket_rows(rows, lambda r: f"{D(r).year} H{1 if D(r).month <= 6 else 2}")
    tr = [(k, stats(v)["total_r"]) for k, v in hb.items()]
    tr.sort(key=lambda kv: -kv[1])
    pos = sum(r for _, r in tr if r > 0)
    print(f"\nCONCENTRATION")
    print(f"  best half-year:      {tr[0][0]}  {tr[0][1]:+.1f}R "
          f"({100*tr[0][1]/tot['total_r']:.0f}% of the run's total R)" if tot["total_r"] else "")
    print(f"  profitable halves:   {sum(1 for _, r in tr if r > 0)} of {len(tr)}")
    print(f"  total R less the best half: {tot['total_r'] - tr[0][1]:+.1f}R "
          f"over {tot['n'] - stats(hb[tr[0][0]])['n']} trades")


if __name__ == "__main__":
    main()
