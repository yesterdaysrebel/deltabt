"""Gainer fade WITH A STOP -- docs/gainer_fade_stop_prereg.md (committed before this file).

    PYTHONPATH=. .venv/bin/python scripts/gainer_fade_stop_lab.py fetch     # 1m ltp for every liquid coin
    PYTHONPATH=. .venv/bin/python scripts/gainer_fade_stop_lab.py dev       # chooses k; nothing judged
    PYTHONPATH=. .venv/bin/python scripts/gainer_fade_stop_lab.py holdout   # ONCE, after dev is in CONTEXT.md

A = top-2 gainers; B = top-2 gainers among defi/layer/low-volume coins. Short at the UTC
open, stop k x daily ATR% above, exit at the stop (ltp_close fill) or the UTC close.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))
import gainer_fade_lab as G  # noqa: E402
from deltabt import signal_features as SF  # noqa: E402
from deltabt.data.store import CandleStore  # noqa: E402

OUT = G.OUT
DAY = G.DAY
KS = (1.0, 1.5, 2.0)
TAKER = 0.0005
LOW_TURNOVER = 712_048.0
B_TAGS = ("defi", "layer")
K_PICKS = 2
N_RANDOM = 2000
ALPHA = 0.025
HOLDOUT_END = pd.Timestamp("2026-10-28", tz="UTC").value // 10**9   # exclusive: last pick day 10-27


def panel() -> pd.DataFrame:
    """G.panel() plus D-1's 14-day daily ATR as a fraction of D-1's close."""
    d = pd.read_parquet(OUT / "days.parquet").sort_values(["symbol", "time"])
    pc = d.groupby("symbol").close.shift(1)
    tr = np.maximum.reduce([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()])
    d["atr"] = pd.Series(tr, index=d.index).groupby(d.symbol).transform(lambda s: s.rolling(14).mean())
    d["atrp"] = d.atr / d.close
    prev = d[["symbol", "time", "atrp"]].assign(time=d.time + DAY)
    p = G.panel().merge(prev, on=["symbol", "time"], how="left")
    return p[p.time < HOLDOUT_END]


def cmd_fetch() -> None:
    p = panel()
    syms = sorted(p[p.liquid].symbol.unique())
    store = CandleStore()
    end = int(HOLDOUT_END) - 60

    def one(sym):
        try:
            d = store.load(sym, "ltp", "1m", G.FIRST, end)
            return sym, len(d)
        except Exception as exc:                                        # noqa: BLE001
            return sym, f"ERROR {exc}"

    with ThreadPoolExecutor(3) as ex:
        for k, (sym, n) in enumerate(ex.map(one, syms), 1):
            print(f"[{k}/{len(syms)}] {sym} {n}", flush=True)


def _outcomes(sym: str, rows: pd.DataFrame) -> pd.DataFrame:
    """R and stop-hit for each (coin, day) row and each k, from D's 1m candles."""
    one = CandleStore().read(sym, "ltp", "1m")
    out = rows.copy()
    for k in KS:
        out[f"R_{k}"] = np.nan
        out[f"hit_{k}"] = np.nan
    out["m1"] = 0
    if one.empty:
        return out
    t = one.time.to_numpy("int64")
    hi, cl = one.high.to_numpy(float), one.close.to_numpy(float)
    for i, r in out.iterrows():
        a, b = np.searchsorted(t, r.time), np.searchsorted(t, r.time + DAY)
        if b <= a or not np.isfinite(r.atrp) or r.atrp <= 0:
            continue
        out.at[i, "m1"] = b - a
        entry = r.open
        h, c = hi[a:b], cl[a:b]
        for k in KS:
            dist = k * r.atrp
            stop = entry * (1 + dist)
            cross = np.nonzero(h >= stop)[0]
            if len(cross):
                x = max(stop, c[cross[0]])
                out.at[i, f"hit_{k}"] = 1.0
            else:
                x = c[-1]
                out.at[i, f"hit_{k}"] = 0.0
            ret = -(x / entry - 1) - TAKER - TAKER * x / entry
            out.at[i, f"R_{k}"] = ret / dist
    return out


def rows(window: str) -> pd.DataFrame:
    path = OUT / f"stop_rows_{window}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    p = panel()
    p = p[p.time < G.DEV_END] if window == "dev" else p[p.time >= G.DEV_END]
    p = p[p.liquid].copy()
    tags = requests.get("https://api.india.delta.exchange/v2/tickers",
                        params={"contract_types": "perpetual_futures"}, timeout=30).json()["result"]
    tag = {r["symbol"]: SF.tag_of(r.get("tags")) for r in tags}
    p["tag"] = p.symbol.map(tag).fillna("other")
    p["in_b"] = p.tag.isin(B_TAGS) | (p.prev_turnover < LOW_TURNOVER)
    with ThreadPoolExecutor(6) as ex:
        parts = list(ex.map(lambda sg: _outcomes(sg[0], sg[1]), p.groupby("symbol")))
    out = pd.concat(parts, ignore_index=True)
    out.to_parquet(path, index=False)
    return out


def top2(r: pd.DataFrame, k: float, only_b: bool) -> pd.DataFrame:
    e = r[r.eligible & r[f"R_{k}"].notna()]
    if only_b:
        e = e[e.in_b]
    return e.sort_values(["time", "prev_change"], ascending=[True, False]).groupby("time").head(K_PICKS)


def vs_random(r: pd.DataFrame, picks: pd.DataFrame, k: float, only_b: bool, seed: int = 0) -> tuple[float, float]:
    pool = r[r[f"R_{k}"].notna()]
    if only_b:
        pool = pool[pool.in_b]
    pools = pool.groupby("time")[f"R_{k}"].apply(np.asarray)
    cnt = picks.groupby("time").size()
    rng = np.random.default_rng(seed)
    draws = np.empty(N_RANDOM)
    for j in range(N_RANDOM):
        tot, n = 0.0, 0
        for t_, c in cnt.items():
            pl = pools.get(t_)
            if pl is None or len(pl) == 0:
                continue
            c = min(c, len(pl))
            tot += rng.choice(pl, size=c, replace=False).sum()
            n += c
        draws[j] = tot / n
    m = picks[f"R_{k}"].mean()
    return float(draws.mean()), float((draws >= m).mean())


def account(picks: pd.DataFrame, k: float) -> tuple[float, float]:
    """$250, 2% risk per trade on start-of-day equity, both picks the same day."""
    eq, peak, dd = 250.0, 250.0, 0.0
    for _, g in picks.groupby("time"):
        risk = 0.02 * eq
        eq += risk * g[f"R_{k}"].sum()
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    return eq, dd


def describe(name: str, r: pd.DataFrame, k: float, only_b: bool) -> tuple[str, dict]:
    pk = top2(r, k, only_b)
    R = pk[f"R_{k}"]
    rmean, rp = vs_random(r, pk, k, only_b)
    eq, dd = account(pk, k)
    res = dict(n=len(R), mean=R.mean(), random_mean=rmean, random_p=rp)
    line = (f"{name} k={k}: {len(R)} trades over {pk.time.nunique()} days | mean {R.mean():+.3f}R "
            f"(±{R.std()/np.sqrt(max(len(R),1)):.3f}), median {R.median():+.2f}R, win {100*(R>0).mean():.0f}%, "
            f"stopped {100*pk[f'hit_{k}'].mean():.0f}%, worst {R.min():+.2f}R | random coins same stop "
            f"{rmean:+.3f}R, share >= picks {rp:.3f} | $250 account -> ${eq:.0f} (max drawdown {100*dd:.0f}%)")
    return line, res


def cmd_dev() -> None:
    r = rows("dev")
    miss = int((r.m1 == 0).sum())
    lines = [f"DEV (Jan-Jul): {len(r)} liquid coin-days, {miss} without 1m data or ATR (skipped)"]
    best = None
    for k in KS:
        la, ra = describe("A", r, k, False)
        lb, _ = describe("B", r, k, True)
        lines += [la, lb]
        if best is None or ra["mean"] > best[1]:
            best = (k, ra["mean"])
    lines.append(f"CHOSEN k = {best[0]} (best A mean net R on dev). Dev is not a judgement (pre-registration).")
    (OUT / "stop_k.txt").write_text(f"{best[0]}\n")
    txt = "\n".join(lines)
    print(txt)
    (OUT / "stop_dev.txt").write_text(txt + "\n")


def cmd_holdout() -> None:
    k = float((OUT / "stop_k.txt").read_text().strip())
    r = rows("holdout")
    miss = int((r.m1 == 0).sum())
    lines = [f"HOLD-OUT (pick days 2026-08-01 .. 2026-10-27), opened once; k = {k} frozen from dev. "
             f"{len(r)} liquid coin-days, {miss} skipped (no 1m data or ATR)"]
    verdicts = []
    for name, only_b in (("A", False), ("B", True)):
        line, res = describe(name, r, k, only_b)
        ok = res["mean"] > 0 and res["random_p"] <= ALPHA
        verdicts.append(ok)
        lines += [line, f"  {name}: mean net R > 0 {res['mean'] > 0} | beats random p <= {ALPHA} "
                        f"{res['random_p'] <= ALPHA} => {'PASS' if ok else 'FAIL'}"]
    lines.append("VERDICT: " + ("at least one version PASSES -> owner decides on paper"
                                if any(verdicts) else "both FAIL -> the fade idea is CLOSED"))
    txt = "\n".join(lines)
    print(txt)
    (OUT / "stop_holdout.txt").write_text(txt + "\n")


if __name__ == "__main__":
    {"fetch": cmd_fetch, "dev": cmd_dev, "holdout": cmd_holdout}[sys.argv[1]]()
