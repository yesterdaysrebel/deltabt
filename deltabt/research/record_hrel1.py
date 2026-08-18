"""Append H-REL-1 to the experiment registry -- the last hypothesis in the phase.

    PYTHONPATH=. python3 -m deltabt.research.record_hrel1
"""

from __future__ import annotations

import json
import sys

from deltabt.research import hrel1 as r1
from deltabt.research.registry import REGISTRY_PATH, Experiment, load_all, record

EXPERIMENT_ID = "H-REL-1"
A = json.loads((r1.OUT / "train_results.json").read_text())["R1-LAG"]
MAN = json.loads((r1.OUT / "manifest.json").read_text())
_p = A["horizons"][f"+{r1.PRIMARY_HORIZON_MIN}m"]["pooled"]


def build() -> Experiment:
    return Experiment(
        experiment_id=EXPERIMENT_ID,
        hypothesis=(
            "Relative movements among BTC, ETH, SOL and XRP contain short-horizon "
            "predictive information. ONE formulation was selected before TRAIN, as "
            "the protocol requires: LEADER SHOCK / FOLLOWER UNDER-RESPONSE. BTC "
            "makes an unusually large 15m move; a follower moves less far in the "
            "same direction; does the follower close the gap? Chosen over "
            "relative-strength divergence and continuation because it is "
            "directional by construction -- the prediction's sign is the leader's "
            "sign, so nothing about direction is fitted -- and needs ONE new "
            "threshold rather than three."),
        strategy_family="cross-asset lead-lag -- information test",
        symbols=list(r1.SYMBOLS),
        timeframe="15m leader and follower returns, 1m measurement resolution",
        data_start="2025-01-01", data_end="2025-12-20 (TRAIN only)",
        entry_rules=(
            "Not a trading rule -- an event definition. BTCUSD is designated leader "
            "A PRIORI (largest asset, venue reference); it is NOT chosen by trying "
            "all four and keeping whichever leads best, which would be a four-arm "
            "search reported as one hypothesis. The event universe is therefore the "
            "three followers ETHUSD, SOLUSD, XRPUSD -- BTC cannot lag itself. "
            "shock[t]: |r_BTC[t]| >= the 95th percentile of |r_BTC| over the "
            "trailing 960 bars, window ENDING AT t-1 and excluding t, computed by "
            "the imported hcompress._rolling_quantile_causal. under[f,t]: "
            "sign(r_BTC[t]) * (r_BTC[t] - r_f[t]) > 0 -- a sign test against zero, "
            "with NO gap threshold. Event = shock AND under, direction = "
            "sign(r_BTC[t]), ONESHOT per follower. The two 15m series are INNER "
            "JOINED on timestamp and never forward-filled: a filled follower bar "
            "carries a price from before the shock and would look exactly like a "
            "follower that failed to move."),
        exit_rules=(
            "None. Forward price return at six pre-declared horizons, primary +1h "
            "declared before TRAIN."),
        position_sizing="not applicable -- no position is taken at Stage A",
        cost_assumptions=(
            "None applied. Costs enter only as the interpretive benchmark: the "
            "production round trip is 15.8 bps."),
        parameters=dict(
            protocol_sha256=MAN["protocol_sha256"],
            preregistration_sha256=MAN["preregistration_sha256"],
            module_sha256=MAN["module_sha256"],
            inherited=MAN["inherited"],
            leader=r1.LEADER, followers=list(r1.FOLLOWERS),
            shock_percentile=r1.SHOCK_PERCENTILE, pct_lookback=r1.PCT_LOOKBACK,
            tf_min=r1.TF_MIN, horizons_min=list(r1.HORIZONS_MIN),
            primary_horizon_min=r1.PRIMARY_HORIZON_MIN,
            inference_primary="cluster",
            cluster_unit="calendar UTC day, pooled across symbols",
            mde_k=r1.MDE_K, control_seed=r1.CONTROL_SEED,
            a5_symbols_required=r1.SYMBOLS_REQUIRED_A5,
            a5_deviation=("A5 is 2-of-3 rather than the protocol's 3-of-4, because "
                          "the leader is excluded from the event universe so 3-of-4 "
                          "is unreachable and would fail automatically -- a bug, not "
                          "a gate. Declared before TRAIN; A1-A4 and A6 unchanged."),
            single_arm="ONE formulation, ONE leader, ONE threshold. Nothing swept."),
        trades=None, effective_n=float(_p["n_clusters"]),
        gross_expectancy_r=None, net_expectancy_r=None,
        t_stat=_p["t"], ci_low=_p["ci_low"], ci_high=_p["ci_high"],
        baseline_result=dict(
            control="timestamp-matched within-symbol direction permutation, 1000 perms",
            observed=_p["effect"], control_mean=A["control"]["mean"],
            p_value=A["control"]["p_value"],
            interpretation="the observed effect sits inside its own permutation null"),
        out_of_sample={
            "train": dict(
                n=_p["n"], n_day_clusters=_p["n_clusters"],
                leader_shocks=MAN["leader_shocks"],
                long=A["n_long"], short=A["n_short"],
                effect_at_1h=_p["effect"], mde_at_1h=_p["mde"],
                effect_over_mde=_p["effect"] / _p["mde"], cluster_t=_p["t"],
                by_horizon={k: v["pooled"]["effect"] for k, v in A["horizons"].items()},
                by_horizon_t={k: v["pooled"]["t"] for k, v in A["horizons"].items()},
                halves={k: v["effect"] for k, v in A["halves"].items()},
                per_symbol={k: v["effect"] for k, v in A["per_symbol"].items()},
                gate={k: v["passed"] for k, v in A["gate"].items() if k.startswith("A")},
                verdict=A["gate"]["verdict"]),
            "valid": ("NOT COMPUTED -- Stage-A gate A1-A5 not passed on TRAIN"),
            "test": "LOCKED - not computed",
        },
        robustness=dict(
            event_census=MAN["census"],
            clustering_matters_most_here=(
                "1,527 events but only 249 day clusters: three followers reacting to "
                "the SAME BTC shock at the SAME timestamp are close to one "
                "observation, not three. The iid SE would have been 3.09 bps against "
                "the cluster's 4.01 bps -- a 1.3x understatement of uncertainty that "
                "the day cluster removes."),
            mde_vs_cost_floor=dict(
                round_trip_cost_bps=15.8, mde_at_1h_bps=10_000 * _p["mde"],
                ratio=15.8 / (10_000 * _p["mde"]),
                note="tightest margin of the three hypotheses, but the MDE is still "
                     "below the cost floor"),
            a4_a5_pass_but_do_not_matter=(
                "A4 and A5 PASS -- consistent sign across TRAIN halves and 2 of 3 "
                "followers. This is not evidence: the quantity being signed is "
                "indistinguishable from zero (0.17x the MDE) and from its own "
                "control (p = 0.557). A consistent sign on noise is still noise."),
            anti_lookahead=("the shock percentile window ends at t-1 and excludes t, "
                            "so a bar cannot help decide whether it is itself "
                            "unusual; only close-of-bar returns are read at t; "
                            "series are inner-joined, never forward-filled"),
        ),
        classification="INSUFFICIENT DATA",
        reason=(
            "Phase verdict INSUFFICIENT POWER, mapped onto the registry's "
            "INSUFFICIENT DATA. At the pre-declared +1h horizon the effect is "
            "+1.91 bps against an MDE of 11.23 bps (ratio 0.17, cluster t 0.48) on "
            "1,527 events in 249 day clusters, drawn from 2,916 BTC shocks. A2 "
            "fails, so the pre-declared tree returns INSUFFICIENT POWER and never "
            "NO EDGE. A3 also fails (permutation p = 0.557). A4 and A5 pass but "
            "carry no weight: a consistent sign on a quantity indistinguishable "
            "from noise is still noise. The MDE remains below the 15.8 bps "
            "round-trip cost floor, so an economically tradable lead-lag effect "
            "would have been detected."),
        notes=(
            "THE THIRD AND LAST hypothesis of the MARKET PHENOMENON DISCOVERY "
            "phase. All three families -- H-STRUCTURE-2, H-VOL-1, H-REL-1 -- failed "
            "Stage A. Per the protocol's final stop rule the research program stops "
            "here and produces a strategic diagnosis rather than a fourth family; "
            "no H-STRUCTURE-3, H-MOMENTUM-1, H-MICROSTRUCTURE-1 or H-ORDERFLOW-1 "
            "was proposed. Diagnosis in out/phase_discovery/strategic_diagnosis.md; "
            "full report out/hrel1/hrel1_final.md."),
    )


def main(path=REGISTRY_PATH) -> int:
    if any(r["experiment_id"] == EXPERIMENT_ID for r in load_all(path)):
        print(f"{EXPERIMENT_ID} is already recorded; append-only. Nothing written.")
        return 1
    print(f"recorded {EXPERIMENT_ID} to {record(build(), path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
