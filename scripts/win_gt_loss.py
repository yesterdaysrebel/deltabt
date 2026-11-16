"""Owner 2026-11-16: "find setup where average loss is less than win" (and: "don't worry about fees from now on").

%R(140) + Supertrend(10, 2) entries on 1m bars with exits built to cut the loss small and let the win run.
ZERO FEES. Written before the run; every cell is printed, nothing is dropped.

ENTRY (long; short mirrored), signal on a 1m close, in at the next bar's open:
  flip      the 1m Supertrend turns up
  breakout  1m Supertrend up and %R closes above -20 having been at or below it
  dip       1m Supertrend up and %R closes above -80 having been at or below it
  each also "+5m": the last closed 5m Supertrend is up too.
STOP: 0.5% / 1% / 2% from the entry, resting from the entry bar on.
WAY OUT if the stop is not hit:
  ride1   the 1m Supertrend turns against -> out at the next open
  ride5   the 5m Supertrend turns against (after having been with the trade) -> out at the next open
  3R      a resting target three stop-distances away
  Everything is closed after 24 h. One position per coin at a time.
FILLS: GENEROUS = entry at the open, stop at its price, target on touch (the target wins a shared bar).
       EXACT    = (added after the first run, because GENEROUS and STRICT landed on opposite sides of zero) entry at
                  the open; the stop wins a shared bar and fills at its price, or at the bar's OPEN when the bar opens
                  beyond it (a gap -- GENEROUS has no loss tail, the trap in CLAUDE.md); target on touch.
       STRICT   = entry 0.05% worse, stop at the worse of stop and that bar's close and 0.1% worse, target must be
                  passed by 0.1%, market exits 0.05% worse, the stop wins a shared bar.
DATA: A = the owner's own coin-days (searching ground); U = the 30 most-traded Delta perps, 2026-08-01..11-15, liquid
days, coin-days of A and B removed (the check: a cell only counts if it holds on U as well).

    PYTHONPATH=. python scripts/win_gt_loss.py         # -> out/win_gt_loss.txt
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import hand_rule_test as R  # noqa: E402
import pullback_5m_trend as P  # noqa: E402
from deltabt.indicators import supertrend, wpr  # noqa: E402

STOPS = (0.005, 0.01, 0.02)
WAYS = ("ride1", "ride5", "3R")
TMAX = 1440


def run(g, up, u5, sig, stp, way, mode):
    """-> list of (entry time, side, return as a fraction of the position)"""
    O, H, L, C, V, T = (g[c].to_numpy(float) for c in ("open", "high", "low", "close", "volume", "time"))
    n, out, free = len(C), [], 0
    strict = mode == "STRICT"
    for i in np.flatnonzero(sig):
        k = i + 1
        if k < free or k >= n or not V[k] > 0:
            continue
        s = int(sig[i])
        e = O[k] * (1 + s * 0.0005) if strict else O[k]
        sp = e * (1 - s * stp)
        tp = e * (1 + s * 3 * stp)
        need = tp * (1 + s * 0.001) if strict else tp
        end = min(n - 1, k + TMAX)
        seen5 = u5[i] == s
        ret, j = None, k
        while j <= end:
            hit_s = L[j] <= sp if s > 0 else H[j] >= sp
            hit_t = way == "3R" and (H[j] >= need if s > 0 else L[j] <= need)
            if hit_s and (mode != "GENEROUS" or not hit_t):
                if strict:
                    x = (min(sp, C[j]) * 0.999) if s > 0 else (max(sp, C[j]) * 1.001)
                elif mode == "EXACT" and j > k:
                    x = min(sp, O[j]) if s > 0 else max(sp, O[j])
                else:
                    x = sp
                ret = s * (x / e - 1)
                break
            if hit_t:
                ret = 3 * stp
                break
            seen5 = seen5 or u5[j] == s
            turn = (way == "ride1" and j > k and up[j] == -s) or (way == "ride5" and seen5 and u5[j] == -s)
            if turn and j + 1 < n:
                x = O[j + 1] * ((1 - s * 0.0005) if strict else 1.0)
                ret, j = s * (x / e - 1), j + 1
                break
            j += 1
        if ret is None:
            j = end
            x = C[j] * ((1 - s * 0.0005) if strict else 1.0)
            ret = s * (x / e - 1)
        free = j + 1
        out.append((T[k], s, ret))
    return out


def data_a():
    for p in sorted((R.DIR / "candles").glob("*.parquet")):
        for g, w, up, u5 in R.segments(p.stem):
            yield g, w, up, u5, None


def data_u():
    seen = P.seen_coin_days()
    for c in json.loads((P.OUT / "universe.json").read_text())["coins"]:
        sym = c["symbol"]
        d = pd.read_parquet(P.OUT / f"{sym}.parquet").sort_values("time").reset_index(drop=True)
        flat = (d.high == d.low).groupby(d.time // 86400).mean()
        bad = set(flat.index[flat > P.FLAT_MAX]) | {dd for dd in flat.index if (sym, int(dd)) in seen}
        for g in P.segments(d):
            w = wpr(g.high, g.low, g.close, 140)
            up = -supertrend(g.high, g.low, g.close, 2.0, 10)[1]
            g5 = g.assign(b=g.time // 300 * 300).groupby("b").agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
            up5 = pd.Series(-supertrend(g5.high, g5.low, g5.close, 2.0, 10)[1], index=g5.index)
            yield g, w, up, up5.reindex((g.time + 60) // 300 * 300 - 300).to_numpy(), bad


def collect(data):
    acc = {}
    for g, w, up, u5, bad in data:
        u5 = np.nan_to_num(u5)
        for name, sig in R.triggers(w, up, u5).items():
            for stp in STOPS:
                for way in WAYS:
                    for mode in ("GENEROUS", "EXACT", "STRICT"):
                        rows = run(g, up, u5, sig, stp, way, mode)
                        if bad:
                            rows = [r for r in rows if r[0] // 86400 not in bad]
                        acc.setdefault((name, stp, way, mode), []).extend(rows)
    return acc


def cell(rows):
    d = pd.DataFrame(rows, columns=["t", "s", "r"])
    day = d.groupby(d.t // 86400).r.sum()
    w, l = d.r[d.r > 0], d.r[d.r <= 0]
    return dict(n=len(d), win=(d.r > 0).mean(), aw=w.mean(), al=l.mean(), mean=d.r.mean(), t=day.mean() / (day.std(ddof=1) / np.sqrt(len(day))))


def main():
    A, U = collect(data_a()), collect(data_u())
    out = ["SETUPS BUILT SO THE AVERAGE LOSS IS SMALLER THAN THE AVERAGE WIN -- %R(140) + Supertrend(10,2), 1m entries, ZERO FEES.",
           "A = the owner's coin-days, U = untouched liquid data. Win / avg win / avg loss / ratio / mean / t at EXACT fills; then the mean at GENEROUS and STRICT.", "",
           f"{'entry':12} {'stop':>5} {'way out':7} | {'A trades':>8} {'win':>4} {'avg win':>8} {'avg loss':>8} {'ratio':>5} {'mean':>8} {'t':>5} {'generous':>8} {'strict':>8} | "
           f"{'U trades':>8} {'win':>4} {'avg win':>8} {'avg loss':>8} {'ratio':>5} {'mean':>8} {'t':>5} {'generous':>8} {'strict':>8} | EXACT > 0 on both"]
    for name in ("flip", "flip+5m", "breakout", "breakout+5m", "dip", "dip+5m"):
        for stp in STOPS:
            for way in WAYS:
                row = f"{name:12} {stp:5.1%} {way:7} |"
                ok = True
                for D in (A, U):
                    g, ge, s = cell(D[(name, stp, way, "EXACT")]), cell(D[(name, stp, way, "GENEROUS")]), cell(D[(name, stp, way, "STRICT")])
                    row += (f" {g['n']:8d} {100 * g['win']:3.0f}% {100 * g['aw']:+7.2f}% {100 * g['al']:+7.2f}% {-g['aw'] / g['al']:5.1f} {100 * g['mean']:+7.3f}% {g['t']:+5.1f} "
                            f"{100 * ge['mean']:+7.3f}% {100 * s['mean']:+7.3f}% |")
                    ok = ok and g["mean"] > 0
                out.append(row + (" YES" if ok else ""))
    txt = "\n".join(out)
    print(txt)
    Path("out/win_gt_loss.txt").write_text(txt + "\n")
    pd.to_pickle((A, U), "out/manual/win_gt_loss.pkl")


if __name__ == "__main__":
    main()
