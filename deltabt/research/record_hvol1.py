"""Append H-VOL-1 to the experiment registry.

    PYTHONPATH=. python3 -m deltabt.research.record_hvol1

Numbers transcribed from out/hvol1/train_results.json. The phase verdict
INSUFFICIENT POWER maps onto the registry's INSUFFICIENT DATA, as H-STRUCTURE-2
did; nothing is added to Experiment.VALID.
"""

from __future__ import annotations

import json
import sys

from deltabt.research import hvol1 as v1
from deltabt.research.registry import REGISTRY_PATH, Experiment, load_all, record

EXPERIMENT_ID = "H-VOL-1"
A = json.loads((v1.OUT / "train_results.json").read_text())["V1-EXP"]
MAN = json.loads((v1.OUT / "manifest.json").read_text())
P = f"+{v1.PRIMARY_HORIZON_MIN}m"
_p = A["horizons"][P]["pooled"]


def build() -> Experiment:
    return Experiment(
        experiment_id=EXPERIMENT_ID,
        hypothesis=(
            "Volatility compression followed by expansion contains directional "
            "information. STAGE A ONLY: forward PRICE returns, no stop, no target, "
            "no R, no costs. One event family, V1-EXP: the first 15m close beyond "
            "the boundary of a valid compression zone, signed by the breakout "
            "direction."),
        strategy_family="volatility compression / expansion -- information test",
        symbols=list(v1.SYMBOLS),
        timeframe="15m compression and expansion, 1m measurement resolution",
        data_start="2025-01-01", data_end="2025-12-20 (TRAIN only)",
        entry_rules=(
            "Not a trading rule -- an event definition. The compression STATE is "
            "INHERITED UNCHANGED from H-Compress-1's frozen pre-registration and is "
            "not re-chosen: ATR(14)/close below the 20th percentile of the trailing "
            "960-bar window ENDING AT t-1, held for >=4 consecutive bars, with "
            "(zone_high - zone_low)/ATR <= 1.5. Implemented by importing "
            "hcompress._rolling_quantile_causal and hcompress._compression_zones. "
            "Choosing fresh thresholds after two related experiments had already "
            "failed would have been three new numbers no reader could verify were "
            "not picked to work. DROPPED from H-Compress-1, because they are "
            "execution and not state: the retest entry, the 3-bar order lifetime, "
            "the volume multiple and the body-size filter. Event: ok[t-1] and "
            "close[t] beyond zone_high[t-1] (+1) or zone_low[t-1] (-1), ONESHOT. "
            "Reference price is the OPEN of the first 1m bar at or after the 15m "
            "close."),
        exit_rules=(
            "None. Forward price return at six pre-declared horizons (+5m, +15m, "
            "+30m, +1h, +4h, +1d), primary +1h declared before TRAIN."),
        position_sizing="not applicable -- no position is taken at Stage A",
        cost_assumptions=(
            "None applied. Costs enter only as the interpretive benchmark: the "
            "production round trip is 15.8 bps."),
        parameters=dict(
            protocol_sha256=MAN["protocol_sha256"],
            preregistration_sha256=MAN["preregistration_sha256"],
            module_sha256=MAN["module_sha256"],
            inherited=MAN["inherited"],
            tf_min=v1.TF_MIN, atr_period=v1.ATR_PERIOD,
            pct_lookback=v1.PCT_LOOKBACK, percentile=v1.PERCENTILE,
            min_duration=v1.MIN_DURATION, range_max=v1.RANGE_MAX,
            horizons_min=list(v1.HORIZONS_MIN),
            primary_horizon_min=v1.PRIMARY_HORIZON_MIN,
            inference_primary="cluster",
            cluster_unit="calendar UTC day, pooled across symbols",
            mde_k=v1.MDE_K, control_seed=v1.CONTROL_SEED,
            single_arm="ONE arm, ONE family. No threshold, window or duration swept."),
        trades=None, effective_n=float(_p["n_clusters"]),
        gross_expectancy_r=None, net_expectancy_r=None,
        t_stat=_p["t"], ci_low=_p["ci_low"], ci_high=_p["ci_high"],
        baseline_result=dict(
            control=("timestamp-matched within-symbol direction permutation, 1000 "
                     "permutations, seed 20260818"),
            observed=_p["effect"], control_mean=A["control"]["mean"],
            p_value=A["control"]["p_value"],
            interpretation="the observed effect sits inside its own permutation null"),
        out_of_sample={
            "train": dict(
                n=_p["n"], n_day_clusters=_p["n_clusters"],
                long=A["n_long"], short=A["n_short"],
                effect_at_1h=_p["effect"], mde_at_1h=_p["mde"],
                effect_over_mde=_p["effect"] / _p["mde"], cluster_t=_p["t"],
                by_horizon={k: v["pooled"]["effect"] for k, v in A["horizons"].items()},
                by_horizon_t={k: v["pooled"]["t"] for k, v in A["horizons"].items()},
                halves={k: v["effect"] for k, v in A["halves"].items()},
                per_symbol={k: v["effect"] for k, v in A["per_symbol"].items()},
                gate={k: v["passed"] for k, v in A["gate"].items() if k.startswith("A")},
                verdict=A["gate"]["verdict"]),
            "valid": ("NOT COMPUTED -- Stage-A gate A1-A5 not passed on TRAIN, and "
                      "VALID is run once and only after it passes"),
            "test": "LOCKED - not computed",
        },
        robustness=dict(
            event_census=MAN["census"],
            mde_vs_cost_floor=dict(
                round_trip_cost_bps=15.8, mde_at_1h_bps=10_000 * _p["mde"],
                ratio=15.8 / (10_000 * _p["mde"]),
                note=("the margin is about 2x here against 3.2x for H-STRUCTURE-2, "
                      "because a 20th-percentile squeeze held four bars fires only "
                      "~80 times per symbol per year. The MDE is still below the cost "
                      "floor, so an economically tradable effect would have been "
                      "detected -- but this is the tightest power margin in the phase "
                      "and is reported as such rather than buried")),
            reported_not_pursued=(
                "The effect is negative at every horizon from +15m to +4h, both TRAIN "
                "halves agree in sign, and 3 of 4 symbols agree -- loosely resembling "
                "mean reversion after expansion, the OPPOSITE of the hypothesis. It is "
                "not a finding: 0.45x the MDE at the primary horizon, permutation "
                "p = 0.164, and a largest |t| of 1.80 at +30m where six pre-declared "
                "horizons require 2.64 under Bonferroni. Flipping a pre-registered "
                "hypothesis after seeing it fail in the stated direction is exactly "
                "what the anti-loop rule forbids, so it was NOT pursued. Recorded so "
                "the observation is on file rather than quietly discarded."),
            anti_lookahead=("the percentile window ends at t-1 and excludes t; zone "
                            "extremes use bars up to t-1; only close[t] is read at t; "
                            "the event is knowable at the 15m close and not before. "
                            "Horizons located by TIMESTAMP; events whose horizon "
                            "crosses a split boundary are excluded, which is what "
                            "keeps VALID from reading a TEST price."),
        ),
        classification="INSUFFICIENT DATA",
        reason=(
            "Phase verdict INSUFFICIENT POWER, mapped onto the registry's "
            "INSUFFICIENT DATA. At the pre-declared +1h horizon the effect is "
            "-3.74 bps against an MDE of 8.30 bps (ratio -0.45, cluster t -1.26) on "
            "317 events in 160 day clusters. A2 fails, so the pre-declared tree "
            "returns INSUFFICIENT POWER and never NO EDGE. A3 also fails "
            "(permutation p = 0.164); A4 and A5 pass, but on an effect the sample "
            "cannot resolve. The MDE remains below the 15.8 bps round-trip cost "
            "floor by about 2x, so an economically tradable effect would still have "
            "been detected -- this is the tightest power margin in the phase and the "
            "squeeze definition is intrinsically rare, firing ~80 times per symbol "
            "per year."),
        notes=(
            "Hypothesis 2 of the 3 available in the MARKET PHENOMENON DISCOVERY "
            "phase. Stage B was not constructed. Per the failure stop rule no "
            "percentile, window, duration, volume filter or timeframe was adjusted. "
            "H-Compress-1, H-Compress-1-rev2 and H-VOL-1 now point the same way for "
            "different reasons -- the first two measured gross R under a "
            "passive-limit retest entry on 169 and 227 trades, this one measured "
            "price directly on 317 events with every execution parameter removed. "
            "The volatility-compression family is closed. Full report "
            "out/hvol1/hvol1_final.md; remaining budget H-REL-1."),
    )


def main(path=REGISTRY_PATH) -> int:
    if any(r["experiment_id"] == EXPERIMENT_ID for r in load_all(path)):
        print(f"{EXPERIMENT_ID} is already recorded; append-only. Nothing written.")
        return 1
    print(f"recorded {EXPERIMENT_ID} to {record(build(), path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
