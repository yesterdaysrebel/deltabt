"""Summarise a backtest sweep.

    PYTHONPATH=. python3 scripts/sweep_report.py [--csv out/sweep/backtests.csv]

Reports pooled results rather than the best cell. With hundreds of cells the
best one is a sampling maximum, so every table here is either an average over
symbols or an explicit count of how many symbols agree.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR


def _pooled(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Trade-weighted pooling, so a 3-trade cell cannot outvote a 3,000."""
    g = df.groupby(by)
    out = g.apply(lambda d: pd.Series({
        "cells": len(d),
        "trades": int(d["trades"].sum()),
        "symbols": d["symbol"].nunique() if "symbol" in d else 1,
        "win_rate": np.average(d["win_rate"], weights=d["trades"].clip(lower=1)),
        "gross_r": np.average(d["gross_r"], weights=d["trades"].clip(lower=1)),
        "net_r": np.average(d["net_r"], weights=d["trades"].clip(lower=1)),
        "cost_r": np.average(d["cost_r"], weights=d["trades"].clip(lower=1)),
        "pos_cells": int((d["net_r"] > 0).sum()),
    }), include_groups=False)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", default=str(OUT_DIR / "sweep" / "backtests.csv"))
    ap.add_argument("--min-trades", type=int, default=30,
                    help="cells below this are reported separately, not pooled")
    args = ap.parse_args()

    raw = pd.read_csv(args.csv)
    ok = raw[raw["status"] == "ok"].copy()
    thin = ok[ok["trades"] < args.min_trades]
    df = ok[ok["trades"] >= args.min_trades].copy()

    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda v: f"{v:+.4f}")

    print(f"cells run              : {len(raw)}")
    print(f"  ok                   : {len(ok)}")
    print(f"  errors / too short   : {len(raw) - len(ok)}")
    print(f"  under {args.min_trades} trades (excluded from pooling): {len(thin)}")
    print(f"  pooled               : {len(df)}   total trades {int(df.trades.sum()):,}")

    print("\n\n=== BY TIMEFRAME (all families pooled) ===")
    print(_pooled(df, ["timeframe_min"]).to_string())

    print("\n\n=== BY FAMILY (all timeframes pooled) ===")
    fam = _pooled(df, ["family"]).sort_values("net_r", ascending=False)
    print(fam.to_string())

    print("\n\n=== BY FAMILY x TIMEFRAME: net R ===")
    piv = df.pivot_table(index="family", columns="timeframe_min",
                         values="net_r", aggfunc="mean")
    print(piv.to_string())

    print("\n\n=== BY FAMILY x TIMEFRAME: gross R (before cost) ===")
    pivg = df.pivot_table(index="family", columns="timeframe_min",
                          values="gross_r", aggfunc="mean")
    print(pivg.to_string())

    print("\n\n=== THE COST WALL ===")
    print("Share of signals refused by the cost gate (max_cost_per_r = 0.15).")
    print("This, not realised cost_r, is where the cost law shows up: the gate")
    print("truncates cost_r from above, so cost_r ON TAKEN TRADES is compressed")
    print("toward the gate at every timeframe and understates the true spread.")
    g = df.groupby("timeframe_min").agg(
        signals=("signals", "sum"), refused=("rej_cost", "sum"), taken=("trades", "sum"))
    g["% refused on cost"] = 100 * g["refused"] / (g["refused"] + g["taken"])
    g["cost_r on taken"] = df.groupby("timeframe_min").apply(
        lambda d: np.average(d["cost_r"], weights=d["trades"]), include_groups=False)
    print(g.to_string())

    print("\n\n=== WIN RATE vs THE RANDOM-ENTRY RATE ===")
    print("a 2R target gives 1/(1+R) = 33.3% on directionless entries")
    w = df.groupby("timeframe_min").apply(
        lambda d: np.average(d["win_rate"], weights=d["trades"]), include_groups=False)
    print(pd.DataFrame({"win_rate": w, "minus 1/3": w - 1 / 3}).to_string())

    print("\n\n=== CELLS WITH POSITIVE NET EXPECTANCY ===")
    pos = df[df["net_r"] > 0].sort_values("net_r", ascending=False)
    print(f"{len(pos)} of {len(df)} pooled cells ({100*len(pos)/max(len(df),1):.1f}%)")
    if len(pos):
        cols = ["symbol", "family", "timeframe_min", "trades", "win_rate",
                "gross_r", "net_r", "net_r_lo", "net_r_hi", "cost_r"]
        print(pos[cols].head(20).to_string(index=False))

        print("\n--- of those, how many have a CI excluding zero? ---")
        sig = pos[pos["net_r_lo"] > 0]
        print(f"{len(sig)} cells")
        if len(sig):
            print(sig[cols].to_string(index=False))

        print("\n--- and does any family/timeframe hold on a MAJORITY of symbols? ---")
        agree = (df.assign(win=df["net_r"] > 0)
                   .groupby(["family", "timeframe_min"])
                   .agg(symbols=("symbol", "nunique"), positive=("win", "sum")))
        agree["share"] = agree["positive"] / agree["symbols"]
        maj = agree[(agree["share"] > 0.5) & (agree["symbols"] >= 3)]
        print(maj.sort_values("share", ascending=False).to_string()
              if len(maj) else "none")

    if len(thin):
        print("\n\n=== THIN CELLS (excluded above; shown so they are not invisible) ===")
        print(thin.groupby("family")["trades"].agg(["count", "sum"]).to_string())


if __name__ == "__main__":
    main()
