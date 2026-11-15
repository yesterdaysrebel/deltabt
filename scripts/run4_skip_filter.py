"""Owner 2026-11-15: "small position is not on the table ... what can act as a cushion?" -- at full size the only
cushion left is NOT TAKING the trades most likely to stop. Features known when the order fills:
  fill_min   minutes from 00:00 UTC to the fill
  rise5/15/60  how far the price rose in the 5/15/60 minutes before the fill (fill price / close N min before - 1)
  prev_change  yesterday's gain;  atrp  the coin's ATR14 %;  prev2_change  the day before;  wick;  prev_turnover
Outcome: run 4 final rules per trade on a fixed $3,184 (run4_trailing.short), STRICT and LENIENT.
For each feature x 'skip above / skip below' x quintile cut, chosen on 2025 only (TRAIN), judged on 2026 + the
20 real picks (TEST, never seen). A filter must cut the stops without cutting the winners more.

    PYTHONPATH=. python scripts/run4_skip_filter.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import gainer_fade_2025_lab as G  # noqa: E402
import run4_trailing as T  # noqa: E402

FEATS = ["fill_min", "rise5", "rise15", "rise60", "prev_change", "atrp", "prev2_change", "wick", "prev_turnover"]


def table():
    hist, *_ = C.paths_history()
    g = G.panel()
    g["date"] = pd.to_datetime(g.time, unit="s")
    pan = g.set_index(["date", "symbol"])
    rows = []
    for day, sym, hi, lo, cl, k0 in hist + C.paths_real():
        a = T.short(sym, hi, lo, cl, k0, 0.49 * C.START, 500.0, 200.0, 0.25 * C.START, False, 0.008, None)
        if a is None:
            continue
        b = T.short(sym, hi, lo, cl, k0, 0.49 * C.START, 500.0, 200.0, 0.25 * C.START, True, 0.008, None)
        kf, e = a[2], cl[k0 - 1] * 1.05
        r = dict(day=pd.Timestamp(day), sym=sym, how=a[0], pnl_s=a[1], pnl_l=b[1], fill_min=kf)
        for n in (5, 15, 60):
            r[f"rise{n}"] = e / cl[max(kf - n, 0)] - 1
        key = (pd.Timestamp(day), sym)
        for f in ("prev_change", "atrp", "prev2_change", "wick", "prev_turnover"):
            r[f] = pan[f].get(key, np.nan) if key in pan.index else np.nan
        rows.append(r)
    return pd.DataFrame(rows)


def main():
    C.checks()
    d = table()
    tr, te = d[d.day < "2026-01-01"], d[d.day >= "2026-01-01"]
    print(f"Run-4 fills: TRAIN 2025 {len(tr)} (stops {tr.how.isin(['STOP', 'LIQ']).sum()}), "
          f"TEST 2026 + real {len(te)} (stops {te.how.isin(['STOP', 'LIQ']).sum()})")
    print(f"Feature data missing: " + ", ".join(f"{f} {d[f].isna().sum()}" for f in FEATS if d[f].isna().any()) + "\n")
    print("Stops vs the rest, median of each feature (TRAIN | TEST):")
    for f in FEATS:
        s1, o1 = tr[tr.how.isin(["STOP", "LIQ"])][f].median(), tr[~tr.how.isin(["STOP", "LIQ"])][f].median()
        s2, o2 = te[te.how.isin(["STOP", "LIQ"])][f].median(), te[~te.how.isin(["STOP", "LIQ"])][f].median()
        print(f"   {f:14s} stops {s1:10.4g} rest {o1:10.4g} | stops {s2:10.4g} rest {o2:10.4g}")
    cands = []
    for f in FEATS:
        for q in (0.2, 0.4, 0.6, 0.8):
            cut = tr[f].quantile(q)
            for side in ("above", "below"):
                keep = (lambda x, c=cut, s=side, ff=f: ~(x[ff] > c) if s == "above" else ~(x[ff] < c))
                k_tr = keep(tr)
                cands.append((tr[k_tr].pnl_s.sum() - tr.pnl_s.sum(), f, side, cut, q, keep))
    cands.sort(key=lambda x: -x[0])
    base_tr = (tr.pnl_s.sum(), tr.pnl_l.sum())
    base_te = (te.pnl_s.sum(), te.pnl_l.sum())
    print(f"\nNo filter: TRAIN ${base_tr[0]:+,.0f} .. ${base_tr[1]:+,.0f} | TEST ${base_te[0]:+,.0f} .. ${base_te[1]:+,.0f} (STRICT .. LENIENT)\n")
    print(f"{len(cands)} skip rules tried; {sum(c[0] > 0 for c in cands)} improve TRAIN. Top 10 on TRAIN, and on the unseen TEST:")
    for gain, f, side, cut, q, keep in cands[:10]:
        k_tr, k_te = keep(tr), keep(te)
        st_tr = tr[~k_tr].how.isin(["STOP", "LIQ"]).sum()
        st_te = te[~k_te].how.isin(["STOP", "LIQ"]).sum()
        tg_te = (te[~k_te].how == "TARGET").sum()
        print(f"  skip {f} {side} {cut:.4g} (train {q:.0%} cut): TRAIN {tr[k_tr].pnl_s.sum() - base_tr[0]:+8,.0f} "
              f"(skips {(~k_tr).sum()} trades, {st_tr} stops) | TEST {te[k_te].pnl_s.sum() - base_te[0]:+8,.0f} .. "
              f"{te[k_te].pnl_l.sum() - base_te[1]:+8,.0f} (skips {(~k_te).sum()}: {st_te} stops, {tg_te} targets)")
    te_gain = np.array([te[c[5](te)].pnl_s.sum() - base_te[0] for c in cands])
    print(f"\nAll {len(cands)} rules on TEST (STRICT): {int((te_gain > 0).sum())} improve; median {np.median(te_gain):+,.0f}")
    d.to_csv("out/run4_skip_filter_table.csv", index=False)


if __name__ == "__main__" and "--fair" not in sys.argv:
    main()


def fair():
    """The fair test: avg $ per KEPT trade vs the unfiltered avg, and vs skipping the same number of trades at random."""
    d = pd.read_csv("out/run4_skip_filter_table.csv", parse_dates=["day"])
    tr, te = d[d.day < "2026-01-01"], d[d.day >= "2026-01-01"]
    rng = np.random.default_rng(0)
    print("\nFAIR TEST -- average $ per trade the filter KEEPS (a skipped trade is $0, not a gain):")
    print(f"   no filter: TRAIN {tr.pnl_s.mean():+.0f} .. {tr.pnl_l.mean():+.0f}  |  TEST {te.pnl_s.mean():+.0f} .. {te.pnl_l.mean():+.0f}  per trade")
    rows = []
    for f in FEATS:
        for q in (0.2, 0.4, 0.6, 0.8):
            cut = tr[f].quantile(q)
            for side in ("above", "below"):
                kt = ~(tr[f] > cut) if side == "above" else ~(tr[f] < cut)
                rows.append((tr[kt].pnl_s.mean(), f, side, cut, q))
    rows.sort(key=lambda x: -x[0])
    print(f"   top 10 by TRAIN avg per kept trade (STRICT), then TEST; 'random' = TEST avg when the same number of trades")
    print(f"   are kept at random (10,000 draws): how often random does at least as well")
    for avg_tr, f, side, cut, q in rows[:10]:
        ke = ~(te[f] > cut) if side == "above" else ~(te[f] < cut)
        n = int(ke.sum())
        a_s, a_l = te[ke].pnl_s.mean(), te[ke].pnl_l.mean()
        rnd = np.array([te.pnl_s.to_numpy()[rng.choice(len(te), n, replace=False)].mean() for _ in range(10000)])
        print(f"   keep unless {f} {side} {cut:.4g}: TRAIN {avg_tr:+5.0f}/trade | TEST kept {n:3d}: {a_s:+5.0f} .. {a_l:+5.0f}/trade; "
              f"random as good {(rnd >= a_s).mean():.0%}")


if __name__ == "__main__" and "--fair" in sys.argv:
    fair()
