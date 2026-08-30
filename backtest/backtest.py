#!/usr/bin/env python3
"""Multi-timeframe BOS/retest strategy backtester.

Strategy (as specified):
  - Directional bias: Daily AND 8H market structure must align (both bullish
    -> longs only; both bearish -> shorts only; otherwise stand aside).
  - 4H defines structure: fractal swing highs/lows on the 4H chart.
  - Entry trigger: a 1H candle CLOSE beyond the most recent confirmed,
    still-unbroken 4H swing level in the direction of bias (break of
    structure, confirmed by candle close).
  - Entry: limit order at the broken 4H level, filled on the retest
    (--entry-mode break-close enters at the confirming candle's close instead).
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
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(HERE), "data")
NY = ZoneInfo("America/New_York")

PAIRS = ["AUDCHF", "AUDUSD", "EURCHF", "EURUSD"]

_data_dir = DATA_DIR   # mutable; --data-dir points the tools at another set


def data_dir() -> str:
    """Directory the candle CSVs are read from (see set_data_dir)."""
    return _data_dir


def set_data_dir(path: str) -> None:
    """Point every tool at a different data directory, e.g. an out-of-sample
    window fetched with `fetch_dukascopy.py --start ... --out data_2020_2022`.
    Read through data_dir(); importing DATA_DIR by name binds a copy and
    would not see this change."""
    global _data_dir
    if not os.path.isdir(path):
        raise SystemExit(f"--data-dir {path!r} is not a directory")
    _data_dir = os.path.abspath(path)


# The quote increment each instrument is measured in. Everything that takes
# a "pips" argument (--sl-buffer-pips, --min-break-pips) and every pip figure
# reported is in these units, so the same number means a comparable distance
# on every instrument.
#   FX majors  0.0001   a standard pip
#   JPY crosses 0.01    3-decimal quotes
#   XAUUSD      0.1     ten cents; gold's conventional pip. At ~$2,600 with
#                       $10-20 4H swings this puts gold's swing sizes on the
#                       same 100-200 scale as the JPY crosses, and keeps a
#                       1-pip stop buffer at a sane 10 cents rather than $1.
#   XAGUSD      0.01    one cent, the same convention one decimal down
#   NAS100      1.0     one index point. Indices are not FX: at ~20,000 with
#                       150-400 point 4H swings this puts them on the same
#                       100-400 scale as the JPY crosses and gold, and keeps
#                       a 1-pip stop buffer at 1 point rather than 0.0001.
PIP_SIZES = {"XAUUSD": 0.1, "XAGUSD": 0.01, "NAS100": 1.0,
             "SPX500": 1.0, "US30": 1.0, "GER40": 1.0}


def pip_size(pair: str) -> float:
    """Quote increment ("pip") for an instrument."""
    pair = pair.upper()
    if pair in PIP_SIZES:
        return PIP_SIZES[pair]
    return 0.01 if pair[3:] == "JPY" else 0.0001


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
    result: str | None = None      # "win" / "loss" / "scratch" / "open"
    r_multiple: float = 0.0
    ambiguous: bool = False
    # breakeven rule bookkeeping (only used with --breakeven):
    orig_sl: float = 0.0           # stop as originally placed
    be_armed: bool = False         # reached +1R, stop moved to entry
    partial_banked: bool = False   # partial-TP mode: half booked at +1R
    shadow: str = ""               # scratched trades: outcome without the rule
    # filled in by the portfolio pass:
    risk_amount: float = 0.0
    units: float = 0.0
    pnl: float = 0.0
    equity_after: float = 0.0


# ---------------------------------------------------------------- CSV loading

def load_candles(pair: str, tf: str) -> list[Candle]:
    path = os.path.join(data_dir(), f"{pair}_{tf}.csv")
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


def atr_series(candles: list[Candle], period: int = 14) -> list[float | None]:
    """ATR(period) ending at each bar; None until there is enough history.

    Value at index i uses bars i-period+1..i only, all of which are closed by
    the time bar i closes, so reading it on bar i is not look-ahead.
    """
    trs, out = [], [None] * len(candles)
    run = 0.0
    for i, c in enumerate(candles):
        tr = c.h - c.l if i == 0 else max(c.h - c.l, abs(c.h - candles[i-1].c),
                                          abs(c.l - candles[i-1].c))
        trs.append(tr)
        run += tr
        if i >= period:
            run -= trs[i - period]      # keep exactly `period` terms: i-period+1..i
            out[i] = run / period
    return out


class TrendTracker:
    """Market-structure trend on one timeframe (used for Daily and 8H bias).

    Default mode ("break"): bullish once a candle closes above the most
    recent confirmed swing high, bearish once one closes below the most
    recent confirmed swing low; the state holds until broken the other way.

    Mode "hhll": a break only sets the trend when the opposite structure
    agrees — bull needs the close-break of the last swing high AND a higher
    low behind it (last confirmed swing low above the previous one); bear
    needs the break of the last swing low AND a lower high behind it. An
    unqualified break against the current state demotes it to no-trend.

    warmup_swings: no state is ever set until the timeframe has confirmed
    that many fractal swings in total (highs + lows).
    min_break: a break only counts if the close clears the level by at
    least this distance (in price units).
    min_break_atr: same filter expressed as a fraction of this timeframe's
    own ATR(14) instead of a fixed distance, so one number means the same
    thing on EURUSD and on an index quoted in points. Takes precedence over
    min_break when set. Bars before ATR is available set no state.
    """

    def __init__(self, candles: list[Candle], n: int,
                 warmup_swings: int = 0, min_break: float = 0.0, mode: str = "break",
                 min_break_atr: float = 0.0, atr_period: int = 14):
        self.candles = candles
        self.n = n
        self.warmup = warmup_swings
        self.min_break = min_break
        self.min_break_atr = min_break_atr
        self.atr = atr_series(candles, atr_period) if min_break_atr else None
        self.mode = mode
        self.i = 0                      # next candle index to ingest
        self.highs: list[Swing] = []    # confirmed, chronological
        self.lows: list[Swing] = []
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
                    self.highs.append(Swing("H", cs[j].open_time, cs[j].h, cs[i].close_time))
                if is_swing(cs, j, self.n, "L"):
                    self.lows.append(Swing("L", cs[j].open_time, cs[j].l, cs[i].close_time))
            c = cs[i]
            self.i += 1
            if self.warmup and len(self.highs) + len(self.lows) < self.warmup:
                continue
            if self.atr is not None:
                a = self.atr[i]
                if a is None:
                    continue    # filter not sizeable yet: leave the state alone
                thr = self.min_break_atr * a
            else:
                thr = self.min_break
            up_brk = bool(self.highs) and c.c > self.highs[-1].price + thr
            dn_brk = bool(self.lows) and c.c < self.lows[-1].price - thr
            if self.mode == "hhll":
                higher_low = len(self.lows) >= 2 and self.lows[-1].price > self.lows[-2].price
                lower_high = len(self.highs) >= 2 and self.highs[-1].price < self.highs[-2].price
                bull = up_brk and higher_low
                bear = dn_brk and lower_high
                if bull and bear:  # freak candle: use candle direction
                    self.state = "bull" if c.c >= c.o else "bear"
                elif bull:
                    self.state = "bull"
                elif bear:
                    self.state = "bear"
                elif up_brk and self.state == "bear":
                    self.state = None  # structure damaged, not yet reversed
                elif dn_brk and self.state == "bull":
                    self.state = None
            else:
                if up_brk and dn_brk:  # freak candle breaking both
                    self.state = "bull" if c.c >= c.o else "bear"
                elif up_brk:
                    self.state = "bull"
                elif dn_brk:
                    self.state = "bear"


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

    def just_confirmed(self, t: datetime, kind: str) -> bool:
        """True if a swing of this kind was confirmed at exactly this time."""
        seq = self.highs if kind == "H" else self.lows
        return bool(seq) and seq[-1].confirm_time == t

    def ascending_lows(self, n: int) -> bool:
        """The last n+1 confirmed lows are strictly ascending (n higher lows)."""
        if len(self.lows) < n + 1:
            return False
        p = [s.price for s in self.lows[-(n + 1):]]
        return all(b > a for a, b in zip(p, p[1:]))

    def descending_highs(self, n: int) -> bool:
        if len(self.highs) < n + 1:
            return False
        p = [s.price for s in self.highs[-(n + 1):]]
        return all(b < a for a, b in zip(p, p[1:]))

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

def close_at_tp(pos: Trade, cfg) -> None:
    """Book a target hit.

    In partial mode the target is only reachable through +1R, so the first
    half is always already banked by the time TP prints: the trade pays
    frac x 1R on the banked half plus (1 - frac) x rr on the runner.
    """
    pos.result = "win"
    if cfg.partial_at_1r:
        pos.partial_banked = True
        pos.r_multiple = round(cfg.partial_frac * 1.0
                               + (1.0 - cfg.partial_frac) * cfg.rr, 6)
    else:
        pos.r_multiple = cfg.rr


def close_at_entry(pos: Trade, cfg) -> None:
    """Stop-out at entry: the banked partial (if any) is all the trade keeps."""
    if pos.partial_banked:
        pos.result = "partial"
        pos.r_multiple = round(cfg.partial_frac * 1.0, 6)
    else:
        pos.result, pos.r_multiple = "scratch", 0.0


# Per-pair setup-fate counters from the most recent run_pair() call, keyed by
# pair. Kept beside the trades rather than inside them because a setup that
# never fills produces no Trade to hang the reason on.
SETUP_FATES: dict[str, dict[str, int]] = {}


def run_pair(pair: str, cfg: argparse.Namespace) -> list[Trade]:
    h1 = load_candles(pair, "60")
    h4 = StructureTracker(load_candles(pair, "240"), cfg.swing_n)
    pip = pip_size(pair)
    min_break = cfg.min_break_pips * pip
    mb_atr = getattr(cfg, "min_break_atr", 0.0) or 0.0
    h8 = TrendTracker(load_candles(pair, "480"), cfg.swing_n,
                      cfg.warmup_swings, min_break, cfg.trend_mode, mb_atr)
    d1 = TrendTracker(load_candles(pair, "1D"), cfg.swing_n,
                      cfg.warmup_swings, min_break, cfg.trend_mode, mb_atr)

    buffer = cfg.sl_buffer_pips * pip
    trades: list[Trade] = []
    setup: Setup | None = None
    pos: Trade | None = None
    # what happened to every BOS setup that was created: it filled, or it died
    # one of three ways before the retest arrived.
    fates = {"created": 0, "filled": 0, "bias_flip": 0,
             "window_expired": 0, "superseded": 0, "pending_at_end": 0}
    waits: list[int] = []         # 1H bars each setup waited before filling

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
            is_long = pos.direction == "long"
            hit_sl = c.l <= pos.sl if is_long else c.h >= pos.sl
            hit_tp = c.h >= pos.tp if is_long else c.l <= pos.tp
            if hit_sl:  # stop has priority (conservative when both are in range)
                pos.exit_time, pos.exit_price = t, pos.sl
                if pos.be_armed:  # stop already moved to entry
                    close_at_entry(pos, cfg)
                else:
                    pos.result, pos.r_multiple = "loss", -1.0
                pos.ambiguous = hit_tp
                trades.append(pos)
                pos = None
            elif hit_tp:
                pos.exit_time, pos.exit_price = t, pos.tp
                close_at_tp(pos, cfg)
                trades.append(pos)
                pos = None
            elif (cfg.breakeven or cfg.partial_at_1r) and not pos.be_armed:
                risk = abs(pos.entry - pos.orig_sl)
                trigger = pos.entry + risk if is_long else pos.entry - risk
                if (c.h >= trigger) if is_long else (c.l <= trigger):
                    pos.be_armed = True
                    pos.sl = pos.entry
                    if cfg.partial_at_1r:  # bank the first half here
                        pos.partial_banked = True
                    # the same bar also traded back through entry: intrabar
                    # order is unknown -> conservative close at entry now
                    if (c.l <= pos.entry) if is_long else (c.h >= pos.entry):
                        pos.exit_time, pos.exit_price = t, pos.entry
                        close_at_entry(pos, cfg)
                        pos.ambiguous = True
                        trades.append(pos)
                        pos = None

        # ---- 2) pending setup: cancel / fill on the retest
        if setup is not None and pos is None:
            if bias != setup.direction:
                fates["bias_flip"] += 1
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
                    fates["filled"] += 1
                    waits.append(cfg.retest_window - setup.bars_left)
                    risk = abs(filled - setup.sl)
                    tp = filled + cfg.rr * risk if setup.direction == "long" else filled - cfg.rr * risk
                    pos = Trade(pair, setup.direction, setup.bos_time, t, filled, setup.sl, tp,
                                orig_sl=setup.sl)
                    setup = None
                    # exit checks on the entry candle itself (post-fill path
                    # unknown -> SL if the far side was traded, TP only on a
                    # close beyond TP; the breakeven trigger is never armed on
                    # the entry candle since the fill point within it is unknown)
                    hit_sl = c.l <= pos.sl if pos.direction == "long" else c.h >= pos.sl
                    tp_close = c.c >= pos.tp if pos.direction == "long" else c.c <= pos.tp
                    if hit_sl:
                        pos.exit_time, pos.exit_price = t, pos.sl
                        pos.result, pos.r_multiple = "loss", -1.0
                        trades.append(pos)
                        pos = None
                    elif tp_close:
                        pos.exit_time, pos.exit_price = t, pos.tp
                        close_at_tp(pos, cfg)
                        pos.ambiguous = True
                        trades.append(pos)
                        pos = None
                elif setup is not None and setup.bars_left <= 0:
                    fates["window_expired"] += 1
                    setup = None

        # ---- 3) entry
        broken_high, broken_low = h4.mark_breaks(c.c)
        warming = cfg.warmup_swings and len(h4.highs) + len(h4.lows) < cfg.warmup_swings

        if cfg.entry_mode == "swing-seq":
            # No BOS, no retest: enter at the close of the 4H candle that
            # confirms the Nth consecutive higher low (long) / lower high
            # (short). That candle has just closed, so management starts on
            # the next 1H bar.
            if not warming and pos is None and bias is not None:
                bar4 = h4.candles[h4.i - 1] if h4.i else None
                if bar4 is not None and bar4.close_time == t:
                    if (bias == "long" and h4.just_confirmed(t, "L")
                            and h4.ascending_lows(cfg.swing_seq)):
                        sl = h4.lows[-1].price - buffer
                        if sl < bar4.c:
                            risk = bar4.c - sl
                            pos = Trade(pair, "long", t, t, bar4.c, sl,
                                        bar4.c + cfg.rr * risk, orig_sl=sl)
                    elif (bias == "short" and h4.just_confirmed(t, "H")
                            and h4.descending_highs(cfg.swing_seq)):
                        sl = h4.highs[-1].price + buffer
                        if sl > bar4.c:
                            risk = sl - bar4.c
                            pos = Trade(pair, "short", t, t, bar4.c, sl,
                                        bar4.c - cfg.rr * risk, orig_sl=sl)
        elif warming:
            pass  # 4H structure still warming up: no setups
        elif pos is None:
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
                    if cfg.entry_mode == "break-close":
                        # enter at the close of the confirming candle: the bar
                        # is already complete, so management starts next bar
                        if sl < c.c:
                            risk = c.c - sl
                            pos = Trade(pair, "long", t, t, c.c, sl,
                                        c.c + cfg.rr * risk, orig_sl=sl)
                    elif sl < broken_high.price:
                        if setup is not None:
                            fates["superseded"] += 1
                        fates["created"] += 1
                        setup = Setup("long", broken_high.price, sl, t, cfg.retest_window)
            elif bias == "short" and broken_low is not None:
                origin = h4.latest_high()
                if origin is not None and origin.price > broken_low.price:
                    if cfg.sl_mode == "origin-swing":
                        anchor = origin.price
                    else:
                        anchor = max(broken_low.price, c.h)
                    sl = anchor + buffer
                    if cfg.entry_mode == "break-close":
                        if sl > c.c:
                            risk = sl - c.c
                            pos = Trade(pair, "short", t, t, c.c, sl,
                                        c.c - cfg.rr * risk, orig_sl=sl)
                    elif sl > broken_low.price:
                        if setup is not None:
                            fates["superseded"] += 1
                        fates["created"] += 1
                        setup = Setup("short", broken_low.price, sl, t, cfg.retest_window)

    if setup is not None:
        fates["pending_at_end"] += 1
    # How long a setup is exposed to a bias change is the whole reason the
    # bias-flip count is what it is, so report it beside the fates.
    fates["fill_wait_median_bars"] = (statistics.median(waits) if waits else None)
    fates["fill_wait_mean_bars"] = (round(statistics.fmean(waits), 1) if waits else None)
    fates["fill_wait_max_bars"] = (max(waits) if waits else None)
    SETUP_FATES[pair] = fates

    if pos is not None:  # still open at end of data
        pos.result = "open"
        trades.append(pos)

    if cfg.breakeven:
        # counterfactual for each scratched trade: keep the original stop and
        # walk forward from the scratch bar -> would it have hit TP or SL?
        close_times = [b.close_time for b in h1]
        import bisect
        for tr in trades:
            if tr.result != "scratch":
                continue
            # start at the scratch bar itself: it may have traded through the
            # original stop after (or before) touching entry
            i = bisect.bisect_left(close_times, tr.exit_time)
            tr.shadow = "open"
            for b in h1[i:]:
                if (b.l <= tr.orig_sl) if tr.direction == "long" else (b.h >= tr.orig_sl):
                    tr.shadow = "SL"  # stop priority, same conservative rule
                    break
                if (b.h >= tr.tp) if tr.direction == "long" else (b.l <= tr.tp):
                    tr.shadow = "TP"
                    break
    return trades


# ---------------------------------------------------------------- portfolio

def run_portfolio(all_trades: list[Trade], cfg: argparse.Namespace):
    closed = [tr for tr in all_trades if tr.result in ("win", "loss", "scratch", "partial")]
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
            tr.units = tr.risk_amount / abs(tr.entry - tr.orig_sl)
        else:
            tr.pnl = tr.r_multiple * tr.risk_amount
            equity += tr.pnl
            tr.equity_after = equity
            curve.append((t, equity))
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)

    wins = [tr for tr in closed if tr.result == "win"]
    losses = [tr for tr in closed if tr.result == "loss"]
    scratches = [tr for tr in closed if tr.result == "scratch"]
    partials = [tr for tr in closed if tr.result == "partial"]
    # by sign, so a partial's banked profit counts as gross profit
    gross_win = sum(tr.pnl for tr in closed if tr.pnl > 0)
    gross_loss = -sum(tr.pnl for tr in closed if tr.pnl < 0)
    summary = {
        "pairs": sorted({tr.pair for tr in all_trades}),
        "config": {
            "swing_n": cfg.swing_n, "retest_window_bars": cfg.retest_window,
            "sl_mode": cfg.sl_mode, "sl_buffer_pips": cfg.sl_buffer_pips,
            "rr": cfg.rr, "risk_pct": cfg.risk_pct, "start_equity": cfg.start_equity,
            "entry_mode": cfg.entry_mode, "swing_seq": cfg.swing_seq,
            "breakeven": cfg.breakeven, "partial_at_1r": cfg.partial_at_1r,
            "partial_frac": cfg.partial_frac, "warmup_swings": cfg.warmup_swings,
            "min_break_pips": cfg.min_break_pips,
            "min_break_atr": cfg.min_break_atr, "trend_mode": cfg.trend_mode,
        },
        "total_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "scratches": len(scratches),
        "partials": len(partials),
        "open_at_end": sum(1 for tr in all_trades if tr.result == "open"),
        "ambiguous_bars": sum(1 for tr in closed if tr.ambiguous),
        "setup_fates": {k: dict(v) for k, v in sorted(SETUP_FATES.items())},
        "win_rate_pct": round(100.0 * len(wins) / len(closed), 2) if closed else None,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "net_profit": round(equity - cfg.start_equity, 2),
        "net_return_pct": round(100.0 * (equity / cfg.start_equity - 1.0), 2),
        "max_drawdown_pct": round(100.0 * max_dd, 2),
        "avg_r": round(sum(tr.r_multiple for tr in closed) / len(closed), 3) if closed else None,
        "total_r": round(sum(tr.r_multiple for tr in closed), 2),
        "final_equity": round(equity, 2),
        "per_pair": {},
    }
    for p in sorted({tr.pair for tr in all_trades}):
        pt = [tr for tr in closed if tr.pair == p]
        pw = sum(1 for tr in pt if tr.result == "win")
        summary["per_pair"][p] = {
            "trades": len(pt), "wins": pw,
            "partials": sum(1 for tr in pt if tr.result == "partial"),
            "win_rate_pct": round(100.0 * pw / len(pt), 2) if pt else None,
            "avg_r": round(sum(tr.r_multiple for tr in pt) / len(pt), 3) if pt else None,
            "pnl": round(sum(tr.pnl for tr in pt), 2),
        }

    if cfg.breakeven:
        # a win always passed +1R on the way to +2R; armed trades that ended
        # as scratch or were still open also reached it
        armed_open = sum(1 for tr in all_trades if tr.result == "open" and tr.be_armed)
        summary["breakeven_mechanics"] = {
            "reached_1r": len(wins) + len(scratches) + armed_open,
            "went_on_to_tp": len(wins),
            "scratched": len(scratches),
            "armed_still_open": armed_open,
            "scratched_would_have_hit": {
                "TP": sum(1 for tr in scratches if tr.shadow == "TP"),
                "SL": sum(1 for tr in scratches if tr.shadow == "SL"),
                "open": sum(1 for tr in scratches if tr.shadow == "open"),
            },
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
                    "pnl", "equity_after", "bos_time_utc", "ambiguous_bar",
                    "reached_1r", "no_be_outcome"])
        for tr in all_trades:
            closed = tr.result in ("win", "loss", "scratch", "partial")
            w.writerow([fmt_t(tr.entry_time), fmt_t(tr.exit_time), tr.pair,
                        tr.direction, f"{tr.entry:.5f}", f"{tr.orig_sl:.5f}",
                        f"{tr.tp:.5f}", tr.result, tr.r_multiple,
                        f"{tr.risk_amount:.2f}", f"{tr.pnl:.2f}",
                        f"{tr.equity_after:.2f}" if closed else "",
                        fmt_t(tr.bos_time), "yes" if tr.ambiguous else "",
                        "yes" if (tr.be_armed or tr.result == "win") else "",
                        tr.shadow])

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
    ap.add_argument("--warmup-swings", type=int, default=0,
                    help="skip all signals on a timeframe (bias state on D/8H, "
                         "BOS setups on 4H) until it has confirmed this many "
                         "fractal swings in total (default 0 = off)")
    ap.add_argument("--min-break-pips", type=float, default=0.0,
                    help="a D/8H structure break only flips the trend state if "
                         "the close clears the swing level by at least this many "
                         "pips (default 0 = off)")
    ap.add_argument("--min-break-atr", type=float, default=0.0,
                    help="same filter as --min-break-pips but sized as a "
                         "fraction of each timeframe's own ATR(14), so one "
                         "value means the same thing on EURUSD and on an index "
                         "quoted in points (default 0 = off). Overrides "
                         "--min-break-pips when both are given")
    ap.add_argument("--trend-mode", choices=["break", "hhll"], default="break",
                    help="break: any close-break of the last confirmed swing "
                         "flips the D/8H trend (default). hhll: bull needs the "
                         "break AND a higher low behind it, bear needs the break "
                         "AND a lower high; an unqualified counter-break demotes "
                         "the state to no-trend.")
    ap.add_argument("--partial-at-1r", action="store_true",
                    help="take partial profit at +1R: close --partial-frac of "
                         "the position there and move the stop to entry, letting "
                         "the remainder run to the --rr target (a win therefore "
                         "pays frac*1R + (1-frac)*rr, an entry stop-out pays "
                         "frac*1R)")
    ap.add_argument("--partial-frac", type=float, default=0.5,
                    help="fraction closed at +1R with --partial-at-1r (default 0.5)")
    ap.add_argument("--breakeven", action="store_true",
                    help="when price reaches +1R in favour, move the stop to "
                         "entry (never armed on the entry candle; a bar that "
                         "touches +1R and trades back through entry scratches "
                         "conservatively)")
    ap.add_argument("--risk-pct", type=float, default=1.0, help="risk per trade, %% of equity")
    ap.add_argument("--start-equity", type=float, default=10_000.0)
    ap.add_argument("--entry-mode", choices=["retest", "break-close", "swing-seq"],
                    default="retest",
                    help="retest: limit at the broken 4H level, filled when price "
                         "returns to it (default). break-close: enter at the close "
                         "of the 1H candle that confirms the break, no retest wait "
                         "— the stop is unchanged, so risk is wider by however far "
                         "the candle closed beyond the level, and the target moves "
                         "out with it. swing-seq: ignore the 1H break entirely "
                         "and enter at the close of the 4H candle confirming the "
                         "Nth consecutive higher low (long) or lower high (short), "
                         "stop beyond that swing.")
    ap.add_argument("--swing-seq", type=int, default=2,
                    help="with --entry-mode swing-seq: how many consecutive higher "
                         "lows / lower highs are required (2 = a second higher low, "
                         "the stricter reading; 1 = simply a higher low)")
    ap.add_argument("--pairs", nargs="+", default=PAIRS,
                    help="pairs to trade (default: the original four)")
    ap.add_argument("--data-dir", default=None,
                    help="directory holding {PAIR}_{60,240,480,1D}.csv "
                         "(default: data/). Use it to run an out-of-sample "
                         "window fetched to another directory.")
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    cfg = ap.parse_args()
    if cfg.min_break_atr and cfg.min_break_pips:
        print(f"note: --min-break-atr {cfg.min_break_atr} overrides "
              f"--min-break-pips {cfg.min_break_pips}", file=sys.stderr)
    if cfg.data_dir:
        set_data_dir(cfg.data_dir)

    all_trades: list[Trade] = []
    for pair in cfg.pairs:
        trades = run_pair(pair, cfg)
        all_trades.extend(trades)
        print(f"{pair}: {sum(1 for t in trades if t.result in ('win', 'loss', 'scratch', 'partial'))} closed trades")

    summary, curve = run_portfolio(all_trades, cfg)
    write_outputs(all_trades, summary, curve, cfg.out)

    print()
    print(f"Total trades:   {summary['total_trades']}"
          f"  (wins {summary['wins']} / losses {summary['losses']}"
          f" / scratches {summary['scratches']}"
          f"{' / partials ' + str(summary['partials']) if summary['partials'] else ''}"
          f", open at end: {summary['open_at_end']})")
    print(f"Win rate:       {summary['win_rate_pct']}%")
    print(f"Profit factor:  {summary['profit_factor']}")
    print(f"Net return:     {summary['net_return_pct']}%  "
          f"(final equity {summary['final_equity']:,.2f})")
    print(f"Max drawdown:   {summary['max_drawdown_pct']}%")
    print(f"Avg R / trade:  {summary['avg_r']}  (total {summary['total_r']}R)")
    print(f"Ambiguous bars (counted as per conservative rules): {summary['ambiguous_bars']}")

    fates = summary.get("setup_fates") or {}
    if fates and cfg.entry_mode == "retest":
        print("\nSetups: what happened between the BOS and the retest")
        print(f"  {'pair':<9}{'created':>9}{'filled':>8}{'bias flip':>11}"
              f"{'expired':>9}{'superseded':>12}{'flip %':>9}{'median wait':>13}")
        tot = {k: 0 for k in ("created", "filled", "bias_flip",
                              "window_expired", "superseded")}
        for pr, f in fates.items():
            for k in tot:
                tot[k] += f[k]
            w = f.get("fill_wait_median_bars")
            print(f"  {pr:<9}{f['created']:>9}{f['filled']:>8}{f['bias_flip']:>11}"
                  f"{f['window_expired']:>9}{f['superseded']:>12}"
                  f"{(100*f['bias_flip']/f['created'] if f['created'] else 0):>8.1f}%"
                  f"{('—' if w is None else f'{w:.0f} bars'):>13}")
        if len(fates) > 1:
            print(f"  {'ALL':<9}{tot['created']:>9}{tot['filled']:>8}"
                  f"{tot['bias_flip']:>11}{tot['window_expired']:>9}"
                  f"{tot['superseded']:>12}"
                  f"{(100*tot['bias_flip']/tot['created'] if tot['created'] else 0):>8.1f}%")
        print("  (superseded = a later BOS replaced the pending setup before it"
              " filled;\n   median wait = 1H bars from the BOS to the fill, i.e. how"
              " long a\n   setup is actually exposed to a bias change)")
    if "breakeven_mechanics" in summary:
        m = summary["breakeven_mechanics"]
        sw = m["scratched_would_have_hit"]
        print(f"\nBreakeven mechanics:")
        print(f"  reached +1R:           {m['reached_1r']}"
              f"  (still open at end: {m['armed_still_open']})")
        print(f"  went on to hit TP:     {m['went_on_to_tp']}")
        print(f"  scratched at entry:    {m['scratched']}")
        print(f"  scratched, without rule would have hit: "
              f"TP {sw['TP']} / SL {sw['SL']} / still open {sw['open']}")
    print(f"\nPer pair:")
    for p, s in summary["per_pair"].items():
        print(f"  {p}: {s['trades']} trades, win rate {s['win_rate_pct']}%, pnl {s['pnl']:,.2f}")
    print(f"\nOutputs written to {cfg.out}/ (trades.csv, equity_curve.csv, "
          f"equity_curve.svg, summary.json)")


if __name__ == "__main__":
    main()
