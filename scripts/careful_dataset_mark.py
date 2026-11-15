"""Delta's MARK-price 1m candles for every careful-sim pick (same 48 h window), -> out/careful/mark.npz (hi, lo, cl,
aligned with out/careful/picks.csv rows). Public /v2/history/candles, symbol MARK:<symbol>; cached per pick.

    PYTHONPATH=. python scripts/careful_dataset_mark.py
"""
import json
import time
import urllib.request
from pathlib import Path
import numpy as np
import pandas as pd

API = "https://api.india.delta.exchange/v2/history/candles"
OUT = Path("out/careful")
N = 2881


def fetch(sym, t0):
    f = OUT / "candles" / f"MARK_{sym}_{t0}.json"
    if f.exists():
        return json.loads(f.read_text())
    for attempt in range(5):
        try:
            req = urllib.request.Request(f"{API}?resolution=1m&symbol=MARK:{sym}&start={t0}&end={t0 + 2 * 86400}",
                                         headers={"User-Agent": "deltabt-research"})
            with urllib.request.urlopen(req, timeout=30) as r:
                rows = json.load(r).get("result") or []
            break
        except Exception:                                            # noqa: BLE001
            time.sleep(2 + 3 * attempt)
    else:
        raise RuntimeError(f"fetch failed {sym} {t0}")
    f.write_text(json.dumps(rows))
    time.sleep(0.15)
    return rows


def main():
    P = pd.read_csv(OUT / "picks.csv", parse_dates=["day"])
    hi, lo, cl = np.full((len(P), N), np.nan), np.full((len(P), N), np.nan), np.full((len(P), N), np.nan)
    have = []
    for i, x in enumerate(P.itertuples()):
        t0 = int(x.day.timestamp())
        for c in fetch(x.symbol, t0):
            k = (int(c["time"]) - t0) // 60
            if 0 <= k < N:
                hi[i, k], lo[i, k], cl[i, k] = float(c["high"]), float(c["low"]), float(c["close"])
        have.append(int(np.isfinite(cl[i]).sum()))
        if i % 100 == 0:
            print(f"  {i}/{len(P)}", flush=True)
    np.savez_compressed(OUT / "mark.npz", hi=hi, lo=lo, cl=cl)
    have = np.array(have)
    print(f"mark candles: picks {len(P)}; none {(have == 0).sum()}; under 2,800 minutes {((have > 0) & (have < 2800)).sum()}; median {int(np.median(have))}")


if __name__ == "__main__":
    main()
