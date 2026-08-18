"""Append the H-EMA-3 interpretation correction and the H-COST-1 verdict.

    PYTHONPATH=. python3 -m deltabt.research.record_hcost1

APPEND-ONLY. The original H-EMA-3 record is neither overwritten nor deleted --
its numbers and methodology stand and are reproducible. What was wrong was the
ATTRIBUTION, and a registry that silently edited the claim would destroy the
evidence that the program can catch its own errors.
"""

from __future__ import annotations

import sys

from deltabt.research.registry import REGISTRY_PATH, Experiment, load_all, record

COMMON = dict(
    strategy_family="EMA crossover / slope / volatility / pullback / HTF regime",
    symbols=["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"],
    data_start="2025-01-01", data_end="2026-04-16",
    position_sizing="per unit of risk; invariant to contract count",
    cost_assumptions="Delta India per-symbol taker x1.18 GST + 2.0 bps slippage, per-symbol funding",
)


def correction() -> Experiment:
    return Experiment(
        experiment_id="H-EMA-3-CORRECTION", **COMMON,
        timeframe="5m / 15m / 1h signals, 1m barrier resolution",
        hypothesis=("CORRECTION to the interpretation of H-EMA-3. The original record's numbers and "
                    "methodology stand and reproduce exactly; its ATTRIBUTION does not."),
        entry_rules="unchanged from H-EMA-3",
        exit_rules="unchanged from H-EMA-3",
        parameters=dict(corrects="H-EMA-3", found_by="H-COST-1 symmetric synthetic stop layer",
                        original_claim_now_invalid="EMA contains a real, replicating directional edge"),
        trades=116668, effective_n=114067,
        gross_expectancy_r=0.0043, net_expectancy_r=-0.1551,
        t_stat=1.67,
        baseline_result=dict(
            adversarial_decomposition={
                "EMA signal (frozen, executable)": {"excess_R": 0.0324, "t": 15.56},
                "wider-structural-stop rule, ZERO EMA content": {"excess_R": 0.0319, "t": 13.62},
                "narrower-stop rule": {"excess_R": -0.0319, "t": -13.62}},
            collinearity="EMA agrees with the wider-stop side on 88.2% of bars",
            median_per_bar_stop_asymmetry="3.20x",
            hit_rates={"wider-stop side": 0.6661, "martingale null": 0.6667,
                       "narrower-stop side": 0.6235},
            mechanism=("The wide side lands ON the null; the NARROW side underperforms it. A stop 3.2x "
                       "tighter is taken out by intrabar noise before the target, and the frozen "
                       "same-bar convention resolves ties to the STOP while stops trigger on MARK, "
                       "whose range exceeds LTP. The statistic measured which leg escaped a mechanical "
                       "penalty, not which direction price went."),
            why_the_estimator_was_trusted=("P(hit +kR before -1R) = 1/(1+k) for any stop width holds for "
                                           "the null IN EXPECTATION, which is what both the author and "
                                           "the independent reviewer relied on. It does not hold for the "
                                           "REALISED statistic, because the same-bar and mark-price "
                                           "conventions are not symmetric in stop width.")),
        out_of_sample=dict(
            symmetric_stop_train={"5m_0.50pct": 0.0049, "t": 1.84, "5m_1.00pct": 0.0043},
            symmetric_stop_valid={"5m_0.50pct": -0.0033, "t": -0.76, "5m_1.00pct": 0.0003},
            note="the residual under a symmetric stop does not replicate out of sample",
            test="LOCKED - not computed"),
        robustness=dict(
            corrected_interpretation=(
                "H-EMA-3's paired excess statistic did replicate out of sample. But adversarial "
                "decomposition shows the same statistic is reproduced by a zero-EMA wider-structural-"
                "stop rule; EMA agrees with that rule on ~88.2% of bars; and the wider-stop side itself "
                "lands approximately on the martingale null. The observed excess was therefore caused by "
                "structural stop-width asymmetry interacting with execution conventions, not by "
                "demonstrated directional EMA information. Symmetric synthetic stops reduce the excess "
                "from ~+0.0324R to ~+0.0043-0.0049R on TRAIN, and it does not replicate on VALID."),
            citation_guidance=("H-EMA-3 must NOT be cited as evidence of a real EMA directional edge."),
            wider_stop_rule=("Recorded as a DIAGNOSTIC/CONTROL discovery, not a strategy. It sits on the "
                             "martingale null (66.61% vs 66.67%), so it is not an edge -- it is merely "
                             "un-penalised. It is explicitly not to be developed into a strategy sweep.")),
        classification="NO SIGNAL",
        reason=("The directional edge attributed to EMA in H-EMA-3 does not exist as directional "
                "information. A rule containing no EMA at all reproduces the statistic to within 0.0005R "
                "(+0.0319 vs +0.0324) at comparable significance. Under a stop that is symmetric by "
                "construction, at the same bars and the same target distance, the excess falls 85% to "
                "+0.0049R (t 1.84) on TRAIN and fails to replicate on VALID (-0.0033R, t -0.76)."),
        notes=("APPEND-ONLY CORRECTION. The original H-EMA-3 record is preserved intact; its arithmetic "
               "is reproducible and unchanged. The methodological finding is the important one: the "
               "estimator could manufacture a t = +15.5 result from zero directional information, and "
               "neither the author nor an independent adversarial reviewer caught it -- both relied on a "
               "scale-free argument that is valid for the null in expectation but not for the realised "
               "statistic. It was found only when H-COST-1 replaced the structural stop with a symmetric "
               "one for unrelated reasons. Next priority is a universal adversarial/null framework that "
               "every future result must pass, ahead of any further indicator family."),
    )


def hcost1() -> Experiment:
    return Experiment(
        experiment_id="H-COST-1", **COMMON,
        timeframe="5m / 15m / 1h, synthetic percentage stops",
        hypothesis=("Is there a region of stop geometry, timeframe, volatility, symbol and execution "
                    "cost in which the H-EMA-3 edge survives? An economic feasibility experiment: the "
                    "signal is frozen and only the economics vary."),
        entry_rules=("frozen H-EMA-3 k=0.5 pooled bet population, conflicting bars DROPPED "
                     "(LONG-only->LONG, SHORT-only->SHORT, BOTH->DROP); 1.46% TRAIN / 1.51% VALID"),
        exit_rules=("synthetic stop = entry x (1 -/+ width), width in "
                    "{0.25,0.5,0.75,1,1.5,2,3,5}% primary and {7.5,10}% out-of-model diagnostic; "
                    "barrier k in {0.25,0.5,0.75,1.0}R"),
        parameters=dict(stage_a="timeframe x stop width x volatility, baseline cost, k=0.5",
                        stage_b="exit x cost scenario over all cells with n>=500, not performance-selected",
                        cost_scenarios=["baseline 2bps", "low 1bps", "high 5bps", "maker exit"],
                        volatility="ATR(14)/close, TRAIN P33/P67, frozen and reused on VALID",
                        control="H-EMA-3 paired mirror-direction under the SAME synthetic stop"),
        trades=40164, effective_n=28711,
        gross_expectancy_r=-0.0033, net_expectancy_r=-0.3113,
        t_stat=-0.76,
        baseline_result=dict(
            note="mirror control under a symmetric stop -- the comparison H-EMA-3 could not make",
            cost_identity="cost/R = 2(taker+slippage)/width, exact because width is fixed"),
        out_of_sample=dict(
            train="24 primary cells, 22 with positive excess, ALL RED",
            valid="24 primary cells, 7 with positive excess, 0 with |t|>2 positive, ALL RED",
            diagnostic="6 out-of-model cells >5%: break-even not reached even outside the constraint",
            test="LOCKED - not computed"),
        robustness=dict(
            gates={"GREEN": 0, "YELLOW": 0, "RED": 24},
            best_valid_excess=0.0070, min_required_multiple_valid="4.5x",
            break_even_prediction=("pre-registered prediction was break-even at ~8.73% stop width "
                                   "ASSUMING the +0.0181R edge persisted; it does not persist, so "
                                   "widening the stop cuts cost/R with no edge left to meet it"),
            prediction_outcome="CONFIRMED, for a stronger reason than predicted"),
        classification="NO ECONOMIC EDGE",
        reason=("No economically viable region exists. All 24 primary cells are RED on TRAIN and on "
                "VALID; the out-of-model diagnostic above the 5% risk ceiling does not reach break-even "
                "either. The residual excess under a symmetric stop is +0.004-0.005R on TRAIN with t<2 "
                "and does not replicate on VALID. The experiment's more important result is the "
                "attribution finding recorded in H-EMA-3-CORRECTION: the signal it was built to "
                "monetise was a stop-geometry artifact."),
        notes=("Pre-registration sha256 71d69f6f0ea9b350680c50d375f326fad8e1358928762d0f70344455d98f863d, "
               "hash-bound to the manifest and verified. The pre-run falsifiable prediction was recorded "
               "before TRAIN and confirmed. Volatility thresholds were TRAIN-derived and reused verbatim "
               "on VALID. Slippage is an assumption, not a measurement -- no order-book depth exists in "
               "this dataset -- which is why scenarios B and C exist and why no thin instrument is called "
               "attractive on a flat 2 bps basis. TEST never computed."),
    )


def main(path=REGISTRY_PATH) -> int:
    have = {r["experiment_id"] for r in load_all(path)}
    for exp in (correction(), hcost1()):
        if exp.experiment_id in have:
            print(f"{exp.experiment_id} already recorded; append-only, nothing written.")
            continue
        record(exp, path)
        print(f"recorded {exp.experiment_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
