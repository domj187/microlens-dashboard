#!/usr/bin/env python3
"""Multi-timeframe BOS/retest strategy backtester.

Strategy (as specified):
  - Directional bias: Daily AND 8H market structure must align (both bullish
    -> longs only; both bearish -> shorts only; otherwise stand aside).
  - 4H defines structure: fractal swing highs/lows on the 4H chart.
  - Entry trigger: a 1H candle CLOSE beyond the most recent confirmed,
    still-unbroken 4H swing level in the direction of bias (break of
    structure, confirmed by candle close).
  - Entry: limit order at the broken 4H level, filled on the retest.
  - Stop loss: just beyond the 4H swing of the BOS leg (see --sl-mode).
  - Take profit: fixed 1:2 RR from actual entry. No exceptions.
  - Risk: 1% of current (booked) equity per trade. One position per pair.

No look-ahead:
  - A higher-timeframe candle is only visible once its CLOSE time is <= the
    close time of the current 1H candle. Daily candles close 17:00 New York
    (validated against the OANDA exports, see data/README.md); 4H/8H candles
    close open_time + duration.
  - A fractal swing (N bars each side) only becomes usable N bars after the
    swing bar, at the close of the confirming candle.

Conservative fills on 1H OHLC (intrabar path unknown):
  - If a candle's range covers both SL and TP, the trade is counted as a LOSS
    and flagged `ambiguous` in the trade list.
  - On the entry candle itself, TP is only granted if the candle CLOSES beyond
    TP (the touch of the entry level may have happened after the high/low).

Prices are Dukascopy mid quotes; spread/commission/slippage are not modeled.
P&L is applied in R-multiples (-1R = -1% of equity at entry, +2R = +2%), which
also sidesteps quote-currency conversion for the CHF-quoted pairs.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(HERE), "data")
NY = ZoneInfo("America/New_York")

PAIRS = ["AUDCHF", "AUDUSD", "EURCHF", "EURUSD"]
PIP = 0.0001  # all four pairs are 5-decimal quotes


# ---------------------------------------------------------------- data model

@dataclass(frozen=True)
class Candle:
    open_time: datetime   # UTC
    close_time: datetime  # UTC
    o: float
    h: float
    l: float
    c: float


@dataclass
class Swing:
    kind: str            # "H" or "L"
    bar_time: datetime   # open time of the swing bar (UTC)
    price: float
    confirm_time: datetime  # close time of the confirming bar (UTC)
    broken: bool = False


@dataclass
class Setup:
    direction: str       # "long" / "short"
    level: float         # broken 4H level = limit entry price
    sl: float
    bos_time: datetime
    bars_left: int


@dataclass
class Trade:
    pair: str
    direction: str
    bos_time: datetime
    entry_time: datetime
    entry: float
    sl: float
    tp: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    result: str | None = None      # "win" / "loss" / "open"
    r_multiple: float = 0.0
    ambiguous: bool = False
    # filled in by the portfolio pass:
    risk_amount: float = 0.0
    units: float = 0.0
    pnl: float = 0.0
    equity_after: float = 0.0


# ---------------------------------------------------------------- CSV loading

def load_candles(pair: str, tf: str) -> list[Candle]:
    path = os.path.join(DATA_DIR, f"{pair}_{tf}.csv")
    out: list[Candle] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
            if tf == "1D":
                # Daily candles are labeled with the close date of a
                # 17:00->17:00 New York session.
                d = datetime.strptime(row["time"], "%Y-%m-%d").date()
                close_t = datetime(d.year, d.month, d.day, 17, tzinfo=NY).astimezone(timezone.utc)
                open_t = close_t - timedelta(hours=24)
            else:
                open_t = datetime.fromisoformat(row["time"]).astimezone(timezone.utc)
                close_t = open_t + timedelta(minutes=int(tf))
            out.append(Candle(open_t, close_t, o, h, l, c))
    out.sort(key=lambda x: x.open_time)
    return out


# ------------------------------------------------------- swings & trend state

def is_swing(candles: list[Candle], i: int, n: int, kind: str) -> bool:
    if i - n < 0 or i + n >= len(candles):
        return False
    if kind == "H":
        v = candles[i].h
        return all(candles[j].h < v for j in range(i - n, i + n + 1) if j != i)
    v = candles[i].l
    return all(candles[j].l > v for j in range(i - n, i + n + 1) if j != i)


class TrendTracker:
    """Market-structure trend on one timeframe (used for Daily and 8H bias).

    Bullish once a candle closes above the most recent confirmed swing high;
    bearish once a candle closes below the most recent confirmed swing low.
    The state holds until broken the other way.
    """

    def __init__(self, candles: list[Candle], n: int):
        self.candles = candles
        self.n = n
        self.i = 0                      # next candle index to ingest
        self.last_high: Swing | None = None
        self.last_low: Swing | None = None
        self.state: str | None = None   # None / "bull" / "bear"

    def advance_to(self, t: datetime) -> None:
        """Ingest every candle whose close time is <= t."""
        cs = self.candles
        while self.i < len(cs) and cs[self.i].close_time <= t:
            i = self.i
            # confirm the swing that this candle (bar i) confirms: bar i - n
            j = i - self.n
            if j >= 0:
                if is_swing(cs, j, self.n, "H"):
                    self.last_high = Swing("H", cs[j].open_time, cs[j].h, cs[i].close_time)
                if is_swing(cs, j, self.n, "L"):
                    self.last_low = Swing("L", cs[j].open_time, cs[j].l, cs[i].close_time)
            c = cs[i]
            bull = self.last_high is not None and c.c > self.last_high.price
            bear = self.last_low is not None and c.c < self.last_low.price
            if bull and bear:  # freak candle breaking both: use candle direction
                self.state = "bull" if c.c >= c.o else "bear"
            elif bull:
                self.state = "bull"
            elif bear:
                self.state = "bear"
            self.i += 1


class StructureTracker:
    """Confirmed 4H fractal swings, with broken/unbroken bookkeeping."""

    def __init__(self, candles: list[Candle], n: int):
        self.candles = candles
        self.n = n
        self.i = 0
        self.highs: list[Swing] = []   # chronological by swing bar time
        self.lows: list[Swing] = []

    def advance_to(self, t: datetime) -> None:
        cs = self.candles
        while self.i < len(cs) and cs[self.i].close_time <= t:
            i = self.i
            j = i - self.n
            if j >= 0:
                if is_swing(cs, j, self.n, "H"):
                    self.highs.append(Swing("H", cs[j].open_time, cs[j].h, cs[i].close_time))
                if is_swing(cs, j, self.n, "L"):
                    self.lows.append(Swing("L", cs[j].open_time, cs[j].l, cs[i].close_time))
            self.i += 1

    @staticmethod
    def _latest_unbroken(swings: list[Swing]) -> Swing | None:
        for s in reversed(swings):
            if not s.broken:
                return s
        return None

    def latest_unbroken_high(self) -> Swing | None:
        return self._latest_unbroken(self.highs)

    def latest_unbroken_low(self) -> Swing | None:
        return self._latest_unbroken(self.lows)

    def latest_low(self) -> Swing | None:
        return self.lows[-1] if self.lows else None

    def latest_high(self) -> Swing | None:
        return self.highs[-1] if self.highs else None

    def mark_breaks(self, close: float) -> tuple[Swing | None, Swing | None]:
        """Mark swings broken by this 1H close.

        Returns (broken_high, broken_low): the most recent swing high newly
        broken upward / swing low newly broken downward on this close.
        """
        bh = bl = None
        for s in self.highs:
            if not s.broken and close > s.price:
                s.broken = True
                if bh is None or s.bar_time > bh.bar_time:
                    bh = s
        for s in self.lows:
            if not s.broken and close < s.price:
                s.broken = True
                if bl is None or s.bar_time > bl.bar_time:
                    bl = s
        return bh, bl


# ---------------------------------------------------------------- backtest

def run_pair(pair: str, cfg: argparse.Namespace) -> list[Trade]:
    h1 = load_candles(pair, "60")
    h4 = StructureTracker(load_candles(pair, "240"), cfg.swing_n)
    h8 = TrendTracker(load_candles(pair, "480"), cfg.swing_n)
    d1 = TrendTracker(load_candles(pair, "1D"), cfg.swing_n)

    buffer = cfg.sl_buffer_pips * PIP
    trades: list[Trade] = []
    setup: Setup | None = None
    pos: Trade | None = None

    for c in h1:
        t = c.close_time
        d1.advance_to(t)
        h8.advance_to(t)
        h4.advance_to(t)

        if d1.state == "bull" and h8.state == "bull":
            bias = "long"
        elif d1.state == "bear" and h8.state == "bear":
            bias = "short"
        else:
            bias = None

        # ---- 1) manage open position on this candle
        if pos is not None:
            hit_sl = c.l <= pos.sl if pos.direction == "long" else c.h >= pos.sl
            hit_tp = c.h >= pos.tp if pos.direction == "long" else c.l <= pos.tp
            if hit_sl:  # SL has priority (conservative when both are in range)
                pos.exit_time, pos.exit_price = t, pos.sl
                pos.result, pos.r_multiple = "loss", -1.0
                pos.ambiguous = hit_tp
                trades.append(pos)
                pos = None
            elif hit_tp:
                pos.exit_time, pos.exit_price = t, pos.tp
                pos.result, pos.r_multiple = "win", cfg.rr
                trades.append(pos)
                pos = None

        # ---- 2) pending setup: cancel / fill on the retest
        if setup is not None and pos is None:
            if bias != setup.direction:
                setup = None
            else:
                setup.bars_left -= 1
                filled = None
                if setup.direction == "long" and c.l <= setup.level:
                    entry = min(c.o, setup.level)  # gap opens below fill better
                    filled = entry if entry > setup.sl else None
                elif setup.direction == "short" and c.h >= setup.level:
                    entry = max(c.o, setup.level)
                    filled = entry if entry < setup.sl else None
                if filled is not None:
                    risk = abs(filled - setup.sl)
                    tp = filled + cfg.rr * risk if setup.direction == "long" else filled - cfg.rr * risk
                    pos = Trade(pair, setup.direction, setup.bos_time, t, filled, setup.sl, tp)
                    setup = None
                    # exit checks on the entry candle itself (post-fill path
                    # unknown -> SL if the far side was traded, TP only on a
                    # close beyond TP)
                    hit_sl = c.l <= pos.sl if pos.direction == "long" else c.h >= pos.sl
                    tp_close = c.c >= pos.tp if pos.direction == "long" else c.c <= pos.tp
                    if hit_sl:
                        pos.exit_time, pos.exit_price = t, pos.sl
                        pos.result, pos.r_multiple = "loss", -1.0
                        trades.append(pos)
                        pos = None
                    elif tp_close:
                        pos.exit_time, pos.exit_price = t, pos.tp
                        pos.result, pos.r_multiple = "win", cfg.rr
                        pos.ambiguous = True
                        trades.append(pos)
                        pos = None
                elif setup is not None and setup.bars_left <= 0:
                    setup = None

        # ---- 3) BOS detection on this 1H close
        broken_high, broken_low = h4.mark_breaks(c.c)
        if pos is None:
            if bias == "long" and broken_high is not None:
                origin = h4.latest_low()
                if origin is not None and origin.price < broken_high.price:
                    if cfg.sl_mode == "origin-swing":
                        anchor = origin.price
                    else:
                        # broken-level: just beyond the broken 4H level, but at
                        # least beyond the low of the 1H BOS candle so the stop
                        # distance is testable at 1H granularity
                        anchor = min(broken_high.price, c.l)
                    sl = anchor - buffer
                    if sl < broken_high.price:
                        setup = Setup("long", broken_high.price, sl, t, cfg.retest_window)
            elif bias == "short" and broken_low is not None:
                origin = h4.latest_high()
                if origin is not None and origin.price > broken_low.price:
                    if cfg.sl_mode == "origin-swing":
                        anchor = origin.price
                    else:
                        anchor = max(broken_low.price, c.h)
                    sl = anchor + buffer
                    if sl > broken_low.price:
                        setup = Setup("short", broken_low.price, sl, t, cfg.retest_window)

    if pos is not None:  # still open at end of data
        pos.result = "open"
        trades.append(pos)
    return trades


# ---------------------------------------------------------------- portfolio

def run_portfolio(all_trades: list[Trade], cfg: argparse.Namespace):
    closed = [tr for tr in all_trades if tr.result in ("win", "loss")]
    events = []  # (time, order, trade)  order: 0 = exit, 1 = entry
    for tr in closed:
        events.append((tr.entry_time, 1, tr))
        events.append((tr.exit_time, 0, tr))
    events.sort(key=lambda e: (e[0], e[1]))

    equity = cfg.start_equity
    curve = [(None, equity)]
    peak, max_dd = equity, 0.0
    for t, kind, tr in events:
        if kind == 1:
            tr.risk_amount = equity * cfg.risk_pct / 100.0
            tr.units = tr.risk_amount / abs(tr.entry - tr.sl)
        else:
            tr.pnl = tr.r_multiple * tr.risk_amount
            equity += tr.pnl
            tr.equity_after = equity
            curve.append((t, equity))
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)

    wins = [tr for tr in closed if tr.result == "win"]
    losses = [tr for tr in closed if tr.result == "loss"]
    gross_win = sum(tr.pnl for tr in wins)
    gross_loss = -sum(tr.pnl for tr in losses)
    summary = {
        "pairs": PAIRS,
        "config": {
            "swing_n": cfg.swing_n, "retest_window_bars": cfg.retest_window,
            "sl_mode": cfg.sl_mode, "sl_buffer_pips": cfg.sl_buffer_pips,
            "rr": cfg.rr, "risk_pct": cfg.risk_pct, "start_equity": cfg.start_equity,
        },
        "total_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "open_at_end": sum(1 for tr in all_trades if tr.result == "open"),
        "ambiguous_bars": sum(1 for tr in closed if tr.ambiguous),
        "win_rate_pct": round(100.0 * len(wins) / len(closed), 2) if closed else None,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "net_profit": round(equity - cfg.start_equity, 2),
        "net_return_pct": round(100.0 * (equity / cfg.start_equity - 1.0), 2),
        "max_drawdown_pct": round(100.0 * max_dd, 2),
        "final_equity": round(equity, 2),
        "per_pair": {},
    }
    for p in PAIRS:
        pt = [tr for tr in closed if tr.pair == p]
        pw = sum(1 for tr in pt if tr.result == "win")
        summary["per_pair"][p] = {
            "trades": len(pt), "wins": pw,
            "win_rate_pct": round(100.0 * pw / len(pt), 2) if pt else None,
            "pnl": round(sum(tr.pnl for tr in pt), 2),
        }
    return summary, curve


# ---------------------------------------------------------------- output

def fmt_t(t: datetime | None) -> str:
    return t.strftime("%Y-%m-%d %H:%M") if t else ""


def write_outputs(all_trades: list[Trade], summary: dict, curve, out_dir: str):
    os.makedirs(out_dir, exist_ok=True)
    all_trades.sort(key=lambda tr: tr.entry_time)

    with open(os.path.join(out_dir, "trades.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entry_date_utc", "exit_date_utc", "pair", "direction",
                    "entry", "sl", "tp", "result", "r_multiple", "risk_amount",
                    "pnl", "equity_after", "bos_time_utc", "ambiguous_bar"])
        for tr in all_trades:
            w.writerow([fmt_t(tr.entry_time), fmt_t(tr.exit_time), tr.pair,
                        tr.direction, f"{tr.entry:.5f}", f"{tr.sl:.5f}",
                        f"{tr.tp:.5f}", tr.result, tr.r_multiple,
                        f"{tr.risk_amount:.2f}", f"{tr.pnl:.2f}",
                        f"{tr.equity_after:.2f}" if tr.result in ("win", "loss") else "",
                        fmt_t(tr.bos_time), "yes" if tr.ambiguous else ""])

    with open(os.path.join(out_dir, "equity_curve.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["exit_date_utc", "equity"])
        for t, eq in curve:
            w.writerow([fmt_t(t) if t else "start", f"{eq:.2f}"])

    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    write_equity_svg(curve, os.path.join(out_dir, "equity_curve.svg"))


def write_equity_svg(curve, path: str, width=900, height=420):
    pts = [(i, eq) for i, (_, eq) in enumerate(curve)]
    if len(pts) < 2:
        return
    eqs = [eq for _, eq in pts]
    lo, hi = min(eqs), max(eqs)
    span = (hi - lo) or 1.0
    lo -= 0.05 * span
    hi += 0.05 * span
    ml, mr, mt, mb = 70, 20, 20, 40
    pw, ph = width - ml - mr, height - mt - mb

    def xy(i, eq):
        x = ml + pw * i / (len(pts) - 1)
        y = mt + ph * (1 - (eq - lo) / (hi - lo))
        return f"{x:.1f},{y:.1f}"

    poly = " ".join(xy(i, eq) for i, eq in pts)
    grid = []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = mt + ph * (1 - k / 4)
        grid.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{width-mr}" y2="{y:.1f}" stroke="#ddd"/>'
                    f'<text x="{ml-8}" y="{y+4:.1f}" text-anchor="end" font-size="12" fill="#555">{v:,.0f}</text>')
    labels = [(0, curve[1][0]), (len(pts) - 1, curve[-1][0])]
    ticks = "".join(
        f'<text x="{ml + pw * i / (len(pts)-1):.1f}" y="{height-12}" text-anchor="middle" font-size="12" fill="#555">'
        f'{t.strftime("%Y-%m-%d") if t else ""}</text>' for i, t in labels)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" font-family="sans-serif">'
           f'<rect width="{width}" height="{height}" fill="white"/>'
           f'{"".join(grid)}{ticks}'
           f'<polyline points="{poly}" fill="none" stroke="#1a7f5a" stroke-width="2"/>'
           f'<text x="{ml}" y="{mt-4}" font-size="13" fill="#333">Equity (per closed trade)</text></svg>')
    with open(path, "w") as f:
        f.write(svg)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--swing-n", type=int, default=2,
                    help="fractal width: bars on each side of a swing (default 2)")
    ap.add_argument("--retest-window", type=int, default=24,
                    help="1H bars a BOS setup waits for its retest (default 24)")
    ap.add_argument("--sl-mode", choices=["origin-swing", "broken-level"],
                    default="origin-swing",
                    help="origin-swing: SL beyond the opposite 4H swing the BOS leg "
                         "came from (default). broken-level: SL just beyond the "
                         "broken 4H level, widened to at least beyond the 1H BOS "
                         "candle's low/high so the stop is testable at 1H data.")
    ap.add_argument("--sl-buffer-pips", type=float, default=1.0,
                    help="'just beyond' buffer in pips (default 1)")
    ap.add_argument("--rr", type=float, default=2.0, help="reward:risk (default 2)")
    ap.add_argument("--risk-pct", type=float, default=1.0, help="risk per trade, %% of equity")
    ap.add_argument("--start-equity", type=float, default=10_000.0)
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    cfg = ap.parse_args()

    all_trades: list[Trade] = []
    for pair in PAIRS:
        trades = run_pair(pair, cfg)
        all_trades.extend(trades)
        print(f"{pair}: {sum(1 for t in trades if t.result in ('win', 'loss'))} closed trades")

    summary, curve = run_portfolio(all_trades, cfg)
    write_outputs(all_trades, summary, curve, cfg.out)

    print()
    print(f"Total trades:   {summary['total_trades']}"
          f"  (wins {summary['wins']} / losses {summary['losses']}"
          f", open at end: {summary['open_at_end']})")
    print(f"Win rate:       {summary['win_rate_pct']}%")
    print(f"Profit factor:  {summary['profit_factor']}")
    print(f"Net return:     {summary['net_return_pct']}%  "
          f"(final equity {summary['final_equity']:,.2f})")
    print(f"Max drawdown:   {summary['max_drawdown_pct']}%")
    print(f"Ambiguous bars (counted as per conservative rules): {summary['ambiguous_bars']}")
    print(f"\nPer pair:")
    for p, s in summary["per_pair"].items():
        print(f"  {p}: {s['trades']} trades, win rate {s['win_rate_pct']}%, pnl {s['pnl']:,.2f}")
    print(f"\nOutputs written to {cfg.out}/ (trades.csv, equity_curve.csv, "
          f"equity_curve.svg, summary.json)")


if __name__ == "__main__":
    main()
