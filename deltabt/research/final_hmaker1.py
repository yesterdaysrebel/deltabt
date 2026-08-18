"""H-MAKER-1 final verdict. Exactly one of PASS / FAIL / INCONCLUSIVE.

    PYTHONPATH=. python3 -m deltabt.research.final_hmaker1

The verdict is computed by the frozen rule in hmaker1.verdict and transcribed.
This module chooses nothing.
"""

from __future__ import annotations

import json
import sys

from deltabt.research import hmaker1 as h

R = json.loads((h.OUT / "results.json").read_text())
P = f"+{h.PRIMARY_MARKOUT_MIN}m"
KILL = h.KILL_THRESHOLD_BPS

HEAD = {
    "PASS": ("PATH B — PASS",
             "Passive execution is sufficiently measurable and economically viable."),
    "FAIL": ("PATH B — FAIL",
             "Passive execution does not overcome the "
             f"{KILL} bps adverse-selection threshold."),
    "INCONCLUSIVE": ("PATH B — INCONCLUSIVE",
                     "The available execution data cannot establish the economics "
                     "reliably."),
}


def main() -> int:
    v = R["verdicts"]["final"]
    c = R["results"]["conservative"]
    o = R["results"]["optimistic"]
    ac, ao = c["adverse"][P], o["adverse"][P]
    cs, os_ = c["stats"], o["stats"]
    t = R["targets"]
    L = []
    w = L.append

    w("# H-MAKER-1 — FINAL VERDICT")
    w("")
    w("> Can passive execution produce a sufficiently low and measurable trading")
    w("> cost?")
    w("")
    w(f"Pre-registration sha256 `{R['preregistration_sha256']}`, frozen before any")
    w("order was simulated. Kill threshold "
      f"**{KILL} bps**, primary horizon **+{h.PRIMARY_MARKOUT_MIN}m**, both frozen.")
    w("")
    w("---")
    w("")
    w("## What was measured")
    w("")
    w("| | conservative | optimistic |")
    w("|---|---:|---:|")
    w(f"| resting orders | {cs['orders']:,} | {os_['orders']:,} |")
    w(f"| touch rate | {cs['touch_rate']:.1%} | {os_['touch_rate']:.1%} |")
    w(f"| **simulated fill rate** | **{cs['fill_rate']:.1%}** | **{os_['fill_rate']:.1%}** |")
    w(f"| fills | {cs['fills']:,} | {os_['fills']:,} |")
    w(f"| partial-fill rate | {cs['partial_rate']:.1%} | {os_['partial_rate']:.1%} |")
    w(f"| median time to fill | {cs['median_time_to_fill_s']:.1f}s | "
      f"{os_['median_time_to_fill_s']:.1f}s |")
    w(f"| adverse selection @ +{h.PRIMARY_MARKOUT_MIN}m | {ac['mean']:+.3f} bps | "
      f"{ao['mean']:+.3f} bps |")
    w(f"| 95% CI | [{ac['ci_low']:+.3f}, {ac['ci_high']:+.3f}] | "
      f"[{ao['ci_low']:+.3f}, {ao['ci_high']:+.3f}] |")
    w(f"| clusters | {ac['n_clusters']:,} | {ao['n_clusters']:,} |")
    w(f"| MDE | {ac['mde']:.3f} bps | {ao['mde']:.3f} bps |")
    w("")
    w("## The frozen decision")
    w("")
    w(f"    sample targets   orders {cs['orders']:,}/{t['orders']:,}  "
      f"fills {cs['fills']:,}/{t['fills']:,}  -> "
      f"{'MET' if t['met'] else 'NOT MET'}")
    w(f"    conservative     {R['verdicts']['conservative']}")
    w(f"    optimistic       {R['verdicts']['optimistic']}")
    w(f"    bounds agree     {R['verdicts']['conservative'] == R['verdicts']['optimistic']}")
    w("")
    w("## The one number this experiment existed to correct")
    w("")
    gap = cs["touch_rate"] - cs["fill_rate"]
    w(f"    touch rate              {cs['touch_rate']:>6.1%}")
    w(f"    simulated fill rate     {cs['fill_rate']:>6.1%}  (conservative)")
    w(f"                            {os_['fill_rate']:>6.1%}  (optimistic)")
    w(f"    gap                     {gap:>6.1%}")
    w("")
    w("The feasibility phase could only see the touch rate from OHLC, said so, and")
    w("declined to lean on it. That caution was warranted: a touch is not a fill,")
    w("and the difference is not a rounding detail.")
    w("")
    w("---")
    w("")
    title, sub = HEAD[v]
    w(f"## {title}")
    w("")
    w(sub)
    w("")

    if v == "PASS":
        w("### Evidence now established")
        w("")
        w(f"- Adverse selection at +{h.PRIMARY_MARKOUT_MIN}m is "
          f"{ac['mean']:+.3f} bps with a 95% CI upper bound of {ac['ci_high']:+.3f} bps,")
        w(f"  under the CONSERVATIVE queue model, against a threshold of {KILL} bps.")
        w("- Both queue bounds imply the same verdict, so the conclusion does not")
        w("  depend on the unresolvable cancellation attribution.")
        w(f"- {cs['fills']:,} fills across {ac['n_clusters']:,} clusters, cluster-primary")
        w("  inference as ratified in H-NULL-1.")
        w("")
        w("### What has NOT been established")
        w("")
        w("- **No actual fill rate.** No real order was placed. Everything here is a")
        w("  reconstruction from public data, bounded rather than exact.")
        w("- **No signal.** This experiment says nothing about whether any")
        w("  predictive edge exists. It says only that the cost floor can be lowered.")
        w("- **No strategy.** Nothing was optimised, and no entry, exit, stop or")
        w("  target was chosen.")
        w("")
        w("### Next step")
        w("")
        w("**Strategy research is NOT reopened by this result.** Per the governing")
        w("instruction, this report stops here and waits for explicit operator")
        w("authorisation before any market research resumes.")
    elif v == "FAIL":
        w("### What follows")
        w("")
        w(f"Adverse selection is at or above the frozen {KILL} bps threshold, so the")
        w("11.08 bps maker saving is erased and Path B does not lower the effective")
        w("cost floor.")
        w("")
        w("All three feasibility paths are now closed: A was not measurable, C was")
        w("dead on the cost identity, and B fails here.")
        w("")
        w("**The Delta directional-trading research program stops.** No fourth")
        w("strategy family is proposed. No H-MAKER-2.")
    else:
        w("### The precise measurement limitation")
        w("")
        reasons = []
        if not t["met"]:
            reasons.append(
                f"- **Sample targets not met.** {cs['orders']:,} orders against a "
                f"frozen target of {t['orders']:,}, and {cs['fills']:,} fills against "
                f"{t['fills']:,}. The targets were frozen before collection and are "
                f"not lowered now to manufacture a verdict.")
        if R["verdicts"]["conservative"] != R["verdicts"]["optimistic"]:
            reasons.append(
                "- **The two queue bounds imply different verdicts.** Cancellation "
                "attribution is unresolvable from this feed, so the answer depends "
                "on an assumption the data cannot settle.")
        if (ac["ci_low"] < KILL <= ac["ci_high"]):
            reasons.append(
                f"- **The confidence interval straddles the threshold.** "
                f"[{ac['ci_low']:+.3f}, {ac['ci_high']:+.3f}] contains {KILL}.")
        for r in reasons:
            w(r)
        w("")
        w("### What is NOT concluded")
        w("")
        w("INCONCLUSIVE is not converted to PASS by adopting the optimistic bound,")
        w("and not converted to FAIL because the result is inconvenient. Both were")
        w("forbidden in advance.")
        w("")
        w("### No rescue cycle")
        w("")
        w("Per the governing instruction, the limitation is identified and **no")
        w("further research cycle is created to rescue the hypothesis.** No")
        w("H-MAKER-2, no alternative execution assumption, no relaxed threshold.")
        w("Any continuation requires explicit operator authorisation.")

    (h.OUT / "final_verdict.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[-40:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
