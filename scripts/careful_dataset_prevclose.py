"""Patch for out/careful/dataset.npz: a pick with no trade in minutes 0-2 gets the last traded close of the previous
day (Delta 1m, 23:00-24:00 of D-1) carried into minute 0 -- what the live bot's last traded price is at 00:02. Highs /
lows stay 'no trade'. Picks still without a price are left out by careful_data.load.

    PYTHONPATH=. python scripts/careful_dataset_prevclose.py
"""
import json
import time
import urllib.request
from pathlib import Path
import numpy as np
import pandas as pd

API = "https://api.india.delta.exchange/v2/history/candles"
OUT = Path("out/careful")


def prev_close(sym, t0):
    f = OUT / "candles" / f"{sym}_{t0}_prev.json"
    if not f.exists():
        req = urllib.request.Request(f"{API}?resolution=1m&symbol={sym}&start={t0 - 3600}&end={t0 - 60}",
                                     headers={"User-Agent": "deltabt-research"})
        with urllib.request.urlopen(req, timeout=30) as r:
            f.write_text(json.dumps(json.load(r).get("result") or []))
        time.sleep(0.2)
    rows = sorted(json.loads(f.read_text()), key=lambda c: c["time"])
    rows = [c for c in rows if int(c["time"]) < t0]
    return float(rows[-1]["close"]) if rows else None


def main():
    P = pd.read_csv(OUT / "picks.csv", parse_dates=["day"])
    z = dict(np.load(OUT / "dataset.npz"))
    cl = z["cl"]
    fixed = missing = 0
    for i in np.nonzero(~np.isfinite(cl[:, 2]))[0]:
        t0 = int(P.day[i].timestamp())
        pc = prev_close(P.symbol[i], t0)
        if pc is None:
            missing += 1
            continue
        first = int(np.argmax(np.isfinite(cl[i])))
        cl[i, :first] = pc
        fixed += 1
    z["cl"] = cl
    np.savez_compressed(OUT / "dataset.npz", **z)
    print(f"carried the previous day's last close into {fixed} picks; still without a price: {missing}")


if __name__ == "__main__":
    main()
