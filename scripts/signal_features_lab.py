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
TAGS = ("meme", "ai", "defi", "layer", "new")


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

def _tag(tags) -> str:
    tags = [str(t).lower() for t in (tags or [])]
    for name in TAGS:
        if any(name in t for t in tags):
            return name
    return "other"


def _htf(d, minutes, cache):
    f = _resampled(d, minutes, cache)[0]
    ind = rulecore.indicators(f, SPEC)
    close_time = f["time"].to_numpy("int64") + minutes * 60
    return close_time, ind


def _asof(close_time, values, at):
    """Value of the last bar CLOSED at or before `at`."""
    j = np.searchsorted(close_time, at, side="right") - 1
    out = np.full(len(at), np.nan)
    ok = j >= 0
    out[ok] = np.asarray(values, dtype="float64")[j[ok]]
    return out


def _btc_frame():
    d = load_symbol("BTCUSD")
    l = d["ltp"]
    c5 = TG._resampled(dict(symbol="BTCUSD", ltp=l, mark=d["mark"], funding=d["funding"],
                            tradable=tradable_mask(l)), 60, {})[0]
    ind = rulecore.indicators(c5, SPEC)
    return dict(m_time=l["time"].to_numpy("int64") + 60, m_close=l["close"].to_numpy("float64"),
                h_close_time=c5["time"].to_numpy("int64") + 3600, h_dir=ind.direction)


def _ret(times, closes, at, back_s):
    now = _asof(times, closes, at)
    then = _asof(times, closes, at - back_s)
    return now / then - 1


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

    # indicators on the primary bars, read at the signal bar
    pi = rulecore.indicators(P, SPEC)
    ptime = P["time"].to_numpy("int64")
    i = np.searchsorted(ptime, t.entry_time.to_numpy("int64"))
    side = t.side.to_numpy(float)
    at = ptime[i] + 5 * 60   # the signal bar's close
    h, lo_, c = pi.high, pi.low, pi.close
    vol = P["volume"].to_numpy("float64")
    vol_prev = pd.Series(vol).shift(1).rolling(20, min_periods=10).mean().to_numpy()
    hi24 = pd.Series(h).rolling(288, min_periods=48).max().to_numpy()
    lo24 = pd.Series(lo_).rolling(288, min_periods=48).min().to_numpy()
    wpr = pi.wpr
    f = pd.DataFrame({
        "symbol": sym, "entry_time": t.entry_time, "side": side,
        "R": t.r_multiple, "win": (t.r_multiple > 0).astype(int), "exit_reason": t.exit_reason,
        "wpr": np.where(side > 0, wpr[i], -100 - wpr[i]),
        "wpr_slope3": (wpr[i] - wpr[np.maximum(i - 3, 0)]) * side,
        "st_dist_atr": (c[i] - pi.st[i]) / pi.atr[i] * side,
        "atr_pct": pi.atr[i] / c[i] * 100,
        "stop_pct": np.abs(t.entry_price - t.stop_price) / t.entry_price * 100,
        "adx_5m": pi.adx[i],
        "vol_ratio": vol[i] / vol_prev[i],
        "range_atr": (h[i] - lo_[i]) / pi.atr[i],
        "ret_1h": (c[i] / c[np.maximum(i - 12, 0)] - 1) * 100 * side,
        "ret_4h": (c[i] / c[np.maximum(i - 48, 0)] - 1) * 100 * side,
        "ret_24h": (c[i] / c[np.maximum(i - 288, 0)] - 1) * 100 * side,
        "from_24h_high": (c[i] / hi24[i] - 1) * 100,
        "from_24h_low": (c[i] / lo24[i] - 1) * 100,
    })
    for mins, name in ((60, "1h"), (240, "4h")):
        ct, ind = _htf(d, mins, cache)
        dirn = _asof(ct, ind.direction, at)
        f[f"st_{name}_agrees"] = np.where(np.isfinite(dirn), ((dirn < 0) == (side > 0)).astype(float), np.nan)
        if name == "1h":
            f["adx_1h"] = _asof(ct, ind.adx, at)
    day = (at // TG.DAY) * TG.DAY
    pk = pick_info.set_index("pick_day")
    f["day_gain_pct"] = pk.change.reindex(day).to_numpy()
    f["day_rank"] = pk["rank"].reindex(day).to_numpy()
    f["log_turnover"] = np.log10(pk.turnover.reindex(day).to_numpy())
    launch = meta.get("launch")
    f["days_listed"] = (at - launch) / TG.DAY if launch else np.nan
    for name in TAGS + ("other",):
        f[f"tag_{name}"] = float(meta.get("tag") == name)
    fu = d["funding"]
    if not fu.empty:
        f["funding_side"] = _asof(fu.time.to_numpy("int64") + 3600, fu.close.to_numpy(float), at) * side
    oi_p = Path(f"data/candles/{sym}/oi_1h.parquet")
    if oi_p.exists():
        oi = pd.read_parquet(oi_p).sort_values("time")
        ot, ov = oi.time.to_numpy("int64") + 3600, oi.oi.to_numpy(float)
        for hrs in (1, 6, 24):
            f[f"oi_chg_{hrs}h"] = (_asof(ot, ov, at) / _asof(ot, ov, at - hrs * 3600) - 1) * 100
    for hrs in (1, 4, 24):
        f[f"btc_ret_{hrs}h"] = _ret(btc["m_time"], btc["m_close"], at, hrs * 3600) * 100 * side
    bdir = _asof(btc["h_close_time"], btc["h_dir"], at)
    f["btc_st_1h_agrees"] = np.where(np.isfinite(bdir), ((bdir < 0) == (side > 0)).astype(float), np.nan)
    f["hour_ist"] = ((at + IST) % TG.DAY) // 3600
    f["weekday"] = ((at + IST) // TG.DAY + 3) % 7   # 0 = Monday
    return f


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
    frames = []
    with ProcessPoolExecutor(8) as ex:
        for k, f in enumerate(ex.map(_coin, jobs, chunksize=4), 1):
            if f is not None:
                frames.append(f)
    df = pd.concat(frames, ignore_index=True).sort_values("entry_time", ignore_index=True)
    df.to_parquet(OUT / "signals.parquet", index=False)
    dev = df[df.entry_time < HOLDOUT_START]
    print(f"{len(df)} signals from {df.symbol.nunique()} coins; dev {len(dev)} "
          f"(win {dev.win.mean():.1%}, mean R {dev.R.mean():+.3f}); hold-out {len(df) - len(dev)} (not read)")
    na = dev.drop(columns=["symbol", "exit_reason"]).isna().mean()
    print("missing share (dev):", ", ".join(f"{k} {v:.0%}" for k, v in na[na > 0].items()) or "none")


# --- evaluate -----------------------------------------------------------

def _features(df):
    return df.drop(columns=["symbol", "entry_time", "R", "win", "exit_reason"])


def _model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                          min_samples_leaf=50, l2_regularization=1.0,
                                          random_state=0)


def _top_vs_random(R, score, frac=0.3, n=2000, seed=0):
    k = max(1, int(round(frac * len(R))))
    top = R[np.argsort(-score)[:k]].mean()
    rng = np.random.default_rng(seed)
    rand = np.array([R[rng.choice(len(R), k, replace=False)].mean() for _ in range(n)])
    return top, float((rand >= top).mean()), k


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


if __name__ == "__main__":
    {"fetch": cmd_fetch, "build": cmd_build, "dev": cmd_dev, "holdout": cmd_holdout}[sys.argv[1]]()
