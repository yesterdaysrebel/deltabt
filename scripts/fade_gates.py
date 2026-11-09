"""Entry gates for the gainer fade, judged per docs/fade_gates_prereg.md (committed before this script ran).

Development: Binance 2021-2023. Held-out: Binance 2024, Binance 2025-26, Delta 2025, Delta 2026.
Trade R comes from the already-built rows (nothing re-simulated): out/binance/trades.parquet,
out/binance_control/trades.parquet, out/gainer_fade/fade_costed_rows.parquet (Delta, with funding + bot entry).

    PYTHONPATH=. .venv/bin/python scripts/fade_gates.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import gainer_fade_2025_lab as L  # noqa: E402

DAY = 86400
GATES = ("G1 bear market", "G2 big pump", "G3 rejection wick", "G4 new coin", "G5 lone pump", "G6 volume spike")


def features(daily: pd.DataFrame, btc: str, turnover_col: str, data_start: int) -> pd.DataFrame:
    """One row per (symbol, D) with the six gates, from daily candles up to D-1 only."""
    d = daily.sort_values(["symbol", "time"]).drop_duplicates(["symbol", "time"]).reset_index(drop=True)
    d["chg"] = d.close / d.open - 1
    rng = (d.high - d.low).replace(0, np.nan)
    d["wick"] = (d.high - d.close) / rng
    d["to"] = d[turnover_col].astype(float)
    g = d.groupby("symbol")
    d["to_med"] = g.to.transform(lambda s: s.shift(1).rolling(20, min_periods=10).median())  # D-1's view: days D-21..D-2
    first = g.time.transform("min")
    d["first_seen"] = np.where(first <= data_start + 2 * DAY, -10**12, first)  # present at data start = old coin
    liquid = d.to >= 250_000
    breadth = d[liquid].groupby("time").chg.apply(lambda s: (s > 0).mean()).rename("breadth")
    b = d[d.symbol == btc].set_index("time").close
    btc_bear = (b < b.rolling(20).mean()).rename("btc_bear")
    f = d[["symbol", "time", "chg", "wick", "to", "to_med"]].merge(breadth, on="time", how="left")
    f = f.merge(btc_bear, left_on="time", right_index=True, how="left")
    f["time"] += DAY                                                   # every reading is D-1's, used on day D
    f = f.merge(d[["symbol", "first_seen"]].drop_duplicates("symbol"), on="symbol")
    out = pd.DataFrame({"symbol": f.symbol, "time": f.time})
    out["G1 bear market"] = f.btc_bear.fillna(False).astype(bool)
    out["G2 big pump"] = f.chg >= 0.25
    out["G3 rejection wick"] = f.wick >= 0.30
    out["G4 new coin"] = (f.time - f.first_seen) < 90 * DAY
    out["G5 lone pump"] = f.breadth < 0.5
    out["G6 volume spike"] = f.to >= 3 * f.to_med
    return out


def load_sets() -> dict[str, pd.DataFrame]:
    bn = pd.read_parquet("out/binance/daily.parquet")
    bc = pd.read_parquet("out/binance_control/daily.parquet")
    bn_all = pd.concat([bn, bc]).drop_duplicates(["symbol", "time"])
    fb = features(bn_all, "BTCUSDT", "quote_volume", int(bn.time.min()))
    t1 = pd.read_parquet("out/binance/trades.parquet")
    t2 = pd.read_parquet("out/binance_control/trades.parquet")
    for t in (t1, t2):
        t["R_A"], t["R_QUICK"] = t["A"], t["QUICK"]
    t1, t2 = t1.merge(fb, on=["symbol", "time"], how="left"), t2.merge(fb, on=["symbol", "time"], how="left")

    dd = L.daily()
    fd = features(dd, "BTCUSD", "turnover", int(dd.time.min()))
    td = pd.read_parquet(L.OUT / "fade_costed_rows.parquet")
    td["R_A"] = td["A_bot"] + td["A_bot_fund"].fillna(0)
    td["R_QUICK"] = td["QUICK_bot"] + td["QUICK_bot_fund"].fillna(0)
    td = td.merge(fd, on=["symbol", "time"], how="left")
    sets = {f"dev Binance {y}": t1[t1.year == y] for y in ("2021", "2022", "2023")}
    sets["held-out Binance 2024"] = t1[t1.year == "2024"]
    sets["held-out Binance 2025-26"] = t2
    sets["held-out Delta 2025"] = td[td.window == "2025"]
    sets["held-out Delta 2026"] = td[td.window == "2026"]
    return sets


def main() -> None:
    sets = load_sets()
    dev = [k for k in sets if k.startswith("dev")]
    held = [k for k in sets if k.startswith("held")]
    for k, v in sets.items():
        miss = v[list(GATES)].isna().any(axis=1).mean()
        print(f"{k:26s} {len(v):5d} trades; gate inputs missing on {100 * miss:.1f}%")
    for rule in ("A", "QUICK"):
        col = f"R_{rule}"
        print(f"\n==================== {rule} ====================")
        print(f"{'gate':20s} " + " ".join(f"{k.split(' ', 1)[1][:14]:>14s}" for k in dev + held) + "   kept(dev)")
        base = {k: sets[k][col].mean() for k in sets}
        print(f"{'(no gate)':20s} " + " ".join(f"{base[k]:+14.3f}" for k in dev + held))
        promoted = []
        for gname in GATES:
            m = {k: sets[k][sets[k][gname].fillna(False).astype(bool)][col] for k in sets}
            kept = sum(len(m[k]) for k in dev) / sum(len(sets[k]) for k in dev)
            print(f"{gname:20s} " + " ".join(f"{m[k].mean():+14.3f}" for k in dev + held) + f"   {100 * kept:5.0f}%")
            better = sum(m[k].mean() > base[k] for k in dev)
            pooled = pd.concat([m[k] for k in dev]).mean()
            if better >= 2 and pooled > 0 and kept >= 0.30:
                promoted.append(gname)
        print(f"STEP 1 promoted on development data: {promoted or 'none'}")
        cands = list(promoted)
        if len(promoted) >= 2:
            combo = " AND ".join(p.split(" ")[0] for p in promoted)
            for k in sets:
                sets[k][combo] = sets[k][promoted].fillna(False).astype(bool).all(axis=1)
            kept = sum(sets[k][combo].sum() for k in dev) / sum(len(sets[k]) for k in dev)
            if kept >= 0.15:
                cands.append(combo)
        for c in cands:
            m = {k: sets[k][sets[k][c].fillna(False).astype(bool)][col] for k in held}
            ok = sum(m[k].mean() > base[k] and m[k].mean() > 0 for k in held)
            pooled = pd.concat(m.values())
            z = pooled.mean() / (pooled.std() / np.sqrt(len(pooled)))
            verdict = "PASS" if ok >= 3 and z >= 2 else "FAIL"
            print(f"STEP 2 {c}: held-out sets better-and-positive {ok}/4, pooled {pooled.mean():+.3f}R z {z:+.2f}, "
                  f"n {len(pooled)} -> {verdict}")


if __name__ == "__main__":
    main()
