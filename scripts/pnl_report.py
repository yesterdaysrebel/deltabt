"""P&L analysis of a backtest sweep, in money rather than R multiples.

    PYTHONPATH=. python3 scripts/pnl_report.py

WHAT THE MONEY NUMBERS MEAN, AND WHAT THEY DO NOT
    Every cell starts at $10,000 and risks 0.5% of CURRENT equity per trade,
    capped at 3x leverage. So P&L compounds, and a dollar figure is a joint
    statement about the signal AND that risk model -- doubling the risk
    fraction roughly doubles both the return and the drawdown without changing
    a single trade. Expectancy in R is the size-independent number; the dollars
    are what that expectancy did under one specific staking plan.

    ``total_pnl`` resolves same-bar stop/target conflicts STOP-first, which is
    the pessimistic reading. ``optimistic_pnl`` resolves them target-first. The
    truth is between them, and the gap is a measure of intrabar ambiguity, not
    of edge.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR


def money(v: float) -> str:
    return f"{v:>12,.0f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", default=str(OUT_DIR / "sweep" / "backtests.csv"))
    ap.add_argument("--min-trades", type=int, default=30)
    args = ap.parse_args()

    raw = pd.read_csv(args.csv)
    ok = raw[raw["status"] == "ok"].copy()
    df = ok[ok["trades"] >= args.min_trades].copy()

    pd.set_option("display.width", 220)
    pd.set_option("display.float_format", lambda v: f"{v:,.2f}")

    n_cells, n_trades = len(df), int(df["trades"].sum())
    print("=" * 96)
    print("BACKTEST P&L  --  every cell starts at $10,000, risking 0.5% of equity per trade")
    print("=" * 96)
    print(f"cells with >= {args.min_trades} trades : {n_cells}")
    print(f"total trades executed    : {n_trades:,}")
    print(f"total signals generated  : {int(ok['signals'].sum()):,}")
    print(f"  refused on cost gate   : {int(ok['rej_cost'].sum()):,}")
    print(f"  refused on cooldown    : {int(ok['rej_cooldown'].sum()):,}")
    print(f"aggregate P&L over all cells : ${df['total_pnl'].sum():,.0f}")
    print(f"  total fees paid            : ${df['total_fees'].sum():,.0f}")
    print(f"  total funding paid         : ${df['total_funding'].sum():,.0f}")
    print(f"cells profitable         : {int((df['total_pnl'] > 0).sum())} of {n_cells}"
          f"  ({100*(df['total_pnl'] > 0).mean():.1f}%)")

    print("\n\n" + "=" * 96)
    print("BY TIMEFRAME")
    print("=" * 96)
    g = df.groupby("timeframe_min").agg(
        cells=("trades", "size"), trades=("trades", "sum"),
        trades_per_day=("trades_per_day", "median"),
        total_pnl=("total_pnl", "sum"), mean_return_pct=("return_pct", "mean"),
        median_return_pct=("return_pct", "median"),
        mean_max_dd_pct=("max_dd_pct", "mean"), fees=("total_fees", "sum"),
        funding=("total_funding", "sum"),
        profitable=("total_pnl", lambda s: int((s > 0).sum())))
    print(g.to_string())

    print("\n\n" + "=" * 96)
    print("BY FAMILY  (all timeframes and symbols pooled)")
    print("=" * 96)
    f = df.groupby("family").agg(
        cells=("trades", "size"), trades=("trades", "sum"),
        total_pnl=("total_pnl", "sum"), mean_return_pct=("return_pct", "mean"),
        mean_max_dd_pct=("max_dd_pct", "mean"),
        profit_factor=("profit_factor", "median"),
        win_rate=("win_rate", "mean"), fees=("total_fees", "sum"),
        profitable=("total_pnl", lambda s: int((s > 0).sum()))
    ).sort_values("total_pnl", ascending=False)
    print(f.to_string())

    print("\n\n" + "=" * 96)
    print("BY SYMBOL")
    print("=" * 96)
    s = df.groupby("symbol").agg(
        cells=("trades", "size"), trades=("trades", "sum"),
        total_pnl=("total_pnl", "sum"), mean_return_pct=("return_pct", "mean"),
        win_rate=("win_rate", "mean"), fees=("total_fees", "sum"),
        profitable=("total_pnl", lambda s2: int((s2 > 0).sum()))
    ).sort_values("total_pnl", ascending=False)
    print(s.to_string())

    print("\n\n" + "=" * 96)
    print("THE TEN BEST CELLS BY P&L")
    print("=" * 96)
    cols = ["symbol", "family", "timeframe_min", "trades", "trades_per_day",
            "win_rate", "total_pnl", "return_pct", "max_dd_pct",
            "profit_factor", "avg_win", "avg_loss", "best_trade", "worst_trade"]
    print(df.nlargest(10, "total_pnl")[cols].to_string(index=False))

    print("\n\nTHE TEN WORST CELLS BY P&L")
    print("=" * 96)
    print(df.nsmallest(10, "total_pnl")[cols].to_string(index=False))

    print("\n\n" + "=" * 96)
    print("WHERE THE MONEY WENT  --  gross wins, gross losses, and friction")
    print("=" * 96)
    w = df.groupby("timeframe_min").agg(
        gross_win=("gross_win", "sum"), gross_loss=("gross_loss", "sum"),
        fees=("total_fees", "sum"), funding=("total_funding", "sum"),
        net=("total_pnl", "sum"))
    w["fees as % of gross wins"] = 100 * w["fees"] / w["gross_win"]
    print(w.to_string())

    print("\n\n" + "=" * 96)
    print("INTRABAR AMBIGUITY  --  pessimistic vs optimistic same-bar resolution")
    print("=" * 96)
    a = df.groupby("timeframe_min").agg(
        pessimistic=("total_pnl", "sum"), optimistic=("optimistic_pnl", "sum"),
        ambiguous_pct=("ambiguous_pct", "mean"))
    a["spread"] = a["optimistic"] - a["pessimistic"]
    print(a.to_string())
    print("\nIf the spread is large relative to net P&L, the result is a")
    print("statement about intrabar assumptions rather than about the strategy.")


if __name__ == "__main__":
    main()
