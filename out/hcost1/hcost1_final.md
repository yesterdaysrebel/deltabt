# H-COST-1 FINAL REPORT

Pre-registration sha256 `71d69f6f0ea9b350…`, hash-bound to the manifest and verified. TRAIN and VALID both run. TEST never computed.

## The three things this experiment separates

### A — What initially appeared to be signal edge

H-EMA-3 reported a paired excess of **+0.0324 R at t = +15.56** on TRAIN and **+0.0181 R at t = +5.40** on VALID, positive in all four symbols, both VALID halves, and blind BEATUSD. It replicated. Every number is reproducible and unchanged.

### B — What the adversarial control shows it actually represents

| rule | TRAIN excess | t |
|---|---|---|
| EMA signal (frozen, executable) | +0.0324 R | +15.56 |
| **wider-structural-stop rule — zero EMA content** | **+0.0319 R** | **+13.62** |
| narrower-stop rule | −0.0319 R | −13.62 |

The EMA signal agrees with the wider-stop side on **88.2%** of bars. Median per-bar stop asymmetry is **3.20×**.

| | P(hit +0.5R before −1R) |
|---|---|
| wider-stop side | 66.61% |
| martingale null | 66.67% |
| narrower-stop side | 62.35% |

The wide side lands **on** the null. The narrow side **underperforms** it. A stop 3.2× tighter is taken out by intrabar noise before the target, and the frozen same-bar convention resolves ties to the STOP while stops trigger on MARK, whose range exceeds LTP. The statistic measured which leg escaped a mechanical penalty, not which direction price went.

Both the author and an independent adversarial reviewer justified the estimator on `P(hit +kR before −1R) = 1/(1+k)` for any stop width. That holds for the null **in expectation** and fails for the **realised** statistic, because the same-bar and mark-price conventions are not symmetric in stop width.

### C — Whether a genuine residual survives symmetric geometry and real costs

No. Under a stop symmetric by construction, at the same bars and the same target distance:

| cell | TRAIN excess | t | VALID excess | t |
|---|---|---|---|---|
| 5m, 0.50% stop | +0.0049 | +1.84 | -0.0033 | -0.76 |
| 5m, 0.75% stop | +0.0044 | +1.67 | +0.0014 | +0.31 |
| 5m, 1.00% stop | +0.0043 | +1.67 | +0.0003 | +0.06 |
| 5m, 3.00% stop | +0.0041 | +1.94 | +0.0011 | +0.30 |

The residual is +0.004–0.005 R with t < 2 on TRAIN and does not replicate on VALID.

## Feasibility map

**24 primary cells (stop ≤ 5%): GREEN 0, YELLOW 0, RED 24 — on TRAIN and on VALID.** TRAIN had positive excess in 22 of 24 cells; VALID in 7 of 24, none with t > 2. Best VALID excess is +0.0070 R, needing a **4.5×** larger signal to break even.

| tf | stop | TRAIN excess | VALID excess | cost/R | VALID net | × needed | gate |
|---|---|---|---|---|---|---|---|
| 5m | 0.25% | -0.0008 | -0.0139 | 0.6320 | -0.6476 | — | RED |
| 5m | 0.50% | +0.0049 | -0.0033 | 0.3160 | -0.3113 | — | RED |
| 5m | 0.75% | +0.0044 | +0.0014 | 0.2107 | -0.2033 | 155.0 | RED |
| 5m | 1.00% | +0.0043 | +0.0003 | 0.1580 | -0.1592 | 604.3 | RED |
| 5m | 1.50% | +0.0023 | -0.0005 | 0.1053 | -0.1235 | — | RED |
| 5m | 2.00% | +0.0028 | +0.0047 | 0.0790 | -0.0947 | 16.7 | RED |
| 5m | 3.00% | +0.0041 | +0.0011 | 0.0527 | -0.0673 | 49.9 | RED |
| 5m | 5.00% | +0.0022 | +0.0045 | 0.0316 | -0.0690 | 7.0 | RED |
| 15m | 0.25% | -0.0034 | -0.0146 | 0.6320 | -0.6501 | — | RED |
| 15m | 0.50% | +0.0065 | -0.0082 | 0.3160 | -0.3194 | — | RED |
| 15m | 0.75% | +0.0100 | -0.0024 | 0.2107 | -0.2114 | — | RED |
| 15m | 1.00% | +0.0111 | -0.0028 | 0.1580 | -0.1685 | — | RED |
| 15m | 1.50% | +0.0109 | +0.0004 | 0.1053 | -0.1328 | 258.5 | RED |
| 15m | 2.00% | +0.0070 | -0.0034 | 0.0790 | -0.1127 | — | RED |
| 15m | 3.00% | +0.0101 | -0.0001 | 0.0527 | -0.0677 | — | RED |
| 15m | 5.00% | +0.0063 | +0.0070 | 0.0316 | -0.0708 | 4.5 | RED |
| 60m | 0.25% | +0.0018 | -0.0248 | 0.6320 | -0.6441 | — | RED |
| 60m | 0.50% | +0.0087 | -0.0218 | 0.3160 | -0.3341 | — | RED |
| 60m | 0.75% | +0.0065 | -0.0107 | 0.2107 | -0.2089 | — | RED |
| 60m | 1.00% | +0.0075 | -0.0017 | 0.1580 | -0.1596 | — | RED |
| 60m | 1.50% | +0.0063 | -0.0097 | 0.1053 | -0.1461 | — | RED |
| 60m | 2.00% | +0.0087 | -0.0108 | 0.0790 | -0.1212 | — | RED |
| 60m | 3.00% | +0.0190 | -0.0241 | 0.0527 | -0.0894 | — | RED |
| 60m | 5.00% | +0.0055 | -0.0182 | 0.0316 | -0.1129 | — | RED |

### Out-of-model diagnostic (> 5%)

Break-even is **not reached even outside the frozen risk constraint**. Best VALID excess in the diagnostic band is +0.0068 R against a cost/R of 0.0158 — still 2.3× short at a 10% stop. **Break-even appears beyond the frozen 5% risk constraint, and beyond the diagnostic band too.**

My pre-registered prediction put break-even at ~8.73% stop width. That was **confirmed in direction and wrong in magnitude**: it assumed the +0.0181 R edge persisted. It does not persist, so widening the stop cuts cost/R with no edge left to meet it.

## The four questions

**Q1 — Evidence of an EMA directional edge after removing stop-geometry asymmetry?** No. A zero-EMA rule reproduces the original statistic to within 0.0005 R; under symmetric stops the excess falls 85% and fails to replicate.

**Q2 — Any residual edge worth researching?** No. +0.004–0.005 R at t < 2 on TRAIN, absent on VALID, against cost floors of 0.03–0.63 R.

**Q3 — Can any tested geometry survive realistic costs?** No. 24/24 primary cells RED on both splits; the out-of-model band does not reach break-even either.

**Q4 — Does the evidence justify continuing indicator discovery?** **No — and that is the finding.** The estimator manufactured a t = +15.5 result from zero directional information, and neither the author nor an independent adversarial reviewer caught it. It surfaced only because H-COST-1 replaced the structural stop with a symmetric one for unrelated reasons.

## Recorded, and explicitly not pursued

The wider-stop rule sits **on** the martingale null (66.61% vs 66.67%). It is not an edge — it is merely un-penalised. It is a diagnostic and control discovery and is **not** to be developed into a strategy sweep.

## Recommended next

Not another indicator family. Fourteen registry records now show the same pattern, and the last three show something worse: the framework has been detecting stop and execution geometry rather than directional alpha, with enough statistical force to look conclusive.

> **H-NULL-1 — a universal adversarial null framework that every future result must pass before it is believed.** At minimum: a control symmetric in stop geometry by construction; a zero-signal rule built from the same execution primitives (the wider-stop rule is the first member); a reported minimum detectable effect beside every null claim; and a synthetic planted-edge test proving the estimator can find what it claims to rule out.

This comes before WPR-2, RSI, MACD or any other family. Three of the last four headline claims in this program were overturned by a deeper check — two by reviewers, one by an experiment aimed at something else. That rate is the problem to fix.

