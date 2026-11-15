"""The FROZEN dataset for the careful run-4 simulation (owner 2026-11-15: "a careful simulation over every trade with
available data ... 2-3 runs independent"). Every pick, Delta's OWN 1m trade candles from D 00:00 to D+2 00:00 UTC
(2,881 minutes, so a late fill still gets its full 24 h), from the public /v2/history/candles (cached per pick).
Picks:
  history  2025-01..2026-10: Delta panel, eligible, top 2 by previous-day change, 2 x ATR14% <= 32.5% (rule A's
           universe, gainer_fade_2025_lab.panel) -- all of them, including those with no Binance match;
  real     2026-10-29..11-14: the paper runner's own rule A picks (quick_20x_rules.real_picks list) + ranks from
           out/real_pick_ranks.json.
Output out/careful/dataset.npz + out/careful/picks.csv (day, symbol, rank, source, minutes with a trade).

    PYTHONPATH=. python scripts/careful_dataset.py
"""
from __future__ import annotations
import json
import sys
import time
import urllib.request
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import gainer_fade_2025_lab as G  # noqa: E402

API = "https://api.india.delta.exchange/v2/history/candles"
OUT = Path("out/careful")
CACHE = OUT / "candles"
N = 2881


def fetch(sym: str, t0: int) -> list:
    f = CACHE / f"{sym}_{t0}.json"
    if f.exists():
        return json.loads(f.read_text())
    for attempt in range(4):
        try:
            req = urllib.request.Request(f"{API}?resolution=1m&symbol={sym}&start={t0}&end={t0 + 2 * 86400}",
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


def picks() -> pd.DataFrame:
    g = G.panel()
    g = g[g.eligible & np.isfinite(g.atrp) & (g.atrp > 0)].sort_values(["time", "prev_change"], ascending=[True, False])
    g = g.groupby("time").head(2).copy()
    g = g[2.0 * g.atrp <= 0.325]
    g["rank"] = g.groupby("time").cumcount() + 1
    h = pd.DataFrame(dict(day=pd.to_datetime(g.time, unit="s"), symbol=g.symbol, rank=g["rank"], source="history"))
    raw = [dict(day=x["day"], symbol=x["symbol"], entry=x["entry_price"], stop=x["stop_price"])
           for x in json.load(open("out/fade_quick_full/quick_paper.json")) if x.get("entry_price")]
    raw += [dict(day="2026-11-13T00:00:00+00:00", symbol="HANAUSD", entry=0.01743, stop=0.02216),
            dict(day="2026-11-14T00:00:00+00:00", symbol="SOPHUSD", entry=0.001792, stop=0.002297)]
    raw = [p for p in raw if p["stop"] / p["entry"] - 1 <= 0.325]
    rk = {(d, s): r for d, s, r in json.load(open("out/real_pick_ranks.json"))}
    r = pd.DataFrame([dict(day=pd.Timestamp(p["day"][:10]), symbol=p["symbol"], rank=rk[(p["day"][:10], p["symbol"])],
                           source="real") for p in raw])
    h = h[h.day < r.day.min()]
    return pd.concat([h, r]).sort_values(["day", "rank"]).reset_index(drop=True)


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    P = picks()
    hi = np.full((len(P), N), -np.inf)
    lo = np.full((len(P), N), np.inf)
    cl = np.full((len(P), N), np.nan)
    have = []
    for i, x in enumerate(P.itertuples()):
        t0 = int(pd.Timestamp(x.day).timestamp())
        rows = fetch(x.symbol, t0)
        for c in rows:
            k = (int(c["time"]) - t0) // 60
            if 0 <= k < N:
                hi[i, k], lo[i, k], cl[i, k] = float(c["high"]), float(c["low"]), float(c["close"])
        have.append(int(np.isfinite(cl[i]).sum()))
        if i % 100 == 0:
            print(f"  {i}/{len(P)}", flush=True)
    P["minutes"] = have
    cl = pd.DataFrame(cl.T).ffill().to_numpy().T                    # a minute with no trade keeps the last close
    np.savez_compressed(OUT / "dataset.npz", hi=hi, lo=lo, cl=cl)
    P.to_csv(OUT / "picks.csv", index=False)
    print(f"picks {len(P)} (history {int((P.source == 'history').sum())}, real {int((P.source == 'real').sum())}); "
          f"{P.day.min().date()} .. {P.day.max().date()}")
    print(f"no Delta candles at all: {int((P.minutes == 0).sum())}; under 2,000 of 2,881 minutes traded: "
          f"{int(((P.minutes > 0) & (P.minutes < 2000)).sum())}; median minutes {int(P.minutes.median())}")


if __name__ == "__main__":
    main()
