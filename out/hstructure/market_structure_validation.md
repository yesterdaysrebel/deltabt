# H-Structure-1 — VALIDATION (§17)

Run once, after `frozen_candidates.json` was written. No swing parameter, structure definition, timeframe, entry rule, stop, target, cost model or symbol was changed on the basis of anything below.

- VALID: 2025-12-20 01:43:48 → 2026-04-16 18:18:24
- Core universe: `['BTCUSD', 'ETHUSD', 'SOLUSD', 'XRPUSD']`
- Supplementary (VALID-only): `['BEATUSD']`; excluded: ['AKEUSD', 'BANKUSD'] (TEST-only history)
- **TEST not computed.**

## Frozen candidates — TRAIN → VALID

| slot | candidate | trades_train | trades_valid | win_rate_train | win_rate_valid | gross_r_train | gross_r_valid | d_gross | t_gross_train | t_gross_valid | cost_r_train | cost_r_valid | net_r_train | net_r_valid | p_hit_2r_train | p_hit_2r_valid |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PRIMARY | C|N8|60m|oneshot | 257 | 106 | 0.3969 | 0.3302 | 0.1907 | -0.0094 | -0.2001 | 2.224 | -0.066 | 0.065 | 0.0706 | 0.1256 | -0.08 | 0.393 | 0.3302 |
| BEST_A | A|N8|15m|oneshot | 894 | 347 | 0.3557 | 0.3256 | 0.0705 | 0.0029 | -0.0676 | 1.354 | 0.028 | 0.2442 | 0.4661 | -0.1738 | -0.4632 | 0.3557 | 0.3343 |
| BEST_B | B|N8|60m|oneshot | 211 | 78 | 0.3886 | 0.3718 | 0.1659 | 0.1154 | -0.0505 | 1.629 | 0.606 | 0.1489 | 0.1909 | 0.0169 | -0.0755 | 0.3744 | 0.3718 |
| BEST_C | C|N8|60m|oneshot | 257 | 106 | 0.3969 | 0.3302 | 0.1907 | -0.0094 | -0.2001 | 2.224 | -0.066 | 0.065 | 0.0706 | 0.1256 | -0.08 | 0.393 | 0.3302 |
| BEST_D | D|N8|15m|oneshot | 766 | 236 | 0.3747 | 0.3814 | 0.124 | 0.1441 | 0.0201 | 2.395 | 1.283 | 0.104 | 0.1235 | 0.02 | 0.0206 | 0.3734 | 0.3771 |
| REF_A | A|N5|15m|oneshot | 1338 | 498 | 0.3393 | 0.3293 | 0.0291 | -0 | -0.0291 | 0.652 | -0 | 0.3512 | 0.3527 | -0.322 | -0.3527 | 0.3408 | 0.3333 |
| REF_B | B|N5|15m|oneshot | 1433 | 452 | 0.3461 | 0.3473 | 0.0656 | 0.0619 | -0.0037 | 1.648 | 0.642 | 0.3834 | 0.4056 | -0.3178 | -0.3437 | 0.3552 | 0.354 |
| REF_C | C|N5|15m|oneshot | 1322 | 434 | 0.348 | 0.3502 | 0.0439 | 0.0507 | 0.0068 | 1.012 | 0.594 | 0.1253 | 0.1561 | -0.0814 | -0.1054 | 0.3472 | 0.3502 |
| REF_D | D|N5|15m|oneshot | 1058 | 356 | 0.3412 | 0.3511 | 0.0236 | 0.0534 | 0.0298 | 0.502 | 0.596 | 0.1236 | 0.1552 | -0.0999 | -0.1019 | 0.3412 | 0.3511 |

## Null baselines on VALID

| label | trades | win_rate | gross_r | cost_r | net_r | t_gross | pf_gross |
|---|---|---|---|---|---|---|---|
| UNCOND_LONG|5m | 2028 | 0.2742 | -0.0444 | 1.996 | -2.0403 | -1.455 | 0.935 |
| UNCOND_SHORT|5m | 2019 | 0.2883 | 0.0074 | 3.1323 | -3.1248 | 0.227 | 1.011 |
| UNCOND_LONG|15m | 1217 | 0.3196 | 0.0551 | 1.3455 | -1.2904 | 1.393 | 1.085 |
| UNCOND_SHORT|15m | 872 | 0.3268 | 0.0562 | 1.4417 | -1.3855 | 1.216 | 1.087 |
| UNCOND_LONG|60m | 538 | 0.2993 | -0.0576 | 2.9302 | -2.9878 | -0.942 | 0.916 |
| UNCOND_SHORT|60m | 423 | 0.3357 | 0.0426 | 0.8841 | -0.8416 | 0.549 | 1.065 |

| family | signal_trades | signal_gross | null_dir_gross | null_dir_sd | z_vs_dir | null_rnd_gross | null_rnd_sd | z_vs_rnd |
|---|---|---|---|---|---|---|---|---|
| A | 498 | -0 | 0.057 | 0.056 | -1.02 | 0.0455 | 0.086 | -0.53 |
| B | 452 | 0.0619 | 0.084 | 0.0469 | -0.47 | 0.0318 | 0.0876 | 0.34 |
| C | 434 | 0.0507 | 0.0543 | 0.0517 | -0.07 | 0.0448 | 0.0762 | 0.08 |
| D | 356 | 0.0534 | 0.019 | 0.0634 | 0.54 | 0.0375 | 0.097 | 0.16 |

## §16 Time to move on VALID

A 2R target needs P(+2R before stop) > 1/3 for positive gross expectancy. Gross R is mechanically `3·P(2R) − 1`.

| slot | candidate | trades | p_05r | p_1r | p_2r | mfe_median |
|---|---|---|---|---|---|---|
| PRIMARY | C|N8|60m|oneshot | 106 | 0.67 | 0.481 | 0.33 | 0.96 |
| BEST_A | A|N8|15m|oneshot | 347 | 0.671 | 0.493 | 0.334 | 0.99 |
| BEST_B | B|N8|60m|oneshot | 78 | 0.718 | 0.5 | 0.372 | 0.99 |
| BEST_C | C|N8|60m|oneshot | 106 | 0.67 | 0.481 | 0.33 | 0.96 |
| BEST_D | D|N8|15m|oneshot | 236 | 0.691 | 0.538 | 0.377 | 1.2 |
| REF_A | A|N5|15m|oneshot | 498 | 0.701 | 0.538 | 0.333 | 1.11 |
| REF_B | B|N5|15m|oneshot | 452 | 0.69 | 0.527 | 0.354 | 1.15 |
| REF_C | C|N5|15m|oneshot | 434 | 0.664 | 0.498 | 0.35 | 0.98 |
| REF_D | D|N5|15m|oneshot | 356 | 0.666 | 0.5 | 0.351 | 1 |

## Supplementary symbol (BEATUSD, VALID only)

| label | trades | win_rate | gross_r | cost_r | net_r | t_gross | p_hit_2r |
|---|---|---|---|---|---|---|---|
| C|N8|60m|oneshot|BEATUSD | 0 |  |  |  |  |  |  |
| A|N8|15m|oneshot|BEATUSD | 42 | 0.2619 | -0.2143 | 0.1809 | -0.3952 | -0.833 | 0.2619 |
| B|N8|60m|oneshot|BEATUSD | 1 | 0 | -1 | -0.0421 | -0.9579 |  | 0 |
| C|N8|60m|oneshot|BEATUSD | 0 |  |  |  |  |  |  |
| D|N8|15m|oneshot|BEATUSD | 19 | 0.1579 | -0.5263 | 0.018 | -0.5443 |  | 0.1579 |
| A|N5|15m|oneshot|BEATUSD | 62 | 0.3065 | -0.0806 | 0.1845 | -0.2651 | -0.533 | 0.3065 |
| B|N5|15m|oneshot|BEATUSD | 37 | 0.3514 | 0.0541 | 0.003 | 0.0511 | 0.28 | 0.3514 |
| C|N5|15m|oneshot|BEATUSD | 66 | 0.2576 | -0.2273 | 0.0437 | -0.271 | -1.248 | 0.2576 |
| D|N5|15m|oneshot|BEATUSD | 38 | 0.2895 | -0.1316 | 0.0205 | -0.1521 | -0.583 | 0.2895 |

## Full grid on VALID (spike diagnostic, not a selection step)

| label | trades | effective_n | win_rate | gross_r | t_gross | cost_r | net_r | median_r | pf_gross | p_hit_2r |
|---|---|---|---|---|---|---|---|---|---|---|
| B|N8|15m|oneshot | 303 | 188.5 | 0.4026 | 0.2178 | 1.682 | 0.3911 | -0.1733 | -1.113 | 1.367 | 0.4059 |
| B|N3|60m|oneshot | 201 | 133.2 | 0.398 | 0.194 | 1.312 | 0.2792 | -0.0852 | -1.0962 | 1.322 | 0.398 |
| B|N8|15m|level | 475 | 471.4 | 0.3411 | 0.1747 | 2.361 | 2.5525 | -2.3778 | -1.1535 | 1.287 | 0.3916 |
| C|N2|60m|oneshot | 244 | 160 | 0.3893 | 0.168 | 1.187 | 0.1086 | 0.0594 | -1.0444 | 1.275 | 0.3893 |
| D|N8|60m|oneshot | 72 | 59.5 | 0.3889 | 0.1667 | 1.329 | 0.0782 | 0.0884 | -1.0051 | 1.273 | 0.3889 |
| B|N2|60m|oneshot | 256 | 171.8 | 0.3828 | 0.1484 | 1.246 | 0.4042 | -0.2558 | -1.1304 | 1.241 | 0.3828 |
| D|N8|15m|oneshot | 236 | 154.4 | 0.3814 | 0.1441 | 1.283 | 0.1235 | 0.0206 | -1.0507 | 1.233 | 0.3771 |
| C|N2|60m|level | 234 | 153.3 | 0.3803 | 0.141 | 1.06 | 0.101 | 0.0401 | -1.0361 | 1.228 | 0.3803 |
| C|N8|15m|oneshot | 305 | 212.7 | 0.3803 | 0.141 | 1.336 | 0.1237 | 0.0173 | -1.0491 | 1.228 | 0.3803 |
| B|N3|15m|oneshot | 651 | 440.9 | 0.3671 | 0.1382 | 1.841 | 0.5763 | -0.438 | -1.1548 | 1.223 | 0.3794 |
| B|N3|60m|level | 317 | 278.2 | 0.3659 | 0.1356 | 1.482 | 0.8854 | -0.7498 | -1.0901 | 1.218 | 0.3754 |
| B|N8|5m|oneshot | 769 | 524 | 0.3654 | 0.1274 | 1.994 | 0.5281 | -0.4006 | -1.1585 | 1.204 | 0.3758 |
| B|N5|60m|level | 215 | 180.4 | 0.3628 | 0.1163 | 0.834 | 0.6562 | -0.5399 | -1.0964 | 1.185 | 0.3721 |
| B|N8|60m|oneshot | 78 | 53.5 | 0.3718 | 0.1154 | 0.606 | 0.1909 | -0.0755 | -1.073 | 1.184 | 0.3718 |
| C|N3|60m|oneshot | 202 | 133.1 | 0.3663 | 0.099 | 0.916 | 0.098 | 0.001 | -1.0322 | 1.156 | 0.3663 |
| B|N2|60m|level | 361 | 208.5 | 0.3573 | 0.097 | 0.817 | 0.5776 | -0.4806 | -1.1253 | 1.153 | 0.3657 |
| B|N3|15m|level | 850 | 445.7 | 0.3282 | 0.0871 | 1.252 | 1.6505 | -1.5634 | -1.1731 | 1.137 | 0.3624 |
| B|N2|15m|oneshot | 886 | 465.6 | 0.3499 | 0.0835 | 1.225 | 0.5155 | -0.432 | -1.1721 | 1.131 | 0.3612 |
| B|N8|5m|level | 1158 | 1,158.0 | 0.2953 | 0.0829 | 1.918 | 2.2112 | -2.1283 | -1.2135 | 1.13 | 0.361 |
| B|N5|60m|oneshot | 125 | 85 | 0.36 | 0.08 | 0.494 | 0.2416 | -0.1616 | -1.0965 | 1.125 | 0.36 |
| C|N8|15m|level | 309 | 206.8 | 0.356 | 0.068 | 0.703 | 0.119 | -0.051 | -1.0446 | 1.106 | 0.3528 |
| B|N5|5m|oneshot | 1183 | 924.4 | 0.3364 | 0.0676 | 1.457 | 0.8026 | -0.7349 | -1.2278 | 1.105 | 0.3559 |
| D|N8|15m|level | 456 | 456 | 0.3355 | 0.0658 | 1.03 | 1.1103 | -1.0446 | -1.0608 | 1.102 | 0.3531 |
| C|N3|60m|level | 172 | 127.6 | 0.3547 | 0.064 | 0.613 | 0.0796 | -0.0157 | -1.0364 | 1.099 | 0.3488 |
| B|N5|15m|oneshot | 452 | 245.1 | 0.3473 | 0.0619 | 0.642 | 0.4056 | -0.3437 | -1.1495 | 1.096 | 0.354 |
| A|N5|60m|oneshot | 130 | 89.8 | 0.3538 | 0.0615 | 0.461 | 0.1084 | -0.0468 | -1.0209 | 1.095 | 0.3538 |
| B|N2|15m|level | 998 | 998 | 0.3216 | 0.0581 | 1.204 | 1.1846 | -1.1265 | -1.1789 | 1.09 | 0.3527 |
| C|N5|5m|oneshot | 1075 | 644.2 | 0.3507 | 0.0549 | 0.939 | 0.2865 | -0.2316 | -1.1234 | 1.085 | 0.3516 |
| D|N3|60m|level | 336 | 310 | 0.3423 | 0.0536 | 0.604 | 0.3876 | -0.3341 | -1.0506 | 1.083 | 0.3482 |
| D|N5|15m|oneshot | 356 | 232.6 | 0.3511 | 0.0534 | 0.596 | 0.1552 | -0.1019 | -1.0695 | 1.082 | 0.3511 |
| C|N5|15m|oneshot | 434 | 291.2 | 0.3502 | 0.0507 | 0.594 | 0.1561 | -0.1054 | -1.0654 | 1.078 | 0.3502 |
| B|N5|15m|level | 655 | 350.1 | 0.3206 | 0.0489 | 0.617 | 1.2316 | -1.1827 | -1.1678 | 1.075 | 0.3496 |
| C|N2|15m|oneshot | 794 | 506.1 | 0.3476 | 0.0428 | 0.646 | 0.1941 | -0.1512 | -1.1002 | 1.066 | 0.3476 |
| B|N3|5m|oneshot | 1980 | 1,658.4 | 0.3227 | 0.0409 | 1.187 | 1.0586 | -1.0177 | -1.265 | 1.063 | 0.347 |
| D|N5|15m|level | 552 | 552 | 0.3207 | 0.038 | 0.585 | 0.864 | -0.826 | -1.0676 | 1.058 | 0.3424 |
| A|N5|60m|level | 243 | 154.7 | 0.3374 | 0.037 | 0.296 | 0.3329 | -0.2958 | -1.0504 | 1.057 | 0.3374 |
| D|N2|60m|oneshot | 197 | 121.6 | 0.3452 | 0.0355 | 0.249 | 0.1122 | -0.0767 | -1.0512 | 1.054 | 0.3452 |
| B|N8|60m|level | 169 | 117.2 | 0.3314 | 0.0296 | 0.188 | 0.4702 | -0.4406 | -1.0916 | 1.045 | 0.3432 |
| A|N3|15m|oneshot | 703 | 703 | 0.33 | 0.0284 | 0.512 | 0.6411 | -0.6127 | -1.1212 | 1.043 | 0.3428 |
| B|N5|5m|level | 1580 | 1,339.2 | 0.2835 | 0.0272 | 0.662 | 2.6191 | -2.5919 | -1.2782 | 1.041 | 0.3424 |
| D|N8|5m|oneshot | 649 | 467.7 | 0.3405 | 0.0216 | 0.311 | 0.1984 | -0.1768 | -1.1079 | 1.033 | 0.3405 |
| C|N3|5m|oneshot | 1622 | 814.7 | 0.3403 | 0.021 | 0.404 | 0.2967 | -0.2758 | -1.1504 | 1.032 | 0.3403 |
| A|N3|15m|level | 909 | 909 | 0.3014 | 0.0198 | 0.409 | 1.503 | -1.4832 | -1.1326 | 1.03 | 0.3399 |
| C|N3|15m|oneshot | 612 | 341.5 | 0.3399 | 0.0196 | 0.258 | 0.1798 | -0.1602 | -1.09 | 1.03 | 0.3399 |
| B|N2|5m|level | 2969 | 2,969.0 | 0.2873 | 0.0175 | 0.666 | 2.1895 | -2.1719 | -1.3113 | 1.027 | 0.3392 |
| B|N2|5m|oneshot | 2734 | 2,645.5 | 0.3043 | 0.015 | 0.535 | 1.1402 | -1.1252 | -1.3318 | 1.023 | 0.3383 |
| D|N3|60m|oneshot | 166 | 116.6 | 0.3373 | 0.012 | 0.076 | 0.0966 | -0.0845 | -1.0461 | 1.018 | 0.3373 |
| D|N2|15m|oneshot | 749 | 566.4 | 0.3364 | 0.0093 | 0.173 | 0.1986 | -0.1893 | -1.1097 | 1.014 | 0.3364 |
| D|N5|5m|oneshot | 965 | 687 | 0.3358 | 0.0073 | 0.141 | 0.2323 | -0.225 | -1.121 | 1.011 | 0.3358 |
| A|N5|15m|level | 683 | 683 | 0.2928 | 0.0059 | 0.107 | 1.9215 | -1.9156 | -1.1131 | 1.009 | 0.3338 |
| A|N2|15m|level | 1126 | 789.5 | 0.3028 | 0.0044 | 0.087 | 1.1984 | -1.1939 | -1.1475 | 1.007 | 0.3348 |
| C|N3|5m|level | 1258 | 808.8 | 0.3347 | 0.004 | 0.084 | 0.2343 | -0.2303 | -1.1029 | 1.006 | 0.3347 |
| D|N5|5m|level | 1100 | 1,100.0 | 0.2991 | 0.0036 | 0.083 | 1.2853 | -1.2816 | -1.119 | 1.005 | 0.3345 |
| A|N2|60m|oneshot | 305 | 198.2 | 0.3246 | 0.0033 | 0.034 | 0.2794 | -0.2761 | -1.0851 | 1.005 | 0.3344 |
| A|N8|15m|oneshot | 347 | 191.1 | 0.3256 | 0.0029 | 0.028 | 0.4661 | -0.4632 | -1.0697 | 1.004 | 0.3343 |
| A|N5|5m|level | 1742 | 1,565.0 | 0.2704 | 0.0023 | 0.059 | 2.9397 | -2.9374 | -1.2455 | 1.003 | 0.3341 |
| C|N5|60m|level | 144 | 113 | 0.3333 | 0 | 0 | 0.0751 | -0.0751 | -1.0331 | 1 | 0.3194 |
| C|N8|60m|level | 108 | 78.4 | 0.3333 | 0 | 0 | 0.0732 | -0.0732 | -1.0345 | 1 | 0.3148 |
| A|N5|15m|oneshot | 498 | 366 | 0.3293 | -0 | -0 | 0.3527 | -0.3527 | -1.0999 | 1 | 0.3333 |
| D|N5|60m|oneshot | 129 | 104.7 | 0.3333 | -0 | -0 | 0.0869 | -0.0869 | -1.0371 | 1 | 0.3333 |
| B|N3|5m|level | 2363 | 2,102.6 | 0.2861 | -0.0021 | -0.07 | 2.3604 | -2.3625 | -1.2952 | 0.997 | 0.3326 |
| D|N2|60m|level | 382 | 382 | 0.3272 | -0.0026 | -0.035 | 0.4446 | -0.4472 | -1.0545 | 0.996 | 0.3325 |
| C|N3|15m|level | 542 | 345.9 | 0.3321 | -0.0037 | -0.049 | 0.1556 | -0.1593 | -1.0707 | 0.994 | 0.3321 |
| C|N2|15m|level | 667 | 398.2 | 0.3313 | -0.006 | -0.08 | 0.1671 | -0.1731 | -1.0789 | 0.991 | 0.3313 |
| D|N3|5m|level | 1540 | 1,540.0 | 0.3091 | -0.0065 | -0.178 | 1.1457 | -1.1521 | -1.1203 | 0.99 | 0.3312 |
| C|N5|60m|oneshot | 142 | 105.6 | 0.331 | -0.007 | -0.059 | 0.0906 | -0.0977 | -1.0348 | 0.989 | 0.331 |
| C|N8|60m|oneshot | 106 | 78.4 | 0.3302 | -0.0094 | -0.066 | 0.0706 | -0.08 | -1.0143 | 0.986 | 0.3302 |
| D|N3|15m|oneshot | 548 | 413.1 | 0.3285 | -0.0146 | -0.232 | 0.1775 | -0.1921 | -1.0941 | 0.978 | 0.3285 |
| C|N8|5m|oneshot | 832 | 596.6 | 0.3269 | -0.0156 | -0.266 | 0.2193 | -0.235 | -1.1169 | 0.977 | 0.3281 |
| C|N5|5m|level | 870 | 501.1 | 0.3276 | -0.0172 | -0.282 | 0.199 | -0.2162 | -1.1007 | 0.974 | 0.3276 |
| A|N3|5m|oneshot | 2065 | 1,426.3 | 0.2983 | -0.0194 | -0.519 | 0.9551 | -0.9744 | -1.2577 | 0.971 | 0.3269 |
| A|N8|5m|level | 1329 | 1,085.9 | 0.2664 | -0.0248 | -0.518 | 2.8243 | -2.8491 | -1.2198 | 0.963 | 0.3251 |
| D|N2|5m|level | 2087 | 1,583.6 | 0.298 | -0.0254 | -0.714 | 1.2702 | -1.2956 | -1.1623 | 0.962 | 0.3249 |
| D|N2|15m|level | 786 | 786 | 0.3104 | -0.0267 | -0.508 | 0.7362 | -0.7629 | -1.0911 | 0.96 | 0.3244 |
| A|N5|5m|oneshot | 1301 | 1,044.7 | 0.3113 | -0.0292 | -0.629 | 0.8043 | -0.8335 | -1.1953 | 0.957 | 0.3236 |
| C|N2|5m|oneshot | 2235 | 1,582.0 | 0.3226 | -0.0295 | -0.822 | 0.3934 | -0.4229 | -1.1853 | 0.956 | 0.3235 |
| D|N3|15m|level | 710 | 710 | 0.3056 | -0.0324 | -0.529 | 0.6234 | -0.6558 | -1.0888 | 0.952 | 0.3211 |
| A|N2|5m|oneshot | 2754 | 1,671.4 | 0.289 | -0.0338 | -0.966 | 1.1946 | -1.2283 | -1.3183 | 0.95 | 0.3221 |
| A|N2|15m|oneshot | 1002 | 663.4 | 0.3074 | -0.0359 | -0.61 | 0.5217 | -0.5577 | -1.1553 | 0.947 | 0.3214 |
| C|N8|5m|level | 612 | 328.6 | 0.3219 | -0.0392 | -0.477 | 0.1571 | -0.1963 | -1.0739 | 0.942 | 0.3186 |
| A|N3|5m|level | 2429 | 1,741.7 | 0.2651 | -0.0403 | -1.24 | 2.0196 | -2.0599 | -1.2753 | 0.941 | 0.3199 |
| D|N3|5m|oneshot | 1433 | 875.4 | 0.3189 | -0.0412 | -0.955 | 0.2909 | -0.3321 | -1.1597 | 0.939 | 0.3196 |
| A|N8|5m|oneshot | 910 | 590.8 | 0.3066 | -0.044 | -0.775 | 0.5022 | -0.5461 | -1.1504 | 0.935 | 0.3187 |
| D|N2|5m|oneshot | 1906 | 1,262.8 | 0.3179 | -0.0446 | -1.18 | 0.3488 | -0.3934 | -1.1914 | 0.935 | 0.3185 |
| C|N5|15m|level | 372 | 233.9 | 0.3172 | -0.0484 | -0.526 | 0.133 | -0.1814 | -1.0571 | 0.929 | 0.3145 |
| C|N2|5m|level | 1772 | 924.6 | 0.316 | -0.0502 | -1.054 | 0.3176 | -0.3678 | -1.1499 | 0.927 | 0.3166 |
| D|N5|60m|level | 333 | 333 | 0.3003 | -0.0541 | -0.666 | 4.0599 | -4.114 | -1.0745 | 0.921 | 0.3123 |
| D|N8|5m|level | 853 | 853 | 0.2814 | -0.0574 | -1.268 | 1.2419 | -1.2994 | -1.1075 | 0.916 | 0.313 |
| A|N2|60m|level | 439 | 339.8 | 0.3007 | -0.0638 | -0.871 | 0.6376 | -0.7014 | -1.0854 | 0.907 | 0.3121 |
| A|N2|5m|level | 3183 | 2,672.3 | 0.2623 | -0.0716 | -2.575 | 2.0513 | -2.123 | -1.3274 | 0.896 | 0.3095 |
| A|N8|15m|level | 582 | 582 | 0.2595 | -0.0722 | -1.341 | 1.6287 | -1.7009 | -1.1118 | 0.896 | 0.3093 |
| D|N8|60m|level | 279 | 266.6 | 0.2975 | -0.086 | -1.038 | 4.9148 | -5.0008 | -1.0543 | 0.876 | 0.3011 |
| A|N3|60m|level | 332 | 332 | 0.2922 | -0.0873 | -0.888 | 1.1216 | -1.2089 | -1.0603 | 0.874 | 0.3012 |
| A|N8|60m|oneshot | 81 | 62 | 0.2963 | -0.1111 | -0.704 | 0.0662 | -0.1773 | -1.0266 | 0.842 | 0.284 |
| A|N3|60m|oneshot | 213 | 152.2 | 0.2817 | -0.1408 | -1.195 | 0.192 | -0.3328 | -1.0629 | 0.803 | 0.2864 |
| A|N8|60m|level | 188 | 184 | 0.2606 | -0.1702 | -1.797 | 7.8484 | -8.0186 | -1.0564 | 0.765 | 0.2713 |

### Multi-timeframe grid on VALID

| label | trades | effective_n | win_rate | gross_r | t_gross | cost_r | net_r | median_r | pf_gross | p_hit_2r |
|---|---|---|---|---|---|---|---|---|---|---|
| B|N8|15m->5m|oneshot | 303 | 188.5 | 0.4026 | 0.2178 | 1.682 | 0.3911 | -0.1733 | -1.113 | 1.367 | 0.4059 |
| B|N8|15m->5m|level | 475 | 471.4 | 0.3411 | 0.1747 | 2.361 | 2.5525 | -2.3778 | -1.1535 | 1.287 | 0.3916 |
| D|N8|15m->5m|oneshot | 236 | 154.4 | 0.3814 | 0.1441 | 1.283 | 0.1235 | 0.0206 | -1.0507 | 1.233 | 0.3771 |
| C|N8|15m->5m|oneshot | 305 | 212.7 | 0.3803 | 0.141 | 1.336 | 0.1237 | 0.0173 | -1.0491 | 1.228 | 0.3803 |
| B|N3|15m->5m|oneshot | 651 | 440.9 | 0.3671 | 0.1382 | 1.841 | 0.5763 | -0.438 | -1.1548 | 1.223 | 0.3794 |
| B|N8|5m->15m|oneshot | 751 | 497.2 | 0.3595 | 0.1225 | 1.786 | 0.626 | -0.5035 | -1.1598 | 1.196 | 0.3742 |
| B|N3|15m->5m|level | 850 | 445.7 | 0.3282 | 0.0871 | 1.252 | 1.6505 | -1.5634 | -1.1731 | 1.137 | 0.3624 |
| B|N2|15m->5m|oneshot | 886 | 465.6 | 0.3499 | 0.0835 | 1.225 | 0.5155 | -0.432 | -1.1721 | 1.131 | 0.3612 |
| C|N8|15m->5m|level | 309 | 206.8 | 0.356 | 0.068 | 0.703 | 0.119 | -0.051 | -1.0446 | 1.106 | 0.3528 |
| D|N8|15m->5m|level | 456 | 456 | 0.3355 | 0.0658 | 1.03 | 1.1103 | -1.0446 | -1.0608 | 1.102 | 0.3531 |
| B|N5|15m->5m|oneshot | 452 | 245.1 | 0.3473 | 0.0619 | 0.642 | 0.4056 | -0.3437 | -1.1495 | 1.096 | 0.354 |
| B|N8|5m->15m|level | 923 | 923 | 0.3218 | 0.0596 | 1.209 | 1.6505 | -1.5909 | -1.1756 | 1.092 | 0.3532 |
| B|N2|15m->5m|level | 998 | 998 | 0.3216 | 0.0581 | 1.204 | 1.1846 | -1.1265 | -1.1789 | 1.09 | 0.3527 |
| D|N5|15m->5m|oneshot | 356 | 232.6 | 0.3511 | 0.0534 | 0.596 | 0.1552 | -0.1019 | -1.0695 | 1.082 | 0.3511 |
| C|N5|15m->5m|oneshot | 434 | 291.2 | 0.3502 | 0.0507 | 0.594 | 0.1561 | -0.1054 | -1.0654 | 1.078 | 0.3502 |
| B|N5|15m->5m|level | 655 | 350.1 | 0.3206 | 0.0489 | 0.617 | 1.2316 | -1.1827 | -1.1678 | 1.075 | 0.3496 |
| C|N2|15m->5m|oneshot | 794 | 506.1 | 0.3476 | 0.0428 | 0.646 | 0.1941 | -0.1512 | -1.1002 | 1.066 | 0.3476 |
| C|N8|5m->15m|oneshot | 751 | 511.6 | 0.3462 | 0.0386 | 0.59 | 0.2039 | -0.1653 | -1.1019 | 1.059 | 0.3462 |
| D|N5|15m->5m|level | 552 | 552 | 0.3207 | 0.038 | 0.585 | 0.864 | -0.826 | -1.0676 | 1.058 | 0.3424 |
| B|N5|5m->15m|oneshot | 1114 | 1,114.0 | 0.3303 | 0.0368 | 0.842 | 0.7836 | -0.7468 | -1.2195 | 1.056 | 0.3456 |
| B|N3|5m->15m|level | 1969 | 1,969.0 | 0.3073 | 0.0345 | 1.032 | 1.8402 | -1.8056 | -1.2488 | 1.053 | 0.3448 |
| B|N5|5m->15m|level | 1301 | 1,301.0 | 0.3036 | 0.0331 | 0.758 | 1.6133 | -1.5803 | -1.235 | 1.05 | 0.3444 |
| B|N3|5m->15m|oneshot | 1823 | 1,823.0 | 0.3182 | 0.0302 | 0.877 | 1.2699 | -1.2398 | -1.2575 | 1.046 | 0.3434 |
| A|N3|15m->5m|oneshot | 703 | 703 | 0.33 | 0.0284 | 0.512 | 0.6411 | -0.6127 | -1.1212 | 1.043 | 0.3428 |
| B|N2|5m->15m|oneshot | 2470 | 1,966.0 | 0.3089 | 0.0227 | 0.701 | 1.3993 | -1.3766 | -1.3098 | 1.034 | 0.3409 |
| D|N5|5m->15m|oneshot | 939 | 663 | 0.3397 | 0.0224 | 0.416 | 0.2539 | -0.2316 | -1.1211 | 1.034 | 0.3408 |
| A|N3|15m->5m|level | 909 | 909 | 0.3014 | 0.0198 | 0.409 | 1.503 | -1.4832 | -1.1326 | 1.03 | 0.3399 |
| C|N3|15m->5m|oneshot | 612 | 341.5 | 0.3399 | 0.0196 | 0.258 | 0.1798 | -0.1602 | -1.09 | 1.03 | 0.3399 |
| D|N2|15m->5m|oneshot | 749 | 566.4 | 0.3364 | 0.0093 | 0.173 | 0.1986 | -0.1893 | -1.1097 | 1.014 | 0.3364 |
| B|N2|5m->15m|level | 2509 | 2,509.0 | 0.2981 | 0.008 | 0.285 | 1.4938 | -1.4858 | -1.2779 | 1.012 | 0.336 |
| C|N3|5m->15m|oneshot | 1431 | 1,119.6 | 0.3319 | 0.0063 | 0.145 | 0.3729 | -0.3667 | -1.134 | 1.009 | 0.3347 |
| A|N5|15m->5m|level | 683 | 683 | 0.2928 | 0.0059 | 0.107 | 1.9215 | -1.9156 | -1.1131 | 1.009 | 0.3338 |
| A|N2|15m->5m|level | 1126 | 789.5 | 0.3028 | 0.0044 | 0.087 | 1.1984 | -1.1939 | -1.1475 | 1.007 | 0.3348 |
| D|N8|5m->15m|oneshot | 637 | 411.7 | 0.3344 | 0.0031 | 0.046 | 0.2518 | -0.2487 | -1.1083 | 1.005 | 0.3344 |
| A|N8|15m->5m|oneshot | 347 | 191.1 | 0.3256 | 0.0029 | 0.028 | 0.4661 | -0.4632 | -1.0697 | 1.004 | 0.3343 |
| A|N5|15m->5m|oneshot | 498 | 366 | 0.3293 | -0 | -0 | 0.3527 | -0.3527 | -1.0999 | 1 | 0.3333 |
| C|N3|15m->5m|level | 542 | 345.9 | 0.3321 | -0.0037 | -0.049 | 0.1556 | -0.1593 | -1.0707 | 0.994 | 0.3321 |
| A|N8|5m->15m|level | 1076 | 1,076.0 | 0.2881 | -0.0046 | -0.11 | 1.6946 | -1.6992 | -1.1644 | 0.993 | 0.3318 |
| D|N3|5m->15m|level | 1426 | 1,426.0 | 0.3156 | -0.0049 | -0.137 | 0.8879 | -0.8928 | -1.1226 | 0.993 | 0.3317 |
| C|N2|15m->5m|level | 667 | 398.2 | 0.3313 | -0.006 | -0.08 | 0.1671 | -0.1731 | -1.0789 | 0.991 | 0.3313 |
| C|N5|5m->15m|oneshot | 1005 | 627.8 | 0.3303 | -0.009 | -0.167 | 0.253 | -0.262 | -1.1182 | 0.987 | 0.3303 |
| C|N3|5m->15m|level | 1251 | 1,092.2 | 0.3269 | -0.012 | -0.307 | 0.3479 | -0.3599 | -1.1104 | 0.982 | 0.3293 |
| D|N3|15m->5m|oneshot | 548 | 413.1 | 0.3285 | -0.0146 | -0.232 | 0.1775 | -0.1921 | -1.0941 | 0.978 | 0.3285 |
| D|N2|5m->15m|level | 1871 | 954.9 | 0.3132 | -0.0171 | -0.384 | 0.6903 | -0.7074 | -1.149 | 0.975 | 0.3276 |
| C|N2|5m->15m|oneshot | 1869 | 1,073.5 | 0.3232 | -0.0177 | -0.413 | 0.408 | -0.4256 | -1.1646 | 0.974 | 0.3269 |
| C|N8|5m->15m|level | 579 | 352.2 | 0.3264 | -0.0207 | -0.251 | 0.1651 | -0.1859 | -1.0769 | 0.969 | 0.3264 |
| C|N5|5m->15m|level | 829 | 527 | 0.3257 | -0.0229 | -0.392 | 0.2248 | -0.2477 | -1.104 | 0.966 | 0.3257 |
| D|N5|5m->15m|level | 940 | 940 | 0.3096 | -0.0234 | -0.557 | 0.8885 | -0.9119 | -1.1068 | 0.965 | 0.3255 |
| D|N2|15m->5m|level | 786 | 786 | 0.3104 | -0.0267 | -0.508 | 0.7362 | -0.7629 | -1.0911 | 0.96 | 0.3244 |
| D|N3|15m->5m|level | 710 | 710 | 0.3056 | -0.0324 | -0.529 | 0.6234 | -0.6558 | -1.0888 | 0.952 | 0.3211 |
| A|N3|5m->15m|oneshot | 1870 | 1,313.2 | 0.2979 | -0.0326 | -0.821 | 1.1521 | -1.1847 | -1.2417 | 0.952 | 0.3225 |
| A|N8|5m->15m|oneshot | 881 | 687.1 | 0.3042 | -0.0329 | -0.604 | 0.7546 | -0.7875 | -1.1464 | 0.951 | 0.3224 |
| A|N2|5m->15m|oneshot | 2597 | 2,451.0 | 0.2907 | -0.0343 | -1.162 | 1.2654 | -1.2997 | -1.2981 | 0.949 | 0.3219 |
| A|N2|15m->5m|oneshot | 1002 | 663.4 | 0.3074 | -0.0359 | -0.61 | 0.5217 | -0.5577 | -1.1553 | 0.947 | 0.3214 |
| C|N2|5m->15m|level | 1726 | 1,162.6 | 0.3169 | -0.0423 | -0.984 | 0.377 | -0.4193 | -1.1491 | 0.938 | 0.3187 |
| A|N5|5m->15m|oneshot | 1259 | 1,122.6 | 0.3034 | -0.0445 | -1.036 | 0.8707 | -0.9152 | -1.1834 | 0.935 | 0.3185 |
| A|N2|5m->15m|level | 2807 | 2,654.2 | 0.274 | -0.0477 | -1.729 | 1.7487 | -1.7964 | -1.285 | 0.93 | 0.3171 |
| C|N5|15m->5m|level | 372 | 233.9 | 0.3172 | -0.0484 | -0.526 | 0.133 | -0.1814 | -1.0571 | 0.929 | 0.3145 |
| D|N3|5m->15m|oneshot | 1400 | 915.7 | 0.3107 | -0.0571 | -1.383 | 0.3139 | -0.371 | -1.1599 | 0.917 | 0.3143 |
| A|N5|5m->15m|level | 1415 | 1,179.5 | 0.27 | -0.0629 | -1.519 | 1.3693 | -1.4322 | -1.2004 | 0.909 | 0.3124 |
| A|N3|5m->15m|level | 2125 | 1,890.9 | 0.272 | -0.0682 | -2.202 | 1.8011 | -1.8693 | -1.2443 | 0.901 | 0.3106 |
| A|N8|15m->5m|level | 582 | 582 | 0.2595 | -0.0722 | -1.341 | 1.6287 | -1.7009 | -1.1118 | 0.896 | 0.3093 |
| D|N2|5m->15m|oneshot | 1749 | 1,341.8 | 0.3053 | -0.0755 | -2.031 | 0.3654 | -0.4409 | -1.1844 | 0.891 | 0.3082 |
| D|N8|5m->15m|level | 796 | 796 | 0.294 | -0.0766 | -1.575 | 0.7689 | -0.8456 | -1.0921 | 0.889 | 0.3078 |

## Grid-wide TRAIN → VALID stability

- primary candidates compared: **96**
- TRAIN gross > 0: **86**; VALID gross > 0: **56**
- sign of gross preserved TRAIN→VALID: **58/96** (60%)
- cross-split correlation of gross R across candidates: **+0.396**
- median gross degradation (VALID − TRAIN): **-0.0258 R**
- candidates with net R > 0 on VALID: **6/96**

## §15 Structure quality on VALID

| label | trades | win_rate | gross_r | net_r | p_hit_1r | p_hit_2r |
|---|---|---|---|---|---|---|
| C|N8|60m|oneshot|displacement=<0.5 ATR | 21 | 0.2381 | -0.2857 | -0.3411 | 0.4286 | 0.2381 |
| C|N8|60m|oneshot|displacement=1-2 ATR | 27 | 0.3333 | 0 | -0.0782 | 0.4444 | 0.3333 |
| C|N8|60m|oneshot|displacement=>2 ATR | 45 | 0.4 | 0.2 | 0.1267 | 0.5556 | 0.4 |
| C|N8|60m|oneshot|break=<0.5 ATR | 53 | 0.3019 | -0.0943 | -0.1721 | 0.4528 | 0.3019 |
| C|N8|60m|oneshot|break=0.5-1 ATR | 31 | 0.2903 | -0.129 | -0.1907 | 0.4839 | 0.2903 |
| A|N8|15m|oneshot|displacement=<0.5 ATR | 60 | 0.1833 | -0.45 | -1.1668 | 0.3 | 0.1833 |
| A|N8|15m|oneshot|displacement=0.5-1 ATR | 55 | 0.2727 | -0.1273 | -0.4369 | 0.4727 | 0.2909 |
| A|N8|15m|oneshot|displacement=1-2 ATR | 86 | 0.3256 | 0.0116 | -0.5252 | 0.5349 | 0.3372 |
| A|N8|15m|oneshot|displacement=>2 ATR | 146 | 0.4041 | 0.2329 | -0.1474 | 0.5548 | 0.411 |
| A|N8|15m|oneshot|break=<0.5 ATR | 329 | 0.3222 | -0.0061 | -0.493 | 0.4954 | 0.3313 |
| B|N8|60m|oneshot|displacement=1-2 ATR | 22 | 0.4545 | 0.3636 | 0.1663 | 0.5 | 0.4545 |
| B|N8|60m|oneshot|displacement=>2 ATR | 34 | 0.3529 | 0.0588 | -0.1082 | 0.5882 | 0.3529 |
| B|N8|60m|oneshot|break=<0.5 ATR | 77 | 0.3766 | 0.1299 | -0.06 | 0.5065 | 0.3766 |
| D|N8|15m|oneshot|displacement=<0.5 ATR | 67 | 0.3433 | 0.0299 | -0.0912 | 0.4925 | 0.3284 |
| D|N8|15m|oneshot|displacement=0.5-1 ATR | 55 | 0.4182 | 0.2545 | 0.1523 | 0.5636 | 0.4182 |
| D|N8|15m|oneshot|displacement=1-2 ATR | 40 | 0.425 | 0.275 | 0.1524 | 0.525 | 0.425 |
| D|N8|15m|oneshot|displacement=>2 ATR | 74 | 0.3649 | 0.0946 | -0.0475 | 0.5676 | 0.3649 |
| D|N8|15m|oneshot|break=<0.5 ATR | 95 | 0.4316 | 0.2947 | 0.1479 | 0.5895 | 0.4211 |
| D|N8|15m|oneshot|break=0.5-1 ATR | 51 | 0.2941 | -0.1176 | -0.2339 | 0.4706 | 0.2941 |
| D|N8|15m|oneshot|break=1-2 ATR | 39 | 0.2821 | -0.1538 | -0.2699 | 0.4872 | 0.2821 |
| D|N8|15m|oneshot|break=>2 ATR | 51 | 0.451 | 0.3529 | 0.26 | 0.549 | 0.451 |
| A|N5|15m|oneshot|displacement=<0.5 ATR | 96 | 0.2292 | -0.3125 | -0.6923 | 0.4896 | 0.2292 |
| A|N5|15m|oneshot|displacement=0.5-1 ATR | 83 | 0.3494 | 0.1205 | -0.3139 | 0.6145 | 0.3735 |
| A|N5|15m|oneshot|displacement=1-2 ATR | 127 | 0.3386 | 0.0157 | -0.4383 | 0.5433 | 0.3386 |
| A|N5|15m|oneshot|displacement=>2 ATR | 192 | 0.3646 | 0.0938 | -0.1431 | 0.526 | 0.3646 |
| A|N5|15m|oneshot|break=<0.5 ATR | 484 | 0.3264 | -0.0083 | -0.3683 | 0.5351 | 0.3306 |
| B|N5|15m|oneshot|displacement=<0.5 ATR | 72 | 0.3472 | 0.0833 | -0.3862 | 0.625 | 0.3611 |
| B|N5|15m|oneshot|displacement=0.5-1 ATR | 84 | 0.2857 | -0.1429 | -0.6057 | 0.4524 | 0.2857 |
| B|N5|15m|oneshot|displacement=1-2 ATR | 114 | 0.386 | 0.1842 | -0.3315 | 0.5263 | 0.3947 |
| B|N5|15m|oneshot|displacement=>2 ATR | 182 | 0.3516 | 0.0714 | -0.2135 | 0.522 | 0.3571 |
| B|N5|15m|oneshot|break=<0.5 ATR | 435 | 0.3517 | 0.0759 | -0.3388 | 0.531 | 0.3586 |
| C|N5|15m|oneshot|displacement=<0.5 ATR | 112 | 0.3393 | 0.0179 | -0.1594 | 0.5089 | 0.3393 |
| C|N5|15m|oneshot|displacement=0.5-1 ATR | 80 | 0.375 | 0.125 | -0.03 | 0.4625 | 0.375 |
| C|N5|15m|oneshot|displacement=1-2 ATR | 97 | 0.3608 | 0.0825 | -0.0891 | 0.5155 | 0.3608 |
| C|N5|15m|oneshot|displacement=>2 ATR | 145 | 0.3379 | 0.0138 | -0.1162 | 0.4966 | 0.3379 |
| C|N5|15m|oneshot|break=<0.5 ATR | 209 | 0.3445 | 0.0335 | -0.1356 | 0.4641 | 0.3445 |
| C|N5|15m|oneshot|break=0.5-1 ATR | 111 | 0.3964 | 0.1892 | 0.0372 | 0.5495 | 0.3964 |
| C|N5|15m|oneshot|break=1-2 ATR | 64 | 0.3125 | -0.0625 | -0.2333 | 0.5312 | 0.3125 |
| C|N5|15m|oneshot|break=>2 ATR | 50 | 0.32 | -0.04 | -0.132 | 0.48 | 0.32 |
| D|N5|15m|oneshot|displacement=<0.5 ATR | 110 | 0.2909 | -0.1273 | -0.3057 | 0.4636 | 0.2909 |
| D|N5|15m|oneshot|displacement=0.5-1 ATR | 72 | 0.3889 | 0.1667 | 0.0098 | 0.5417 | 0.3889 |
| D|N5|15m|oneshot|displacement=1-2 ATR | 70 | 0.3714 | 0.1143 | -0.029 | 0.4714 | 0.3714 |
| D|N5|15m|oneshot|displacement=>2 ATR | 104 | 0.375 | 0.125 | -0.0126 | 0.5288 | 0.375 |
| D|N5|15m|oneshot|break=<0.5 ATR | 149 | 0.3423 | 0.0268 | -0.1387 | 0.4966 | 0.3423 |
| D|N5|15m|oneshot|break=0.5-1 ATR | 86 | 0.3372 | 0.0116 | -0.165 | 0.4651 | 0.3372 |
| D|N5|15m|oneshot|break=1-2 ATR | 62 | 0.4032 | 0.2097 | 0.0618 | 0.5645 | 0.4032 |
| D|N5|15m|oneshot|break=>2 ATR | 59 | 0.339 | 0.0169 | -0.0887 | 0.4915 | 0.339 |

---

# H-Structure-1 FINAL VERDICT

**Family:** Market structure HH / HL / LH / LL — confirmed swing transitions,
break of structure, and structure flip.

**Look-ahead:** **PASS** — see `lookahead_audit.txt`. Structure state survives a
truncation test at 48 cut points across 2 symbols × 3 timeframes × 2 swing
strengths; the full pipeline reproduces identical entries from truncated data;
`entry_time ≥ swing confirmation instant` on all 36,732 trades checked, zero
violations.

## TRAIN

96 pre-declared primary candidates (4 families × N∈{2,3,5,8} × {5m,15m,1h} ×
{one-shot, level}) plus 64 multi-timeframe candidates. 86/96 primary candidates
showed positive gross R. Best was `D|N8|60m|oneshot` at gross **+0.420 R**,
t=3.56, but on only 188 trades — below the pre-declared 200-trade eligibility
floor, so the freeze rule excluded it. The frozen PRIMARY slot went to
`C|N8|60m|oneshot`: gross **+0.191 R**, t=2.22, n=257.

Almost every candidate was **net negative on TRAIN** despite positive gross,
because the structural stop is frequently far tighter than the round trip: mean
cost ran 0.05 R at 1h down-weighted stops to **2.7 R** for 5m level triggers.

## VALID

| | TRAIN | VALID |
|---|---|---|
| primary candidates with gross > 0 | 86/96 | 56/96 |
| candidates with net > 0 | 9/96 | 6/96 |
| sign of gross preserved TRAIN→VALID | — | 58/96 (60%) |
| cross-split correlation of gross R | — | **+0.396** |
| median gross degradation | — | **−0.026 R** |

Frozen candidates:

| slot | candidate | gross TRAIN → VALID | t TRAIN → VALID | net VALID |
|---|---|---|---|---|
| PRIMARY | `C\|N8\|60m\|oneshot` | +0.191 → **−0.009** | 2.22 → −0.07 | −0.080 |
| BEST_A | `A\|N8\|15m\|oneshot` | +0.071 → +0.003 | 1.35 → 0.03 | −0.463 |
| BEST_B | `B\|N8\|60m\|oneshot` | +0.166 → +0.115 | 1.63 → 0.61 | −0.076 |
| BEST_D | `D\|N8\|15m\|oneshot` | +0.124 → +0.144 | 2.40 → 1.28 | +0.021 |
| REF_A | `A\|N5\|15m\|oneshot` | +0.029 → −0.000 | 0.65 → 0.00 | −0.353 |
| REF_B | `B\|N5\|15m\|oneshot` | +0.066 → +0.062 | 1.65 → 0.64 | −0.344 |
| REF_C | `C\|N5\|15m\|oneshot` | +0.044 → +0.051 | 1.01 → 0.59 | −0.105 |
| REF_D | `D\|N5\|15m\|oneshot` | +0.024 → +0.053 | 0.50 → 0.60 | −0.102 |

**The null baselines are the decisive result.** On VALID, at the declared
reference setting, signal gross vs the random-direction null (same entry times,
coin-flip direction, 100 sims):

| family | signal gross | null gross | null sd | z |
|---|---|---|---|---|
| A | −0.000 | +0.057 | 0.056 | −1.02 |
| B | +0.062 | +0.084 | 0.047 | −0.47 |
| C | +0.051 | +0.054 | 0.052 | −0.07 |
| D | +0.053 | +0.019 | 0.063 | +0.54 |

Three of four families **underperform a coin flip placed at their own entry
times**. Unconditional entry does as well: buying every 15m bar on VALID earns
gross +0.055 R and shorting every 15m bar earns +0.056 R — the same magnitude
the "signals" produce.

## Best candidate

`D|N8|15m|oneshot` (structure flip, N=8, 15m, one-shot). It is the only frozen
candidate positive on gross **and** net across both splits: gross +0.124 →
+0.144, net +0.020 → +0.021, t 2.40 → 1.28, 766 → 236 trades. But its gross
edge is +0.144 R against a cost of +0.124 R, its VALID t is 1.28, and its
per-symbol VALID gross is +0.200 / +0.023 / +0.048 / +0.266 (BTC/ETH/SOL/XRP)
with only ~60 trades each. That is not an edge; it is a candidate that has not
yet been falsified.

## Gross edge: **NO**

Across the whole VALID grid, P(+2R before stop) has median **0.336** against a
break-even of **1/3**. Because both exits are fixed multiples of R, gross
expectancy is mechanically `3·P(2R) − 1`, so the entire family reduces to that
one number — and it sits on the break-even line. 58% of candidates are above it,
which is what a coin flip produces.

## Cost survivable: **NO**

6 of 96 candidates are net positive on VALID. Cost/R is driven by the structural
stop distance, and market structure gives no control over it: the last confirmed
swing low can be 0.008% away (fee alone = 15 R) or 5% away. Families A and B
suffer worst because they can fire with price sitting on the swing; C and D have
a natural floor because price must first travel past the broken level.

## Cross-symbol robustness: **NO**

Every frozen candidate flips sign across symbols. PRIMARY on VALID: BTC −0.273,
ETH −0.077, SOL +0.143, XRP +0.269. The aggregate is an average of
contradictions, not a shared effect. The supplementary symbol BEATUSD (VALID
only, no TRAIN data, so it could not influence the freeze) is negative on 7 of
8 frozen candidates.

## Confirmation-delay problem: **YES**

The settings that look best on TRAIN are exactly the slowest ones. N=8 on 1h
carries an **8-hour** confirmation lag by construction, and that is before the
break has to occur. Faster settings have delays of 10–75 minutes but are
uniformly worse, and their tighter stops make cost/R catastrophic. The family
faces a hard trade-off: enough confirmation to be meaningful costs enough delay
to erase the move, and cutting the delay cuts the stop distance that pays for
the trade.

## Classification: **DEAD**

Criteria applied strictly. The frozen PRIMARY is negative on VALID gross. Three
of four families underperform their own random-direction null out of sample. An
unconditional always-on baseline earns as much gross R as the signals. P(2R)
sits on break-even. The one candidate still standing has a VALID t of 1.28 out
of 96 pre-declared tries, which is what the maximum of 96 correlated noise draws
looks like. Nothing here justifies "PROMISING".

## Most important finding

**Market structure transitions do not carry directional information at this
horizon — but the cost geometry is what kills the family first.** Two things
came out of the design that were not obvious going in:

1. Because the exit is a fixed 2R target against a structural stop, gross
   expectancy collapses to `3·P(2R) − 1`. Every candidate in the grid, including
   the null baselines, produced P(2R) between 0.27 and 0.41 with a median of
   0.336. The signal moves that number by less than a percentage point.
2. The structural stop's distance is uncontrolled, spanning three orders of
   magnitude (0.008% to 5%). Mean cost/R is dominated by the tiny-stop tail:
   `A|N5|15m` has a median cost of 0.13 R but a mean of 0.35 R, and the 5m level
   triggers reach 2.7 R. Any future structure work must constrain R before it
   evaluates direction.

Two secondary results worth recording:

- **The 15m→5m multi-timeframe combination is degenerate.** A 15m bar's close is
  always a 5m boundary, so "15m structure, 5m execution" is arithmetically
  identical to plain 15m execution — verified, all 32 candidate pairs match to
  the last trade. Only 5m→15m adds real delay, and it is worse.
- **Displacement quality does not reproduce.** On VALID, family A's P(2R) rises
  monotonically with ATR-normalised displacement (0.183 → 0.291 → 0.337 →
  0.411), which looks like the answer to §15. On TRAIN the same buckets give
  0.331 → 0.296 → 0.425 → 0.346 — no ordering at all. It is noise, and it is
  exactly the kind of result that would have been reported as a discovery had
  VALID been looked at first.

## Recommended next experiment

**Do not continue this family as a directional signal.** If structure is
revisited, the next experiment should not be about entries at all:

> **H-Structure-2 — is the confirmed swing point a better STOP than the
> Supertrend leg extreme?** Hold H-WPR-1's entry signal completely fixed and
> vary only the stop reference: Supertrend leg extreme (control) vs last
> confirmed swing low/high vs the wider of the two, with a pre-declared minimum
> R floor expressed in ATR so cost/R is bounded by construction.

That question is worth asking because the one thing this experiment established
is that structure has no directional content but does define levels that price
respects mechanically — and the H-WPR-1 record shows its cost/R, not its win
rate, is the binding constraint. It also isolates a single variable against an
existing control, which is the only way the result will be interpretable.

**Explicitly NOT recommended:** re-running this family with a different N, a
different timeframe, a trailing stop, a 3R target, or an added indicator. The
grid already covers 160 pre-declared variants and the family sits on its null.
