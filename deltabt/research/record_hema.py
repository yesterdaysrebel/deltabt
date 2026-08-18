"""Append the H-EMA-2 and H-EMA-3 results to the experiment registry.

    PYTHONPATH=. python3 -m deltabt.research.record_hema

Two records, because both are part of the account: H-EMA-2 is SUPERSEDED with
its integrity failures named, and H-EMA-3 carries the verdict.
"""

from __future__ import annotations

import json
import sys

from deltabt.research.registry import REGISTRY_PATH, Experiment, load_all, record

COMMON = dict(
    strategy_family="EMA crossover / slope / volatility / pullback / HTF regime",
    symbols=["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"],
    data_start="2025-01-01", data_end="2026-04-16",
    position_sizing="constant unit risk; every metric per unit of risk and invariant to contract count",
    cost_assumptions="Delta India per-symbol taker x1.18 GST + 2.0 bps slippage, per-symbol funding cadence",
)


def hema2() -> Experiment:
    return Experiment(
        experiment_id="H-EMA-2", **COMMON,
        timeframe="5m / 15m / 1h signals, 1m fill resolution",
        hypothesis=("Do EMA mechanisms have edge on higher timeframes after costs and after "
                    "controlling for stop geometry? 135 pre-declared arms against a "
                    "stop-width-matched random-direction control."),
        entry_rules="5 mechanisms x 5 EMA pairs x 3 timeframes; crossover as a FALSE->TRUE event",
        exit_rules="frozen Supertrend(10,2.0) leg-extreme stop on the execution timeframe, 2R target, 5% stop cap",
        parameters=dict(arms=135, mechanisms=["M1", "M2", "M3", "M4", "M5"],
                        ema_pairs=[[5, 20], [9, 21], [12, 26], [20, 50], [50, 200]],
                        controls=["C_a timestamp-matched", "C_b width-matched"], seeds=[11, 23, 37, 53, 71]),
        trades=155715, effective_n=None,
        gross_expectancy_r=0.0492, net_expectancy_r=-0.1076,
        baseline_result=dict(note="control comparison unreliable; see reason"),
        out_of_sample=dict(train="135 arms, 9 of 84 eligible net-positive", valid="NOT RUN", test="LOCKED"),
        robustness=dict(
            cost_per_R_by_tf={"5m": 0.305, "15m": 0.152, "1h": 0.077},
            median_stop_pct_by_tf={"5m": 0.717, "15m": 1.235, "1h": 2.563},
            verified="cost/R x median stop = 0.19-0.20 across all timeframes, i.e. 2(taker+slip)/stop"),
        classification="NO ECONOMIC EDGE",
        reason=("SUPERSEDED BY H-EMA-3 -- the economic conclusion held but the protocol did not. "
                "Independent review found ten deviations: the recorded pre-registration hash did not "
                "match the file (a section was appended after sealing); the frozen primary metric "
                "(EMA net - C-b net) was replaced after it proved inconvenient and the substitute was "
                "falsely described as pre-registered; the control's contamination was misdiagnosed as a "
                "long/short stop asymmetry when the real cause was an open bottom decile admitting stops "
                "far tighter than the arm ever traded; the control's direction was selected conditional "
                "on stop width rather than by coin; exit walks resolved on the next segment's data. "
                "Decisively, the design's per-arm minimum detectable effect was 0.14-0.16R while the "
                "effects it claimed to rule out were 0.006-0.022R -- 7-24x too blunt to support its own "
                "null. Its one solid finding is that cost/R is mechanically 2(taker+slip)/stop_pct."),
        notes=("Reported results are unchanged and reproducible; the failure is one of protocol "
               "integrity, not of arithmetic. Full deviation log in out/hema2/ S 14."),
    )


def hema3() -> Experiment:
    return Experiment(
        experiment_id="H-EMA-3", **COMMON,
        timeframe="5m / 15m / 1h signals, 1m barrier resolution",
        hypothesis=("Do EMA mechanisms carry directional information, and is it large enough to pay the "
                    "round trip? Measured by a paired mirror-direction barrier test: at every signal bar "
                    "score the signal against the average of BOTH directions, each using its own "
                    "structural stop, swept over barrier multiples k."),
        entry_rules="mechanisms and pairs inherited unchanged from H-EMA-2's frozen manifest",
        exit_rules=("barrier sweep k in {0.5, 1, 2, 4} against a 1R structural stop; walk truncated at "
                    "the split boundary; same-bar barrier+stop resolves to STOP"),
        parameters=dict(estimator="paired mirror-direction barrier test", barriers=[0.5, 1.0, 2.0, 4.0],
                        dedup=["symbol", "exec_tf", "bar_index", "side"], cluster="symbol-day",
                        why=("P(hit +kR before -1R) = 1/(1+k) for ANY stop width under a martingale, so "
                             "the estimator is scale-free and immune to the stop-geometry confound that "
                             "wrecked H-EMA-2's resampled control")),
        trades=120112, effective_n=40555,
        gross_expectancy_r=0.0181, net_expectancy_r=-0.2789,
        ci_low=0.0115, ci_high=0.0246, t_stat=5.40,
        baseline_result=dict(
            mirror_control="average of both directions at the same bar; zero seed noise, exactly paired",
            validated=("martingale null returns 1/(1+k) to within 0.03 at every k and is invariant across "
                       "an 8x range of stop widths; a planted 60/40 edge is recovered at t>5")),
        out_of_sample=dict(
            train={"k0.5": 0.0315, "k1": 0.0194, "k2": 0.0036, "k4": -0.0144,
                   "t_k0.5": 15.53, "t_k1": 6.11},
            valid={"k0.5": 0.0181, "k1": 0.0007, "k2": -0.0137, "k4": -0.0305,
                   "t_k0.5": 5.40, "t_k4": -2.80},
            supplementary_BEATUSD={"k0.5": 0.0379, "t": 3.56, "note": "no TRAIN data, blind"},
            test="LOCKED - not computed"),
        robustness=dict(
            valid_k05_by_symbol={"BTCUSD": 0.0253, "ETHUSD": 0.0078, "SOLUSD": 0.0221, "XRPUSD": 0.0173},
            valid_k05_by_tf={"5m": 0.0288, "15m": -0.0030, "1h": -0.0303},
            valid_halves={"H1": 0.0125, "H2": 0.0237},
            cost_floor_R=0.297, edge_as_pct_of_cost=6,
            decay="information decays monotonically in k and REVERSES: -0.0305R at k=4, t=-2.80",
            trend_rescue="refuted -- a wider target, trailing stop or longer hold all make it worse"),
        classification="NO ECONOMIC EDGE",
        reason=("A real, replicating directional edge exists and is 16x too small to trade. At k=0.5 the "
                "excess over a mirror-direction control is +0.0315R on TRAIN (t=15.5) and +0.0181R on "
                "VALID (t=5.40, CI excluding zero), positive in all four symbols, both VALID halves, four "
                "of five mechanisms, and independently in blind BEATUSD. Against a round-trip cost floor "
                "of 0.297R that is 6%. The edge is a 5m phenomenon (+0.0288) while 5m carries the worst "
                "cost/R (0.305); at 1h where cost/R is 0.077 the edge is negative (-0.0303). Signal and "
                "economics point in opposite directions."),
        notes=("Supersedes H-EMA-2. The decay curve is the durable result: information is concentrated "
               "below 1R and reverses by 4R, which refutes the trend-following rescue on evidence rather "
               "than assumption and explains why H-EMA-2's fixed 2R exit was structurally blind. "
               "Methodological lesson recorded: H-EMA-2 declared a null with an instrument whose MDE was "
               "7-24x the effect size; the same data under a paired estimator shows 15 sigma. Recommended "
               "successor is H-COST-1 -- sweep cost/R cross-sectionally rather than sweeping signals. "
               "TEST segment never computed."),
    )


def main(path=REGISTRY_PATH) -> int:
    have = {r["experiment_id"] for r in load_all(path)}
    for exp in (hema2(), hema3()):
        if exp.experiment_id in have:
            print(f"{exp.experiment_id} already recorded; append-only registry, nothing written.")
            continue
        record(exp, path)
        print(f"recorded {exp.experiment_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
