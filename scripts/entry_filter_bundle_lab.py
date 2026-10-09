"""Can open interest, the signal bar or the longer regime pick the strategy's good entries? (2026-10-09)

    PYTHONPATH=. <python with lightgbm + numba> scripts/entry_filter_bundle_lab.py
    (lightgbm is not in this repo's venv; run from a scratch environment holding
    the repo's own numpy/pandas/numba versions plus lightgbm and scikit-learn.)

PRE-REGISTERED in docs/entry_filter_bundle_prereg.md, committed before the
open-interest data was fetched. Read that first: it holds the question, the
features, the pass rule (P1-P3), the validity checks (V1-V2) and the hold-out.
This script implements it and nothing else.

WHY. The candle ML (scripts/entry_filter_ml_lab.py) found nothing: AUC 0.490.
These are the three sources it never had: open interest (Delta's undocumented
OI:<SYMBOL> series, scripts/fetch_oi_history.py), the 5m signal bar itself
(the candle lab stopped 10 minutes before the fill), and regime beyond 24h.

TIMING. The engine fills at the close of the 5m signal bar; entry_time is that
bar's open, so the decision instant is D = entry_time + 300. New features use
1m bars that started before D, 5m OI bars that closed by D, and the engine's
own 5m %R. Base features are the candle lab's, computed by its own code, with
one fix: funding reads only a bar that had CLOSED (the lab read the open hour).

ORDER. V1 (planted signal) and V2 (shuffled labels) run first; if either
fails the run stops as INVALID before the real result is computed.

RESULT 2026-10-09, the one pre-registered run
(out/sweep/five_min_arm_lab/symbol_screen/entry_filter_bundle/result_2026-10-09.txt):
VALID (V1 planted 0.560 >= 0.520; V2 shuffled p 0.069 / 0.917) and FAIL on all three.
  * P1 mean AUC 0.516 (months 0.496, 0.484, 0.501, 0.534, 0.567, 0.517) < 0.530.
  * P2 25% picks -0.053R hold-to-3R (p 0.66 vs day-matched random), -0.028R trail
    (p 0.062); all entries -0.083R / -0.093R.
  * P3 below the cheap-fee rule on both exits (+0.017R / -0.017R).
  * Open interest adds nothing: base + OI AUC 0.487 against candle-only 0.490.
    Regime and the signal bar move AUC to 0.510 / 0.507 -- below the bar, and
    the picks still lose money after fees on both exits.
  * The candle-only row reproduces the 10-05 lab (AUC 0.490) with the funding
    leak fixed. 4,950 / 4,950 entries match an engine signal.
  Not judged: PRIMARY 50% trail -0.042R, p 0.002 vs random -- one cell of many,
  still losing, 1/6 months positive on hold-to-3R. Not evidence.
"""
import glob, json, math, pathlib, sys
import numpy as np, pandas as pd
import lightgbm as lgb
from sklearn.metrics import roc_auc_score

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.data.quality import tradable_mask
from deltabt.harness import _resampled, load_symbol

O = "out/sweep/five_min_arm_lab/symbol_screen"; OUT = pathlib.Path(O) / "entry_filter_bundle"; DAY = 86400
SEEDS = (11, 22, 33, 44, 55); DRAWS = 2000; Q = 0.25
PATCHY = {"AIOUSD", "AKEUSD", "AXSUSD", "BANKUSD", "BLESSUSD", "HYPEUSD", "MUBARAKUSD", "STRKUSD", "SUIUSD", "WIFUSD", "ZROUSD"}

# ---- base features: the candle lab's own code --------------------------------
src = pathlib.Path("scripts/entry_filter_ml_lab.py").read_text()
g = {"__name__": "lab"}; argv = sys.argv; sys.argv = ["lab", O]
exec(compile(src[:src.index("\nX, ok = features()")], "candle_lab_prefix", "exec"), g); sys.argv = argv
X, ok = g["features"](); T = g["T"]
assert ok.all(), "candle lab dropped entries"
T = T.reset_index(drop=True); X = X.reset_index(drop=True); BASE = list(X.columns)
N = len(T); side = T.side.to_numpy(); et = T.entry_time.to_numpy("int64"); D = et + 300; sym = T.symbol.to_numpy()
cut = et - 300                                                         # the candle lab's cut
for s in np.unique(sym):                                               # FIX: funding from a CLOSED hourly bar
    fd = pd.read_parquet(f"data/candles/{s}/funding_1h.parquet").sort_values("time")
    m = sym == s; j = np.searchsorted(fd.time.to_numpy("int64") + 3600, cut[m], side="right") - 1
    fr = np.where(j >= 0, fd.close.to_numpy(float)[np.maximum(j, 0)], np.nan)
    X.loc[m, "funding"] = fr; X.loc[m, "al_funding"] = side[m] * fr

# ---- 1m traded bars, OI, engine signals ----------------------------------------
def traded(s):
    d = pd.read_parquet(f"data/candles/{s}/ltp_1m.parquet").sort_values("time")
    d = d[~((d.volume == 0) & (d.open == d.close) & (d.high == d.low))]
    return {k: d[k].to_numpy("int64" if k == "time" else float) for k in ("time", "open", "high", "low", "close", "volume")}
BARS = {s: traded(s) for s in np.unique(sym)}
OI = {}
for s in np.unique(sym):
    d = pd.read_parquet(f"data/candles/{s}/oi_5m.parquet").sort_values("time")
    OI[s] = (d.time.to_numpy("int64"), d.close.to_numpy(float))

def price_at(b, x):
    """Close of the last traded 1m bar that had CLOSED by x (NaN if none within a day)."""
    i = np.searchsorted(b["time"], np.asarray(x) - 60, side="right") - 1
    ok_ = (i >= 0) & (b["time"][np.maximum(i, 0)] >= np.asarray(x) - DAY)
    return np.where(ok_, b["close"][np.maximum(i, 0)], np.nan)

def oi_at(s, x):
    """OI at the close of the last 5m bar that had closed by x (NaN if > 1h stale or <= 0)."""
    t, v = OI[s]; x = np.asarray(x); i = np.searchsorted(t, x - 300, side="right") - 1
    val = np.where(i >= 0, v[np.maximum(i, 0)], np.nan)
    return np.where((i >= 0) & (t[np.maximum(i, 0)] + 300 >= x - 3600) & (val > 0), val, np.nan)

spec = build_spec("manual_scalp_both_t3", 5); cache = {}; WPR = np.full(N, np.nan); WPRP = np.full(N, np.nan); matched = np.zeros(N, bool)
for s in np.unique(sym):
    d = load_symbol(s); l = d["ltp"]; flat = (l.volume == 0) & (l.open == l.close) & (l.high == l.low)
    l = l[~flat].reset_index(drop=True)
    d = dict(symbol=s, ltp=l, mark=d["mark"], funding=d["funding"], tradable=tradable_mask(l))
    P = _resampled(d, 5, cache)[0]; C = _resampled(d, 1, cache)[0]
    sig = rulecore.to_engine_signals(rulecore.compute(P, C, spec)); pt = P.time.to_numpy("int64")
    m = np.flatnonzero(sym == s); i = np.searchsorted(pt, et[m]); i = np.minimum(i, len(pt) - 1)
    hit = pt[i] == et[m]
    matched[m] = hit & np.where(side[m] > 0, sig.long_entry[i], sig.short_entry[i])
    WPR[m] = np.where(hit, sig.wpr[i], np.nan); WPRP[m] = np.where(hit & (i > 0), sig.wpr[np.maximum(i - 1, 0)], np.nan)

# ---- new features ------------------------------------------------------------------
F = pd.DataFrame(index=range(N), dtype=float)
vol24 = X.vol_24h.to_numpy(float)
for s in np.unique(sym):
    m = np.flatnonzero(sym == s); b = BARS[s]; sd = side[m]; Dm = D[m]; v24 = vol24[m]
    pD = price_at(b, Dm)
    # signal bar: traded 1m bars with entry_time <= t < D
    lo = np.searchsorted(b["time"], et[m], side="left"); hi = np.searchsorted(b["time"], Dm, side="left")
    sbo = np.full(len(m), np.nan); sbc = sbo.copy(); sbh = sbo.copy(); sbl = sbo.copy(); sbv = sbo.copy(); sbn = (hi - lo).astype(float)
    for k, (a, z) in enumerate(zip(lo, hi)):
        if z > a:
            sbo[k], sbc[k] = b["open"][a], b["close"][z - 1]; sbh[k], sbl[k] = b["high"][a:z].max(), b["low"][a:z].min(); sbv[k] = b["volume"][a:z].sum()
    v0 = np.searchsorted(b["time"], Dm - DAY, side="left"); cv = np.concatenate([[0.0], np.cumsum(b["volume"])])
    v24sum = cv[hi] - cv[v0]
    rng_ = sbh - sbl
    F.loc[m, "sb_ret_al"] = sd * np.log(sbc / sbo) / v24
    F.loc[m, "sb_range"] = np.log(sbh / sbl) / v24
    F.loc[m, "sb_body"] = np.where(rng_ > 0, np.abs(sbc - sbo) / np.where(rng_ > 0, rng_, 1), np.nan)
    F.loc[m, "sb_close_pos_al"] = np.where(rng_ > 0, np.where(sd > 0, sbc - sbl, sbh - sbc) / np.where(rng_ > 0, rng_, 1), np.nan)
    F.loc[m, "sb_vol_rel"] = np.where(v24sum > 0, sbv / (v24sum / 288), np.nan)
    F.loc[m, "sb_minutes"] = sbn
    F.loc[m, "al_ret_10m"] = sd * np.log(pD / price_at(b, Dm - 600)) / v24
    w, wp = WPR[m], WPRP[m]; wa = np.where(sd > 0, w, -100 - w); wpa = np.where(sd > 0, wp, -100 - wp)
    F.loc[m, "wpr_al"] = wa; F.loc[m, "wpr_jump_al"] = wa - wpa
    # regime
    for name, h in (("3d", 3 * DAY), ("7d", 7 * DAY), ("30d", 30 * DAY)):
        F.loc[m, f"al_ret_{name}"] = sd * np.log(pD / price_at(b, Dm - h)) / (v24 * math.sqrt(h / 60))
    lc = np.log(b["close"]); dl = np.diff(lc); c2 = np.concatenate([[0.0], np.cumsum(dl)]); c2s = np.concatenate([[0.0], np.cumsum(dl * dl)])
    a30 = np.searchsorted(b["time"], Dm - 30 * DAY, side="left"); n30 = (hi - 1 - a30).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        s1 = c2[np.maximum(hi - 1, 0)] - c2[a30]; s2 = c2s[np.maximum(hi - 1, 0)] - c2s[a30]
        vol30 = np.sqrt(np.maximum(s2 / n30 - (s1 / n30) ** 2, 0))
    F.loc[m, "vol_regime"] = np.where((n30 > 20 * 1440 * 0.2) & (vol30 > 0), v24 / vol30, np.nan)
    pb = price_at(BARS["BTCUSD"], Dm)
    F.loc[m, "al_btc_7d"] = sd * np.log(pb / price_at(BARS["BTCUSD"], Dm - 7 * DAY))
    # open interest
    o = {k: oi_at(s, Dm - k) for k in (0, 300, 900, 3600, 14400, DAY)}
    for name, k in (("5m", 300), ("15m", 900), ("1h", 3600), ("4h", 14400), ("24h", DAY)):
        F.loc[m, f"oi_chg_{name}"] = np.log(o[0] / o[k])
    t5, v5 = OI[s]; cs = np.concatenate([[0.0], np.cumsum(v5)]); e7 = np.searchsorted(t5, Dm - 300, side="right"); s7 = np.searchsorted(t5, Dm - 300 - 7 * DAY, side="right")
    mean7 = np.where(e7 - s7 > 288, (cs[e7] - cs[s7]) / np.maximum(e7 - s7, 1), np.nan)
    F.loc[m, "oi_vs_7d"] = np.log(o[0] / mean7)
    hrs = np.stack([oi_at(s, Dm - k * 3600) for k in range(170)], axis=1)
    ch = np.log(hrs[:, :-1] / hrs[:, 1:])
    with np.errstate(invalid="ignore"):
        sdv = np.nanstd(ch[:, 1:], axis=1); enough = np.isfinite(ch[:, 1:]).sum(axis=1) >= 48
    F.loc[m, "oi_chg_1h_z"] = np.where(enough & (sdv > 0), ch[:, 0] / np.where(sdv > 0, sdv, 1), np.nan)
    for name, h in (("1h", 3600), ("4h", 14400)):
        pr = np.log(pD / price_at(b, Dm - h))
        F.loc[m, f"al_oi_price_{name}"] = F.loc[m, f"oi_chg_{name}"].to_numpy() * np.sign(sd * pr)

# breadth across the other coins at D
r24 = {s: np.log(price_at(BARS[s], D) / price_at(BARS[s], D - DAY)) for s in BARS}
R = np.stack([r24[s] for s in sorted(BARS)], axis=1); own = np.array(sorted(BARS))[None, :] == sym[:, None]; R[own] = np.nan
with np.errstate(invalid="ignore"):
    F["breadth_al"] = np.nanmean(np.where(np.isfinite(R), (np.sign(R) == side[:, None]).astype(float), np.nan), axis=1)
    F["breadth_ret_al"] = side * np.nanmean(R, axis=1)
F = F.replace([np.inf, -np.inf], np.nan)
X = pd.concat([X, F], axis=1)
OI_F = [c for c in F.columns if c.startswith(("oi_", "al_oi"))]
SB_F = [c for c in F.columns if c.startswith(("sb_", "wpr")) or c == "al_ret_10m"]
REG_F = [c for c in F.columns if c not in OI_F + SB_F]
FEE = ("fee_r", "vol_24h"); ALL = list(X.columns); PRIMARY = [c for c in ALL if c not in FEE]
BASE_NF = [c for c in BASE if c not in FEE]

# ---- labels, months, walk-forward --------------------------------------------------
y = (T.r_baseline >= 2.0).astype(int).to_numpy()
net3 = T.r_baseline.to_numpy(); nett = T.r_trail.to_numpy(); gross3 = net3 + T.cost_per_r.to_numpy(); fee = T.cost_per_r.to_numpy()
month = pd.to_datetime(T.entry_time, unit="s").dt.strftime("%Y-%m").to_numpy(); day = et // DAY
mstart = {mm: int(pd.Timestamp(mm + "-01", tz="UTC").timestamp()) for mm in sorted(set(month))}
MONTHS = [mm for mm in sorted(set(month)) if mm >= "2026-04"]; PREV = {mm: sorted(mstart)[sorted(mstart).index(mm) - 1] for mm in MONTHS}
exit3 = T.exit_baseline.to_numpy()

def fit(cols, tr, te, seed, labels, data):
    mdl = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.03, num_leaves=8, min_child_samples=40, subsample=0.8,
                             subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0, random_state=seed, verbose=-1)
    mdl.fit(data.loc[tr, cols], labels[tr]); return mdl.predict_proba(data.loc[te, cols])[:, 1], mdl

def walk(cols, labels=None, data=None, rows=None):
    """Seed-mean scores per test month: (threshold-month scores, test-month scores)."""
    labels = y if labels is None else labels; data = X if data is None else data
    rows = np.ones(N, bool) if rows is None else rows; out = {}
    for mm in MONTHS:
        pm = PREV[mm]
        trA = rows & (exit3 < mstart[pm]); teA = rows & (month == pm); trB = rows & (exit3 < mstart[mm]); teB = rows & (month == mm)
        if trA.sum() < 300 or teA.sum() < 30:
            continue
        sA = np.mean([fit(cols, trA, teA, sd, labels, data)[0] for sd in SEEDS], axis=0)
        sB = np.mean([fit(cols, trB, teB, sd, labels, data)[0] for sd in SEEDS], axis=0)
        out[mm] = (sA, np.flatnonzero(teB), sB)
    return out

def picks(W, q):
    return np.concatenate([idx[sB >= np.quantile(sA, 1 - q)] for sA, idx, sB in W.values()]).astype(int)

def aucs(W):
    return [roc_auc_score(y[idx], sB) for _, idx, sB in W.values() if len(set(y[idx])) > 1]

TEST = np.isin(month, MONTHS); RNG = np.random.default_rng(7)
def p_day(idx, arrs, rows=None):
    """Day-matched random: same picks per UTC day, drawn from that day's entries."""
    pool = TEST if rows is None else TEST & rows
    tot = np.zeros((DRAWS, len(arrs))); k_by_day = pd.Series(day[idx]).value_counts()
    for dd, k in k_by_day.items():
        cand = np.flatnonzero(pool & (day == dd))
        sel = cand[np.argsort(RNG.random((DRAWS, len(cand))), axis=1)[:, :k]]
        tot += np.stack([a[sel].sum(axis=1) for a in arrs], axis=1)
    obs = np.array([a[idx].mean() for a in arrs]); return ((tot / len(idx)) >= obs).mean(axis=0)

def cheap(q, rows=None):
    rows = np.ones(N, bool) if rows is None else rows
    return np.concatenate([np.flatnonzero(rows & (month == mm) & (fee <= np.quantile(fee[rows & (month == PREV[mm])], q))) for mm in MONTHS])

def row(name, idx, q, rows=None):
    p3, pt = p_day(idx, [net3, nett], rows); by = pd.Series(net3[idx]).groupby(month[idx]).mean()
    print(f"{name:30s}{100 * q:5.0f}%{len(idx):7d}{100 * y[idx].mean():8.1f}{fee[idx].mean():7.3f}{gross3[idx].mean():+9.3f}"
          f"{net3[idx].mean():+8.3f}{p3:7.3f}{nett[idx].mean():+10.3f}{pt:7.3f}{int((by > 0).sum()):6d}/{len(by)}")
    return dict(n=int(len(idx)), net3=float(net3[idx].mean()), nett=float(nett[idx].mean()), p3=float(p3), pt=float(pt))
HDR = f"{'selector':30s}{'depth':>6s}{'trades':>7s}{'3R hit%':>8s}{'fee R':>7s}{'gross 3R':>9s}{'net 3R':>8s}{'p day':>7s}{'net trail':>10s}{'p day':>7s}{'months +':>9s}"

# ---- report: data checks -------------------------------------------------------------
print(f"{N} entries, {len(ALL)} features ({len(BASE)} base + {len(OI_F)} OI + {len(SB_F)} signal bar + {len(REG_F)} regime); "
      f"PRIMARY = {len(PRIMARY)} (no {', '.join(FEE)}); test months {MONTHS[0]}..{MONTHS[-1]}; seeds {SEEDS} averaged; day-matched random x{DRAWS}")
print(f"entries matching an engine signal of the same side at entry_time: {int(matched.sum())}/{N}")
flat = {s: float((np.diff(OI[s][1]) == 0).mean()) for s in sorted(OI)}
print("OI 5m bars unchanged from the bar before (share): " + ", ".join(f"{s} {v:.2f}" for s, v in flat.items()))
na = X[[c for c in F.columns]].isna().mean()
print("missing share, new features: " + ", ".join(f"{c} {v:.2f}" for c, v in na.items() if v > 0.005) or "none above 0.5%")
OUT.mkdir(parents=True, exist_ok=True)
X.assign(symbol=sym, side=side, entry_time=et, label=y, month=month, engine_signal_match=matched).round(6).to_csv(OUT / "features.csv.gz", index=False)

# ---- V1, V2 first -------------------------------------------------------------------------
Xp = X.copy(); Xp["plant"] = 0.25 * y + np.random.default_rng(0).normal(size=N)
v1 = float(np.mean(aucs(walk(PRIMARY + ["plant"], data=Xp))))
ysh = np.random.default_rng(0).permutation(y); Wsh = walk(PRIMARY, labels=ysh); ish = picks(Wsh, Q)
v2p = p_day(ish, [net3, nett])
V1 = v1 >= 0.520; V2 = bool((v2p > 0.05).all())
print(f"\nV1 planted signal (0.25*label + N(0,1)) in PRIMARY: mean AUC {v1:.3f} (needs >= 0.520) -> {'OK' if V1 else 'FAIL'}")
print(f"V2 shuffled labels, PRIMARY at 25%: p vs day-matched random hold-to-3R {v2p[0]:.3f}, trail {v2p[1]:.3f} (needs > 0.05 on both) -> {'OK' if V2 else 'FAIL'}")
if not (V1 and V2):
    print("\nVERDICT: INVALID -- the real result was not computed."); sys.exit(0)

# ---- the pre-registered result ---------------------------------------------------------------
a = np.flatnonzero(TEST)
print(f"\nALL entries of the test months: n {len(a)}, 3R hit {100 * y[a].mean():.1f}%, hold-to-3R net {net3[a].mean():+.3f}R, trail net {nett[a].mean():+.3f}R, fee {fee[a].mean():.3f}R")
WP = walk(PRIMARY); A = aucs(WP)
print(f"\nPRIMARY AUC by month: {', '.join(f'{v:.3f}' for v in A)}; mean {np.mean(A):.3f}")
print("\n" + HDR); res = {}
for q in (0.10, 0.25, 0.50):
    res[("primary", q)] = row("PRIMARY (no fee inputs)", picks(WP, q), q)
    res[("cheap", q)] = row("cheap fees only", cheap(q), q)
    print()
P, C_ = res[("primary", Q)], res[("cheap", Q)]
P1 = np.mean(A) >= 0.530; P2 = P["p3"] <= 0.05 and P["pt"] <= 0.05; P3 = P["net3"] > C_["net3"] and P["nett"] > C_["nett"]
print(f"P1 mean AUC {np.mean(A):.3f} >= 0.530: {'PASS' if P1 else 'fail'}")
print(f"P2 25% beats day-matched random, p hold-to-3R {P['p3']:.3f}, trail {P['pt']:.3f} (both <= 0.05): {'PASS' if P2 else 'fail'}")
print(f"P3 25% above cheap-fee rule, hold-to-3R {P['net3']:+.3f} vs {C_['net3']:+.3f}, trail {P['nett']:+.3f} vs {C_['nett']:+.3f}: {'PASS' if P3 else 'fail'}")
print(f"\nVERDICT: {'PASS -- a lead for the October-December hold-out, not a change to the bot' if (P1 and P2 and P3) else 'FAIL'}")

# ---- reported, not judged -------------------------------------------------------------------
print("\nREPORTED, NOT JUDGED (25% depth unless shown)")
print(HDR)
for name, cols in (("all features (with fee)", ALL), ("candle-only (base, no fee)", BASE_NF), ("base + OI", BASE_NF + OI_F),
                   ("base + signal bar", BASE_NF + SB_F), ("base + regime", BASE_NF + REG_F)):
    W = walk(cols); r = row(name, picks(W, Q), Q); print(f"{'':30s}  mean AUC {np.mean(aucs(W)):.3f}")
keep = ~np.isin(sym, list(PATCHY)); W = walk(PRIMARY, rows=keep)
row(f"PRIMARY without the {len(PATCHY)} patchy-OI", picks(W, Q), Q, rows=keep); row("cheap fees, same symbols", cheap(Q, keep), Q, rows=keep)
print(f"{'':30s}  mean AUC {np.mean(aucs(W)):.3f}; {int((~keep).sum())} entries dropped")
_, mdl = fit(PRIMARY, exit3 < mstart[MONTHS[-1]], month == MONTHS[-1], 11, y, X)
imp = pd.Series(mdl.booster_.feature_importance("gain"), index=PRIMARY).sort_values(ascending=False)
print("top PRIMARY features by gain (last fold, seed 11): " + ", ".join(f"{k} {100 * v / imp.sum():.0f}%" for k, v in imp.head(10).items()))
import lightgbm, sklearn, numba
(OUT / "versions.json").write_text(json.dumps(dict(lightgbm=lightgbm.__version__, numpy=np.__version__, pandas=pd.__version__,
                                                    sklearn=sklearn.__version__, numba=numba.__version__, python=sys.version.split()[0])))
