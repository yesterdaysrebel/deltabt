"""Data for docs/pullback_5m_trend_prereg.md: Delta 1m trade candles, 2026-08-01 .. 2026-11-16 UTC, for the 30 USD
perpetuals with the highest 24 h turnover at download time (stock / metal / oil tokens excluded by Delta's own
`top_tag`). Public GET only. Resumable.

    PYTHONPATH=. python scripts/pullback_data.py       # -> out/pullback/<sym>.parquet, out/pullback/universe.json
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

OUT = Path("out/pullback")
API = "https://api.india.delta.exchange/v2"
START = int(pd.Timestamp("2026-08-01", tz="UTC").timestamp())
END = int(pd.Timestamp("2026-11-16", tz="UTC").timestamp())


def _get(url, params, tries=6):
    for k in range(tries):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (k + 1))
                continue
            return r.json().get("result", []) or []
        except Exception:  # noqa: BLE001
            time.sleep(2 * (k + 1))
    raise RuntimeError(f"gave up {params}")


def universe():
    p = OUT / "universe.json"
    if p.exists():
        return json.loads(p.read_text())
    t = _get(f"{API}/tickers", {"contract_types": "perpetual_futures"})
    tags = sorted({str(x.get("top_tag")) for x in t})
    c = [x for x in t if x.get("top_tag") == "crypto" and x["symbol"].endswith("USD")]
    c.sort(key=lambda x: -float(x.get("turnover_usd") or 0))
    u = dict(fetched=pd.Timestamp.now(tz="UTC").isoformat(), tags_seen=tags,
             coins=[dict(symbol=x["symbol"], turnover_usd=float(x["turnover_usd"])) for x in c[:30]])
    p.write_text(json.dumps(u, indent=1))
    return u


def one(sym):
    p = OUT / f"{sym}.parquet"
    if p.exists():
        return f"{sym} cached"
    rows, x, n = [], START, 0
    while x < END:
        e = min(END, x + 1999 * 60)
        rows += _get(f"{API}/history/candles", {"symbol": sym, "resolution": "1m", "start": x, "end": e})
        x, n = e, n + 1
        time.sleep(0.3)
    d = pd.DataFrame(rows)
    if d.empty:
        return f"{sym} EMPTY"
    d = d.drop_duplicates("time").astype({"time": "int64"}).sort_values("time")
    d = d[(d.time >= START) & (d.time < END)]
    d[["time", "open", "high", "low", "close", "volume"]].to_parquet(p, index=False)
    return f"{sym} {len(d)} rows, {n} requests"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    u = universe()
    print("tags seen:", u["tags_seen"])
    print("universe:", " ".join(f"{c['symbol']}({c['turnover_usd'] / 1e6:.1f}M)" for c in u["coins"]))
    with ThreadPoolExecutor(3) as ex:
        for msg in ex.map(one, [c["symbol"] for c in u["coins"]]):
            print(msg, flush=True)


if __name__ == "__main__":
    main()
