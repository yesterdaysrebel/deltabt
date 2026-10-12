"""Fetch each new listing's first 45 days (and a later-life block) of Delta 1m LTP, mark and funding.

    PYTHONPATH=. .venv/bin/python scripts/fetch_listings.py

docs/new_listing_drift_prereg.md. Read-only public API via the repo's DeltaClient
(paged, 4 requests a second). Output data/listings/<SYMBOL>/{early,late}_{ltp,mark}_1m.parquet
and funding_1h.parquet (gitignored), plus a manifest under out/.
"""
import datetime as dt, json, pathlib, time
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from deltabt.data.client import DeltaClient
from deltabt.data.store import _to_frame

NOW = int(time.time()); DAY = 86400
T = pd.DataFrame(json.load(open("out/sweep/five_min_arm_lab/symbol_screen/tickers_2026-10-05.json")))
T["launch_dt"] = pd.to_datetime(T.launch, utc=True); T["launch"] = (T.launch_dt.astype("int64") // 10 ** 9).astype("int64")
L = T[(T.launch_dt >= "2024-06-01") & (T.launch_dt <= "2026-09-01")].sort_values("launch")
OUT = pathlib.Path("data/listings"); MAN = pathlib.Path("out/sweep/five_min_arm_lab/new_listing"); MAN.mkdir(parents=True, exist_ok=True)
cl = DeltaClient(per_second=4.0)


def fetch(sym, launch):
    d = OUT / sym; d.mkdir(parents=True, exist_ok=True); info = {}
    try:
        first = cl.earliest_candle_time(sym, "1m", search_from=launch - 7 * DAY, search_to=launch + 7 * DAY)
    except Exception:
        first = None
    start = int(first) if first else launch
    blocks = {"early": (start, min(start + 45 * DAY, NOW)), "late": (start + 90 * DAY, min(start + 135 * DAY, NOW))}
    for name, (s, e) in blocks.items():
        if e - s < 3 * DAY:
            continue
        for series, prefix in (("ltp", ""), ("mark", "MARK:")):
            p = d / f"{name}_{series}_1m.parquet"
            if p.exists():
                continue
            try:
                rows = cl.candles(prefix + sym, "1m", s, e)
            except Exception as ex:
                info[f"{name}_{series}_error"] = repr(ex)[:120]; continue
            if rows:
                _to_frame(rows).to_parquet(p, index=False)
        p = d / f"{name}_ltp_1m.parquet"
        if p.exists():
            x = pd.read_parquet(p); info[name] = dict(rows=len(x), first=int(x.time.min()), last=int(x.time.max()))
    p = d / "funding_1h.parquet"
    if not p.exists():
        try:
            rows = cl.candles("FUNDING:" + sym, "1h", start, min(start + 140 * DAY, NOW))
            if rows:
                _to_frame(rows).to_parquet(p, index=False)
        except Exception as ex:
            info["funding_error"] = repr(ex)[:120]
    info["funding_rows"] = len(pd.read_parquet(p)) if p.exists() else 0
    return sym, int(launch), info


if __name__ == "__main__":
    man = {"fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "listings": {}}
    with ThreadPoolExecutor(3) as pool:
        for sym, launch, info in pool.map(lambda r: fetch(*r), list(zip(L.symbol, L.launch))):
            man["listings"][sym] = dict(launch=launch, **info)
            e = info.get("early"); print(f"{sym:12s} launch {dt.datetime.fromtimestamp(launch, dt.timezone.utc):%Y-%m-%d}  early {e['rows'] if e else 0:6d} rows  "
                                         f"late {info.get('late', {}).get('rows', 0):6d}  funding {info['funding_rows']}", flush=True)
    (MAN / "fetch_manifest.json").write_text(json.dumps(man, indent=1))
