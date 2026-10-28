"""Owner challenge 2026-10-28: does survivorship explain rule A's 2025 failure?

Adds the delisted Delta India perps that still serve price history to the universe of
scripts/gainer_fade_2025_lab.py and re-runs rule A and its random baseline on 2025 and
2026 with the SAME pre-registered rule and test (docs/gainer_fade_2025_prereg.md). The
original result files are left untouched; this writes out/gainer_fade/delisted_*.

Re-listed coins (PEPE -> 1000PEPE, FLOKI, BONK, SHIB) are the same coin under a new
contract: on a day both contracts traded, the old one is dropped so the move is counted once.

    PYTHONPATH=. .venv/bin/python scripts/gainer_fade_delisted_check.py
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

SKIP = {"FTMUSD", "RNDRUSD", "MATICUSD"}          # no 1m history / ended before 2025
RELISTED = {"PEPEUSD": "1000PEPEUSD", "FLOKIUSD": "1000FLOKIUSD",
            "BONKUSD": "1000BONKUSD", "SHIBUSD": "1000SHIBUSD"}


def delisted() -> dict[str, tuple[float, int]]:
    """symbol -> (contract value, delisting time). The REST pager anchors on `end` and stops
    at an empty page, so every fetch must END at the delisting, not today."""
    j = requests.get("https://api.india.delta.exchange/v2/products",
                     params={"contract_types": "perpetual_futures", "states": "expired",
                             "page_size": 500}, timeout=30).json()["result"]
    return {x["symbol"]: (float(x["contract_value"]), int(pd.Timestamp(x["settlement_time"]).timestamp()))
            for x in j if x["symbol"] not in SKIP and x.get("settlement_time")}


def main() -> None:
    extra = delisted()
    st = L._store()
    lo, hi = L.DAILY_FROM, L.WINDOWS["2026"][1] - 60

    def fetch(sym):
        end = min(hi, extra[sym][1])
        d = st.load(sym, "ltp", "1d", lo, end)
        d = d[d.time + L.DAY <= end]                                  # no flat filler after delisting
        n1 = len(st.load(sym, "ltp", "1m", L.WINDOWS["2025"][0] - L.DAY, end)) if end > L.WINDOWS["2025"][0] else 0
        try:
            nf = len(st.load(sym, "funding", "1h", L.WINDOWS["2025"][0] - 3 * L.DAY, end))
        except Exception:                                             # noqa: BLE001
            nf = 0
        return sym, d, n1, nf

    frames = []
    with ThreadPoolExecutor(3) as ex:
        for sym, d, n1, nf in ex.map(fetch, sorted(extra)):
            print(f"  {sym}: daily {len(d)}, 1m {n1}, funding {nf}", flush=True)
            if len(d):
                frames.append(d.assign(symbol=sym, turnover=d.volume * extra[sym][0] * (d.high + d.low + d.close) / 3))
    base = L.daily()
    add = pd.concat(frames, ignore_index=True)[base.columns]
    # one move counted once: drop the old contract on days the new one traded
    for old, new in RELISTED.items():
        new_days = set(base[base.symbol == new].time)
        add = add[~((add.symbol == old) & add.time.isin(new_days))]
    combined = pd.concat([base, add], ignore_index=True)

    orig_daily = L.daily
    L.daily = lambda: combined                                        # noqa: E731
    p = L.panel()
    L.daily = orig_daily
    p = p[p.symbol.isin(extra)]
    jobs = [(sym, g, 8 * L.HOUR) for sym, g in p.groupby("symbol")]
    with ThreadPoolExecutor(6) as ex:
        parts = [x for x in ex.map(L._coin_rows, jobs) if len(x)]
    new_rows = pd.concat(parts, ignore_index=True).drop(columns=["Index"], errors="ignore")
    old_rows = pd.read_parquet(L.OUT / "rows_2025_2026.parquet")
    rows = pd.concat([old_rows, new_rows[old_rows.columns]], ignore_index=True)
    rows.to_parquet(L.OUT / "delisted_rows_2025_2026.parquet", index=False)

    lines = [f"Delisted perps added: {len(extra)} ({', '.join(sorted(extra))}); liquid coin-days added "
             f"{len(new_rows)} (2025: {int((new_rows.window == '2025').sum())}, 2026: {int((new_rows.window == '2026').sum())})"]
    for w in ("2025", "2026"):
        for label, r in (("listed today only (as judged)", old_rows), ("WITH delisted coins", rows)):
            rw = r[r.window == w]
            e = L.select(rw, None)
            rp, rm = L.random_p(rw, e)
            ok = e.R.mean() > 0 and rp <= L.ALPHA_A
            nd = int(e.symbol.isin(extra).sum())
            dl = e[e.symbol.isin(extra)].R
            lines.append(f"[{w}] {label}: {L.summary(e)} | random {rm:+.3f}R, share >= A {rp:.3f} => {'PASS' if ok else 'FAIL'}"
                         + (f" | delisted coins picked {nd} times, their mean {dl.mean():+.3f}R" if nd else ""))
    txt = "\n".join(lines)
    print(txt)
    (L.OUT / "delisted_check.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
