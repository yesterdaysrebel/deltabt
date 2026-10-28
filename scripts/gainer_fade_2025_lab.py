"""Gainer fade: rule A on 2025 + four squeeze filters -- docs/gainer_fade_2025_prereg.md
(committed before this file and before any 2025 data was fetched).

    PYTHONPATH=. .venv/bin/python scripts/gainer_fade_2025_lab.py fetch   # daily, 1m and funding
    PYTHONPATH=. .venv/bin/python scripts/gainer_fade_2025_lab.py rows    # per coin-day outcomes, both years
    PYTHONPATH=. .venv/bin/python scripts/gainer_fade_2025_lab.py judge   # step 2 + step 3, once
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from deltabt.costs import funding_timestamps  # noqa: E402
from deltabt.data.store import CandleStore, ProductCatalog  # noqa: E402
from deltabt.engine import _funding_lookup  # noqa: E402
from top_gainers_backtest import MIN_TURNOVER, _crypto_perps  # noqa: E402

OUT = Path("out/gainer_fade")
DAY, HOUR = 86400, 3600
TS = lambda s: pd.Timestamp(s, tz="UTC").value // 10**9          # noqa: E731
DAILY_FROM = TS("2024-12-15")
WINDOWS = {"2025": (TS("2025-01-15"), TS("2026-01-01")),          # pick days, end exclusive
           "2026": (TS("2026-01-15"), TS("2026-10-28"))}
K, TAKER, PICKS, N_RANDOM, N_BOOT = 2.0, 0.0005, 2, 2000, 5000
ALPHA_A, ALPHA_F = 0.05, 0.0125
FILTERS = ("a", "b", "c", "d")


def _store():
    return CandleStore()


def daily() -> pd.DataFrame:
    p = OUT / "days_2025_2026.parquet"
    if p.exists():
        return pd.read_parquet(p)
    rows = _crypto_perps()
    cv = {r["symbol"]: float(r["contract_value"]) for r in rows}
    last = WINDOWS["2026"][1] - DAY
    st = _store()

    def one(sym):
        try:
            return sym, st.load(sym, "ltp", "1d", DAILY_FROM, last)
        except Exception as exc:                                     # noqa: BLE001
            print(f"  {sym}: {exc}", flush=True)
            return sym, pd.DataFrame()

    frames = []
    with ThreadPoolExecutor(4) as ex:
        for sym, d in ex.map(one, sorted(cv)):
            if len(d):
                frames.append(d.assign(symbol=sym, turnover=d.volume * cv[sym] * (d.high + d.low + d.close) / 3))
    d = pd.concat(frames, ignore_index=True)
    d = d[d.time <= last][["symbol", "time", "open", "high", "low", "close", "volume", "turnover"]]
    d.to_parquet(p, index=False)
    return d


def panel() -> pd.DataFrame:
    """One row per (coin, D) with D-1/D-2 readings; liquid/eligible flags."""
    d = daily().sort_values(["symbol", "time"]).reset_index(drop=True)
    g = d.groupby("symbol")
    d["change"] = d.close / d.open - 1
    pc = g.close.shift(1)
    tr = np.maximum.reduce([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()])
    d["atrp"] = pd.Series(tr, index=d.index).groupby(d.symbol).transform(lambda s: s.rolling(14).mean()) / d.close
    rng = (d.high - d.low).replace(0, np.nan)
    d["wick"] = (d.high - d.close) / rng                             # 0 = closed at the high
    prev = d[["symbol", "time", "change", "turnover", "atrp", "wick"]].copy()
    prev["time"] += DAY
    prev.columns = ["symbol", "time", "prev_change", "prev_turnover", "atrp", "wick"]
    prev2 = d[["symbol", "time", "change"]].copy()
    prev2["time"] += 2 * DAY
    prev2.columns = ["symbol", "time", "prev2_change"]
    p = d[["symbol", "time", "open", "close"]].merge(prev, on=["symbol", "time"]).merge(prev2, on=["symbol", "time"], how="left")
    p["liquid"] = p.prev_turnover >= MIN_TURNOVER
    p["eligible"] = p.liquid & (p.prev_change > 0)
    p["window"] = None
    for w, (a, b) in WINDOWS.items():
        p.loc[(p.time >= a) & (p.time < b), "window"] = w
    return p[p.window.notna() & p.liquid].reset_index(drop=True)


def cmd_fetch() -> None:
    p = panel()
    syms = sorted(p.symbol.unique())
    st = _store()
    lo, hi = WINDOWS["2025"][0] - DAY, WINDOWS["2026"][1] + DAY - 60

    def one(sym):
        try:
            a = len(st.load(sym, "ltp", "1m", lo, hi))
            b = len(st.load(sym, "funding", "1h", lo - 2 * DAY, hi))
            return sym, a, b
        except Exception as exc:                                     # noqa: BLE001
            return sym, f"ERROR {exc}", ""

    with ThreadPoolExecutor(3) as ex:
        for k, r in enumerate(ex.map(one, syms), 1):
            print(f"[{k}/{len(syms)}] {r[0]} 1m {r[1]} funding {r[2]}", flush=True)


def _trade(t, hi, cl, start, end, entry, dist):
    """Short from `start` (entry price given) to `end`; returns (R before funding, stop hit, exit time) or None."""
    a, b = np.searchsorted(t, start), np.searchsorted(t, end)
    if b <= a:
        return None
    stop = entry * (1 + dist)
    cross = np.nonzero(hi[a:b] >= stop)[0]
    if len(cross):
        j = a + cross[0]
        x, hit, xt = max(stop, cl[j]), 1.0, int(t[j]) + 60
    else:
        x, hit, xt = cl[b - 1], 0.0, int(t[b - 1]) + 60
    ret = -(x / entry - 1) - TAKER - TAKER * x / entry
    return ret / dist, hit, xt


def _coin_rows(args):
    sym, rows, interval = args
    st = _store()
    one = st.read(sym, "ltp", "1m")
    fund = st.read(sym, "funding", "1h")
    out = []
    if one.empty:
        return pd.DataFrame()
    t = one.time.to_numpy("int64")
    hi, cl = one.high.to_numpy(float), one.close.to_numpy(float)
    ft = fund.time.to_numpy("int64") if len(fund) else np.zeros(0, "int64")
    fv = fund.close.to_numpy(float) if len(fund) else np.zeros(0)
    for r in rows.itertuples():
        if not np.isfinite(r.atrp) or r.atrp <= 0:
            continue
        dist = K * r.atrp
        rec = r._asdict()
        i = np.searchsorted(ft, r.time, side="right") - 1
        rec["fund_at_entry"] = float(fv[i]) if i >= 0 else np.nan
        # rule A: 00:00 -> 24:00
        A = _trade(t, hi, cl, r.time, r.time + DAY, r.open, dist)
        if A is None:
            continue
        rec["R_A"], rec["hit_A"], xt = A
        st_ = funding_timestamps(int(r.time) + 1, xt - 1, interval)
        rec["fund_R_A"] = sum(_funding_lookup(fund, st_).values()) / 100 / dist if len(fund) else np.nan
        # filter b: 04:00 entry if below the open, -> D+1 04:00
        j = np.searchsorted(t, r.time + 4 * HOUR) - 1                # the 03:59 candle
        rec["px_0400"] = cl[j] if j >= 0 and t[j] >= r.time else np.nan
        rec["confirmed"] = bool(np.isfinite(rec["px_0400"]) and rec["px_0400"] < r.open)
        rec["R_B"] = rec["hit_B"] = rec["fund_R_B"] = np.nan
        if rec["confirmed"]:
            B = _trade(t, hi, cl, r.time + 4 * HOUR, r.time + DAY + 4 * HOUR, rec["px_0400"], dist)
            if B is not None:
                rec["R_B"], rec["hit_B"], xtb = B
                stb = funding_timestamps(int(r.time) + 4 * HOUR + 1, xtb - 1, interval)
                rec["fund_R_B"] = sum(_funding_lookup(fund, stb).values()) / 100 / dist if len(fund) else np.nan
        out.append(rec)
    return pd.DataFrame(out)


def cmd_rows() -> None:
    p = panel()
    cat = ProductCatalog()
    jobs = []
    for sym, g in p.groupby("symbol"):
        try:
            iv = int(cat.get(sym)["funding_interval_seconds"])
        except Exception:                                            # noqa: BLE001
            iv = 8 * HOUR
        jobs.append((sym, g, iv))
    with ThreadPoolExecutor(6) as ex:
        parts = [x for x in ex.map(_coin_rows, jobs) if len(x)]
    r = pd.concat(parts, ignore_index=True).drop(columns=["Index"], errors="ignore")
    r.to_parquet(OUT / "rows_2025_2026.parquet", index=False)
    for w, g in r.groupby("window"):
        print(f"{w}: {len(g)} liquid coin-days with outcomes, {g.symbol.nunique()} coins, "
              f"{int(g.eligible.sum())} eligible; funding known for {100*g.fund_R_A.notna().mean():.0f}%")


# ---------------------------------------------------------------- judging

def select(r: pd.DataFrame, f: str | None) -> pd.DataFrame:
    """Top 2 by D-1 change among eligible coins passing filter f; columns R, hit, fundR."""
    e = r[r.eligible]
    if f == "a":
        e = e[~(e.wick < 0.25)]
    elif f == "b":
        e = e[e.confirmed & e.R_B.notna()]
    elif f == "c":
        e = e[~(e.fund_at_entry < 0)]
    elif f == "d":
        e = e[~(e.prev2_change > 0)]
    e = e.sort_values(["time", "prev_change"], ascending=[True, False]).groupby("time").head(PICKS).copy()
    col = "B" if f == "b" else "A"
    e["R"], e["hit"], e["fundR"] = e[f"R_{col}"], e[f"hit_{col}"], e[f"fund_R_{col}"]
    return e


def summary(e: pd.DataFrame) -> str:
    R = e.R
    w, l = R[R > 0], R[R <= 0]
    eq, peak, dd = 250.0, 250.0, 0.0
    for _, g in e.groupby("time"):
        eq += 0.02 * eq * g.R.sum()
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    return (f"{len(R)} trades | mean {R.mean():+.3f}R (±{R.std()/np.sqrt(len(R)):.3f}) | with funding "
            f"{(R + e.fundR.fillna(0)).mean():+.3f}R | win {100*len(w)/len(R):.0f}% | avg win {w.mean():+.2f}R "
            f"avg loss {l.mean():+.2f}R | PF {w.sum()/-l.sum():.2f} | stopped {100*e.hit.mean():.0f}% | "
            f"$250 -> ${eq:.0f} (max dd {100*dd:.0f}%)")


def random_p(r: pd.DataFrame, e: pd.DataFrame, seed=0) -> float:
    pools = r.groupby("time").R_A.apply(np.asarray)
    cnt = e.groupby("time").size()
    rng = np.random.default_rng(seed)
    draws = np.empty(N_RANDOM)
    for j in range(N_RANDOM):
        s = n = 0
        for t_, c in cnt.items():
            pl = pools.get(t_)
            if pl is None or not len(pl):
                continue
            c = min(c, len(pl))
            s += rng.choice(pl, c, replace=False).sum()
            n += c
        draws[j] = s / n
    return float((draws >= e.R.mean()).mean()), float(draws.mean())


def boot_diff(base: pd.DataFrame, filt: pd.DataFrame, seed=0) -> tuple[float, float]:
    """Mean R difference (filter - A) and P(diff <= 0) by resampling days."""
    days = np.array(sorted(set(base.time) | set(filt.time)))
    bs = base.groupby("time").R.agg(["sum", "count"]).reindex(days, fill_value=0)
    fs = filt.groupby("time").R.agg(["sum", "count"]).reindex(days, fill_value=0)
    diff = fs["sum"].sum() / fs["count"].sum() - bs["sum"].sum() / bs["count"].sum()
    rng = np.random.default_rng(seed)
    le = 0
    for _ in range(N_BOOT):
        i = rng.integers(0, len(days), len(days))
        d = fs["sum"].values[i].sum() / max(fs["count"].values[i].sum(), 1) - bs["sum"].values[i].sum() / max(bs["count"].values[i].sum(), 1)
        le += d <= 0
    return float(diff), le / N_BOOT


def cmd_judge() -> None:
    r = pd.read_parquet(OUT / "rows_2025_2026.parquet")
    lines = []
    base = {}
    for w in ("2025", "2026"):
        rw = r[r.window == w]
        e = select(rw, None)
        base[w] = e
        rp, rm = random_p(rw, e)
        lines.append(f"[{w}] rule A: {summary(e)} | random coins same stop {rm:+.3f}R, share >= A {rp:.3f}")
        if w == "2025":
            ok = e.R.mean() > 0 and rp <= ALPHA_A
            lines.append(f"STEP 2 (2025 replication): mean > 0 {e.R.mean() > 0} | beats random p <= {ALPHA_A} {rp <= ALPHA_A} => {'PASS' if ok else 'FAIL'}")
            a_pass = ok
    lines.append("STEP 3 filters (judged on 2025; 2026 = direction check):")
    for f in FILTERS:
        res = {}
        for w in ("2025", "2026"):
            rw = r[r.window == w]
            e = select(rw, f)
            d, p = boot_diff(base[w], e)
            res[w] = (d, p)
            lines.append(f"  [{w}] filter {f}: {summary(e)} | vs A {d:+.3f}R (P(diff<=0) {p:.4f})")
        ok = res["2025"][0] > 0 and res["2025"][1] <= ALPHA_F and res["2026"][0] > 0
        lines.append(f"  filter {f}: better on 2025 {res['2025'][0] > 0}, p <= {ALPHA_F} {res['2025'][1] <= ALPHA_F}, "
                     f"same sign on 2026 {res['2026'][0] > 0} => {'ADOPT' if ok and a_pass else ('would adopt but A failed' if ok else 'REJECT')}")
    txt = "\n".join(lines)
    print(txt)
    (OUT / "judge_2025.txt").write_text(txt + "\n")


if __name__ == "__main__":
    {"fetch": cmd_fetch, "rows": cmd_rows, "judge": cmd_judge}[sys.argv[1]]()
