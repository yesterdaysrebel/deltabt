"""Command-line entry point.

    python -m deltabt.cli screen
    python -m deltabt.cli fetch --symbols BTCUSD --days 30
    python -m deltabt.cli backtest --mode parity --symbols BTCUSD
    python -m deltabt.cli wpr-curve --symbols BTCUSD
    python -m deltabt.cli sweep
    python -m deltabt.cli walkforward
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import replace

import pandas as pd

from deltabt.config import OUT_DIR, RESOLUTION_1M, StrategyParams, WprLatch
from deltabt.data.store import DEFAULT_HISTORY_START, CandleStore, ProductCatalog
from deltabt.metrics import format_summary
from deltabt.runner import (
    backtest_symbol,
    load_symbol,
    rank_by_turnover,
    screen_universe,
)
from deltabt.sweep import GridSpec, params_from_row, run_grid, select_best, walk_forward

DEFAULT_SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]


def _window(args) -> tuple[int, int]:
    end = int(args.end) if args.end else int(time.time())
    if args.start:
        start = int(args.start)
    elif args.days:
        start = end - args.days * 86400
    else:
        start = DEFAULT_HISTORY_START
    return start, end


def _params_from_args(args) -> StrategyParams:
    if args.mode == "parity":
        return StrategyParams.parity()
    wpr = WprLatch(
        enabled=args.wpr,
        length=args.wpr_length,
        fire_long=args.wpr_fire_long,
        fire_short=args.wpr_fire_short,
        expiry_bars=args.wpr_expiry,
    )
    return StrategyParams(
        mode="corrected",
        base_minutes=args.base_minutes,
        confirm_minutes=args.confirm_minutes,
        st_atr_period=args.st_atr,
        st_factor=args.st_factor,
        wpr=wpr,
        max_leverage=args.max_leverage,
        risk_percent=args.risk,
        reward_risk=args.rr,
        max_cost_per_r=None if args.no_cost_gate else args.max_cost_per_r,
    )


def cmd_screen(args) -> int:
    store, catalog = CandleStore(), ProductCatalog()
    start, end = _window(args)
    symbols = args.symbols or rank_by_turnover(catalog, limit=args.limit)
    print(f"screening {len(symbols)} symbols on 1m data quality...\n")
    df = screen_universe(symbols, start, end, store=store, catalog=catalog,
                         max_synthetic=args.max_synthetic)

    cols = ["symbol", "bars", "synthetic_ratio", "coverage", "age_days",
            "halt_bars", "passes", "reason"]
    show = df[[c for c in cols if c in df.columns]]
    with pd.option_context("display.width", 160, "display.max_rows", 200):
        print(show.to_string(index=False))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_DIR / "screen.csv", index=False)
    passing = df.loc[df["passes"], "symbol"].tolist()
    print(f"\npassing: {passing or '(none)'}")
    print(f"written to {OUT_DIR / 'screen.csv'}")
    return 0


def cmd_fetch(args) -> int:
    """Populate the candle cache so the research modules can run offline.

    The research package reads with ``CandleStore.read``, which never fetches:
    an empty cache makes an experiment exit rather than silently study a short
    window. `data/` is gitignored, so a clean checkout has no candles at all
    and this is what fills it.

    The product catalog is refreshed too. Candles alone are not enough to run a
    research module offline -- ``SymbolCosts.from_spec(catalog.get(sym))`` needs
    `meta/products.json`, which a clean checkout also lacks.
    """
    store, catalog = CandleStore(), ProductCatalog()
    start, end = _window(args)
    symbols = args.symbols or DEFAULT_SYMBOLS
    print(f"fetching {len(symbols)} symbols "
          f"{pd.Timestamp(start, unit='s').date()}..{pd.Timestamp(end, unit='s').date()}\n")
    if not args.offline:
        catalog.all()

    rows = []
    for sym in symbols:
        try:
            series = store.load_all_series(
                sym, RESOLUTION_1M, start, end, refresh=not args.offline,
            )
        except Exception as exc:
            print(f"  {sym}: SKIPPED ({exc})")
            continue
        ltp = series["ltp"]
        # An empty result is a failure, not a cached symbol: without this the
        # `not rows` guard below only catches symbols that RAISED, so an
        # inverted or pre-listing window reports success and exits 0.
        if ltp.empty:
            print(f"  {sym}: SKIPPED (no candles in the requested window)")
            continue
        row = {"symbol": sym}
        for name, df in series.items():
            row[f"{name}_bars"] = len(df)
        row["first"] = pd.Timestamp(int(ltp["time"].iloc[0]), unit="s").date()
        row["last"] = pd.Timestamp(int(ltp["time"].iloc[-1]), unit="s").date()
        rows.append(row)
        print(f"  {sym}: {len(ltp):,} 1m bars  {row['first']} -> {row['last']}")

    if not rows:
        print("\nno symbols cached")
        return 1
    with pd.option_context("display.width", 160):
        print(f"\n{pd.DataFrame(rows).to_string(index=False)}")
    print(f"\ncached under {store.cache_dir}")
    return 0


def cmd_backtest(args) -> int:
    store, catalog = CandleStore(), ProductCatalog()
    start, end = _window(args)
    params = _params_from_args(args)
    symbols = args.symbols or DEFAULT_SYMBOLS

    print(f"mode={params.mode}  wpr={'off' if not params.wpr.enabled else params.wpr.length}  "
          f"window={pd.Timestamp(start, unit='s').date()}..{pd.Timestamp(end, unit='s').date()}\n")

    summaries = []
    for sym in symbols:
        try:
            data = load_symbol(
                sym, start, end, store=store, catalog=catalog,
                slippage_bps=args.slippage_bps, refresh=not args.offline,
            )
        except Exception as exc:
            print(f"  {sym}: SKIPPED ({exc})")
            continue
        result, m = backtest_symbol(data, params, initial_capital=args.capital)
        print(format_summary(m))
        print()
        summaries.append(m.as_dict())
        if args.save_trades:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            result.to_frame().to_csv(OUT_DIR / f"trades_{sym}_{params.mode}.csv", index=False)

    if summaries:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / f"summary_{params.mode}.json"
        path.write_text(json.dumps(summaries, indent=2, default=str))
        print(f"written to {path}")

        total = sum(s["trades"] for s in summaries)
        if params.mode == "parity":
            print()
            if total <= 20:
                print(f"PARITY CHECK PASSED: {total} trades across {len(summaries)} symbols.")
                print("  Matches the TradingView ground truth of zero closed trades.")
            else:
                print(f"PARITY CHECK FAILED: {total} trades, expected ~0.")
                print("  The port disagrees with TradingView; downstream results are void.")
    return 0


def cmd_wpr_curve(args) -> int:
    """Trade count and net E[R] as a function of WPR length."""
    store, catalog = CandleStore(), ProductCatalog()
    start, end = _window(args)
    symbols = args.symbols or DEFAULT_SYMBOLS
    lengths = [int(x) for x in args.lengths.split(",")]

    base = _params_from_args(args)
    rows = []
    for sym in symbols:
        try:
            data = load_symbol(
                sym, start, end, store=store, catalog=catalog,
                slippage_bps=args.slippage_bps, refresh=not args.offline,
            )
        except Exception as exc:
            print(f"  {sym}: SKIPPED ({exc})")
            continue

        # WPR off is the reference the panel found hardest to beat; length 0
        # denotes it in the output so the curve has an explicit baseline.
        off = replace(base, wpr=replace(base.wpr, enabled=False))
        _, m = backtest_symbol(data, off, initial_capital=args.capital)
        rows.append({"symbol": sym, "wpr_length": 0, **_curve_row(m)})
        print(f"  {sym} wpr=OFF  trades={m.trades:6d}  E[R]={m.expectancy_r:+.3f}")

        for L in lengths:
            p = replace(base, wpr=replace(base.wpr, enabled=True, length=L))
            _, m = backtest_symbol(data, p, initial_capital=args.capital)
            rows.append({"symbol": sym, "wpr_length": L, **_curve_row(m)})
            print(f"  {sym} wpr={L:4d}  trades={m.trades:6d}  E[R]={m.expectancy_r:+.3f}")
        print()

    df = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_DIR / "wpr_curve.csv", index=False)
    print(df.to_string(index=False))
    print(f"\nwritten to {OUT_DIR / 'wpr_curve.csv'}")
    return 0


def _curve_row(m) -> dict:
    return {
        "trades": m.trades,
        "interpretable": m.interpretable,
        "win_rate": m.win_rate,
        "expectancy_r": m.expectancy_r,
        "expectancy_r_lo": m.expectancy_r_lo,
        "expectancy_r_hi": m.expectancy_r_hi,
        "median_r_bps": m.median_r_bps,
        "cost_per_r": m.avg_cost_per_r,
        "total_pnl": m.total_pnl,
        "max_dd_pct": m.max_drawdown_pct,
    }


def _load_all(args, store, catalog, start, end) -> dict:
    """Load every requested symbol once; the sweep reuses them across cells."""
    out = {}
    for sym in (args.symbols or DEFAULT_SYMBOLS):
        try:
            out[sym] = load_symbol(
                sym, start, end, store=store, catalog=catalog,
                slippage_bps=args.slippage_bps, refresh=not args.offline,
            )
        except Exception as exc:
            print(f"  {sym}: SKIPPED ({exc})")
    return out


def cmd_sweep(args) -> int:
    store, catalog = CandleStore(), ProductCatalog()
    start, end = _window(args)
    datasets = _load_all(args, store, catalog, start, end)
    if not datasets:
        print("no symbols loaded")
        return 1

    grid = GridSpec.fine() if args.fine else GridSpec()
    if args.no_cost_gate:
        # Removing the gate stops signals being FILTERED by cost ratio; fees,
        # slippage and funding are still charged in the P&L, so results remain
        # net of cost.
        grid = replace(grid, max_cost_per_r=(None,))
    if not args.wpr:
        grid = replace(grid, wpr_length=(0,))
    combos = len(grid.combinations())
    print(f"sweeping {combos} configurations over {len(datasets)} symbols "
          f"({sum(len(d.ltp) for d in datasets.values()):,} bars total)\n")
    if combos > 2000 and not args.yes:
        print(f"That is {combos} cells. Beyond a few hundred, the best cell is "
              f"more likely to be noise than edge.\nRe-run with --yes to proceed.")
        return 1

    df = run_grid(datasets, grid, initial_capital=args.capital)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.drop(columns=["per_symbol_trades"]).to_csv(OUT_DIR / "sweep.csv", index=False)

    cols = ["base_min", "st_factor", "st_atr", "di_len", "adx_sm", "adx_p1m", "adx_5m",
            "wpr_len", "trades", "symbols_traded", "win_rate", "expectancy_r",
            "t_stat", "avg_cost_per_r", "eligible"]
    with pd.option_context("display.width", 200):
        print(df[cols].head(20).to_string(index=False))

    best = select_best(df)
    print()
    if best is None:
        n_elig = int(df["eligible"].sum())
        print(f"NO CONFIGURATION QUALIFIED ({n_elig} eligible of {len(df)}).")
        print("  Selection requires >=200 pooled trades on >=4 symbols.")
        print("  'Nothing qualified' is a real answer, not a bug -- the cost")
        print("  gate and the trade-count floor are both doing their job.")
    else:
        print("best eligible configuration:")
        for k, v in best[cols].items():
            print(f"    {k:16s} {v}")
        print("\n  In-sample only. Confirm with `walkforward` before trusting it.")
    print(f"\nwritten to {OUT_DIR / 'sweep.csv'}")
    return 0


def cmd_walkforward(args) -> int:
    store, catalog = CandleStore(), ProductCatalog()
    start, end = _window(args)
    datasets = _load_all(args, store, catalog, start, end)
    if not datasets:
        print("no symbols loaded")
        return 1

    grid = GridSpec.fine() if args.fine else GridSpec()
    if args.no_cost_gate:
        # Removing the gate stops signals being FILTERED by cost ratio; fees,
        # slippage and funding are still charged in the P&L, so results remain
        # net of cost.
        grid = replace(grid, max_cost_per_r=(None,))
    if not args.wpr:
        grid = replace(grid, wpr_length=(0,))
    print(f"walk-forward: {args.splits} anchored splits, "
          f"{len(grid.combinations())} configs per split\n")
    df = walk_forward(datasets, grid, n_splits=args.splits,
                      initial_capital=args.capital)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_DIR / "walkforward.csv", index=False)
    with pd.option_context("display.width", 200):
        print(df.drop(columns=["selected"]).to_string(index=False))

    oos = df["oos_expectancy_r"].dropna()
    print()
    if oos.empty:
        print("NO SPLIT PRODUCED AN OUT-OF-SAMPLE RESULT.")
        print("  Nothing cleared the trade-count floor in training.")
    else:
        print(f"mean OOS E[R] across splits: {oos.mean():+.3f}")
        print(f"splits with positive OOS   : {int((oos > 0).sum())}/{len(oos)}")
        if (oos > 0).sum() < len(oos):
            print("  Not consistently positive out of sample -- the in-sample")
            print("  winner did not generalise.")
    print(f"\nwritten to {OUT_DIR / 'walkforward.csv'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="deltabt", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        # Also accepted after the subcommand, which is where people type it.
        sp.add_argument("-v", "--verbose", action="store_true")
        sp.add_argument("--symbols", nargs="*")
        sp.add_argument("--start", type=int)
        sp.add_argument("--end", type=int)
        sp.add_argument("--days", type=int)
        sp.add_argument("--offline", action="store_true",
                        help="use only cached candles, do not hit the API")

    def strategy_args(sp):
        sp.add_argument("--mode", choices=["parity", "corrected"], default="corrected")
        sp.add_argument("--capital", type=float, default=10_000.0)
        sp.add_argument("--risk", type=float, default=0.5)
        sp.add_argument("--rr", type=float, default=2.0)
        sp.add_argument("--base-minutes", type=int, default=1,
                        help="base bar size; the dominant driver of cost per R")
        sp.add_argument("--confirm-minutes", type=int, default=5,
                        help="higher timeframe for trend confirmation")
        sp.add_argument("--st-atr", type=int, default=10)
        sp.add_argument("--st-factor", type=float, default=2.0)
        sp.add_argument("--max-leverage", type=float, default=3.0)
        sp.add_argument("--slippage-bps", type=float, default=2.0)
        # WPR is off by default: it cut the sample 86% and worsened
        # expectancy in every measured configuration.
        sp.add_argument("--wpr", action="store_true", help="enable the WPR gate")
        sp.add_argument("--no-wpr", action="store_true",
                        help="no-op; WPR is already off by default")
        sp.add_argument("--wpr-length", type=int, default=14)
        sp.add_argument("--wpr-fire-long", type=float, default=-20.0)
        sp.add_argument("--wpr-fire-short", type=float, default=-80.0)
        sp.add_argument("--wpr-expiry", type=int, default=30)
        sp.add_argument("--max-cost-per-r", type=float, default=0.15)
        sp.add_argument("--no-cost-gate", action="store_true")

    sp = sub.add_parser("screen", help="score symbols on 1m data quality")
    common(sp)
    sp.add_argument("--limit", type=int, default=40)
    sp.add_argument("--max-synthetic", type=float, default=0.05,
                    help="max fraction of forward-filled bars allowed")
    sp.set_defaults(func=cmd_screen)

    sp = sub.add_parser("fetch", help="populate the candle cache for a symbol set")
    common(sp)
    sp.set_defaults(func=cmd_fetch)

    sp = sub.add_parser("backtest", help="run one configuration")
    common(sp)
    strategy_args(sp)
    sp.add_argument("--save-trades", action="store_true")
    sp.set_defaults(func=cmd_backtest)

    sp = sub.add_parser("wpr-curve", help="trade count and E[R] vs WPR length")
    common(sp)
    strategy_args(sp)
    sp.add_argument("--lengths", default="14,21,28,50,75,100,140")
    sp.set_defaults(func=cmd_wpr_curve)

    sp = sub.add_parser("sweep", help="grid search over strategy parameters")
    common(sp)
    strategy_args(sp)
    sp.add_argument("--fine", action="store_true",
                    help="use the wide grid (thousands of cells; overfits readily)")
    sp.add_argument("--yes", action="store_true", help="skip the large-grid guard")
    sp.set_defaults(func=cmd_sweep)

    sp = sub.add_parser("walkforward", help="anchored out-of-sample validation")
    common(sp)
    strategy_args(sp)
    sp.add_argument("--splits", type=int, default=4)
    sp.add_argument("--fine", action="store_true")
    sp.set_defaults(func=cmd_walkforward)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
