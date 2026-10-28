"""Gainer fade: four filters from external sources -- docs/gainer_fade_external_prereg.md
(committed before this file).

    PYTHONPATH=. .venv/bin/python scripts/gainer_fade_external_lab.py oi      # Delta OI:<SYM> 1h history
    PYTHONPATH=. .venv/bin/python scripts/gainer_fade_external_lab.py judge   # once
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))
import gainer_fade_2025_lab as L  # noqa: E402
import gainer_fade_delisted_check as D  # noqa: E402
from deltabt.data.client import DeltaClient  # noqa: E402
from deltabt.data.store import ProductCatalog  # noqa: E402

OI_DIR = L.OUT / "oi"
ALPHA = 0.0125
FILTERS = ("H1", "H2", "H3", "H4")


def rows() -> pd.DataFrame:
    return pd.read_parquet(L.OUT / "delisted_rows_2025_2026.parquet")


def ends() -> dict[str, int]:
    """Last time to fetch per symbol: the delisting for delisted products, else the window end."""
    out = {s: L.WINDOWS["2026"][1] for s in rows().symbol.unique()}
    for s, (_, t) in D.delisted().items():
        if s in out:
            out[s] = min(out[s], t)
    return out


def cmd_oi() -> None:
    OI_DIR.mkdir(parents=True, exist_ok=True)
    cl = DeltaClient(per_second=2.0)
    lo = L.WINDOWS["2025"][0] - 3 * L.DAY

    def one(item):
        sym, end = item
        p = OI_DIR / f"{sym}.parquet"
        if p.exists():
            return sym, "cached"
        try:
            rs = cl.candles("OI:" + sym, "1h", lo, end)
            d = pd.DataFrame([{"time": int(r["time"]), "oi": float(r["close"])} for r in rs if r.get("close") is not None])
            d.to_parquet(p, index=False)
            return sym, len(d)
        except Exception as exc:                                         # noqa: BLE001
            return sym, f"ERROR {exc}"

    with ThreadPoolExecutor(3) as ex:
        for k, (sym, n) in enumerate(ex.map(one, sorted(ends().items())), 1):
            print(f"[{k}] {sym} {n}", flush=True)


def features(r: pd.DataFrame) -> pd.DataFrame:
    r = r.copy()
    # H1: liquid-set median turnover per day (rows hold every liquid coin-day)
    r["day_med_turnover"] = r.groupby("time").prev_turnover.transform("median")
    # H2: abnormal volume = D-1 turnover / mean(D-31 .. D-2) from the daily candles
    live = L.daily()
    extra = D.delisted()
    st = L._store()
    frames = [live]
    for sym, (cv, end) in extra.items():
        d = st.read(sym, "ltp", "1d")
        d = d[(d.time + L.DAY <= end)]
        if len(d):
            frames.append(d.assign(symbol=sym, turnover=d.volume * cv * (d.high + d.low + d.close) / 3)[live.columns])
    days = pd.concat(frames, ignore_index=True).sort_values(["symbol", "time"])
    days["base_turnover"] = days.groupby("symbol").turnover.transform(lambda s: s.shift(1).rolling(30, min_periods=20).mean())
    ab = days[["symbol", "time", "turnover", "base_turnover"]].assign(time=days.time + L.DAY)
    ab["abnormal_vol"] = ab.turnover / ab.base_turnover
    r = r.merge(ab[["symbol", "time", "abnormal_vol"]], on=["symbol", "time"], how="left")
    # H3: funding normalised to 8 h
    cat = ProductCatalog()
    iv = {}
    for s in r.symbol.unique():
        try:
            iv[s] = int(cat.get(s)["funding_interval_seconds"])
        except Exception:                                                 # noqa: BLE001
            iv[s] = 8 * L.HOUR
    r["fund_8h"] = r.fund_at_entry * (8 * L.HOUR) / r.symbol.map(iv)
    # H4: OI change over D-1 (00:00 to 00:00)
    oi_rows = []
    for s in r.symbol.unique():
        p = OI_DIR / f"{s}.parquet"
        if not p.exists():
            continue
        o = pd.read_parquet(p).sort_values("time")
        if o.empty:
            continue
        t, v = o.time.to_numpy("int64"), o.oi.to_numpy(float)
        sub = r[r.symbol == s][["symbol", "time"]].drop_duplicates()
        i1 = np.searchsorted(t, sub.time.to_numpy("int64"), side="right") - 1
        i0 = np.searchsorted(t, sub.time.to_numpy("int64") - L.DAY, side="right") - 1
        ok = (i0 >= 0) & (i1 >= 0)
        a = np.where(ok, v[np.clip(i0, 0, None)], np.nan)
        b = np.where(ok, v[np.clip(i1, 0, None)], np.nan)
        with np.errstate(divide="ignore", invalid="ignore"):
            oi_rows.append(sub.assign(oi_change=np.where(a > 0, b / a - 1, np.nan)))
    if oi_rows:
        r = r.merge(pd.concat(oi_rows), on=["symbol", "time"], how="left")
    else:
        r["oi_change"] = np.nan
    return r


def select(r: pd.DataFrame, f: str | None) -> pd.DataFrame:
    e = r[r.eligible].copy()
    if f == "H1":
        e = e[e.prev_turnover < e.day_med_turnover]
    elif f == "H2":
        med = e.groupby("time").abnormal_vol.transform("median")
        e = e[e.abnormal_vol < med]
    elif f == "H3":
        med = e.groupby("time").fund_8h.transform("median")
        e = e[e.fund_8h >= med]
    elif f == "H4":
        e = e[e.oi_change > 0]
    e = e.sort_values(["time", "prev_change"], ascending=[True, False]).groupby("time").head(L.PICKS).copy()
    e["R"], e["hit"], e["fundR"] = e.R_A, e.hit_A, e.fund_R_A
    return e


def cmd_judge() -> None:
    r = features(rows())
    lines = [f"coverage: abnormal volume known {100*r.abnormal_vol.notna().mean():.0f}% | funding {100*r.fund_8h.notna().mean():.0f}% "
             f"| OI change {100*r.oi_change.notna().mean():.0f}% of liquid coin-days"]
    base = {w: select(r[r.window == w], None) for w in ("2025", "2026")}
    for w in ("2025", "2026"):
        lines.append(f"[{w}] rule A: {L.summary(base[w])}")
    for f in FILTERS:
        diffs = {}
        for w in ("2025", "2026"):
            e = select(r[r.window == w], f)
            diffs[w] = e.R.mean() - base[w].R.mean()
            lines.append(f"  [{w}] {f}: {L.summary(e)} | vs A {diffs[w]:+.3f}R")
        bp = pd.concat([base["2025"], base["2026"]])
        fp = pd.concat([select(r[r.window == w], f) for w in ("2025", "2026")])
        d, p = L.boot_diff(bp, fp)
        ok = diffs["2025"] > 0 and diffs["2026"] > 0 and p <= ALPHA and fp.R.mean() > 0
        lines.append(f"  {f}: better in 2025 {diffs['2025'] > 0}, better in 2026 {diffs['2026'] > 0}, pooled "
                     f"{d:+.3f}R P(diff<=0) {p:.4f} (bar {ALPHA}), pooled mean {fp.R.mean():+.3f}R => {'ADOPT' if ok else 'REJECT'}")
    txt = "\n".join(lines)
    print(txt)
    (L.OUT / "external_judge.txt").write_text(txt + "\n")


if __name__ == "__main__":
    {"oi": cmd_oi, "judge": cmd_judge}[sys.argv[1]]()
