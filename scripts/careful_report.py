"""Careful sim, part 2: every monthly start, 2026, the real picks, and per-trade results at a fixed $3,184 -- every
number computed by all three implementations (per trade: A and B, the two with a per-trade function) and asserted equal.

    PYTHONPATH=. python scripts/careful_report.py
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
import run4_resell_account as RA  # noqa: E402
import run4_trailing as T  # noqa: E402

VARS = ["run4", "run4+resell", "run4+resell+bank"]


def three(days, var, le):
    r = [M.run(days, var, le) for M in (A, B, Cc)]
    tot = [x[1] + x[2] for x in r]
    assert max(tot) - min(tot) < 0.01 and len({len(x[0]) for x in r}) == 1, (var, le, tot)
    return r[0]


def main():
    days = D.load()
    print("Every number below: A, B and C agree (asserted).\n")
    # per trade, fixed $3,184 (no account effects)
    print("PER TRADE at a fixed $3,184 (every pick that fills; no account effects), A == B asserted:")
    for le in (False, True):
        rows, re_rows = [], []
        for day, picks in days:
            for rank, sym, H, L, Cl in picks:
                a = T.short(sym, H, L, Cl, 3, 0.49 * D.START, 500.0, 200.0, 0.25 * D.START, le, 0.008, None)
                b = B.first_trade(sym, H, L, Cl, D.START, le)
                assert (a is None) == (b is None)
                if a is None:
                    continue
                assert a[2:4] == (b["k"], b["j"]) and abs(a[1] - b["pnl"]) < 0.01, (day, sym, a, b)
                rows.append((day, a[0], a[1]))
                if a[0] in ("STOP", "LIQ") and a[3] <= 1438:
                    qa = RA.resell_usd((day, sym, H, L, Cl, a[3]), D.START + a[1], le)
                    qb = B.resell(sym, H, L, Cl, a[3], D.START + a[1], le)
                    assert (qa is None) == (qb is None) and (qa is None or (qa[0] == qb["how"] and abs(qa[1] - qb["pnl"]) < 0.01)), (sym, qa, qb)
                    if qa:
                        re_rows.append((day, qa[0], qa[1]))
        df = pd.DataFrame(rows, columns=["day", "how", "pnl"])
        rd = pd.DataFrame(re_rows, columns=["day", "how", "pnl"])
        c = Counter(df.how)
        print(f"  {'LENIENT' if le else 'STRICT '}: run 4 {len(df)} fills {dict(sorted(c.items()))}  total ${df.pnl.sum():+,.0f}  "
              f"avg ${df.pnl.mean():+.0f}/trade | 2025 avg ${df[df.day < '2026'].pnl.mean():+.0f}, 2026 avg ${df[df.day >= '2026'].pnl.mean():+.0f}")
        print(f"           re-sell A {len(rd)} re-sells {dict(sorted(Counter(rd.how).items()))}  total ${rd.pnl.sum():+,.0f}  "
              f"avg ${rd.pnl.mean():+.0f} | 2025 avg ${rd[rd.day < '2026'].pnl.mean():+.0f}, 2026 avg ${rd[rd.day >= '2026'].pnl.mean():+.0f}")
    # periods
    real = [d for d in days if d[0] >= pd.Timestamp("2026-10-29")]
    y26 = [d for d in days if d[0] >= pd.Timestamp("2026-01-01")]
    print("\nACCOUNTS from $3,184, end = bank + wallet, STRICT .. LENIENT (floor $1,000 = bot STOPPED, no refill):")
    print(f"  {'variant':18s} | {'20 real picks':>19s} | {'2026 from Jan 1':>19s} | {'22 monthly starts: median end':>30s} | ended under $1,000 | ended above $3,184")
    for var in VARS:
        cells = []
        for ps in (real, y26):
            s, l_ = three(ps, var, False), three(ps, var, True)
            cells.append(f"{s[1] + s[2]:7,.0f} .. {l_[1] + l_[2]:7,.0f}")
        ends = {False: [], True: []}
        for mo in pd.period_range("2025-01", "2026-10", freq="M"):
            sub = [d for d in days if d[0] >= mo.to_timestamp()]
            for le in (False, True):
                r = three(sub, var, le)
                ends[le].append(r[1] + r[2])
        es, el = np.array(ends[False]), np.array(ends[True])
        print(f"  {var:18s} | {cells[0]} | {cells[1]} | {np.median(es):12,.0f} .. {np.median(el):7,.0f} | "
              f"{(es < 1000).sum():2d} .. {(el < 1000).sum():2d} of 22     | {(es > 3184).sum():2d} .. {(el > 3184).sum():2d} of 22")
    for var in ("run4", "run4+resell"):
        for le in (False, True):
            led = three(y26, var, le)[0]
            df = pd.DataFrame(led, columns=["day", "sym", "leg", "k", "j", "how", "pnl", "bal"])
            df["m"] = pd.to_datetime(df.day).dt.strftime("%Y-%m")
            print(f"\n  2026 from Jan 1, {var}, {'LENIENT' if le else 'STRICT'}:")
            prev = D.START
            for m, g in df.groupby("m"):
                cc = Counter(g.how)
                print(f"    {m} start {prev:7,.0f} trades {len(g):2d} (re {int((g.leg == 'resell').sum())}) T{cc.get('TARGET', 0)} "
                      f"L{cc.get('LOCKED', 0)} S{cc.get('STOP', 0) + cc.get('LIQ', 0)}  P&L {g.pnl.sum():+8,.0f} end {g.bal.iloc[-1]:7,.0f}")
                prev = g.bal.iloc[-1]
    for le in (False, True):
        led = three(real, "run4+resell", le)[0]
        print(f"\n  20 real picks, run4+resell, {'LENIENT' if le else 'STRICT'}:")
        for d, s, leg, k, j, how, pnl, bal in led:
            print(f"    {d} {s:12s} {leg:6s} {how:6s} {pnl:+8,.0f}  balance {bal:8,.0f}")


if __name__ == "__main__":
    main()
