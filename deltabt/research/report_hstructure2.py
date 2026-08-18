"""Human-readable journal + mandatory TRAIN/VALID table for H-STRUCTURE-2.

    PYTHONPATH=. python3 -m deltabt.research.report_hstructure2

Every number is transcribed from out/hstructure2/train_results.json. Nothing is
recomputed here -- a report that recomputes can drift away from the run it
claims to describe.
"""

from __future__ import annotations

import json
import sys

from deltabt.research import hstructure2 as h2

R = json.loads((h2.OUT / "train_results.json").read_text())
PRIMARY = f"+{h2.PRIMARY_HORIZON_MIN}m"

#: production model: 2 x (taker 5 bps x 1.18 GST + 2.0 bps slippage)
ROUND_TRIP_COST = 2 * (0.0005 * 1.18 + 0.0002)


def bps(x):
    return "n/a" if x is None else f"{10_000 * x:+.2f} bps"


def main() -> int:
    L = []
    w = L.append
    w("# H-STRUCTURE-2 — FINAL REPORT")
    w("")
    w("Hypothesis 1 of 3 in the MARKET PHENOMENON DISCOVERY phase.")
    w("")
    w("> HH/HL and LH/LL structural transitions contain directional information")
    w("> about subsequent price movement.")
    w("")
    w("Pre-registration `out/hstructure2/hstructure2_preregistration.md`, sha256")
    w("`7338dddb3159fc0a1443ac8f12ab6cf0c366b42be2d6eb670d4749bd7b41689d`, frozen")
    w("before any event was counted. Manifest `out/hstructure2/manifest.json`.")
    w("")
    w("---")
    w("")
    w("## Journal")
    w("")
    w("```")
    for fam in h2.FAMILIES:
        a = R[fam]
        p = a["horizons"][PRIMARY]["pooled"]
        g = a["gate"]
        w(f"{fam}   ({' + '.join(h2.FAMILIES[fam])})")
        w("")
        w(f"    Events:            {p['n']:>10,}")
        w(f"    Long:              {a['n_long']:>10,}")
        w(f"    Short:             {a['n_short']:>10,}")
        w(f"    Day clusters:      {p['n_clusters']:>10,}")
        w("")
        w("    TRAIN  (effect = mean signed forward return)")
        w("    " + "-" * 42)
        for hzn in h2.HORIZONS_MIN:
            q = a["horizons"][f"+{hzn}m"]["pooled"]
            star = "   <-- PRIMARY" if hzn == h2.PRIMARY_HORIZON_MIN else ""
            w(f"    {'+' + str(hzn) + 'm':<8} {100 * q['effect']:>+10.4f}%"
              f"   t {q['t']:>+6.2f}{star}")
        w("")
        w(f"    MDE:               {100 * p['mde']:>+10.4f}%")
        w(f"    Effect/MDE:        {p['effect'] / p['mde']:>10.2f}x")
        w(f"    Control:           {100 * a['control']['mean']:>+10.4f}%"
          f"   p = {a['control']['p_value']:.4f}")
        pos = sum(1 for v in a["per_symbol"].values() if v["effect"] > 0)
        w(f"    Symbols positive:  {pos:>10}/4")
        w(f"    Agreeing (A5):     {g['A5_cross_sectional']['agreeing']:>10}/4")
        w("    Per symbol:        " + "  ".join(
            f"{k} {100 * v['effect']:+.4f}%" for k, v in a["per_symbol"].items()))
        w(f"    Halves:            H1 {100 * a['halves']['H1']['effect']:+.4f}%"
          f"   H2 {100 * a['halves']['H2']['effect']:+.4f}%")
        w("")
        w("    VALID              NOT COMPUTED — Stage-A gate not passed on TRAIN")
        w("")
        w(f"    Stage A:           {g['verdict']}")
        w("")
        w(f"    FINAL:             INSUFFICIENT POWER")
        w("")
    w("```")
    w("")
    w("---")
    w("")
    w("## Mandatory TRAIN / VALID table")
    w("")
    for fam in h2.FAMILIES:
        a = R[fam]
        p = a["horizons"][PRIMARY]["pooled"]
        g = a["gate"]
        w(f"### {fam}")
        w("")
        w("| Metric | TRAIN | VALID |")
        w("|---|---:|---:|")
        w(f"| Events | {p['n']:,} | not computed |")
        w(f"| Effect | {bps(p['effect'])} | not computed |")
        w(f"| MDE | {bps(p['mde'])} | not computed |")
        w(f"| Effect / MDE | {p['effect'] / p['mde']:.2f}x | not computed |")
        w(f"| Control | {bps(a['control']['mean'])} | not computed |")
        pos = sum(1 for v in a["per_symbol"].values() if v["effect"] > 0)
        w(f"| Symbols positive | {pos}/4 | not computed |")
        w(f"| Symbols agreeing with pooled sign (A5) | "
          f"{g['A5_cross_sectional']['agreeing']}/4 | not computed |")
        w("| Gross R | not tested | not tested |")
        w("| Cost/R | not tested | not tested |")
        w("| Net R | not tested | not tested |")
        w("")
        w(f"    INFORMATION:    INSUFFICIENT POWER")
        w(f"    REPLICATION:    NO  (VALID not run — TRAIN gate not passed)")
        w(f"    ECONOMIC:       NOT TESTED")
        w(f"    FINAL VERDICT:  INSUFFICIENT POWER")
        w("")
    w("---")
    w("")
    w("## Stage-A gate, item by item")
    w("")
    w("| Gate | S2-CONT | S2-FAIL |")
    w("|---|---|---|")
    for k, label in (("A1_train_effect", "A1 effect nonzero"),
                     ("A2_power", "A2 effect >= MDE"),
                     ("A3_control", "A3 exceeds control"),
                     ("A4_temporal", "A4 same sign H1/H2"),
                     ("A5_cross_sectional", "A5 >=3/4 symbols agree")):
        c = "PASS" if R["S2-CONT"]["gate"][k]["passed"] else "**FAIL**"
        f = "PASS" if R["S2-FAIL"]["gate"][k]["passed"] else "**FAIL**"
        w(f"| {label} | {c} | {f} |")
    w("")
    w("The verdict is INSUFFICIENT POWER rather than NO INFORMATION because the")
    w("pre-declared gate evaluates A2 before A3, and that ordering was frozen. It")
    w("is worth saying plainly that **A3 and A4 also failed**: the effects are")
    w("indistinguishable from their own permutation controls, and the two halves of")
    w("TRAIN disagree in sign. Under the control criterion alone the verdict would")
    w("read NO INFORMATION. Both point the same way; only the label differs.")
    w("")
    w("A6 (VALID) was never reached. VALID is run once and only after A1–A5 pass;")
    w("spending it on a hypothesis that already failed on TRAIN would consume the")
    w("out-of-sample segment for nothing.")
    w("")
    w("---")
    w("")
    w("## What INSUFFICIENT POWER does and does not mean here")
    w("")
    w("The formal verdict follows the pre-declared decision tree: A2 fails, so the")
    w("verdict is INSUFFICIENT POWER and never NO EDGE. That wording is required")
    w("because it is literally true — an effect smaller than the MDE cannot be")
    w("distinguished from zero by this sample.")
    w("")
    w("It would be a serious misreading to conclude *\"the effect may be real, we")
    w("just need more data.\"* The relevant comparison is not effect against zero,")
    w("it is **MDE against the cost floor**:")
    w("")
    w(f"    round-trip cost   2 x (5 bps taker x 1.18 GST + 2.0 bps slippage)")
    w(f"                      = {10_000 * ROUND_TRIP_COST:.1f} bps")
    w("")
    w("| | S2-CONT | S2-FAIL |")
    w("|---|---:|---:|")
    for key, fn in (("observed effect at +1h", lambda a: a["horizons"][PRIMARY]["pooled"]["effect"]),
                    ("MDE at +1h", lambda a: a["horizons"][PRIMARY]["pooled"]["mde"])):
        w(f"| {key} | {bps(fn(R['S2-CONT']))} | {bps(fn(R['S2-FAIL']))} |")
    c_mde = R["S2-CONT"]["horizons"][PRIMARY]["pooled"]["mde"]
    f_mde = R["S2-FAIL"]["horizons"][PRIMARY]["pooled"]["mde"]
    w(f"| cost floor / MDE | {ROUND_TRIP_COST / c_mde:.1f}x | {ROUND_TRIP_COST / f_mde:.1f}x |")
    w("")
    w("The MDE is roughly **3x smaller than the round-trip cost**. Any effect large")
    w("enough to survive execution costs would have been detected comfortably. The")
    w("experiment is underpowered only in the region where the phenomenon could")
    w("never have been traded anyway.")
    w("")
    w("So the honest statement is narrow and specific: **there is no structural")
    w("effect here of a size that could matter economically.** Whether some effect")
    w("of 1 bp exists is unresolved and uninteresting.")
    w("")
    w("---")
    w("")
    w("## Supporting observations")
    w("")
    w("- **The control confirms it independently.** The permutation control —")
    w("  which preserves symbol, timestamp and the exact direction imbalance, and")
    w(f"  randomizes only the direction assignment — gives p = "
      f"{R['S2-CONT']['control']['p_value']:.3f} for S2-CONT and "
      f"p = {R['S2-FAIL']['control']['p_value']:.3f} for S2-FAIL. The observed")
    w("  effects sit inside the middle of their own null distributions.")
    w("- **The two halves of TRAIN disagree in sign** for both families, which is")
    w("  what noise looks like and not what a phenomenon looks like.")
    w("- **No horizon rescues it.** All six pre-declared horizons are reported")
    w("  above; the largest |t| anywhere in the table is 1.17. Bonferroni x6 for")
    w("  six horizons would require |t| > 2.64, so no horizon comes close even")
    w("  before correction — there is nothing to select from.")
    w("- **The continuation effect at +1h is weakly negative** (t -0.73). If")
    w("  anything, continuation events are followed by very slightly adverse")
    w("  moves, but at a quarter of the MDE this is noise, not a reversal signal,")
    w("  and it is not pursued. Pursuing it would be a new hypothesis.")
    w("")
    w("---")
    w("")
    w("## Relationship to H-Structure-1")
    w("")
    w("H-Structure-1 returned NO SIGNAL for this family under a 2R target and a")
    w("structural stop. That was a joint test of information and trade geometry,")
    w("and H-COST-1 and H-NULL-1 later showed geometry can both hide a real effect")
    w("and manufacture a false one — so the joint test could not settle the")
    w("question. H-STRUCTURE-2 removed the geometry entirely and asked only")
    w("whether the events predict price.")
    w("")
    w("They do not, at any size that matters. The two experiments now agree for")
    w("independent reasons, and the family is closed.")
    w("")
    w("---")
    w("")
    w("## FINAL VERDICT")
    w("")
    w("    S2-CONT:  INSUFFICIENT POWER")
    w("    S2-FAIL:  INSUFFICIENT POWER")
    w("")
    w("    H-STRUCTURE-2:  INSUFFICIENT POWER")
    w("")
    w("Stage B was not constructed. Per protocol section 6, Stage-A survivors get")
    w("an executable strategy and non-survivors do not.")
    w("")
    w("Per the failure stop rule, this hypothesis stops here. No filter, threshold,")
    w("timeframe, swing strength, confirmation indicator or exit rule is added —")
    w("each of those would be a new hypothesis, and the phase budget is three.")
    w("")
    w("Remaining budget: **H-VOL-1**, then **H-REL-1**.")

    out = h2.OUT / "hstructure2_final.md"
    out.write_text("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\nwritten -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
