"""New-listing drift, as pre-registered in docs/new_listing_drift_prereg.md (2026-10-13).

    PYTHONPATH=. .venv/bin/python scripts/new_listing_lab.py

Short each new Delta perp at the close of the minute 48h after its first served
candle; 30% stop on mark (minute by minute, fills at LTP close + slippage);
exit at 21 days (7 / 14 reported); taker both legs x1.18 GST + 10 bps slippage
per leg; funding at each 8h settlement crossed. Control: the same short at a
random minute inside days 90..135 of the same coin, 50 draws per window.
Filler minutes (volume 0, o=h=l=c) are dropped before anything is read.
"""
import json, pathlib, sys
import numpy as np, pandas as pd
sys.path.insert(0, ".")
from deltabt.config import GST_MULTIPLIER

DAY, H = 86400, 3600; STOP, HOLD_D, TAKER, SLIP, FI = 0.30, 21, 0.0005 * GST_MULTIPLIER, 0.0010, 28800
N_RANDOM, MIN_LIST = 50, 30
MAN = json.load(open("out/sweep/five_min_arm_lab/new_listing/fetch_manifest.json"))["listings"]
OUT = pathlib.Path("out/sweep/five_min_arm_lab/new_listing")
WINDOWS = {"OLD (launched 2024-06..2025-12)": ("2024-06-01", "2025-12-19"), "DEV (launched 2025-12-20..2026-09-01)": ("2025-12-20", "2026-09-01")}


def load(sym, block):
    d = pathlib.Path("data/listings") / sym
    if not (d / f"{block}_ltp_1m.parquet").exists():
        return None
    l = pd.read_parquet(d / f"{block}_ltp_1m.parquet").sort_values("time"); l = l[~((l.volume == 0) & (l.open == l.close) & (l.high == l.low))]
    mk = pd.read_parquet(d / f"{block}_mark_1m.parquet").sort_values("time") if (d / f"{block}_mark_1m.parquet").exists() else None
    fd = pd.read_parquet(d / "funding_1h.parquet").sort_values("time") if (d / "funding_1h.parquet").exists() else pd.DataFrame()
    return dict(ltp=l.reset_index(drop=True), mark=mk, funding=fd)


def short_from(data, t0, holds=(7, 14, 21)):
    """Open a short at the first traded minute >= t0; resolve stop / time exits. Returns dict or None."""
    l = data["ltp"]; lt = l.time.to_numpy("int64"); a = np.searchsorted(lt, t0)
    if a >= len(lt) - 10 or lt[a] - t0 > 6 * H:
        return None
    entry = float(l.close.iat[a]) * (1 - SLIP); stop = entry * (1 + STOP); end = lt[a] + HOLD_D * DAY
    b = np.searchsorted(lt, end, side="right"); seg = l.iloc[a:b]; tt = seg.time.to_numpy("int64"); lc = seg.close.to_numpy(float)
    if data["mark"] is not None:
        mk = data["mark"]; mt = mk.time.to_numpy("int64"); ma, mb = np.searchsorted(mt, lt[a]), np.searchsorted(mt, end, side="right")
        mhi = pd.Series(mk.high.to_numpy(float)[ma:mb], index=mt[ma:mb]).reindex(tt).ffill().bfill().to_numpy()
    else:
        mhi = seg.high.to_numpy(float)
    out = dict(entry_time=int(lt[a]), entry=entry, last_time=int(tt[-1]))
    hit = np.flatnonzero(mhi[1:] >= stop) + 1; k_stop = int(hit[0]) if len(hit) else None
    for hd in holds:
        kh = int(np.searchsorted(tt, lt[a] + hd * DAY, side="right") - 1)
        if kh < 1:
            out[f"ret{hd}"] = np.nan; continue
        if k_stop is not None and k_stop <= kh:
            exit_px, k_exit, reason = float(lc[k_stop]) * (1 + SLIP), k_stop, "stop"
        else:
            exit_px, k_exit, reason = float(lc[kh]) * (1 + SLIP), kh, "time"
        gross = (entry - exit_px) / entry; fees = TAKER * 2
        fund = 0.0; fd = data["funding"]
        if len(fd):
            ft = fd.time.to_numpy("int64"); fr = fd.close.to_numpy(float)
            for st in range(int(np.ceil(lt[a] / FI)) * FI, int(tt[k_exit]) + 1, FI):
                j = np.searchsorted(ft, st, side="right") - 1
                if j >= 0:
                    fund += fr[j] / 100.0                           # a short RECEIVES positive funding (longs pay)
        out[f"ret{hd}"] = gross - fees + fund; out[f"gross{hd}"] = gross; out[f"fund{hd}"] = fund
        if hd == HOLD_D:
            out["reason"] = reason; out["exit_time"] = int(tt[k_exit]); out["days_held"] = (tt[k_exit] - lt[a]) / DAY
    return out


def day_t(x, days):
    g = pd.DataFrame(dict(x=x, d=days)).dropna().groupby("d").x.agg(["sum", "count"]); n = g["count"].sum()
    if n < 2: return np.nan, np.nan
    mu = g["sum"].sum() / n; se = np.sqrt(((g["sum"] - mu * g["count"]) ** 2).sum()) / n; return mu, (mu / se if se > 0 else np.nan)


def one_at_a_time(T):
    T = T.sort_values("entry_time"); keep, busy = [], -1
    for i, r in T.iterrows():
        if r.entry_time >= busy:
            keep.append(i); busy = r.exit_time
    return T.loc[keep]


if __name__ == "__main__":
    verdict = {}
    for win, (lo, hi) in WINDOWS.items():
        lo_s, hi_s = int(pd.Timestamp(lo, tz="UTC").timestamp()), int(pd.Timestamp(hi, tz="UTC").timestamp()) + DAY
        rows, ctrl = [], {j: [] for j in range(N_RANDOM)}; rng = np.random.default_rng(42)
        for sym, info in MAN.items():
            if not (lo_s <= info["launch"] < hi_s) or "early" not in info:
                continue
            early = load(sym, "early")
            if early is None or len(early["ltp"]) < 1000:
                continue
            first = int(early["ltp"].time.iat[0]); r = short_from(early, first + 2 * DAY)
            if r is None or not np.isfinite(r.get("ret21", np.nan)):
                continue
            usd = (early["ltp"].close * early["ltp"].volume).groupby(early["ltp"].time // DAY).sum()
            rows.append(dict(symbol=sym, launch=info["launch"], liquid=bool(usd.median() >= 250_000), **r))
            late = load(sym, "late")
            if late is not None and len(late["ltp"]) > 5000:
                lt = late["ltp"].time.to_numpy("int64"); span = lt[-1] - lt[0] - (HOLD_D + 1) * DAY
                if span > DAY:
                    for j in range(N_RANDOM):
                        c = short_from(late, int(lt[0] + rng.integers(0, span)))
                        if c and np.isfinite(c.get("ret21", np.nan)): ctrl[j].append(c["ret21"])
        T = pd.DataFrame(rows); n = len(T)
        print(f"\n==== {win}: {n} listings with a trade (floor {MIN_LIST}) ====")
        if n < MIN_LIST:
            print("  CANNOT ANSWER"); verdict[win] = None; continue
        T.to_csv(OUT / f"trades_{win[:3]}.csv", index=False); ld = T.launch // DAY
        for hd in (7, 14, 21):
            mu, t = day_t(T[f"ret{hd}"], ld); g, _ = day_t(T[f"gross{hd}"], ld)
            print(f"  {hd:2d}-day short: net {100 * mu:+.2f}% per trade (launch-day t {t:+.2f})  gross {100 * g:+.2f}%  funding {100 * T[f'fund{hd}'].mean():+.2f}%  "
                  f"win {100 * (T[f'ret{hd}'] > 0).mean():.0f}%  = {mu / STOP:+.3f}R")
        null = np.array([np.mean(v) for v in ctrl.values() if len(v) >= 10]); mu21, t21 = day_t(T.ret21, ld)
        p = float(np.mean(null >= mu21)) if len(null) else np.nan
        print(f"  control (same coins, random start in days 90..135, 21-day short) x{len(null)}: mean {100 * np.nanmean(null):+.2f}%, "
              f"5-95% {100 * np.nanpercentile(null, 5):+.2f}%..{100 * np.nanpercentile(null, 95):+.2f}%; p(control >= real) {p:.2f}")
        print(f"  exits: {T.reason.value_counts().to_dict()}; stopped out {100 * (T.reason == 'stop').mean():.0f}%")
        Lq = T[T.liquid]; S = one_at_a_time(T)
        print(f"  liquid only (median daily $ volume >= $250k): {len(Lq)} listings, net 21d {100 * Lq.ret21.mean():+.2f}%  |  one position at a time: {len(S)} trades, net 21d {100 * S.ret21.mean():+.2f}%")
        N1, N2, N3 = mu21 > 0, t21 >= 2, (p <= 0.05) if np.isfinite(p) else False
        print(f"  N1 net > 0: {'PASS' if N1 else 'fail'}   N2 launch-day t >= 2: {'PASS' if N2 else 'fail'}   N3 beats control p <= 0.05: {'PASS' if N3 else 'fail'}")
        verdict[win] = bool(N1 and N2 and N3)
    print("\nNEW-LISTING DRIFT:", "PASS" if all(v is True for v in verdict.values()) else "FAIL" if any(v is False for v in verdict.values()) else "CANNOT ANSWER")
