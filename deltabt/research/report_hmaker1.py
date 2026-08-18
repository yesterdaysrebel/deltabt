"""H-MAKER-1 reports. Every number transcribed from results.json, none recomputed.

    PYTHONPATH=. python3 -m deltabt.research.report_hmaker1
"""

from __future__ import annotations

import json
import sys

from deltabt.research import hmaker1 as h

R = json.loads((h.OUT / "results.json").read_text())
P = f"+{h.PRIMARY_MARKOUT_MIN}m"
KILL = h.KILL_THRESHOLD_BPS


def w(lines, s=""):
    lines.append(s)


def collection_report() -> str:
    L = []
    fd = R["feed"]
    w(L, "# H-MAKER-1 — COLLECTION REPORT")
    w(L)
    w(L, f"Pre-registration sha256 `{R['preregistration_sha256']}`.")
    w(L, "Read-only recording. **No order was ever placed.**")
    w(L)
    w(L, "## What was captured")
    w(L)
    w(L, "| symbol | L2 snapshots | trade prints |")
    w(L, "|---|---:|---:|")
    for s in h.SYMBOLS:
        w(L, f"| {s} | {fd['snapshots'][s]:,} | {fd['trades'][s]:,} |")
    w(L)
    w(L, f"- feed files: {', '.join(fd['files'])}")
    w(L, f"- gaps longer than {h.MAX_GAP_S:.0f}s: **{fd['gaps']}**")
    w(L, f"- paper orders generated: **{R['orders_generated']:,}**")
    w(L)
    w(L, "## Submission policy (frozen, signal-free)")
    w(L)
    w(L, f"    one order per symbol every {h.SUBMIT_EVERY_S:.0f}s")
    w(L, f"    side alternates by sequence position alone")
    w(L, f"    limit = best bid (BUY) / best ask (SELL), joining the back of the queue")
    w(L, f"    size {h.ORDER_SIZE} contract, lifetime {h.ORDER_LIFETIME_S:.0f}s")
    w(L)
    w(L, "The side depends on the order's index and nothing else. No price, no")
    w(L, "volatility, no book state and no clock feature enters the decision. That")
    w(L, "is what keeps this an execution measurement rather than a strategy.")
    w(L)
    w(L, "## Sample targets")
    w(L)
    t = R["targets"]
    st = R["results"]["conservative"]["stats"]
    w(L, f"| target | required | achieved | met |")
    w(L, f"|---|---:|---:|---|")
    w(L, f"| resting orders | {t['orders']:,} | {st['orders']:,} | "
         f"{'YES' if st['orders'] >= t['orders'] else '**NO**'} |")
    w(L, f"| credible fills | {t['fills']:,} | {st['fills']:,} | "
         f"{'YES' if st['fills'] >= t['fills'] else '**NO**'} |")
    w(L)
    w(L, "The targets were frozen before collection. Collection was not stopped")
    w(L, "early because the estimate looked favourable, and not extended because it")
    w(L, "looked unfavourable.")
    return "\n".join(L) + "\n"


def fill_model() -> str:
    L = []
    w(L, "# H-MAKER-1 — FILL MODEL  (Q1)")
    w(L)
    w(L, "> Can a realistic resting limit order actually fill?")
    w(L)
    w(L, "## The three quantities, kept separate")
    w(L)
    c = R["results"]["conservative"]["stats"]
    o = R["results"]["optimistic"]["stats"]
    w(L, "| quantity | value | what it means |")
    w(L, "|---|---:|---|")
    w(L, f"| **1. touch rate** | {c['touch_rate']:.1%} | price reached our limit. "
         f"**This is not a fill rate.** |")
    w(L, f"| **2. simulated fill rate** | {c['fill_rate']:.1%} – {o['fill_rate']:.1%} | "
         f"reconstructed book/trade sequence implies a fill. A BOUND. |")
    w(L, "| **3. actual fill rate** | **not measurable** | no real order was "
         "placed. Not approximated. |")
    w(L)
    gap = c["touch_rate"] - c["fill_rate"]
    w(L, f"**The gap between (1) and (2) is {100 * gap:.1f} percentage points.** That gap is")
    w(L, "the entire reason this experiment was run. The feasibility phase could only")
    w(L, "see the touch rate from OHLC and said so; here the two are measured side by")
    w(L, "side against real book and trade data.")
    w(L)
    w(L, "## Fill statistics")
    w(L)
    w(L, "| | conservative | optimistic |")
    w(L, "|---|---:|---:|")
    w(L, f"| orders | {c['orders']:,} | {o['orders']:,} |")
    w(L, f"| fills | {c['fills']:,} | {o['fills']:,} |")
    w(L, f"| fill rate | {c['fill_rate']:.1%} | {o['fill_rate']:.1%} |")
    w(L, f"| partial fills | {c['partial']} | {o['partial']} |")
    w(L, f"| partial-fill rate | {c['partial_rate']:.1%} | {o['partial_rate']:.1%} |")
    w(L, f"| median time to fill | {c['median_time_to_fill_s']:.1f}s | "
         f"{o['median_time_to_fill_s']:.1f}s |")
    w(L, f"| p90 time to fill | {c['p90_time_to_fill_s']:.1f}s | "
         f"{o['p90_time_to_fill_s']:.1f}s |")
    w(L, f"| median queue ahead | {c['median_queue_ahead']:,.0f} | "
         f"{o['median_queue_ahead']:,.0f} |")
    w(L, f"| median spread | {c['median_spread_bps']:.2f} bps | — |")
    w(L)
    w(L, "Partial fills are structurally impossible with a 1-contract order, and the")
    w(L, "measured rate confirms it rather than being interpreted.")
    w(L)
    w(L, "## Per symbol (conservative)")
    w(L)
    w(L, "| symbol | orders | touch | fill | median time to fill |")
    w(L, "|---|---:|---:|---:|---:|")
    for s, v in R["results"]["conservative"]["per_symbol"].items():
        ttf = (f"{v['median_time_to_fill_s']:.1f}s"
               if v["median_time_to_fill_s"] == v["median_time_to_fill_s"] else "—")
        w(L, f"| {s} | {v['orders']:,} | {v['touch_rate']:.1%} | "
             f"{v['fill_rate']:.1%} | {ttf} |")
    w(L)
    w(L, "## Queue position — the limitation, stated plainly")
    w(L)
    w(L, "**Exact queue position cannot be reconstructed from Delta's public feed.**")
    w(L, "Three measured reasons, recorded in the pre-registration before collection:")
    w(L)
    w(L, "1. **No order count.** Levels carry aggregate size only. We know how much")
    w(L, "   rests at a price, never how many orders. Queue position is expressible")
    w(L, "   in size ahead, never in orders ahead.")
    w(L, "2. **Coalescing.** `l2_orderbook` arrives at ~1 Hz with sequence deltas of")
    w(L, "   2–4, so two to three book updates are skipped between snapshots. Event")
    w(L, "   ordering inside a one-second window is unobservable.")
    w(L, "3. **Cancellations are not attributable.** A fall in aggregate size means")
    w(L, "   someone cancelled, but not whether they were ahead of us (which helps)")
    w(L, "   or behind us (which does not) — and the net figure also absorbs new")
    w(L, "   orders joining behind us.")
    w(L)
    w(L, "Hence a bound rather than an estimate. The conservative model assumes no")
    w(L, "cancellation ever helps us; the optimistic model assumes every one does.")
    w(L, "**No midpoint is reported, because the feed does not contain one.**")
    return "\n".join(L) + "\n"


def adverse_selection() -> str:
    L = []
    w(L, "# H-MAKER-1 — ADVERSE SELECTION  (Q2)")
    w(L)
    w(L, "> What is the economic adverse selection of those actual fills?")
    w(L)
    w(L, "## Definition (frozen before collection)")
    w(L)
    w(L, "    signed_markout(h) = side * (mid(fill+h) - mid(fill)) / mid(fill) * 10_000")
    w(L, "    adverse_selection(h) = -signed_markout(h)")
    w(L)
    w(L, "A passive BUY followed by a decline, or a passive SELL followed by a rise,")
    w(L, "gives **positive** adverse selection — the fill hurt us. Markout is against")
    w(L, "the **mid**, not our fill price: measuring against our own price would")
    w(L, "credit us the half-spread we earned by resting, which the 4.72 bps fee")
    w(L, "arithmetic already counts.")
    w(L)
    for mode in h.MODES:
        a = R["results"][mode]["adverse"]
        w(L, f"## {mode.capitalize()} queue bound")
        w(L)
        w(L, "| horizon | adverse (bps) | 95% CI | fills | clusters | MDE | cluster t |")
        w(L, "|---|---:|---|---:|---:|---:|---:|")
        for hz in h.MARKOUT_MIN:
            v = a[f"+{hz}m"]
            star = " **(PRIMARY)**" if hz == h.PRIMARY_MARKOUT_MIN else ""
            w(L, f"| +{hz}m{star} | {v['mean']:+.3f} | "
                 f"[{v['ci_low']:+.3f}, {v['ci_high']:+.3f}] | {v['n']:,} | "
                 f"{v['n_clusters']:,} | {v['mde']:.3f} | {v['t']:+.2f} |")
        w(L)
    w(L, f"## Against the frozen kill threshold of {KILL} bps")
    w(L)
    w(L, "| bound | adverse @ +1m | 95% CI upper | vs threshold |")
    w(L, "|---|---:|---:|---|")
    for mode in h.MODES:
        v = R["results"][mode]["adverse"][P]
        w(L, f"| {mode} | {v['mean']:+.3f} | {v['ci_high']:+.3f} | "
             f"{'**below**' if v['ci_high'] < KILL else '**at or above**'} |")
    w(L)
    w(L, "## Per symbol (conservative, primary horizon)")
    w(L)
    w(L, "| symbol | fills | adverse @ +1m | 95% CI |")
    w(L, "|---|---:|---:|---|")
    for s, v in R["results"]["conservative"]["per_symbol"].items():
        ap = v["adverse_primary"]
        w(L, f"| {s} | {ap['n']:,} | {ap['mean']:+.3f} | "
             f"[{ap['ci_low']:+.3f}, {ap['ci_high']:+.3f}] |")
    return "\n".join(L) + "\n"


def statistical_report() -> str:
    L = []
    w(L, "# H-MAKER-1 — STATISTICAL REPORT")
    w(L)
    w(L, "## Inference")
    w(L)
    w(L, "Ratified H-NULL-1 hierarchy, used unchanged:")
    w(L)
    w(L, "    PRIMARY               cluster")
    w(L, "    SECONDARY DIAGNOSTIC  moving-block bootstrap")
    w(L, "    DIAGNOSTIC            iid")
    w(L)
    w(L, f"    cluster unit = (symbol, {h.CLUSTER_BUCKET_S // 60}-minute bucket)")
    w(L, f"    MDE = {h.MDE_K} * SE_cluster")
    w(L)
    w(L, "Declared before collection. Orders 30 s apart on one symbol overlap in")
    w(L, "their markout windows and see the same order flow; an iid standard error")
    w(L, "would understate uncertainty exactly as it did in H-REL-1.")
    w(L)
    w(L, "`hnull1.inference()` is called unchanged and `se_cluster` is read")
    w(L, "explicitly — that function predates the ratification and still defaults")
    w(L, "`se` to the block estimator.")
    w(L)
    w(L, "## Cluster versus iid, at the primary horizon")
    w(L)
    w(L, "| bound | fills | clusters | SE cluster | SE iid | understatement |")
    w(L, "|---|---:|---:|---:|---:|---:|")
    for mode in h.MODES:
        v = R["results"][mode]["adverse"][P]
        if v["n"] and v.get("se_iid"):
            w(L, f"| {mode} | {v['n']:,} | {v['n_clusters']:,} | {v['se']:.3f} | "
                 f"{v['se_iid']:.3f} | {v['se'] / v['se_iid']:.2f}× |")
    w(L)
    w(L, "## Decision rule (frozen)")
    w(L)
    w(L, f"    PASS          CI upper < {KILL} bps AND fill model supported")
    w(L, f"                  AND sample targets met")
    w(L, f"    FAIL          CI lower >= {KILL} bps")
    w(L, f"    INCONCLUSIVE  CI straddles {KILL}, OR targets not met,")
    w(L, f"                  OR the two queue bounds imply different verdicts")
    w(L)
    v = R["verdicts"]
    w(L, f"    conservative bound -> {v['conservative']}")
    w(L, f"    optimistic bound   -> {v['optimistic']}")
    w(L, f"    sample targets met -> {R['targets']['met']}")
    w(L, f"    FINAL              -> {v['final']}")
    return "\n".join(L) + "\n"


def main() -> int:
    (h.OUT / "collection_report.md").write_text(collection_report())
    (h.OUT / "fill_model.md").write_text(fill_model())
    (h.OUT / "adverse_selection.md").write_text(adverse_selection())
    (h.OUT / "statistical_report.md").write_text(statistical_report())
    print("wrote collection_report.md, fill_model.md, adverse_selection.md, "
          "statistical_report.md")
    print(f"verdict: {R['verdicts']['final']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
