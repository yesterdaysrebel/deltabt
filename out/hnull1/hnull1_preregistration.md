# H-NULL-1 — Pre-registration

**Status: FROZEN.** SHA-256 in `candidates.json` computed AFTER this file was
final and verified against it.

Research-infrastructure validation. **Not** a strategy search. Nothing here may
be used to find an edge; it exists to find out when our estimators invent one.

---

## 1. Question

> Can our research framework distinguish genuine directional information from
> artifacts created by stop geometry, execution conventions and estimator design?

Mandatory before any further indicator family, because three recent headline
claims were overturned by deeper analysis and one survived independent
adversarial review.

## 2. What is already established (audit, §3, complete)

Measured on a **driftless random walk with provably zero directional
information** — see `estimator_audit.md`:

    symmetric 0.4%/0.4%,  random direction        -0.0071  t -1.75   ok
    symmetric 0.4%/0.4%,  wider-stop direction    -0.0019  t -0.46   ok
    asymmetric 0.4%/0.1%, random direction        -0.0075  t -2.01   ok
    asymmetric 0.4%/0.1%, wider-stop direction    +0.0188  t +5.02   FALSE POSITIVE

**The dangerous condition is not asymmetry by itself. It is asymmetry PLUS
directional selection correlated with that asymmetry.** Neither alone suffices.

Also established: the theoretical null `P(hit +kR before -1R) = 1/(1+k)` is a
property of a continuous idealisation, not of any bar-level implementation.
Measured deviations at k=0.5 range from **-0.0203 to +0.0552** depending on
geometry — the violation cannot be signed or bounded a priori.

Two of my own causal explanations were isolated and refuted (same-bar
convention: delta 0.0000; MARK: ~11%; plain bar-range discretisation: sign
flips). This is why §7 requires each convention be isolated and its delta
reported rather than argued.

## 3. Gate 2 — Equal-R comparison invariant (STRUCTURAL)

Replaces the weaker "survives a symmetric control".

> **No primary estimator may compare outcomes whose stop distances differ across
> the compared directions.**

A valid comparison must satisfy at least one of:

    LONG R == SHORT R
    R is fixed independently of direction
    the estimator explicitly conditions/matches on R, and has been independently
        validated for that exact asymmetry

A post-hoc demonstration that a symmetric control happened to pass is
**insufficient**. The implementation must make invalid comparisons difficult or
impossible: the safe estimator REFUSES unequal-R input and returns
`INVALID_COMPARISON` with a reason, rather than returning a number.

Where a strategy naturally produces different LONG/SHORT stop distances, the
framework must either normalise/match the risk geometry before comparison, or
use an estimator proven invariant to that asymmetry. Reporting an asymmetric
comparison alongside "the symmetric control passed" is not permitted.

### What is NOT claimed

Symmetric stops are **not** claimed to guarantee unbiasedness mathematically.
The claim is only: *the tested symmetric-R construction removes the identified
false-positive mechanism under the tested execution conventions.* The permanent
rule is the stronger one — do not compare unequal-R legs unless an estimator has
been independently proven valid for that exact asymmetry.

## 4. Canonical null

Symmetric by construction: same entry population, identical long/short stop and
target geometry, identical execution rules and entry timing, with direction the
only randomness. It may not inherit information through stop width,
direction-dependent eligibility, symbol or volatility selection, position
overlap, or entry timing.

## 5. Null library

    N1  random direction                      expectation 0
    N2  random entry timing                   expectation 0
    N3  random direction + random timing      expectation 0
    N4  symmetric-stop null                   expectation 0
    N5  wider-stop side diagnostic            expectation ~0 under a safe estimator
    N6  narrower-stop side diagnostic         expectation ~0 under a safe estimator

N5/N6 are diagnostics, never strategies. Under the historical estimator with
asymmetric R they are expected to produce the artifact; under the safe estimator
they must be classified INVALID or return null-consistent.

## 6. Diagnostic matrix (permanent)

                          direction independent | direction correlated with R
    symmetric R                    SAFE          |          SAFE
    asymmetric R                   SAFE          |   POTENTIALLY INVALID

Verified experimentally on the driftless walk. All four cells reported.

## 7. Geometry and execution matrices

Geometry, pre-declared: long/short stop multipliers 1.0/1.0, 1.0/1.5, 1.0/2.0,
1.0/3.0, 1.0/4.0.

Execution conventions isolated one at a time, each reporting its delta against
the frozen setting: E1 frozen convention; E2 same-bar -> STOP vs TARGET vs
EXCLUDE; E3 MARK-triggered stop; E4 LTP reference.

## 8. Planted-edge test (MANDATORY GATE)

A synthetic signal with `P(correct direction) = 0.60`, constructed so the
planted edge is **independent of stop geometry**. Required: estimated excess
> 0, CI away from zero, useful power, survival across the tested stop widths and
execution conventions. The suite must FAIL if an estimator always returns zero.

## 9. Power

Every null result reports observed effect, 95% CI, MDE, n and effective n, and
is classified NULL CONSISTENT / INCONCLUSIVE / NULL FAILURE / SENSITIVITY
FAILURE. "Not significant" is never reported as "no effect".

## 10. Estimator explainability (§8 of the brief)

Each estimator documents inputs -> pairing/matching -> risk normalisation ->
outcome calculation -> bootstrap -> test statistic, and must answer:

> Can a direction rule gain statistical advantage merely by selecting the side
> with a different R?

If the answer is "yes" or "unknown", that estimator may not be used for future
directional-edge claims.

## 11. Architectural invariants (enforced in code)

    test_no_unmatched_directional_R_comparison   safe estimator refuses unequal-R
    test_adversarial_wider_stop_is_flagged       artifact classified INVALID,
                                                 not reported as a result
    test_planted_edge_recovered                  guards an always-zero estimator
    test_diagnostic_matrix                       all four cells behave as declared

## 12. PASS criteria

PASS requires ALL of: zero-signal nulls correct; the unequal-R artifact detected;
the safe estimator not fooled by it; symmetric-R construction correct; same-bar
isolated; MARK/LTP isolated; discretisation effects understood; planted edge
recovered; MDE reported; power adequate; TRAIN -> VALID frozen; and the future
strategy gate enforceable in code. Any failure gives PARTIAL PASS or FAIL, and
no further strategy family may begin.

## 13. Prohibited

No EMA/WPR/ADX/Supertrend testing. No parameter search for profitability. No
cost or stop-width optimisation. No symbol selection for performance. No search
for a profitable null. Success means invalidating false-discovery mechanisms.

## 14. Deviations

None. Any deviation found later is recorded here with the date and whether it
preceded or followed the run it affects.
