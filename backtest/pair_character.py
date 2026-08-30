#!/usr/bin/env python3
"""Measure the structural character of each pair — independent of any strategy.

For every pair with data in data/, this reports:

  1. 4H swing size — the median/mean distance of a completed swing leg
     (confirmed fractal high to the next confirmed fractal low, or the
     reverse), in pips and as a multiple of 4H ATR(14).
  2. BOS follow-through — after a 4H close breaks the last unbroken swing,
     does price go on to travel 2x the depth of the broken swing leg
     (follow-through) before trading back through the swing the leg came
     from (reversal)? Reported as a share of all breaks, and against the
     33.3% a driftless random walk would score: the target sits 2x depth
     away while invalidation sits 1x depth away, so 1-in-3 is the
     no-edge baseline (and, not coincidentally, the breakeven rate of a
     1:2 target stopped at the origin swing).
  3. Retest availability — how often the broken level is revisited within
     --retest-window 1H bars, i.e. how often a retest entry is even offered.
  4. Daily trend persistence — how many daily bars a directional market
     structure state survives before it flips the other way.
  5. Typical spread — only when bid and ask files are present (see
     --bid-dir / --ask-dir); mid-only data cannot show a spread.

Then it ranks the pairs on how well they suit a structure-break-and-retest
system and shows the components behind each score.

Stdlib only.  Usage:

  python3 backtest/pair_character.py
  python3 backtest/pair_character.py --pairs EURUSD GBPJPY --json out.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics as stats
from datetime import timedelta

from backtest import load_candles, is_swing, pip_size, data_dir, set_data_dir


def discover_pairs() -> list[str]:
    out = set()
    for p in glob.glob(os.path.join(data_dir(), "*_240.csv")):
        name = os.path.basename(p)[:-len("_240.csv")]
        if all(os.path.exists(os.path.join(data_dir(), f"{name}_{tf}.csv"))
               for tf in ("60", "240", "480", "1D")):
            out.add(name)
    return sorted(out)


def atr(candles, period=14):
    """True-range average ending at each index (None until enough history)."""
    trs, out = [], [None] * len(candles)
    for i, c in enumerate(candles):
        tr = c.h - c.l if i == 0 else max(c.h - c.l, abs(c.h - candles[i-1].c),
                                          abs(c.l - candles[i-1].c))
        trs.append(tr)
        if i >= period:
            out[i] = sum(trs[i-period+1:i+1]) / period
    return out


def confirmed_swings(candles, n):
    """[(confirm_index, bar_index, 'H'|'L', price)] in confirmation order."""
    out = []
    for i in range(len(candles)):
        j = i - n
        if j < 0:
            continue
        if is_swing(candles, j, n, "H"):
            out.append((i, j, "H", candles[j].h))
        if is_swing(candles, j, n, "L"):
            out.append((i, j, "L", candles[j].l))
    return out


def analyse(pair: str, cfg) -> dict:
    h4 = load_candles(pair, "240")
    h1 = load_candles(pair, "60")
    d1 = load_candles(pair, "1D")
    pip = pip_size(pair)
    a4 = atr(h4, cfg.atr_period)
    swings = confirmed_swings(h4, cfg.swing_n)

    # ---- 1) swing legs: alternating H -> L -> H ...
    legs, leg_atr = [], []
    last = None
    for ci, bi, kind, price in swings:
        if last is None:
            last = (ci, bi, kind, price)
            continue
        if kind == last[2]:
            # same side: keep the more extreme one
            if (kind == "H" and price > last[3]) or (kind == "L" and price < last[3]):
                last = (ci, bi, kind, price)
            continue
        depth = abs(price - last[3])
        legs.append(depth / pip)
        if a4[ci] and a4[ci] > 0:
            leg_atr.append(depth / a4[ci])
        last = (ci, bi, kind, price)

    # ---- 2/3) breaks of structure: follow-through, reversal, retest
    ft = rev = unresolved = 0
    retested = 0
    h1_times = [c.close_time for c in h1]
    import bisect
    highs, lows = [], []          # confirmed swings with broken flags
    si = 0
    for i, c in enumerate(h4):
        while si < len(swings) and swings[si][0] <= i:
            _, bi, kind, price = swings[si]
            (highs if kind == "H" else lows).append([bi, price, False])
            si += 1
        # most recent unbroken level on each side
        uh = next((s for s in reversed(highs) if not s[2]), None)
        ul = next((s for s in reversed(lows) if not s[2]), None)
        for s in highs:
            if not s[2] and c.c > s[1]:
                s[2] = True
        for s in lows:
            if not s[2] and c.c < s[1]:
                s[2] = True
        brk = None
        if uh and c.c > uh[1]:
            origin = next((s for s in reversed(lows) if s[0] < uh[0]), None)
            if origin and origin[1] < uh[1]:
                brk = ("up", uh[1], origin[1])
        elif ul and c.c < ul[1]:
            origin = next((s for s in reversed(highs) if s[0] < ul[0]), None)
            if origin and origin[1] > ul[1]:
                brk = ("down", ul[1], origin[1])
        if not brk:
            continue
        side, level, origin_px = brk
        depth = abs(level - origin_px)
        if depth <= 0:
            continue
        target = level + 2 * depth if side == "up" else level - 2 * depth
        resolved = False
        for f in h4[i+1:]:
            hit_t = f.h >= target if side == "up" else f.l <= target
            hit_r = f.l <= origin_px if side == "up" else f.h >= origin_px
            if hit_r and hit_t:      # same bar: conservative -> reversal
                rev += 1; resolved = True; break
            if hit_r:
                rev += 1; resolved = True; break
            if hit_t:
                ft += 1; resolved = True; break
        if not resolved:
            unresolved += 1
        # retest offered within the window?
        k = bisect.bisect_right(h1_times, c.close_time)
        for f in h1[k:k + cfg.retest_window]:
            if (f.l <= level) if side == "up" else (f.h >= level):
                retested += 1
                break

    # ---- 4) daily trend persistence
    dsw = confirmed_swings(d1, cfg.swing_n)
    dhi = dlo = None
    state = None
    runs, run = [], 0
    di = 0
    for i, c in enumerate(d1):
        while di < len(dsw) and dsw[di][0] <= i:
            _, _, kind, price = dsw[di]
            if kind == "H":
                dhi = price
            else:
                dlo = price
            di += 1
        new = state
        if dhi is not None and c.c > dhi:
            new = "bull"
        elif dlo is not None and c.c < dlo:
            new = "bear"
        if state is not None and new != state:
            runs.append(run)
            run = 0
        state = new
        if state is not None:
            run += 1
    if run:
        runs.append(run)

    breaks = ft + rev + unresolved
    res = {
        "pair": pair,
        "bars": {"h1": len(h1), "h4": len(h4), "d1": len(d1)},
        "range": [str(h4[0].open_time.date()), str(h4[-1].close_time.date())],
        "swing_legs": len(legs),
        "swing_median_pips": round(stats.median(legs), 1) if legs else None,
        "swing_mean_pips": round(stats.fmean(legs), 1) if legs else None,
        "swing_atr_median": round(stats.median(leg_atr), 2) if leg_atr else None,
        "breaks": breaks,
        "followthrough_pct": round(100 * ft / breaks, 1) if breaks else None,
        "reversal_pct": round(100 * rev / breaks, 1) if breaks else None,
        "unresolved_pct": round(100 * unresolved / breaks, 1) if breaks else None,
        "ft_to_rev": round(ft / rev, 2) if rev else None,
        "edge_vs_random_pp": round(100 * ft / breaks - 100.0 / 3, 1) if breaks else None,
        "retest_pct": round(100 * retested / breaks, 1) if breaks else None,
        "daily_runs": len(runs),
        "daily_persistence_median": round(stats.median(runs), 1) if runs else None,
        "daily_persistence_mean": round(stats.fmean(runs), 1) if runs else None,
        "spread_pips": None,
        "spread_pct_of_swing": None,
    }

    # ---- 5) spread, only if bid and ask files were supplied
    if cfg.bid_dir and cfg.ask_dir:
        bp = os.path.join(cfg.bid_dir, f"{pair}_60.csv")
        ap = os.path.join(cfg.ask_dir, f"{pair}_60.csv")
        if os.path.exists(bp) and os.path.exists(ap):
            import csv as _csv
            bid = {r["time"]: float(r["close"]) for r in _csv.DictReader(open(bp))}
            ask = {r["time"]: float(r["close"]) for r in _csv.DictReader(open(ap))}
            sp = [(ask[t] - bid[t]) / pip for t in bid.keys() & ask.keys()]
            sp = [s for s in sp if s >= 0]
            if sp:
                res["spread_pips"] = round(stats.median(sp), 2)
                if res["swing_median_pips"]:
                    res["spread_pct_of_swing"] = round(
                        100 * res["spread_pips"] / res["swing_median_pips"], 2)
    return res


def rank(results: list[dict]) -> list[dict]:
    """Score each pair on the properties a break-and-retest system needs."""
    def norm(vals):
        lo, hi = min(vals), max(vals)
        return [0.5] * len(vals) if hi == lo else [(v - lo) / (hi - lo) for v in vals]

    ok = [r for r in results if r["followthrough_pct"] is not None]
    if not ok:
        return results
    comps = {
        "follow_through": ([r["edge_vs_random_pp"] for r in ok], 0.40),
        "retest_offered": ([r["retest_pct"] for r in ok], 0.20),
        "trend_persistence": ([r["daily_persistence_median"] or 0 for r in ok], 0.20),
        "swing_size": ([r["swing_median_pips"] or 0 for r in ok], 0.20),
    }
    scores = {r["pair"]: 0.0 for r in ok}
    parts = {r["pair"]: {} for r in ok}
    for name, (vals, w) in comps.items():
        for r, n in zip(ok, norm(vals)):
            scores[r["pair"]] += w * n
            parts[r["pair"]][name] = round(n, 2)
    # spread only participates when every pair has it
    if all(r["spread_pct_of_swing"] is not None for r in ok):
        for r, n in zip(ok, norm([-r["spread_pct_of_swing"] for r in ok])):
            scores[r["pair"]] += 0.15 * n
            parts[r["pair"]]["cost_efficiency"] = round(n, 2)
    for r in ok:
        r["score"] = round(scores[r["pair"]], 3)
        r["score_parts"] = parts[r["pair"]]
    return sorted(results, key=lambda r: r.get("score", -1), reverse=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pairs", nargs="+", default=None,
                    help="default: every pair with a full set of files in data/")
    ap.add_argument("--swing-n", type=int, default=2, help="fractal width (default 2)")
    ap.add_argument("--atr-period", type=int, default=14)
    ap.add_argument("--retest-window", type=int, default=24,
                    help="1H bars a retest may take to arrive (default 24)")
    ap.add_argument("--bid-dir", default=None, help="dir of bid-priced CSVs, for spread")
    ap.add_argument("--ask-dir", default=None, help="dir of ask-priced CSVs, for spread")
    ap.add_argument("--data-dir", default=None,
                    help="directory holding the candle CSVs (default: data/)")
    ap.add_argument("--json", default=None, help="also write the full results here")
    cfg = ap.parse_args()
    if cfg.data_dir:
        set_data_dir(cfg.data_dir)

    pairs = cfg.pairs or discover_pairs()
    if not pairs:
        raise SystemExit(f"no complete pair datasets found in {data_dir()}")
    results = rank([analyse(p, cfg) for p in pairs])

    r0 = results[0]
    print(f"Data range: {r0['range'][0]} to {r0['range'][1]}   "
          f"({len(pairs)} pairs, 4H fractal n={cfg.swing_n})\n")

    def f(v, w, dec=1, suffix=""):
        return ("—" if v is None else f"{v:.{dec}f}{suffix}").rjust(w)

    print("4H SWING STRUCTURE")
    print(f"  {'pair':<9}{'legs':>6}{'median':>9}{'mean':>8}{'x ATR':>8}")
    for r in results:
        print(f"  {r['pair']:<9}{r['swing_legs']:>6}{f(r['swing_median_pips'],9,1,'p')}"
              f"{f(r['swing_mean_pips'],8,1,'p')}{f(r['swing_atr_median'],8,2)}")

    print("\nBREAK OF STRUCTURE — what happens next")
    print(f"  {'pair':<9}{'breaks':>7}{'2x depth':>10}{'reversal':>10}{'open':>7}"
          f"{'vs 33.3%':>10}{'retest':>8}")
    for r in results:
        print(f"  {r['pair']:<9}{r['breaks']:>7}{f(r['followthrough_pct'],10,1,'%')}"
              f"{f(r['reversal_pct'],10,1,'%')}{f(r['unresolved_pct'],7,1,'%')}"
              f"{('—' if r['edge_vs_random_pp'] is None else format(r['edge_vs_random_pp'],'+.1f')+'p').rjust(10)}"
              f"{f(r['retest_pct'],8,1,'%')}")
    print("  (33.3% is the no-edge baseline: target 2x depth away, invalidation 1x away)")

    print("\nDAILY TREND PERSISTENCE (bars before the state flips)")
    print(f"  {'pair':<9}{'flips':>7}{'median':>9}{'mean':>8}")
    for r in results:
        print(f"  {r['pair']:<9}{r['daily_runs']:>7}{f(r['daily_persistence_median'],9,1,'d')}"
              f"{f(r['daily_persistence_mean'],8,1,'d')}")

    print("\nSPREAD")
    if any(r["spread_pips"] is not None for r in results):
        for r in results:
            if r["spread_pips"] is None:
                print(f"  {r['pair']:<9}      —   no bid/ask files found")
            else:
                pct = ("" if r["spread_pct_of_swing"] is None
                       else f"   {r['spread_pct_of_swing']}% of a median swing")
                print(f"  {r['pair']:<9}{r['spread_pips']:>7.2f}p{pct}")
    else:
        print("  not measurable — data/ holds mid prices only. Rebuild bid and ask")
        print("  sets (scripts/fetch_dukascopy.py --price bid|ask) and pass")
        print("  --bid-dir/--ask-dir to include spread in the ranking.")

    print("\nRANKING for a structure-break-and-retest system")
    for i, r in enumerate(results, 1):
        if "score" not in r:
            continue
        parts = " · ".join(f"{k} {v}" for k, v in r["score_parts"].items())
        print(f"  {i}. {r['pair']:<9}score {r['score']:.3f}   [{parts}]")

    if cfg.json:
        with open(cfg.json, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nfull results -> {cfg.json}")


if __name__ == "__main__":
    main()
