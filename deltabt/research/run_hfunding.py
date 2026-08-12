"""Execute H-Funding on TRAIN + VALIDATION only. TEST STAYS LOCKED.

    PYTHONPATH=. python3 -u -m deltabt.research.run_hfunding
"""

from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research import hfunding as hf
from deltabt.research.hfunding_nulls import run_nulls
from deltabt.research.stats import (
    block_bootstrap_mean,
    bootstrap_diff,
    trade_design_effect,
)
from deltabt.strategy import resample_ohlcv

OUT = OUT_DIR / "hfunding"
STUDY_START = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())


def eligibility(store, cat, candidates):
    """Frozen rule: coverage, funding completeness, liquidity, history."""
    rows, universe = [], []
    for s in candidates:
        ltp = store.read(s, "ltp", "1m"); fund = store.read(s, "funding", "1h")
        if ltp.empty or fund.empty:
            rows.append(dict(symbol=s, eligible=False, reason="no data")); continue
        w = ltp[ltp.time >= STUDY_START]; fw = fund[fund.time >= STUDY_START]
        if len(w) < 10_000 or len(fw) < hf.MIN_PCTL_OBS + 100:
            rows.append(dict(symbol=s, eligible=False, reason="insufficient in-window data"))
            continue
        v = fw["close"].to_numpy("float64")
        t = fw["time"].to_numpy("int64")
        d = np.diff(t); mode = int(np.median(d)) if len(d) else 3600
        rec = dict(symbol=s,
                   price_bars=len(w), funding_obs=len(fw),
                   funding_nan=int(np.isnan(v).sum()),
                   funding_dupes=int((d == 0).sum()),
                   funding_gaps=int((d > mode * 1.5).sum()),
                   avg_volume=float(w["volume"].mean()),
                   avg_funding=float(np.nanmean(v)),
                   covers_window=bool(int(ltp.time.iloc[0]) <= STUDY_START),
                   pre_history_days=(STUDY_START - int(ltp.time.iloc[0])) / 86400)
        reasons = []
        if not rec["covers_window"]:
            reasons.append("listing starts inside window")
        if rec["pre_history_days"] < 180:
            reasons.append("<180d pre-window history")
        if rec["funding_nan"] / max(len(fw), 1) > 0.01:
            reasons.append(">1% missing funding")
        rec["eligible"] = not reasons
        rec["reason"] = "; ".join(reasons) if reasons else "ok"
        if rec["eligible"]:
            universe.append(s)
        rows.append(rec)
    return pd.DataFrame(rows), universe


def splits(last: int) -> dict:
    span = last - STUDY_START
    a = STUDY_START + int(span * 0.60); b = STUDY_START + int(span * 0.80)
    return {"train": (STUDY_START, a), "valid": (a, b), "test": (b, last)}


def summarise(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return dict(label=label, trades=0, note="no trades")
    net = df["net_bps"].to_numpy("float64")
    de = trade_design_effect(df.assign(r_net=net))
    bs = block_bootstrap_mean(net, mean_block=5.0, n_boot=3000, seed=11)
    t_adj = bs["t"] / np.sqrt(de["deff"]) if np.isfinite(bs["t"]) else np.nan
    wins = net[net > 0]; losses = net[net <= 0]
    months = pd.to_datetime(df["entry_time"], unit="s").dt.to_period("M")
    quarters = pd.to_datetime(df["entry_time"], unit="s").dt.to_period("Q")
    return dict(
        label=label, trades=int(len(df)),
        effective_n=round(de["n_eff"], 1),
        win_rate=round(float((net > 0).mean()), 4),
        price_bps=round(float(df["price_bps"].mean()), 3),
        funding_bps=round(float(df["funding_bps"].mean()), 3),
        fee_bps=round(float(df["fee_bps"].mean()), 3),
        slippage_bps=round(float(df["slippage_bps"].mean()), 3),
        gross_bps=round(float((df.price_bps + df.funding_bps).mean()), 3),
        net_bps=round(float(net.mean()), 3),
        median_bps=round(float(np.median(net)), 3),
        ci_low=round(bs["ci_low"], 3), ci_high=round(bs["ci_high"], 3),
        t_net=round(t_adj, 3) if np.isfinite(t_adj) else None,
        price_r=round(float(df["price_r"].mean()), 4),
        funding_r=round(float(df["funding_r"].mean()), 4),
        cost_r=round(float(df["cost_r"].mean()), 4),
        net_r=round(float(df["net_r"].mean()), 4),
        profit_factor=round(float(wins.sum() / -losses.sum()), 3) if losses.sum() < 0 else None,
        max_dd_bps=round(float(np.max(np.maximum.accumulate(np.cumsum(net)) - np.cumsum(net))), 1),
        hold_mean=round(float(df["hold_hours"].mean()), 1),
        pct_positive_months=round(100 * float((df.groupby(months)["net_bps"].sum() > 0).mean()), 1),
        pct_positive_quarters=round(100 * float((df.groupby(quarters)["net_bps"].sum() > 0).mean()), 1),
        pct_long=round(100 * float((df.side > 0).mean()), 1),
        funding_gaps=int(df["funding_gap"].sum()),
    )


def run_all(data, lo, hi, *, arm, params, strict=False, cm=1.0, sm=1.0):
    frames = {}
    for s, d in data.items():
        r = hf.run(d["ltp"], d["funding"], d["costs"], start=STUDY_START, end=hi,
                   arm=arm, strict=strict, cost_multiplier=cm,
                   slippage_multiplier=sm, **params)
        f = r.to_frame()
        if len(f):
            frames[s] = f[(f.entry_time >= lo) & (f.entry_time < hi)]
    pooled = pd.concat(frames.values(), ignore_index=True) if frames else pd.DataFrame()
    return frames, pooled


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    store, cat = CandleStore(), ProductCatalog()
    candidates = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "BEATUSD", "XAUTUSD", "PAXGUSD"]
    elig, universe = eligibility(store, cat, candidates)
    print("ELIGIBILITY (frozen rule, applied before any performance)")
    cols = [c for c in ["symbol", "price_bars", "funding_obs", "funding_nan",
                        "funding_dupes", "funding_gaps", "avg_funding",
                        "pre_history_days", "eligible", "reason"] if c in elig.columns]
    print(elig[cols].to_string(index=False))
    elig.to_csv(OUT / "eligibility.csv", index=False)
    print(f"\nUNIVERSE: {universe}\n")
    if not universe:
        print("no eligible symbols"); return 1

    data = {s: dict(ltp=store.read(s, "ltp", "1m"),
                    funding=store.read(s, "funding", "1h"),
                    costs=SymbolCosts.from_spec(cat.get(s), slippage_bps=2.0))
            for s in universe}
    last = min(int(d["ltp"].time.iloc[-1]) for d in data.values())
    SP = splits(last)
    for k, (a, b) in SP.items():
        print(f"  {k:<6} {pd.Timestamp(a,unit='s').date()} -> {pd.Timestamp(b,unit='s').date()}"
              + ("   [LOCKED]" if k == "test" else ""))
    tr_lo, tr_hi = SP["train"]; va_lo, va_hi = SP["valid"]

    results = {}
    for arm in ("A", "B"):
        for strict, tag in ((False, "pre-registered >="), (True, "strict > [pathology fix]")):
            _, tr = run_all(data, tr_lo, tr_hi, arm=arm, params=hf.PRIMARY, strict=strict)
            frames, va = run_all(data, va_lo, va_hi, arm=arm, params=hf.PRIMARY, strict=strict)
            key = f"{arm}|{'strict' if strict else 'preregistered'}"
            results[key] = dict(train=summarise(tr, "train"), valid=summarise(va, "valid"),
                                tr=tr, va=va, frames=frames)
            print(f"\n=== ARM {arm}  ({tag})")
            for split in ("train", "valid"):
                m = results[key][split]
                if not m["trades"]:
                    print(f"    {split}: no trades"); continue
                print(f"    {split}: n={m['trades']:<5} win={m['win_rate']:.3f} | "
                      f"PRICE {m['price_bps']:+7.2f}  FUNDING {m['funding_bps']:+6.2f}  "
                      f"COST {-(m['fee_bps']+m['slippage_bps']):+6.2f}  "
                      f"=> GROSS {m['gross_bps']:+7.2f}  NET {m['net_bps']:+7.2f} bps  "
                      f"t={m['t_net']}")

    json.dump({k: {kk: v[kk] for kk in ("train", "valid")} for k, v in results.items()},
              open(OUT / "arms.json", "w"), indent=2, default=str)

    # ---- detail on the pre-registered Arm A ------------------------------
    key = "A|preregistered"
    both = pd.concat([results[key]["tr"], results[key]["va"]], ignore_index=True)
    if not both.empty:
        both.to_csv(OUT / "trades_armA.csv", index=False)
        print("\nPER SYMBOL (Arm A, train+valid)")
        rows = [summarise(g, s) for s, g in both.groupby("symbol")]
        ps = pd.DataFrame(rows)
        print(ps[["label", "trades", "win_rate", "price_bps", "funding_bps",
                  "net_bps", "profit_factor", "max_dd_bps"]].to_string(index=False))
        pos = int((ps.net_bps > 0).sum())
        print(f"  symbols with positive net: {pos}/{len(ps)}")
        ps.to_csv(OUT / "per_symbol.csv", index=False)

        print("\nLONG vs SHORT (Arm A)")
        print(pd.DataFrame([summarise(g, "long (neg funding)" if k > 0 else "short (pos funding)")
                            for k, g in both.groupby("side")])
              [["label", "trades", "win_rate", "price_bps", "funding_bps", "net_bps"]]
              .to_string(index=False))

        # regime buckets: pre-defined terciles of 7d realised vol at entry
        print("\nBY VOLATILITY REGIME (pre-defined terciles of trailing 7d realised vol)")
        vol_rows = []
        for s, g in both.groupby("symbol"):
            h1 = resample_ohlcv(data[s]["ltp"][data[s]["ltp"].time >= STUDY_START], 60)
            c = h1["close"].to_numpy("float64")
            rv = pd.Series(np.concatenate(([np.nan], np.diff(np.log(c))))).rolling(
                24 * 7, min_periods=24 * 7).std().shift(1).to_numpy()
            idx = np.searchsorted(h1["time"].to_numpy("int64"), g["entry_time"].to_numpy("int64"))
            idx = np.clip(idx, 0, len(rv) - 1)
            vol_rows.append(g.assign(rv=rv[idx]))
        vb = pd.concat(vol_rows, ignore_index=True).dropna(subset=["rv"])
        if len(vb) > 30:
            q = vb["rv"].quantile([1 / 3, 2 / 3]).to_numpy()
            vb["regime"] = np.where(vb.rv <= q[0], "low",
                                    np.where(vb.rv <= q[1], "normal", "high"))
            print(pd.DataFrame([summarise(g, r) for r, g in vb.groupby("regime")])
                  [["label", "trades", "price_bps", "funding_bps", "net_bps"]]
                  .to_string(index=False))

        print("\nBY FUNDING EXTREMITY (quartile of |funding| at signal)")
        b2 = both.assign(absf=both.funding_at_signal.abs())
        # No explicit labels: the +/-0.01 point mass collapses quantile edges,
        # so the number of surviving bins is data-dependent.
        b2["bucket"] = pd.qcut(b2.absf, 4, duplicates="drop")
        print(pd.DataFrame([summarise(g, str(r)) for r, g in b2.groupby("bucket", observed=True)])
              [["label", "trades", "price_bps", "funding_bps", "net_bps"]].to_string(index=False))

        print("\nFUNDING PERSISTENCE (descriptive only, never a filter)")
        pr = []
        for s, g in both.groupby("symbol"):
            p = hf.funding_persistence(data[s]["funding"], g, STUDY_START)
            if not p.empty:
                pr.append(p.mean().rename(s))
        if pr:
            print(pd.DataFrame(pr).round(3).to_string())

        print("\nNULL MODELS (Arm A, train+valid)")
        pools = {"A": [], "B": [], "C": []}
        for s, g in both.groupby("symbol"):
            nn = run_nulls(data[s]["ltp"], data[s]["funding"], data[s]["costs"], g,
                           start=STUDY_START, end=va_hi,
                           hold_h=hf.PRIMARY["hold_h"], n_sims=40, seed=3)
            for k2 in pools:
                if nn[k2].size:
                    pools[k2].append(nn[k2])
        strat = both["net_bps"].to_numpy("float64")
        names = {"A": "A vol-matched random timing", "B": "B randomised sign",
                 "C": "C funding shifted vs price"}
        null_out = {}
        for k2, lbl in names.items():
            if not pools[k2]:
                continue
            pool = np.concatenate(pools[k2])
            cmp = bootstrap_diff(strat, pool, mean_block=5.0, n_boot=3000, seed=17)
            null_out[k2] = dict(null_mean=float(pool.mean()), n=int(pool.size),
                                diff=float(cmp["diff"]), t=float(cmp["t"]))
            print(f"  {lbl:<30} null={pool.mean():+7.2f} bps  strat-null={cmp['diff']:+7.2f}  "
                  f"CI[{cmp['ci_low']:+.2f},{cmp['ci_high']:+.2f}]  t={cmp['t']:.2f}")
        json.dump(null_out, open(OUT / "nulls.json", "w"), indent=2)

    # ---- cost sensitivity ------------------------------------------------
    print("\nCOST SENSITIVITY (Arm A)")
    for cm, sm, lbl in ((1.0, 1.0, "realistic taker"), (0.4, 1.0, "maker-equivalent"),
                        (1.0, 0.0, "zero slippage"), (0.0, 0.0, "zero cost [diagnostic]")):
        _, tr = run_all(data, tr_lo, tr_hi, arm="A", params=hf.PRIMARY, cm=cm, sm=sm)
        _, va = run_all(data, va_lo, va_hi, arm="A", params=hf.PRIMARY, cm=cm, sm=sm)
        f = pd.concat([tr, va], ignore_index=True)
        if not f.empty:
            print(f"  {lbl:<22} n={len(f):>5} gross={(f.price_bps+f.funding_bps).mean():+7.2f} "
                  f"net={f.net_bps.mean():+7.2f} bps")

    # ---- pre-declared grid, validation only ------------------------------
    print("\nPRE-DECLARED GRID (validation)")
    rows = []
    for arm in ("A", "B"):
        keys = ["low_pctl", "high_pctl", "hold_h"] + (["price_lookback_h"] if arm == "B" else [])
        for combo in itertools.product(*(hf.GRID[k] for k in keys)):
            p = dict(zip(keys, combo))
            p.setdefault("price_lookback_h", 24)
            _, va = run_all(data, va_lo, va_hi, arm=arm, params=p)
            r_ = summarise(va, f"arm{arm}|" + "|".join(f"{k}={v}" for k, v in p.items()))
            r_.update(arm=arm, **p)
            rows.append(r_)
    grid = pd.DataFrame(rows); grid.to_csv(OUT / "grid.csv", index=False)
    ok = grid[grid.trades >= 30]
    print(f"  arms: {len(grid)} | with >=30 trades: {len(ok)}")
    if len(ok):
        print(f"  positive NET on validation:   {int((ok.net_bps>0).sum())}/{len(ok)}")
        print(f"  positive GROSS on validation: {int((ok.gross_bps>0).sum())}/{len(ok)}")
        print("\n  best 5 by net (NOT a selection):")
        print(ok.nlargest(5, "net_bps")[
            ["arm", "low_pctl", "high_pctl", "hold_h", "trades",
             "price_bps", "funding_bps", "net_bps", "t_net"]].to_string(index=False))

    # ---- gate --------------------------------------------------------------
    a = results["A|preregistered"]
    promising = (a["train"]["trades"] and a["valid"]["trades"]
                 and (a["train"].get("net_bps") or 0) > 0
                 and (a["valid"].get("net_bps") or 0) > 0)
    print("\n" + "=" * 78)
    print(f"PROMISING (net > 0 on train AND validation, Arm A): {'MET' if promising else 'NOT MET'}")
    print("TEST NOT COMPUTED (locked).")
    print(f"\nwritten to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
