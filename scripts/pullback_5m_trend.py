"""The single pre-registered test of "a 1m pullback inside a 5m trend" -- docs/pullback_5m_trend_prereg.md.
Rule, fills, data hygiene and pass bar are in that file and are not repeated or changed here.

    PYTHONPATH=. python scripts/pullback_5m_trend.py       # -> out/pullback/result.txt, out/pullback/trades.parquet
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from deltabt.indicators import supertrend, wpr  # noqa: E402

OUT = Path("out/pullback")
TAKER, MAKER = 0.0005 * 1.18, 0.0002 * 1.18
SPLIT = int(pd.Timestamp("2026-09-23", tz="UTC").timestamp())
WARM, FLAT_MAX = 300, 0.20


def seen_coin_days():
    """(symbol, UTC day number) already used by data A (owner's coin-days) or data B (careful dataset)."""
    seen = set()
    for p in Path("out/manual/candles").glob("*.parquet"):
        for d in np.unique(pd.read_parquet(p, columns=["time"]).time.to_numpy() // 86400):
            seen.add((p.stem, int(d)))
    import careful_data as D
    for day, picks in D.load():
        d0 = int(pd.Timestamp(day).timestamp()) // 86400
        for _, sym, *_ in picks:
            seen.add((sym, d0))
            seen.add((sym, d0 + 1))
    return seen


def segments(d):
    for _, g in d.groupby((d.time.diff() != 60).cumsum()):
        g = g.iloc[int(np.argmax((g.time % 300 == 0).to_numpy())):]
        if len(g) > WARM + 50:
            yield g.reset_index(drop=True)


def simulate(g, fill):
    """-> list of (entry time, side, return, how)"""
    T, O, H, L, C, V = (g[k].to_numpy(float) for k in ("time", "open", "high", "low", "close", "volume"))
    n = len(C)
    w = wpr(H, L, C, 140)
    u1 = np.nan_to_num(-supertrend(H, L, C, 2.0, 10)[1])
    n5 = n // 5
    u5 = np.nan_to_num(-supertrend(H[:n5 * 5].reshape(n5, 5).max(1), L[:n5 * 5].reshape(n5, 5).min(1), C[4:n5 * 5:5], 2.0, 10)[1])
    j = (np.arange(n) + 1) // 5 - 1
    u5 = np.where(j >= 0, u5[np.clip(j, 0, n5 - 1)], 0)
    with np.errstate(invalid="ignore"):
        st_long = (u1 == 1) & (u5 == 1) & (w > -80) & (w <= -60)
        st_short = (u1 == -1) & (u5 == -1) & (w < -20) & (w >= -40)
    side = np.where(st_long, 1, np.where(st_short, -1, 0))
    onset = (side != 0) & (side != np.r_[0, side[:-1]])
    strict = fill == "STRICT"
    out, i = [], WARM
    idx = np.flatnonzero(onset)
    idx = idx[idx >= WARM]
    free = 0
    for i in idx:
        if i < free:
            continue
        s = int(side[i])
        if strict and (i + 1 >= n or not V[i + 1] > 0):
            continue
        k = i + 1
        while k < n:
            x = w[k]
            if not np.isnan(x) and (x >= -20 or x <= -80):
                break
            k += 1
        if k >= n or (strict and k + 1 >= n):
            break                                                      # still open when the data ends: dropped
        ahead = (w[k] >= -20) if s > 0 else (w[k] <= -80)
        if strict:
            e, x = O[i + 1] * (1 + s * 0.0005), O[k + 1] * (1 - s * 0.0005)
            ret = s * (x / e - 1) - TAKER - TAKER * x / e
        else:
            e, x = C[i], C[k]
            ret = s * (x / e - 1) - MAKER - TAKER * x / e
        out.append((int(T[i]), s, ret, "AHEAD" if ahead else "BEHIND", k - i))
        free = k + 1
    return out


def stats(d):
    day = d.groupby(d.t // 86400).ret.sum()
    t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day))) if len(day) > 2 else np.nan
    return f"{len(d):6d} trades  mean {100 * d.ret.mean():+.4f}%  t {t:+6.2f}  win {100 * (d.ret > 0).mean():3.0f}%  reached the band ahead {100 * (d.how == 'AHEAD').mean():3.0f}%"


def main():
    uni = json.loads((OUT / "universe.json").read_text())
    seen = seen_coin_days()
    rows, bars, days_all, days_flat, days_seen = [], 0, 0, 0, 0
    for c in uni["coins"]:
        sym = c["symbol"]
        p = OUT / f"{sym}.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p).sort_values("time").reset_index(drop=True)
        bars += len(d)
        flat = (d.high == d.low).groupby(d.time // 86400).mean()
        bad_flat = set(flat.index[flat > FLAT_MAX])
        bad_seen = {dd for dd in flat.index if (sym, int(dd)) in seen}
        days_all, days_flat, days_seen = days_all + len(flat), days_flat + len(bad_flat), days_seen + len(bad_seen - bad_flat)
        for g in segments(d):
            for fill in ("STRICT", "LENIENT"):
                for t, s, ret, how, hold in simulate(g, fill):
                    if t // 86400 in bad_flat or t // 86400 in bad_seen:
                        continue
                    rows.append((sym, fill, t, s, ret, how, hold))
    R = pd.DataFrame(rows, columns=["sym", "fill", "t", "side", "ret", "how", "hold"])
    out = ["1m PULLBACK INSIDE A 5m TREND -- the pre-registered test (docs/pullback_5m_trend_prereg.md)",
           f"data: {len(uni['coins'])} coins, {bars:,} one-minute bars, {days_all:,} coin-days; dropped {days_flat:,} for flat minutes, {days_seen:,} already in data A or B",
           "universe: " + " ".join(c["symbol"] for c in uni["coins"]), ""]
    verdict = {}
    for fill in ("STRICT", "LENIENT"):
        d = R[R.fill == fill]
        out.append(f"== {fill}")
        out.append(f"   all                  {stats(d)}   median hold {d.hold.median():.0f} min")
        out.append(f"   2026-08-01..09-22    {stats(d[d.t < SPLIT])}")
        out.append(f"   2026-09-23..11-15    {stats(d[d.t >= SPLIT])}")
        out.append(f"   longs                {stats(d[d.side > 0])}")
        out.append(f"   shorts               {stats(d[d.side < 0])}")
        pc = d.groupby("sym").ret.agg(["mean", "size"])
        pc = pc[pc["size"] >= 30]
        out.append(f"   coins with 30+ trades: {len(pc)}, positive on {int((pc['mean'] > 0).sum())} ({100 * (pc['mean'] > 0).mean():.0f}%)")
        day = d.groupby(d.t // 86400).ret.sum()
        verdict[fill] = dict(mean=d.ret.mean(), t=day.mean() / (day.std(ddof=1) / np.sqrt(len(day))), h1=d.ret[d.t < SPLIT].mean(), h2=d.ret[d.t >= SPLIT].mean(),
                             coins=(pc["mean"] > 0).mean(), longs=d.ret[d.side > 0].mean(), shorts=d.ret[d.side < 0].mean())
    S, Ln = verdict["STRICT"], verdict["LENIENT"]
    checks = [("STRICT mean > 0", S["mean"] > 0), ("t >= 2 by day (STRICT)", S["t"] >= 2), ("STRICT > 0 in both halves", S["h1"] > 0 and S["h2"] > 0),
              ("STRICT > 0 on 60%+ of coins", S["coins"] >= 0.6), ("longs and shorts each > 0 under LENIENT", Ln["longs"] > 0 and Ln["shorts"] > 0)]
    out.append("")
    out += [f"   {'PASS' if ok else 'FAIL'}  {nm}" for nm, ok in checks]
    out.append(f"VERDICT: {'PASS' if all(ok for _, ok in checks) else 'FAIL'}" + ("   (LENIENT mean <= 0: dead, nothing is tuned)" if Ln["mean"] <= 0 else ""))
    txt = "\n".join(out)
    print(txt)
    (OUT / "result.txt").write_text(txt + "\n")
    R.to_parquet(OUT / "trades.parquet", index=False)


if __name__ == "__main__":
    main()
