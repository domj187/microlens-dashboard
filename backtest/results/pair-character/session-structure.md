# Session breaks and holiday closures: does NAS100's calendar distort the read?

NAS100 is the first instrument in the set that does not trade 24/5. Everything
below is measured on `data/NAS100_*.csv` (2023-08-30 → 2026-08-28) against the
FX pairs over the same window.

## What the calendar actually looks like

| | NAS100 | EURUSD | USDJPY |
|---|---|---|---|
| 1H bars | 17,659 | 18,247 | 18,209 |
| bars on a normal weekday | **23** | 24 | 24 |
| weekdays with zero bars | **3** (Good Friday ×3) | 0 | 0 |
| weekdays with < 21 bars | **30** | 7 | 7 |

The daily break is 17:00–18:00 New York (21:00–22:00 UTC in summer,
22:00–23:00 in winter). That is *exactly* the boundary `session_start()`
already uses for the daily candle, so the break falls on a day boundary and
the daily bars are unaffected.

## 1. Swing detection — a real bias, and unavoidable

A 23-hour session cannot be tiled by 4-hour bars: **one 4H bar per day is
always short**, whatever anchor you pick. With the current 17:00 NY anchor
it is the 18:00–21:00 NY bar, and it holds 3 hours of trading instead of 4:

```
NAS100 4H buckets: 3831 full (4x 1H) · 774 short (3x 1H) · 11 other (holidays)
EURUSD 4H buckets: 4559 full          ·   1 short         ·  4 other
```

That short bar has less range, so it is markedly less likely to be a fractal
extreme:

```
                 bars    swings    swing rate
NAS100 full(4)   3831      1099        28.7%
NAS100 short(3)   774        92        11.9%   <- 2.4x under-represented
```

So turning points that occur in the thin post-close hours are systematically
under-registered. This is genuine, not a bug — but it is worth knowing that
NAS100's swing set is not sampled uniformly across the day.

## 2. Follow-through measurement — bias does not propagate

The obvious worry is that a distorted swing set distorts the headline
follow-through number. Measured by sweeping the bucket anchor, which moves
the short bar to a different part of the day each time:

| anchor | short bar covers (NY) | swing rate full / short | breaks | follow-through |
|---|---|---|---|---|
| 17:00 NY (current) | 17:00, 09:00 | 28.7% / **11.9%** | 619 | **43.3%** |
| +1h | 14:00, 10:00 | 26.2% / **28.5%** | 610 | 44.1% |
| +2h | 15:00, 11:00 | 28.0% / **23.6%** | 631 | 42.8% |
| +3h | 16:00 | 29.6% / **11.7%** | 634 | 43.2% |

The short-bar handicap swings from 11.9% to 28.5% across anchors; follow-
through moves only between 42.8% and 44.1%, i.e. **±0.7pp around the reported
43.3%**. NAS100's +10pp edge over the 33.3% random-walk baseline survives
every anchor choice.

## 3. Gaps — larger, but they do not change the scoring

NAS100 gaps more than FX across the break and over holidays:

```
4H |open - prev close|      median   p95    max   p95 as % of median swing
NAS100                         0.3  28.2  997.6                      8.9%
EURUSD                         0.1   0.5  127.7                      0.8%
USDJPY                         0.1   1.0  204.1                      0.9%
```

The mechanism that could bite is a single bar spanning both the 2x-depth
target and the origin swing, which the analysis scores conservatively as a
reversal. Counted directly:

```
             breaks   both-in-one-bar   follow-through if scored the other way
NAS100          619            2 (0.3%)   43.6%   (reported 43.3%)
EURUSD          616            1 (0.2%)   41.1%   (reported 40.9%)
USDJPY          577            0 (0.0%)   48.4%   (reported 48.4%)
```

Worth 0.3pp. Not material.

## 4. Retest window — comparable

`--retest-window 24` is counted in *bars*, not hours, so a 23-bar day makes
NAS100's window slightly longer in wall-clock terms:

```
span of 24 1H bars    median   p95   max   > 48h
NAS100                   25h   73h  101h   21.8%
EURUSD                   24h   72h   96h   20.0%
```

One hour on the median. The >48h tail is the weekend and is the same for both.

## 5. Window mismatch

NAS100's data runs a month longer than the FX set (to 2026-08-28 vs
2026-07-31). Trimmed to the FX window the numbers are unchanged:
follow-through 43.4% vs 43.3%, edge +10.1pp vs +10.0pp, swing 2.66 vs
2.65× ATR, daily persistence 15.0d in both.

## Verdict

Swing detection **is** biased by the session break — the short daily bar is
2.4× less likely to register a swing. That bias does **not** reach the
follow-through or retest numbers, which hold to within a point across anchor
choices, gap-scoring rules and window trims. NAS100's structural read stands
as measured.
