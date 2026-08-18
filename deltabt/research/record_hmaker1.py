"""Append H-MAKER-1 to the experiment registry.

    PYTHONPATH=. python3 -m deltabt.research.record_hmaker1

Numbers transcribed from out/hmaker1/results.json. Append-only; no historical
entry is modified.

VERDICT VOCABULARY
    H-MAKER-1 is an EXECUTION measurement, not a market hypothesis. It makes no
    directional claim about any instrument, so `classification` is None -- the
    same choice H-NULL-1 made, and for the same reason: every entry in
    Experiment.VALID describes a strategy outcome and would be a category error
    here. The PASS / FAIL / INCONCLUSIVE verdict lives in `reason`.
"""

from __future__ import annotations

import json
import sys

from deltabt.research import hmaker1 as h
from deltabt.research.registry import REGISTRY_PATH, Experiment, load_all, record

EXPERIMENT_ID = "H-MAKER-1"
R = json.loads((h.OUT / "results.json").read_text())
P = f"+{h.PRIMARY_MARKOUT_MIN}m"


def _mode(m: str) -> dict:
    s = R["results"][m]["stats"]
    a = R["results"][m]["adverse"][P]
    return dict(orders=s["orders"], fills=s["fills"], fill_rate=s["fill_rate"],
                touch_rate=s["touch_rate"], partial_rate=s["partial_rate"],
                median_time_to_fill_s=s["median_time_to_fill_s"],
                adverse_primary_bps=a["mean"], ci=[a["ci_low"], a["ci_high"]],
                n_fills=a["n"], n_clusters=a["n_clusters"], mde_bps=a["mde"],
                verdict=R["verdicts"][m])


def build() -> Experiment:
    v = R["verdicts"]["final"]
    c = _mode("conservative")
    o = _mode("optimistic")
    return Experiment(
        experiment_id=EXPERIMENT_ID,
        hypothesis=(
            "EXECUTION MEASUREMENT, not market prediction. Can passive maker "
            "execution on Delta India produce a sufficiently low and MEASURABLE "
            "trading cost to justify reopening strategy research? Two questions "
            "only: (Q1) can a realistic resting limit order actually fill, and "
            "(Q2) what is the economic adverse selection of those fills? "
            "Authorised by the feasibility phase, which found Path B the only "
            "surviving path and made it conditional on this measurement."),
        strategy_family="execution microstructure -- passive fill and adverse selection",
        symbols=list(h.SYMBOLS),
        timeframe=("live L2 order book at ~1 Hz and individual trade prints at "
                   "microsecond resolution"),
        data_start=str(R["feed"]["session"].get("started")),
        data_end="see collection_report.md",
        entry_rules=(
            "NO REAL ORDERS WERE PLACED. Paper orders simulated offline against a "
            "recorded feed. Submission policy frozen and SIGNAL-FREE: one order "
            f"per symbol every {h.SUBMIT_EVERY_S:.0f}s, side alternating by "
            "sequence position ALONE, limit at the current best bid (BUY) or best "
            f"ask (SELL) joining the back of the queue, size {h.ORDER_SIZE} "
            f"contract, lifetime {h.ORDER_LIFETIME_S:.0f}s. The rule consults no "
            "price, no volatility, no book state and no clock feature -- that is "
            "what keeps it an execution measurement rather than a strategy."),
        exit_rules=(
            "None. Adverse selection measured as signed markout against the MID at "
            "+1m (primary), +5m and +15m. Markout against the mid rather than our "
            "own fill price, because measuring against our price would credit us "
            "the half-spread that the 4.72 bps fee arithmetic already counts."),
        position_sizing="1 contract, fixed. Size effects are not studied.",
        cost_assumptions=(
            "taker 5.90 bps incl 1.18 GST, maker 2.36 bps, slippage 2.0 bps; "
            "taker round trip 15.80, maker both legs 4.72, saving 11.08"),
        parameters=dict(
            preregistration_sha256=R["preregistration_sha256"],
            kill_threshold_bps=R["kill_threshold_bps"],
            primary_markout_min=R["primary_markout_min"],
            markout_horizons_min=list(h.MARKOUT_MIN),
            submit_every_s=h.SUBMIT_EVERY_S, lifetime_s=h.ORDER_LIFETIME_S,
            cluster_unit=f"(symbol, {h.CLUSTER_BUCKET_S // 60}-minute bucket)",
            inference_primary="cluster", mde_k=h.MDE_K,
            targets=R["targets"], queue_bounds=list(h.MODES)),
        trades=c["fills"], effective_n=float(c["n_clusters"]),
        gross_expectancy_r=None, net_expectancy_r=None,
        t_stat=None, ci_low=c["ci"][0], ci_high=c["ci"][1],
        baseline_result=dict(
            touch_rate=c["touch_rate"],
            note=("the touch rate is reported ONLY to show how misleading it is. "
                  "The feasibility phase could see nothing else from OHLC, said "
                  "so, and declined to lean on it -- correctly."),
            simulated_fill_rate_bounds=[c["fill_rate"], o["fill_rate"]],
            actual_fill_rate=("NOT MEASURABLE -- no real order was placed. Not "
                              "approximated.")),
        out_of_sample={
            "conservative": c, "optimistic": o,
            "valid": "not applicable -- this is not a predictive experiment",
            "test": "LOCKED - not computed, and not touched by this experiment",
        },
        robustness=dict(
            feed_facts_measured_before_prereg=dict(
                l2_cadence="~1 Hz, 77 snapshots in 75 s",
                l2_coalesced=("sequence deltas median 3 max 4 -- 2-3 book updates "
                              "skipped between every snapshot received"),
                level_schema="aggregate size only, NO ORDER COUNT",
                trades="microsecond timestamps with buyer_role/seller_role, so the "
                       "aggressor side is known rather than inferred"),
            queue_position_limitation=(
                "EXACT QUEUE POSITION CANNOT BE RECONSTRUCTED from this feed, for "
                "three reasons recorded in the pre-registration BEFORE collection: "
                "no order count, so position is expressible in size ahead but never "
                "in orders ahead; coalescing, so event ordering inside a 1-second "
                "window is unobservable; and cancellations are not attributable to "
                "a queue position, since a net size fall mixes cancels ahead of us, "
                "cancels behind us and new joins behind us. The experiment therefore "
                "BOUNDS the fill rate between a conservative model (only trades "
                "consume our queue) and an optimistic one (every cancellation is "
                "ahead of us). NO MIDPOINT IS REPORTED, because the feed does not "
                "contain one."),
            feed_gaps=R["feed"]["gaps"],
            snapshots=R["feed"]["snapshots"], trade_prints=R["feed"]["trades"]),
        classification=None,
        reason=(
            f"{v}. " + {
                "PASS": "Adverse selection at the primary horizon sits below the "
                        "frozen 5.54 bps threshold under the conservative queue "
                        "bound, and both bounds imply the same verdict. Strategy "
                        "research is NOT reopened by this result; it awaits "
                        "explicit operator authorisation.",
                "FAIL": "Adverse selection is at or above the frozen 5.54 bps "
                        "threshold, erasing the 11.08 bps maker saving. All three "
                        "feasibility paths are now closed and the Delta "
                        "directional-trading research program stops.",
                "INCONCLUSIVE": "The available execution data cannot establish the "
                                "economics reliably. The precise limitation is "
                                "named in final_verdict.md. INCONCLUSIVE was NOT "
                                "converted to PASS by adopting the optimistic "
                                "bound, nor to FAIL because it is inconvenient -- "
                                "both were forbidden in advance.",
            }[v]
            + f" Conservative bound: fill rate {c['fill_rate']:.1%} on "
              f"{c['orders']:,} orders, adverse selection {c['adverse_primary_bps']:+.3f} bps "
              f"(95% CI [{c['ci'][0]:+.3f}, {c['ci'][1]:+.3f}], {c['n_fills']:,} fills, "
              f"{c['n_clusters']:,} clusters). Touch rate was {c['touch_rate']:.1%} -- "
              f"the gap between touch and fill is the whole reason this experiment ran."),
        notes=(
            "Authorised solely to test the feasibility phase's Path B condition. "
            "No indicator, no signal, no direction forecast, no entry/exit/stop/"
            "target optimisation, no parameter sweep, no VALID, no TEST. NO REAL "
            "ORDER WAS PLACED and no order-placement code exists anywhere in this "
            "repository; tests/live/test_no_live_trading.py enforces that against "
            "the shipped source and scans this experiment's modules too. Raw feed "
            "recorded to disk before any processing so the simulation can be "
            "re-run and audited without re-collecting. Reports in out/hmaker1/: "
            "preregistration.md, collection_report.md, fill_model.md, "
            "adverse_selection.md, statistical_report.md, final_verdict.md."),
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
