# XAUUSD break-close — is the edge spread, or is it the bull leg?

Short answer: **spread across calendar time, but entirely conditional on
direction and on gold rising.** The window contains no evidence the strategy
can trade gold's declines, and the whole 3-year window is one bull market, so
"spread across the window" and "it's the bull leg" are the same statement here.

## By calendar year

```
  period       trades     W/P/L    avg R  total R       P&L   price move
  2023              6     1/0/5   -0.583     -3.5      -347   +1154p rising
  2024             27   11/3/13   +0.185     +5.0      +475   +5609p rising
  2025             26    12/5/9   +0.442    +11.5     +1213  +16933p rising
  2026             16     6/3/7   +0.219     +3.5      +392   +1267p rising
```

## By six-month period

```
  2023 H2           6     1/0/5   -0.583     -3.5      -347   +1154p rising
  2024 H1          11     4/0/7   -0.091     -1.0      -104   +2631p rising
  2024 H2          16     7/3/6   +0.375     +6.0      +579   +2975p rising
  2025 H1          14     6/3/5   +0.393     +5.5      +562   +6769p rising
  2025 H2          12     6/2/4   +0.500     +6.0      +651  +10155p rising
  2026 H1          13     5/2/6   +0.192     +2.5      +277   -3181p FALLING
  2026 H2           3     1/1/1   +0.333     +1.0      +115   +4410p rising
```

Best half-year is 2024 H2 at +6.0R, 36% of the run's total. Removing it still
leaves +10.5R over 59 trades (+0.178 avg R), and 5 of 7 halves are profitable.
On calendar concentration alone this looks healthy.

## By direction — where it actually lives

```
                   trades     win%      PF    avg R  total R
  long                 54    48.1%   1.909   +0.370     20.0
  short                21    19.0%   0.708   -0.167     -3.5
  all                  75    40.0%   1.485   +0.220     16.5
```

```
  95% CI on avg R
  longs   n=54   +0.370   [+0.056, +0.676]   <- excludes zero
  shorts  n=21   -0.167   [-0.595, +0.286]
  all     n=75   +0.220   [-0.040, +0.480]
```

Longs are the first result in this project whose CI excludes zero, and they
are profitable in every year but the 6-trade 2023 stub: 2024 +0.333, 2025
+0.524, 2026 +0.438.

## Why that is trend exposure rather than an edge

Bucketing long trades by whether gold rose or fell in the month they were
entered:

```
  gold rose   n=49   win 65.3%   avg R +0.510   95% CI [+0.184, +0.827]
  gold fell   n= 5   win  0.0%   avg R -1.000   95% CI [-1.000, -1.000]
```

**Every long entered in a falling month lost the full stop — 5 for 5, no
partials, no scratches:**

```
  2023-12-07  -1.0     2023-12-26  -1.0     2024-12-11  -1.0
  2026-04-01  -1.0     2026-05-07  -1.0
```

Across the 19 months carrying 2+ long trades, the correlation between gold's
monthly move and the long avg R is **+0.580 (t=+2.94)**. The long result is a
function of the trend, not independent of it.

## The 2026 H1 reading that looked like counter-evidence

2026 H1 is the only falling half-year (-3181p) and still returned +0.192 avg
R, which looks like the strategy handling a decline. Month by month it does
not:

```
  month       gold move  longs  shorts   long R
  2026-01         +5698      2       0   +1.500
  2026-02         +4582      1       0   +1.500
  2026-03         -6171      0       2        —
  2026-04         -1460      1       1   -1.000
  2026-05         -1508      1       2   -1.000
  2026-06         -4569      0       3        —
```

The H1 longs cluster in January and February, the two months gold rose hard.
Both longs entered in falling months lost in full. The half-year aggregate
hid the monthly structure.

## Benchmark

```
  buy and hold        gold 1947.51 -> 4452.54      +128.6%
  strategy (1% risk)  +16.5R                        +17.3%
```

Over a window in which gold more than doubled, a long-biased strategy
returned an eighth of buy-and-hold. That is the context for the +0.220 avg R.

## What would actually test it

Gold data covering a sustained decline — 2012-2015 would do. Until then the
long edge and gold's bull market are the same variable, and nothing here
separates them.
