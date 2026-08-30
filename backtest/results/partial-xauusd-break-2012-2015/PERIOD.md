# XAUUSD break-close, 2012-2015 out-of-sample (gold -32%)

Same config as the 2023-2026 run, `--data-dir data_2012_2015`:

```
--sl-mode origin-swing --sl-buffer-pips 1.0 --rr 2.0 --partial-at-1r
--partial-frac 0.5 --warmup-swings 10 --min-break-atr 0.10
--trend-mode break --swing-n 2 --retest-window 24 --risk-pct 1.0
--entry-mode break-close
```

## Headline: it holds in the opposite regime

```
  window                gold move  trades   L/S split     PF   avg R   maxDD    net%
  2012-2015 bear           -32.2%      74   27L / 47S  1.323  +0.155   5.44%  +11.68%
  2023-2026 bull          +128.6%      75   54L / 21S  1.484  +0.220   5.47%  +17.33%
```

Near-identical trade count and drawdown, four years apart, in opposite
regimes. Against buy-and-hold: **+43.9pp in the bear window**, -111.3pp in
the bull one.

## By direction — the profitable side flips with the regime

```
                       n    win%      PF    avg R   tot R             95% CI
  2012-15 long        27   29.6%   1.154   +0.074     2.0   [-0.333, +0.500]
  2012-15 short       47   34.0%   1.475   +0.202     9.5   [-0.106, +0.521]
  2012-15 all         74   32.4%   1.348   +0.155    11.5   [-0.095, +0.412]

  2023-26 long        54   48.1%   1.909   +0.370    20.0   [+0.056, +0.676]
  2023-26 short       21   19.0%   0.708   -0.167    -3.5   [-0.595, +0.286]
  2023-26 all         75   40.0%   1.485   +0.220    16.5   [-0.040, +0.480]
```

This answers the question the 2023-2026 window could not. There, longs earned
everything and shorts lost, and the window was one long bull market, so
"the strategy works" and "gold went up" were the same variable. Here the
market fell 32% and **shorts carry the run instead**, with longs roughly flat.
The 2023-2026 long result was not a long-only artifact.

## By year and direction

```
  2012 long     2   +1.000   +2.0        2012 short    7   +0.357   +2.5
  2013 long     7   -0.214   -1.5        2013 short   16   +0.125   +2.0
  2014 long    10   -0.050   -0.5        2014 short    8   +0.438   +3.5
  2015 long     8   +0.250   +2.0        2015 short   16   +0.094   +1.5
```

Shorts are positive in all four years. Longs are negative in 2013 and 2014,
the two years gold fell hardest.

## By calendar period

```
  period    trades     W/P/L    avg R  total R    price move
  2012           9     3/4/2   +0.500     +4.5   +1100p rising
  2013          23    7/4/12   +0.022     +0.5   -4696p falling
  2014          18     6/4/8   +0.167     +3.0    -209p falling
  2015          24    8/5/11   +0.146     +3.5   -1233p falling

  2012 H1        3     1/2/0   +0.833     +2.5
  2012 H2        6     2/2/2   +0.333     +2.0
  2013 H1       11     4/2/5   +0.182     +2.0
  2013 H2       12     3/2/7   -0.125     -1.5
  2014 H1        8     2/2/4   +0.000     +0.0
  2014 H2       10     4/2/4   +0.300     +3.0
  2015 H1       10     4/3/3   +0.450     +4.5
  2015 H2       14     4/2/8   -0.071     -1.0

  best half-year 2015 H1 +4.5R (39% of total); 5 of 8 halves profitable;
  total less the best half +7.0R over 64 trades
```

Same shape as 2023-2026: profitable in most sub-periods, one half carrying
~two-fifths of the total, and it stays positive with that half removed.

## What is and is not established

**Established.** The bias filter allocates trades to the dominant direction
(47 shorts / 27 longs when gold fell; 54 longs / 21 shorts when it rose), and
the run is profitable in both regimes at nearly identical drawdown. Trade
allocation is measured from entries, independent of outcomes, so this is not
circular. The strategy is a working trend-follower, not a long-only proxy.

**Not established, and a caveat on my earlier analysis.** Bucketing trades by
whether the entry month rose or fell gives a stark split in both windows:

```
                              n    win%    avg R   tot R
  2012-15  with the move     57   63.2%   +0.316   +18.0
  2012-15  against           17   29.4%   -0.382    -6.5
  2023-26  with the move     62   61.3%   +0.387   +24.0
  2023-26  against           13   23.1%   -0.577    -7.5
```

The structure replicates exactly across both regimes, but **this test is
partly circular** and I over-weighted it in the 2023-2026 write-up. A trade's
R and the month's net move are both driven by the same price path, so wins
cluster into with-trend months by construction. It describes the strategy's
dependence on trend persistence; it is not independent evidence of it. The
non-circular evidence is the regime comparison above.

Per-window CIs still span zero except 2023-26 longs. Two windows of ~75
trades each is not enough to call the edge established.
