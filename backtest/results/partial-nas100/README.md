# NAS100 — best variant (step-1 filtered + partial at 1R, retest entry)

```
--sl-mode origin-swing --sl-buffer-pips 1.0 --rr 2.0 --partial-at-1r
--partial-frac 0.5 --warmup-swings 10 --min-break-pips 5.0
--trend-mode break --swing-n 2 --retest-window 24 --risk-pct 1.0
```

Identical settings to `partial-all7` / `partial-usdjpy`. Result: **PF 0.929,
avg R -0.034, net -2.92%, max DD 9.15% over 74 trades.**

## Two caveats on the numbers

**`--min-break-pips 5.0` is not scale-neutral.** It is 5 index points on an
instrument whose median 4H swing is 314.9 points — 1.6% of a swing, where the
same flag is 8.3% of a swing on EURUSD and 4.7% on USDJPY. NAS100 is
effectively running the bias filter wide open. Sweeping it:

```
   pts   % of swing   trades   win%      PF    avg R   max DD    net %
     5          1.6       74  29.73   0.929   -0.034     9.15    -2.92
    10          3.2       75  29.33   0.976   -0.007     9.15    -0.96
    15          4.8       75  29.33   0.976   -0.007     9.15    -0.96
    26          8.3       74  29.73   1.040   +0.027     6.84    +1.56
    40         12.7       74  31.08   1.066   +0.041     5.92    +2.57
    60         19.1       73  32.88   1.121   +0.068     5.92    +4.64
```

At 26 points (EURUSD's ratio) it crosses into profit. This is a real change of
trade set, not a rounding effect — mb=60 shares only 58 of mb=5's 74 trades.

**But none of it is distinguishable from zero.** Bootstrap 95% CI on avg R
(20,000 resamples):

```
  NAS100 min-break  5 pts   n=74   avg R -0.034   95% CI [-0.284, +0.223]
  NAS100 min-break 26 pts   n=74   avg R +0.027   95% CI [-0.223, +0.277]
  NAS100 min-break 60 pts   n=73   avg R +0.068   95% CI [-0.192, +0.329]
  USDJPY best variant       n=58   avg R +0.207   95% CI [-0.069, +0.483]
  all 7 FX best variant     n=363  avg R +0.045   95% CI [-0.061, +0.152]
```

Every interval spans zero, USDJPY included. The min-break sweep should be read
as "the flag is mis-scaled for an index", not as a tuning result.

## Setup fates

Instrumented in this run (`setup_fates` in summary.json): of 93 setups created,
75 filled, 17 were superseded by a later BOS, 1 expired, and **0 were cancelled
by a bias flip** — the same zero every FX pair shows. The reason is the fill
speed: median wait from BOS to fill is **1 bar**, mean 2.9. A Daily+8H bias
change inside a 3-hour window essentially never happens, so the bias gate does
all its filtering at BOS time and none during the wait.

Verified the counter is live rather than dead code: with the fill condition
disabled so setups must die some other way, it fires 66 times on NAS100 and 54
on USDJPY, matching an independent replication of the bias sequence.
