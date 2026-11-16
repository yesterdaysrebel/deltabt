"""BTCUSD 1m closes (Delta) for every careful-sim pick day, 00:00..24:00 UTC -> out/careful/btc.npz (days, close[1441]).

    PYTHONPATH=. python scripts/careful_dataset_btc.py
"""
import json
import time
import urllib.request
from pathlib import Path
import numpy as np
import pandas as pd

API = "https://api.india.delta.exchange/v2/history/candles"
OUT = Path("out/careful")
P = pd.read_csv(OUT / "picks.csv", parse_dates=["day"])
days = sorted(P.day.unique())
cl = np.full((len(days), 1441), np.nan)
for i, d in enumerate(days):
    t0 = int(pd.Timestamp(d).timestamp())
    f = OUT / "candles" / f"BTCUSD_{t0}_day.json"
    if not f.exists():
        for attempt in range(5):
            try:
                req = urllib.request.Request(f"{API}?resolution=1m&symbol=BTCUSD&start={t0}&end={t0 + 86400}", headers={"User-Agent": "deltabt-research"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    f.write_text(json.dumps(json.load(r).get("result") or []))
                break
            except Exception:                                        # noqa: BLE001
                time.sleep(2 + 3 * attempt)
        time.sleep(0.15)
    for c in json.loads(f.read_text()):
        k = (int(c["time"]) - t0) // 60
        if 0 <= k < 1441:
            cl[i, k] = float(c["close"])
cl = pd.DataFrame(cl.T).ffill().bfill().to_numpy().T
np.savez_compressed(OUT / "btc.npz", days=np.array([pd.Timestamp(d).value for d in days]), close=cl)
print(f"BTC days {len(days)}; days with no data {(~np.isfinite(cl).any(axis=1)).sum()}")
