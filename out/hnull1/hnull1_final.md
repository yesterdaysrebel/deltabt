# H-NULL-1 — FINAL REPORT

Pre-registration sha256 `5190a074…`, hash-bound and verified. Inference candidates frozen before the calibration run (module sha256 `1164b949df0fe7d2…`).

Research-infrastructure validation. No strategy was tested; no economic claim is made.

## Why this existed

Three headline claims in this program were overturned by deeper analysis, and one survived independent adversarial review. H-EMA-3 reported a +15.5σ directional edge that H-COST-1 later showed a zero-EMA rule reproduced. The question was whether the framework can distinguish genuine directional information from artifacts of stop geometry, execution convention and estimator design.

## The artifact, reproduced on data with zero directional information

| geometry | direction rule | historical estimator | safe estimator |
|---|---|---|---|
| symmetric | independent | −0.0032 (t −0.58) | −0.0032 (t −0.58) |
| symmetric | correlated with R | −0.0057 (t −1.04) | −0.0057 (t −1.04) |
| asymmetric 2× | correlated with R | +0.0023 (t +0.41) | **INVALID_COMPARISON** |
| asymmetric 3× | correlated with R | +0.0099 (t +1.86) | **INVALID_COMPARISON** |
| asymmetric 4× | correlated with R | **+0.0142 (t +2.76)** | **INVALID_COMPARISON** |

The artifact grows monotonically with asymmetry when direction tracks R, and shows no trend when direction is independent. **The dangerous condition is not asymmetry by itself — it is asymmetry plus directional selection correlated with it.** Neither alone suffices.

## Type-I calibration — 2,000 replications, α = 0.05, tolerance [0.02, 0.08]

| null | iid | block (primary) | cluster (secondary) |
|---|---|---|---|
| N1 random direction | 4.5% PASS | 4.4% PASS | 5.7% PASS |
| N4 symmetric stop | 4.5% PASS | 4.4% PASS | 5.7% PASS |
| N5 constant LONG | 19.4% FAIL | 9.5% FAIL | 6.7% PASS |
| N6 constant SHORT | 19.4% FAIL | 9.5% FAIL | 6.7% PASS |

Verdicts are by 95% CI, not point estimate. **The pre-declared PRIMARY fails its own gate for persistent direction** (block 9.5%, CI [0.082, 0.108], entirely above 0.08). The pre-declared SECONDARY passes everywhere.

## Direction-persistence boundary

| persistence | iid | block | cluster |
|---|---|---|---|
| 0.00 | 0.8% FAIL | 3.3% PASS | 6.4% PASS |
| 0.25 | 1.7% INCO | 3.0% INCO | 6.2% PASS |
| 0.50 | 4.7% PASS | 4.6% PASS | 5.2% PASS |
| 0.75 | 10.4% FAIL | 7.9% INCO | 6.2% PASS |
| 0.90 | 14.2% FAIL | 7.6% INCO | 4.9% PASS |
| 1.00 | 20.2% FAIL | 9.8% INCO | 6.7% INCO |

The measured rule, not the assumed one: **iid inference is anti-conservative above persistence 0.5 (20.2% at 1.00) and over-conservative below it (0.8% at 0.00). Cluster inference is calibrated across the entire range.** Future experiments must declare their direction-persistence structure and use the inference calibrated for it.

## Remaining gates

| gate | result |
|---|---|
| zero directional bias | mean effect +0.00002 over 2,000 reps; antithetic average exactly 0 |
| path reflection | **bit-identical** under log barriers |
| direction reversal | exact identity to 1e-15 |
| N5/N6 mirror | mean effects sum to +0.00e+00; all three rejection rates identical |
| unequal-R rejection | 0 leaks across 8 unequal cells |
| scale invariance | spread 0.0e+00 across 4 price scales |
| execution conventions | under symmetric R: same-bar delta 0.000000, MARK vs LTP −0.000121 |
| planted edge | power 97.3% at p=0.55, 100% at p=0.60, 6.0% at p=0.50 |
| deterministic suite | 16 regression tests, all passing |

## Production MDE

```
MDE            0.03462 R
confidence     95%
alpha          0.05
sample size    2,400 bets
dependence     cluster
cluster def    non-overlapping 50-bet episodes
```

MDE scales as 1/√n, so at H-EMA-3's 117,000 bets it is ≈0.0050 R. **Mandatory field in every future strategy report.** An effect below it is *insufficient power to distinguish from the null*, never *no edge* — the error H-EMA-2 made with an MDE 7–24× its claimed effect.

## Verdict: PASS, with one governance item

Every substantive gate passes. The framework has a calibrated inference procedure for every direction-persistence regime, structurally refuses the comparison that manufactured the H-EMA-3 artifact, recovers a planted edge at 97–100% power, and is exactly invariant under reflection, reversal and scale.

**Governance item:** the pre-declared primary (moving-block) failed its own pre-declared gate and the pre-declared secondary (cluster) passed. Promoting cluster is a selection made after seeing the calibration. It is justified by a rule that was itself pre-declared — the CI-based PASS/FAIL criterion — but it changes the frozen hierarchy and should be ratified explicitly rather than assumed.

## Mandatory gate for all future strategy work

```
Gate 1  canonical zero-signal null behaves as calibrated
Gate 2  EQUAL-R: no estimator may compare legs with different R
Gate 3  the wider-stop artifact is classified INVALID, never PROMISING
Gate 4  planted edge recovered
Gate 5  MDE reported beside every null claim
Gate 6  direction-persistence declared, inference matched to it
Gate 7  economics only after Gates 1-6
```

## Stop

Methodology development ends here. The instrument is sufficiently trustworthy, not perfect. No H-NULL-2. Further methodology work only on evidence of a concrete failure in an existing test.

