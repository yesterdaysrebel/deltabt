"""1m MARK-price path around every large Delta funding settlement (|rate one hour before| >= 0.25%).

    PYTHONPATH=. python scripts/explore_settle_data.py   -> out/binance_paths/settle_paths.npz (not committed)

Needs out/binance_paths/delta_settlements.parquet. Window: 65 minutes before to 125 minutes after the stamp.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import option_selling_lab as OL  # noqa: E402  (its rate-limited _get)

PRE, POST, TH = 65, 125, 0.25
N = PRE + POST


def one(x):
    out = np.full((3, N), np.nan, dtype=np.float32)
    try:
        rows = OL._get({"resolution": "1m", "symbol": "MARK:" + x.symbol, "start": int(x.stamp) - PRE * 60,
                        "end": int(x.stamp) + POST * 60 - 60})
    except Exception:                                                # noqa: BLE001
        return out
    for r in rows or []:
        i = (int(r["time"]) - (int(x.stamp) - PRE * 60)) // 60
        if 0 <= i < N:
            out[:, i] = (float(r["high"]), float(r["low"]), float(r["close"]))
    return out


def main() -> None:
    e = pd.read_parquet("out/binance_paths/delta_settlements.parquet")
    e = e[e.rate_before.abs() >= TH].sort_values(["stamp", "symbol"]).reset_index(drop=True)
    print(f"{len(e)} events ...", flush=True)
    arr = np.full((len(e), 3, N), np.nan, dtype=np.float32)
    with ThreadPoolExecutor(8) as ex:
        for k, a in enumerate(ex.map(one, e.itertuples())):
            arr[k] = a
            if (k + 1) % 500 == 0:
                print(f"  {k + 1}/{len(e)}", flush=True)
    np.savez_compressed("out/binance_paths/settle_paths.npz", paths=arr)
    e.to_parquet("out/binance_paths/settle_events.parquet", index=False)
    print(f"saved; events with a price at the stamp: {int(np.isfinite(arr[:, 2, PRE - 1]).sum())}", flush=True)


if __name__ == "__main__":
    main()
