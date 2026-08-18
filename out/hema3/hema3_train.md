# H-EMA-3 — TRAIN report

Pre-registration sha256 `00ffd0bcf97834dc0ae0bc7eaf6e5b97…`, computed after the document was final and verified against it. Supersedes H-EMA-2.

TRAIN 2025-01-01 → 2025-12-20. VALID not computed. TEST locked.

## Primary — pooled, deduplicated, cluster-robust on symbol-day

| k | n | clusters | excess_gross_R | se_gross_R | t | ci_low_R | ci_high_R | cost_floor_R | multiple_of_cost | mde_gross_R |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.5 | 117511 | 1415 | 0.0315 | 0.002 | 15.5347 | 0.0275 | 0.0355 | 0.2565 | 0.1227 | 0.0057 |
| 1 | 117426 | 1414 | 0.0194 | 0.0032 | 6.1126 | 0.0132 | 0.0257 | 0.2565 | 0.0758 | 0.0089 |
| 2 | 117299 | 1414 | 0.0036 | 0.0048 | 0.753 | -0.0058 | 0.013 | 0.2565 | 0.0141 | 0.0134 |
| 4 | 117144 | 1413 | -0.0144 | 0.0071 | -2.0192 | -0.0283 | -0.0004 | 0.2565 | -0.0561 | 0.0199 |

**There is directional information, and it is far too small to trade.**

- At k=0.5 the excess is **+0.0315 R at t = +15.5** — overwhelming statistical evidence.
- At k=1 it is **+0.0194 R at t = +6.1**, independently reproducing the reviewer's +0.0193 R.
- At k=2 — H-EMA-2's fixed exit — it is **+0.0036 R at t = +0.75**, indistinguishable from zero.
- At k=4 it turns **negative**, −0.0144 R at t = −2.0.

The information decays monotonically in the barrier multiple and reverses. H-EMA-2's 2R target was measuring at precisely the horizon where the signal's information has already decayed to nothing, which is why that design found 'no edge' and could not have found anything else.

**The economic gate is failed by an order of magnitude.** The round trip is 15.8 bps of price; against the pooled median stop width that is a cost floor of **0.257 R**. The largest edge found anywhere in the sweep is 12% of it:

```
k=0.5   +0.0315 R   vs cost floor 0.257 R   ->  12% of cost
k=1.0   +0.0194 R   vs cost floor 0.257 R   ->   8% of cost
```

## Robustness of the k≤1 signal


### k = 0.5

| label | n | excess_gross_R | se_gross_R | t | ci_low_R | ci_high_R |
|---|---|---|---|---|---|---|
| tf=5m | 82821 | 0.0383 | 0.0023 | 16.4133 | 0.0337 | 0.0429 |
| tf=15m | 27722 | 0.0179 | 0.0042 | 4.3166 | 0.0098 | 0.0261 |
| tf=60m | 6968 | 0.0042 | 0.0085 | 0.4927 | -0.0125 | 0.0209 |
| symbol=BTCUSD | 29456 | 0.0343 | 0.004 | 8.57 | 0.0264 | 0.0421 |
| symbol=ETHUSD | 30015 | 0.0271 | 0.004 | 6.8065 | 0.0193 | 0.0349 |
| symbol=SOLUSD | 28529 | 0.0372 | 0.0041 | 8.9643 | 0.029 | 0.0453 |
| symbol=XRPUSD | 29511 | 0.0277 | 0.0041 | 6.8204 | 0.0197 | 0.0356 |
| TRAIN-H1 | 58373 | 0.0356 | 0.0029 | 12.1338 | 0.0299 | 0.0414 |
| TRAIN-H2 | 59138 | 0.0274 | 0.0028 | 9.8157 | 0.0219 | 0.0329 |
| mech=M1 | 79942 | 0.0441 | 0.0022 | 19.9366 | 0.0398 | 0.0484 |
| mech=M2 | 59915 | 0.0462 | 0.0026 | 17.9687 | 0.0412 | 0.0513 |
| mech=M3 | 5780 | 0.0767 | 0.0088 | 8.7074 | 0.0594 | 0.0939 |
| mech=M4 | 43569 | 0.0079 | 0.0032 | 2.4357 | 0.0015 | 0.0142 |
| mech=M5 | 37685 | 0.0416 | 0.0039 | 10.5594 | 0.0339 | 0.0493 |
| POOLED-capped-5pct | 116368 | 0.0313 | 0.002 | 15.414 | 0.0274 | 0.0353 |

### k = 1.0

| label | n | excess_gross_R | se_gross_R | t | ci_low_R | ci_high_R |
|---|---|---|---|---|---|---|
| tf=5m | 82787 | 0.0215 | 0.0035 | 6.2058 | 0.0147 | 0.0283 |
| tf=15m | 27689 | 0.0134 | 0.0063 | 2.1349 | 0.0011 | 0.0258 |
| tf=60m | 6950 | 0.0186 | 0.0129 | 1.4443 | -0.0066 | 0.0437 |
| symbol=BTCUSD | 29434 | 0.017 | 0.0062 | 2.7599 | 0.0049 | 0.0291 |
| symbol=ETHUSD | 29994 | 0.0194 | 0.0065 | 2.9988 | 0.0067 | 0.0321 |
| symbol=SOLUSD | 28501 | 0.0266 | 0.0064 | 4.1649 | 0.0141 | 0.0391 |
| symbol=XRPUSD | 29497 | 0.015 | 0.0064 | 2.3462 | 0.0025 | 0.0276 |
| TRAIN-H1 | 58373 | 0.0275 | 0.0046 | 5.9842 | 0.0185 | 0.0365 |
| TRAIN-H2 | 59053 | 0.0115 | 0.0044 | 2.6155 | 0.0029 | 0.0201 |
| mech=M1 | 79886 | 0.0254 | 0.0033 | 7.6589 | 0.0189 | 0.0319 |
| mech=M2 | 59869 | 0.0261 | 0.0038 | 6.8142 | 0.0186 | 0.0336 |
| mech=M3 | 5775 | 0.0641 | 0.0135 | 4.7629 | 0.0377 | 0.0904 |
| mech=M4 | 43535 | 0.0106 | 0.0048 | 2.1925 | 0.0011 | 0.0201 |
| mech=M5 | 37665 | 0.0246 | 0.0064 | 3.8532 | 0.0121 | 0.0371 |
| POOLED-capped-5pct | 116295 | 0.0192 | 0.0032 | 6.0036 | 0.0129 | 0.0255 |

Sign is consistent across all four symbols, both halves of TRAIN, all five mechanisms and the 5%-capped subset. It is strongest at 5m and weakest at 1h — the opposite of the timeframe effect on cost/R, so the two work against each other.

## Power

The pooled minimum detectable effect at 80% power is in the table above (`mde_gross_R`). Unlike H-EMA-2, whose per-arm MDE of 0.14–0.16 R was 7–24× the effects it claimed to rule out, this design's MDE is smaller than the effect it measures at every k ≤ 1. A null here would be informative; as it happens, the result is not null.

## What this settles

| effect | verdict |
|---|---|
| **A. Signal edge** | REAL. +0.0315 R at k=0.5, t=15.5, consistent everywhere. |
| **B. Timeframe improves signal** | NO. The edge is largest at 5m (+0.0383) and vanishes at 1h (+0.0042, t=0.5). |
| **C. Stop geometry** | The only thing higher timeframes improve — cost/R, not prediction. |
| **D. Selection** | Not applicable. One pooled number per k, whole grid reported. |
