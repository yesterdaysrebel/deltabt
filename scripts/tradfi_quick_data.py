"""Delta's 26 non-crypto perps (stock tokens, gold, silver, oil): 1m TRADE candles and 1m INDEX candles
(.DE<symbol>, the spot index the perp's mark tracks) from listing to 2026-11-16 00:00 UTC. Public GET only.

    PYTHONPATH=. python scripts/tradfi_quick_data.py          # resumable; out/tradfi_quick/<sym>_{trade,index}.parquet
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

OUT = Path("out/tradfi_quick")
API = "https://api.india.delta.exchange/v2/history/candles"
END = int(pd.Timestamp("2026-11-16", tz="UTC").timestamp())
STEP = 1999 * 60


def _get(params, tries=6):
    for k in range(tries):
        try:
            r = requests.get(API, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (k + 1))
                continue
            return r.json().get("result", []) or []
        except Exception:  # noqa: BLE001
            time.sleep(2 * (k + 1))
    raise RuntimeError(f"gave up {params}")


def series(sym: str, kind: str, start: int) -> str:
    p = OUT / f"{sym}_{kind}.parquet"
    if p.exists():
        return f"{p.name} cached"
    q = sym if kind == "trade" else ".DE" + sym
    rows, t = [], start
    while t < END:
        e = min(END, t + STEP)
        rows += _get({"symbol": q, "resolution": "1m", "start": t, "end": e})
        t = e
        time.sleep(0.4)                                   # ~2.5 req/s per thread, 2 threads
    d = pd.DataFrame(rows)
    if d.empty:
        return f"{p.name} EMPTY"
    d = d.drop_duplicates("time").astype({"time": "int64"}).sort_values("time")
    cols = [c for c in ("time", "open", "high", "low", "close", "volume") if c in d]
    d[cols].to_parquet(p, index=False)
    return f"{p.name} {len(d)} rows {pd.to_datetime(d.time.min(), unit='s')} .. {pd.to_datetime(d.time.max(), unit='s')}"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    prods = json.load(open(OUT / "products.json"))["result"]
    syms = set(Path("out/tradfi/symbols.txt").read_text().split()) | {"KORUBUSD", "AAOIBUSD", "MRNABUSD"}
    jobs = []
    for x in prods:
        if x["symbol"] in syms:
            st = int(pd.Timestamp(x["launch_time"]).timestamp()) // 60 * 60
            jobs += [(x["symbol"], k, st) for k in ("trade", "index")]
    with ThreadPoolExecutor(2) as ex:
        for msg in ex.map(lambda j: series(*j), jobs):
            print(msg, flush=True)


if __name__ == "__main__":
    main()
