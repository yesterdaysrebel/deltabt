"""Owner 2026-11-16: "my trades that you analysed, you should find something there with quick entry and exit."

What his 761 real entries showed (before fees): price moved AGAINST him after the entry (-0.16% at 5 min, -0.27% at
10 min, t -2.9), most of all when he went WITH a 15-minute move of 3%+ (-0.67% at 10 min); the entries he took AGAINST a
15-minute move of 1%+ went his way (+0.5..+0.8% in 5-15 min, 93 trades). Both say: after a sharp 15-minute move in
these coins the price comes back within minutes. This script takes that out of his hands and makes it mechanical.

RULE: at a 1m close, if the price has moved X or more over the last 15 minutes, trade AGAINST the move at the next
bar's open and get out at the close N minutes later. No stop, no target. After an entry no new entry on that coin for
15 minutes. Before fees. A second line shows the same with 0.05% slippage on each side.
X in bands 1-2%, 2-3%, 3-5%, 5%+;  N = 5, 10, 15 (the scalper window), 30, 60.

DATA: A  = the owner's own coin-days (where the idea comes from -- not evidence)
      U  = 30 most-traded Delta perps, 2026-08-01..11-15, liquid days          (check 1)
      O  = the same 30 coins, 2026-03-01..07-31, liquid days                   (check 2)
Coin-days of A (and of the careful dataset) are removed from U and O.

    PYTHONPATH=. python scripts/fade_15m_move.py           # -> out/fade_15m_move.txt
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import pullback_5m_trend as P  # noqa: E402

BANDS = ((0.01, 0.02), (0.02, 0.03), (0.03, 0.05), (0.05, 9.0))
HZ = (5, 10, 15, 30, 60)


def events(g, bad=None, sym=""):
    O, C, V, T = (g[c].to_numpy(float) for c in ("open", "close", "volume", "time"))
    n = len(C)
    r15 = np.r_[np.full(15, np.nan), C[15:] / C[:-15] - 1]
    out, nxt = [], 0
    for i in np.flatnonzero(np.abs(np.nan_to_num(r15)) >= BANDS[0][0]):
        k = i + 1
        if i < 300 or i < nxt or k + 60 >= n or not V[k] > 0 or (bad and T[k] // 86400 in bad):
            continue
        s = -np.sign(r15[i])
        out.append((sym, T[k], s, abs(r15[i]), *[s * (C[k + h - 1] / O[k] - 1) for h in HZ]))
        nxt = i + 15
    return out


def data_a():
    for p in sorted(Path("out/manual/candles").glob("*.parquet")):
        d = pd.read_parquet(p).sort_values("time").reset_index(drop=True)
        for _, g in d.groupby((d.time.diff() != 60).cumsum()):
            if len(g) > 400:
                yield p.stem, g.reset_index(drop=True), None


def data_p(folder, seen):
    for c in json.loads(Path("out/pullback/universe.json").read_text())["coins"]:
        sym = c["symbol"]
        p = Path(folder) / f"{sym}.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p).sort_values("time").reset_index(drop=True)
        flat = (d.high == d.low).groupby(d.time // 86400).mean()
        bad = set(flat.index[flat > P.FLAT_MAX]) | {dd for dd in flat.index if (sym, int(dd)) in seen}
        for g in P.segments(d):
            yield sym, g, bad


def table(name, rows, out):
    D = pd.DataFrame(rows, columns=["sym", "t", "s", "mv", *[f"r{h}" for h in HZ]])
    out.append(f"\n===== {name}: {len(D):,} fades of a 15-minute move of 1%+   (mean % of the position, share in profit, t by day)")
    out.append(f"{'15-min move':12} {'trades':>6} | " + " | ".join(f"{'out after ' + str(h) + ' min':^26}" for h in HZ) + " | after 10 min with 0.05% slippage a side")
    for lo, hi in BANDS + ((0.02, 9.0), (0.01, 9.0)):
        d = D[(D.mv >= lo) & (D.mv < hi)]
        if len(d) < 30:
            out.append(f"{f'{lo:.0%}-{hi:.0%}' if hi < 9 else f'{lo:.0%}+':12} {len(d):6d} | -")
            continue
        cells = []
        for h in HZ:
            day = d.groupby(d.t // 86400)[f"r{h}"].sum()
            t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day))) if len(day) > 2 else np.nan
            cells.append(f"{100 * d[f'r{h}'].mean():+7.3f}% {100 * (d[f'r{h}'] > 0).mean():3.0f}% t{t:+6.2f}")
        w, l = d.r10[d.r10 > 0], d.r10[d.r10 <= 0]
        out.append(f"{f'{lo:.0%}-{hi:.0%}' if hi < 9 else f'{lo:.0%}+':12} {len(d):6d} | " + " | ".join(cells) +
                   f" | {100 * d.r10.mean() - 0.10:+.3f}%   (10 min: avg win {100 * w.mean():+.2f}%, avg loss {100 * l.mean():+.2f}%; fades of up-moves {100 * d.r10[d.s < 0].mean():+.3f}%, of down-moves {100 * d.r10[d.s > 0].mean():+.3f}%)")
    return D


def main():
    seen = P.seen_coin_days()
    out = ["FADE A SHARP 15-MINUTE MOVE, OUT WITHIN MINUTES -- before fees. A = the owner's coin-days (source of the idea), U and O = untouched checks."]
    res = {}
    for name, key, gen in (("A  the owner's own coin-days", "A", data_a()), ("U  30 most-traded perps, 2026-08..11, liquid days", "U", data_p("out/pullback", seen)),
                           ("O  the same coins, 2026-03..07, liquid days", "O", data_p("out/breakout_oos", seen))):
        rows = []
        for sym, g, bad in gen:
            rows += events(g, bad, sym)
        res[key] = table(name, rows, out)
    txt = "\n".join(out)
    print(txt)
    Path("out/fade_15m_move.txt").write_text(txt + "\n")
    pd.to_pickle(res, "out/manual/fade_15m_move.pkl")


if __name__ == "__main__":
    main()
