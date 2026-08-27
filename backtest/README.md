# Multi-timeframe BOS/retest backtester

Runs the Daily/8H-bias, 4H-structure, 1H-BOS-retest strategy over the
Dukascopy CSVs in `data/` for AUDCHF, AUDUSD, EURCHF and EURUSD.

```bash
python3 backtest/backtest.py --out backtest/results/origin-swing
python3 backtest/backtest.py --sl-mode broken-level --out backtest/results/broken-level
python3 backtest/backtest.py --rr 1.8 --out backtest/results/origin-swing-rr1.8
```

Stdlib only, no packages needed. Committed results live in
`backtest/results/origin-swing/` (wide SL, 1:2), `backtest/results/broken-level/`
(tight SL, 1:2) and `backtest/results/origin-swing-rr1.8/` (wide SL, 1:1.8);
each directory contains:

| File | Contents |
|---|---|
| `trades.csv` | Full trade list: entry/exit date, pair, direction, entry, SL, TP, result, R, risk $, P&L, running equity |
| `equity_curve.csv` | Equity after every closed trade (all pairs merged chronologically) |
| `equity_curve.svg` | Equity curve chart |
| `summary.json` | Totals, win rate, profit factor, max drawdown, per-pair breakdown, config used |

## Rules as implemented

1. **Bias** — market-structure trend on the Daily AND the 8H must agree.
   A timeframe is bullish once a candle *closes* above its most recent
   confirmed swing high, bearish once it closes below its most recent
   confirmed swing low; the state holds until broken the other way.
   Swings are fractals with `--swing-n` bars (default 2) on each side.
2. **4H structure** — confirmed 4H fractal swing highs/lows, tracked with
   broken/unbroken state.
3. **Trigger** — a 1H candle *close* beyond the most recent still-unbroken
   4H swing level in the bias direction = break of structure (BOS).
4. **Entry** — limit order at the broken 4H level, filled when price retests
   it within `--retest-window` 1H bars (default 24). The setup is cancelled
   if bias flips first; a newer BOS replaces a pending one. One position per
   pair at a time.
5. **Stop loss** — `--sl-mode`:
   - `origin-swing` (default): just beyond the opposite-side 4H swing the
     BOS leg came from (for a long: 1 pip below the latest confirmed 4H
     swing low). This is the standard reading of "beyond the swing that
     produced the BOS" and gives stops the 1H data can actually resolve.
   - `broken-level`: 1 pip beyond the broken 4H level itself — the literal
     reading — widened to at least beyond the low/high of the 1H BOS candle.
     Without that minimum the entry *is* the broken level and risk collapses
     to the buffer (~1 pip), untestable at 1H granularity; anchoring to the
     BOS candle extreme keeps the tight stop but gives it a real distance.
6. **Take profit** — fixed 1:2 RR from the actual fill. No exceptions:
   positions are held to SL or TP regardless of later bias changes.
7. **Risk** — 1% of current booked equity per trade, compounding.

## No look-ahead bias

- A higher-timeframe candle becomes visible only once its **close time** is
  ≤ the close time of the current 1H bar. Daily candles close 17:00
  New York (the convention validated against the OANDA exports in
  `data/README.md`); 4H/8H close at open + duration.
- A fractal swing is only usable from the close of its confirming bar
  (N bars after the swing bar).
- All decisions (bias, BOS, setup creation) happen on 1H closes; only fills
  and SL/TP hits use intrabar highs/lows.

## Conservative fill rules (1H OHLC, intrabar path unknown)

- Any bar whose range covers both SL and TP counts as a **loss** and is
  flagged `ambiguous_bar` in `trades.csv`.
- On the entry bar itself, TP is only granted if the bar *closes* beyond TP.
- A gap through the entry level fills at the open (better price for a limit);
  a gap beyond the SL cancels the setup rather than inventing a fill.

## Modeling notes

- Prices are Dukascopy mid quotes; spread, commission and slippage are not
  modeled — with fixed 1:2 targets, real spread costs would shave a few
  percent off the win rate.
- P&L is applied in R-multiples (−1R = −1% of equity at entry, +2R = +2%),
  which also sidesteps quote-currency conversion for the CHF-quoted pairs.
- A position still open when the data ends is listed as `open` and excluded
  from the statistics.
