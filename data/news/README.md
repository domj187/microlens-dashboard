# Historical economic calendar (manual drop-in)

The Claude Code cloud environment's network policy blocks every economic
calendar source tested (ForexFactory/faireconomy, Investing.com,
TradingView, FXStreet — all 403 at the egress proxy), and the free
ForexFactory feed only serves the current week anyway. To run the news
correlation analysis, download a historical calendar yourself and drop it
in this folder, then run:

```bash
python3 backtest/news_analysis.py \
    --trades backtest/results/origin-swing-filtered/trades.csv \
    --calendar data/news/high_impact.csv
```

## Canonical format — `data/news/high_impact.csv`

```csv
datetime_utc,currency,impact,event
2023-09-13 12:30,USD,High,CPI m/m
2023-09-14 12:15,EUR,High,ECB Main Refinancing Rate
2023-10-03 03:30,AUD,High,RBA Cash Rate
2023-09-21 07:30,CHF,High,SNB Policy Rate
```

- `datetime_utc` — `YYYY-MM-DD HH:MM` in **UTC** (ISO 8601 with an offset
  also works, e.g. `2023-09-13T08:30:00-04:00`).
- `currency` — 3-letter code; only USD / EUR / AUD / CHF matter for these
  pairs, other rows are ignored.
- `impact` — rows containing `High` (or `red`, or `3`) are used; everything
  else is skipped unless you pass `--impact all`.
- `event` — free text, echoed into the annotated output.

## Also accepted

The loader is tolerant of common export shapes:

- Separate `Date` + `Time` columns instead of one datetime column
  (`2:30pm`-style 12-hour times are fine; all-day/tentative rows are
  skipped).
- `Country` instead of `currency`, `Title` instead of `event`.
- Calendar times in a local timezone: pass e.g.
  `--calendar-tz America/New_York` and they are converted (DST-aware) —
  useful for a ForexFactory export left on US Eastern time.

Cover **2023-08-25 through 2026-08-25** (the span of the price data) for
USD, EUR, AUD and CHF, high-impact only is enough.

## Where to get it

Any of these can produce a compatible file with light column renaming:

- **ForexFactory** calendar (forexfactory.com/calendar) — set the timezone
  in your FF profile (ideally UTC), filter to high impact + the four
  currencies, and export/scrape month by month.
- **Investing.com** economic calendar — filter importance = 3 stars, export.
- **Myfxbook / FXStreet / Metals Mine** calendar exports — same idea.

What the analysis does with it: for every closed trade in the chosen run
it flags a high-impact release for either of the trade's currencies within
±4h of entry (configurable via `--window-hours`) or between entry and
exit, then reports win rate and average R for news-affected vs clean
trades, the per-pair split, and writes an annotated trade list
(`news/trades_annotated.csv`) naming the exact releases behind each flag.
