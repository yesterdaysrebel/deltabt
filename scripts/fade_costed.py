"""Rule A and QUICK on Delta 2025-2026 WITH the costs the earlier account tables left out (owner 2026-11-09,
after the independent review: "run the script on year 2025, 2026 and present numbers here").

Same picks (top 2 previous-day gainers, stop 2 x ATR14%, stops <= 32.5%), same fills and fees as
scripts/fade_target_compare.py, plus:
  ENTRY    the paper/real bot's entry (live/fade.py): the CLOSE of the first traded minute from 00:00 UTC
           (within 15 min; a coin with no trade by then is skipped); the trade starts the minute after.
  FUNDING  Delta's own funding history for each coin, every funding stamp strictly inside the holding time,
           in R exactly as gainer_fade_2025_lab.py (sum of rate% / 100 / stop distance; a short RECEIVES a
           positive rate).
Accounts: scripts/fade_ladder_account.py (day-sized, 45% cap, compounding), $1,500 each year.
Periods: 2025 (replication), 2026 Jan 15-Jul 31 (the stop width was tuned here), 2026 Aug 1-Oct 27 (sealed hold-out).

    PYTHONPATH=. .venv/bin/python scripts/fade_costed.py
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_ladder_account as LA  # noqa: E402
import gainer_fade_2025_lab as L  # noqa: E402
from deltabt.costs import funding_timestamps  # noqa: E402
from deltabt.data.store import ProductCatalog  # noqa: E402
from deltabt.engine import _funding_lookup  # noqa: E402

FEE, QUICK_TP, ENTRY_WINDOW, HOLDOUT = 0.0005, 0.2, 15 * 60, L.TS("2026-08-01")


def one_trade(t, hi, lo, cl, start, end, e, dist, tp):
    """Short from bar index `start` (first bar AFTER the entry) to `end`; returns (net R, exit time, how)."""
    h, l_, c = hi[start:end], lo[start:end], cl[start:end]
    if len(c) == 0:
        return None
    stop = e * (1 + dist)
    s = np.nonzero(h >= stop)[0]
    si = s[0] if len(s) else 10**9
    ti = 10**9
    if tp is not None:
        tg = e * (1 - tp * dist)
        q = np.nonzero(l_ <= tg)[0]
        ti = q[0] if len(q) else 10**9
    if si <= ti and si < 10**9:
        j, xp, how = si, max(stop, c[si]), "stop"
    elif ti < 10**9:
        j, xp, how = ti, tg, "target"
    else:
        j, xp, how = len(c) - 1, c[-1], "time"
    return (-(xp / e - 1) - FEE - FEE * xp / e) / dist, int(t[start + j]) + 60, how


def coin(args):
    sym, g, interval = args
    st = L._store()
    one = st.read(sym, "ltp", "1m")
    lo_t, hi_t = int(g.time.min()) - 3600, int(g.time.max()) + 2 * L.DAY
    try:
        fund = st.load(sym, "funding", "1h", lo_t, hi_t)
    except Exception:                                                # noqa: BLE001
        fund = pd.DataFrame()
    t = one.time.to_numpy("int64")
    hi, lo, cl = one.high.to_numpy(float), one.low.to_numpy(float), one.close.to_numpy(float)
    out = []
    for x in g.itertuples():
        a, b = np.searchsorted(t, x.time), np.searchsorted(t, x.time + L.DAY)
        rec = {"symbol": sym, "time": int(x.time), "window": x.window, "dist": x.dist}
        for entry in ("open", "bot"):
            if entry == "open":
                e, start, t0 = float(x.open), a, int(x.time)
            else:
                if b <= a or t[a] >= x.time + ENTRY_WINDOW:
                    rec["skip_bot"] = True
                    continue
                e, start, t0 = cl[a], a + 1, int(t[a]) + 60
            for name, tp in (("A", None), ("QUICK", QUICK_TP)):
                res = one_trade(t, hi, lo, cl, start, b, e, x.dist, tp)
                if res is None:
                    continue
                r, xt, how = res
                fr = np.nan
                if len(fund):
                    stamps = funding_timestamps(t0 + 1, xt - 1, interval)
                    fr = sum(_funding_lookup(fund, stamps).values()) / 100 / x.dist
                rec[f"{name}_{entry}"] = r
                rec[f"{name}_{entry}_fund"] = fr
                rec[f"{name}_{entry}_how"] = how
        out.append(rec)
    return out


def period(df: pd.DataFrame, name: str) -> pd.DataFrame:
    ts = df.time
    return {"2025": df[df.window == "2025"],
            "2026 Jan-Jul (tuned)": df[(df.window == "2026") & (ts < HOLDOUT)],
            "2026 Aug-Oct (sealed)": df[(df.window == "2026") & (ts >= HOLDOUT)],
            "2026 full": df[df.window == "2026"]}[name]


def main() -> None:
    cache = L.OUT / "fade_costed_rows.parquet"
    if cache.exists():
        res = pd.read_parquet(cache)
    else:
        rows = pd.read_parquet(L.OUT / "fade_more_picks_rows.parquet")
        rows = rows[(rows["rank"] <= 2) & (rows.dist <= LA.MAX_DIST)][["symbol", "time", "window", "dist"]]
        rows = rows.merge(L.daily()[["symbol", "time", "open"]], on=["symbol", "time"])
        cat = ProductCatalog()
        jobs = []
        for sym, g in rows.groupby("symbol"):
            try:
                iv = int(cat.get(sym)["funding_interval_seconds"])
            except Exception:                                        # noqa: BLE001
                iv = 8 * 3600
            jobs.append((sym, g, iv))
        print(f"{len(rows)} trades on {len(jobs)} coins; reading 1m, fetching funding ...", flush=True)
        with ThreadPoolExecutor(3) as ex:
            res = pd.DataFrame([r for part in ex.map(coin, jobs) for r in part]).sort_values("time")
        res.to_parquet(cache, index=False)
    missing = res[[c for c in res.columns if c.endswith("_fund")]].isna().mean().max()
    print(f"{len(res)} trades; funding missing on {100 * missing:.1f}% (counted as 0)")

    names = ["2025", "2026 Jan-Jul (tuned)", "2026 Aug-Oct (sealed)", "2026 full"]
    print("\n=== mean net R per trade, step by step ===")
    print(f"  {'rule':6s} {'period':24s} {'n':>4s} {'as before':>9s} {'+funding':>9s} {'+bot entry':>10s} {'± SE':>6s} {'win':>5s}")
    for rule in ("A", "QUICK"):
        for n in names:
            d = period(res, n)
            base = d[f"{rule}_open"]
            fund = base + d[f"{rule}_open_fund"].fillna(0)
            bot = (d[f"{rule}_bot"] + d[f"{rule}_bot_fund"].fillna(0)).dropna()
            print(f"  {rule:6s} {n:24s} {len(d):4d} {base.mean():+9.3f} {fund.mean():+9.3f} {bot.mean():+10.3f} "
                  f"{bot.std() / np.sqrt(len(bot)):6.3f} {100 * (bot > 0).mean():4.0f}%")

    print("\n=== $1,500 accounts WITH funding and the bot's entry (each period starts at $1,500) ===")
    print(f"  {'rule / sizing':22s} " + " | ".join(f"{n}: end, low, drop" for n in names[:3]))
    for rule, sizings in (("QUICK", ("flat 3%", "flat 5%")), ("A", ("flat 3%", "flat 5%", "flat 10%", "ladder 10/5/3"))):
        for s in sizings:
            cells = []
            for n in names[:3]:
                d = period(res, n).copy()
                d["R"] = d[f"{rule}_bot"] + d[f"{rule}_bot_fund"].fillna(0)
                d = d.dropna(subset=["R"])
                days = [(g.dist.to_numpy(float), g.R.to_numpy(float)) for _, g in d.groupby("time")]
                a = LA.simulate(days, LA.SIZINGS[s])
                cells.append(f"${a['end']:>7,.0f} ${a['low']:>6,.0f} {a['worst_drop']:4.0%}")
            print(f"  {rule + ' ' + s:22s} " + " | ".join(cells))

    print("\n=== month by month, WITH costs: $ P&L at a FIXED $75 per R (5% of $1,500), and the ladder's compounded month-end ===")
    for rule in ("QUICK", "A"):
        for w in ("2025", "2026"):
            d = res[res.window == w].copy()
            d["R"] = d[f"{rule}_bot"] + d[f"{rule}_bot_fund"].fillna(0)
            d = d.dropna(subset=["R"])
            d["mon"] = pd.to_datetime(d.time, unit="s").dt.strftime("%b")
            line = "  ".join(f"{m} {75 * g.R.sum():+,.0f}" for m, g in d.groupby("mon", sort=False))
            print(f"  {rule:6s} {w}: {line}  | year {75 * d.R.sum():+,.0f}")
            if rule == "A":
                days = [(g.dist.to_numpy(float), g.R.to_numpy(float)) for _, g in d.groupby("time")]
                path = LA.simulate(days, LA.LADDER)["path"]
                months = [g.mon.iloc[0] for _, g in d.groupby("time")]
                ends = {}
                for m, e in zip(months, path):
                    ends[m] = e
                print(f"  {'ladder':6s} {w}: " + "  ".join(f"{m} ${e:,.0f}" for m, e in ends.items()))


if __name__ == "__main__":
    main()
