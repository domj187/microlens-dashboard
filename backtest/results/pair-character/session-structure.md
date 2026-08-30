# Session breaks and holiday closures: do NAS100 and XAUUSD's calendars distort the read?

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


---

# XAUUSD — same calendar, same verdict

Gold's session calendar is **identical** to NAS100's: 23 bars on a normal
weekday, the same 17:00-18:00 New York break, and the same three missing
Good Fridays (2024-03-29, 2025-04-18, 2026-04-03). It differs only in
trading through more US holidays than the index does.

```
                  1H bars   normal weekday   zero-bar weekdays   4H buckets (bars each)
  XAUUSD            17723               23                   3   4:3840  3:774  2:19  1:3
  NAS100            17659               23                   3   4:3831  3:774  2:2   1:9
  USDCAD            18660               24                   0   4:4662  3:2    2:3
```

Same anchor sweep, same conclusion — the short daily bar is swing-blind, but
that bias does not reach the follow-through number:

| anchor | swing rate full / short | breaks | follow-through |
|---|---|---|---|
| 17:00 NY (current) | 28.1% / **18.7%** | 579 | **46.3%** |
| +1h | 30.0% / **10.1%** | 582 | 46.2% |
| +2h | 30.2% / **11.0%** | 592 | 47.6% |
| +3h | 30.4% / **10.1%** | 617 | 46.5% |

Follow-through spans 46.2%-47.6%, i.e. ±0.7pp — the same tolerance NAS100
showed. Gold's +13.0pp edge over the random-walk baseline is not an artifact
of its session breaks.

**USDCAD needs none of this**: 24 bars every weekday, no closures, 4662 of
4667 4H buckets full. It is a plain 24/5 FX pair.

## The one number that is NOT a calendar artifact: gold's retest rate

XAUUSD posts the lowest retest availability in the set, 68.7%. Because gold's
23-bar day makes a 24-*bar* window slightly *longer* in wall-clock terms than
an FX pair's, the session break would if anything inflate that number, not
depress it. Measured across window lengths, gold is lowest at every one:

```
  inst            12       24       48       96      240   (1H bars after the BOS)
  USDJPY       66.0%    76.1%    84.2%    88.9%    93.2%
  USDCAD       71.3%    79.2%    84.5%    88.7%    92.8%
  NAS100       63.7%    77.1%    82.6%    87.2%    91.9%
  XAUUSD       58.9%    68.7%    77.2%    81.9%    89.1%   <- lowest at every window
  AUDCHF       76.4%    83.5%    87.7%    91.7%    94.4%
```

It pairs with the most skewed swing distribution in the set (mean/median leg
1.49, against 1.15-1.26 everywhere else). Gold breaks structure, runs in big
lumpy legs, and does not come back to offer the entry.
