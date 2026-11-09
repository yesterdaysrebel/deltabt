"""High leverage + ROE take-profit on rule A, judged per docs/fade_high_leverage_roe_prereg.md.

    PYTHONPATH=. .venv/bin/python scripts/fade_high_lev_roe.py

Per trade and setting: ROE on a fixed $300 isolated margin. Binance 1m from the public archive (free), Delta 1m
from the local store; reduced at once to out/gainer_fade/high_lev_rows.parquet.
"""
from __future__ import annotations

import itertools
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_binance_test as B      # noqa: E402
import fade_target_grid as T       # noqa: E402
import gainer_fade_2025_lab as L   # noqa: E402

DAY, FEE, MM, MARGIN = 86400, 0.0005, 0.005, 300.0
LEVS, ROES, STOPS = (5, 10, 20), (0.10, 0.20, 0.30, 0.50, 0.75, 1.00), ("liq", "half")
ROWS = Path("out/gainer_fade/high_lev_rows.parquet")


def key(lev, x, st):
    return f"L{lev}_X{int(round(100 * x))}_{st}"


def outcomes(e, h, lo, c) -> dict:
    out = {}
    for lev, x, st in itertools.product(LEVS, ROES, STOPS):
        liq = e * (1 + 1 / lev - MM)
        stop = liq if st == "liq" else e * (1 + 0.5 / lev)
        tg = e * (1 - x / lev)
        s = np.nonzero(h >= stop)[0]
        t = np.nonzero(lo <= tg)[0]
        si = s[0] if len(s) else 10**9
        ti = t[0] if len(t) else 10**9
        fee = 2 * FEE * lev
        if si <= ti and si < 10**9:
            if st == "liq":
                roe, how = -1.0 - fee, "liq"
            else:
                px = max(stop, c[si])
                roe, how = (max(-(px / e - 1) * lev, -1.0) - fee), ("liq" if px >= liq else "stop")
        elif ti < 10**9:
            roe, how = x - fee, "target"
        else:
            roe, how = -(c[-1] / e - 1) * lev - fee, "time"
            if c[-1] >= liq:
                roe, how = -1.0 - fee, "liq"
        out[key(lev, x, st)] = roe
        out[key(lev, x, st) + "_how"] = how
    return out


def binance_one(x):
    day = pd.to_datetime(x.time, unit="s").strftime("%Y-%m-%d")
    try:
        b = B.get(f"{B.ARCHIVE}/daily/klines/{x.symbol}/1m/{x.symbol}-1m-{day}.zip")
    except Exception:                                                # noqa: BLE001
        return None
    if not b:
        return None
    m = B.read_zip(b)
    t = m.open_time.astype("int64").to_numpy() // 1000
    keep = (t >= x.time) & (t < x.time + DAY)
    h, lo, c = (m[k].to_numpy(float)[keep] for k in ("high", "low", "close"))
    if not len(c):
        return None
    return {"venue": "Binance", "symbol": x.symbol, "time": int(x.time), "year": str(x.year), "dist": float(x.dist),
            **outcomes(float(x.open), h, lo, c)}


def delta_one(x):
    try:
        m = L._store().load(x.symbol, "ltp", "1m", int(x.time), int(x.time) + DAY - 60)
    except Exception:                                                # noqa: BLE001
        return None
    m = m[(m.time >= x.time) & (m.time < x.time + DAY)]
    if m.empty:
        return None
    return {"venue": "Delta", "symbol": x.symbol, "time": int(x.time), "year": str(x.window), "dist": 2 * float(x.atrp),
            **outcomes(float(x.open), m.high.to_numpy(float), m.low.to_numpy(float), m.close.to_numpy(float))}


def build():
    bp = pd.concat([pd.read_parquet("out/binance/picks.parquet"), pd.read_parquet("out/binance_control/picks.parquet")])
    bp = bp[bp.dist <= 0.325]
    rows = []
    print(f"Binance: {len(bp)} picks ...", flush=True)
    with ThreadPoolExecutor(12) as ex:
        rows += [r for r in ex.map(binance_one, bp.itertuples()) if r]
    p = L.panel()
    dp = (p[p.eligible & np.isfinite(p.atrp) & (p.atrp > 0)]
          .sort_values(["time", "prev_change"], ascending=[True, False]).groupby("time").head(2))
    dp = dp[2 * dp.atrp <= 0.325]
    with ThreadPoolExecutor(3) as ex:
        rows += [r for r in ex.map(delta_one, dp.itertuples()) if r]
    d = pd.DataFrame(rows).sort_values(["venue", "time", "symbol"]).reset_index(drop=True)
    d.to_parquet(ROWS, index=False)
    return d


def monthly(v: pd.DataFrame, k: str) -> pd.Series:
    return (v[k] * MARGIN).groupby(pd.to_datetime(v.time, unit="s").dt.to_period("M")).sum()


def main():
    d = pd.read_parquet(ROWS) if ROWS.exists() else build()
    sets = T.sets(d)
    ins = sets.pop("held Delta 2026")
    keys = [key(*k) for k in itertools.product(LEVS, ROES, STOPS)]
    lines = [f"{len(d)} trades; ${MARGIN:.0f} isolated margin per trade, 2 a day"]
    lines.append("\nDelta 2026 (IN-SAMPLE: where the owner found it) — every setting")
    lines.append(f"{'setting':16s} {'win':>5s} {'liq':>5s} {'total $':>10s} {'$/month':>9s} {'months up':>10s} {'worst month':>12s}")
    stats = {}
    for k in keys:
        m = monthly(ins, k)
        stats[k] = m.sum()
        lines.append(f"{k:16s} {100 * (ins[k] > 0).mean():4.0f}% {100 * (ins[k + '_how'] == 'liq').mean():4.0f}% "
                     f"{m.sum():>10,.0f} {m.mean():>9,.0f} {(m > 0).sum():>4d}/{len(m):<5d} {m.min():>12,.0f}")
    best = max(keys, key=lambda k: stats[k])
    lines.append(f"\nchosen on Delta 2026: {best} (${stats[best]:,.0f})")
    lines.append(f"\nthe chosen setting on the six OTHER periods")
    lines.append(f"{'period':22s} {'trades':>6s} {'win':>5s} {'liq':>5s} {'total $':>10s} {'$/month':>9s} {'months up':>10s} {'worst':>9s}")
    allm, pos = [], 0
    for name, v in sets.items():
        m = monthly(v, best)
        allm.append(m)
        pos += m.sum() > 0
        lines.append(f"{name:22s} {len(v):6d} {100 * (v[best] > 0).mean():4.0f}% {100 * (v[best + '_how'] == 'liq').mean():4.0f}% "
                     f"{m.sum():>10,.0f} {m.mean():>9,.0f} {(m > 0).sum():>4d}/{len(m):<5d} {m.min():>9,.0f}")
    am = pd.concat(allm)
    lines.append(f"all six: {len(am)} months, median ${am.median():,.0f}, mean ${am.mean():,.0f}, up {100 * (am > 0).mean():.0f}%")
    ok = pos >= 5 and am.median() > 0 and am.mean() >= 1000
    lines.append(f"RULE: positive in {pos}/6 (need 5), median month ${am.median():,.0f} (need > 0), mean month "
                 f"${am.mean():,.0f} (need >= $1,000) -> {'PASS' if ok else 'FAIL'}")
    lines.append("\nevery setting on the six other periods (total $, months up)")
    for k in keys:
        mm = pd.concat([monthly(v, k) for v in sets.values()])
        tot = [monthly(v, k).sum() for v in sets.values()]
        lines.append(f"{k:16s} total ${sum(tot):>10,.0f}  periods up {sum(t > 0 for t in tot)}/6  months up "
                     f"{100 * (mm > 0).mean():3.0f}%  mean month ${mm.mean():>7,.0f}  worst ${mm.min():>7,.0f}")
    text = "\n".join(lines)
    print(text)
    Path("out/gainer_fade/high_lev_roe.txt").write_text(text + "\n")


if __name__ == "__main__":
    main()
