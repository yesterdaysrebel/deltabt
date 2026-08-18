# H-EMA-2 — TRAIN report

Frozen manifest: **135 arms**, preregistration sha256 `3fceea5e2a552be6a2505c8078ec7bc9…`
TRAIN 2025-01-01 → 2025-12-20. VALID not computed. TEST locked.

**Reconciliation: 135/135 arms passed** — per-trade and aggregate R totals tie out to 1e-9, trades ≤ eligible setups, no journal trade exceeds the 5% stop cap.

## Human summary
```
Arms tested:       135
Symbols:           4  (BTCUSD ETHUSD SOLUSD XRPUSD)
Timeframes:        5m / 15m / 1h

Total setups:      564,360
Eligible setups:   332,315
Executed trades:   155,715

Arms with >=200 trades:        84
  of those, net expectancy >0: 9
  of those, EXCESS GROSS >0:   53

Median cost/R   5m 0.305 | 15m 0.152 | 1h 0.077
Median stop %   5m 0.717 | 15m 1.235 | 1h 2.563
Median net R    5m -0.2875 | 15m -0.0650 | 1h -0.0541

Highest excess-gross arm (DESCRIPTIVE, not selected):
M3|15m|20/50|v1.2
Trades:            276
Win rate:          44.9%
Gross expectancy:  +0.3478R
NET:               +0.2434R
C-b gross:         +0.0274R
Excess GROSS:      +0.3205R
```

## The headline finding

EMA gross expectancy is indistinguishable from a random-direction control at every timeframe.

| exec_tf | arms | arm_gross | ca_gross | cb_gross | excess_gross | arm_cost | ca_cost | cb_cost | arm_net |
|---|---|---|---|---|---|---|---|---|---|
| 5 | 35 | 0.0302 | 0.0361 | 0.0305 | 0.0057 | 0.3096 | 2.5202 | 0.5815 | -0.3061 |
| 15 | 30 | 0.071 | 0.0481 | 0.0581 | 0.0218 | 0.1597 | 1.3707 | 0.2741 | -0.108 |
| 60 | 19 | 0.0455 | 0.0361 | 0.0441 | 0.0151 | 0.0862 | 0.5651 | 0.1175 | -0.0479 |

**The pre-registered primary quantity (`EMA net − C-b net`) is contaminated and must not be read as signal.** `stop_long = min(ST, leg_lo)` and `stop_short = max(ST, leg_hi)` are different distances at the same bar, so randomising direction hands the control the *other* stop — in a trending leg, the tighter one. Its R shrinks, cost/R rises, and the arm wins on cost rather than on prediction. Matching the median stop width does not repair it because cost/R ∝ 1/stop and its mean is driven by the small-stop tail.

The control was frozen before TRAIN and is **not** being altered now. `Excess gross`, also a pre-registered §17 output, is unaffected by the cost asymmetry and is used as the clean comparison.

## Timeframe summary

| exec_tf | arms | setups | eligible | trades | skipped_stop | med_gross | med_net | med_stop_pct | med_cost_R | med_excess_gross |
|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 45 | 400982 | 235663 | 110787 | 427 | 0.0366 | -0.2875 | 0.7172 | 0.3047 | 0.0086 |
| 15 | 45 | 131443 | 77477 | 35755 | 842 | 0.0939 | -0.065 | 1.235 | 0.1525 | 0.0495 |
| 60 | 45 | 31935 | 19175 | 9173 | 1431 | 0.0278 | -0.0541 | 2.5627 | 0.0765 | 0.0098 |

Cost/R falls by 3.6x from 5m to 1h and median stop width rises 3.6x — the mechanical relationship H-EMA-1 predicted, now measured directly. Net expectancy improves with it but never reaches zero.

## Mechanism summary

| mechanism | arms | trades | med_gross | med_net | med_excess_gross | med_cost_R | net_pos | excess_gross_pos | best_arm | worst_arm |
|---|---|---|---|---|---|---|---|---|---|---|
| M1 | 15 | 30701 | 0.0469 | -0.105 | 0.0182 | 0.1525 | 2 | 10 | M1|15m|12/26 | M1|1h|5/20 |
| M2 | 45 | 39228 | 0.0788 | -0.0293 | 0.0545 | 0.119 | 17 | 27 | M2|1h|50/200|s0.25 | M2|1h|20/50|s0.50 |
| M3 | 30 | 7327 | 0.0824 | -0.0525 | 0.0495 | 0.0986 | 12 | 21 | M3|1h|50/200|v1.2 | M3|1h|12/26|v1.5 |
| M4 | 30 | 50935 | 0.0417 | -0.2422 | 0.0062 | 0.301 | 0 | 17 | M4|15m|50/200|d0.25 | M4|1h|50/200|d0.5 |
| M5 | 15 | 27524 | 0.0377 | -0.1578 | 0.0071 | 0.1748 | 2 | 10 | M5|1h|50/200 | M5|15m|50/200 |

## Setup funnel (all arms aggregated)
```
Setups detected             564,360
   - warmup                          178
   - stop invalid                      0
   - no entry bar                     11
   - outside split               231,856
Eligible setups             332,315
   - stop >5% or <=0               2,700
   - size rounds to 0                  0
   - position already open       173,900
Trades entered              155,715
```

**52.3% of eligible setups never traded because a position was already open** — the frozen one-position-at-a-time rule, not signal scarcity. The 5% stop cap removed 2,700 setups, concentrated at 1h (1,431 of them).

## Descriptive rankings (NOT selection)

Pre-declared in §15.3. These do not gate VALID; all 135 arms are reported in `summaries/`.

### Top 10 by excess gross vs C-b (n>=200)

| candidate_id | trades | win_rate | gross_expectancy | cost_per_R | net_expectancy | cb_gross | excess_gross_cb | median_stop_pct | t_gross |
|---|---|---|---|---|---|---|---|---|---|
| M3|15m|20/50|v1.2 | 276 | 0.4493 | 0.3478 | 0.1045 | 0.2434 | 0.0274 | 0.3205 | 1.8983 | 3.995 |
| M4|15m|12/26|d0.25 | 1017 | 0.3677 | 0.1062 | 0.3168 | -0.2107 | -0.0093 | 0.1155 | 0.6318 | 2.247 |
| M2|15m|12/26|s0.25 | 868 | 0.3952 | 0.1855 | 0.1671 | 0.0184 | 0.0794 | 0.1061 | 1.1662 | 3.464 |
| M2|15m|20/50|s0.25 | 457 | 0.3851 | 0.1554 | 0.1231 | 0.0322 | 0.0511 | 0.1042 | 1.6366 | 2.241 |
| M5|1h|12/26 | 329 | 0.3708 | 0.1125 | 0.0969 | 0.0155 | 0.0146 | 0.0979 | 2.1988 | 1.238 |
| M2|15m|12/26|s0.00 | 1640 | 0.3774 | 0.1323 | 0.16 | -0.0277 | 0.0405 | 0.0919 | 1.1874 | 3.183 |
| M4|15m|20/50|d0.25 | 438 | 0.3767 | 0.137 | 0.3664 | -0.2294 | 0.0574 | 0.0796 | 0.59 | 1.937 |
| M4|5m|50/200|d0.5 | 373 | 0.3029 | 0.0858 | 1.1593 | -1.0735 | 0.0108 | 0.075 | 0.2237 | 1.172 |
| M2|15m|50/200|s0.00 | 393 | 0.3791 | 0.145 | 0.1212 | 0.0239 | 0.0703 | 0.0748 | 2.1023 | 1.822 |
| M3|5m|20/50|v1.2 | 660 | 0.353 | 0.0636 | 0.1949 | -0.1313 | -0.0035 | 0.0671 | 1.0813 | 1.118 |

### Top 10 by net expectancy (n>=200)

| candidate_id | trades | gross_expectancy | cost_per_R | net_expectancy | cb_net | excess_gross_cb | median_stop_pct |
|---|---|---|---|---|---|---|---|
| M3|15m|20/50|v1.2 | 276 | 0.3478 | 0.1045 | 0.2434 | -0.3776 | 0.3205 | 1.8983 |
| M2|1h|5/20|s0.00 | 482 | 0.1141 | 0.0778 | 0.0363 | -0.0484 | 0.0617 | 2.5021 |
| M2|15m|20/50|s0.25 | 457 | 0.1554 | 0.1231 | 0.0322 | -0.1435 | 0.1042 | 1.6366 |
| M2|1h|12/26|s0.25 | 220 | 0.1045 | 0.0745 | 0.0301 | -0.0541 | 0.0668 | 2.4179 |
| M2|15m|50/200|s0.00 | 393 | 0.145 | 0.1212 | 0.0239 | -0.2919 | 0.0748 | 2.1023 |
| M1|15m|50/200 | 392 | 0.1403 | 0.1187 | 0.0216 | -0.2556 | 0.0514 | 2.1159 |
| M2|1h|12/26|s0.00 | 479 | 0.096 | 0.0768 | 0.0192 | -0.0652 | 0.0402 | 2.4239 |
| M2|15m|12/26|s0.25 | 868 | 0.1855 | 0.1671 | 0.0184 | -0.1448 | 0.1061 | 1.1662 |
| M5|1h|12/26 | 329 | 0.1125 | 0.0969 | 0.0155 | -0.0996 | 0.0979 | 2.1988 |
| M1|1h|12/26 | 489 | 0.0736 | 0.0779 | -0.0043 | -0.0552 | 0.0195 | 2.4239 |

### Worst 5 by net expectancy (n>=200)

| candidate_id | trades | gross_expectancy | cost_per_R | net_expectancy | median_stop_pct |
|---|---|---|---|---|---|
| M4|5m|50/200|d0.25 | 218 | 0.0183 | 1.2744 | -1.256 | 0.2121 |
| M4|5m|50/200|d0.5 | 373 | 0.0858 | 1.1593 | -1.0735 | 0.2237 |
| M4|5m|20/50|d0.25 | 1561 | 0.009 | 0.756 | -0.7471 | 0.3165 |
| M4|5m|20/50|d0.5 | 2466 | 0.0438 | 0.7217 | -0.6779 | 0.3486 |
| M4|5m|12/26|d0.25 | 3506 | 0.0807 | 0.6146 | -0.5339 | 0.3419 |

## Control seed sensitivity

| candidate_id | cb_net | cb_seed_sd | cb_min | cb_max | cb_gross |
|---|---|---|---|---|---|
| M3|15m|20/50|v1.2 | -0.3776 | 0.5662 | -1.3077 | 0.0661 | 0.0274 |
| M4|15m|12/26|d0.25 | -0.5617 | 0.1047 | -0.7154 | -0.4467 | -0.0093 |
| M2|15m|12/26|s0.25 | -0.1448 | 0.0276 | -0.1738 | -0.1121 | 0.0794 |
| M2|15m|20/50|s0.25 | -0.1435 | 0.0836 | -0.2626 | -0.0506 | 0.0511 |
| M5|1h|12/26 | -0.0996 | 0.075 | -0.2172 | -0.0182 | 0.0146 |
| M2|15m|12/26|s0.00 | -0.183 | 0.0406 | -0.2497 | -0.145 | 0.0405 |
| M4|15m|20/50|d0.25 | -0.5441 | 0.2094 | -0.8777 | -0.3654 | 0.0574 |
| M4|5m|50/200|d0.5 | -1.9315 | 0.4879 | -2.5038 | -1.3905 | 0.0108 |
| M2|15m|50/200|s0.00 | -0.2919 | 0.3889 | -0.9629 | 0.0283 | 0.0703 |
| M3|5m|20/50|v1.2 | -0.4189 | 0.2164 | -0.6909 | -0.1649 | -0.0035 |

Across all eligible arms the C-b across-seed sd of net expectancy has median 0.1049R, so the control estimate is stable; the problem with it is bias, not variance.

## Multiplicity

135 arms were tested. Under that many correlated tests the largest |t| arising from noise alone is roughly 2.8–3.0. The maximum observed t_gross among eligible arms is 4.00 and the minimum is -1.35. No arm is called promising on a t-statistic, and the best observed arm was not independently pre-selected.

Four effects are separated deliberately:

- **A. Signal edge** — EMA predicts direction. Measured by excess gross: **+0.006 to +0.022 R, i.e. none.**
- **B. Timeframe effect** — higher TF improves signal quality. Median gross is 0.030 / 0.071 / 0.046 across 5m/15m/1h with no monotone trend; not supported.
- **C. Stop-geometry effect** — higher TF widens stops and cuts cost/R from 0.310 to 0.086. **Strongly present, and it is the entire reason net expectancy improves with timeframe.**
- **D. Selection effect** — with 135 arms, the best cells have small samples. The arm with the highest raw net expectancy has a fraction of the median arm's trade count.

## Per-symbol dispersion

Across 79 arms with >=50 trades in every symbol, the median within-arm spread between the best and worst symbol's net expectancy is **0.309R** — larger than any arm's measured edge, so aggregate figures are not driven by a shared effect.

| candidate_id | symbol | trades | win_rate | gross_expectancy | net_expectancy | median_stop_pct | cost_per_R |
|---|---|---|---|---|---|---|---|
| M3|15m|20/50|v1.2 | BTCUSD | 76 | 0.4737 | 0.4211 | 0.2692 | 1.1671 | 0.1518 |
| M3|15m|20/50|v1.2 | ETHUSD | 81 | 0.4691 | 0.4074 | 0.3156 | 1.899 | 0.0919 |
| M3|15m|20/50|v1.2 | SOLUSD | 60 | 0.5 | 0.5 | 0.4192 | 2.5118 | 0.0808 |
| M3|15m|20/50|v1.2 | XRPUSD | 59 | 0.339 | 0.0169 | -0.0679 | 2.0658 | 0.0849 |
