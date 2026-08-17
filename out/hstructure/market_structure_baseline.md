# H-Structure-1 — Null baselines (§12)

TRAIN window only. Every baseline runs through the **same** simulator, structural stop, 2R target, sizing and production cost model as the signal candidates; only the entry rule differs.

- Universe: `['BTCUSD', 'ETHUSD', 'SOLUSD', 'XRPUSD']`
- TRAIN: 2025-01-01 00:00:00 → 2025-12-20 01:43:48
- Excluded: {'AKEUSD': 'listed 2026-07-22, data is TEST-only', 'BANKUSD': 'listed 2026-07-22, data is TEST-only'}

## NULL-3 — unconditional long / short

Enter every structure bar, one position at a time. Whatever this earns is drift and payoff geometry, not signal.

| label | trades | win_rate | gross_r | cost_r | net_r | t_gross | pf_gross | stop_pct_median | pct_target |
|---|---|---|---|---|---|---|---|---|---|
| UNCOND_LONG|5m | 6630 | 0.2798 | -0.024 | 2.4127 | -2.4367 | -1.377 | 0.964 | 0.4056 | 32.5 |
| UNCOND_SHORT|5m | 7710 | 0.2767 | 0.0089 | 2.4088 | -2.3999 | 0.495 | 1.013 | 0.332 | 33.6 |
| UNCOND_LONG|15m | 3211 | 0.2996 | -0.0143 | 1.1778 | -1.1922 | -0.561 | 0.979 | 0.7027 | 32.9 |
| UNCOND_SHORT|15m | 3477 | 0.3106 | 0.0311 | 1.3955 | -1.3644 | 1.263 | 1.047 | 0.5695 | 34.4 |
| UNCOND_LONG|60m | 1411 | 0.3189 | -0.0177 | 0.5345 | -0.5522 | -0.488 | 0.974 | 1.1938 | 32.7 |
| UNCOND_SHORT|60m | 1379 | 0.3387 | 0.0529 | 0.7194 | -0.6665 | 1.281 | 1.082 | 1.1197 | 35.1 |

## NULL-1 / NULL-2 — randomised direction and randomised entry

NULL-1 keeps the candidate's own entry times and flips a coin for direction. NULL-2 randomises both time and direction at matched trade count. `z` is (signal gross − null mean gross) / null sd across 100 simulations.

| family | ref | signal_trades | signal_gross | null_dir_gross | null_dir_sd | z_vs_dir | null_rnd_gross | null_rnd_sd | z_vs_rnd |
|---|---|---|---|---|---|---|---|---|---|
| A | N5|15m|oneshot | 1338 | 0.0291 | 0.0423 | 0.0321 | -0.41 | 0.0317 | 0.0587 | -0.04 |
| B | N5|15m|oneshot | 1433 | 0.0656 | 0.0121 | 0.0348 | 1.54 | 0.0194 | 0.0483 | 0.96 |
| C | N5|15m|oneshot | 1322 | 0.0439 | 0.025 | 0.0274 | 0.69 | 0.0434 | 0.0514 | 0.01 |
| D | N5|15m|oneshot | 1058 | 0.0236 | -0.0061 | 0.0349 | 0.85 | 0.0344 | 0.0538 | -0.2 |

## Swing census

Share of TRAIN structure bars in each state, by timeframe and swing strength (mean over the universe).

| struct_tf | swing_n | bars | bull_pct | bear_pct | conf_delay_min |
|---|---|---|---|---|---|
| 5 | 2 | 101,685.0 | 30.81 | 30.56 | 10 |
| 5 | 3 | 101,685.0 | 30.84 | 29.91 | 15 |
| 5 | 5 | 101,685.0 | 30.94 | 29.72 | 25 |
| 5 | 8 | 101,685.0 | 30.85 | 29.68 | 40 |
| 15 | 2 | 33,895.0 | 30.82 | 29.84 | 30 |
| 15 | 3 | 33,895.0 | 30.51 | 29.43 | 45 |
| 15 | 5 | 33,895.0 | 30.24 | 29.14 | 75 |
| 15 | 8 | 33,895.0 | 29.88 | 29.86 | 120 |
| 60 | 2 | 8,474.0 | 30.24 | 29.66 | 120 |
| 60 | 3 | 8,474.0 | 29.5 | 29.93 | 180 |
| 60 | 5 | 8,474.0 | 30.02 | 30.1 | 300 |
| 60 | 8 | 8,474.0 | 29.73 | 28.78 | 480 |
