"""Take-profit targets vs the 24 h hold, judged per docs/fade_target_grid_prereg.md (committed before this ran).

    PYTHONPATH=. .venv/bin/python scripts/fade_target_grid.py          # fetches Binance 1m once, then judges

Binance minute candles come from the public archive (free; same source as scripts/fade_binance_test.py) and are
reduced at once to one row per trade with every target's R (out/gainer_fade/target_grid_rows.parquet), so a re-run
needs no network. Delta minute candles come from the local store (as scripts/fade_target_compare.py).
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_binance_test as B      # noqa: E402
import fade_live_sim as S          # noqa: E402  (the run-3 account, rules typed in)
import gainer_fade_2025_lab as L   # noqa: E402

DAY = 86400
FEE, MAX_DIST = 0.0005, 0.325
TPS = [0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, None]
ROWS = Path("out/gainer_fade/target_grid_rows.parquet")


def col(tp) -> str:
    return "hold" if tp is None else f"{tp:g}x"


def grid(e: float, dist: float, h: np.ndarray, lo: np.ndarray, c: np.ndarray) -> dict:
    stop = e * (1 + dist)
    s = np.nonzero(h >= stop)[0]
    si = s[0] if len(s) else 10**9
    out = {}
    for tp in TPS:
        ti = 10**9
        if tp is not None:
            tg = e * (1 - tp * dist)
            q = np.nonzero(lo <= tg)[0]
            ti = q[0] if len(q) else 10**9
        if si <= ti and si < 10**9:
            xp, how = max(stop, c[si]), "stop"
        elif ti < 10**9:
            xp, how = tg, "target"
        else:
            xp, how = c[-1], "time"
        out[col(tp)] = (-(xp / e - 1) - FEE - FEE * xp / e) / dist
        out[col(tp) + "_how"] = how
    return out


def binance_one(x) -> dict | None:
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


def delta_one(x) -> dict | None:
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
    bp = bp[bp.dist <= MAX_DIST]
    rows = []
    print(f"Binance: {len(bp)} picks; fetching 1m from the public archive ...", flush=True)
    with ThreadPoolExecutor(12) as ex:
        for k, r in enumerate(ex.map(binance_one, bp.itertuples()), 1):
            if r:
                rows.append(r)
            if k % 500 == 0:
                print(f"  {k}/{len(bp)}", flush=True)
    p = L.panel()
    dp = (p[p.eligible & np.isfinite(p.atrp) & (p.atrp > 0)]
          .sort_values(["time", "prev_change"], ascending=[True, False]).groupby("time").head(2))
    dp = dp[2 * dp.atrp <= MAX_DIST]
    print(f"Delta: {len(dp)} picks from the local store ...", flush=True)
    with ThreadPoolExecutor(3) as ex:
        rows += [r for r in ex.map(delta_one, dp.itertuples()) if r]
    d = pd.DataFrame(rows).sort_values(["venue", "time", "symbol"]).reset_index(drop=True)
    d.to_parquet(ROWS, index=False)
    return d


def sets(d: pd.DataFrame) -> dict[str, pd.DataFrame]:
    b, dl = d[d.venue == "Binance"], d[d.venue == "Delta"]
    out = {f"dev Binance {y}": b[b.year == y] for y in ("2021", "2022", "2023")}
    out["held Binance 2024"] = b[b.year == "2024"]
    out["held Binance 2025-26"] = b[b.year.isin(["2025", "2026"])]
    out["held Delta 2025"] = dl[dl.year == "2025"]
    out["held Delta 2026"] = dl[dl.year == "2026"]
    return out


def account(df: pd.DataFrame, c: str) -> dict:
    days = [g.assign(R=g[c]) for _, g in df.sort_values("time").groupby("time")]
    return S.reference(days, skip_refill_day=True)


def main() -> None:
    d = pd.read_parquet(ROWS) if ROWS.exists() else build()
    lines = [f"{len(d)} trades ({(d.venue == 'Binance').sum()} Binance, {(d.venue == 'Delta').sum()} Delta)"]
    # positive control: "hold" must equal the Binance test's rule A on the same trades
    ref = pd.concat([pd.read_parquet("out/binance/trades.parquet"), pd.read_parquet("out/binance_control/trades.parquet")])
    m = d[d.venue == "Binance"].merge(ref[["symbol", "time", "A", "QUICK"]], on=["symbol", "time"])
    lines.append(f"positive control: {len(m)} Binance trades matched; |hold - A| max {np.abs(m.hold - m.A).max():.2e}, "
                 f"|0.2x - QUICK| max {np.abs(m['0.2x'] - m.QUICK).max():.2e}")
    S_ = sets(d)
    cols = [col(tp) for tp in TPS]
    lines.append(f"\nmean R per trade (win rate) — 'hold' = rule A, 24 h")
    lines.append(f"{'set':22s} {'n':>5s} " + " ".join(f"{c:>13s}" for c in cols))
    for k, v in S_.items():
        lines.append(f"{k:22s} {len(v):5d} " + " ".join(f"{v[c].mean():+7.3f} ({100 * (v[c] > 0).mean():2.0f}%)"
                                                         for c in cols))
    lines.append(f"{'':22s} {'':5s} " + " ".join(f"{'tgt hit':>13s}" for _ in cols))
    for k, v in S_.items():
        lines.append(f"{k:22s} {'':5s} " + " ".join(f"{100 * (v[c + '_how'] == 'target').mean():12.0f}%" for c in cols))
    # step 1
    dev = [k for k in S_ if k.startswith("dev")]
    held = [k for k in S_ if k.startswith("held")]
    pool = pd.concat([S_[k] for k in dev])
    tgt = [c for c in cols if c != "hold"]
    best = max(tgt, key=lambda c: pool[c].mean())
    beats = sum(S_[k][best].mean() > S_[k].hold.mean() for k in dev)
    promoted = beats >= 2
    lines.append(f"\nSTEP 1 dev pooled mean R: " + ", ".join(f"{c} {pool[c].mean():+.4f}" for c in cols))
    lines.append(f"  best target {best}: beats the hold in {beats}/3 dev years -> {'PROMOTED' if promoted else 'not promoted'}")
    # step 2 (reported whether or not promoted, judged only if promoted)
    diffs = {k: S_[k][best] - S_[k].hold for k in held}
    pooled = pd.concat(diffs.values())
    z = pooled.mean() / (pooled.std() / np.sqrt(len(pooled)))
    above = sum(v.mean() > 0 for v in diffs.values())
    lines.append(f"STEP 2 held-out paired diff {best} - hold: " + ", ".join(f"{k.split(' ', 1)[1]} {v.mean():+.4f}"
                                                                         for k, v in diffs.items()))
    lines.append(f"  above zero in {above}/4 sets; pooled {pooled.mean():+.4f}R z {z:+.2f} -> "
                 f"{'PASS' if promoted and above >= 3 and z >= 2 else 'FAIL'}")
    # descriptive: the run-3 account per set and target
    lines.append(f"\nrun-3 account (ladder 10/5/3 @20/35, cap 30%, $1,500, refill < $1,000, $3,000 budget): "
                 f"end $ / refills")
    lines.append(f"{'set':22s} " + " ".join(f"{c:>13s}" for c in cols))
    for k, v in S_.items():
        res = [account(v, c) for c in cols]
        lines.append(f"{k:22s} " + " ".join(f"{r['end']:>8,.0f}/{r['refills']}{'x' if r['done'] else ' '}"
                                            for r in res))
    lines.append("x = refill budget spent (run over)")
    text = "\n".join(lines)
    print(text)
    Path("out/gainer_fade/target_grid.txt").write_text(text + "\n")


if __name__ == "__main__":
    main()
