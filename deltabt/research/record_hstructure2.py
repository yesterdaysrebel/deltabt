"""Append H-STRUCTURE-2 to the experiment registry.

    PYTHONPATH=. python3 -m deltabt.research.record_hstructure2

Every number is transcribed from out/hstructure2/train_results.json, not
recomputed.

VERDICT VOCABULARY
    The phase verdict is INSUFFICIENT POWER, which the registry's older and
    narrower vocabulary does not contain. INSUFFICIENT DATA is its designated
    entry for "the sample could not resolve the question", so that is the
    mapping used, exactly as H-Structure-1 mapped its DEAD onto NO SIGNAL.
    Nothing is added to Experiment.VALID to accommodate a new word.

    That label understates the result in one specific way, so `reason` says it
    outright: the MDE was about 3x SMALLER than the round-trip cost floor, so
    the experiment was comfortably powered for every effect size that could
    ever have been traded. This is NOT a "collect more data" verdict.
"""

from __future__ import annotations

import json
import sys

from deltabt.research import hstructure2 as h2
from deltabt.research.registry import REGISTRY_PATH, Experiment, load_all, record

EXPERIMENT_ID = "H-STRUCTURE-2"
R = json.loads((h2.OUT / "train_results.json").read_text())
MAN = json.loads((h2.OUT / "manifest.json").read_text())
PRIMARY = f"+{h2.PRIMARY_HORIZON_MIN}m"


def _fam(name: str) -> dict:
    a = R[name]
    p = a["horizons"][PRIMARY]["pooled"]
    return dict(
        n=p["n"], n_day_clusters=p["n_clusters"],
        long=a["n_long"], short=a["n_short"],
        effect_at_1h=p["effect"], mde_at_1h=p["mde"],
        effect_over_mde=p["effect"] / p["mde"],
        cluster_t=p["t"], ci=[p["ci_low"], p["ci_high"]],
        control_mean=a["control"]["mean"], control_p=a["control"]["p_value"],
        halves={"H1": a["halves"]["H1"]["effect"], "H2": a["halves"]["H2"]["effect"]},
        per_symbol={k: v["effect"] for k, v in a["per_symbol"].items()},
        by_horizon={k: v["pooled"]["effect"] for k, v in a["horizons"].items()},
        by_horizon_t={k: v["pooled"]["t"] for k, v in a["horizons"].items()},
        gate={k: v["passed"] for k, v in a["gate"].items() if k.startswith("A")},
        verdict=a["gate"]["verdict"])


def build() -> Experiment:
    return Experiment(
        experiment_id=EXPERIMENT_ID,
        hypothesis=(
            "HH/HL and LH/LL structural transitions contain directional information "
            "about subsequent price movement. STAGE A ONLY: forward PRICE returns, "
            "with no stop, no target, no R, no fees, no slippage and no funding "
            "anywhere in the measurement. H-Structure-1 tested this family JOINTLY "
            "with a 2R/structural-stop geometry and returned NO SIGNAL; H-COST-1 and "
            "H-NULL-1 then showed geometry can both hide a real effect and "
            "manufacture a false one, so the joint test could not settle it. Four "
            "frozen events in two families: S2-CONT (HH->HL->break the HH, and the "
            "LL->LH->break the LL mirror) and S2-FAIL (HH->HL->break the HL, and its "
            "mirror)."),
        strategy_family="market structure HH/HL/LH/LL transitions -- information test",
        symbols=list(h2.SYMBOLS),
        timeframe="15m structure, 1m measurement resolution",
        data_start="2025-01-01", data_end="2025-12-20 (TRAIN only)",
        entry_rules=(
            "Not a trading rule -- an event definition. Swing detection is REUSED "
            "UNCHANGED from the H-Structure-1 archive (out/hstructure/code/"
            "hstructure.py, loaded by path, not copied), which passed that "
            "experiment's anti-lookahead audit. Fractal swings, strict inequality "
            "both sides, N=3 on a 15m grid, confirmed only at the close of bar k+N "
            "so the confirmation delay is exactly 3 structure bars by construction. "
            "ONESHOT trigger. Measurement reference price is the OPEN of the first "
            "1m bar at or after the structure bar's close. Bars satisfying a "
            "continuation and a failure condition simultaneously are DROPPED."),
        exit_rules=(
            "None. Stage A has no exit: it measures the forward price return at six "
            "pre-declared horizons (+5m, +15m, +30m, +1h, +4h, +1d), signed by the "
            "event's hypothesized direction. The primary horizon +1h was declared "
            "before TRAIN so that 'any of six horizons passes' could not become a "
            "six-fold multiple test."),
        position_sizing="not applicable -- no position is taken at Stage A",
        cost_assumptions=(
            "None applied. Costs enter only as the interpretive benchmark: the "
            "production round trip is 2 x (5 bps taker x 1.18 GST + 2.0 bps "
            "slippage) = 15.8 bps."),
        parameters=dict(
            protocol_sha256=MAN["protocol_sha256"],
            preregistration_sha256=MAN["preregistration_sha256"],
            module_sha256=MAN["module_sha256"],
            reused_swing_detector=MAN["reused_swing_detector"],
            structure_tf_min=h2.STRUCT_TF_MIN, swing_n=h2.SWING_N,
            trigger=h2.TRIGGER, horizons_min=list(h2.HORIZONS_MIN),
            primary_horizon_min=h2.PRIMARY_HORIZON_MIN,
            inference_primary="cluster", cluster_unit="calendar UTC day, pooled across symbols",
            mde_k=h2.MDE_K, control="within-symbol direction permutation, 1000 perms",
            control_seed=h2.CONTROL_SEED,
            single_arm=("ONE arm. N, timeframe and trigger were chosen a priori and "
                        "NOT swept; the event census read no forward return and was "
                        "barred from changing them.")),
        trades=None,
        effective_n=float(R["S2-CONT"]["horizons"][PRIMARY]["pooled"]["n_clusters"]),
        gross_expectancy_r=None, net_expectancy_r=None,
        t_stat=R["S2-CONT"]["horizons"][PRIMARY]["pooled"]["t"],
        ci_low=R["S2-CONT"]["horizons"][PRIMARY]["pooled"]["ci_low"],
        ci_high=R["S2-CONT"]["horizons"][PRIMARY]["pooled"]["ci_high"],
        baseline_result=dict(
            control=("timestamp-matched direction permutation within symbol: "
                     "preserves symbol, timestamp and the exact direction imbalance, "
                     "randomizes only which event gets which direction. A fair coin "
                     "would not reproduce the imbalance, so any drift in the window "
                     "would leak into the signal as though it were structure."),
            S2_CONT=dict(observed=R["S2-CONT"]["horizons"][PRIMARY]["pooled"]["effect"],
                         control_mean=R["S2-CONT"]["control"]["mean"],
                         p_value=R["S2-CONT"]["control"]["p_value"]),
            S2_FAIL=dict(observed=R["S2-FAIL"]["horizons"][PRIMARY]["pooled"]["effect"],
                         control_mean=R["S2-FAIL"]["control"]["mean"],
                         p_value=R["S2-FAIL"]["control"]["p_value"]),
            interpretation=("both observed effects sit in the middle of their own "
                            "permutation null distributions")),
        out_of_sample={
            "train": {"S2-CONT": _fam("S2-CONT"), "S2-FAIL": _fam("S2-FAIL")},
            "valid": ("NOT COMPUTED -- the Stage-A gate A1-A5 was not passed on "
                      "TRAIN, and VALID is run once and only after it passes. "
                      "Spending it on a hypothesis that already failed would consume "
                      "the out-of-sample segment for nothing."),
            "test": "LOCKED - not computed",
        },
        robustness=dict(
            event_census=MAN["census"],
            largest_abs_t_anywhere=1.17,
            bonferroni_note=("six pre-declared horizons would require |t| > 2.64; the "
                             "largest |t| in the whole table is 1.17, so no horizon "
                             "comes close even before correction -- there is nothing "
                             "to select from"),
            mde_vs_cost_floor=dict(
                round_trip_cost_bps=15.8,
                mde_at_1h_bps={"S2-CONT": 4.98, "S2-FAIL": 5.93},
                ratio=("the cost floor is ~3x the MDE, so any effect large enough to "
                       "survive execution costs would have been detected comfortably")),
            anti_lookahead=("structure state proved invariant to truncation at three "
                            "cut points; every event timed at or after the close of "
                            "the structure bar that defines it; horizons located by "
                            "TIMESTAMP so a cache gap drops the event instead of "
                            "silently shortening the window; events whose horizon "
                            "crosses a split boundary are excluded, which is what "
                            "keeps a +1d VALID event from reading a TEST price"),
        ),
        classification="INSUFFICIENT DATA",
        reason=(
            "Phase verdict INSUFFICIENT POWER, mapped onto the registry's "
            "INSUFFICIENT DATA. At the pre-declared +1h horizon S2-CONT gives "
            "-1.30 bps against an MDE of 4.98 bps (ratio -0.26, cluster t -0.73) and "
            "S2-FAIL gives +0.24 bps against an MDE of 5.93 bps (ratio 0.04, t 0.11). "
            "A2 fails for both, so the pre-declared tree returns INSUFFICIENT POWER "
            "and never NO EDGE. A3 and A4 also fail: permutation p = 0.383 and 0.867, "
            "and the two halves of TRAIN disagree in sign for both families. "
            "IMPORTANT -- this is NOT a 'collect more data' verdict. The MDE is about "
            "3x SMALLER than the 15.8 bps round-trip cost floor, so the experiment "
            "was comfortably powered for every effect size that could ever have been "
            "traded. The precise claim is therefore narrow: there is no structural "
            "effect here of a size that could matter economically. Whether some 1 bp "
            "effect exists is unresolved and uninteresting."),
        notes=(
            "Hypothesis 1 of the 3 available in the MARKET PHENOMENON DISCOVERY "
            "phase (out/phase_discovery/research_protocol.md). Stage B was not "
            "constructed: Stage-A survivors get an executable strategy and "
            "non-survivors do not. Per the failure stop rule no filter, threshold, "
            "timeframe, swing strength, confirmation indicator or exit rule was "
            "added -- each would be a new hypothesis and the budget is three. "
            "H-Structure-1 and H-STRUCTURE-2 now agree for INDEPENDENT reasons: the "
            "first found no tradable expectancy under one trade geometry, the second "
            "found no price information with the geometry removed entirely. The "
            "family is closed. The weakly negative continuation effect at +1h "
            "(t -0.73, a quarter of the MDE) is noise and was deliberately not "
            "pursued as a reversal signal. Full report out/hstructure2/"
            "hstructure2_final.md; remaining budget H-VOL-1 then H-REL-1."),
    )


def main(path=REGISTRY_PATH) -> int:
    if any(r["experiment_id"] == EXPERIMENT_ID for r in load_all(path)):
        print(f"{EXPERIMENT_ID} is already recorded; the registry is append-only "
              f"and this would duplicate it. Nothing written.")
        return 1
    print(f"recorded {EXPERIMENT_ID} to {record(build(), path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
