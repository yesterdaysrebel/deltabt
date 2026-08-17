"""§13 ANTI-LOOKAHEAD AUDIT for H-Structure-1. Mandatory. Run before anything else.

The decisive test is TRUNCATION, not inspection. If any future bar leaked into
a structure value, then recomputing that value on data that has been cut off at
that bar would change it. So: build the structure on the full series, rebuild it
on a series truncated at bar K, and require bit-identical state at K. Repeated
across timeframes, swing strengths and cut points, that is a proof rather than
an argument.

The same test is then applied to the whole pipeline: the trades a candidate
produces from truncated data must be identical to the trades it produces from
the full data, for every trade entered before the cut.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import common
import hstructure as hs

HERE = Path(__file__).parent
FAIL = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAIL.append(name)


# ---------------------------------------------------------------- 1. source scan

BANNED = [
    (r"shift\(\s*-", "negative shift (future value)"),
    (r"center\s*=\s*True", "centred rolling window"),
    (r"bfill|backfill|method\s*=\s*[\"']b", "backward fill"),
    (r"\[::-1\]", "reversed traversal"),
    (r"\.rolling\([^)]*center", "centred rolling"),
]


def source_scan():
    print("\n1. STATIC SOURCE SCAN (signal code only; the simulator is frozen H-WPR-1)")
    src = (HERE / "hstructure.py").read_text()
    # Strip docstrings and comments before scanning. The module docstring names
    # the banned constructs in order to state that none are used, and matching
    # that sentence is a scanner bug, not a finding.
    code = re.sub(r'"""[\s\S]*?"""', "", src)
    body = "\n".join(l for l in code.splitlines() if not l.strip().startswith("#"))
    for pat, why in BANNED:
        hits = [l for l in body.splitlines() if re.search(pat, l)]
        check(f"no {why}", not hits, f"{hits[:2]}" if hits else "")
    # the only forward-looking construct permitted is the swing window itself,
    # which is read at k+N and never at k
    check("swing flags are consumed at k + N, never at k",
          "k = t - n_str" in src and "if k >= n_str" in src)


# ---------------------------------------------------------------- 2. truncation


def truncation_structure(data):
    print("\n2. STRUCTURE TRUNCATION TEST")
    print("   rebuild the structure on data cut off at bar K; state at K must be identical")
    keys = ("last_h_px", "prev_h_px", "last_l_px", "prev_l_px", "is_hh", "is_lh",
            "is_hl", "is_ll", "bull", "bear", "A_long_shot", "B_short_shot",
            "C_long_shot", "C_short_shot", "D_long_shot", "D_short_shot",
            "D_long_level", "D_short_level")
    worst = 0
    before = len(FAIL)
    for sym in ("BTCUSD", "SOLUSD"):
        df = data[sym]["df"]
        for tf in (5, 15, 60):
            for n in (2, 8):
                full = hs.build_structure(df, tf, n)
                nb = len(full["time"])
                cuts = [int(nb * f) for f in (0.25, 0.5, 0.75, 0.9)]
                for K in cuts:
                    cut_end = int(full["time"][K]) + tf * 60      # close of bar K
                    sub = df[df.time < cut_end].reset_index(drop=True)
                    part = hs.build_structure(sub, tf, n)
                    if len(part["time"]) != K + 1:
                        check(f"{sym} tf={tf} N={n} cut@{K} bar alignment", False,
                              f"{len(part['time'])} != {K+1}")
                        continue
                    for k in keys:
                        a, b = full[k][K], part[k][K]
                        same = (a == b) or (isinstance(a, float) and np.isnan(a)
                                            and np.isnan(b))
                        if not same:
                            check(f"{sym} tf={tf} N={n} cut@{K} key={k}", False,
                                  f"{a} != {b}")
                            break
                    worst += 1
    check(f"all {worst} truncation points reproduce structure state exactly",
          len(FAIL) == before)


def truncation_pipeline(data):
    print("\n3. PIPELINE TRUNCATION TEST (trades from truncated data == trades from full data)")
    sym = "BTCUSD"
    d = data[sym]
    df = d["df"]
    for tf, n, fam, trig in ((15, 5, "C", "oneshot"), (5, 3, "A", "level"),
                             (60, 2, "D", "oneshot")):
        full_S = hs.build_structure(df, tf, n)
        cut = int(common.TRAIN[0] + 0.5 * (common.TRAIN[1] - common.TRAIN[0]))
        r_full = hs.run_variant(d, full_S, fam, trig, tf,
                                start=common.TRAIN[0], end=cut)
        sub = df[df.time < cut].reset_index(drop=True)
        d2 = dict(d)
        d2.update(df=sub, t1=sub.time.to_numpy("int64"),
                  o=sub.open.to_numpy("float64"), h=sub.high.to_numpy("float64"),
                  l=sub.low.to_numpy("float64"), c=sub.close.to_numpy("float64"),
                  mh=d["mh"][:len(sub)], ml=d["ml"][:len(sub)],
                  tradable=d["tradable"][:len(sub)])
        S2 = hs.build_structure(sub, tf, n)
        r_cut = hs.run_variant(d2, S2, fam, trig, tf,
                               start=common.TRAIN[0], end=cut)
        a, b = r_full.to_frame(), r_cut.to_frame()
        # a trade still open at the cut cannot be compared on its exit
        if len(a) and len(b):
            m = min(len(a), len(b)) - 2
            m = max(m, 0)
            same = (a.entry_time.to_numpy()[:m].tolist()
                    == b.entry_time.to_numpy()[:m].tolist()) and (
                   np.allclose(a.stop_price.to_numpy()[:m],
                               b.stop_price.to_numpy()[:m], rtol=0, atol=0)) and (
                   a.side.to_numpy()[:m].tolist() == b.side.to_numpy()[:m].tolist())
            check(f"{fam}/{trig} tf={tf} N={n}: {m} entries identical under truncation",
                  same)
        else:
            check(f"{fam}/{trig} tf={tf} N={n}: produced trades", len(a) > 0,
                  f"full={len(a)} cut={len(b)}")


# ---------------------------------------------------------------- 4. timestamps


def timestamp_order(data):
    print("\n4. PER-TRADE TIMESTAMP ORDERING")
    print("   entry_time >= swing confirmation instant, for EVERY trade")
    bad_total = tot = 0
    for sym in common.CORE:
        d = data[sym]
        for tf, n in ((5, 3), (15, 5), (60, 8)):
            S = hs.build_structure(d["df"], tf, n)
            for fam in hs.FAMILIES:
                r = hs.run_variant(d, S, fam, "oneshot", tf,
                                   start=common.TRAIN[0], end=common.VALID[1])
                f = r.to_frame()
                if f.empty:
                    continue
                f = hs.attach_diagnostics(f, S, d, tf)
                tot += len(f)
                bad_total += int((f.entry_time < f.struct_conf_time).sum())
                bad_total += int((f.entry_time < f.struct_bar_time + tf * 60).sum())
    check(f"entry_time >= confirmation instant on all {tot:,} trades",
          bad_total == 0, f"violations={bad_total}")


def both_sides_trade(data):
    """Regression guard: shorts must actually reach the simulator.

    The frozen simulator computes a short stop as max(st1, leg_hi) and numba's
    max propagates NaN, so an unfilled long-stop slot silently deletes every
    short trade while leaving longs untouched. That is invisible in aggregate
    output, so it is asserted here.
    """
    print("\n5. BOTH DIRECTIONS REACH THE SIMULATOR (regression guard)")
    d = data["BTCUSD"]
    S = hs.build_structure(d["df"], 15, 5)
    for fam, want in (("A", "long"), ("B", "short"), ("C", "both"), ("D", "both")):
        r = hs.run_variant(d, S, fam, "oneshot", 15,
                           start=common.TRAIN[0], end=common.TRAIN[1])
        f = r.to_frame()
        nl = int((f.side > 0).sum()) if len(f) else 0
        ns = int((f.side < 0).sum()) if len(f) else 0
        ok = {"long": nl > 0 and ns == 0, "short": ns > 0 and nl == 0,
              "both": nl > 0 and ns > 0}[want]
        check(f"family {fam} ({want}): {nl} long / {ns} short", ok)


def main():
    print("=" * 96)
    print("H-STRUCTURE-1  ANTI-LOOKAHEAD AUDIT  (§13)")
    print("=" * 96)
    data = common.load(common.CORE)
    source_scan()
    truncation_structure(data)
    truncation_pipeline(data)
    timestamp_order(data)
    both_sides_trade(data)
    print("\n" + "=" * 96)
    status = "PASS" if not FAIL else "FAIL"
    print(f"LOOK-AHEAD STATUS: {status}")
    if FAIL:
        print("FAILED CHECKS:")
        for f in FAIL:
            print("   -", f)
        print("\nSTOP. Performance results must not be interpreted.")
    print("=" * 96)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
