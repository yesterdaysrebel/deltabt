"""Daily premium index (perp price vs index, i.e. the basis) for every Binance perp-month in the daily panel.

    PYTHONPATH=. python scripts/explore_basis_data.py     -> out/binance_paths/premium.parquet (not committed)
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_binance_test as B  # noqa: E402
import fade_families_data as FD  # noqa: E402


def one(key):
    sym, m = key
    k = FD.csv_zip(f"{B.ARCHIVE}/monthly/premiumIndexKlines/{sym}/1d/{sym}-1d-{m}.zip", B.COLS)
    if k is None or k.empty:
        return None
    return pd.DataFrame({"symbol": sym, "time": FD.secs(k.open_time), "prem_close": k.close.astype(float),
                         "prem_mean": (k.open.astype(float) + k.high.astype(float) + k.low.astype(float) + k.close.astype(float)) / 4})


def main() -> None:
    d = FD.daily_all()
    d["m"] = pd.to_datetime(d.time, unit="s").dt.strftime("%Y-%m")
    keys = sorted(set(zip(d.symbol, d.m)))
    print(f"{len(keys)} symbol-months ...", flush=True)
    out = []
    with ThreadPoolExecutor(24) as ex:
        for i, f in enumerate(ex.map(one, keys), 1):
            if f is not None:
                out.append(f)
            if i % 2000 == 0:
                print(f"  {i}/{len(keys)}", flush=True)
    p = pd.concat(out, ignore_index=True)
    p.to_parquet(Path("out/binance_paths") / "premium.parquet", index=False)
    print(f"saved {len(p)} rows, {p.symbol.nunique()} symbols", flush=True)


if __name__ == "__main__":
    main()
