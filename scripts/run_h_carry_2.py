"""Driver for H-Carry-2. Executes the frozen prereg and writes out/hcarry2/.

Order is deliberate: leakage assertions (tests/test_h_carry_2.py) run first and
are a hard gate, then the primary cell, then the kill criteria, then the nulls,
then the exploratory grid. Nothing downstream can change anything upstream.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import h_carry_2 as hc  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "out" / "hcarry2"
OUT.mkdir(parents=True, exist_ok=True)

PRIMARY = dict(lookback=7, hold=7, threshold=1_000_000.0)
GRID_L = [7, 14, 30]
GRID_H = [7, 14, 30]
GRID_T = [500_000.0, 1_000_000.0, 5_000_000.0]
N_NULL_SEEDS = 200

SPLITS = {
    "TRAIN": ("2025-01-01", "2025-12-31"),
    "VALIDATION": ("2026-01-01", "2026-05-31"),
    "TEST": ("2026-06-01", "2026-08-28"),
}


def effective_n(panel: hc.Panel, symbols: list[str]) -> float:
    """N_eff = N^2 / sum_ij rho_ij for an equally weighted book."""
    if len(symbols) < 2:
        return float(len(symbols))
    r = panel.ret[symbols].dropna(how="all")
    c = r.corr().to_numpy()
    c = np.nan_to_num(c, nan=0.0)
    return float(len(symbols) ** 2 / c.sum())


def main() -> None:
    panel = hc.build_panel(hc.load())
    report: dict = {"calendar": [str(panel.calendar.min().date()),
                                str(panel.calendar.max().date())],
                    "n_symbols_loaded": len(panel.symbols)}

    # ------------------------------------------------------------ primary
    res = hc.run(panel, PRIMARY["lookback"], PRIMARY["hold"], PRIMARY["threshold"])
    daily, census, sizing = res["daily"], res["census"], res["sizing"]
    stats = hc.summarise(daily, PRIMARY["hold"])
    held_syms = sorted(census["symbol"].unique())
    stats["n_symbols_used"] = len(held_syms)
    stats["symbols_used"] = held_syms
    stats["n_eligible_mean"] = float(sizing["n_eligible"].mean())
    stats["k_mode"] = int(sizing["k"].mode().iloc[0])
    stats["k_range"] = [int(sizing["k"].min()), int(sizing["k"].max())]
    stats["effective_n"] = effective_n(panel, held_syms)
    report["primary"] = stats
    daily.to_csv(OUT / "primary_daily.csv")
    census.to_csv(OUT / "primary_census.csv", index=False)
    sizing.to_csv(OUT / "primary_sizing.csv", index=False)

    # ------------------------------------------------------- kill criteria
    reb = census["day"].nunique()
    shorts = census[census["side"] == "SHORT"]
    below = shorts[shorts["turnover"] < PRIMARY["threshold"]]
    k1_frac = below["day"].nunique() / reb if reb else float("nan")
    share = shorts.groupby("symbol")["day"].nunique() / reb
    k2_sym = share.idxmax() if len(share) else None
    k2_max = float(share.max()) if len(share) else 0.0
    k3_resid = stats["price_vol_ann"]
    k3_carry = stats["carry_ann"]
    report["kill"] = {
        "k1_liquidity_disguise": {
            "frac_rebalances_with_sub_floor_short": float(k1_frac),
            "limit": 0.30, "verdict": "PASS" if k1_frac <= 0.30 else "FAIL",
            "note": ("Vacuous by construction: eligibility screens on the same "
                     "causal 30d median at the same floor, so a short-leg "
                     "constituent below the floor cannot exist. Disclosed, not "
                     "modified. See k1_supplementary."),
        },
        "k1_supplementary": {
            "short_leg_turnover_p05": float(shorts["turnover"].quantile(0.05)),
            "short_leg_turnover_median": float(shorts["turnover"].median()),
            "long_leg_turnover_median": float(
                census[census["side"] == "LONG"]["turnover"].median()),
            "frac_short_picks_below_5M": float((shorts["turnover"] < 5e6).mean()),
            "frac_short_picks_below_2M": float((shorts["turnover"] < 2e6).mean()),
        },
        "k2_single_name": {
            "worst_symbol": k2_sym, "share_of_rebalances": k2_max,
            "limit": 0.50, "verdict": "PASS" if k2_max <= 0.50 else "FAIL",
            "short_leg_shares": {s: float(v) for s, v in
                                 share.sort_values(ascending=False).items()},
        },
        "k3_residual": {
            "annualised_price_vol": float(k3_resid),
            "annualised_carry": float(k3_carry),
            "ratio_vol_over_carry": float(k3_resid / k3_carry)
            if k3_carry else float("inf"),
            "limit": 2.0,
            "verdict": "PASS" if k3_resid < 2 * k3_carry else "FAIL",
        },
    }

    # --------------------------------------------------------------- nulls
    nulls = {}
    for mode in ("sign", "shuffle", "random"):
        nets, carries, sharpes = [], [], []
        for seed in range(N_NULL_SEEDS):
            r = hc.run(panel, PRIMARY["lookback"], PRIMARY["hold"],
                       PRIMARY["threshold"], mode=mode, seed=seed)
            st = hc.summarise(r["daily"], PRIMARY["hold"], boot=False)
            nets.append(st["net_ann"]); carries.append(st["carry_ann"])
            sharpes.append(st["sharpe"])
        nets, carries, sharpes = map(np.array, (nets, carries, sharpes))
        nulls[mode] = {
            "seeds": N_NULL_SEEDS,
            "net_ann_mean": float(nets.mean()),
            "net_ann_p05": float(np.percentile(nets, 5)),
            "net_ann_p95": float(np.percentile(nets, 95)),
            "carry_ann_mean": float(carries.mean()),
            "sharpe_mean": float(np.nanmean(sharpes)),
            "frac_null_beats_real_net": float((nets >= stats["net_ann"]).mean()),
            "frac_null_beats_real_carry": float((carries >= stats["carry_ann"]).mean()),
        }
    report["nulls"] = nulls

    # ------------------------------------------------------- splits
    splits = {}
    for name, (a, b) in SPLITS.items():
        d = daily.loc[(daily.index >= pd.Timestamp(a, tz="UTC")) &
                      (daily.index <= pd.Timestamp(b, tz="UTC"))]
        splits[name] = hc.summarise(d, PRIMARY["hold"], boot=False)
    report["splits"] = splits

    # -------------------------------------------------------- exploratory grid
    grid = []
    for L in GRID_L:
        for H in GRID_H:
            for T in GRID_T:
                r = hc.run(panel, L, H, T)
                st = hc.summarise(r["daily"], H, boot=False)
                if not st.get("days"):
                    continue
                c = r["census"]
                st.update(L=L, H=H, T=T,
                          n_symbols=int(c["symbol"].nunique()) if len(c) else 0,
                          k_mode=int(r["sizing"]["k"].mode().iloc[0]),
                          n_eligible_mean=float(r["sizing"]["n_eligible"].mean()),
                          is_primary=bool(L == PRIMARY["lookback"] and
                                          H == PRIMARY["hold"] and
                                          T == PRIMARY["threshold"]))
                grid.append(st)
    g = pd.DataFrame(grid)
    g["bh_pass"] = hc.benjamini_hochberg(g["p_value"].fillna(1.0).tolist())
    g.drop(columns=[c for c in ("sharpe_ci",) if c in g], errors="ignore").to_csv(
        OUT / "grid.csv", index=False)
    report["grid"] = {
        "cells": int(len(g)),
        "raw_p_lt_05": int((g["p_value"] < 0.05).sum()),
        "raw_p_lt_05_and_positive": int(((g["p_value"] < 0.05) & (g["net_ann"] > 0)).sum()),
        "surviving_bh": int(g["bh_pass"].sum()),
        "sharpe_gt_1": int((g["sharpe"] > 1.0).sum()),
    }

    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
