"""Can a model learn which of the bot's signals to take? (owner, 2026-10-13)

    PYTHONPATH=. .venv/bin/python scripts/signal_features_lab.py fetch   # BTC 1m to date, OI 1h per coin
    PYTHONPATH=. .venv/bin/python scripts/signal_features_lab.py build   # signals + features + outcomes
    PYTHONPATH=. .venv/bin/python scripts/signal_features_lab.py dev     # Jan-Jul only: walk-forward + per-feature
    PYTHONPATH=. .venv/bin/python scripts/signal_features_lab.py holdout # ONCE, after dev, Aug-Oct

WHY. The owner asked for a model that learns from trades and filters better
ones, recording parameters beyond the setup itself. Step 2 of the plan: build
the same features on the 9 months of history first, so we know early whether
anything separates winners from losers. Earlier filters here (candle-shape ML
AUC 0.49, OI + regime bundle 0.516, Binance order flow 0.50) found nothing;
this one uses the bot's own rules and coins.

SIGNALS. The bot as built: manual_scalp_both_t3_st at 5m/1m on the daily top-10
gainers (scripts/top_gainers_backtest.py picks), 2026-01-01..10-12, flat
no-trade minutes kept. Every TAKEABLE signal is a row: each coin is run on its
own (one position per coin, no daily cap, no pause), so signals the real
account skipped because of the 2/day cap are included. Label = the trade's net
R (3R target / 4xATR stop / 72h, ltp_close stop fill, fees and funding);
win = R > 0.

FEATURES (known at the signal bar's close; directional ones are multiplied by
the trade side so "+" always means "with the trade"):
  setup   wpr (mirrored for shorts), wpr slope over 3 bars, distance from
          Supertrend in ATR, ATR %, stop %, ADX 5m, volume vs the 20 bars
          before, bar range in ATR
  coin    return 1h/4h/24h, distance from 24h high and low, 1h and 4h
          Supertrend agree with the side, ADX 1h, the day's gain %, rank and
          log turnover from the picks, days since listing, tag (meme/ai/defi/
          layer/new/other), funding rate (side-adjusted: + = you pay), open
          interest change 1h/6h/24h
  market  BTC return 1h/4h/24h, BTC 1h Supertrend agrees with the side
  time    hour of day (IST), weekday
  shape   (added 2026-10-13 before any result) biggest move with the trade
          and deepest pullback over the last 4h in ATR, share of the last
          hour's bars closing with the trade

PRICE PATHS (owner, 2026-10-13: "find price action when trade lost and won"):
paths.parquet, NOT model inputs -- 24h of 5m closes before entry and the 5m
high/low path until exit, in R; best/worst excursion and time to the best.
The `paths` step describes how winners and losers moved; it judges nothing.
The `patterns` step (owner, 2026-10-13: "identify price action around both
kinds of trades") tags seven NAMED pre-entry patterns (rules in NAMED, fixed
before any result) and groups the 24h-before charts into 8 k-means families,
with pictures; dev months only. A pattern or family counts only if it also
holds on the sealed Aug-Oct months.

FIXED BEFORE ANY RESULT IS READ (2026-10-13):
  * SEALED HOLD-OUT: signals entered on or after 2026-08-01. `dev` never reads
    them; `holdout` is run once, after `dev`, with nothing changed in between.
  * MODEL, not tuned: sklearn HistGradientBoostingClassifier(max_depth=3,
    learning_rate=0.05, max_iter=200, min_samples_leaf=50,
    l2_regularization=1.0, random_state=0), target win.
  * DEV (Jan-Jul): walk-forward, train on months before m, test on month m,
    for m = Apr, May, Jun, Jul; plus each feature's AUC and Spearman with R.
    Descriptive only; nothing is selected from it.
  * HOLD-OUT RULE: fit on all of Jan-Jul, score Aug-Oct. The filter PASSES if
      (a) AUC >= 0.55, and
      (b) the top 30% of signals by score have a higher mean net R than at
          least 95% of 2,000 random 30% subsets of the same signals, and
      (c) that top-30% mean R is above zero.
    PASS = worth building into the bot as a filter (still the owner's call,
    and live only after it also holds on recorded live signals).
    FAIL = record the features live anyway (the plan's step 1) and re-test
    when there is more data; do not filter trades with it.
"""
from __future__ import annotations

import dataclasses
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
from deltabt import rulecore  # noqa: E402
from deltabt import signal_features as SF  # noqa: E402
from deltabt import signal_model as SM  # noqa: E402
from deltabt.catalog import build_spec  # noqa: E402
from deltabt.costs import SymbolCosts  # noqa: E402
from deltabt.data.client import DeltaClient  # noqa: E402
from deltabt.data.quality import tradable_mask  # noqa: E402
from deltabt.data.store import CandleStore, ProductCatalog  # noqa: E402
from deltabt.harness import _resampled, load_symbol, params_for  # noqa: E402
from deltabt.portfolio import Book, RiskGates, run_portfolio  # noqa: E402
import top_gainers_backtest as TG  # noqa: E402

OUT = Path("out/signal_lab")
HOLDOUT_START = pd.Timestamp("2026-08-01", tz="UTC").value // 10**9
DEV_TEST_MONTHS = ["2026-04", "2026-05", "2026-06", "2026-07"]
SPEC = build_spec(TG.FAMILY, 5, 1)
IST = 19800
TAGS = SF.TAGS


# --- fetch ---------------------------------------------------------------

def cmd_fetch() -> None:
    now = int(time.time()) // 60 * 60 - 120
    CandleStore().load_all_series("BTCUSD", "1m", TG.WARMUP_START, now)
    picks = pd.read_csv(TG.OUT / "picks.csv")
    span = picks.groupby("symbol").pick_day.agg(["min", "max"])
    cl = DeltaClient(per_second=2.0)
    for k, sym in enumerate(sorted(span.index), 1):
        p = Path(f"data/candles/{sym}/oi_1h.parquet")
        if p.exists():
            continue
        lo = int(span.loc[sym, "min"]) - 3 * TG.DAY
        hi = min(int(span.loc[sym, "max"]) + TG.DAY, now)
        try:
            rows = cl.candles("OI:" + sym, "1h", lo, hi)
            d = pd.DataFrame([{"time": int(r["time"]), "oi": float(r["close"])}
                              for r in rows if r.get("close") is not None])
            d.to_parquet(p, index=False)
            print(f"[{k}/{len(span)}] {sym} oi rows {len(d)}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[{k}/{len(span)}] {sym} OI ERROR {exc}", flush=True)


# --- build ---------------------------------------------------------------

# The definitions live in deltabt/signal_features.py, shared with the live
# recorder (app/learning/recorder.py) so live rows pool with these.
_tag = SF.tag_of
_asof = SF.asof


def _btc_frame():
    d = load_symbol("BTCUSD")
    l = d["ltp"]
    c60 = TG._resampled(dict(symbol="BTCUSD", ltp=l, mark=d["mark"], funding=d["funding"],
                             tradable=tradable_mask(l)), 60, {})[0]
    return SF.prepare_btc(l, c60, SPEC)


def _coin(args):
    sym, allowed, pick_info, meta, btc = args
    d = load_symbol(sym)
    if d is None:
        return None
    l = d["ltp"]
    d = dict(symbol=sym, ltp=l, mark=d["mark"], funding=d["funding"], tradable=tradable_mask(l))
    cache = {}
    P, mk, tr = _resampled(d, 5, cache)
    C = _resampled(d, 1, cache)[0]
    if len(P) <= SPEC.warmup_bars:
        return None
    sig = rulecore.to_engine_signals(rulecore.compute(P, C, SPEC))
    close_day = ((P["time"].to_numpy("int64") + 5 * 60 - 1) // TG.DAY) * TG.DAY
    allow = np.isin(close_day, np.fromiter(allowed, dtype="int64"))
    sig = replace(sig, long_entry=sig.long_entry & allow, short_entry=sig.short_entry & allow)
    book = Book(symbol=sym, bars=P, signals=sig, costs=SymbolCosts.from_spec(ProductCatalog().get(sym)),
                mark=mk, tradable=tr, fill_ltp=d["ltp"], fill_mark=d["mark"])
    params = replace(params_for(SPEC, 5, 72), stop_fill="ltp_close", risk_percent=TG.RISK_PCT,
                     max_leverage=TG.MAX_LEV, max_cost_per_r=None)
    gates = replace(RiskGates.off(), max_open_positions=1)
    res = run_portfolio({sym: book}, params, gates, initial_capital=10_000.0,
                        funding={sym: d["funding"]})
    if not res.trades:
        return None
    t = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])

    # readings at the signal bar (deltabt/signal_features.py)
    ctx = SF.prepare_coin(P, _resampled(d, 60, cache)[0], _resampled(d, 240, cache)[0], SPEC)
    i = np.searchsorted(ctx.time, t.entry_time.to_numpy("int64"))
    side = t.side.to_numpy(float)
    oi_p = Path(f"data/candles/{sym}/oi_1h.parquet")
    feats = SF.features_at(
        ctx, i, side, t.entry_price.to_numpy(float), t.stop_price.to_numpy(float),
        btc=btc, funding=d["funding"], oi=pd.read_parquet(oi_p) if oi_p.exists() else None,
        picks=pick_info, launch=meta.get("launch"), tag=meta.get("tag"))
    f = pd.concat([pd.DataFrame({
        "symbol": sym, "entry_time": t.entry_time, "side": side,
        "R": t.r_multiple, "win": (t.r_multiple > 0).astype(int), "exit_reason": t.exit_reason,
    }), feats], axis=1)

    # PRICE PATHS -- NOT model inputs. Before entry: 24h of 5m closes; after
    # entry: 5m high/low until the exit. All in R from the entry price, "+" =
    # in the trade's favour. Kept in a separate frame so the after-entry path
    # can never leak into the features.
    rpu = np.abs(t.entry_price - t.stop_price).to_numpy(float)
    ep = t.entry_price.to_numpy(float)
    sp = t.stop_price.to_numpy(float)
    exit_t = t.exit_time.to_numpy("int64")
    paths = []
    for k, ii in enumerate(i):
        paths.append(dict(symbol=sym, entry_time=int(t.entry_time.iloc[k]),
                          pre_24h=SF.pre_entry_path(ctx, ii, side[k], ep[k], sp[k]).tolist(),
                          **SF.post_entry_path(ctx.time, ctx.high, ctx.low, ii, exit_t[k],
                                               side[k], ep[k], rpu[k])))
    return f, pd.DataFrame(paths)


def cmd_build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    picks = pd.read_csv(TG.OUT / "picks.csv")
    picks["rank"] = picks.groupby("pick_day").cumcount() + 1
    rows = requests.get("https://api.india.delta.exchange/v2/tickers",
                        params={"contract_types": "perpetual_futures"}, timeout=30).json()["result"]
    tags = {r["symbol"]: _tag(r.get("tags")) for r in rows}
    cat = ProductCatalog()
    btc = _btc_frame()
    jobs = []
    for sym, g in picks.groupby("symbol"):
        lt = cat.get(sym).get("launch_time")
        launch = int(pd.Timestamp(lt).value // 10**9) if lt else None
        jobs.append((sym, set(g.pick_day.astype("int64")), g, {"launch": launch, "tag": tags.get(sym, "other")}, btc))
    frames, pframes = [], []
    with ProcessPoolExecutor(8) as ex:
        for k, got in enumerate(ex.map(_coin, jobs, chunksize=4), 1):
            if got is not None:
                frames.append(got[0]); pframes.append(got[1])
    df = pd.concat(frames, ignore_index=True).sort_values("entry_time", ignore_index=True)
    df.to_parquet(OUT / "signals.parquet", index=False)
    pd.concat(pframes, ignore_index=True).to_parquet(OUT / "paths.parquet", index=False)
    dev = df[df.entry_time < HOLDOUT_START]
    print(f"{len(df)} signals from {df.symbol.nunique()} coins; dev {len(dev)} "
          f"(win {dev.win.mean():.1%}, mean R {dev.R.mean():+.3f}); hold-out {len(df) - len(dev)} (not read)")
    na = dev.drop(columns=["symbol", "exit_reason"]).isna().mean()
    print("missing share (dev):", ", ".join(f"{k} {v:.0%}" for k, v in na[na > 0].items()) or "none")


# --- evaluate -----------------------------------------------------------

# Model and grade live in deltabt/signal_model.py, shared with the weekly
# grader (`python -m live grade-model`).
_features = SM.features
_model = SM.model
_top_vs_random = SM.top_vs_random


def cmd_dev() -> None:
    from scipy.stats import spearmanr
    from sklearn.metrics import roc_auc_score
    df = pd.read_parquet(OUT / "signals.parquet")
    dev = df[df.entry_time < HOLDOUT_START].reset_index(drop=True)
    month = pd.to_datetime(dev.entry_time, unit="s").dt.strftime("%Y-%m")
    lines = [f"DEV (Jan-Jul): {len(dev)} signals, win {dev.win.mean():.1%}, mean R {dev.R.mean():+.3f}",
             "", "walk-forward (train on earlier months, test on the month):"]
    for m in DEV_TEST_MONTHS:
        tr, te = dev[month < m], dev[month == m]
        mdl = _model().fit(_features(tr), tr.win)
        s = mdl.predict_proba(_features(te))[:, 1]
        auc = roc_auc_score(te.win, s) if te.win.nunique() > 1 else float("nan")
        top, p, k = _top_vs_random(te.R.to_numpy(), s)
        lines.append(f"  {m}: train {len(tr)}, test {len(te)}  AUC {auc:.3f}  top30% R {top:+.3f} "
                     f"vs all {te.R.mean():+.3f} (random beats it {p:.0%})")
    lines += ["", "each feature alone (dev): AUC for win, Spearman with R"]
    rows = []
    for col in _features(dev).columns:
        x = dev[col]
        ok = x.notna()
        if ok.sum() < 100 or x[ok].nunique() < 2:
            continue
        auc = roc_auc_score(dev.win[ok], x[ok])
        rho = spearmanr(x[ok], dev.R[ok]).statistic
        rows.append((abs(auc - 0.5), col, auc, rho, int(ok.sum())))
    for _, col, auc, rho, n in sorted(rows, reverse=True):
        lines.append(f"  {col:18s} AUC {auc:.3f}  rho {rho:+.3f}  n {n}")
    text = "\n".join(lines)
    print(text)
    (OUT / "dev.txt").write_text(text + "\n")


def cmd_holdout() -> None:
    from sklearn.metrics import roc_auc_score
    df = pd.read_parquet(OUT / "signals.parquet")
    dev, ho = df[df.entry_time < HOLDOUT_START], df[df.entry_time >= HOLDOUT_START]
    mdl = _model().fit(_features(dev), dev.win)
    s = mdl.predict_proba(_features(ho))[:, 1]
    auc = roc_auc_score(ho.win, s)
    top, p, k = _top_vs_random(ho.R.to_numpy(), s)
    a, b, c = auc >= 0.55, p <= 0.05, top > 0
    text = (f"HOLD-OUT (Aug-Oct, read once): {len(ho)} signals, win {ho.win.mean():.1%}, mean R {ho.R.mean():+.3f}\n"
            f"  AUC {auc:.3f} (needs >= 0.55: {'yes' if a else 'no'})\n"
            f"  top 30% ({k}) mean R {top:+.3f}; random 30% subsets beat it {p:.1%} of the time "
            f"(needs <= 5%: {'yes' if b else 'no'}); above zero: {'yes' if c else 'no'}\n"
            f"  RESULT: {'PASS' if a and b and c else 'FAIL'}")
    print(text)
    (OUT / "holdout.txt").write_text(text + "\n")


def cmd_paths() -> None:
    """How winners and losers moved, and taken vs skipped (dev months only)."""
    df = pd.read_parquet(OUT / "signals.parquet")
    pa = pd.read_parquet(OUT / "paths.parquet")
    d = df.merge(pa, on=["symbol", "entry_time"])
    d = d[d.entry_time < HOLDOUT_START]
    win, lose = d[d.R > 0], d[d.R <= 0]
    q = lambda x: f"{100 * x:.0f}%"
    L = [f"PRICE PATHS (Jan-Jul): {len(win)} winners, {len(lose)} losers", "",
         "losers -- how far they went in our favour before the stop:",
         f"  reached +0.5R {q((lose.mfe_R >= 0.5).mean())}, +1R {q((lose.mfe_R >= 1).mean())}, "
         f"+2R {q((lose.mfe_R >= 2).mean())}; median best {lose.mfe_R.median():+.2f}R",
         f"  died within 1h {q((lose.bars_held <= 12).mean())}, within 6h {q((lose.bars_held <= 72).mean())}; "
         f"median life {lose.bars_held.median() * 5 / 60:.1f}h",
         "winners -- how deep they dipped before paying:",
         f"  dipped below -0.5R {q((win.mae_R <= -0.5).mean())}, below -0.8R {q((win.mae_R <= -0.8).mean())}; "
         f"median worst {win.mae_R.median():+.2f}R; median time to exit {win.bars_held.median() * 5 / 60:.1f}h",
         "", "before entry -- price vs entry, in R (+ = in the trade's direction), median:"]
    for lbl, back in (("24h before", 287), ("4h before", 47), ("1h before", 11)):
        w = win.pre_24h.map(lambda a: a[-1 - back] if len(a) > back else np.nan).median()
        lo = lose.pre_24h.map(lambda a: a[-1 - back] if len(a) > back else np.nan).median()
        L.append(f"  {lbl:10s} winners {w:+.2f}R   losers {lo:+.2f}R")
    for col in ("run_4h_atr", "pullback_4h_atr", "with_trade_share_1h", "ret_24h", "from_24h_high"):
        L.append(f"  {col:20s} winners {win[col].median():+.2f}   losers {lose[col].median():+.2f}")
    acct = TG.OUT / "trades_5_1_5x.csv"
    if acct.exists():
        taken = pd.read_csv(acct)
        cut = int(taken.entry_time.max())   # the account halted after this (drawdown latch)
        dd = d[d.entry_time <= cut]
        key = set(zip(taken.symbol, taken.entry_time.astype("int64")))
        is_taken = np.array([(s_, int(t_)) in key for s_, t_ in zip(dd.symbol, dd.entry_time)])
        L += ["", f"taken by the $250 account vs skipped (2/day, one at a time, pause), to its halt:",
              f"  taken   {is_taken.sum():5d}  mean R {dd.R[is_taken].mean():+.3f}  win {q(dd.win[is_taken].mean())}",
              f"  skipped {(~is_taken).sum():5d}  mean R {dd.R[~is_taken].mean():+.3f}  win {q(dd.win[~is_taken].mean())}"]
    text = "\n".join(L)
    print(text)
    (OUT / "paths.txt").write_text(text + "\n")


NAMED = {
    # pre-entry, known at the decision; thresholds fixed here, not tuned
    "after_spike": lambda d: d.run_4h_atr >= 6,                       # >= 6 ATR with the trade in 4h
    "pullback_in_trend": lambda d: (d.st_1h_agrees == 1) & (d.pullback_4h_atr <= -2),
    "quiet_breakout": lambda d: (d.vol_ratio >= 3) & (d.run_4h_atr < 3),
    "near_24h_high": lambda d: d.from_24h_high >= -1,                  # within 1% of the 24h high
    "against_1h_trend": lambda d: d.st_1h_agrees == 0,
    "against_btc": lambda d: d.btc_st_1h_agrees == 0,
    "late_in_move": lambda d: d.ret_24h >= 20,                        # already +20% with the trade in 24h
}


def _shape(a, n=48):
    a = np.asarray(a, dtype="float64")
    if len(a) < 288:
        return None
    return a[::6][-n:]   # every 30 min over the last 24h, in R from the entry


def cmd_patterns() -> None:
    """Name and group the price action around winners and losers (dev only)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from sklearn.cluster import KMeans
    df = pd.read_parquet(OUT / "signals.parquet")
    pa = pd.read_parquet(OUT / "paths.parquet")
    d = df.merge(pa, on=["symbol", "entry_time"])
    d = d[d.entry_time < HOLDOUT_START].reset_index(drop=True)
    base = d.R.mean()
    L = [f"PATTERNS (Jan-Jul, {len(d)} signals, all: win {100 * d.win.mean():.0f}%, mean R {base:+.3f})",
         "", "named patterns before entry (rules fixed in the script):"]
    for name, rule in NAMED.items():
        m = rule(d).fillna(False).to_numpy(bool)
        if m.sum() < 20:
            L.append(f"  {name:18s} n {m.sum():4d}  (too few)"); continue
        r = d.R[m]
        L.append(f"  {name:18s} n {m.sum():4d}  win {100 * d.win[m].mean():3.0f}%  mean R {r.mean():+.3f} "
                 f"(+/- {r.std(ddof=1) / np.sqrt(len(r)):.3f})")
    # families of pre-entry shape
    shapes = d.pre_24h.map(_shape)
    ok = shapes.notna().to_numpy()
    X = np.vstack(shapes[ok].to_numpy())
    X = np.clip(X, -30, 30)
    km = KMeans(n_clusters=8, n_init=10, random_state=0).fit(X)
    dd = d[ok].assign(family=km.labels_)
    L += ["", "families of the 24h chart before entry (found by k-means, k=8 fixed):"]
    fig, axes = plt.subplots(2, 4, figsize=(16, 7), sharey=True)
    xs = np.arange(-24, 0, 0.5)
    for k in range(8):
        g = dd[dd.family == k]
        r = g.R
        L.append(f"  family {k}: n {len(g):4d}  win {100 * g.win.mean():3.0f}%  mean R {r.mean():+.3f} "
                 f"(+/- {r.std(ddof=1) / np.sqrt(max(len(r), 2)):.3f})")
        ax = axes.flat[k]
        Xk = X[km.labels_ == k]
        for j, row in enumerate(Xk[:40]):
            ax.plot(xs, row, color=("tab:green" if g.win.iloc[j] else "tab:red"), alpha=0.25, lw=0.7)
        ax.plot(xs, km.cluster_centers_[k], color="black", lw=2)
        ax.axhline(0, color="grey", lw=0.5); ax.axhline(-1, color="grey", lw=0.5, ls=":")
        ax.set_title(f"family {k}: n {len(g)}, win {100 * g.win.mean():.0f}%, {r.mean():+.2f}R", fontsize=9)
        ax.set_xlabel("hours before entry"); ax.set_ylim(-15, 15)
    axes.flat[0].set_ylabel("price vs entry, in R (+ = trade direction)")
    fig.suptitle("Before entry: green = signal ended a winner, red = loser; black = family average")
    fig.tight_layout(); fig.savefig(OUT / "families_before_entry.png", dpi=110); plt.close(fig)
    # how losers died / how winners paid
    lose, win = d[d.R <= 0], d[d.R > 0]
    kinds = {
        "instant reversal (never +0.3R)": lose.mfe_R < 0.3,
        "up first, then reversed (>= +1R)": lose.mfe_R >= 1,
        "small bounce, then down": (lose.mfe_R >= 0.3) & (lose.mfe_R < 1),
    }
    L += ["", f"how the {len(lose)} losers died:"]
    L += [f"  {k:34s} {100 * v.mean():3.0f}%  median life {lose.bars_held[v].median() * 5 / 60:.1f}h"
          for k, v in kinds.items()]
    wk = {"clean (never below -0.3R)": win.mae_R > -0.3,
          "dipped to -0.3..-0.8R first": (win.mae_R <= -0.3) & (win.mae_R > -0.8),
          "nearly stopped (below -0.8R)": win.mae_R <= -0.8}
    L += ["", f"how the {len(win)} winners paid:"]
    L += [f"  {k:34s} {100 * v.mean():3.0f}%  median time {win.bars_held[v].median() * 5 / 60:.1f}h"
          for k, v in wk.items()]
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=True)
    for ax, g, title, col in ((axes[0], win, "winners", "tab:green"), (axes[1], lose, "losers", "tab:red")):
        for a, b in zip(g.fav.head(60), g.adv.head(60)):
            m = (np.asarray(a) + np.asarray(b)) / 2
            ax.plot(np.arange(len(m)) * 5 / 60, m, color=col, alpha=0.25, lw=0.7)
        ax.axhline(0, color="grey", lw=0.5); ax.axhline(3, color="grey", ls=":"); ax.axhline(-1, color="grey", ls=":")
        ax.set_title(f"after entry: {title} (60 examples)"); ax.set_xlabel("hours after entry"); ax.set_xlim(0, 72)
    axes[0].set_ylabel("price vs entry, in R"); axes[0].set_ylim(-2, 3.5)
    fig.tight_layout(); fig.savefig(OUT / "paths_after_entry.png", dpi=110); plt.close(fig)
    text = "\n".join(L)
    print(text)
    (OUT / "patterns.txt").write_text(text + "\n")


#: FIXED 2026-10-13 AFTER reading `dev`, `paths` and `patterns` and BEFORE the
#: hold-out is read. The Jan-Jul findings, each with the direction it showed.
#: A finding HOLDS on Aug-Oct if: for a reading, Spearman rho with R has the
#: same sign at one-sided p <= 0.05; for a pattern or family, its mean R sits
#: below the hold-out average by at least 1.645 standard errors. Nine checks
#: at 5% each: about one could pass by luck alone.
FINDINGS = [
    ("stop_pct", "rho", +1), ("day_rank", "rho", -1), ("day_gain_pct", "rho", +1),
    ("funding_side", "rho", -1), ("from_24h_low", "rho", +1),
    ("near_24h_high", "worse", None), ("quiet_breakout", "worse", None),
    ("family 4", "worse", None), ("family 5", "worse", None),
]


def cmd_holdout_findings() -> None:
    from scipy.stats import spearmanr
    from sklearn.cluster import KMeans
    df = pd.read_parquet(OUT / "signals.parquet")
    pa = pd.read_parquet(OUT / "paths.parquet")
    d = df.merge(pa, on=["symbol", "entry_time"])
    dev, ho = d[d.entry_time < HOLDOUT_START], d[d.entry_time >= HOLDOUT_START].reset_index(drop=True)
    shp = dev.pre_24h.map(_shape)
    km = KMeans(n_clusters=8, n_init=10, random_state=0).fit(np.clip(np.vstack(shp[shp.notna()].to_numpy()), -30, 30))
    hs = ho.pre_24h.map(_shape)
    fam = np.full(len(ho), -1)
    okh = hs.notna().to_numpy()
    fam[okh] = km.predict(np.clip(np.vstack(hs[okh].to_numpy()), -30, 30))
    base = ho.R.mean()
    L = [f"HOLD-OUT FINDINGS (Aug-Oct, read once): {len(ho)} signals, mean R {base:+.3f}"]
    held = 0
    for name, kind, sign in FINDINGS:
        if kind == "rho":
            x = ho[name]; ok = x.notna()
            r = spearmanr(x[ok], ho.R[ok])
            p1 = r.pvalue / 2 if np.sign(r.statistic) == sign else 1 - r.pvalue / 2
            good = p1 <= 0.05
            L.append(f"  {name:16s} rho {r.statistic:+.3f} (dev direction {'+' if sign > 0 else '-'}), "
                     f"one-sided p {p1:.3f}: {'HOLDS' if good else 'does not hold'}")
        else:
            m = (fam == int(name.split()[1])) if name.startswith("family") else NAMED[name](ho).fillna(False).to_numpy(bool)
            r = ho.R[m]
            se = r.std(ddof=1) / np.sqrt(len(r)) if len(r) > 1 else np.inf
            z = (base - r.mean()) / se if len(r) > 1 else 0.0
            good = z >= 1.645
            L.append(f"  {name:16s} n {len(r):4d}  mean R {r.mean():+.3f} vs all {base:+.3f}  "
                     f"z {z:+.2f}: {'HOLDS' if good else 'does not hold'}")
        held += good
    L.append(f"  {held} of {len(FINDINGS)} hold (about 0.5 expected by luck)")
    text = "\n".join(L)
    print(text)
    (OUT / "holdout_findings.txt").write_text(text + "\n")


if __name__ == "__main__":
    {"fetch": cmd_fetch, "build": cmd_build, "dev": cmd_dev, "holdout": cmd_holdout,
     "paths": cmd_paths, "patterns": cmd_patterns,
     "holdout_findings": cmd_holdout_findings}[sys.argv[1]]()
