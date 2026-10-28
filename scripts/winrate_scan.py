"""Win-rate scan (owner 2026-10-28: "look for something which has 70-80% win rate in both years").

Small take-profit / wide stop exits reach 70-80% wins on almost any entry, so every cell is
compared with RANDOM entries under the same exit. A cell is interesting only if, in BOTH
2025 and 2026: win rate 70-80%, mean net R > 0, and mean net R above the random entry's.
Exploratory: both years have been looked at before, so anything found needs forward data.

Entries (all at 00:00 UTC on day D, held at most 24 h):
  fade   = short the top 2 previous-day gainers (rule A's picks)
  r_shrt = short every liquid coin-day (= the average random short)
  r_long = long every liquid coin-day (= the average random long)
  g_long = long the top 2 previous-day gainers (momentum, the opposite bet)
Exit: stop k x ATR14% away, take-profit tp x the stop distance; first touch wins, a minute that
touches both counts as the STOP; else the 24 h close. Taker 0.05% each side.

    PYTHONPATH=. .venv/bin/python scripts/winrate_scan.py
"""
from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import gainer_fade_2025_lab as L  # noqa: E402

KS = (1.0, 2.0, 3.0)
TPS = (0.1, 0.2, 0.3, 0.5, 1.0)
FEE = 0.0005


def coin(args):
    sym, g = args
    one = L._store().read(sym, "ltp", "1m")
    if one.empty:
        return []
    t = one.time.to_numpy("int64")
    hi, lo, cl = one.high.to_numpy(float), one.low.to_numpy(float), one.close.to_numpy(float)
    out = []
    for x in g.itertuples():
        a, b = np.searchsorted(t, x.time), np.searchsorted(t, x.time + L.DAY)
        if b <= a or not np.isfinite(x.atrp) or x.atrp <= 0:
            continue
        h, l_, c = hi[a:b], lo[a:b], cl[a:b]
        e = x.open
        rec = {"symbol": sym, "time": x.time, "window": x.window}
        for side in (-1, 1):
            for k in KS:
                dist = k * x.atrp
                stop = e * (1 - side * dist)
                s_hit = (h >= stop) if side < 0 else (l_ <= stop)
                si = int(np.argmax(s_hit)) if s_hit.any() else 10**9
                for tp in TPS:
                    tgt = e * (1 + side * tp * dist)
                    t_hit = (l_ <= tgt) if side < 0 else (h >= tgt)
                    ti = int(np.argmax(t_hit)) if t_hit.any() else 10**9
                    if si <= ti and si < 10**9:
                        xp = max(stop, c[si]) if side < 0 else min(stop, c[si])
                    elif ti < 10**9:
                        xp = tgt
                    else:
                        xp = c[-1]
                    ret = side * (xp / e - 1) - FEE - FEE * xp / e
                    rec[f"{'S' if side < 0 else 'L'}_{k}_{tp}"] = ret / dist
        out.append(rec)
    return out


def main() -> None:
    r = pd.read_parquet(L.OUT / "delisted_rows_2025_2026.parquet")
    cache = L.OUT / "winrate_scan_rows.parquet"
    if cache.exists():
        res = pd.read_parquet(cache)
    else:
        with ProcessPoolExecutor(8) as ex:
            parts = list(ex.map(coin, list(r.groupby("symbol")), chunksize=4))
        res = pd.DataFrame([x for p in parts for x in p])
        res.to_parquet(cache, index=False)
    res = res.merge(r[["symbol", "time", "eligible", "prev_change"]], on=["symbol", "time"])
    picks = (res[res.eligible].sort_values(["time", "prev_change"], ascending=[True, False])
             .groupby("time").head(2))
    sets = {"fade (short top-2 gainers)": (picks, "S"), "random short": (res, "S"),
            "momentum (long top-2 gainers)": (picks, "L"), "random long": (res, "L")}
    rows = []
    for name, (df, side) in sets.items():
        for k in KS:
            for tp in TPS:
                col = f"{side}_{k}_{tp}"
                rec = {"entry": name, "stop_xATR": k, "target_xstop": tp}
                for w in ("2025", "2026"):
                    v = df[df.window == w][col].dropna()
                    rec[f"n_{w}"], rec[f"win_{w}"], rec[f"R_{w}"] = len(v), (v > 0).mean(), v.mean()
                rows.append(rec)
    T = pd.DataFrame(rows)
    rnd = {("S"): T[T.entry == "random short"].set_index(["stop_xATR", "target_xstop"]),
           ("L"): T[T.entry == "random long"].set_index(["stop_xATR", "target_xstop"])}
    def beats(x):
        side = "S" if ("short" in x["entry"] or "fade" in x["entry"]) else "L"
        base = rnd[side].loc[(x["stop_xATR"], x["target_xstop"])]
        return all(x[f"R_{w}"] > base[f"R_{w}"] for w in ("2025", "2026"))
    T["beats_random_both"] = [beats(x) for x in T.to_dict("records")]
    T["hit"] = ((T.win_2025.between(0.70, 0.80)) & (T.win_2026.between(0.70, 0.80))
                & (T.R_2025 > 0) & (T.R_2026 > 0) & T.beats_random_both)
    pd.set_option("display.width", 220)
    show = T.copy()
    for w in ("2025", "2026"):
        show[f"win_{w}"] = (100 * show[f"win_{w}"]).round(0)
        show[f"R_{w}"] = show[f"R_{w}"].round(3)
    print(show[["entry", "stop_xATR", "target_xstop", "win_2025", "R_2025", "win_2026", "R_2026", "beats_random_both", "hit"]].to_string(index=False))
    T.to_csv(L.OUT / "winrate_scan.csv", index=False)
    print(f"\ncells meeting all conditions in both years: {int(T.hit.sum())}")


if __name__ == "__main__":
    main()
