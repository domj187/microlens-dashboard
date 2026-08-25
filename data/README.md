# Historical FX data (Dukascopy)

This folder is the target for 3 years of historical OHLC candles for
AUDCHF, AUDUSD, EURCHF and EURUSD, built from Dukascopy's free datafeed
in the same column format as the `OANDA_*.csv` exports in the repo root
(`time,open,high,low,close`).

## Status: download pending — network policy blocker

The Claude Code cloud environment this pipeline was built in only allows
outbound traffic to GitHub and package registries. `datafeed.dukascopy.com`
(and every other free market-data host tested: Yahoo Finance, Stooq,
HistData, frankfurter.app) is denied by the egress proxy, so the CSVs could
not be generated here yet. To unblock, either:

1. Change the environment's network policy to allow `datafeed.dukascopy.com`
   (claude.ai → Code → environment settings; see
   https://code.claude.com/docs/en/claude-code-on-the-web), then re-run the
   session task, or
2. Run the script locally: `python3 scripts/fetch_dukascopy.py`
   (Python 3.9+, stdlib only — no packages needed).

## What the script produces

`scripts/fetch_dukascopy.py` downloads monthly hourly-candle files
(BID and ASK), computes mid prices, and writes per pair:

| File | Timeframe |
|---|---|
| `data/{PAIR}_60.csv` | 1H |
| `data/{PAIR}_240.csv` | 4H |
| `data/{PAIR}_480.csv` | 8H |
| `data/{PAIR}_1D.csv` | Daily |

Raw downloads are cached in `data/raw/` (gitignored); re-runs and
`--offline` rebuilds are cheap.

## Candle conventions (validated against the OANDA exports)

- 4H/8H buckets anchor to the **17:00 America/New_York** session boundary,
  and daily candles cover one 17:00→17:00 NY session labeled with the close
  date. `scripts/validate_against_oanda.py` proves this convention: OANDA 1H
  aggregated this way reproduces the OANDA 4H/8H/1D exports exactly (one
  0.00001 discrepancy in OANDA's own daily AUDCHF high on 2026-08-20).
- Intraday timestamps are ISO 8601 in Europe/London, daily timestamps plain
  `YYYY-MM-DD` — same as the OANDA files.
- Prices are mid = (bid+ask)/2 rounded to 5 decimals (`--price bid|ask|mid`).
- Weekend filler candles (Fri 17:00 NY → Sun 17:00 NY) are dropped; the
  built-in validation reports any duplicate timestamps or gaps that are not
  weekend/holiday closures, plus the date range and candle count per file.
