"""Flexible leverage with an ROE take-profit, never liquidated — docs/fade_flex_leverage_prereg.md.

    PYTHONPATH=. .venv/bin/python scripts/fade_flex_lev.py
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_binance_test as B      # noqa: E402
import fade_target_grid as T       # noqa: E402
import gainer_fade_2025_lab as L   # noqa: E402

DAY, FEE, MM, MARGIN, SLIP, CAP = 86400, 0.0005, 0.005, 300.0, 0.01, 10
XS = (0.20, 0.30, 0.40, 0.50)
ROWS = Path("out/gainer_fade/flex_lev_rows.parquet")


def flex_lev(dist: float) -> int:
    from_fill = (1 + dist) / (1 - SLIP) - 1
    ok = [lev for lev in range(1, CAP + 1) if 1.0 / lev - MM >= 2 * from_fill]
    return max(ok) if ok else 0


def outcomes(e, dist, h, lo, c) -> dict:
    lev = flex_lev(dist)
    out = {"lev": lev}
    stop, liq = e * (1 + dist), e * (1 + 1 / max(lev, 1) - MM)
    s = np.nonzero(h >= stop)[0]
    si = s[0] if len(s) else 10**9
    for x in XS + (None,):
        ti = 10**9
        if x is not None:
            tg = e * (1 - x / lev)
            q = np.nonzero(lo <= tg)[0]
            ti = q[0] if len(q) else 10**9
        fee = 2 * FEE * lev
        if si <= ti and si < 10**9:
            px = max(stop, c[si])
            roe, how = (-1.0 - fee, "liq") if px >= liq else (-(px / e - 1) * lev - fee, "stop")
        elif ti < 10**9:
            roe, how = x - fee, "target"
        else:
            roe, how = -(c[-1] / e - 1) * lev - fee, "time"
        k = "hold" if x is None else f"roe{int(100 * x)}"
        out[k], out[k + "_how"] = roe, how
    # the 1x hold, for reference
    roe1 = (-(max(stop, c[si]) / e - 1) if si < 10**9 else -(c[-1] / e - 1)) - 2 * FEE
    out["hold1x"] = roe1
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
            **outcomes(float(x.open), float(x.dist), h, lo, c)}


def delta_one(x):
    try:
        m = L._store().load(x.symbol, "ltp", "1m", int(x.time), int(x.time) + DAY - 60)
    except Exception:                                                # noqa: BLE001
        return None
    m = m[(m.time >= x.time) & (m.time < x.time + DAY)]
    if m.empty:
        return None
    dist = 2 * float(x.atrp)
    return {"venue": "Delta", "symbol": x.symbol, "time": int(x.time), "year": str(x.window), "dist": dist,
            **outcomes(float(x.open), dist, m.high.to_numpy(float), m.low.to_numpy(float), m.close.to_numpy(float))}


def build():
    bp = pd.concat([pd.read_parquet("out/binance/picks.parquet"), pd.read_parquet("out/binance_control/picks.parquet")])
    bp = bp[bp.dist <= 0.325]
    print(f"Binance: {len(bp)} picks ...", flush=True)
    with ThreadPoolExecutor(12) as ex:
        rows = [r for r in ex.map(binance_one, bp.itertuples()) if r]
    p = L.panel()
    dp = (p[p.eligible & np.isfinite(p.atrp) & (p.atrp > 0)]
          .sort_values(["time", "prev_change"], ascending=[True, False]).groupby("time").head(2))
    dp = dp[2 * dp.atrp <= 0.325]
    with ThreadPoolExecutor(3) as ex:
        rows += [r for r in ex.map(delta_one, dp.itertuples()) if r]
    d = pd.DataFrame(rows).sort_values(["venue", "time", "symbol"]).reset_index(drop=True)
    d.to_parquet(ROWS, index=False)
    return d


def main():
    d = pd.read_parquet(ROWS) if ROWS.exists() else build()
    g = pd.read_parquet(T.ROWS)[["venue", "symbol", "time", "hold"]]
    m = d.merge(g, on=["venue", "symbol", "time"], suffixes=("", "_g"))
    lines = [f"{len(d)} trades; positive control: 1x hold vs the target grid's hold, max diff "
             f"{np.abs(m.hold1x / m.dist - m.hold_g).max():.2e} (R units)",
             f"leverage chosen: {d.lev.value_counts().sort_index().to_dict()}"]
    sets = T.sets(d)
    cols = [f"roe{int(100 * x)}" for x in XS] + ["hold"]
    lines.append(f"\nmean ROE per trade (win rate) | liquidations | target hit")
    lines.append(f"{'set':22s} {'n':>5s} " + " ".join(f"{c:>15s}" for c in cols) + f" {'1x hold':>9s}")
    for k, v in sets.items():
        lines.append(f"{k:22s} {len(v):5d} " + " ".join(
            f"{v[c].mean():+7.3f} ({100 * (v[c] > 0).mean():2.0f}%)" for c in cols) + f" {v.hold1x.mean():+9.3f}")
        lines.append(f"{'':22s} {'':5s} " + " ".join(
            f"liq {100 * (v[c + '_how'] == 'liq').mean():4.1f}% t{100 * (v[c + '_how'] == 'target').mean():3.0f}%"
            for c in cols))
    dev = [k for k in sets if k.startswith("dev")]
    held = [k for k in sets if k.startswith("held")]
    pool = pd.concat([sets[k] for k in dev])
    best = max(cols[:-1], key=lambda c: pool[c].mean())
    beats = sum(sets[k][best].mean() > sets[k].hold.mean() for k in dev)
    lines.append("\nSTEP 1 dev pooled mean ROE: " + ", ".join(f"{c} {pool[c].mean():+.4f}" for c in cols))
    lines.append(f"  best {best}: beats the same-leverage hold in {beats}/3 -> {'PROMOTED' if beats >= 2 else 'not promoted'}")
    diffs = {k: sets[k][best] - sets[k].hold for k in held}
    pooled = pd.concat(diffs.values())
    z = pooled.mean() / (pooled.std() / np.sqrt(len(pooled)))
    above = sum(v.mean() > 0 for v in diffs.values())
    lines.append("STEP 2 paired diff vs hold: " + ", ".join(f"{k.split(' ', 1)[1]} {v.mean():+.4f}" for k, v in diffs.items()))
    lines.append(f"  above zero {above}/4, pooled {pooled.mean():+.4f} z {z:+.2f} -> "
                 f"{'PASS' if beats >= 2 and above >= 3 and z >= 2 else 'FAIL'}")
    t = pd.to_datetime(d.time, unit="s")
    v = d.assign(y=d.venue + " " + t.dt.year.astype(str), mo=t.dt.month)
    M = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for c, title in (("roe20", "flexible leverage, take profit at 20% ROE, else 24 h"),
                     (best, f"flexible leverage, {best} (chosen on dev)"),
                     ("hold", "flexible leverage, 24 h hold"), ("hold1x", "1x, 24 h hold (reference)")):
        lines.append(f"\n### {title} — $ per month at ${MARGIN:.0f} margin per trade\n")
        lines.append("| | " + " | ".join(M) + " | Year | Months up |")
        lines.append("|---|" + "---|" * 14)
        for y, gy in v.groupby("y"):
            mm = (gy[c] * MARGIN).groupby(gy.mo).sum()
            lines.append(f"| {y} | " + " | ".join(f"{mm[i]:+,.0f}" if i in mm.index else "" for i in range(1, 13))
                         + f" | **{mm.sum():+,.0f}** | {(mm > 0).sum()}/{len(mm)} |")
    text = "\n".join(lines)
    print(text)
    Path("out/gainer_fade/flex_lev.txt").write_text(text + "\n")


if __name__ == "__main__":
    main()
