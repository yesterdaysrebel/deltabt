"""Fetch Binance's public archive for the screen coins: 1m candles (monthly) and 5m metrics (daily).

    PYTHONPATH=. .venv/bin/python scripts/fetch_binance_flow.py [2024-06] [2026-09]

docs/binance_flow_prereg.md. Read-only, no account, from data.binance.vision.
Delta XUSD <-> Binance XUSDT (USD-M perpetual). Months before a coin's Binance
listing are 404 and skipped. Each zip is converted to parquet under
data/binance/<SYMBOL>/ (gitignored) and deleted, so the disk holds ~500 MB,
not the zips. A manifest with row counts and date ranges is written to
out/sweep/five_min_arm_lab/binance_flow/fetch_manifest.json.
"""
import datetime as dt, glob, io, json, pathlib, sys, zipfile
from concurrent.futures import ThreadPoolExecutor
import pandas as pd, requests

BASE = "https://data.binance.vision/data/futures/um"
SYMS = sorted(pathlib.Path(f).stem for f in glob.glob("out/sweep/five_min_arm_lab/symbol_screen/trades/*.csv"))
M0 = sys.argv[1] if len(sys.argv) > 1 else "2024-06"; M1 = sys.argv[2] if len(sys.argv) > 2 else "2026-09"
OUT = pathlib.Path("data/binance"); MAN = pathlib.Path("out/sweep/five_min_arm_lab/binance_flow"); MAN.mkdir(parents=True, exist_ok=True)
KCOLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
S = requests.Session()


def get_csv(url, cols=None):
    r = S.get(url, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        raw = z.read(z.namelist()[0])
    head = raw[:200].decode(errors="ignore").split("\n")[0]
    has_header = not head.split(",")[0].strip().isdigit()
    return pd.read_csv(io.BytesIO(raw), header=0 if has_header else None, names=None if has_header else cols)


def months():
    m = pd.Period(M0, "M"); out = []
    while m <= pd.Period(M1, "M"):
        out.append(str(m)); m += 1
    return out


def days():
    d0 = pd.Timestamp(M0 + "-01"); d1 = pd.Period(M1, "M").end_time.normalize()
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(d0, d1, freq="D")]


def fetch_symbol(sym):
    b = sym.replace("USD", "USDT"); d = OUT / sym; d.mkdir(parents=True, exist_ok=True); info = {"binance": b}
    if not (d / "klines_1m.parquet").exists():
        parts = []
        for m in months():
            k = get_csv(f"{BASE}/monthly/klines/{b}/1m/{b}-1m-{m}.zip", KCOLS)
            if k is not None:
                k.columns = KCOLS[:len(k.columns)]; parts.append(k[["open_time", "volume", "count", "taker_buy_volume", "quote_volume"]])
        if parts:
            k = pd.concat(parts, ignore_index=True)
            k["open_time"] = (k.open_time // (1000 if k.open_time.iloc[0] > 10 ** 12 else 1)).astype("int64")   # ms (or us) -> s
            k = k.drop_duplicates("open_time").sort_values("open_time"); k.to_parquet(d / "klines_1m.parquet", index=False)
    if (d / "klines_1m.parquet").exists():
        k = pd.read_parquet(d / "klines_1m.parquet"); info["klines"] = dict(rows=len(k), first=int(k.open_time.min()), last=int(k.open_time.max()))
    if not (d / "metrics_5m.parquet").exists():
        with ThreadPoolExecutor(8) as pool:
            parts = [x for x in pool.map(lambda day: get_csv(f"{BASE}/daily/metrics/{b}/{b}-metrics-{day}.zip"), days()) if x is not None]
        if parts:
            mt = pd.concat(parts, ignore_index=True)
            mt["time"] = (pd.to_datetime(mt.create_time, utc=True).astype("int64") // 10 ** 9).astype("int64")
            keep = ["time", "sum_open_interest", "sum_open_interest_value", "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
                    "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]
            mt = mt[[c for c in keep if c in mt.columns]].drop_duplicates("time").sort_values("time"); mt.to_parquet(d / "metrics_5m.parquet", index=False)
    if (d / "metrics_5m.parquet").exists():
        mt = pd.read_parquet(d / "metrics_5m.parquet"); info["metrics"] = dict(rows=len(mt), first=int(mt.time.min()), last=int(mt.time.max()))
    return sym, info


if __name__ == "__main__":
    manifest = {"fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "months": [M0, M1], "symbols": {}}
    with ThreadPoolExecutor(4) as pool:
        for sym, info in pool.map(fetch_symbol, SYMS):
            manifest["symbols"][sym] = info
            k, m = info.get("klines"), info.get("metrics")
            print(f"{sym:11s} klines {k['rows'] if k else 0:8d} from {dt.datetime.fromtimestamp(k['first'], dt.timezone.utc):%Y-%m-%d}" if k else f"{sym:11s} klines none",
                  f"| metrics {m['rows'] if m else 0:7d}" + (f" from {dt.datetime.fromtimestamp(m['first'], dt.timezone.utc):%Y-%m-%d}" if m else ""), flush=True)
    (MAN / "fetch_manifest.json").write_text(json.dumps(manifest, indent=1))
