"""The owner's stated tools as a MECHANICAL rule, on the coins and days the owner chose to trade (2026-11-16).

Owner: "I work with wpr 140, super trend 10,2 ... 1m mostly, 5m for confirmation sometimes."
Data: Delta 1m trade candles in out/manual/candles/ = for every hand trade, the 24 h before it to 1 h after (72 h after
a liquidation). So this is the rule taken EVERY time it fires on the owner's own coin-days -- the favourable case for
the rule (the owner's coin selection is kept), not an out-of-sample test.

Written before the run. Signal on the close of a 1m bar, entry at the next bar's open (that bar must have traded).
TRIGGERS (long; shorts mirrored):
  flip      1m Supertrend(10,2) turns up on this bar
  breakout  1m Supertrend up and 1m %R(140) closes above -20 having closed at or below it the bar before
  dip       1m Supertrend up and 1m %R(140) closes above -80 having closed at or below it the bar before
  each also with "+5m": the last closed 5m Supertrend(10,2) is up too.
EXITS: A  target +1% / stop 2.8% (where the owner was liquidated) / out after 60 min
       B  target +1% / stop 1%   / out after 60 min
       C  no target: out when the 1m Supertrend turns against (next open), stop 2.8%, out after 240 min
One position per coin at a time. Fees: this account's 0.059% taker / 0.0236% maker (GST in).
STRICT: entry 0.05% worse than the open (taker), stop fills at the worse of stop and the bar's close and 0.1% worse,
  target must be passed by 0.1% (maker), stop wins a shared bar, market exits 0.05% worse (taker).
LENIENT: entry at the open (maker), stop at its price, target on touch and wins a shared bar, market exits at the price.
Result = % of the position per trade; t from day totals (trades on one day are not independent).

    PYTHONPATH=. python scripts/hand_rule_test.py      # -> out/manual/rule_test.txt
"""
from pathlib import Path

import numpy as np
import pandas as pd

from deltabt.indicators import supertrend, wpr

DIR = Path("out/manual")
TAKER, MAKER = 0.0005 * 1.18, 0.0002 * 1.18
EXITS = {"A": (0.01, 0.028, 60, False), "B": (0.01, 0.01, 60, False), "C": (None, 0.028, 240, True)}


def segments(sym):
    d = pd.read_parquet(DIR / "candles" / f"{sym}.parquet").sort_values("time").reset_index(drop=True)
    for _, g in d.groupby((d.time.diff() != 60).cumsum()):
        if len(g) < 900:
            continue
        g = g.reset_index(drop=True)
        w = wpr(g.high, g.low, g.close, 140)
        up = -supertrend(g.high, g.low, g.close, 2.0, 10)[1]
        g5 = g.assign(b=g.time // 300 * 300).groupby("b").agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
        up5 = pd.Series(-supertrend(g5.high, g5.low, g5.close, 2.0, 10)[1], index=g5.index)
        last5 = (g.time + 60) // 300 * 300 - 300                       # the last 5m bar closed by this 1m bar's close
        u5 = up5.reindex(last5).to_numpy()
        yield g, w, up, u5


def triggers(w, up, u5):
    """-> {name: array of +1 / -1 / 0 per bar}"""
    n = len(w)
    wp, upp = np.r_[np.nan, w[:-1]], np.r_[np.nan, up[:-1]]
    out = {}
    out["flip"] = np.where((up == 1) & (upp == -1), 1, np.where((up == -1) & (upp == 1), -1, 0))
    out["breakout"] = np.where((up == 1) & (w > -20) & (wp <= -20), 1, np.where((up == -1) & (w < -80) & (wp >= -80), -1, 0))
    out["dip"] = np.where((up == 1) & (w > -80) & (wp <= -80), 1, np.where((up == -1) & (w < -20) & (wp >= -20), -1, 0))
    for k in ("flip", "breakout", "dip"):
        out[k + "+5m"] = np.where(out[k] == u5, out[k], 0)
    for k in out:
        out[k][:300] = 0                                               # indicator warm-up
        out[k][n - 1] = 0
    return out


def run(g, up, sig, ex, fill):
    """-> list of (entry time, side, return as a fraction of the position, how)"""
    tgt, stp, tmax, st_exit = EXITS[ex]
    O, H, L, C, V, T = (g[c].to_numpy() for c in ("open", "high", "low", "close", "volume", "time"))
    strict = fill == "STRICT"
    res, i, n = [], 0, len(g)
    idx = np.flatnonzero(sig)
    free = 0
    for i in idx:
        k = i + 1
        if k < free or k >= n or not (V[k] > 0):
            continue
        s = int(sig[i])
        e = O[k] * (1 + s * 0.0005) if strict else O[k]
        fee_in = TAKER if strict else MAKER
        sp = e * (1 - s * stp)
        tp = e * (1 + s * tgt) if tgt else None
        need = tp * (1 + s * 0.001) if (tgt and strict) else tp
        end = min(n - 1, k + tmax)
        ret = how = None
        for j in range(k, end + 1):
            hit_s = L[j] <= sp if s > 0 else H[j] >= sp
            hit_t = tgt is not None and (H[j] >= need if s > 0 else L[j] <= need)
            if hit_s and (strict or not hit_t):
                x = ((min(sp, C[j]) * 0.999) if s > 0 else (max(sp, C[j]) * 1.001)) if strict else sp
                ret, how = s * (x / e - 1) - fee_in - TAKER * x / e, "STOP"
                break
            if hit_t:
                ret, how = tgt - fee_in - MAKER * (1 + s * tgt), "TARGET"
                break
            if st_exit and j > k and up[j] == -s and j + 1 < n:
                x = O[j + 1] * ((1 - s * 0.0005) if strict else 1.0)
                ret, how, j = s * (x / e - 1) - fee_in - TAKER * x / e, "ST", j + 1
                break
        if ret is None:
            j = end
            x = C[j] * ((1 - s * 0.0005) if strict else 1.0)
            ret, how = s * (x / e - 1) - fee_in - TAKER * x / e, "TIME"
        free = j + 1
        res.append((T[k], s, ret, how))
    return res


def main():
    syms = sorted(p.stem for p in (DIR / "candles").glob("*.parquet"))
    acc = {}
    bars = 0
    for sym in syms:
        for g, w, up, u5 in segments(sym):
            bars += len(g)
            tr = triggers(w, up, u5)
            for name, sig in tr.items():
                for ex in EXITS:
                    for fill in ("STRICT", "LENIENT"):
                        acc.setdefault((name, ex, fill), []).extend((sym, *r) for r in run(g, up, sig, ex, fill))
    out = [f"The owner's tools as a mechanical rule on the owner's own coin-days: {len(syms)} coins, {bars:,} one-minute bars "
           f"({bars / 1440:,.0f} coin-days). % of the position per trade.", "",
           f"{'trigger':12} {'exit':4} | {'trades':>6} | {'STRICT mean':>11} {'t':>6} {'win':>4} | {'LENIENT mean':>12} {'t':>6} {'win':>4} | {'longs':>7} {'shorts':>7} (lenient)"]
    for name in ("flip", "flip+5m", "breakout", "breakout+5m", "dip", "dip+5m"):
        for ex in EXITS:
            row = f"{name:12} {ex:4} |"
            for fill in ("STRICT", "LENIENT"):
                d = pd.DataFrame(acc[(name, ex, fill)], columns=["sym", "t", "side", "ret", "how"])
                day = d.groupby(d.t // 86400).ret.sum()
                t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day)))
                if fill == "STRICT":
                    row += f" {len(d):6d} |"
                row += f" {100 * d.ret.mean():+{11 if fill == 'STRICT' else 12}.3f}% {t:+6.2f} {100 * (d.ret > 0).mean():3.0f}% |"
            row += f" {100 * d.ret[d.side > 0].mean():+6.3f}% {100 * d.ret[d.side < 0].mean():+6.3f}%"
            out.append(row)
    txt = "\n".join(out)
    print(txt)
    (DIR / "rule_test.txt").write_text(txt + "\n")
    pd.to_pickle(acc, DIR / "rule_test.pkl")


if __name__ == "__main__":
    main()
