"""Human-readable journal + mandatory TRAIN/VALID table for H-REL-1."""

from __future__ import annotations

import json
import sys

from deltabt.research import hrel1 as r1

A = json.loads((r1.OUT / "train_results.json").read_text())["R1-LAG"]
P = f"+{r1.PRIMARY_HORIZON_MIN}m"
ROUND_TRIP_COST = 2 * (0.0005 * 1.18 + 0.0002)


def bps(x):
    return f"{10_000 * x:+.2f} bps"


def main() -> int:
    p = A["horizons"][P]["pooled"]
    g = A["gate"]
    pos = sum(1 for v in A["per_symbol"].values() if v["effect"] > 0)
    L = []
    w = L.append

    w("# H-REL-1 — FINAL REPORT")
    w("")
    w("Hypothesis 3 of 3 — the last in the MARKET PHENOMENON DISCOVERY phase.")
    w("")
    w("> Relative movements among BTC, ETH, SOL and XRP contain short-horizon")
    w("> predictive information.")
    w("")
    w("Selected formulation, frozen before TRAIN: **leader shock, follower")
    w("under-response**. BTC makes an unusually large 15m move; a follower does not")
    w("move as far in the same direction; does the follower close the gap?")
    w("")
    w("Pre-registration `out/hrel1/hrel1_preregistration.md`, sha256")
    w("`0711bb59aa7e07779080e6c618a16b1ece0aa206ecc8e23c65ca8ec8d10fd586`.")
    w("")
    w("---")
    w("")
    w("## Journal")
    w("")
    w("```")
    w("R1-LAG   (LAG_UP + LAG_DOWN)")
    w("")
    w(f"    BTC shocks:        {2916:>10,}   (5.2% of 15m bars)")
    w(f"    Events:            {p['n']:>10,}")
    w(f"    Long:              {A['n_long']:>10,}")
    w(f"    Short:             {A['n_short']:>10,}")
    w(f"    Day clusters:      {p['n_clusters']:>10,}")
    w("")
    w("    TRAIN  (effect = mean signed forward return)")
    w("    " + "-" * 42)
    for hzn in r1.HORIZONS_MIN:
        q = A["horizons"][f"+{hzn}m"]["pooled"]
        star = "   <-- PRIMARY" if hzn == r1.PRIMARY_HORIZON_MIN else ""
        w(f"    {'+' + str(hzn) + 'm':<8} {100 * q['effect']:>+10.4f}%"
          f"   t {q['t']:>+6.2f}{star}")
    w("")
    w(f"    MDE:               {100 * p['mde']:>+10.4f}%")
    w(f"    Effect/MDE:        {p['effect'] / p['mde']:>10.2f}x")
    w(f"    Control:           {100 * A['control']['mean']:>+10.4f}%"
      f"   p = {A['control']['p_value']:.4f}")
    w(f"    Symbols positive:  {pos:>10}/3")
    w(f"    Agreeing (A5):     {g['A5_cross_sectional']['agreeing']:>10}/3"
      f"   (required {r1.SYMBOLS_REQUIRED_A5})")
    w("    Per symbol:        " + "  ".join(
        f"{k} {100 * v['effect']:+.4f}%" for k, v in A["per_symbol"].items()))
    w(f"    Halves:            H1 {100 * A['halves']['H1']['effect']:+.4f}%"
      f"   H2 {100 * A['halves']['H2']['effect']:+.4f}%")
    w("")
    w("    VALID              NOT COMPUTED — Stage-A gate not passed on TRAIN")
    w("")
    w(f"    Stage A:           {g['verdict']}")
    w("")
    w("    FINAL:             INSUFFICIENT POWER")
    w("```")
    w("")
    w("---")
    w("")
    w("## Mandatory TRAIN / VALID table")
    w("")
    w("| Metric | TRAIN | VALID |")
    w("|---|---:|---:|")
    w(f"| Events | {p['n']:,} | not computed |")
    w(f"| Effect | {bps(p['effect'])} | not computed |")
    w(f"| MDE | {bps(p['mde'])} | not computed |")
    w(f"| Effect / MDE | {p['effect'] / p['mde']:.2f}x | not computed |")
    w(f"| Control | {bps(A['control']['mean'])} | not computed |")
    w(f"| Symbols positive | {pos}/3 | not computed |")
    w(f"| Symbols agreeing with pooled sign (A5) | "
      f"{g['A5_cross_sectional']['agreeing']}/3 | not computed |")
    w("| Gross R | not tested | not tested |")
    w("| Cost/R | not tested | not tested |")
    w("| Net R | not tested | not tested |")
    w("")
    w("    INFORMATION:    INSUFFICIENT POWER")
    w("    REPLICATION:    NO  (VALID not run — TRAIN gate not passed)")
    w("    ECONOMIC:       NOT TESTED")
    w("    FINAL VERDICT:  INSUFFICIENT POWER")
    w("")
    w("---")
    w("")
    w("## Stage-A gate")
    w("")
    w("| Gate | R1-LAG |")
    w("|---|---|")
    for k, label in (("A1_train_effect", "A1 effect nonzero"),
                     ("A2_power", "A2 effect >= MDE"),
                     ("A3_control", "A3 exceeds control"),
                     ("A4_temporal", "A4 same sign H1/H2"),
                     ("A5_cross_sectional", "A5 >=2/3 followers agree")):
        w(f"| {label} | {'PASS' if g[k]['passed'] else '**FAIL**'} |")
    w("")
    w("A4 and A5 pass. A2 and A3 do not, and they are the ones that matter: the")
    w("effect cannot be distinguished from zero, nor from its own control")
    w(f"(p = {A['control']['p_value']:.3f}). A consistent sign across halves and symbols is")
    w("not evidence when the quantity being signed is indistinguishable from noise.")
    w("")
    w("---")
    w("")
    w("## Power")
    w("")
    w("| | H-STRUCTURE-2 | H-VOL-1 | H-REL-1 |")
    w("|---|---:|---:|---:|")
    w("| TRAIN events at +1h | 3,811 | 317 | 1,527 |")
    w("| day clusters | 353 | 160 | 249 |")
    w(f"| MDE at +1h | 4.98 bps | 8.30 bps | {10_000 * p['mde']:.2f} bps |")
    w("| round-trip cost floor | 15.8 bps | 15.8 bps | 15.8 bps |")
    w(f"| cost floor / MDE | 3.2x | 1.9x | {ROUND_TRIP_COST / p['mde']:.1f}x |")
    w("")
    w("1,527 events but only 249 day clusters, because three followers reacting to")
    w("the same BTC shock at the same timestamp are close to one observation rather")
    w("than three. The day cluster is what prevents that from being counted as three")
    w("times more evidence than it is; the iid standard error here would have been")
    w(f"{10_000 * p['se_iid']:.2f} bps against the cluster's {10_000 * p['se']:.2f} bps, a")
    w(f"{p['se'] / p['se_iid']:.1f}x understatement of uncertainty.")
    w("")
    w("This is the tightest power margin of the three, but the MDE still sits below")
    w("the cost floor: an effect large enough to trade would have been detected.")
    w("")
    w("---")
    w("")
    w("## FINAL VERDICT")
    w("")
    w("    H-REL-1:  INSUFFICIENT POWER")
    w("")
    w("Stage B was not constructed. No percentile, leader, gap threshold or")
    w("timeframe was adjusted.")
    w("")
    w("**This was the third and last hypothesis in the phase.** All three families")
    w("have now failed Stage A. Per the protocol's final stop rule the research")
    w("program stops here and produces a strategic diagnosis rather than a fourth")
    w("family. See `out/phase_discovery/strategic_diagnosis.md`.")

    (r1.OUT / "hrel1_final.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[-30:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
