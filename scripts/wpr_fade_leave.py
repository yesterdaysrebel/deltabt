"""The pre-registered test of "fade the %R band exit" -- docs/wpr_fade_leave_prereg.md. Rule, fills, data hygiene and
pass bar are in that file and are not repeated or changed here.

    PYTHONPATH=. python scripts/wpr_fade_leave.py          # -> out/pullback/fade_leave_result.txt
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from deltabt.indicators import wpr  # noqa: E402
from pullback_5m_trend import FLAT_MAX, OUT, SPLIT, seen_coin_days, segments  # noqa: E402

TAKER, MAKER = 0.0005 * 1.18, 0.0002 * 1.18


def simulate(g, step, fill):
    """step = 5 (5m bars, judged) or 1 (1m bars). -> list of (entry time, side, return, how, bars held)"""
    T, O, H, L, C, V = (g[k].to_numpy(float) for k in ("time", "open", "high", "low", "close", "volume"))
    n = len(C) // step
    hb, lb, cb = H[:n * step].reshape(n, step).max(1), L[:n * step].reshape(n, step).min(1), C[step - 1:n * step:step]
    w = wpr(hb, lb, cb, 140)
    wp = np.r_[np.nan, w[:-1]]
    with np.errstate(invalid="ignore"):
        sig = np.where((w < -20) & (wp >= -20), 1, np.where((w > -80) & (wp <= -80), -1, 0))
    strict = fill == "STRICT"
    out, free = [], 0
    for j in np.flatnonzero(sig):
        if j < free:
            continue
        s = int(sig[j])
        e_i = (j + 1) * step                                           # the first 1m bar after the signal bar
        if strict and (e_i >= len(C) or not V[e_i] > 0):
            continue
        k = j + 1
        while k < n and (np.isnan(w[k]) or (-80 < w[k] < -20)):
            k += 1
        x_i = (k + 1) * step
        if k >= n or (strict and x_i >= len(C)):
            break
        back = (w[k] >= -20) if s > 0 else (w[k] <= -80)
        if strict:
            e, x = O[e_i] * (1 + s * 0.0005), O[x_i] * (1 - s * 0.0005)
            ret = s * (x / e - 1) - TAKER - TAKER * x / e
        else:
            e, x = cb[j], cb[k]
            ret = s * (x / e - 1) - MAKER - TAKER * x / e
        out.append((int(T[(j + 1) * step - 1]), s, ret, "BACK" if back else "ACROSS", k - j))
        free = k + 1
    return out


def stats(d):
    day = d.groupby(d.t // 86400).ret.sum()
    t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day))) if len(day) > 2 else np.nan
    return f"{len(d):6d} trades  mean {100 * d.ret.mean():+.4f}%  t {t:+6.2f}  win {100 * (d.ret > 0).mean():3.0f}%  back into the band {100 * (d.how == 'BACK').mean():3.0f}%"


def main():
    uni = json.loads((OUT / "universe.json").read_text())
    seen = seen_coin_days()
    rows = []
    for c in uni["coins"]:
        sym = c["symbol"]
        d = pd.read_parquet(OUT / f"{sym}.parquet").sort_values("time").reset_index(drop=True)
        flat = (d.high == d.low).groupby(d.time // 86400).mean()
        bad = set(flat.index[flat > FLAT_MAX]) | {dd for dd in flat.index if (sym, int(dd)) in seen}
        for g in segments(d):
            for step in (5, 1):
                for fill in ("STRICT", "LENIENT"):
                    rows += [(sym, step, fill, t, s, r, how, hold) for t, s, r, how, hold in simulate(g, step, fill) if t // 86400 not in bad]
    R = pd.DataFrame(rows, columns=["sym", "step", "fill", "t", "side", "ret", "how", "hold"])
    out = ["FADE THE %R BAND EXIT -- the pre-registered test (docs/wpr_fade_leave_prereg.md); data out/pullback (30 coins, 2026-08-01..11-15)", ""]
    verdict = {}
    for step, nm in ((5, "5m bars  (JUDGED)"), (1, "1m bars  (secondary, not judged)")):
        out.append(f"===== {nm}")
        for fill in ("STRICT", "LENIENT"):
            d = R[(R.step == step) & (R.fill == fill)]
            out.append(f"== {fill}")
            out.append(f"   all                  {stats(d)}   median hold {d.hold.median():.0f} bars")
            out.append(f"   2026-08-01..09-22    {stats(d[d.t < SPLIT])}")
            out.append(f"   2026-09-23..11-15    {stats(d[d.t >= SPLIT])}")
            out.append(f"   buys                 {stats(d[d.side > 0])}")
            out.append(f"   sells                {stats(d[d.side < 0])}")
            pc = d.groupby("sym").ret.agg(["mean", "size"])
            pc = pc[pc["size"] >= 30]
            day = d.groupby(d.t // 86400).ret.sum()
            out.append(f"   coins with 30+ trades: {len(pc)}, positive on {int((pc['mean'] > 0).sum())};  BACK trades mean {100 * d.ret[d.how == 'BACK'].mean():+.3f}%, ACROSS trades mean "
                       f"{100 * d.ret[d.how == 'ACROSS'].mean():+.3f}%;  worst trade {100 * d.ret.min():+.2f}%, worst day {100 * day.min():+.2f}%, best day {100 * day.max():+.2f}%")
            if step == 5:
                verdict[fill] = dict(mean=d.ret.mean(), t=day.mean() / (day.std(ddof=1) / np.sqrt(len(day))), h1=d.ret[d.t < SPLIT].mean(), h2=d.ret[d.t >= SPLIT].mean(),
                                     coins=(pc["mean"] > 0).mean(), buys=d.ret[d.side > 0].mean(), sells=d.ret[d.side < 0].mean())
        out.append("")
    S, Ln = verdict["STRICT"], verdict["LENIENT"]
    checks = [("STRICT mean > 0", S["mean"] > 0), ("t >= 2 by day (STRICT)", S["t"] >= 2), ("STRICT > 0 in both halves", S["h1"] > 0 and S["h2"] > 0),
              ("STRICT > 0 on 60%+ of coins", S["coins"] >= 0.6), ("buys and sells each > 0 under LENIENT", Ln["buys"] > 0 and Ln["sells"] > 0)]
    out += [f"   {'PASS' if ok else 'FAIL'}  {nm}" for nm, ok in checks]
    out.append(f"VERDICT (5m): {'PASS' if all(ok for _, ok in checks) else 'FAIL'}" + ("   (LENIENT mean <= 0: dead)" if Ln["mean"] <= 0 else ""))
    txt = "\n".join(out)
    print(txt)
    (OUT / "fade_leave_result.txt").write_text(txt + "\n")
    R.to_parquet(OUT / "fade_leave_trades.parquet", index=False)


if __name__ == "__main__":
    main()
