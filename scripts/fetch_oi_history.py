"""Fetch Delta's open-interest history for the symbol screen's 32 perps (2026-10-09).

    PYTHONPATH=. .venv/bin/python scripts/fetch_oi_history.py OUTDIR

WHY. The bundled entry-filter test (docs/entry_filter_bundle_prereg.md) needs
open interest at each entry. Delta has no documented OI history, but the
candle endpoint serves an undocumented `OI:<SYMBOL>` series (found 2026-10-09:
1m/5m/1h, complete minute coverage on the majors, units equal to the ticker's
`oi`). Read-only public endpoint, no keys, paced at 2 requests a second.

Writes data/candles/<SYMBOL>/oi_5m.parquet (gitignored) and OUTDIR/oi_manifest.json
with the fetch time, row counts and sha256 of every file.

CONSISTENCY CHECK FIRST (the series is undocumented and nothing in this repo
ever recorded OI live): on two sampled days for six symbols, the 5m close at
bar t must equal the 1m close at t+240, and the 1h close at t the 5m close at
t+3300. Over 1% mismatched stops the script before the full fetch.
"""
import datetime as dt, glob, hashlib, json, pathlib, sys, time
import pandas as pd
from deltabt.data.client import DeltaClient

OUT = pathlib.Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
START = int(dt.datetime(2025, 12, 12, tzinfo=dt.timezone.utc).timestamp())
END = int(dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc).timestamp())
SYMS = sorted(pathlib.Path(f).stem for f in glob.glob("out/sweep/five_min_arm_lab/symbol_screen/trades/*.csv"))
cl = DeltaClient(per_second=2.0)


def frame(sym, res, start, end):
    rows = cl.candles("OI:" + sym, res, start, end)
    return pd.DataFrame([{"time": int(r["time"]), "close": float(r["close"])} for r in rows if r.get("close") is not None])


# 1) consistency
checks = []; bad = tot = 0
for sym in ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "BEATUSD", "VELVETUSD"):
    for day in ("2026-07-01", "2026-09-10"):
        s = int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp()); e = s + 86400 - 60
        m1, m5, h1 = (frame(sym, r, s, e).set_index("time").close for r in ("1m", "5m", "1h"))
        a = pd.concat([m5.rename("m5"), m1.reindex(m5.index + 240).set_axis(m5.index).rename("m1")], axis=1).dropna()
        b = pd.concat([h1.rename("h1"), m5.reindex(h1.index + 3300).set_axis(h1.index).rename("m5")], axis=1).dropna()
        n_bad = int((abs(a.m5 - a.m1) > 1e-9 * abs(a.m1).clip(lower=1)).sum() + (abs(b.h1 - b.m5) > 1e-9 * abs(b.m5).clip(lower=1)).sum())
        bad += n_bad; tot += len(a) + len(b)
        checks.append(dict(symbol=sym, day=day, m5_vs_m1=len(a), h1_vs_m5=len(b), mismatched=n_bad))
        print(f"check {sym:10s} {day}: 5m-vs-1m {len(a)} pairs, 1h-vs-5m {len(b)} pairs, mismatched {n_bad}", flush=True)
share = bad / max(tot, 1)
print(f"consistency: {bad} of {tot} mismatched ({100 * share:.2f}%)", flush=True)
manifest = dict(fetched_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), series="OI:<SYMBOL> 5m",
                start=START, end=END, consistency=dict(pairs=tot, mismatched=bad, share=share, checks=checks), files={})
if share > 0.01:
    (OUT / "oi_manifest.json").write_text(json.dumps(manifest, indent=1)); sys.exit("STOP: OI series inconsistent across resolutions")

# 2) full fetch
for sym in SYMS:
    d = frame(sym, "5m", START, END)
    p = pathlib.Path(f"data/candles/{sym}/oi_5m.parquet"); d.to_parquet(p, index=False)
    manifest["files"][sym] = dict(rows=len(d), first=int(d.time.min()) if len(d) else None, last=int(d.time.max()) if len(d) else None,
                                  sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    print(f"{sym:11s} {len(d):6d} bars  {pd.to_datetime(d.time.min(), unit='s') if len(d) else '-'} .. {pd.to_datetime(d.time.max(), unit='s') if len(d) else '-'}", flush=True)
(OUT / "oi_manifest.json").write_text(json.dumps(manifest, indent=1))
