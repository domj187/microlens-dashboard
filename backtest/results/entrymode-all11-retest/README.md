# Entry mode: retest vs break-close, all 11 instruments

```
--sl-mode origin-swing --sl-buffer-pips 1.0 --rr 2.0 --partial-at-1r
--partial-frac 0.5 --warmup-swings 10 --min-break-atr 0.10
--trend-mode break --swing-n 2 --retest-window 24 --risk-pct 1.0
--entry-mode {retest | break-close}
```

Companion run in `../entrymode-all11-break-close/`. Single-instrument copies:
`../partial-xauusd-retest/`, `../partial-xauusd-break/`, `../partial-usdcad/`.

## Retest entry

```
  inst         n    win%      PF    avg R   total R
  USDJPY      56   33.9%   1.636   +0.250      14.0
  XAUUSD      74   33.8%   1.379   +0.169      12.5
  USDCAD      57   26.3%   1.240   +0.105       6.0
  GBPUSD      53   17.0%   1.275   +0.104       5.5
  EURCHF      28   28.6%   1.192   +0.089       2.5
  GBPJPY      63   28.6%   1.097   +0.048       3.0
  NAS100      74   29.7%   0.975   -0.014      -1.0
  AUDCHF      34   14.7%   0.967   -0.015      -0.5
  EURUSD      67   23.9%   0.875   -0.067      -4.5
  AUDUSD      57   19.3%   0.862   -0.070      -4.0
  CADJPY      65   21.5%   0.829   -0.092      -6.0
  ---------------------------------------------------
  pooled     628   25.8%   1.063   +0.044     +27.5   95% CI [-0.037, +0.126]
```

## Break-close entry

```
  inst         n    win%      PF    avg R   total R
  USDJPY      45   31.1%   1.647   +0.244      11.0
  XAUUSD      75   40.0%   1.485   +0.220      16.5
  USDCAD      50   30.0%   1.318   +0.140       7.0
  CADJPY      57   26.3%   0.950   -0.026      -1.5
  AUDUSD      55   18.2%   0.942   -0.027      -1.5
  GBPJPY      59   25.4%   0.935   -0.034      -2.0
  GBPUSD      52   15.4%   0.917   -0.038      -2.0
  EURCHF      25   12.0%   0.909   -0.040      -1.0
  NAS100      77   24.7%   0.837   -0.091      -7.0
  EURUSD      56   19.6%   0.800   -0.107      -6.0
  AUDCHF      60    8.3%   0.750   -0.117      -7.0
  ---------------------------------------------------
  pooled     611  23.73%   1.006   +0.011      +6.5   95% CI [-0.072, +0.093]
```

**Break-close is worse overall** — 8 of 11 instruments decline, and pooled avg
R falls from +0.044 to +0.011. It is not a general improvement.

## The prediction, and how it did

The pair-character read said gold's low retest availability (68.7%, lowest of
11) should make it the biggest beneficiary of skipping the retest. Ranked by
retest -> break-close improvement:

```
  inst       retest avail   break-close gain
  CADJPY            80.5%             +0.066
  XAUUSD            68.7%             +0.051
  AUDUSD            79.8%             +0.043
  USDCAD            79.2%             +0.035
  USDJPY            76.1%             -0.006
  EURUSD            77.1%             -0.040
  NAS100            77.1%             -0.077
  GBPJPY            78.2%             -0.082
  AUDCHF            83.5%             -0.102
  EURCHF            82.1%             -0.129
  GBPUSD            79.5%             -0.142
```

Gold does improve, and it is 2nd of 11 on the improvement — but **the
mechanism does not hold up**. CADJPY gains more with an ordinary 80.5% retest
rate, and AUDUSD (79.8%) gains more than USDCAD (79.2%). The correlation
between retest availability and break-close gain is **-0.426 (n=11,
t=-1.41)** — the predicted sign, but not distinguishable from noise at this
sample size. Treat "gold suits break-close" as an observation about gold, not
as a validated rule about low-retest instruments.

## Stop sizing

Break-close enters past the level, so risk widens on most instruments:

```
  inst      median stop (retest)   (break-close)
  XAUUSD                46.099          48.075     gold, price units ($)
  NAS100               355.064         399.505     index points
  USDJPY                 1.340           1.425
  USDCAD               0.00690         0.00803
```

Gold's median stop of $46 against a $40.67 median 4H swing is consistent with
`origin-swing` spanning the whole leg plus buffer — the point sizing is
behaving.

## Significance

Every per-instrument CI still spans zero, XAUUSD break-close included
(+0.220, 95% CI [-0.040, +0.480], n=75). The pooled retest CI spans zero too.
Nothing here is established; the tables rank, they do not prove.
