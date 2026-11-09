"""ROE take-profit exits vs the 24 h hold, judged per docs/fade_roe_exit_prereg.md (committed before this ran).

    PYTHONPATH=. .venv/bin/python scripts/fade_roe_grid.py

ROE X is a price target X / L below the entry, L = live.fade.lever_plan leverage at the 10% step. Binance 1m from
the public archive (free), Delta 1m from the local store, reduced at once to one row per trade
(out/gainer_fade/roe_grid_rows.parquet).
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_binance_test as B      # noqa: E402
import fade_live_sim as S          # noqa: E402
import fade_target_grid as T       # noqa: E402
import gainer_fade_2025_lab as L   # noqa: E402
from live.fade import lever_plan   # noqa: E402

DAY, FEE = 86400, 0.0005
ROES = [0.20, 0.30, 0.40, 0.50, 0.60, 0.70]
ROWS = Path("out/gainer_fade/roe_grid_rows.parquet")


def lev_for(dist: float) -> int:
    return lever_plan(0.10, dist, 2.5, liq_dist=(1 + dist) / 0.99 - 1)[1] or 1


def grid(e, dist, h, lo, c) -> dict:
    lev = lev_for(dist)
    stop = e * (1 + dist)
    s = np.nonzero(h >= stop)[0]
    si = s[0] if len(s) else 10**9
    out = {"lev": lev}
    for x in ROES + [None]:
        ti = 10**9
        if x is not None:
            tg = e * (1 - x / lev)
            q = np.nonzero(lo <= tg)[0]
            ti = q[0] if len(q) else 10**9
        if si <= ti and si < 10**9:
            xp, how = max(stop, c[si]), "stop"
        elif ti < 10**9:
            xp, how = tg, "target"
        else:
            xp, how = c[-1], "time"
        k = "hold" if x is None else f"roe{int(round(100 * x))}"
        out[k] = (-(xp / e - 1) - FEE - FEE * xp / e) / dist
        out[k + "_how"] = how
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
            **grid(float(x.open), float(x.dist), h, lo, c)}


def delta_one(x):
    try:
        m = L._store().load(x.symbol, "ltp", "1m", int(x.time), int(x.time) + DAY - 60)
    except Exception:                                                # noqa: BLE001
        return None
    m = m[(m.time >= x.time) & (m.time < x.time + DAY)]
    if m.empty:
        return None
    dist = 2.0 * float(x.atrp)
    return {"venue": "Delta", "symbol": x.symbol, "time": int(x.time), "year": str(x.window), "dist": dist,
            **grid(float(x.open), dist, m.high.to_numpy(float), m.low.to_numpy(float), m.close.to_numpy(float))}


def build() -> pd.DataFrame:
    bp = pd.concat([pd.read_parquet("out/binance/picks.parquet"), pd.read_parquet("out/binance_control/picks.parquet")])
    bp = bp[bp.dist <= 0.325]
    rows = []
    print(f"Binance: {len(bp)} picks ...", flush=True)
    with ThreadPoolExecutor(12) as ex:
        for k, r in enumerate(ex.map(binance_one, bp.itertuples()), 1):
            if r:
                rows.append(r)
            if k % 1000 == 0:
                print(f"  {k}/{len(bp)}", flush=True)
    p = L.panel()
    dp = (p[p.eligible & np.isfinite(p.atrp) & (p.atrp > 0)]
          .sort_values(["time", "prev_change"], ascending=[True, False]).groupby("time").head(2))
    dp = dp[2 * dp.atrp <= 0.325]
    with ThreadPoolExecutor(3) as ex:
        rows += [r for r in ex.map(delta_one, dp.itertuples()) if r]
    d = pd.DataFrame(rows).sort_values(["venue", "time", "symbol"]).reset_index(drop=True)
    d.to_parquet(ROWS, index=False)
    return d


def main() -> None:
    d = pd.read_parquet(ROWS) if ROWS.exists() else build()
    g = pd.read_parquet(T.ROWS)
    m = d.merge(g[["venue", "symbol", "time", "hold"]], on=["venue", "symbol", "time"], suffixes=("", "_grid"))
    lines = [f"{len(d)} trades; positive control: {len(m)} matched the target grid, |hold - grid hold| max "
             f"{np.abs(m.hold - m.hold_grid).max():.2e}; leverage at the 10% step: "
             f"{d.lev.value_counts().sort_index().to_dict()}"]
    sets = T.sets(d)
    cols = [f"roe{int(round(100 * x))}" for x in ROES] + ["hold"]
    lines.append("\nmean R per trade (win rate); target hit rate below")
    lines.append(f"{'set':22s} {'n':>5s} " + " ".join(f"{c:>13s}" for c in cols))
    for k, v in sets.items():
        lines.append(f"{k:22s} {len(v):5d} " + " ".join(f"{v[c].mean():+7.3f} ({100 * (v[c] > 0).mean():2.0f}%)"
                                                         for c in cols))
    for k, v in sets.items():
        lines.append(f"{k:22s} {'':5s} " + " ".join(f"{100 * (v[c + '_how'] == 'target').mean():12.0f}%" for c in cols))
    dev = [k for k in sets if k.startswith("dev")]
    held = [k for k in sets if k.startswith("held")]
    pool = pd.concat([sets[k] for k in dev])
    best = max(cols[:-1], key=lambda c: pool[c].mean())
    beats = sum(sets[k][best].mean() > sets[k].hold.mean() for k in dev)
    promoted = beats >= 2
    lines.append("\nSTEP 1 dev pooled mean R: " + ", ".join(f"{c} {pool[c].mean():+.4f}" for c in cols))
    lines.append(f"  best {best}: beats the hold in {beats}/3 dev years -> {'PROMOTED' if promoted else 'not promoted'}")
    diffs = {k: sets[k][best] - sets[k].hold for k in held}
    pooled = pd.concat(diffs.values())
    z = pooled.mean() / (pooled.std() / np.sqrt(len(pooled)))
    above = sum(v.mean() > 0 for v in diffs.values())
    lines.append("STEP 2 held-out paired diff vs hold: " + ", ".join(f"{k.split(' ', 1)[1]} {v.mean():+.4f}"
                                                                   for k, v in diffs.items()))
    lines.append(f"  above zero in {above}/4; pooled {pooled.mean():+.4f}R z {z:+.2f} -> "
                 f"{'PASS' if promoted and above >= 3 and z >= 2 else 'FAIL'}")
    lines.append("\nrun-3 account with leverage (amendment 1), $1,500, refills: end $ / refills (x = budget spent)")
    lines.append(f"{'set':22s} " + " ".join(f"{c:>13s}" for c in cols))
    for k, v in sets.items():
        res = [S.reference([g_.assign(R=g_[c]) for _, g_ in v.sort_values("time").groupby("time")]) for c in cols]
        lines.append(f"{k:22s} " + " ".join(f"{r['end']:>8,.0f}/{r['refills']}{'x' if r['done'] else ' '}" for r in res))
    text = "\n".join(lines)
    print(text)
    Path("out/gainer_fade/roe_grid.txt").write_text(text + "\n")


if __name__ == "__main__":
    main()
