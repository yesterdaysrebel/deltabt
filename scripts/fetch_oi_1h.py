"""Fetch Delta's OI:<coin> 1h history 2024-06-01..2026-10-01 for the 32 screen coins (idea 4).

    PYTHONPATH=. .venv/bin/python scripts/fetch_oi_1h.py

Read-only public endpoint, no keys, 4 requests a second, ~6 requests per coin.
Writes data/candles/<coin>/oi_1h.parquet (gitignored) and
out/sweep/five_min_arm_lab/forced_selling/oi_1h_manifest.json.
"""
import datetime as dt, glob, hashlib, json, pathlib
import pandas as pd
from deltabt.data.client import DeltaClient

START = int(dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc).timestamp())
END = int(dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc).timestamp())
SYMS = sorted(pathlib.Path(f).stem for f in glob.glob("out/sweep/five_min_arm_lab/symbol_screen/trades/*.csv"))
OUT = pathlib.Path("out/sweep/five_min_arm_lab/forced_selling"); OUT.mkdir(parents=True, exist_ok=True)
cl = DeltaClient(per_second=4.0); manifest = {"fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "files": {}}
for s in SYMS:
    rows = cl.candles("OI:" + s, "1h", START, END)
    d = pd.DataFrame([{"time": int(r["time"]), "close": float(r["close"])} for r in rows if r.get("close") is not None])
    p = pathlib.Path(f"data/candles/{s}/oi_1h.parquet"); p.parent.mkdir(parents=True, exist_ok=True); d.to_parquet(p, index=False)
    manifest["files"][s] = dict(rows=len(d), first=int(d.time.min()) if len(d) else None, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    print(f"{s:11s} {len(d):6d} hourly bars from {pd.to_datetime(d.time.min(), unit='s') if len(d) else '-'}", flush=True)
(OUT / "oi_1h_manifest.json").write_text(json.dumps(manifest, indent=1))
