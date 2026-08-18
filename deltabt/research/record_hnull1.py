"""Append-only records for H-NULL-1 and the second H-EMA-3 attribution correction.

    PYTHONPATH=. python3 -m deltabt.research.record_hnull1

Neither historical H-EMA-3 record is modified. The first correction's central
conclusion stands; only the CAUSAL MECHANISM it named is retracted, because
isolating each convention on zero-signal data refuted it.
"""

from __future__ import annotations

import sys

from deltabt.research.registry import REGISTRY_PATH, Experiment, load_all, record

COMMON = dict(
    strategy_family="research infrastructure / estimator validation",
    symbols=["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"],
    data_start="2025-01-01", data_end="2026-04-16",
    position_sizing="per unit of risk",
    cost_assumptions="not applicable -- no economic claim is made",
)


def correction2() -> Experiment:
    return Experiment(
        experiment_id="H-EMA-3-CORRECTION-2", **COMMON,
        timeframe="n/a -- synthetic driftless random walk",
        hypothesis=("SECOND CORRECTION to H-EMA-3. Retracts the causal mechanism named in "
                    "H-EMA-3-CORRECTION while preserving its conclusion. Neither historical "
                    "record is modified."),
        entry_rules="n/a", exit_rules="n/a",
        parameters=dict(corrects="H-EMA-3-CORRECTION", found_by="H-NULL-1 estimator audit"),
        trades=23300, effective_n=23300,
        gross_expectancy_r=0.0188, net_expectancy_r=None, t_stat=5.02,
        baseline_result=dict(
            preserved_conclusion={"EMA directional edge": "NOT ESTABLISHED",
                                  "stop/execution geometry interaction": "CONFIRMED"},
            retracted_causes=[
                "same-bar-resolves-to-STOP convention as the primary cause",
                "MARK triggering as the primary cause",
                "simple bar-range discretisation"],
            isolation_evidence={
                "same-bar -> STOP (frozen)": {"excess_R": 0.0188, "t": 5.02},
                "same-bar -> TARGET": {"excess_R": 0.0188, "delta": 0.0},
                "same-bar EXCLUDED": {"excess_R": 0.0188, "delta": 0.0},
                "LTP instead of MARK": {"excess_R": 0.0168, "delta": -0.0020},
                "note": ("same-bar contributes exactly nothing: with a stop at -1R and a "
                         "target at +0.5R a bar spanning 1.5R is essentially never observed. "
                         "MARK explains ~11%. Bar-range discretisation is refuted because the "
                         "bias CHANGES SIGN with bar range (-0.0317 at range/R 0.054, +0.0552 "
                         "at 0.540) and persists as bar range -> 0.")},
            corrected_attribution=(
                "H-EMA-3's apparent directional excess is not evidence of EMA directional "
                "information. The effect is reproducible under a zero-information driftless "
                "random walk when stop distances differ AND the direction rule is correlated "
                "with which side has the larger R."),
            necessary_and_sufficient={
                "unequal R alone": "insufficient -- random direction at 4x asymmetry gives -0.0075 (t -2.01)",
                "direction/R correlation alone": "insufficient -- symmetric R with the wider-stop rule gives -0.0019 (t -0.46)",
                "both jointly": "FALSE POSITIVE POSSIBLE -- +0.0188 (t +5.02)"},
            permanent_regression_fixture={
                "process": "driftless random walk, zero directional information",
                "stops": "0.4% / 0.1% asymmetric",
                "direction_rule": "wider-stop side",
                "expected_artifact": {"excess_R": 0.0188, "t": 5.02}}),
        out_of_sample=dict(note="synthetic; no market data claim", test="LOCKED - not computed"),
        robustness=dict(
            methodological_result=(
                "The statistic is not identifiable as directional alpha when unequal risk "
                "units are allowed to correlate with direction. This is stronger and more "
                "durable than saying the H-EMA-3 statistic was 'wrong'."),
            what_is_NOT_claimed=(
                "Symmetric stops are NOT claimed to guarantee unbiasedness mathematically. "
                "The tested symmetric-R construction removes the identified false-positive "
                "mechanism under the tested execution conventions -- no more."),
            standing_rule=("Do not compare unequal-R legs unless an estimator has been "
                           "independently proven valid for that exact asymmetry."),
            null_is_implementation_dependent=(
                "P(hit +kR before -1R) = 1/(1+k) is a property of a continuous idealisation, "
                "not of any bar-level implementation. Measured deviations at k=0.5 run from "
                "-0.0203 to +0.0552 depending on geometry, so the violation cannot be signed "
                "or bounded a priori and must be measured on zero-signal data before use.")),
        classification="NO SIGNAL",
        reason=("The mechanism previously recorded is retracted. Isolating each execution "
                "convention on a driftless random walk shows the same-bar rule contributes "
                "0.0000 and MARK ~11%, while the bias persists and even changes sign under "
                "conditions those explanations cannot produce. The conclusion of the first "
                "correction is unchanged and is now more strongly supported: the excess is a "
                "stop-geometry interaction, not EMA directional information."),
        notes=("Second correction in the same append-only chain: H-EMA-3 -> "
               "H-EMA-3-CORRECTION -> H-EMA-3-CORRECTION-2. Two rounds of my own causal "
               "attribution were wrong before the effect was isolated empirically, which is "
               "itself the argument for H-NULL-1's requirement that every convention be "
               "isolated and its delta reported rather than argued. Full audit in "
               "out/hnull1/estimator_audit.md."),
    )


def hnull1() -> Experiment:
    """H-NULL-1 itself.

    `classification` is deliberately None. The registry's vocabulary describes
    STRATEGY outcomes -- edge, no edge, no economic edge -- and H-NULL-1 makes no
    directional claim about any market, so every available verdict would be a
    category error. H-Structure-1's record set the precedent that nothing is added
    to `Experiment.VALID` to accommodate a new vocabulary; the PASS verdict lives
    in `reason` and `baseline_result` where it can be read without pretending the
    experiment traded anything.
    """
    return Experiment(
        experiment_id="H-NULL-1", **COMMON,
        timeframe="synthetic driftless GBM paths; 1m equivalent bar spacing",
        hypothesis=(
            "Can our research framework distinguish genuine directional information "
            "from artifacts created by stop geometry, execution conventions and "
            "estimator design? Tested by feeding the framework data that provably "
            "contains no directional information and requiring it to say so."),
        entry_rules=(
            "Not a trading rule. Signals are generated by the null library: N1 random "
            "direction, N4 symmetric stop, N5 constant LONG, N6 constant SHORT, plus a "
            "two-state Markov direction chain sweeping P(stay) over "
            "{0.00,0.25,0.50,0.75,0.90,1.00}, and a planted-edge generator with "
            "P(correct)in{0.50,0.55,0.60}."),
        exit_rules=(
            "Barrier walk parameterised over every execution convention under test: "
            "same-bar stop+target resolving to STOP / to TARGET / excluded, and MARK "
            "vs LTP triggering. Barriers placed in linear and log space."),
        parameters=dict(
            preregistration_sha256="5190a0746393138ec4d82cf45a84151269ba236e89f327f7e12d5216ef84d390",
            inference_module_sha256="1164b949df0fe7d2b04d062d54a13d659e07ac47bf9544206631e4e7115a6810",
            alpha=0.05, acceptable_interval=[0.02, 0.08], replications=2000, n_boot=200,
            verdict_rule="PASS iff the entire 95% CI of the rejection rate lies inside [0.02,0.08]",
            block_length_rule="b = FIRST lag at which |acf| falls below 2/sqrt(n), clamped to b <= n//2",
            frozen_hierarchy_before_run=dict(primary="moving-block bootstrap",
                                             secondary="cluster (non-overlapping 50-bet episodes)",
                                             diagnostic="iid"),
        ),
        trades=None, effective_n=2400.0,
        gross_expectancy_r=None, net_expectancy_r=None, t_stat=None,
        baseline_result=dict(
            verdict="PASS, with one governance item",
            framework_bias=dict(
                mean_effect_over_2000_reps=2.02e-05,
                antithetic_pairing="exactly 0",
                path_reflection="bit-identical",
                direction_reversal="exact to 1e-15",
                note=("the historical -0.0035R was direction-seed noise, not a framework "
                      "offset; the framework's own bias is provably zero")),
            type_i_error=dict(
                note="alpha=0.05, tolerance [0.02,0.08], 2000 replications, verdict by CI",
                N1_random_direction=dict(iid=[0.0445, "PASS"], block=[0.0440, "PASS"],
                                         cluster=[0.0565, "PASS"]),
                N4_symmetric_stop=dict(iid=[0.0445, "PASS"], block=[0.0440, "PASS"],
                                       cluster=[0.0565, "PASS"]),
                N5_constant_LONG=dict(iid=[0.1940, "FAIL"], block=[0.0950, "FAIL"],
                                      cluster=[0.0670, "PASS"]),
                N6_constant_SHORT=dict(iid=[0.1940, "FAIL"], block=[0.0950, "FAIL"],
                                       cluster=[0.0670, "PASS"]),
                mirror_check=("N5 and N6 mean effects sum to exactly +0.00e+00 and all "
                              "three rejection rates are identical")),
            persistence_sweep={
                "0.00": dict(iid=[0.008, "FAIL"], block=[0.033, "PASS"], cluster=[0.064, "PASS"]),
                "0.25": dict(iid=[0.017, "INCONCLUSIVE"], block=[0.030, "INCONCLUSIVE"], cluster=[0.062, "PASS"]),
                "0.50": dict(iid=[0.047, "PASS"], block=[0.046, "PASS"], cluster=[0.052, "PASS"]),
                "0.75": dict(iid=[0.104, "FAIL"], block=[0.079, "INCONCLUSIVE"], cluster=[0.062, "PASS"]),
                "0.90": dict(iid=[0.142, "FAIL"], block=[0.076, "INCONCLUSIVE"], cluster=[0.049, "PASS"]),
                "1.00": dict(iid=[0.202, "FAIL"], block=[0.098, "INCONCLUSIVE"], cluster=[0.067, "INCONCLUSIVE"]),
                "interpretation": ("iid is anti-conservative above persistence 0.5 AND "
                                   "over-conservative below it -- it is wrong in both "
                                   "directions, not merely optimistic. Cluster is the only "
                                   "method calibrated across the whole range."),
            },
            planted_edge_power={
                "0.50": dict(iid=0.037, block=0.037, cluster=0.060),
                "0.55": dict(iid=0.987, block=0.987, cluster=0.973),
                "0.60": dict(iid=1.0, block=1.0, cluster=1.0),
            },
        ),
        out_of_sample=dict(
            note=("H-NULL-1 has no TRAIN/VALID/TEST split. It is run on synthetic "
                  "zero-information paths, so no market data segment is consumed."),
            test="LOCKED - not computed, and not touched by this experiment"),
        robustness=dict(
            geometry_matrix=dict(
                unequal_R_leaks=0, symmetric_allowed=2, verdict="PASS",
                dose_response=("with direction correlated to R, the HISTORICAL estimator's "
                               "excess rises monotonically with stop asymmetry: -0.0057 at 1x, "
                               "+0.0023 at 2x, +0.0099 at 3x, +0.0142 (t +2.76) at 4x. With "
                               "direction independent of R there is no trend at any multiplier."),
                necessary_and_sufficient=("the artifact requires asymmetry AND directional "
                                          "selection correlated with it; neither alone suffices"),
                gate_2=("safe_paired_excess raises InvalidComparison on every unequal-R cell "
                        "rather than returning a number; 8 of 10 cells refused, 0 leaks")),
            execution_conventions=dict(
                same_bar_TARGET_delta=0.0, same_bar_EXCLUDED_delta=0.0,
                LTP_instead_of_MARK_delta=-0.000121,
                verdict=("under symmetric R the execution conventions contribute 0.000000 "
                         "between them; they are not the mechanism")),
            scale_invariance=dict(price_scales=[100.0, 1000.0, 10000.0, 100000.0],
                                  spread=0.0, verdict="PASS"),
            production_mde=dict(
                mde_gross_R=0.03462, sample_size_bets=2400, confidence=0.95, alpha=0.05,
                method="cluster",
                scaling="1/sqrt(n) -- approx 0.0050 R at H-EMA-3's 117,000 bets"),
        ),
        classification=None,
        reason=(
            "PASS, with one governance item. The framework's own bias is provably zero "
            "(antithetic pairing exactly 0, path reflection bit-identical, direction "
            "reversal exact to 1e-15); it structurally refuses the unequal-R comparison "
            "that manufactured the H-EMA-3 artifact (0 leaks in 8 cells); it recovers a "
            "planted edge at 97-100% power from P(correct)=0.55; and it is exactly "
            "invariant under reflection, reversal and price scale. GOVERNANCE ITEM: the "
            "pre-declared PRIMARY (moving-block) FAILED its own pre-declared gate at "
            "19.4%/9.5% under constant-direction nulls, while the pre-declared SECONDARY "
            "(cluster) PASSED at 6.7%. Promoting cluster was a selection made after "
            "seeing the calibration, justified by a criterion that was itself "
            "pre-declared, and it was referred for explicit ratification rather than "
            "assumed. The operator ratified it: cluster is PRIMARY, iid is diagnostic, "
            "moving-block is secondary diagnostic. See out/hnull1/inference_promotion.json."),
        notes=(
            "H-NULL-1 closes methodology development for this program. Seven gates are now "
            "mandatory for every subsequent experiment: (1) the canonical zero-signal null "
            "behaves as calibrated; (2) EQUAL-R -- no estimator may compare legs with "
            "different R; (3) the wider-stop artifact is classified INVALID, never "
            "PROMISING; (4) a planted edge is recovered; (5) the MDE is reported beside "
            "every null claim; (6) direction persistence is declared and inference matched "
            "to it; (7) economics are tested only after (1)-(6). Two bugs found and fixed "
            "BEFORE any headline result: the original block-length rule returned 244/288/270 "
            "for white noise / AR(0.5) / AR(0.9) -- no discrimination at all -- and the "
            "block SE collapsed to exactly 0 when b >= n, making any mean infinitely "
            "significant. Both are documented in out/hnull1/. The discarded rule is kept in "
            "block_length_rule.json rather than deleted. Full report: "
            "out/hnull1/hnull1_final.md; estimator audit: out/hnull1/estimator_audit.md."),
    )


def main(path=REGISTRY_PATH) -> int:
    have = {r["experiment_id"] for r in load_all(path)}
    for exp in (correction2(), hnull1()):
        if exp.experiment_id in have:
            print(f"{exp.experiment_id} already recorded; append-only, nothing written.")
            continue
        record(exp, path)
        print(f"recorded {exp.experiment_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
