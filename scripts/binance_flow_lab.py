"""Does Binance order flow before a Delta entry predict its outcome? (docs/binance_flow_prereg.md, 2026-10-12)

    PYTHONPATH=. <python with lightgbm + numba> scripts/binance_flow_lab.py

For each window (DEV, then OLD): the engine's own hold-to-3R trades (labkit,
per-coin spread slippage), the candle lab's 29 base features computed on that
window's cache (its code, exec'd with the data path patched; funding from a
CLOSED bar), ~17 flow features from Binance 1m candles and 5m metrics at the
decision instant D = entry + 300, then the bundle lab's walk-forward test.
"""
import glob, json, math, pathlib, sys, tempfile
import numpy as np, pandas as pd
import lightgbm as lgb
from sklearn.metrics import roc_auc_score
sys.path.insert(0, "."); sys.path.insert(0, "scripts")
import labkit as K

OUT = pathlib.Path("out/sweep/five_min_arm_lab/binance_flow"); OUT.mkdir(parents=True, exist_ok=True)
SEEDS = (11, 22, 33, 44, 55); DRAWS = 2000; Q = 0.25; DAY = 86400
FIRST_TEST = {"DEV 2025-12..2026-09": "2026-04", "OLD 2024-06..2025-12": "2024-10"}
STALE = 7200


def base_features(root, T):
    """The candle lab's 29 features for trade list T, on the cache under root."""
    src = pathlib.Path("scripts/entry_filter_ml_lab.py").read_text().replace('f"data/candles/', 'f"' + root + '/candles/')
    tmp = pathlib.Path(tempfile.mkdtemp()); (tmp / "trades").mkdir()
    for s, g in T.groupby("symbol"):
        g.to_csv(tmp / "trades" / f"{s}.csv", index=False)
    g = {"__name__": "lab"}; argv = sys.argv; sys.argv = ["lab", str(tmp)]
    exec(compile(src[:src.index("\nX, ok = features()")], "candle_lab_prefix", "exec"), g); sys.argv = argv
    X, ok = g["features"](); T2 = g["T"].reset_index(drop=True); X = X.reset_index(drop=True)
    side = T2.side.to_numpy(); cut = T2.entry_time.to_numpy("int64") - 300
    for s in np.unique(T2.symbol):                                       # funding from a CLOSED hourly bar
        fd = pd.read_parquet(f"{root}/candles/{s}/funding_1h.parquet").sort_values("time"); m = (T2.symbol == s).to_numpy()
        j = np.searchsorted(fd.time.to_numpy("int64") + 3600, cut[m], side="right") - 1
        fr = np.where(j >= 0, fd.close.to_numpy(float)[np.maximum(j, 0)], np.nan); X.loc[m, "funding"] = fr; X.loc[m, "al_funding"] = side[m] * fr
    return T2, X, ok


def flow_features(T):
    F = pd.DataFrame(index=range(len(T)), dtype=float); side = T.side.to_numpy(); D = T.entry_time.to_numpy("int64") + 300
    for s in np.unique(T.symbol):
        m = np.flatnonzero(T.symbol.to_numpy() == s); d = pathlib.Path("data/binance") / s
        if not (d / "klines_1m.parquet").exists():
            continue
        k = pd.read_parquet(d / "klines_1m.parquet"); t = k.open_time.to_numpy("int64")
        cv, cc, cb = (np.concatenate([[0.0], np.cumsum(k[c].to_numpy(float))]) for c in ("volume", "count", "taker_buy_volume"))
        hi = np.searchsorted(t, D[m] - 60, side="right")                      # bars with open_time <= D - 60 (closed by D)
        def win(mins):
            lo = np.searchsorted(t, D[m] - 60 - mins * 60, side="right")
            return cv[hi] - cv[lo], cc[hi] - cc[lo], cb[hi] - cb[lo]
        v24, c24, _ = win(1440); fresh = (hi > 0) & (t[np.maximum(hi - 1, 0)] >= D[m] - 600)   # last closed bar within 10 min
        for w in (5, 15, 30, 60):
            v, c, b = win(w); tbs = np.where(v > 0, b / np.where(v > 0, v, 1), np.nan)
            F.loc[m, f"al_tbs_{w}"] = np.where(fresh, side[m] * (2 * tbs - 1), np.nan)
            if w == 15:
                F.loc[m, "vol_surge_15"] = np.where(fresh & (v24 > 0), v / (v24 / 96), np.nan)
                F.loc[m, "cnt_surge_15"] = np.where(fresh & (c24 > 0), c / (c24 / 96), np.nan)
                F.loc[m, "size_15"] = np.where(fresh & (c > 0) & (c24 > 0), (v / np.maximum(c, 1)) / (v24 / np.maximum(c24, 1)), np.nan)
        F.loc[m, "al_tbs_chg"] = F.loc[m, "al_tbs_5"].to_numpy() - F.loc[m, "al_tbs_60"].to_numpy()
        if (d / "metrics_5m.parquet").exists():
            mt = pd.read_parquet(d / "metrics_5m.parquet"); mtime = mt.time.to_numpy("int64")
            def at(x):
                j = np.searchsorted(mtime, x, side="right") - 1; ok = (j >= 0) & (mtime[np.maximum(j, 0)] >= x - STALE)
                return j, ok
            j0, ok0 = at(D[m]); j1, ok1 = at(D[m] - 3600); j4, ok4 = at(D[m] - 14400); jd, okd = at(D[m] - DAY)
            col = lambda c, j: mt[c].to_numpy(float)[np.maximum(j, 0)]
            oi = col("sum_open_interest", j0)
            with np.errstate(divide="ignore", invalid="ignore"):
                F.loc[m, "oi_chg_1h"] = np.where(ok0 & ok1, np.log(oi / col("sum_open_interest", j1)), np.nan)
                F.loc[m, "oi_chg_4h"] = np.where(ok0 & ok4, np.log(oi / col("sum_open_interest", j4)), np.nan)
                F.loc[m, "oi_chg_24h"] = np.where(ok0 & okd, np.log(oi / col("sum_open_interest", jd)), np.nan)
                tr = np.log(col("sum_taker_long_short_vol_ratio", j0)); tr1 = np.log(col("sum_taker_long_short_vol_ratio", j1))
                F.loc[m, "al_taker_ratio"] = np.where(ok0, side[m] * tr, np.nan)
                F.loc[m, "al_taker_ratio_chg_1h"] = np.where(ok0 & ok1, side[m] * (tr - tr1), np.nan)
                F.loc[m, "al_top_acc"] = np.where(ok0, side[m] * np.log(col("count_toptrader_long_short_ratio", j0)), np.nan)
                F.loc[m, "al_top_pos"] = np.where(ok0, side[m] * np.log(col("sum_toptrader_long_short_ratio", j0)), np.nan)
                F.loc[m, "al_global"] = np.where(ok0, side[m] * np.log(col("count_long_short_ratio", j0)), np.nan)
    return F.replace([np.inf, -np.inf], np.nan)


def fit(X, cols, tr, te, seed, labels):
    mdl = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.03, num_leaves=8, min_child_samples=40, subsample=0.8, subsample_freq=1,
                             colsample_bytree=0.8, reg_lambda=5.0, random_state=seed, verbose=-1)
    mdl.fit(X.loc[tr, cols], labels[tr]); return mdl.predict_proba(X.loc[te, cols])[:, 1]


def test_window(win, root):
    data = K.load_window(root, K.SCREEN); bks = K.books(data)
    th, _ = K.run(bks, data, "hold-to-3R"); tt, _ = K.run(bks, data, "trail"); del bks
    T = th[["symbol", "side", "entry_time", "exit_time", "cost_per_r", "r_multiple"]].rename(columns={"r_multiple": "r_baseline", "exit_time": "exit_baseline"})
    tr_ = tt[["symbol", "entry_time", "r_multiple"]].rename(columns={"r_multiple": "r_trail"})
    T = T.merge(tr_, on=["symbol", "entry_time"], how="left"); T["exit_trail"] = T.exit_baseline; T["r_ladder"] = np.nan; T["exit_ladder"] = T.exit_baseline
    T = T.sort_values("entry_time").reset_index(drop=True)
    T, Xb, ok = base_features(root, T); F = flow_features(T); X = pd.concat([Xb, F], axis=1)
    del data
    N = len(T); y = (T.r_baseline >= 2.0).astype(int).to_numpy(); net3 = T.r_baseline.to_numpy(float); nett = T.r_trail.to_numpy(float)
    fee = T.cost_per_r.to_numpy(float); et = T.entry_time.to_numpy("int64"); day = et // DAY; ex = T.exit_baseline.to_numpy("int64")
    month = pd.to_datetime(T.entry_time, unit="s").dt.strftime("%Y-%m").to_numpy()
    mstart = {mm: int(pd.Timestamp(mm + "-01", tz="UTC").timestamp()) for mm in sorted(set(month))}; ms = sorted(mstart)
    MONTHS = [mm for mm in ms if mm >= FIRST_TEST[win]]; PREV = {mm: ms[ms.index(mm) - 1] for mm in MONTHS}
    FLOW = list(F.columns); FEE = ("fee_r", "vol_24h"); BASE_NF = [c for c in Xb.columns if c not in FEE]; PRIMARY = BASE_NF + FLOW
    TEST = np.isin(month, MONTHS); n_flow = int((TEST & F.notna().any(axis=1).to_numpy()).sum())
    print(f"\n==== {win}: {N} trades, {len(FLOW)} flow + {len(BASE_NF)} base features; test months {MONTHS[0]}..{MONTHS[-1]}; "
          f"{int(TEST.sum())} test entries, {n_flow} with flow features ====")
    print("flow coverage: " + ", ".join(f"{c} {100 * F[c].notna().mean():.0f}%" for c in FLOW[:6]) + " ...")
    X.assign(symbol=T.symbol, side=T.side, entry_time=et, label=y, month=month).round(6).to_csv(OUT / f"features_{win[:3]}.csv.gz", index=False)
    if n_flow < 1500:
        print("  CANNOT ANSWER: fewer than 1,500 test entries carry flow features"); return None
    RNG = np.random.default_rng(7)

    def walk(cols, labels=y, data=X):
        out = {}
        for mm in MONTHS:
            pm = PREV[mm]; trA = ex < mstart[pm]; teA = month == pm; trB = ex < mstart[mm]; teB = month == mm
            if trA.sum() < 300 or teA.sum() < 30:
                continue
            sA = np.mean([fit(data, cols, trA, teA, sd, labels) for sd in SEEDS], axis=0)
            sB = np.mean([fit(data, cols, trB, teB, sd, labels) for sd in SEEDS], axis=0)
            out[mm] = (sA, np.flatnonzero(teB), sB)
        return out

    picks = lambda W, q: np.concatenate([idx[sB >= np.quantile(sA, 1 - q)] for sA, idx, sB in W.values()]).astype(int)
    aucs = lambda W: [roc_auc_score(y[idx], sB) for _, idx, sB in W.values() if len(set(y[idx])) > 1]

    def p_day(idx, arrs):
        tot = np.zeros((DRAWS, len(arrs))); k_by_day = pd.Series(day[idx]).value_counts()
        for dd, k in k_by_day.items():
            cand = np.flatnonzero(TEST & (day == dd)); sel = cand[np.argsort(RNG.random((DRAWS, len(cand))), axis=1)[:, :k]]
            tot += np.stack([np.nansum(a[sel], axis=1) for a in arrs], axis=1)
        obs = np.array([np.nanmean(a[idx]) for a in arrs]); return ((tot / len(idx)) >= obs).mean(axis=0)

    def cheap(q):
        return np.concatenate([np.flatnonzero((month == mm) & (fee <= np.quantile(fee[month == PREV[mm]], q))) for mm in MONTHS])

    def row(name, idx):
        p3, pt = p_day(idx, [net3, nett]); by = pd.Series(net3[idx]).groupby(month[idx]).mean()
        print(f"    {name:26s} n {len(idx):5d}  3R hit {100 * y[idx].mean():5.1f}%  fee {fee[idx].mean():.3f}R  net 3R {net3[idx].mean():+.3f}R (p day {p3:.3f})"
              f"  trail {np.nanmean(nett[idx]):+.3f}R (p {pt:.3f})  months+ {int((by > 0).sum())}/{len(by)}")
        return dict(net3=float(net3[idx].mean()), p3=float(p3))

    Xp = X.copy(); Xp["plant"] = 0.25 * y + np.random.default_rng(0).normal(size=N); v1 = float(np.mean(aucs(walk(PRIMARY + ["plant"], data=Xp))))
    ysh = np.random.default_rng(0).permutation(y); v2 = p_day(picks(walk(PRIMARY, labels=ysh), Q), [net3, nett])
    V = v1 >= 0.52 and v2[0] > 0.05
    print(f"  V1 planted AUC {v1:.3f} (>= 0.52) ; V2 shuffled p {v2[0]:.3f} (> 0.05) -> {'valid' if V else 'INVALID'}")
    if not V:
        return None
    a = np.flatnonzero(TEST); print(f"  all test entries: n {len(a)}  3R hit {100 * y[a].mean():.1f}%  net 3R {net3[a].mean():+.3f}R  trail {np.nanmean(nett[a]):+.3f}R")
    WP = walk(PRIMARY); A = aucs(WP); print(f"  PRIMARY AUC by month: {', '.join(f'{v:.3f}' for v in A)}; mean {np.mean(A):.3f}")
    P = row("PRIMARY 25%", picks(WP, Q)); C = row("cheap fees 25%", cheap(Q))
    for name, cols in (("flow only 25%", FLOW), ("base only 25%", BASE_NF)):
        W = walk(cols); r = row(name, picks(W, Q)); print(f"    {'':26s} mean AUC {np.mean(aucs(W)):.3f}")
    P1, P2, P3 = np.mean(A) >= 0.53, P["p3"] <= 0.05, P["net3"] > C["net3"]
    print(f"  P1 AUC >= 0.530: {'PASS' if P1 else 'fail'}   P2 beats day-matched random p <= 0.05: {'PASS' if P2 else 'fail'}   "
          f"P3 above cheap rule: {'PASS' if P3 else 'fail'}   -> {'PASS' if P1 and P2 and P3 else 'FAIL'}")
    return P1 and P2 and P3


if __name__ == "__main__":
    res = {win: test_window(win, root) for win, root in K.WINDOWS.items()}
    print("\nVERDICT:", "PASS on both windows -- a lead for a plain-rule follow-up, not a bot change" if all(v is True for v in res.values())
          else "FAIL" if any(v is False for v in res.values()) else "CANNOT ANSWER")
    import lightgbm, sklearn
    (OUT / "versions.json").write_text(json.dumps(dict(lightgbm=lightgbm.__version__, numpy=np.__version__, pandas=pd.__version__, sklearn=sklearn.__version__)))
