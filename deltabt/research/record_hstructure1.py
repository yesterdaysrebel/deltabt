"""Append the H-Structure-1 result to the experiment registry.

    PYTHONPATH=. python3 -m deltabt.research.record_hstructure1

Every number here was measured by the run in out/hstructure/ and is transcribed,
not recomputed: the registry is a record of what a run produced, and a record
that recomputes can drift away from the report it claims to summarise.

The headline figures are the frozen PRIMARY candidate, `C|N8|60m|oneshot`, on
train+validation combined, exactly as H-WPR-1's record used its Arm A. The grid
result sits in `robustness`.

VERDICT VOCABULARY
    The H-Structure-1 report classifies the family DEAD, which is the protocol's
    word. The registry's vocabulary is older and narrower and has no DEAD; the
    equivalent it does define is NO SIGNAL, which is what the evidence supports
    -- the family does not exceed a no-signal baseline. Nothing is added to
    `Experiment.VALID` to accommodate a synonym.
"""

from __future__ import annotations

import sys

from deltabt.research.registry import REGISTRY_PATH, Experiment, load_all, record

EXPERIMENT_ID = "H-Structure-1"


def build() -> Experiment:
    return Experiment(
        experiment_id=EXPERIMENT_ID,
        hypothesis=(
            "A confirmed transition into higher-high / higher-low structure "
            "predicts upside continuation, and the mirror into lower-high / "
            "lower-low structure predicts downside continuation, strongly "
            "enough to produce positive GROSS expectancy before costs. Four "
            "families were pre-declared and run separately: A bull structure "
            "(confirmed HH + HL), B bear structure (confirmed LL + LH), C break "
            "of structure, D structure flip. No other indicator was combined in."
        ),
        strategy_family="market structure HH/HL/LH/LL swing transitions",
        symbols=["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"],
        timeframe="5m / 15m / 1h structure, 1m fill resolution",
        data_start="2025-01-01",
        data_end="2026-04-16",
        entry_rules=(
            "Fractal swings with strict inequality on both sides: swing high at "
            "bar k iff high[k] > high[j] for all j in [k-N, k+N], j != k; swing "
            "low mirrors. N in {2,3,5,8}, pre-declared. A swing is CONFIRMED "
            "only at the close of bar k+N, so confirmation delay is exactly N "
            "structure bars by construction. A confirmed high is HH above the "
            "previous confirmed high else LH; a confirmed low is HL above the "
            "previous confirmed low else LL. Order placed at the open of the "
            "first execution bar at or after the structure bar's close; never "
            "backdated to the swing bar. Both one-shot (FALSE->TRUE) and level "
            "trigger semantics tested. 96 primary candidates plus 64 "
            "multi-timeframe candidates."
        ),
        exit_rules=(
            "Structural stop = last CONFIRMED swing low (long) / swing high "
            "(short) at the signal bar; target 2R; inherited 5% max stop "
            "distance; same-bar stop+target resolves to STOP; stop triggered on "
            "MARK price. No trailing, no time stop, no structure exit -- "
            "deliberately excluded so the entry is the only variable."
        ),
        position_sizing=(
            "Constant unit risk (equity 1e18 at 1e-9 risk) so the compounding "
            "equity path cannot starve a losing arm's sample; every reported "
            "figure is per unit of risk and exactly invariant to contract count"
        ),
        cost_assumptions=(
            "Production model unchanged: Delta India per-symbol taker x1.18 GST "
            "+ 2.0 bps slippage, per-symbol funding cadence charged at snapshot "
            "crossings"
        ),
        parameters={
            "families": {
                "A": "bull structure: confirmed HH + confirmed HL",
                "B": "bear structure: confirmed LL + confirmed LH",
                "C": "break of structure after an HL / LH",
                "D": "structure flip: bear -> confirmed HL -> break of the standing LH",
            },
            "swing_n": [2, 3, 5, 8],
            "structure_tf_minutes": [5, 15, 60],
            "mtf_combinations": [[5, 15], [15, 5]],
            "triggers": ["oneshot", "level"],
            "target_r": 2.0,
            "max_stop_pct": 0.05,
            "primary_candidate": "C|N8|60m|oneshot",
            "candidate_freeze_rule": (
                "eligible = trades>=200 and effective_n>=30; PRIMARY = max TRAIN "
                "gross_r; frozen to out/hstructure/frozen_candidates.json before "
                "validation was run"
            ),
            "primary_grid_arms": 96,
            "mtf_grid_arms": 64,
        },
        trades=363,
        effective_n=300.3,
        gross_expectancy_r=0.1322,
        net_expectancy_r=0.0656,
        ci_low=-0.0707,
        ci_high=0.1927,
        t_stat=1.716,
        sharpe=None,
        max_drawdown=19.7,
        baseline_result={
            "null_1_random_direction_same_entry_times": {
                "note": "100 sims, coin-flip direction at the candidate's own "
                        "entries, identical stop/target/cost path",
                "valid_signal_vs_null_gross": {
                    "A": {"signal": -0.0000, "null": 0.0570, "null_sd": 0.0560},
                    "B": {"signal": 0.0619, "null": 0.0840, "null_sd": 0.0469},
                    "C": {"signal": 0.0507, "null": 0.0543, "null_sd": 0.0517},
                    "D": {"signal": 0.0534, "null": 0.0190, "null_sd": 0.0634},
                },
            },
            "null_2_random_times_and_direction": {
                "valid_gross_by_family": {"A": 0.0455, "B": 0.0318,
                                          "C": 0.0448, "D": 0.0375},
            },
            "null_3_unconditional_always_on": {
                "valid_gross_long_15m": 0.0551,
                "valid_gross_short_15m": 0.0562,
            },
            "interpretation": (
                "Three of four families underperform a coin flip placed at their "
                "own entry times on validation, and an always-on baseline earns "
                "as much gross R as the signals. The families do not exceed a "
                "no-signal baseline."
            ),
        },
        out_of_sample={
            "train": {"n": 257, "gross_r": 0.1907, "net_r": 0.1256, "t_gross": 2.224},
            "valid": {"n": 106, "gross_r": -0.0094, "net_r": -0.0800, "t_gross": -0.066},
            "test": "LOCKED - not computed",
        },
        robustness={
            "per_symbol_gross_r": {"BTCUSD": 0.1622, "ETHUSD": -0.0455,
                                   "SOLUSD": 0.0946, "XRPUSD": 0.3000},
            "per_symbol_gross_r_validation_only": {"BTCUSD": -0.2727, "ETHUSD": -0.0769,
                                                   "SOLUSD": 0.1429, "XRPUSD": 0.2692},
            "symbols_with_positive_gross_on_validation": "2/4",
            "grid_96_arms_positive_gross": {"train": "86/96", "valid": "56/96"},
            "grid_96_arms_positive_net_on_validation": "6/96",
            "gross_sign_preserved_train_to_valid": "58/96 (60%)",
            "cross_split_correlation_of_gross_r": 0.396,
            "median_gross_degradation_valid_minus_train": -0.0258,
            "p_hit_2r_across_96_valid_arms": {"min": 0.271, "median": 0.336,
                                              "max": 0.406, "break_even": 0.3333},
            "supplementary_symbol_BEATUSD": (
                "listed 2026-01-05, validation only, no train data so it could "
                "not influence the freeze: negative on 7 of 8 frozen candidates"
            ),
            "excluded_symbols": {
                "AKEUSD": "listed 2026-07-22, entire history inside the locked TEST window",
                "BANKUSD": "listed 2026-07-22, entire history inside the locked TEST window",
            },
            "lookahead_audit": (
                "PASS - structure state reproduced exactly at 48 truncation "
                "points; pipeline entries identical from truncated data; "
                "entry_time >= swing confirmation instant on all 36,732 trades, "
                "zero violations"
            ),
        },
        classification="NO SIGNAL",
        reason=(
            "The frozen PRIMARY candidate collapses from +0.1907R gross on train "
            "to -0.0094R on validation, t 2.22 -> -0.07. Because both exits are "
            "fixed R-multiples, gross expectancy is mechanically 3*P(2R) - 1, and "
            "P(2R) across the 96 validation arms has median 0.336 against a "
            "break-even of 0.3333 -- the family sits on the break-even line. "
            "Three of four families underperform a random-direction null placed "
            "at their own entry times, and an unconditional always-on baseline "
            "earns the same gross R. Per-symbol gross flips sign on every frozen "
            "candidate. The report classifies this DEAD; NO SIGNAL is the "
            "registry's equivalent verdict."
        ),
        notes=(
            "Cost is the second, independent failure. The structural stop's "
            "distance is uncontrolled across three orders of magnitude (0.008% "
            "to 5% of entry), so mean cost/R is dominated by the tiny-stop tail: "
            "0.05R at 1h against 2.7R for 5m level triggers, and only 6 of 96 "
            "validation arms are net positive. Two secondary results worth "
            "keeping: the 15m->5m multi-timeframe combination is arithmetically "
            "degenerate because a 15m close is always a 5m boundary (verified "
            "identical on all 32 candidate pairs), and the ATR-normalised "
            "displacement effect that looks monotone on validation is flat on "
            "train, so it is noise. Full report, code and per-trade events are "
            "in out/hstructure/. Test segment remains locked and uncomputed."
        ),
    )


def main(path=REGISTRY_PATH) -> int:
    """``path`` is a parameter so tests can exercise this without writing to the
    real registry -- ``REGISTRY_PATH`` is bound at import, so an env var set
    inside a test is already too late."""
    if any(r["experiment_id"] == EXPERIMENT_ID for r in load_all(path)):
        print(f"{EXPERIMENT_ID} is already recorded; the registry is append-only "
              f"and this would duplicate it. Nothing written.")
        return 1
    written = record(build(), path)
    print(f"recorded {EXPERIMENT_ID} to {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
