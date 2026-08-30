# All 8 instruments on equal terms — `--min-break-atr 0.10`

```
--sl-mode origin-swing --sl-buffer-pips 1.0 --rr 2.0 --partial-at-1r
--partial-frac 0.5 --warmup-swings 10 --min-break-atr 0.10
--trend-mode break --swing-n 2 --retest-window 24 --risk-pct 1.0
```

The only change from `partial-all7` / `partial-nas100` is that the D/8H break
filter is sized in ATR rather than pips, so one number means the same thing on
EURUSD and on an index quoted in points.

## Pooled

```
                trades   win%      PF   avg R   maxDD%    net%
  pips 5.0         437  24.94   1.041   0.032    27.33   10.43
  atr 0.10         432  25.00   1.048   0.035    27.33   11.90
```

Essentially unchanged — which is the expected result. 0.10 was picked because
it is closest to what `--min-break-pips 5` was already applying to the FX
pairs (their median was 0.135 of 8H ATR, 0.074 of Daily), so the FX side
should barely move and it doesn't.

## Per instrument

```
  inst         n  avgR pips     n  avgR atr    delta            95% CI (atr)
  AUDCHF      32     +0.078    34    -0.015   -0.093   [-0.324, +0.309]
  AUDUSD      58     -0.060    57    -0.070   -0.010   [-0.325, +0.193]
  EURCHF      27     +0.130    28    +0.089   -0.040   [-0.304, +0.482]
  EURUSD      70     -0.086    67    -0.067   +0.019   [-0.321, +0.194]
  GBPJPY      65     +0.038    63    +0.048   +0.009   [-0.222, +0.317]
  GBPUSD      53     +0.104    53    +0.104   +0.000   [-0.142, +0.349]
  USDJPY      58     +0.207    56    +0.250   +0.043   [-0.036, +0.518]
  NAS100      74     -0.034    74    -0.014   +0.020   [-0.264, +0.243]

  pooled pips 5.0   n=437   avg R +0.032   95% CI [-0.068, +0.129]
  pooled atr 0.10   n=432   avg R +0.035   95% CI [-0.064, +0.135]
```

Every per-instrument CI spans zero, and so does the pooled one. Nothing here
is established; the ordering (USDJPY best, EURUSD/AUDUSD worst) is the same as
before.

## Why 0.10 and not the sweep's best

The in-sample sweep is monotone up to 0.20:

```
  atr frac   trades   win%      PF   avg R   maxDD%    net%
  0.05          441  24.72   1.026   0.023    28.08    6.43
  0.10          432  25.00   1.048   0.035    27.33   11.90
  0.15          425  24.94   1.079   0.051    26.64   19.27
  0.20          431  25.29   1.111   0.064    25.56   27.55
  0.30          419  25.06   1.108   0.063    25.58   26.13
```

Taking 0.20 would be selecting on the test set. Checked against the 2020-2022
USDJPY out-of-sample data, the ranking **inverts**:

```
  USDJPY 2020-2022    trades   win%      PF   avg R   maxDD%
  pips 5.0                63  30.16   1.292   0.135     4.92
  atr 0.10                61  29.51   1.287   0.131     3.96
  atr 0.15                64  29.69   1.249   0.117     5.87
  atr 0.20                63  28.57   1.198   0.095     5.94
  atr 0.30                54  29.63   1.209   0.102     5.94
```

Performance falls monotonically as the fraction rises — the opposite of the
in-sample sweep. The sweep was fitting noise. The flag is a units fix; it is
not a source of edge.
