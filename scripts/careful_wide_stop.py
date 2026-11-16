"""Owner 2026-11-15: "find a way to solve the stop-loss problem". The drift after the +5% spike is slow (+1.4% mean
after 24 h, 0 after 15 min; out/careful/drift.txt) and the path against the short is wide (over +10% in 38%). So:
the SAME dollars at risk per trade, but the stop far enough that noise does not reach it, and a hold of hours.
  entry   resting limit sell 5% above the 00:02 price (as built), maker 0.02% + 0.05% slippage
  stop    X = 5 / 7.5 / 10 / 15 / 20 / 30% above the entry (touch; STRICT = worse of the stop and that minute's
          close, +0.1%; LENIENT = the stop +0.1%); taker 0.05%
  target  none, or a fall of 5 / 10% (fills 0.1% through, taker)
  hold    out at the close 4 / 12 / 24 h after the fill (+0.1% slippage, taker)
  size    by risk: notional = RISK / (X + 0.1% + 0.12% costs), so a stop AT its price loses RISK. R = P&L / RISK.
          Isolated margin must cover X + 2.5% maintenance of the notional (e.g. X = 15%: 17.5% of the notional), so
          liquidation sits beyond the stop.
Per pick; TRAIN 2025, TEST 2026 + real. Two implementations (W1 loop, W2 vectorised) must agree.

    PYTHONPATH=. python scripts/careful_wide_stop.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402

IN, OUT_FEE = 0.0007, 0.0005


def w1(H, L, Cl, le, X, T, hold):
    e = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    if k is None:
        return None
    unit = X + 0.001 + 0.0012 + X * 0.0005                 # loss per $1 of position when the stop fills at its price
    ret = lambda x: -(x / e - 1) - IN - OUT_FEE * x / e   # noqa: E731
    stop, tp = e * (1 + X), (None if T is None else e * (1 - T))
    last = min(2880, k + hold)
    for j in range(k + 1, last + 1):
        if H[j] >= stop:
            x = stop * 1.001 if le else max(stop, Cl[j]) * 1.001
            return "STOP", ret(x) / unit, k, j
        if tp is not None and L[j] <= tp * 0.999:
            return "TARGET", ret(tp) / unit, k, j
    return "TIME", ret(Cl[last] * 1.001) / unit, k, last


def w2(H, L, Cl, le, X, T, hold):
    e = Cl[2] * 1.05
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if f.size == 0:
        return None
    k = 3 + int(f[0])
    last = min(2880, k + hold)
    h, l, c = H[k + 1:last + 1], L[k + 1:last + 1], Cl[k + 1:last + 1]
    stop = e * (1 + X)
    si = np.flatnonzero(h >= stop)
    ti = np.flatnonzero(l <= e * (1 - T) * 0.999) if T is not None else np.array([], int)
    s_at = int(si[0]) if si.size else 10 ** 9
    t_at = int(ti[0]) if ti.size else 10 ** 9
    unit = X + 0.001 + 0.0012 + X * 0.0005
    if s_at <= t_at and s_at < 10 ** 9:
        x = stop * 1.001 if le else max(stop, c[s_at]) * 1.001
        how, j = "STOP", k + 1 + s_at
    elif t_at < 10 ** 9:
        x, how, j = e * (1 - T), "TARGET", k + 1 + t_at
    else:
        x, how, j = Cl[last] * 1.001, "TIME", last
    return how, ((e - x) / e - IN - OUT_FEE * x / e) / unit, k, j


GRID = [(X, T, hold) for X in (0.05, 0.075, 0.10, 0.15, 0.20, 0.30) for T in (None, 0.05, 0.10) for hold in (240, 720, 1440)]


def label(c):
    X, T, hold = c
    return f"stop +{X:.1%}, {'no target' if T is None else f'target {T:.0%}':10s}, hold {hold // 60:2d} h"


def main():
    picks = [(d, s, H, L, Cl) for d, ps in D.load() for r, s, H, L, Cl in ps]
    res = {}
    for cfg in GRID:
        for le in (False, True):
            out = []
            for d, s, H, L, Cl in picks:
                a, b = w1(H, L, Cl, le, *cfg), w2(H, L, Cl, le, *cfg)
                assert (a is None) == (b is None) and (a is None or (a[0] == b[0] and a[2:] == b[2:] and abs(a[1] - b[1]) < 1e-9)), (cfg, d, s, a, b)
                if a:
                    out.append((d, s, a[0], a[1], a[2], a[3]))
            res[(cfg, le)] = pd.DataFrame(out, columns=["day", "sym", "how", "R", "k", "j"])
    print(f"CHECK PASSED: two implementations agree on every trade of all {len(GRID)} designs, both fills.\n")
    print("R per trade (1 R = the planned loss at the stop), STRICT .. LENIENT. TRAIN = 2025 (321 fills), TEST = 2026 + real (296).")
    print(f"  {'design':42s} | {'TRAIN avg R':>16s} | {'TEST avg R':>16s} | TEST strict: stops, win rate, t-stat | position per $750 risk")
    rows = []
    for cfg in GRID:
        s, l_ = res[(cfg, False)], res[(cfg, True)]
        trs, tes, trl, tel = s[s.day < "2026"], s[s.day >= "2026"], l_[l_.day < "2026"], l_[l_.day >= "2026"]
        t = tes.R.mean() / (tes.R.std() / np.sqrt(len(tes)))
        rows.append((cfg, trs.R.mean(), trl.R.mean(), tes.R.mean(), tel.R.mean(), t))
        unit = cfg[0] + 0.001 + 0.0012 + cfg[0] * 0.0005
        print(f"  {label(cfg):42s} | {trs.R.mean():+.3f} .. {trl.R.mean():+.3f} | {tes.R.mean():+.3f} .. {tel.R.mean():+.3f} | "
              f"{(tes.how == 'STOP').mean():4.0%} stops, {(tes.R > 0).mean():4.0%} win, t {t:+.1f} | ${750 / unit:7,.0f}")
    good = [r for r in rows if r[1] > 0]
    print(f"\ndesigns positive on TRAIN (strict): {len(good)} of {len(rows)}; of those positive on TEST strict: {sum(r[3] > 0 for r in good)}, "
          f"TEST lenient: {sum(r[4] > 0 for r in good)}")
    best = max(rows, key=lambda r: r[1])
    print(f"BEST ON TRAIN (strict): {label(best[0])}: TRAIN {best[1]:+.3f} .. {best[2]:+.3f} R -> TEST {best[3]:+.3f} .. {best[4]:+.3f} R (t {best[5]:+.1f})")
    for le in (False, True):
        res[(best[0], le)].to_csv(f"out/careful/wide_stop_best_{'lenient' if le else 'strict'}.csv", index=False)
    return best[0], res


if __name__ == "__main__":
    main()
