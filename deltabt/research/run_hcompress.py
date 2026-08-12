"""Execute H-Compress-1 on TRAIN + VALIDATION only. TEST STAYS LOCKED.

    PYTHONPATH=. python3 -u -m deltabt.research.run_hcompress

The test slice is not computed anywhere in this module. Opening it requires
`--unlock-test`, which is refused unless the pre-registered PROMISING criteria
are met on train and validation.
"""

from __future__ import annotations

import argparse
import itertools
import json

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research import hcompress as hc
from deltabt.research.hcompress_nulls import run_nulls
from deltabt.research.stats import (
    block_bootstrap_mean,
    bootstrap_diff,
    effective_n,
    max_drawdown,
    sharpe_sortino,
    trade_design_effect,
)
from deltabt.strategy import resample_ohlcv

OUT = OUT_DIR / "hcompress"
STUDY_START = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())
SIG_BAR = 3.2


def splits(symbols_end: int) -> dict:
    """Chronological 60 / 20 / 20. TEST bounds are recorded but never used here."""
    span = symbols_end - STUDY_START
    a = STUDY_START + int(span * 0.60)
    b = STUDY_START + int(span * 0.80)
    return {"train": (STUDY_START, a), "valid": (a, b), "test": (b, symbols_end)}


def load(universe: list[str]) -> dict:
    store, cat = CandleStore(), ProductCatalog()
    return {
        s: dict(ltp=store.read(s, "ltp", "1m"), mark=store.read(s, "mark", "1m"),
                funding=store.read(s, "funding", "1h"),
                costs=SymbolCosts.from_spec(cat.get(s), slippage_bps=2.0))
        for s in universe
    }


def summarise(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return dict(label=label, trades=0, gross_r=None, net_r=None, note="no trades")
    r = df["r_net"].to_numpy("float64")
    g = df["r_gross"].to_numpy("float64")
    de = trade_design_effect(df)
    bs = block_bootstrap_mean(r, mean_block=6.0, n_boot=3000, seed=11)
    bg = block_bootstrap_mean(g, mean_block=6.0, n_boot=3000, seed=12)
    t_adj = bs["t"] / np.sqrt(de["deff"]) if np.isfinite(bs["t"]) else np.nan
    tg_adj = bg["t"] / np.sqrt(de["deff"]) if np.isfinite(bg["t"]) else np.nan
    wins = r[r > 0]; losses = r[r <= 0]
    sh, so = sharpe_sortino(r, periods_per_year=365 * 288 / max(df["bars_held"].mean(), 1))
    months = pd.to_datetime(df["entry_time"], unit="s").dt.to_period("M")
    monthly = df.groupby(months)["r_net"].sum()
    return dict(
        label=label, trades=int(len(df)),
        effective_n=round(de["n_eff"], 1), deff=round(de["deff"], 2),
        win_rate=round(float((r > 0).mean()), 4),
        gross_r=round(float(g.mean()), 4), cost_r=round(float(df["cost_r"].mean()), 4),
        funding_r=round(float(df["funding_r"].mean()), 5),
        net_r=round(float(r.mean()), 4), median_r=round(float(np.median(r)), 4),
        avg_win=round(float(wins.mean()), 4) if wins.size else None,
        avg_loss=round(float(losses.mean()), 4) if losses.size else None,
        profit_factor=round(float(wins.sum() / -losses.sum()), 3) if losses.sum() < 0 else None,
        sharpe=round(sh, 3) if np.isfinite(sh) else None,
        sortino=round(so, 3) if np.isfinite(so) else None,
        max_dd_r=round(max_drawdown(np.cumsum(r)), 2),
        ci_low=round(bs["ci_low"], 4), ci_high=round(bs["ci_high"], 4),
        t_net=round(t_adj, 3) if np.isfinite(t_adj) else None,
        t_gross=round(tg_adj, 3) if np.isfinite(tg_adj) else None,
        hold_mean=round(float(df["bars_held"].mean()), 2),
        hold_median=float(df["bars_held"].median()),
        stop_pct_mean=round(float(df["stop_pct"].mean()) * 100, 3),
        stop_pct_median=round(float(df["stop_pct"].median()) * 100, 3),
        pct_positive_months=round(100 * float((monthly > 0).mean()), 1),
        pct_target=round(100 * float((df.exit_reason == "target").mean()), 1),
        pct_stop=round(100 * float((df.exit_reason == "stop").mean()), 1),
        pct_time=round(100 * float((df.exit_reason == "time").mean()), 1),
        pct_long=round(100 * float((df.side > 0).mean()), 1),
    )


def run_arm(data, lo, hi, *, arm, params, fill_model="touch",
            cost_multiplier=1.0, slippage_multiplier=1.0):
    res, frames = {}, []
    for s, d in data.items():
        r = hc.run(d["ltp"], d["mark"], d["funding"], d["costs"],
                   start=STUDY_START, end=hi, arm=arm, fill_model=fill_model,
                   cost_multiplier=cost_multiplier,
                   slippage_multiplier=slippage_multiplier, **params)
        res[s] = r
        f = r.to_frame()
        if len(f):
            frames.append(f[(f.entry_time >= lo) & (f.entry_time < hi)])
    pooled = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return res, pooled


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--unlock-test", action="store_true",
                    help="refused unless train+validation meet the PROMISING criteria")
    args = ap.parse_args(argv)

    OUT.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(OUT_DIR / "hcompress_universe.csv").symbol.tolist()
    data = load(universe)
    last = min(int(d["ltp"].time.iloc[-1]) for d in data.values())
    SP = splits(last)
    print(f"universe: {universe}")
    for k, (a, b) in SP.items():
        print(f"  {k:<6} {pd.Timestamp(a,unit='s').date()} -> {pd.Timestamp(b,unit='s').date()}"
              + ("   [LOCKED]" if k == "test" else ""))

    cols = {}
    for s in universe:
        d = data[s]["ltp"]; d = d[d.time >= STUDY_START]
        b = resample_ohlcv(d, 5)
        cols[s] = pd.Series(b.close.to_numpy(float), index=b.time.to_numpy("int64"))
    xn = effective_n(np.log(pd.DataFrame(cols)).diff().dropna())
    print(f"  cross-sectional: nominal {xn['K']}, mean_rho {xn['mean_rho']:.3f}, "
          f"N_eff(PR) {xn['pr']:.2f}, N_eff(DEFF) {xn['deff']:.2f}\n")

    tr_lo, tr_hi = SP["train"]; va_lo, va_hi = SP["valid"]

    # ---- PRIMARY, both execution arms -----------------------------------
    print("=" * 78)
    print("PRIMARY ARM  (percentile 20, min 4 bars, vol 1.5x, body 0.5 ATR, 2R)")
    print("=" * 78)
    primary = {}
    for arm, tag in (("A", "A passive retest [PRIMARY]"), ("B", "B taker breakout [DIAGNOSTIC]")):
        res_tr, tr = run_arm(data, tr_lo, tr_hi, arm=arm, params=hc.PRIMARY)
        res_va, va = run_arm(data, va_lo, va_hi, arm=arm, params=hc.PRIMARY)
        primary[arm] = dict(train=summarise(tr, "train"), valid=summarise(va, "valid"),
                            tr_df=tr, va_df=va, res=res_va)
        print(f"\n--- {tag}")
        print(f"    compression events {sum(r.compression_events for r in res_va.values()):,}"
              f" | expansion {sum(r.expansion_events for r in res_va.values()):,}"
              f" | orders {sum(r.orders for r in res_va.values()):,}"
              f" | fills {sum(r.fills for r in res_va.values()):,}"
              f" | wide-stop skips {sum(r.skipped_wide_stop for r in res_va.values()):,}")
        fr = sum(r.fills for r in res_va.values()) / max(sum(r.orders for r in res_va.values()), 1)
        print(f"    fill rate {100*fr:.1f}%")
        for k in ("train", "valid"):
            m = primary[arm][k]
            if m["trades"] == 0:
                print(f"    {k}: no trades"); continue
            print(f"    {k}: n={m['trades']:<5} win={m['win_rate']:.3f} "
                  f"GROSS={m['gross_r']:+.4f} cost={m['cost_r']:.4f} NET={m['net_r']:+.4f} "
                  f"t_net={m['t_net']} t_gross={m['t_gross']}")

    json.dump({a: {k: v for k, v in d.items() if k in ("train", "valid")}
               for a, d in primary.items()},
              open(OUT / "primary.json", "w"), indent=2, default=str)
    for a in ("A", "B"):
        pd.concat([primary[a]["tr_df"], primary[a]["va_df"]], ignore_index=True) \
          .to_csv(OUT / f"trades_arm{a}.csv", index=False)

    # ---- detail on the primary arm --------------------------------------
    A = primary["A"]
    both = pd.concat([A["tr_df"], A["va_df"]], ignore_index=True)
    if not both.empty:
        print("\nPER SYMBOL (arm A, train+valid)")
        print(pd.DataFrame([summarise(g, s) for s, g in both.groupby("symbol")])
              [["label", "trades", "win_rate", "gross_r", "cost_r", "net_r", "t_net"]]
              .to_string(index=False))
        print("\nLONG vs SHORT (arm A)")
        print(pd.DataFrame([summarise(g, "long" if k > 0 else "short")
                            for k, g in both.groupby("side")])
              [["label", "trades", "win_rate", "gross_r", "net_r"]].to_string(index=False))
        q = both.assign(quarter=pd.to_datetime(both.entry_time, unit="s")
                        .dt.to_period("Q").astype(str))
        print("\nBY QUARTER (arm A)")
        rows = []
        for name, sub in q.groupby("quarter"):
            r_ = summarise(sub, name)
            if len(sub) < 20:
                r_["note"] = "INSUFFICIENT"
            rows.append(r_)
        qs = pd.DataFrame(rows)
        c_ = ["label", "trades", "win_rate", "gross_r", "net_r"] + (["note"] if "note" in qs else [])
        print(qs[c_].to_string(index=False))
        qs.to_csv(OUT / "by_quarter.csv", index=False)

    # ---- nulls, on the primary arm, train+valid --------------------------
    print("\nNULL MODELS (arm A, train+valid)")
    nulls = {"A": [], "B": [], "C": []}
    for s, d in data.items():
        sub = both[both.symbol == s] if not both.empty else pd.DataFrame()
        if sub.empty:
            continue
        nn = run_nulls(d["ltp"], d["mark"], d["funding"], d["costs"], sub,
                       start=STUDY_START, end=va_hi, arm="A",
                       target_r=hc.PRIMARY["target_r"], n_sims=40, seed=3)
        for k in nulls:
            if nn[k].size:
                nulls[k].append(nn[k])
    strat = both["r_net"].to_numpy("float64") if not both.empty else np.zeros(0)
    null_summary = {}
    names = {"A": "A random eligible timing", "B": "B shuffled direction",
             "C": "C vol-matched permutation"}
    for k, label in names.items():
        if not nulls[k] or strat.size == 0:
            print(f"  {label:<30} (no data)"); continue
        pool = np.concatenate(nulls[k])
        cmp = bootstrap_diff(strat, pool, mean_block=6.0, n_boot=3000, seed=17)
        null_summary[k] = dict(null_mean=float(pool.mean()), n=int(pool.size), **{
            kk: (float(vv) if isinstance(vv, (int, float, np.floating)) else vv)
            for kk, vv in cmp.items()})
        print(f"  {label:<30} null={pool.mean():+.4f} (n={pool.size:,})  "
              f"strategy-null={cmp['diff']:+.4f}  CI[{cmp['ci_low']:+.4f},{cmp['ci_high']:+.4f}]  "
              f"t={cmp['t']:.2f}")

    # ---- cost sensitivity -------------------------------------------------
    print("\nCOST SENSITIVITY (arm A, train+valid)")
    sens = []
    for cm, sm, lbl in ((1.0, 1.0, "realistic"), (0.5, 0.5, "50% costs"),
                        (1.0, 0.0, "zero slippage"), (0.0, 0.0, "zero cost [diagnostic]")):
        _, tr = run_arm(data, tr_lo, tr_hi, arm="A", params=hc.PRIMARY,
                        cost_multiplier=cm, slippage_multiplier=sm)
        _, va = run_arm(data, va_lo, va_hi, arm="A", params=hc.PRIMARY,
                        cost_multiplier=cm, slippage_multiplier=sm)
        f = pd.concat([tr, va], ignore_index=True)
        if not f.empty:
            sens.append(dict(scenario=lbl, trades=len(f),
                             gross_r=round(float(f.r_gross.mean()), 4),
                             net_r=round(float(f.r_net.mean()), 4)))
            print(f"  {lbl:<22} n={len(f):>5} gross={f.r_gross.mean():+.4f} "
                  f"net={f.r_net.mean():+.4f}")
    pd.DataFrame(sens).to_csv(OUT / "cost_sensitivity.csv", index=False)

    # ---- pre-declared grid, validation only ------------------------------
    print("\nPRE-DECLARED GRID (108 arms, arm A, VALIDATION)")
    keys = list(hc.GRID)
    rows = []
    for combo in itertools.product(*(hc.GRID[k] for k in keys)):
        p = dict(zip(keys, combo))
        _, va = run_arm(data, va_lo, va_hi, arm="A", params=p)
        r_ = summarise(va, "|".join(f"{k}={v}" for k, v in p.items()))
        r_.update(p)
        rows.append(r_)
    grid = pd.DataFrame(rows)
    grid.to_csv(OUT / "grid.csv", index=False)
    ok = grid[grid.trades >= 30]
    print(f"  arms: {len(grid)} | with >=30 trades: {len(ok)}")
    if len(ok):
        print(f"  arms with positive NET on validation: {int((ok.net_r > 0).sum())}/{len(ok)}")
        print(f"  arms with positive GROSS on validation: {int((ok.gross_r > 0).sum())}/{len(ok)}")
        print("\n  best 5 by net R (NOT a selection -- multiple-comparison exposed):")
        print(ok.nlargest(5, "net_r")[
            ["percentile", "min_duration", "volume_mult", "body_mult", "target_r",
             "trades", "gross_r", "net_r", "t_net"]].to_string(index=False))
        # expected max |t| under the null across the effective number of arms
        m_eff = max(len(ok) / 8, 2)   # grid cells are heavily correlated
        e_max = (1 - 0.5772) * abs(np.percentile(np.random.default_rng(0).standard_normal(200000),
                                                 100 * (1 - 1 / m_eff))) + 0.5772 * abs(
            np.percentile(np.random.default_rng(1).standard_normal(200000),
                          100 * (1 - 1 / (m_eff * np.e))))
        print(f"\n  E[max |t|] under the null at M_eff~{m_eff:.0f}: {e_max:.2f} "
              f"(observed best |t_net|: {ok.t_net.abs().max():.2f})")

    json.dump(dict(universe=universe, splits={k: list(v) for k, v in SP.items()},
                   cross_sectional_n=xn, nulls=null_summary),
              open(OUT / "meta.json", "w"), indent=2, default=str)

    # ---- gate -------------------------------------------------------------
    print("\n" + "=" * 78)
    tr_m, va_m = primary["A"]["train"], primary["A"]["valid"]
    promising = (tr_m["trades"] and va_m["trades"]
                 and (tr_m.get("gross_r") or 0) > 0 and (va_m.get("gross_r") or 0) > 0
                 and (tr_m.get("net_r") or 0) > 0 and (va_m.get("net_r") or 0) > 0)
    print(f"PROMISING criteria (gross AND net > 0 on BOTH train and validation): "
          f"{'MET' if promising else 'NOT MET'}")
    if args.unlock_test and not promising:
        print("TEST REMAINS LOCKED — unlock refused; criteria not met.")
    elif args.unlock_test:
        print("TEST unlock permitted by the pre-registered rule.")
    else:
        print("TEST NOT COMPUTED (locked by default).")
    print(f"\nwritten to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
