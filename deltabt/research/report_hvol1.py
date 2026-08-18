"""Human-readable journal + mandatory TRAIN/VALID table for H-VOL-1.

    PYTHONPATH=. python3 -m deltabt.research.report_hvol1

Numbers transcribed from out/hvol1/train_results.json, not recomputed.
"""

from __future__ import annotations

import json
import sys

from deltabt.research import hvol1 as v1

A = json.loads((v1.OUT / "train_results.json").read_text())["V1-EXP"]
P = f"+{v1.PRIMARY_HORIZON_MIN}m"
ROUND_TRIP_COST = 2 * (0.0005 * 1.18 + 0.0002)


def bps(x):
    return f"{10_000 * x:+.2f} bps"


def main() -> int:
    p = A["horizons"][P]["pooled"]
    g = A["gate"]
    pos = sum(1 for v in A["per_symbol"].values() if v["effect"] > 0)
    L = []
    w = L.append

    w("# H-VOL-1 — FINAL REPORT")
    w("")
    w("Hypothesis 2 of 3 in the MARKET PHENOMENON DISCOVERY phase.")
    w("")
    w("> Volatility compression followed by expansion contains directional")
    w("> information.")
    w("")
    w("Pre-registration `out/hvol1/hvol1_preregistration.md`, sha256")
    w("`624d0b2848bbc58555f347d4f1e33027dbc46f9590846b7cfad6f3be851b36a5`.")
    w("Manifest `out/hvol1/manifest.json`.")
    w("")
    w("---")
    w("")
    w("## Journal")
    w("")
    w("```")
    w("V1-EXP   (EXP_UP + EXP_DOWN)")
    w("")
    w(f"    Events:            {p['n']:>10,}")
    w(f"    Long:              {A['n_long']:>10,}")
    w(f"    Short:             {A['n_short']:>10,}")
    w(f"    Day clusters:      {p['n_clusters']:>10,}")
    w("")
    w("    TRAIN  (effect = mean signed forward return)")
    w("    " + "-" * 42)
    for hzn in v1.HORIZONS_MIN:
        q = A["horizons"][f"+{hzn}m"]["pooled"]
        star = "   <-- PRIMARY" if hzn == v1.PRIMARY_HORIZON_MIN else ""
        w(f"    {'+' + str(hzn) + 'm':<8} {100 * q['effect']:>+10.4f}%"
          f"   t {q['t']:>+6.2f}{star}")
    w("")
    w(f"    MDE:               {100 * p['mde']:>+10.4f}%")
    w(f"    Effect/MDE:        {p['effect'] / p['mde']:>10.2f}x")
    w(f"    Control:           {100 * A['control']['mean']:>+10.4f}%"
      f"   p = {A['control']['p_value']:.4f}")
    w(f"    Symbols positive:  {pos:>10}/4")
    w(f"    Agreeing (A5):     {g['A5_cross_sectional']['agreeing']:>10}/4")
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
    w(f"| Symbols positive | {pos}/4 | not computed |")
    w(f"| Symbols agreeing with pooled sign (A5) | "
      f"{g['A5_cross_sectional']['agreeing']}/4 | not computed |")
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
    w("| Gate | V1-EXP |")
    w("|---|---|")
    for k, label in (("A1_train_effect", "A1 effect nonzero"),
                     ("A2_power", "A2 effect >= MDE"),
                     ("A3_control", "A3 exceeds control"),
                     ("A4_temporal", "A4 same sign H1/H2"),
                     ("A5_cross_sectional", "A5 >=3/4 symbols agree")):
        w(f"| {label} | {'PASS' if g[k]['passed'] else '**FAIL**'} |")
    w("")
    w("---")
    w("")
    w("## Power is the binding constraint, and it is much tighter than H-STRUCTURE-2")
    w("")
    w("This must be stated plainly rather than buried, because the margin here is")
    w("genuinely narrow:")
    w("")
    w("| | H-STRUCTURE-2 | H-VOL-1 |")
    w("|---|---:|---:|")
    w("| TRAIN events at +1h | 3,811 | 317 |")
    w("| day clusters | 353 | 160 |")
    w(f"| MDE at +1h | 4.98 bps | {10_000 * p['mde']:.2f} bps |")
    w(f"| round-trip cost floor | 15.8 bps | 15.8 bps |")
    w(f"| cost floor / MDE | 3.2x | {ROUND_TRIP_COST / p['mde']:.1f}x |")
    w("")
    w("The squeeze definition is intrinsically rare — a 20th-percentile ATR state")
    w("held for at least four consecutive bars fires roughly 80 times per symbol per")
    w("year. The conclusion still holds, but with less room: **the MDE remains below")
    w("the cost floor, so an effect large enough to trade would still have been")
    w("detected** — by a factor of about two rather than three.")
    w("")
    w(f"The observed effect is {bps(p['effect'])} against an MDE of "
      f"{bps(p['mde'])}, and the permutation control gives")
    w(f"p = {A['control']['p_value']:.3f}. Nothing here is distinguishable from its own")
    w("null.")
    w("")
    w("---")
    w("")
    w("## One observation that is reported but NOT pursued")
    w("")
    w("The effect is negative at every horizon from +15m to +4h, the two halves of")
    w("TRAIN agree in sign, and 3 of 4 symbols agree. Read loosely that resembles")
    w("mean reversion after expansion — the opposite of the hypothesis.")
    w("")
    w("It is not a finding, for three reasons:")
    w("")
    w(f"1. At the primary horizon it is {abs(p['effect'] / p['mde']):.2f}x the MDE. The")
    w("   sample cannot distinguish it from zero.")
    w(f"2. The permutation control gives p = {A['control']['p_value']:.3f}. It is inside")
    w("   its own null distribution.")
    w("3. The largest |t| at any horizon is 1.80, at +30m. Six pre-declared horizons")
    w("   require |t| > 2.64 under Bonferroni. It does not survive even before")
    w("   correction.")
    w("")
    w("Turning this into a reversal hypothesis would mean flipping the direction of")
    w("a pre-registered hypothesis after seeing that it failed in the stated")
    w("direction. That is precisely the move the phase protocol's anti-loop rule")
    w("exists to prevent, and it is not made. It is recorded here so that the")
    w("observation is on file rather than quietly discarded.")
    w("")
    w("---")
    w("")
    w("## Relationship to H-Compress-1 and H-Compress-1-rev2")
    w("")
    w("Both returned NO SIGNAL, measuring gross R under a passive-limit retest entry")
    w("with volume and body-size confirmation, on 169 and 227 trades. H-VOL-1 kept")
    w("their frozen compression *state* — deliberately, so that no threshold could")
    w("be accused of having been picked today — dropped every execution parameter,")
    w("and measured price directly on 317 events.")
    w("")
    w("Three experiments now point the same way for different reasons. The")
    w("volatility-compression family is closed.")
    w("")
    w("---")
    w("")
    w("## FINAL VERDICT")
    w("")
    w("    H-VOL-1:  INSUFFICIENT POWER")
    w("")
    w("Stage B was not constructed. Per the failure stop rule no percentile, window,")
    w("duration, volume filter or timeframe is adjusted — each would be a new")
    w("hypothesis.")
    w("")
    w("Remaining budget: **H-REL-1**.")

    out = v1.OUT / "hvol1_final.md"
    out.write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
