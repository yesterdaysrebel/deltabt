"""Spike Sell with reloads (owner 2026-11-15: "if we reloaded the $3,000 post 2 stops what are the numbers then"):
'reload monthly' tops the wallet up to $3,184 on each month's first pick day (the 2-stops month-off rule kept);
'reload at 2 stops' tops up at once after the 2nd full stop and resets the count (trades on). Net = end balance -
$3,184 - all reloads. Every number: implementations A, B and C agree trade by trade (asserted).

    PYTHONPATH=. python scripts/careful_reload.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_a as A  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402

VARS = ["run4", "run4 reload monthly", "run4 reload at 2 stops"]


def three(days, var, le):
    r = [M.run(days, var, le) for M in (A, B, Cc)]
    a = r[0][0]
    for o in r[1:]:
        assert len(o[0]) == len(a) and all(x[:6] == y[:6] and abs(x[6] - y[6]) < 0.01 and abs(x[7] - y[7]) < 0.01
                                           for x, y in zip(a, o[0])), var
    led = a
    dep = sum(x[6] for x in led if x[5] == "RELOAD")
    nre = sum(1 for x in led if x[5] == "RELOAD" and x[6] > 0)
    end = r[0][1]
    # worst point: lowest (balance - 3,184 - reloads so far) after any trade
    worst, put = 0.0, 0.0
    for x in led:
        if x[5] == "RELOAD":
            put += x[6]
        worst = min(worst, x[7] - D.START - put)
    return dict(end=end, dep=dep, n=nre, net=end - D.START - dep, worst=worst, led=led,
                trades=sum(1 for x in led if x[5] != "RELOAD"))


def main():
    days = D.load()
    real = [d for d in days if d[0] >= pd.Timestamp("2026-10-29")]
    y26 = [d for d in days if d[0] >= pd.Timestamp("2026-01-01")]
    print("Every number: A, B and C agree trade by trade (asserted).  Net = end balance - $3,184 - reloads.\n")
    for nm, ps in (("FULL HISTORY from 2025-01-15", days), ("2026 from Jan 1", y26), ("20 REAL PICKS", real)):
        print(nm)
        for var in VARS:
            s, l_ = three(ps, var, False), three(ps, var, True)
            print(f"  {var:24s} trades {s['trades']:3d}..{l_['trades']:3d} | reloads {s['n']:2d}..{l_['n']:2d} = ${s['dep']:7,.0f} .. ${l_['dep']:7,.0f} | "
                  f"end ${s['end']:7,.0f} .. ${l_['end']:7,.0f} | NET ${s['net']:+8,.0f} .. ${l_['net']:+8,.0f} | worst ${s['worst']:+8,.0f} .. ${l_['worst']:+8,.0f}")
        print()
    print("22 MONTHLY STARTS (2025-01 .. 2026-10, each run to Nov 14 2026): NET, STRICT .. LENIENT")
    for var in VARS:
        nets = {le: np.array([three([d for d in days if d[0] >= mo.to_timestamp()], var, le)["net"]
                              for mo in pd.period_range("2025-01", "2026-10", freq="M")]) for le in (False, True)}
        s, l_ = nets[False], nets[True]
        print(f"  {var:24s} median ${np.median(s):+8,.0f} .. ${np.median(l_):+8,.0f} | made money {(s > 0).sum():2d} .. {(l_ > 0).sum():2d} of 22 | "
              f"worst ${s.min():+8,.0f} .. ${l_.min():+8,.0f} | best ${s.max():+8,.0f} .. ${l_.max():+8,.0f}")
    for var in ("run4 reload monthly", "run4 reload at 2 stops"):
        for le in (False, True):
            r = three(days, var, le)
            df = pd.DataFrame(r["led"], columns=["day", "sym", "leg", "k", "j", "how", "pnl", "bal"])
            df["m"] = pd.to_datetime(df.day).dt.strftime("%Y-%m")
            print(f"\n  {var}, full history, {'LENIENT' if le else 'STRICT'}, month by month:")
            put = 0.0
            for m, g in df.groupby("m"):
                tr = g[g.how != "RELOAD"]
                rl = g[g.how == "RELOAD"].pnl.sum()
                put += rl
                print(f"    {m} trades {len(tr):2d} T{(tr.how == 'TARGET').sum()} L{(tr.how == 'LOCKED').sum()} S{tr.how.isin(['STOP', 'LIQ']).sum()} "
                      f"P&L {tr.pnl.sum():+8,.0f} | reloaded ${rl:6,.0f} (total ${put:7,.0f}) | balance {g.bal.iloc[-1]:7,.0f} | "
                      f"net so far ${g.bal.iloc[-1] - D.START - put:+8,.0f}")


if __name__ == "__main__":
    main()
