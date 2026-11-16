"""MARK-price 1m candles (Delta's fair price, built from the spot index) for the 30 coins of
out/pullback/universe.json, 2026-03-01 .. 2026-11-16 -- data for the "last price vs fair price" family in
docs/intraday_families_prereg.md. Public GET only. Resumable.

    PYTHONPATH=. python scripts/families_mark_data.py      # -> out/marks/<sym>.parquet
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import pullback_data as D  # noqa: E402

OUT = Path("out/marks")
START = int(pd.Timestamp("2026-03-01", tz="UTC").timestamp())
END = int(pd.Timestamp("2026-11-16", tz="UTC").timestamp())


def one(sym):
    p = OUT / f"{sym}.parquet"
    if p.exists():
        return f"{sym} cached"
    rows, x = [], START
    while x < END:
        e = min(END, x + 1999 * 60)
        rows += D._get(f"{D.API}/history/candles", {"symbol": f"MARK:{sym}", "resolution": "1m", "start": x, "end": e})
        x = e
        time.sleep(0.25)
    d = pd.DataFrame(rows)
    if d.empty:
        return f"{sym} EMPTY"
    d = d.drop_duplicates("time").astype({"time": "int64"}).sort_values("time")
    d[["time", "open", "high", "low", "close"]].to_parquet(p, index=False)
    return f"{sym} {len(d)} rows"


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    coins = [c["symbol"] for c in json.loads(Path("out/pullback/universe.json").read_text())["coins"]]
    with ThreadPoolExecutor(3) as ex:
        for msg in ex.map(one, coins):
            print(msg, flush=True)
