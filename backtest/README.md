# Multi-timeframe BOS/retest backtester

Runs the Daily/8H-bias, 4H-structure, 1H-BOS-retest strategy over the
Dukascopy CSVs in `data/` for AUDCHF, AUDUSD, EURCHF and EURUSD.

```bash
python3 backtest/backtest.py --out backtest/results/origin-swing
python3 backtest/backtest.py --sl-mode broken-level --out backtest/results/broken-level
python3 backtest/backtest.py --rr 1.8 --out backtest/results/origin-swing-rr1.8
python3 backtest/backtest.py --breakeven --out backtest/results/origin-swing-be
python3 backtest/backtest.py --warmup-swings 10 --min-break-pips 5 \
    --out backtest/results/origin-swing-filtered
python3 backtest/backtest.py --warmup-swings 10 --min-break-pips 5 \
    --trend-mode hhll --out backtest/results/origin-swing-hhll
python3 backtest/backtest.py --warmup-swings 10 --min-break-pips 5 \
    --partial-at-1r --out backtest/results/origin-swing-partial
# the same variant on the wider pair set (--pairs defaults to the original four)
python3 backtest/backtest.py --warmup-swings 10 --min-break-pips 5 --partial-at-1r \
    --pairs USDJPY GBPJPY --out backtest/results/partial-jpy
python3 backtest/backtest.py --warmup-swings 10 --min-break-pips 5 --partial-at-1r \
    --pairs AUDCHF AUDUSD EURCHF EURUSD GBPUSD USDJPY GBPJPY \
    --out backtest/results/partial-all7
python3 backtest/backtest.py --warmup-swings 10 --min-break-pips 5 --partial-at-1r \
    --entry-mode break-close --pairs AUDCHF AUDUSD EURCHF EURUSD GBPUSD USDJPY GBPJPY \
    --out backtest/results/breakclose-all7
python3 backtest/backtest.py --warmup-swings 10 --min-break-pips 5 --partial-at-1r \
    --entry-mode swing-seq --swing-seq 2 \
    --pairs AUDCHF AUDUSD EURCHF EURUSD GBPUSD USDJPY GBPJPY \
    --out backtest/results/swingseq2-all7
```

Pip size is per instrument, defined once in `backtest.pip_size()` and shared
by every tool here, so `--sl-buffer-pips`, `--min-break-pips` and all
reported pip figures mean a comparable distance everywhere:

| Instrument | Pip | Note |
|---|---|---|
| FX majors | 0.0001 | standard pip |
| JPY crosses | 0.01 | 3-decimal quotes |
| XAUUSD | 0.1 | ten cents — gold's conventional pip; puts its ~$15-20 4H swings on the same 150-200 scale as the JPY crosses and keeps a 1-pip stop buffer at 10c rather than $1 |
| XAGUSD | 0.01 | one cent |

Fetching a metal also needs the right integer price scale. Dukascopy is
believed to serve XAUUSD at 1e3, but that is **unverified** — the feed is
unreachable from the development environment. `sanity_prices()` rejects an
implausible median price at fetch time and names the fix, and
`--price-scale` overrides the table if the feed disagrees:

```bash
python3 scripts/fetch_dukascopy.py --pairs XAUUSD --years 3
# if it reports an implausible median close:
python3 scripts/fetch_dukascopy.py --pairs XAUUSD --years 3 --price-scale 100
```

Stdlib only, no packages needed. Committed results live in
`backtest/results/origin-swing/` (wide SL, 1:2), `backtest/results/broken-level/`
(tight SL, 1:2), `backtest/results/origin-swing-rr1.8/` (wide SL, 1:1.8) and
`backtest/results/origin-swing-be/` (wide SL, 1:2, breakeven rule);
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
8. **Breakeven rule** (optional, `--breakeven`) — when price reaches +1R in
   favour of the trade, the stop moves to entry; a later stop-out at entry is
   a 0R "scratch". The trigger is never armed on the entry candle (the fill
   point within it is unknown), and a bar that touches +1R and also trades
   back through entry scratches conservatively (flagged ambiguous). The
   trade list gains `reached_1r` and `no_be_outcome` columns — the latter
   shows, for each scratched trade, whether it would have hit TP or SL
   without the rule (shadow-simulated with the original stop), and
   `summary.json` gains a `breakeven_mechanics` block with the totals.
9. **Bias filters** (optional) — `--warmup-swings N` suppresses all signals
   on a timeframe (bias state on Daily/8H, BOS setups on 4H) until it has
   confirmed N fractal swings in total, so no trend is declared off the
   thin structure at the start of the data. `--min-break-pips X` requires a
   Daily/8H close to clear the swing level by at least X pips before the
   trend state flips. `--trend-mode hhll` tightens the trend definition:
   bull needs the close-break of the last confirmed swing high AND a
   higher low behind it (last confirmed swing low above the previous one),
   bear the mirror image; an unqualified break against the current state
   demotes it to no-trend instead of reversing it.
10. **Entry mode** (`--entry-mode`) — `retest` (default) places the limit at
   the broken 4H level and waits for price to return to it. `break-close`
   skips the wait and enters at the close of the 1H candle that confirmed
   the break. The stop is identical in both, so entering beyond the level
   widens the risk by however far the candle closed past it, and the fixed
   target moves out with it: the same move must now travel further to pay
   the same R. Every setup trades (no missed retests), so trade counts and
   the outcome mix change too. `swing-seq` drops the 1H break and the
   retest entirely: it enters at the close of the 4H candle that confirms
   the `--swing-seq` Nth consecutive higher low (long) or lower high
   (short), with the stop just beyond that swing. N=2 is "a second higher
   low"; N=1 is simply "a higher low". Because the stop sits at the swing
   the entry is measured from, risk is far tighter than either BOS mode.
11. **Partial profit at +1R** (optional, `--partial-at-1r`) — closes
   `--partial-frac` of the position (default half) at +1R, banks it, and
   moves the stop to entry for the remainder, which runs to the `--rr`
   target. Outcomes per trade: never reached +1R and stopped = **−1R**
   (`loss`); banked the half then stopped at entry = **+0.5R**
   (`partial`); banked the half then the runner hit 2R = **+1.5R**
   (`win`). Because the 2R target is only reachable through +1R, a target
   hit always counts the partial as banked. Profit factor is computed by
   P&L sign, so a partial's banked profit counts as gross profit.

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

## News correlation analysis

`backtest/news_analysis.py` cross-references any run's `trades.csv` with a
historical high-impact economic calendar and reports win rate / average R
for news-affected vs clean trades. The calendar cannot be fetched from
this environment (network policy) — see `data/news/README.md` for the
drop-in file format and sources, then:

```bash
python3 backtest/news_analysis.py \
    --trades backtest/results/origin-swing-filtered/trades.csv \
    --calendar data/news/high_impact.csv
```

## Sub-period breakdown

`backtest/period_breakdown.py` splits any run into calendar years and
six-month periods — trade count, W/P/L, avg R, total R and P&L — alongside
the instrument's own price move over the same period, so a profitable
stretch can be checked against whether the pair was simply trending. It
also reports how much of the total R comes from the single best half-year.

```bash
python3 backtest/period_breakdown.py \
    --trades backtest/results/partial-usdjpy/trades.csv --pair USDJPY
```

Trades are bucketed by entry date, which is what attributes a result to the
market regime that produced it.

## Pair character analysis

`backtest/pair_character.py` measures the structural character of each pair
**without running the strategy** — swing size, what follows a break of
structure, retest availability, daily trend persistence, spread — then ranks
the pairs on how well they suit a break-and-retest system. It picks up every
pair that has a complete set of files in `data/`, so new pairs are included
automatically.

```bash
python3 backtest/pair_character.py
python3 backtest/pair_character.py --pairs EURUSD GBPJPY --json character.json
```

The follow-through test is calibrated against a **33.3% no-edge baseline**:
the 2x-depth target sits twice as far away as the invalidation level, so a
driftless random walk reaches it first one time in three. That is also the
breakeven rate of a 1:2 target stopped at the origin swing, which makes the
"vs 33.3%" column a direct read on structural edge.

Spread needs bid and ask data, which `data/` does not hold (mid only):

```bash
python3 scripts/fetch_dukascopy.py --price bid --pairs ... # -> data_bid/
python3 scripts/fetch_dukascopy.py --price ask --pairs ... # -> data_ask/
python3 backtest/pair_character.py --bid-dir data_bid --ask-dir data_ask
```

### Adding pairs

`datafeed.dukascopy.com` is blocked by the cloud environment's network
policy, so new pairs must be fetched on a machine with open network access:

```bash
python3 scripts/fetch_dukascopy.py --years 3 \
    --pairs GBPUSD USDJPY GBPJPY EURGBP
```

That writes `data/{PAIR}_{60,240,480,1D}.csv` in the same format as the
existing four. Commit them and every tool here — backtester included —
picks them up. JPY pairs are handled: pip size switches to 0.01.
