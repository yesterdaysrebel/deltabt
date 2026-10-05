"""Can an ML filter pick the strategy's good entries? (2026-10-05)

    <python with lightgbm> scripts/entry_filter_ml_lab.py out/sweep/five_min_arm_lab/symbol_screen
    (lightgbm is not in this repo's venv; run from a scratch environment.)

WHY. Owner's question: can the ML work in the memecoins-pump-or-dump repo be
applied here? That work is a selective filter -- a LightGBM model scores each
event at decision time, only the top slice is bought, the threshold is fixed
from training, outcomes are used only once they have RESOLVED, and it is judged
walk-forward on the real payoff. The same shape fits this strategy as a filter
on its entries. What does NOT transfer is its strongest ingredient: wallet
reputation and the 60-second trade tape do not exist for an exchange's
perpetuals. Here the features can only come from candles and funding.

THE TEST. The 4,950 entries of scripts/symbol_screen.py (32 symbols, the dry
run's own spec, three exits on identical entries). For each entry, ~24 features
from data known BEFORE the entry bar (1m candles ending >= 5 minutes earlier;
funding; BTC as the market; the strategy's own trades that had already CLOSED).
Label: did hold-to-3R reach its target. Walk-forward by calendar month,
April..September:
  * the model for month M is trained only on trades that had CLOSED before M;
  * the buy threshold is the score the top q% of month M-1 cleared, under a
    model that had not seen M-1 (so it is fixed before M starts);
  * each entry is decided on its own.
Depths q = 10%, 25%, 50%; 5 seeds.

HOW IT IS JUDGED, net R per trade after fees, for hold-to-3R and for the trail:
  all      every entry of the test months
  random   the same number of entries per month picked at random, 400 times
           (p = share of random pickers that did at least as well)
  cheap    the cost law alone: lowest fee cost per R at the same depth. The
           model sees fee cost as a feature, so it must BEAT this to have found
           anything new.
  no-fee   the model without the fee feature, also scored on GROSS R
  shuffled the model trained on shuffled labels (must look like random: a leak
           check)

RESULT 2026-10-05 (out/sweep/five_min_arm_lab/symbol_screen/entry_filter_ml_2026-10-05.txt),
4,950 entries, 29 features, 3,138 test entries April..September, 5 seeds.
Every entry of the test months: 3R hit 24.4%, hold-to-3R -0.083R, trail -0.093R.
  * THE MODEL FINDS NOTHING. Out-of-sample AUC by month 0.495, 0.491, 0.500,
    0.424, 0.511, 0.518 -- mean 0.490, a coin flip. Its top third of entries
    hit 3R 23.2% of the time, its bottom third 24.4%.
  * As a filter it is no better than random picking on hold-to-3R at any
    depth (10% -0.178R, 25% -0.104R, 50% -0.097R; p vs random 0.45-0.73) and
    WORSE than trading everything. On the trail it is a little less bad
    (-0.04 to -0.07R) and that is the fee feature: without it, p 0.16-0.38.
  * The leak check passes: shuffled labels look like random picking.
  * THE ONE-LINE COST RULE BEATS THE MODEL at every depth: lowest-fee 10% of
    entries +0.112R hold-to-3R (p 0.02 vs random, 356 trades, 4 of 6 months in
    profit) and -0.004R trail; 25% +0.017R; 50% +0.010R. That is the cost
    law again, and at 356 trades +0.11R is still not distinguishable from zero.
  * Feature importance is flat (5-7% each): fitted noise.
WHAT THIS DOES AND DOES NOT CLOSE. It closes a candle-and-funding entry
filter for this strategy on this data. It does not test what made the memecoin
stack work -- who is trading (wallet reputation) and the first minute of the
trade tape. The exchange analogue (aggressor-side trade flow, order-book
imbalance, open interest, liquidations) is not in this repo's archive: only
1m candles and funding are. That data would have to be recorded forward first.
"""
import glob, math, sys
import numpy as np, pandas as pd
import lightgbm as lgb

O = sys.argv[1]; DAY = 86400
T = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(f"{O}/trades/*.csv"))]).sort_values("entry_time").reset_index(drop=True)


def bars(sym):
    d = pd.read_parquet(f"data/candles/{sym}/ltp_1m.parquet").sort_values("time")
    live = ~((d.volume == 0) & (d.open == d.close) & (d.high == d.low))
    a = d[live]
    return dict(t_all=d.time.to_numpy("int64"), t=a.time.to_numpy("int64"), c=a.close.to_numpy(float),
                h=a.high.to_numpy(float), l=a.low.to_numpy(float), v=a.volume.to_numpy(float))


def window(b, cut, seconds):
    i1 = np.searchsorted(b["t"], cut, side="left"); i0 = np.searchsorted(b["t"], cut - seconds, side="left")
    return i0, i1


def price_features(b, cut, prefix=""):
    """From traded 1m bars that STARTED before `cut` - 60 (so they have closed by `cut`)."""
    out = {}
    i0, i1 = window(b, cut - 60, DAY)
    c = b["c"][i0:i1]
    if len(c) < 30:
        return None
    lr = np.diff(np.log(c)); vol24 = lr.std() or np.nan
    for name, sec in (("15m", 900), ("1h", 3600), ("4h", 14400)):
        j0, _ = window(b, cut - 60, sec)
        seg = b["c"][j0:i1]
        out[f"{prefix}ret_{name}"] = (math.log(seg[-1] / seg[0]) / vol24) if len(seg) > 1 and vol24 == vol24 else np.nan
    out[f"{prefix}ret_24h"] = math.log(c[-1] / c[0]) / vol24 if vol24 == vol24 else np.nan
    j0, _ = window(b, cut - 60, 3600)
    lr1 = np.diff(np.log(b["c"][j0:i1]))
    out[f"{prefix}vol_ratio"] = (lr1.std() / vol24) if len(lr1) > 5 and vol24 == vol24 else np.nan
    hi, lo = b["h"][i0:i1].max(), b["l"][i0:i1].min()
    out[f"{prefix}pos_24h"] = (c[-1] - lo) / (hi - lo) if hi > lo else 0.5
    return out, vol24, (i0, i1, j0)


def features():
    B = {s: bars(s) for s in sorted(T.symbol.unique())}
    F = {s: pd.read_parquet(f"data/candles/{s}/funding_1h.parquet").sort_values("time") for s in B}
    btc = B["BTCUSD"]
    ex = T.exit_baseline.to_numpy(); et = T.entry_time.to_numpy(); r = T.r_baseline.to_numpy(); sym = T.symbol.to_numpy()
    rows = []
    for k, tr in enumerate(T.itertuples()):
        cut = tr.entry_time - 300                       # nothing from the entry bar or the 5 minutes before it
        b = B[tr.symbol]
        pf = price_features(b, cut)
        if pf is None:
            rows.append(None); continue
        f, vol24, (i0, i1, j0) = pf
        side = tr.side
        f = {**{k2: v for k2, v in f.items()},
             **{f"al_{n}": side * f[f"ret_{n}"] for n in ("15m", "1h", "4h", "24h")}}      # aligned with the trade
        f["al_pos_24h"] = f["pos_24h"] if side > 0 else 1 - f["pos_24h"]
        f["side"] = side; f["fee_r"] = tr.cost_per_r; f["vol_24h"] = vol24
        v = b["v"]; f["volume_surge"] = (v[j0:i1].sum() / (v[i0:i1].sum() / 24)) if v[i0:i1].sum() > 0 else np.nan
        a0 = np.searchsorted(b["t_all"], cut - DAY); a1 = np.searchsorted(b["t_all"], cut)
        f["idle_24h"] = 1 - (i1 - i0) / max(a1 - a0, 1)
        fd = F[tr.symbol]; j = np.searchsorted(fd.time.to_numpy(), cut) - 1
        fr = float(fd.close.iloc[j]) if j >= 0 else np.nan
        f["funding"] = fr; f["al_funding"] = side * fr
        hod = (tr.entry_time % DAY) / 3600
        f["hour_sin"], f["hour_cos"] = math.sin(2 * math.pi * hod / 24), math.cos(2 * math.pi * hod / 24)
        f["weekend"] = int(((tr.entry_time // DAY) + 4) % 7 >= 5)
        bf = price_features(btc, cut, "btc_")
        if bf is not None:
            f.update({k2: v2 for k2, v2 in bf[0].items() if k2 in ("btc_ret_1h", "btc_ret_24h", "btc_vol_ratio")})
            f["al_btc_1h"] = side * bf[0]["btc_ret_1h"]; f["al_btc_24h"] = side * bf[0]["btc_ret_24h"]
        # the strategy's own RESOLVED results: closed before the cut, entered up to 3 days back
        done = (ex[:k] < cut) & (et[:k] >= cut - 3 * DAY)
        f["mkt_recent_r"] = float(r[:k][done].mean()) if done.sum() >= 3 else np.nan
        f["mkt_recent_n"] = int(done.sum())
        same = done & (sym[:k] == tr.symbol)
        f["sym_recent_r"] = float(r[:k][same].mean()) if same.any() else np.nan
        rows.append(f)
    X = pd.DataFrame([x or {} for x in rows]); ok = np.array([x is not None for x in rows])
    return X, ok


X, ok = features()
T = T[ok].reset_index(drop=True); X = X[ok].reset_index(drop=True)
y = (T.r_baseline >= 2.0).astype(int).to_numpy()
gross3 = (T.r_baseline + T.cost_per_r).to_numpy(); net3 = T.r_baseline.to_numpy(); nett = T.r_trail.to_numpy()
month = pd.to_datetime(T.entry_time, unit="s").dt.strftime("%Y-%m").to_numpy()
mstart = {m: int(pd.Timestamp(m + "-01", tz="UTC").timestamp()) for m in sorted(set(month))}
MONTHS = [m for m in sorted(set(month)) if m >= "2026-04"]; PREV = {m: sorted(mstart)[sorted(mstart).index(m) - 1] for m in MONTHS}
FEATS = list(X.columns); NOFEE = [c for c in FEATS if c not in ("fee_r", "vol_24h")]
print(f"{len(T)} entries with features ({int((~ok).sum())} dropped for thin history), {len(FEATS)} features, "
      f"base rate of a 3R hit {100 * y.mean():.1f}%; test months {MONTHS[0]}..{MONTHS[-1]}")


def fit_score(cols, train, test, seed, labels=y):
    m = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.03, num_leaves=8, min_child_samples=40, subsample=0.8,
                           subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0, random_state=seed, verbose=-1)
    m.fit(X.loc[train, cols], labels[train])
    return m.predict_proba(X.loc[test, cols])[:, 1], m


def walk(cols, q, seed, shuffle=False):
    """Indices selected over the test months; threshold fixed from the previous month."""
    lab = y.copy()
    if shuffle:
        lab = np.random.default_rng(seed).permutation(lab)
    picked = []
    for m in MONTHS:
        pm = PREV[m]
        trA = (T.exit_baseline < mstart[pm]).to_numpy(); teA = month == pm
        trB = (T.exit_baseline < mstart[m]).to_numpy(); teB = month == m
        if trA.sum() < 300 or teA.sum() < 30:
            continue
        sA, _ = fit_score(cols, trA, teA, seed, lab)
        thr = np.quantile(sA, 1 - q)
        sB, _ = fit_score(cols, trB, teB, seed, lab)
        picked += list(np.flatnonzero(teB)[sB >= thr])
    return np.array(picked, dtype=int)


def cheap(q):
    picked = []
    for m in MONTHS:
        thr = np.quantile(T.cost_per_r[month == PREV[m]], q)
        picked += list(np.flatnonzero((month == m) & (T.cost_per_r.to_numpy() <= thr)))
    return np.array(picked, dtype=int)


test = np.isin(month, MONTHS); RNG = np.random.default_rng(7)
def stats(idx):
    if len(idx) == 0:
        return None
    by = pd.Series(net3[idx]).groupby(month[idx]).mean()
    return dict(n=len(idx), hit=100 * y[idx].mean(), n3=net3[idx].mean(), nt=nett[idx].mean(), g3=gross3[idx].mean(),
                fee=T.cost_per_r.to_numpy()[idx].mean(), months_pos=int((by > 0).sum()), months=len(by))
def p_random(idx, arr):
    if len(idx) == 0:
        return float("nan")
    cnt = pd.Series(month[idx]).value_counts(); obs = arr[idx].mean(); draws = []
    for _ in range(400):
        pick = np.concatenate([RNG.choice(np.flatnonzero(month == m), size=n, replace=False) for m, n in cnt.items()])
        draws.append(arr[pick].mean())
    return float((np.array(draws) >= obs).mean())

a = stats(np.flatnonzero(test))
print(f"\nALL entries of the test months: n {a['n']}, 3R hit {a['hit']:.1f}%, hold-to-3R net {a['n3']:+.3f}R (gross {a['g3']:+.3f}), "
      f"trail net {a['nt']:+.3f}R, fee {a['fee']:.3f}R, months in profit {a['months_pos']}/{a['months']}")
print(f"\n{'selector':22s}{'depth':>6s}{'trades':>8s}{'3R hit%':>8s}{'fee R':>7s}{'gross 3R':>9s}{'net 3R':>8s}{'p rand':>7s}{'net trail':>10s}{'p rand':>7s}{'months +':>9s}{'seed spread (net 3R)':>22s}")
SEEDS = (11, 22, 33, 44, 55)
for q in (0.10, 0.25, 0.50):
    for name, cols, kw in (("model, all features", FEATS, {}), ("model, no fee feature", NOFEE, {}),
                           ("model, shuffled labels", FEATS, {"shuffle": True})):
        runs = [walk(cols, q, s, **kw) for s in SEEDS]
        ss = [stats(i) for i in runs if len(i)]
        if not ss:
            continue
        avg = {k: float(np.mean([s[k] for s in ss])) for k in ss[0]}
        idx = runs[0]
        print(f"{name:22s}{100 * q:5.0f}%{avg['n']:8.0f}{avg['hit']:8.1f}{avg['fee']:7.3f}{avg['g3']:+9.3f}{avg['n3']:+8.3f}{p_random(idx, net3):7.2f}"
              f"{avg['nt']:+10.3f}{p_random(idx, nett):7.2f}{avg['months_pos']:6.1f}/{ss[0]['months']}"
              f"   {min(s['n3'] for s in ss):+.3f} .. {max(s['n3'] for s in ss):+.3f}")
    idx = cheap(q); s = stats(idx)
    print(f"{'cheap fees only':22s}{100 * q:5.0f}%{s['n']:8.0f}{s['hit']:8.1f}{s['fee']:7.3f}{s['g3']:+9.3f}{s['n3']:+8.3f}{p_random(idx, net3):7.2f}"
          f"{s['nt']:+10.3f}{p_random(idx, nett):7.2f}{s['months_pos']:6.0f}/{s['months']}")
    print()
# does the model add anything INSIDE a fee bucket? score vs outcome among entries with similar fees
full = np.zeros(len(T)); seen = np.zeros(len(T), bool)
for m in MONTHS:
    tr = (T.exit_baseline < mstart[m]).to_numpy(); te = month == m
    s = np.mean([fit_score(NOFEE, tr, te, sd)[0] for sd in SEEDS], axis=0); full[te] = s; seen[te] = True
d = pd.DataFrame(dict(score=full[seen], y=y[seen], g=gross3[seen], n3=net3[seen], nt=nett[seen], month=month[seen]))
d["rank"] = d.groupby("month").score.rank(pct=True)
d["tercile"] = pd.cut(d["rank"], [0, 1 / 3, 2 / 3, 1], labels=["bottom third", "middle third", "top third"])
print("OUT-OF-SAMPLE SCORE (no fee feature) against what happened, test months, ranked within each month (analysis only: a live rule cannot rank a month):")
for t_, g in d.groupby("tercile", observed=True):
    print(f"  {t_:13s} n {len(g):5d}  3R hit {100 * g.y.mean():5.1f}%  gross 3R {g.g.mean():+.3f}R  net 3R {g.n3.mean():+.3f}R  net trail {g.nt.mean():+.3f}R")
from sklearn.metrics import roc_auc_score
aucs = [roc_auc_score(g.y, g.score) for _, g in d.groupby("month") if g.y.nunique() > 1]
print(f"  AUC by month: {', '.join(f'{x:.3f}' for x in aucs)}  (0.500 = no information); mean {np.mean(aucs):.3f}")
_, mdl = fit_score(FEATS, (T.exit_baseline < mstart[MONTHS[-1]]).to_numpy(), month == MONTHS[-1], 11)
imp = pd.Series(mdl.booster_.feature_importance("gain"), index=FEATS).sort_values(ascending=False)
print("  top features by gain (last fold, all features):", ", ".join(f"{k} {100 * v / imp.sum():.0f}%" for k, v in imp.head(8).items()))
