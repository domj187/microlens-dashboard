# USDCAD 2012-2015 out-of-sample vs 2023-2026

Best variant, retest entry, `--data-dir data_2012_2015`:

```
--sl-mode origin-swing --sl-buffer-pips 1.0 --rr 2.0 --partial-at-1r
--partial-frac 0.5 --warmup-swings 10 --min-break-atr 0.10
--trend-mode break --swing-n 2 --retest-window 24 --risk-pct 1.0
--entry-mode retest
```

## Headline: the numbers replicate closely

```
  window          USDCAD move   trades  L/S split    win%      PF   avg R   maxDD    net%
  2012-2015            +35.6%       75  45L / 30S   28.0%   1.221  +0.100   5.85%  +7.33%
  2023-2026             +2.8%       57  36L / 21S   26.3%   1.240  +0.105   5.85%  +5.85%
```

Profit factor within 0.02, avg R within 0.005, identical drawdown, across
windows 8 years apart. As a stability check this is the closest match in the
project.

The two windows are different regimes, but not opposite ones: 2012-2015 is a
sustained 35.6% uptrend (range 0.9633-1.4001, 45.3% wide); 2023-2026 is
essentially flat at +2.8% (range 1.3177-1.4793, 12.3% wide). This is
trending vs ranging, not up vs down.

## By direction — flips, and in the direction the regime implies

```
                       n    win%      PF    avg R   tot R             95% CI
  2012-15 long        45   28.9%   1.472   +0.189     8.5   [-0.122, +0.489]
  2012-15 short       30   26.7%   0.938   -0.033    -1.0   [-0.417, +0.367]

  2023-26 long        36   25.0%   1.000   +0.000     0.0   [-0.333, +0.347]
  2023-26 short       21   28.6%   1.857   +0.286     6.0   [-0.143, +0.690]
```

In the uptrend, longs carry it and shorts are flat. In the flat window, longs
return exactly zero and the whole result comes from 21 shorts. Same pattern as
gold — the profitable side follows the regime — but the 2023-2026 short result
rests on 21 trades whose CI spans zero, and its best year (2025, +0.833) is
3 trades. Do not lean on it.

## By year and direction

```
  2012-2015                          2023-2026
  2012 long   7  +0.286  +2.0        2023 long   2  -1.000  -2.0  (n<5)
  2012 short 10  +0.050  +0.5        2023 short  3  +0.833  +2.5  (n<5)
  2013 long   8  +0.188  +1.5        2024 long  14  +0.036  +0.5
  2013 short  7  -0.071  -0.5        2024 short  9  -0.278  -2.5
  2014 long  14  +0.036  +0.5        2025 long   9  +0.056  +0.5
  2014 short 10  -0.100  -1.0        2025 short  3  +0.833  +2.5  (n<5)
  2015 long  16  +0.281  +4.5        2026 long  11  +0.091  +1.0
  2015 short  3  +0.000   0.0        2026 short  6  +0.583  +3.5
```

Longs are positive in all four years of the uptrend. No year in either window
reaches a magnitude that survives its own error bar.

## By calendar period

```
  2012-2015                                  2023-2026
  2012 H1  11  -0.045  -0.5                  2023 H2   5  +0.100  +0.5
  2012 H2   6  +0.500  +3.0                  2024 H1  11  -0.409  -4.5
  2013 H1   7  -0.357  -2.5                  2024 H2  12  +0.208  +2.5
  2013 H2   8  +0.438  +3.5                  2025 H1   9  +0.333  +3.0
  2014 H1  11  +0.318  +3.5                  2025 H2   3  +0.000   0.0
  2014 H2  13  -0.308  -4.0                  2026 H1  15  +0.267  +4.0
  2015 H1  10  +0.400  +4.0                  2026 H2   2  +0.250  +0.5
  2015 H2   9  +0.056  +0.5
```

## The weakness: both windows lean on one half-year

```
                       best half   share of total R   remainder
  2012-2015              2015 H1                53%   +3.5R over 65 trades (+0.054)
  2023-2026              2026 H1                67%   +2.0R over 42 trades (+0.048)
```

Gold's equivalents were 39% and 36%, leaving +0.11 and +0.18 avg R. USDCAD
strips down to about +0.05 avg R in both windows, which is indistinguishable
from nothing. 5 of 8 and 5 of 7 halves are profitable, so it is not one
freak period — but the magnitude lives in one.

## Verdict

The **replication is real**: two windows, 8 years apart, PF 1.221 vs 1.240 and
avg R +0.100 vs +0.105 at the same 5.85% drawdown. Direction allocation
behaves the way it does on gold.

The **magnitude is not established**. Every CI in this document spans zero,
and removing the single best half-year in each window leaves ~+0.05 avg R.
USDCAD looks consistent rather than profitable; it is a weaker result than
gold, where the remainder after removing the best half was still +0.11 to
+0.18 avg R and the direction flip was between a real uptrend and a real
downtrend rather than a trend and a range.
