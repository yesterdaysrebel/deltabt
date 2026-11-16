"""Owner 2026-11-15: "How about testing on different margins". The three exits (scripts/careful_margin.py: same price
levels, every dollar amount scaled with the position, extra margin = margin x 25/49) at 5 / 10 / 15 / 20 / 30 / 49% of
the balance as margin, up to 20x. Bot account from $3,184: first fill wins, one position at a time, month off after 2
full stops, no trades under the $1,000 floor. Full history, 2026, the 20 real picks, 22 monthly starts.
Two implementations per exit are asserted equal on the full history at every margin.

    PYTHONPATH=. python scripts/careful_margin_sweep.py
"""
import os
import sys
from multiprocessing import Pool
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_margin as M  # noqa: E402

MARGINS = (0.05, 0.10, 0.15, 0.20, 0.30, 0.49)


def job(arg):
    name, mf, le = arg
    f1, f2 = M.EXITS[name]
    days = D.load()
    real = [d for d in days if d[0] >= pd.Timestamp("2026-10-29")]
    y26 = [d for d in days if d[0] >= pd.Timestamp("2026-01-01")]
    a, b = M.account(days, f1, mf, le), M.account(days, f2, mf, le)
    assert len(a) == len(b) and all(x[:5] == y[:5] and abs(x[5] - y[5]) < 0.01 for x, y in zip(a, b)), arg
    fast = f2 if name == "Break-even + 5% trail" else f1
    full = M.stats(a)
    ends = np.array([M.stats(M.account([d for d in days if d[0] >= mo.to_timestamp()], fast, mf, le))[0]
                     for mo in pd.period_range("2025-01", "2026-10", freq="M")])
    df = pd.DataFrame(a, columns=["day", "sym", "k", "j", "how", "pnl", "bal"])
    df["m"] = pd.to_datetime(df.day).dt.strftime("%Y-%m")
    mon = df.groupby("m").pnl.sum()
    return arg, dict(full=full[0], dd=full[2], low=full[1], n=len(a), y26=M.stats(M.account(y26, fast, mf, le))[0],
                     real=M.stats(M.account(real, fast, mf, le))[0], under=int((ends < 1000).sum()), above=int((ends > D.START).sum()),
                     med=float(np.median(ends)), worst=float(ends.min()), best=float(ends.max()),
                     mon_up=int((mon > 0).sum()), mon_n=len(mon), mon_avg=float(mon.mean()), mon_worst=float(mon.min()))


def main():
    args = [(name, mf, le) for name in M.EXITS for mf in MARGINS for le in (False, True)]
    with Pool(min(len(args), os.cpu_count() or 2)) as p:
        res = dict(p.map(job, args))
    print("CHECK PASSED: two implementations per exit agree on every trade of the full history at every margin.\n")
    print(f"From ${D.START:,.0f}. STRICT .. LENIENT. 'starts' = 22 monthly starts, each run to Nov 14 2026.")
    for name in M.EXITS:
        print(f"\n{name}")
        print(f"  {'margin':>6s} {'position':>9s} {'a stop':>7s} | {'full history end':>19s} | {'deepest drop':>12s} | {'2026 from Jan 1':>17s} | {'20 real picks':>17s} | "
              f"{'starts < $1,000':>15s} | {'starts > $3,184':>15s} | {'median start ends':>17s} | avg month $ (full history)")
        for mf in MARGINS:
            s, l_ = res[(name, mf, False)], res[(name, mf, True)]
            pos = D.START * mf / 0.0512
            print(f"  {mf:6.0%} ${pos:8,.0f} ${pos * 0.0285:6,.0f} | {s['full']:8,.0f} .. {l_['full']:8,.0f} | {s['dd']:4.0%} .. {l_['dd']:4.0%} | "
                  f"{s['y26']:7,.0f} .. {l_['y26']:7,.0f} | {s['real']:7,.0f} .. {l_['real']:7,.0f} | {s['under']:5d} .. {l_['under']:2d}     | "
                  f"{s['above']:5d} .. {l_['above']:2d}     | {s['med']:7,.0f} .. {l_['med']:7,.0f} | {s['mon_avg']:+5.0f} .. {l_['mon_avg']:+5.0f} ({s['mon_up']}..{l_['mon_up']} of {s['mon_n']} up)")


if __name__ == "__main__":
    main()
