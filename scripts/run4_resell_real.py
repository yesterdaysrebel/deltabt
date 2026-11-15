"""The two re-sell settings from scripts/run4_resell_search.py on the 20 real picks (Delta 1m), event by event.
Dollars: A at run 4's size (49% of $3,184 at 20x, its 2.25% stop sits inside the +5% liquidation with the 25% extra
margin); both also shown risking $300.

    PYTHONPATH=. python scripts/run4_resell_real.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_resell_search as S  # noqa: E402
import run4_trailing as T  # noqa: E402

hm = lambda k: f"{int(k) // 60 % 24:02d}:{int(k) % 60:02d}" + ("+1d" if k >= 1440 else "")  # noqa: E731
CFG = {"A (5% off the high, stop +2.25%, hold 24 h)": (("off", 0.05), ("fixed", 0.0225), None),
       "B (30 min after, stop 3% above the high, target 12%)": (("after", 30), ("above_high", 0.03), 0.12)}


def detail(ev, w, s, t, lenient):
    day, sym, hi, lo, cl, ks = ev
    ep = S.entry_point(hi, lo, cl, ks, w)
    if ep is None:
        return None
    k, px = ep
    e = px * 0.999
    sp = e * (1 + s[1]) if s[0] == "fixed" else max(hi[ks:k + 1].max() * (1 + s[1]), e * 1.005)
    r = S.resell(ev, w, s, t, lenient)
    end = min(len(cl) - 1, ks + 1440)
    tp = None if t is None else e * (1 - t)
    for j in range(k + 1, end + 1):
        if hi[j] >= sp:
            x = sp * 1.001 if lenient else max(sp, cl[j]) * 1.001
            break
        if tp is not None and lo[j] <= tp * 0.999:
            x = tp
            break
    else:
        j, x = end, cl[end] * 1.001
    return dict(k=k, e=e, sp=sp, tp=tp, j=j, x=x, R=r[0], how=r[1], top=hi[ks:k + 1].max())


def main():
    C.checks()
    real = sorted(C.paths_real(), key=lambda x: x[0])
    mg, ex = 0.49 * C.START, 0.25 * C.START
    print("Run 4 (final rules, fixed $3,184) on the 20 real picks -- the full stops are the re-sell events:\n")
    evs = []
    for day, sym, hi, lo, cl, k0 in real:
        r = T.short(sym, hi, lo, cl, k0, mg, 500.0, 200.0, ex, False, 0.008, None)
        if r is None:
            continue
        print(f"  {pd.Timestamp(day).date()} {sym:12s} {r[0]:6s} {r[1]:+7,.0f}  (sold {hm(r[2])}, out {hm(r[3])})")
        if r[0] in ("STOP", "LIQ") and r[3] < 1439:
            evs.append(((pd.Timestamp(day), sym, hi, lo, cl, r[3]), cl[k0 - 1] * 1.05))
    print(f"\n{len(evs)} full stops -> {len(evs)} re-sell chances.\n")
    for name, (w, s, t) in CFG.items():
        print(f"RE-SELL {name}")
        tot = {False: [0.0, 0.0], True: [0.0, 0.0]}
        for ev, first_e in evs:
            day, sym, hi, lo, cl, ks = ev
            print(f"  {day.date()} {sym}: first sell {first_e:.6g}, stopped {hm(ks)}; high of the day after the stop "
                  f"{hi[ks:1440].max():.6g} ({hm(ks + int(np.argmax(hi[ks:1440])))}), 24:00 close {cl[1439]:.6g}")
            for le in (False, True):
                d = detail(ev, w, s, t, le)
                if d is None:
                    print(f"     {'LENIENT' if le else 'STRICT '}: never triggered")
                    continue
                notional, _ = C.size(sym, mg, d["e"])
                usd_run4 = d["R"] * notional * (d["sp"] * 1.001 / d["e"] - 1 + 0.001)
                usd300 = d["R"] * 300
                tot[le][0] += usd_run4
                tot[le][1] += usd300
                tgt = "none" if d["tp"] is None else f"{d['tp']:.6g}"
                print(f"     {'LENIENT' if le else 'STRICT '}: re-sell {hm(d['k'])} at {d['e']:.6g} (high so far {d['top']:.6g}) | "
                      f"stop {d['sp']:.6g} (+{d['sp'] / d['e'] - 1:.1%}) target {tgt} | out {hm(d['j'])} {d['how']} at {d['x']:.6g} | "
                      f"{d['R']:+.2f} R = ${usd_run4:+,.0f} at run-4 size, ${usd300:+,.0f} risking $300")
        print(f"  TOTAL re-sells: STRICT ${tot[False][0]:+,.0f} (run-4 size) / ${tot[False][1]:+,.0f} (risk $300); "
              f"LENIENT ${tot[True][0]:+,.0f} / ${tot[True][1]:+,.0f}\n")
    print("Note B's stop can sit >5% above its sell: at run-4 size (20x) that is past the liquidation price even with the\n"
          "25% extra margin, so B only works risk-sized (lower leverage); its run-4-size $ is shown for comparison only.")


if __name__ == "__main__":
    main()
