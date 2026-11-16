"""Owner 2026-11-15: "how about no SL at all?" Spike Sell entry and size as built (limit +5%, 49% of the balance at up
to 20x). NO stop order: the only exits are the profit exit, the 24 h time exit (market, close x 1.001) and Delta's
LIQUIDATION, which takes the whole isolated margin (margin + extra).
  extra margin  25% of the balance (as built: liquidation ~ +5.1%, a liquidation loses 74% of the balance)
                51% of the balance (everything: liquidation ~ +7.8%, a liquidation loses 100%)
  profit exit   QUICK BOOK (first 1m close netting >= $50 x balance/$3,184) | the +$500 target (0.1% through)
  liquidation   STRICT: a 1m LAST-TRADED high reaches the liquidation price; LENIENT: a 1m MARK high does (Delta
                liquidates on the mark price; out/careful/mark.npz). Liquidation is checked before the profit exit.
Two implementations of the trade (loop / vectorised) must agree; account = first fill wins, one position at a time,
no trades under the $1,000 floor.

    PYTHONPATH=. python scripts/careful_no_stop.py
"""
import sys
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402


def load():
    P = pd.read_csv("out/careful/picks.csv", parse_dates=["day"])
    z = np.load("out/careful/dataset.npz")
    hi, lo, cl = z["hi"], z["lo"], z["cl"]                 # read each array ONCE (indexing the npz re-reads the file)
    mhi = pd.DataFrame(np.load("out/careful/mark.npz")["hi"].T).ffill().to_numpy().T
    mhi = np.where(np.isfinite(mhi), mhi, -np.inf)
    days = {}
    for i, x in enumerate(P.itertuples()):
        days.setdefault(x.day, []).append((int(x.rank), x.symbol, hi[i], lo[i], cl[i], mhi[i]))
    return [(d, sorted(v, key=lambda t: t[0])) for d, v in sorted(days.items())]


def n1(sym, H, L, Cl, MH, bal, strict, xfrac, exit_):
    e = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    if k is None:
        return None
    margin, extra = 0.49 * bal, xfrac * bal
    n, ld = B.sizing(sym, margin, e, extra)
    cap, Q = margin + extra, e * (1 + ld)
    raw = lambda x: n * (-(x / e - 1) - 0.0007 - 0.0005 * x / e)  # noqa: E731
    sc = bal / D.START
    tp = e * (1 - (500 * sc / n + 0.0012))
    trig = H if strict else MH
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        if trig[j] >= Q:
            return "LIQ", -cap, k, j
        if exit_ == "book" and raw(Cl[j] * 1.001) >= 50 * sc:
            return "BOOKED", raw(Cl[j] * 1.001), k, j
        if exit_ == "target" and L[j] <= tp * 0.999:
            return "TARGET", raw(tp), k, j
    return "24h", max(raw(Cl[last] * 1.001), -cap), k, last


def n2(sym, H, L, Cl, MH, bal, strict, xfrac, exit_):
    e = Cl[2] * 1.05
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if f.size == 0:
        return None
    k = 3 + int(f[0])
    margin, extra = bal * 0.49, bal * xfrac
    n, liq = Cc.notional_for(sym, margin, e)
    Q = e * (1 + liq + extra / n)
    last = min(2880, k + 1440)
    sl = slice(k + 1, last + 1)
    val = n * ((e - Cl[sl] * 1.001) / e - 0.0007 - 0.0005 * Cl[sl] * 1.001 / e)
    tpx = e * (1 - (500 * bal / D.START / n + 0.0012))
    li = np.flatnonzero((H if strict else MH)[sl] >= Q)
    pi = np.flatnonzero(val >= 50 * bal / D.START) if exit_ == "book" else np.flatnonzero(L[sl] <= tpx * 0.999)
    a = int(li[0]) if li.size else 10 ** 9
    b = int(pi[0]) if pi.size else 10 ** 9
    if a <= b and a < 10 ** 9:
        return "LIQ", -(margin + extra), k, k + 1 + a
    if b < 10 ** 9:
        if exit_ == "book":
            return "BOOKED", float(val[b]), k, k + 1 + b
        return "TARGET", n * ((e - tpx) / e - 0.0007 - 0.0005 * tpx / e), k, k + 1 + b
    return "24h", max(float(val[-1]), -(margin + extra)), k, last


def account(days, fn, strict, xfrac, exit_, fixed=False, floor=1000.0):
    bal, busy, out = D.START, -1, []
    for day, picks in days:
        t0 = int(day.timestamp()) // 60
        if (not fixed and bal < floor) or busy > t0 + 2:
            continue
        best = None
        for rank, sym, H, L, Cl, MH in picks:
            r = fn(sym, H, L, Cl, MH, D.START if fixed else bal, strict, xfrac, exit_)
            if r and (best is None or r[2] < best[1][2]):
                best = (sym, r)
        if best is None:
            continue
        sym, r = best
        bal = bal + r[1] if fixed else max(bal + r[1], 0.0)
        busy = t0 + r[3]
        out.append((day.date(), sym, r[2], r[3], r[0], r[1], bal))
    return out


def both(days, strict, xfrac, exit_, **kw):
    a, b = account(days, n1, strict, xfrac, exit_, **kw), account(days, n2, strict, xfrac, exit_, **kw)
    assert len(a) == len(b) and all(x[:5] == y[:5] and abs(x[5] - y[5]) < 0.01 for x, y in zip(a, b)), (strict, xfrac, exit_, kw)
    return a


def main():
    days = load()
    real = [d for d in days if d[0] >= pd.Timestamp("2026-10-29")]
    y26 = [d for d in days if d[0] >= pd.Timestamp("2026-01-01")]
    cfgs = [(xf, ex) for ex in ("book", "target") for xf in (0.25, 0.51)]
    print("NO STOP-LOSS. Every trade the rule takes at a fixed $3,184 size. STRICT (liquidation on last traded price) .. LENIENT (on mark price).")
    print(f"  {'version':44s} | {'trades':>6s} | {'liquidated':>14s} | {'liquidation costs':>17s} | {'left at 24 h':>12s} | {'total':>21s} | avg per trade")
    for xf, ex in cfgs:
        r = {}
        for st in (True, False):
            df = pd.DataFrame(both(days, st, xf, ex, fixed=True), columns=["day", "sym", "k", "j", "how", "pnl", "bal"])
            r[st] = df
        s, l_ = r[True], r[False]
        nm = f"{'quick book >= $50' if ex == 'book' else '+$500 target'}, extra margin {xf:.0%} (liq ~{'+5.1%' if xf == 0.25 else '+7.8%'})"
        print(f"  {nm:44s} | {len(s):6d} | {(s.how == 'LIQ').sum():3d} .. {(l_.how == 'LIQ').sum():3d} ({(s.how == 'LIQ').mean():.0%}) | "
              f"${-s[s.how == 'LIQ'].pnl.mean():7,.0f} each     | {(s.how == '24h').sum():4d} .. {(l_.how == '24h').sum():3d} | "
              f"{s.pnl.sum():+9,.0f} .. {l_.pnl.sum():+9,.0f} | {s.pnl.mean():+5.0f} .. {l_.pnl.mean():+5.0f}")
    print("  (with the 2.25% stop: quick book -19,321 .. -4,829; as built -40,700 .. -18,224)\n")
    print(f"ACCOUNT from ${D.START:,.0f} (first fill wins, one position at a time, no trades under $1,000), end balance STRICT .. LENIENT:")
    print(f"  {'version':44s} | {'full history':>17s} | {'2026 from Jan 1':>17s} | {'20 real picks':>17s} | 22 monthly starts: under $1,000, above $3,184, median trades before the first liquidation")
    for xf, ex in cfgs:
        cells = []
        for ps in (days, y26, real):
            a, b = both(ps, True, xf, ex), both(ps, False, xf, ex)
            cells.append(f"{a[-1][6]:7,.0f} .. {b[-1][6]:7,.0f}")
        under, above, first = [0, 0], [0, 0], []
        for mo in pd.period_range("2025-01", "2026-10", freq="M"):
            sub = [d for d in days if d[0] >= mo.to_timestamp()]
            for i, st in enumerate((True, False)):
                led = both(sub, st, xf, ex)
                end = led[-1][6] if led else D.START
                under[i] += end < 1000
                above[i] += end > D.START
                if st:
                    liq = [n for n, x in enumerate(led) if x[4] == "LIQ"]
                    first.append(liq[0] if liq else len(led))
        nm = f"{'quick book >= $50' if ex == 'book' else '+$500 target'}, extra margin {xf:.0%}"
        print(f"  {nm:44s} | {cells[0]} | {cells[1]} | {cells[2]} | {under[0]:2d} .. {under[1]:2d},  {above[0]:2d} .. {above[1]:2d},  {int(np.median(first))} trades")
    for st in (True, False):
        led = both(days, st, 0.25, "book")
        print(f"\n  quick book, extra 25%, full history, {'STRICT' if st else 'LENIENT'} -- every trade until the account stops:")
        for d, s, k, j, how, pnl, bal in led[:40]:
            print(f"    {d} {s:12s} {how:6s} held {j - k:4d} min {pnl:+8,.0f}  balance {bal:8,.0f}")
        if len(led) > 40:
            print(f"    ... {len(led)} trades in all, {Counter(x[4] for x in led)}; ends {led[-1][6]:,.0f}")
    print("\n  20 real picks, quick book, extra 25%, STRICT:")
    for d, s, k, j, how, pnl, bal in both(real, True, 0.25, "book"):
        print(f"    {d} {s:12s} {how:6s} held {j - k:4d} min {pnl:+8,.0f}  balance {bal:8,.0f}")


if __name__ == "__main__":
    main()
