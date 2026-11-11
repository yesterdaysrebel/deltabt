"""Cache the 1m price path (D, D+1, D+2) of every rule A pick on Binance, for the idea search.

    PYTHONPATH=. python scripts/fade_idea_paths.py

Reads the picks already simulated by scripts/fade_binance_test.py (out/binance and out/binance_control
trades.parquet + daily.parquet) and writes out/binance_paths/paths.npz (not committed): per pick, high/low/close
per minute for 3 days (NaN where no candle), plus the pick's readings. Public archive, no key.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_binance_test as B  # noqa: E402

OUT = Path("out/binance_paths")
DAYS = 3
N = DAYS * 1440


def picks() -> pd.DataFrame:
    frames = []
    for d in ("out/binance", "out/binance_control"):
        tr = pd.read_parquet(Path(d) / "trades.parquet")
        day = pd.read_parquet(Path(d) / "daily.parquet")[["symbol", "time", "open", "high", "low", "close"]]
        day = day.drop_duplicates(["symbol", "time"])
        frames.append(tr.merge(day, on=["symbol", "time"], how="left"))
    p = pd.concat(frames, ignore_index=True)
    return p[p.dist <= B.MAX_DIST].sort_values(["time", "rank"]).reset_index(drop=True)


def one(x) -> np.ndarray:
    out = np.full((3, N), np.nan, dtype=np.float32)
    for k in range(DAYS):
        t0 = int(x.time) + k * B.DAY
        day = pd.to_datetime(t0, unit="s").strftime("%Y-%m-%d")
        try:
            b = B.get(f"{B.ARCHIVE}/daily/klines/{x.symbol}/1m/{x.symbol}-1m-{day}.zip")
        except Exception:                                            # noqa: BLE001
            b = None
        if not b:
            continue
        m = B.read_zip(b)
        t = m.open_time.astype("int64").to_numpy() // 1000
        i = (t - int(x.time)) // 60
        ok = (i >= 0) & (i < N)
        for row, col in enumerate(("high", "low", "close")):
            out[row, i[ok]] = m[col].to_numpy(float)[ok]
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p = picks()
    print(f"{len(p)} picks; 1m paths for {DAYS} days each ...", flush=True)
    arr = np.full((len(p), 3, N), np.nan, dtype=np.float32)
    with ThreadPoolExecutor(16) as ex:
        for k, a in enumerate(ex.map(one, p.itertuples())):
            arr[k] = a
            if (k + 1) % 250 == 0:
                print(f"  {k + 1}/{len(p)}", flush=True)
    np.savez_compressed(OUT / "paths.npz", paths=arr)
    p.to_parquet(OUT / "picks.parquet", index=False)
    print(f"saved; picks with a full first day: {int((np.isfinite(arr[:, 2, :1440]).sum(axis=1) >= 1430).sum())}")


if __name__ == "__main__":
    main()
