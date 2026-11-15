"""Runs the three careful-sim implementations on the frozen Delta dataset, every variant, STRICT and LENIENT, and
compares them trade by trade (same trades, same minutes, same exits, P&L within $0.01 or 1e-6). Only if all three
agree are the results printed. docs/careful_sim_spec.md has the rules.

    PYTHONPATH=. python scripts/careful_compare.py
"""
import sys
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_a as A  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402


def same(x, y):
    return (x[:6] == y[:6] and abs(x[6] - y[6]) <= max(0.01, 1e-6 * abs(x[6]))
            and abs(x[7] - y[7]) <= max(0.01, 1e-6 * abs(x[7])))


def main():
    P = pd.read_csv("out/careful/picks.csv", parse_dates=["day"])
    days = D.load()
    n_used = sum(len(p) for _, p in days)
    print(f"DATA: {len(P)} picks ({(P.source == 'history').sum()} history, {(P.source == 'real').sum()} real), "
          f"{P.day.min().date()} .. {P.day.max().date()}; with Delta candles {n_used}; none {(P.minutes == 0).sum()}\n")
    results = {}
    bad = 0
    for var in D.VARIANTS:
        for le in (False, True):
            ra, rb, rc = (M.run(days, var, le) for M in (A, B, Cc))
            la, lb, lc = ra[0], rb[0], rc[0]
            ok = len(la) == len(lb) == len(lc) and all(same(x, y) and same(x, z) for x, y, z in zip(la, lb, lc))
            ok = ok and abs(ra[1] - rb[1]) < 0.01 and abs(ra[1] - rc[1]) < 0.01 and abs(ra[2] - rb[2]) < 0.01 and abs(ra[2] - rc[2]) < 0.01
            fill = "LENIENT" if le else "STRICT"
            print(f"  {var:22s} {fill:7s}: A {len(la)} trades ${ra[1] + ra[2]:,.2f} | B {len(lb)} ${rb[1] + rb[2]:,.2f} | "
                  f"C {len(lc)} ${rc[1] + rc[2]:,.2f}  -> {'AGREE' if ok else 'DISAGREE'}")
            if not ok:
                bad += 1
                for i, (x, y, z) in enumerate(zip(la, lb, lc)):
                    if not (same(x, y) and same(x, z)):
                        print("     first difference at trade", i, "\n       A", x, "\n       B", y, "\n       C", z)
                        break
                else:
                    print("     lengths", len(la), len(lb), len(lc))
            results[(var, le)] = ra
    if bad:
        print(f"\n{bad} DISAGREEMENTS -- no results printed.")
        return
    print("\nALL THREE IMPLEMENTATIONS AGREE on every trade, every variant, both fill models.\n")
    report(results, days)


def report(results, days):
    print(f"From ${D.START:,.0f} on {days[0][0].date()}. Floor $1,000 = the bot's STOPPED state (no refill).")
    print(f"  {'variant':22s} | {'end (bank + wallet) strict .. lenient':>38s} | {'trades':>9s} | outcomes (strict)")
    for var in D.VARIANTS:
        s, l_ = results[(var, False)], results[(var, True)]
        c = Counter(x[5] for x in s[0])
        rs = sum(x[2] == "resell" for x in s[0])
        stopped = next((x[0] for x in s[0] if x[7] < D.VARIANTS[var]["floor"]), None)
        print(f"  {var:22s} | ${s[1] + s[2]:9,.0f} .. ${l_[1] + l_[2]:9,.0f} (bank {s[2]:,.0f} .. {l_[2]:,.0f}) | "
              f"{len(s[0]):4d} ({rs} re) | {dict(sorted(c.items()))}" + (f"  under the floor from {stopped}" if stopped else ""))
    for var in ("run4", "run4+resell"):
        for le in (False, True):
            led = results[(var, le)][0]
            print(f"\n  {var}, {'LENIENT' if le else 'STRICT'}, month by month:")
            df = pd.DataFrame(led, columns=["day", "sym", "leg", "k", "j", "how", "pnl", "bal"])
            df["m"] = pd.to_datetime(df.day).dt.strftime("%Y-%m")
            prev = D.START
            for m, g in df.groupby("m"):
                cc = Counter(g.how)
                print(f"    {m} start {prev:8,.0f} trades {len(g):2d} (re {int((g.leg == 'resell').sum())}) "
                      f"T{cc.get('TARGET', 0)} L{cc.get('LOCKED', 0)} S{cc.get('STOP', 0)} LIQ{cc.get('LIQ', 0)} "
                      f"24h{cc.get('24h', 0) + cc.get('TIME', 0)}  P&L {g.pnl.sum():+9,.0f} end {g.bal.iloc[-1]:8,.0f}")
                prev = g.bal.iloc[-1]
    for var in D.VARIANTS:
        for le in (False, True):
            pd.DataFrame(results[(var, le)][0], columns=["day", "sym", "leg", "fill_min", "exit_min", "how", "pnl", "balance"]).to_csv(
                f"out/careful/ledger_{var.replace(' ', '_').replace('+', '_')}_{'lenient' if le else 'strict'}.csv", index=False)


if __name__ == "__main__":
    main()
