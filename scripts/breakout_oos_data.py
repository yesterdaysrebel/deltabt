"""Data for docs/breakout_3r_prereg.md: the same 30 coins as out/pullback/universe.json, Delta 1m trade candles,
2026-03-01 .. 2026-07-31 UTC. Public GET only. Resumable.

    PYTHONPATH=. python scripts/breakout_oos_data.py       # -> out/breakout_oos/<sym>.parquet
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import pullback_data as D  # noqa: E402

UNIVERSE = Path("out/pullback/universe.json")
D.OUT = Path("out/breakout_oos")
D.START = int(pd.Timestamp("2026-03-01", tz="UTC").timestamp())
D.END = int(pd.Timestamp("2026-08-01", tz="UTC").timestamp())

if __name__ == "__main__":
    D.OUT.mkdir(parents=True, exist_ok=True)
    coins = [c["symbol"] for c in json.loads(UNIVERSE.read_text())["coins"]]
    with ThreadPoolExecutor(3) as ex:
        for msg in ex.map(D.one, coins):
            print(msg, flush=True)
