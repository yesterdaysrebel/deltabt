"""Owner's observation, 2026-11-16: "although the 1 min move for wpr is exhausted, the 5 min has just started; the 5 min
always tries to reach the other side of the wpr band."

Written before the run.
EVENT (up; down mirrored): 5m %R(140) closes above -80 having closed at or below -80 the bar before = the 5m move has
  just started. 1m STATE at that close, %R(140) on 1m: EXHAUSTED above -20 (the 1m has already made its move),
  MIDDLE -80..-20, NOT STARTED at or below -80.
CLAIM 1 (the indicator): after the event the 5m %R reaches the other band (closes at or above -20) --
  "first" = before it closes back at or below -80; "within 140" = at any time in the next 140 5m bars.
CLAIM 2 (the money): buy at the event bar's close; sell at the 5m close where %R is at or above -20; stop at
  RANGE = the 140-bar 5m low at entry (the bottom of the %R window) or PCT = 2.8% (where the owner was liquidated);
  out at the close 140 bars after entry. One position per coin at a time.
  STRICT: entry and target exit 0.05% worse and taker fee (0.059%); stop at the worse of stop and that 5m close, 0.1% worse.
  LENIENT: entry and target exit at the close, maker fee (0.0236%); stop at its price, taker fee.
  A stop inside a bar comes before that bar's close, so the stop wins a shared bar in both.
DATA A: the owner's own coin-days (out/manual/candles, Delta 1m, 66 coins).
DATA B: the careful dataset (Delta's top gainers, 2 days of 1m per pick, 2025-01 .. 2026-11) -- coins of the same kind
  that the owner did not choose.

    PYTHONPATH=. python scripts/wpr_5m_carry.py        # -> out/wpr_5m_carry.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from deltabt.indicators import wpr

TAKER, MAKER = 0.0005 * 1.18, 0.0002 * 1.18
N, TMAX = 140, 140
STATES = ("EXHAUSTED", "MIDDLE", "NOT STARTED")


def segments_a():
    for p in sorted(Path("out/manual/candles").glob("*.parquet")):
        d = pd.read_parquet(p).sort_values("time").reset_index(drop=True)
        for _, g in d.groupby((d.time.diff() != 60).cumsum()):
            a = int(np.argmax((g.time % 300 == 0).to_numpy()))            # start on a 5m boundary
            g = g.iloc[a:]
            if len(g) >= 1200:
                yield p.stem, g.time.to_numpy(), g.high.to_numpy(float), g.low.to_numpy(float), g.close.to_numpy(float)


def segments_b():
    sys.path.insert(0, str(Path(__file__).parent))
    import careful_data as D
    for day, picks in D.load():
        t0 = int(pd.Timestamp(day).timestamp())
        for _, sym, H, L, Cl in picks:
            n = len(Cl) // 5 * 5
            yield sym, t0 + 60 * np.arange(n), np.asarray(H[:n], float), np.asarray(L[:n], float), np.asarray(Cl[:n], float)


def study(sym, T, h, l, c):
    """-> (events, trades): events (day, dir, state, reached first, reached within 140);
    trades (day, dir, state, stop kind, fill, return, how, price move to the exit)."""
    n5 = len(c) // 5
    w1 = wpr(h, l, c, N)
    H5, L5, C5 = h[:n5 * 5].reshape(n5, 5).max(1), l[:n5 * 5].reshape(n5, 5).min(1), c[4:n5 * 5:5]
    w5 = wpr(H5, L5, C5, N)
    ev, tr = [], []
    free = {}
    for j in range(N, n5 - 1):
        a, b = w5[j], w5[j - 1]
        if np.isnan(a) or np.isnan(b):
            continue
        s = 1 if (a > -80 and b <= -80) else (-1 if (a < -20 and b >= -20) else 0)
        if not s:
            continue
        x1 = w1[5 * j + 4]
        if np.isnan(x1):
            continue
        fav = x1 if s > 0 else -100 - x1                                  # in the move's own terms: 0 = the 1m is at the far end
        state = "EXHAUSTED" if fav > -20 else ("MIDDLE" if fav > -80 else "NOT STARTED")
        day = int(T[5 * j + 4] // 86400)
        end = min(n5 - 1, j + TMAX)
        seg = w5[j + 1:end + 1]
        reach = (seg >= -20) if s > 0 else (seg <= -80)
        back = (seg <= -80) if s > 0 else (seg >= -20)
        ir = int(np.argmax(reach)) if reach.any() else 10 ** 9
        ib = int(np.argmax(back)) if back.any() else 10 ** 9
        ev.append((day, s, state, ir < ib, ir < 10 ** 9))
        e0 = C5[j]
        for kind in ("RANGE", "PCT"):
            for fill in ("STRICT", "LENIENT"):
                key = (kind, fill)
                if free.get(key, 0) > j:
                    continue
                strict = fill == "STRICT"
                e = e0 * (1 + s * 0.0005) if strict else e0
                fin = TAKER if strict else MAKER
                if kind == "RANGE":
                    sp = (L5[j - N + 1:j + 1].min() * 0.999) if s > 0 else (H5[j - N + 1:j + 1].max() * 1.001)
                else:
                    sp = e0 * (1 - s * 0.028)
                ret = how = None
                for k in range(j + 1, end + 1):
                    if (L5[k] <= sp) if s > 0 else (H5[k] >= sp):
                        x = (((min(sp, C5[k]) * 0.999) if s > 0 else (max(sp, C5[k]) * 1.001)) if strict else sp)
                        ret, how = s * (x / e - 1) - fin - TAKER * x / e, "STOP"
                        break
                    if (w5[k] >= -20) if s > 0 else (w5[k] <= -80):
                        x = C5[k] * ((1 - s * 0.0005) if strict else 1.0)
                        ret, how = s * (x / e - 1) - fin - (TAKER if strict else MAKER) * x / e, "TARGET"
                        break
                if ret is None:
                    k = end
                    x = C5[k] * ((1 - s * 0.0005) if strict else 1.0)
                    ret, how = s * (x / e - 1) - fin - TAKER * x / e, "TIME"
                free[key] = k + 1
                tr.append((day, s, state, kind, fill, ret, how, s * (C5[k] / e0 - 1)))
    return ev, tr


def report(name, segs, out):
    ev, tr, bars = [], [], 0
    for sym, T, h, l, c in segs:
        bars += len(c)
        a, b = study(sym, T, h, l, c)
        ev += a
        tr += b
    E = pd.DataFrame(ev, columns=["day", "dir", "state", "first", "ever"])
    R = pd.DataFrame(tr, columns=["day", "dir", "state", "kind", "fill", "ret", "how", "move"])
    out.append(f"\n===== {name}: {bars:,} one-minute bars, {len(E):,} events (5m %R just left a band)")
    out.append("CLAIM 1  the 5m %R reaches the other band")
    out.append(f"   {'1m state':12} {'events':>6} {'reaches it first':>16} {'within 140 bars':>15}   up moves / down moves (first)")
    for st in STATES + ("ALL",):
        g = E if st == "ALL" else E[E.state == st]
        if len(g):
            out.append(f"   {st:12} {len(g):6d} {100 * g['first'].mean():15.0f}% {100 * g.ever.mean():14.0f}%   "
                       f"{100 * g['first'][g.dir > 0].mean():.0f}% / {100 * g['first'][g.dir < 0].mean():.0f}%")
    out.append("CLAIM 2  trading it: in at the event, out when the 5m %R reaches the other band. % of the position per trade")
    out.append(f"   {'1m state':12} {'stop':6} | {'trades':>6} {'target':>6} {'stop':>5} {'time':>5} | {'STRICT':>8} {'t':>6} | {'LENIENT':>8} {'t':>6} | "
               f"{'price move when the target is hit':>33} {'when stopped':>12}")
    for st in STATES + ("ALL",):
        for kind in ("RANGE", "PCT"):
            row = f"   {st:12} {kind:6} |"
            for fill in ("STRICT", "LENIENT"):
                g = R[(R.kind == kind) & (R.fill == fill)]
                g = g if st == "ALL" else g[g.state == st]
                if g.empty:
                    continue
                d = g.groupby("day").ret.sum()
                t = d.mean() / (d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 2 else np.nan
                if fill == "STRICT":
                    row += f" {len(g):6d} {100 * (g.how == 'TARGET').mean():5.0f}% {100 * (g.how == 'STOP').mean():4.0f}% {100 * (g.how == 'TIME').mean():4.0f}% |"
                row += f" {100 * g.ret.mean():+7.3f}% {t:+6.2f} |"
            row += f" {100 * g.move[g.how == 'TARGET'].mean():+32.2f}% {100 * g.move[g.how == 'STOP'].mean():+11.2f}%"
            out.append(row)
    return E, R


def main():
    out = ["Owner: when the 1m %R is exhausted and the 5m %R has just started, the 5m goes to the other band. %R(140)."]
    res = {"A": report("A  the owner's own coin-days (Delta 1m, 66 coins)", segments_a(), out),
           "B": report("B  Delta's top gainers 2025-01..2026-11 (careful dataset)", segments_b(), out)}
    txt = "\n".join(out)
    print(txt)
    Path("out/wpr_5m_carry.txt").write_text(txt + "\n")
    pd.to_pickle(res, "out/manual/wpr_5m_carry.pkl")


if __name__ == "__main__":
    main()
