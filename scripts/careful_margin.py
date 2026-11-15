"""Owner 2026-11-15: "Run number with 10% margin". The three exits (Spike Sell as built; quick booking; break-even +
5% trail) with MF = 10% / 20% / 49% of the balance as margin at up to 20x. Same PRICE levels as before: stop +2.25%,
extra margin = MF x 25/49 (liquidation ~ +5.1%), and every dollar amount scales with the position (target $500 x
MF/49%, lock, booking and activation likewise). Implemented by calling the verified trade functions with the balance
x MF / 0.49 (the margin, extra margin and dollar levels are all proportional to it; Delta's sizing is applied to the
actual margin). Account: first fill wins, one position at a time, month off after 2 full stops, no trades under the
$1,000 floor. Two implementations per exit must agree on every trade; MF = 49% must reproduce the earlier results.

    PYTHONPATH=. python scripts/careful_margin.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_be_trail as BT  # noqa: E402
import careful_quick_book as QB  # noqa: E402
import careful_sim_b as B  # noqa: E402
import run4_trailing as T  # noqa: E402


def built1(sym, H, L, Cl, vb, le):
    t = B.first_trade(sym, H, L, Cl, vb, le)
    return None if t is None else (t["how"].replace("TIME", "24h"), t["pnl"], t["k"], t["j"])


def built2(sym, H, L, Cl, vb, le):
    s = vb / D.START
    return T.short(sym, H, L, Cl, 3, 0.49 * vb, 500 * s, 200 * s, 0.25 * vb, le, 0.008, None)


def quick2(sym, H, L, Cl, vb, le):
    e = Cl[2] * 1.05
    hit = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if hit.size == 0:
        return None
    k = 3 + int(hit[0])
    pos = QB.Pos(sym, e, k, vb, le)
    for m in range(k + 1, pos.until + 1):
        r = pos.minute(m, H[m], Cl[m])
        if r:
            return r[0], r[1], k, m


EXITS = {"Spike Sell as built": (built1, built2), "Quick booking": (QB.q1_trade, quick2), "Break-even + 5% trail": (BT.t1, BT.t2)}


def account(days, fn, mf, le, floor=1000.0):
    bal, busy, month, stops, out = D.START, -1, None, 0, []
    for day, picks in days:
        t0 = int(day.timestamp()) // 60
        m = day.strftime("%Y-%m")
        if m != month:
            month, stops = m, 0
        if bal < floor or stops >= 2 or busy > t0 + 2:
            continue
        best = None
        for rank, sym, H, L, Cl in picks:
            r = fn(sym, H, L, Cl, bal * mf / 0.49, le)
            if r and (best is None or r[2] < best[1][2]):
                best = (sym, r)
        if best is None:
            continue
        sym, r = best
        bal = max(bal + r[1], 0.0)
        busy = t0 + r[3]
        stops += r[0] in ("STOP", "LIQ")
        out.append((day.date(), sym, r[2], r[3], r[0], r[1], bal))
    return out


def stats(led):
    if not led:
        return D.START, D.START, 0.0
    b = np.array([D.START] + [x[6] for x in led])
    peak = np.maximum.accumulate(b)
    return b[-1], b.min(), float((1 - b / peak).max())


def main():
    days = D.load()
    real = [d for d in days if d[0] >= pd.Timestamp("2026-10-29")]
    y26 = [d for d in days if d[0] >= pd.Timestamp("2026-01-01")]
    res = {}
    for name, (f1, f2) in EXITS.items():
        for mf in (0.10, 0.20, 0.49):
            for pn, ps in (("all", days), ("2026", y26), ("real", real)):
                for le in (False, True):
                    a, b = account(ps, f1, mf, le), account(ps, f2, mf, le)
                    assert len(a) == len(b) and all(x[:5] == y[:5] and abs(x[5] - y[5]) < 0.01 and abs(x[6] - y[6]) < 0.01 for x, y in zip(a, b)), (name, mf, pn, le)
                    res[(name, mf, pn, le)] = a
    chk = {("Spike Sell as built", False): 836.48, ("Spike Sell as built", True): 854.46}
    for (name, le), v in chk.items():
        assert abs(stats(res[(name, 0.49, "all", le)])[0] - v) < 0.01
    assert abs(stats(res[("Quick booking", 0.49, "all", False)])[0] - QB.both(days, False)[-1][6]) < 0.01
    assert abs(stats(res[("Break-even + 5% trail", 0.49, "all", False)])[0] - BT.both(days, False)[-1][6]) < 0.01
    print("CHECKS PASSED: two implementations per exit agree on every trade (all margins, periods, fills); 49% reproduces the earlier results.\n")
    print(f"ACCOUNTS from ${D.START:,.0f} (first fill wins, one at a time, month off after 2 full stops, floor $1,000). STRICT .. LENIENT.")
    print(f"  {'exit':22s} {'margin':>6s} | {'position':>8s} | {'full history end':>19s} | {'deepest drop':>12s} | {'trades':>9s} | {'2026 from Jan 1':>17s} | {'20 real picks':>17s}")
    for name in EXITS:
        for mf in (0.10, 0.20, 0.49):
            s, l_ = stats(res[(name, mf, "all", False)]), stats(res[(name, mf, "all", True)])
            cells = [f"{stats(res[(name, mf, p, False)])[0]:7,.0f} .. {stats(res[(name, mf, p, True)])[0]:7,.0f}" for p in ("2026", "real")]
            print(f"  {name:22s} {mf:6.0%} | ${D.START * mf / 0.0512:7,.0f} | {s[0]:8,.0f} .. {l_[0]:8,.0f} | {s[2]:4.0%} .. {l_[2]:4.0%} | "
                  f"{len(res[(name, mf, 'all', False)]):3d} ..{len(res[(name, mf, 'all', True)]):4d} | {cells[0]} | {cells[1]}")
    print("\n22 MONTHLY STARTS at 10% margin (each run to Nov 14 2026), end balance STRICT .. LENIENT:")
    for name, (f1, f2) in EXITS.items():
        e = {le: np.array([stats(account([d for d in days if d[0] >= mo.to_timestamp()], f2 if name != "Spike Sell as built" else f1, 0.10, le))[0]
                           for mo in pd.period_range("2025-01", "2026-10", freq="M")]) for le in (False, True)}
        print(f"  {name:22s} median {np.median(e[False]):6,.0f} .. {np.median(e[True]):6,.0f} | worst {e[False].min():6,.0f} .. {e[True].min():6,.0f} | best {e[False].max():6,.0f} .. {e[True].max():6,.0f} | "
              f"under $1,000: {(e[False] < 1000).sum():2d} .. {(e[True] < 1000).sum():2d} | above $3,184: {(e[False] > D.START).sum():2d} .. {(e[True] > D.START).sum():2d} of 22")
    for name in EXITS:
        print(f"\n  {name}, 10% margin, full history, month by month (STRICT | LENIENT):")
        tabs = []
        for le in (False, True):
            df = pd.DataFrame(res[(name, 0.10, "all", le)], columns=["day", "sym", "k", "j", "how", "pnl", "bal"])
            df["m"] = pd.to_datetime(df.day).dt.strftime("%Y-%m")
            tabs.append(df.groupby("m").agg(n=("pnl", "size"), st=("how", lambda x: int(x.isin(["STOP", "LIQ"]).sum())), pnl=("pnl", "sum"), end=("bal", "last")))
        for m in sorted(set(tabs[0].index) | set(tabs[1].index)):
            a = tabs[0].loc[m] if m in tabs[0].index else None
            b = tabs[1].loc[m] if m in tabs[1].index else None
            fa = f"trades {int(a.n):2d} stops {int(a.st)} P&L {a.pnl:+6,.0f} end {a.end:6,.0f}" if a is not None else " " * 42
            fb = f"trades {int(b.n):2d} stops {int(b.st)} P&L {b.pnl:+6,.0f} end {b.end:6,.0f}" if b is not None else ""
            print(f"    {m}  {fa} | {fb}")
    for name in EXITS:
        for le in (False, True):
            pd.DataFrame(res[(name, 0.10, "all", le)], columns=["day", "sym", "fill_min", "exit_min", "how", "pnl", "balance"]).to_csv(
                f"out/careful/trades_margin10_{name.split()[0].lower()}_{'lenient' if le else 'strict'}.csv", index=False)


if __name__ == "__main__":
    main()
