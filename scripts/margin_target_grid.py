"""Owner 2026-11-15: "are you sure numbers are right? run more tests with 75% margin different targets, 50% with
different targets adding more margin". Same fill rules and checks as scripts/clean_compare.py.

    PYTHONPATH=. python scripts/margin_target_grid.py

Version: spike +5% limit entry, 20x on the trading margin (Delta per-coin limits), lock +$200 after a 1% fall, off for
the month after 2 full stops (and, for comparison, no hard stop). Extra margin is added to the isolated position after
the fill: the contracts do not change; liquidation moves out; a liquidation loses trading margin + extra.
Part D prints minute-by-minute candles for sample trades so the fills can be checked by hand.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402


def per_trade(hist, margin, target, stop, extra):
    res = [(day, sym, C.short(sym, hi, lo, cl, k0, margin * C.START, target, "spike", 200, stop, None, None, extra * C.START))
           for day, sym, hi, lo, cl, k0 in hist]
    return [(dd, s, r) for dd, s, r in res if r]


def summary(hist, real, margin, target, stop, extra, off):
    res = per_trade(hist, margin, target, stop, extra)
    v = np.array([r[1] for _, _, r in res]); h = np.array([r[0] for _, _, r in res])
    mo = pd.Series(v).groupby([dd.strftime("%Y-%m") for dd, _, _ in res]).sum()
    cfg = dict(entry="spike", margin=margin, target=target, lock=200, stop=stop, extra=extra)
    rb, _ = C.account(real, cfg, months_off_after=off)
    hb, hl = C.account(hist, cfg, months_off_after=off)
    gone = up3 = 0
    b12 = []
    for m in pd.period_range("2025-02", "2026-10", freq="M"):
        s = m.to_timestamp()
        sub = [x for x in hist if x[0] >= s]
        gone += C.account(sub, cfg, months_off_after=off)[1] < 1000
        up3 += C.account([x for x in sub if x[0] < s + pd.Timedelta(days=90)], cfg, months_off_after=off)[0] > C.START
        if s + pd.Timedelta(days=365) <= hist[-1][0]:
            b12.append(C.account([x for x in sub if x[0] < s + pd.Timedelta(days=365)], cfg, months_off_after=off)[0])
    return dict(n=len(v), tgt=(h == "TARGET").mean(), lck=(h == "LOCKED").mean(), stp=(h == "STOP").mean(),
                liq=(h == "LIQ").mean(), liq_n=int((h == "LIQ").sum()), avg=v.mean(), stop_avg=v[h == "STOP"].mean(),
                worst=v.min(), green=(mo > 0).sum(), months=len(mo), real=rb, end=hb, low=hl, gone=gone, up3=up3,
                med12=np.median(b12))


def main() -> None:
    C.checks()
    hist, *_ = C.paths_history()
    real = C.paths_real()
    print(f"Spike +5%, 20x on the trading margin, lock +$200 after a 1% fall. Start ${C.START:,.0f}. Same fill rules as clean_compare.\n")
    hdr = (f"{'margin':>6s} {'extra':>5s} {'position':>8s} {'liq at':>6s} {'target':>6s} {'stop':>5s} | {'tgt':>4s} {'lock':>4s} {'stop':>4s} "
           f"{'LIQ':>7s} | {'avg/tr':>7s} {'stop avg':>8s} {'worst':>7s} {'green':>5s} | {'real':>6s} {'hist end':>8s} {'lowest':>6s} "
           f"{'<$1k':>5s} {'up3m':>5s} {'med12m':>7s}")
    for off, lab in ((2, "WITH the hard stop (off for the month after 2 full stops)"), (None, "WITHOUT a hard stop")):
        print(lab)
        print(hdr)
        for margin, extras in ((0.50, (0.0, 0.25, 0.45)), (0.75, (0.0, 0.20))):
            pos = C.size("XUSD", margin * C.START, 1.0)[0]
            for extra in extras:
                for stop in (0.0225, 0.01):
                    for target in (300, 500, 700, 1000):
                        r = summary(hist, real, margin, target, stop, extra, off)
                        print(f"{100 * margin:5.0f}% {100 * extra:4.0f}% {pos:8,.0f} {100 * (0.025 + extra * C.START / pos):5.2f}% {target:6d} "
                              f"{100 * stop:4.2f}% | {100 * r['tgt']:3.0f}% {100 * r['lck']:3.0f}% {100 * r['stp']:3.0f}% {100 * r['liq']:3.0f}%({r['liq_n']:3d}) | "
                              f"{r['avg']:+7.1f} {r['stop_avg']:8,.0f} {r['worst']:7,.0f} {r['green']:2d}/{r['months']:<2d} | {r['real']:6,.0f} {r['end']:8,.0f} "
                              f"{r['low']:6,.0f} {r['gone']:2d}/21 {r['up3']:2d}/21 {r['med12']:7,.0f}")
        print()
    print("D) SPOT CHECKS: minute candles of sample trades (50% margin, $500, stop +2.25%, no extra), so the fills can be checked")
    res = per_trade(hist, 0.5, 500, 0.0225, 0.0)
    for want in ("LIQ", "STOP", "TARGET"):
        dd, sym, r = next(x for x in res if x[2][0] == want)
        p = next(x for x in hist if x[0] == dd and x[1] == sym)
        _, _, hi, lo, cl, k0 = p
        e = cl[k0 - 1] * 1.05
        print(f"  {want}: {sym} {dd.date()}  00:03 close {cl[k0 - 1]:.6g} -> limit sell {e:.6g}; stop {e * 1.0225:.6g}; liquidation {e * 1.025:.6g}; "
              f"filled minute {C.Q.hm(r[2])}, exit minute {C.Q.hm(r[3])}, P&L ${r[1]:+,.0f}")
        for k in range(max(r[2] - 1, 0), min(r[3] + 2, len(cl))):
            print(f"      {C.Q.hm(k)}  high {hi[k]:.6g}  low {lo[k]:.6g}  close {cl[k]:.6g}")


if __name__ == "__main__":
    main()
