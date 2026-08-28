"""Driver for the H1 momentum kill test. Frozen parameters only."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import h1_momentum as hm  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "out" / "h1mom"
OUT.mkdir(parents=True, exist_ok=True)
N_RANDOM_SEEDS = 200

# Predeclared regime rules. Fixed before any result was read.
VOL_WARMUP = 90            # expanding-median warmup, days
BULL, BEAR = 0.05, -0.05   # BTC trailing 30d return thresholds


def regimes(panel: hm.Panel) -> pd.DataFrame:
    btc = panel.ret["BTCUSD"]
    rv = btc.rolling(30).std().shift(1) * np.sqrt(hm.DAYS_PER_YEAR)
    med = rv.expanding(VOL_WARMUP).median()          # causal, expanding
    mkt = panel.px["BTCUSD"].pct_change(30).shift(1)
    return pd.DataFrame({
        "vol": np.where(rv.isna() | med.isna(), None,
                        np.where(rv > med, "high_vol", "low_vol")),
        "market": np.where(mkt.isna(), None,
                           np.where(mkt > BULL, "bull",
                                    np.where(mkt < BEAR, "bear", "flat"))),
    }, index=panel.px.index)


def attribution(panel: hm.Panel, census: pd.DataFrame) -> dict:
    books, cur = {}, {}
    for day, g in census.groupby("day"):
        n = len(g)
        books[day] = {r.symbol: (0.5 / (n / 2) if r.side == "LONG" else -0.5 / (n / 2))
                      for r in g.itertuples()}
    pnl = {}
    for day in panel.px.index:
        if day in books:
            cur = books[day]
        if not cur:
            continue
        r = panel.ret.loc[day]
        for s, w in cur.items():
            v = r.get(s, 0.0)
            pnl[s] = pnl.get(s, 0.0) + w * (v if np.isfinite(v) else 0.0)
    p = pd.Series(pnl).sort_values(key=np.abs, ascending=False)
    total = p.sum()
    syms = sorted(census["symbol"].unique())
    rr = panel.ret[syms].dropna(how="all")
    c = np.nan_to_num(rr.corr().to_numpy(), nan=0.0)
    return {
        "effective_n": float(len(syms) ** 2 / c.sum()) if c.sum() else float("nan"),
        "n_names_held": len(syms),
        "total_pnl": float(total),
        "top1": float(p.iloc[0]), "top1_name": str(p.index[0]),
        "top3": float(p.iloc[:3].sum()), "top3_names": list(p.index[:3]),
        "top5": float(p.iloc[:5].sum()), "top5_names": list(p.index[:5]),
        "per_symbol": {s: float(v) for s, v in p.items()},
    }


def main() -> None:
    panel = hm.load_panel()
    reg = regimes(panel)
    rep: dict = {
        "panel": {"symbols": len(panel.symbols), "days": len(panel.px),
                  "start": str(panel.px.index.min().date()),
                  "end": str(panel.px.index.max().date())},
        "cost_model": {"gst": 1.18, "slippage_bps": 2.0,
                       "leg_cost_min": float(panel.leg_cost.min()),
                       "leg_cost_max": float(panel.leg_cost.max()),
                       "distinct_rates": int(panel.leg_cost.nunique())},
    }

    # ------------------------------------------------------------- PRIMARY
    F, T = hm.FORMATIONS["primary"], hm.THRESHOLDS["primary"]
    res = hm.backtest(panel, F, T)
    daily, census, sizing = res["daily"], res["census"], res["sizing"]
    st = hm.summarise(daily, hm.REBALANCE, boot=True)
    st.update(formation=F, threshold=T, rebalance=hm.REBALANCE)
    st["universe"] = {
        "eligible_mean": float(sizing["n_eligible"].mean()),
        "eligible_min": int(sizing["n_eligible"].min()),
        "eligible_max": int(sizing["n_eligible"].max()),
        "held_per_rebalance_mean": float(sizing["n_side"].mean()),
        "rebalances_skipped": int((sizing["n_side"] == 0).sum()),
        "names_ever_held": int(census["symbol"].nunique()),
    }
    # long and short legs measured separately
    for leg, mode in (("long_only", "long_only"),):
        r = hm.backtest(panel, F, T, mode=mode)
        st[leg] = hm.summarise(r["daily"], hm.REBALANCE)
    short_only = daily["gross"] - hm.backtest(panel, F, T, mode="long_only")["daily"]["gross"].reindex(daily.index).fillna(0.0)
    st["short_leg_gross_ann"] = float(short_only.sum() / (len(daily) / hm.DAYS_PER_YEAR))
    rep["primary"] = st
    daily.to_csv(OUT / "primary_daily.csv")
    census.to_csv(OUT / "primary_census.csv", index=False)

    rep["concentration"] = attribution(panel, census)

    # ------------------------------------------------------------ CONTROLS
    ctl = {}
    for mode in ("reverse", "long_only", "tsmom"):
        r = hm.backtest(panel, F, T, mode=mode)
        ctl[mode] = hm.summarise(r["daily"], hm.REBALANCE)
    nets, sharpes = [], []
    for seed in range(N_RANDOM_SEEDS):
        r = hm.backtest(panel, F, T, mode="random", seed=seed)
        s = hm.summarise(r["daily"], hm.REBALANCE)
        nets.append(s["net_ann"]); sharpes.append(s["sharpe"])
    nets = np.array(nets)
    ctl["random"] = {"seeds": N_RANDOM_SEEDS, "net_ann_mean": float(nets.mean()),
                     "net_ann_p05": float(np.percentile(nets, 5)),
                     "net_ann_p95": float(np.percentile(nets, 95)),
                     "sharpe_mean": float(np.nanmean(sharpes)),
                     "frac_random_beats_real":
                         float((nets >= rep["primary"]["net_ann"]).mean())}
    rep["controls"] = ctl

    # ---------------------------------------------------------- ROBUSTNESS
    rob = []
    for fname, F2 in hm.FORMATIONS.items():
        for tname, T2 in hm.THRESHOLDS.items():
            r = hm.backtest(panel, F2, T2)
            s = hm.summarise(r["daily"], hm.REBALANCE)
            if not s.get("days"):
                continue
            s.update(formation=F2, threshold=T2, form_label=fname,
                     thr_label=tname,
                     eligible_mean=float(r["sizing"]["n_eligible"].mean()),
                     is_primary=bool(F2 == F and T2 == T))
            rob.append(s)
    R = pd.DataFrame(rob)
    R.drop(columns=[c for c in ("sharpe_ci",) if c in R], errors="ignore").to_csv(
        OUT / "robustness.csv", index=False)
    rep["robustness_cells"] = int(len(R))
    rep["robustness_positive_net"] = int((R["net_ann"] > 0).sum())

    # ---------------------------------------------------------- SPLITS
    sp = {}
    for name, (a, b) in hm.SPLITS.items():
        d = daily.loc[(daily.index >= pd.Timestamp(a, tz="UTC")) &
                      (daily.index <= pd.Timestamp(b, tz="UTC"))]
        sp[name] = hm.summarise(d, hm.REBALANCE)
    rep["splits"] = sp

    # ---------------------------------------------------------- REGIMES
    rg = {}
    j = reg.reindex(daily.index)
    for col in ("vol", "market"):
        for label in [x for x in j[col].dropna().unique()]:
            d = daily[j[col] == label]
            if len(d) > 20:
                rg[f"{col}:{label}"] = hm.summarise(d, hm.REBALANCE)
    rep["regimes"] = rg

    (OUT / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print("written", OUT / "report.json")


if __name__ == "__main__":
    main()
