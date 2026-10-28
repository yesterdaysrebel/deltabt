"""Gainer fade lab -- docs/gainer_fade_prereg.md (committed before this file).

    PYTHONPATH=. .venv/bin/python scripts/gainer_fade_lab.py build     # daily candles -> out/gainer_fade/days.parquet
    PYTHONPATH=. .venv/bin/python scripts/gainer_fade_lab.py dev       # dev window only
    PYTHONPATH=. .venv/bin/python scripts/gainer_fade_lab.py holdout   # ONCE, after the dev result is in CONTEXT.md

Short the K previous-day top gainers at the UTC open, cover at the UTC close.
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from deltabt.data.store import CandleStore  # noqa: E402
from top_gainers_backtest import MIN_TURNOVER, _crypto_perps  # noqa: E402

OUT = Path("out/gainer_fade")
DAY = 86400
FIRST = pd.Timestamp("2026-01-01", tz="UTC").value // 10**9
DEV_END = pd.Timestamp("2026-08-01", tz="UTC").value // 10**9      # exclusive
FEE = 0.0010                                                        # taker 0.05% x 2
K_PRIMARY = 2
K_CONTEXT = (1, 5, 10)
N_RANDOM = 2000


def cmd_build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = _crypto_perps()
    cv = {r["symbol"]: float(r["contract_value"]) for r in rows}
    last = (int(time.time()) // DAY) * DAY - DAY                       # last complete UTC day
    store = CandleStore()

    def one(sym):
        try:
            return sym, store.load(sym, "ltp", "1d", FIRST - 3 * DAY, last)
        except Exception as exc:                                        # noqa: BLE001
            print(f"  {sym}: {exc}", flush=True)
            return sym, pd.DataFrame()

    frames = []
    with ThreadPoolExecutor(4) as ex:
        for sym, d in ex.map(one, sorted(cv)):
            if d.empty:
                continue
            frames.append(d.assign(symbol=sym,
                                   turnover=d.volume * cv[sym] * (d.high + d.low + d.close) / 3))
    days = pd.concat(frames, ignore_index=True)[["symbol", "time", "open", "high", "low", "close", "volume", "turnover"]]
    days = days[days.time <= last]
    days.to_parquet(OUT / "days.parquet", index=False)
    print(f"{days.symbol.nunique()} coins with daily candles, {len(days)} coin-days, "
          f"{pd.to_datetime(days.time.min(), unit='s').date()} .. {pd.to_datetime(days.time.max(), unit='s').date()}")


def panel() -> pd.DataFrame:
    """One row per (coin, day D): D-1's change/turnover and D's short return."""
    d = pd.read_parquet(OUT / "days.parquet").sort_values(["symbol", "time"])
    d["change"] = d.close / d.open - 1
    prev = d[["symbol", "time", "change", "turnover"]].copy()
    prev["time"] = prev.time + DAY                                      # D-1's stats, keyed on D
    p = d[["symbol", "time", "open", "close"]].merge(
        prev.rename(columns={"change": "prev_change", "turnover": "prev_turnover"}),
        on=["symbol", "time"], how="inner")
    p["short"] = -(p.close / p.open - 1)
    p = p[p.time >= FIRST]
    p["liquid"] = p.prev_turnover >= MIN_TURNOVER                      # the baseline set
    p["eligible"] = p.liquid & (p.prev_change > 0)                     # the pick set
    return p


def picks(p: pd.DataFrame, k: int) -> pd.DataFrame:
    e = p[p.eligible].sort_values(["time", "prev_change"], ascending=[True, False])
    return e.groupby("time").head(k)


def evaluate(p: pd.DataFrame, k: int, rng_seed: int = 0) -> dict:
    pk = picks(p, k)
    mkt = p[p.liquid].groupby("time").short.mean().rename("mkt")
    pk = pk.join(mkt, on="time")
    pk["net"] = pk.short - FEE
    pk["excess"] = pk.short - pk.mkt
    by_day = pk.groupby("time").agg(excess=("excess", "mean"), net=("net", "mean"), short=("short", "mean"))
    n = len(by_day)
    ex_m, ex_sd = by_day.excess.mean(), by_day.excess.std(ddof=1)
    t = ex_m / (ex_sd / np.sqrt(n)) if n > 1 and ex_sd > 0 else np.nan
    from scipy import stats
    p_ex = float(stats.t.sf(t, n - 1)) if np.isfinite(t) else np.nan
    # random picks: per day, k random coins from the liquid set (same count as picked that day)
    rng = np.random.default_rng(rng_seed)
    liquid = p[p.liquid].groupby("time").short.apply(np.asarray)
    counts = pk.groupby("time").size()
    days = counts.index
    pools = [liquid.loc[t_] for t_ in days]
    cnt = counts.to_numpy()
    draws = np.empty(N_RANDOM)
    for j in range(N_RANDOM):
        tot = 0.0
        for pool, c in zip(pools, cnt):
            tot += rng.choice(pool, size=min(c, len(pool)), replace=False).sum()
        draws[j] = tot / cnt.sum()
    p_rand = float((draws >= pk.short.mean()).mean())
    return dict(k=k, picks=len(pk), days=n, mean_short=pk.short.mean(), mean_net=pk.net.mean(),
                mean_excess=ex_m, excess_t=t, excess_p=p_ex, random_p=p_rand,
                random_mean=draws.mean(), win=(pk.net > 0).mean(), median_net=pk.net.median(),
                worst=pk.net.min(), best=pk.net.max())


def _fmt(r: dict) -> str:
    return (f"K={r['k']:<2} picks {r['picks']:4d} over {r['days']:3d} days | short {100*r['mean_short']:+.2f}%  "
            f"net {100*r['mean_net']:+.2f}%  (median {100*r['median_net']:+.2f}%, win {100*r['win']:.0f}%, "
            f"worst {100*r['worst']:+.1f}%, best {100*r['best']:+.1f}%) | excess vs market {100*r['mean_excess']:+.2f}% "
            f"(t {r['excess_t']:+.2f}, one-sided p {r['excess_p']:.3f}) | random picks {100*r['random_mean']:+.2f}%, "
            f"share >= picks {r['random_p']:.3f}")


def run(window: str) -> None:
    p = panel()
    if window == "dev":
        p = p[p.time < DEV_END]
    else:
        p = p[p.time >= DEV_END]
    lines = [f"{window.upper()}: pick days {pd.to_datetime(p.time.min(), unit='s').date()} .. "
             f"{pd.to_datetime(p.time.max(), unit='s').date()}; {p.symbol.nunique()} coins; "
             f"liquid coin-days {int(p.liquid.sum())}, eligible {int(p.eligible.sum())}"]
    prim = evaluate(p, K_PRIMARY)
    lines.append("PRIMARY " + _fmt(prim))
    if window == "dev":
        ok = (prim["mean_net"] > 0, prim["mean_excess"] > 0 and prim["excess_p"] <= 0.05, prim["random_p"] <= 0.05)
        lines.append(f"DEV DECISION: net>0 {ok[0]} | excess>0 & p<=0.05 {ok[1]} | beats random p<=0.05 {ok[2]} "
                     f"=> {'PASS' if all(ok) else 'FAIL'}")
    else:
        ok = (prim["mean_net"] > 0, prim["mean_excess"] > 0)
        lines.append(f"HOLD-OUT DECISION: net>0 {ok[0]} | excess>0 {ok[1]} => {'PASS' if all(ok) else 'FAIL'}")
    lines.append("context (not decisions):")
    for k in K_CONTEXT:
        lines.append("  " + _fmt(evaluate(p, k)))
    # by D-1 change size, K=10 picks (context)
    pk = picks(p, 10).join(p[p.liquid].groupby("time").short.mean().rename("mkt"), on="time")
    pk["bucket"] = pd.cut(100 * pk.prev_change, [0, 10, 20, 40, 1e9], labels=["0-10%", "10-20%", "20-40%", ">40%"])
    for b, g in pk.groupby("bucket", observed=True):
        lines.append(f"  previous-day gain {b:>7}: n {len(g):4d}  short {100*g.short.mean():+.2f}%  "
                     f"excess {100*(g.short - g.mkt).mean():+.2f}%")
    txt = "\n".join(lines)
    print(txt)
    (OUT / f"{window}.txt").write_text(txt + "\n")


if __name__ == "__main__":
    {"build": cmd_build, "dev": lambda: run("dev"), "holdout": lambda: run("holdout")}[sys.argv[1]]()
