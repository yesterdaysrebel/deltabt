"""Portfolio backtests with the live risk gates on, reported in money.

    PYTHONPATH=. python3 -u scripts/gated_backtest.py

ONE ACCOUNT, ONE POSITION SLOT, ALL SYMBOLS COMPETING
    ``scripts/backtest_sweep.py`` runs each symbol on its own $10,000 account,
    which lets N symbols hold N concurrent positions. Production holds ONE.
    ``app/config/variants.py`` records what that difference was worth once:
    "turned a t of +8.30 into a negative result". This script is the corrected
    version, and its P&L is the only P&L in this repository that describes a
    system somebody could actually run.

WHAT A GATE DOES AND DOES NOT DO
    Gates bound drawdown. They do not improve expectancy, and they bias any
    measurement of it upward, because a daily loss limit conditions the sample
    on the day not already having gone badly. Both gated and ungated runs are
    reported for that reason -- the ungated column is the measurement, the
    gated column is the account curve.
"""

from __future__ import annotations

import argparse
import logging
import time

import numpy as np
import pandas as pd

from deltabt import rulecore
from deltabt.catalog import FAMILIES, build_spec
from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.harness import _resampled, load_symbol, params_for
from deltabt.metrics import compute
from deltabt.portfolio import Book, RiskGates, run_portfolio

log = logging.getLogger("gated")
OUT = OUT_DIR / "sweep"

#: Gate configurations compared side by side.
GATE_SETS = {
    "ungated": RiskGates.off(),
    "live_defaults": RiskGates(),                       # 20/day, 10% DD, 3 losses
    # WHAT THE BOT ACTUALLY RUNS, which "live_defaults" is not. That entry is
    # RiskGates(), whose defaults are max_open_positions=1 and
    # max_daily_loss_pct=1.0 (disabled). infra/terraform/variables.tf and
    # deploy/aws/run.sh set DELTABOT_MAX_OPEN=6 and DELTABOT_MAX_DAILY_LOSS
    # =0.02, so the difference is six concurrent slots instead of one and a
    # daily-loss limit that exists. Six slots is the bigger of the two: with
    # one slot the symbols queue for it and most signals are simply never
    # taken, which understates trade count several-fold.
    "production": RiskGates(max_open_positions=6, max_trades_per_day=20,
                            max_daily_loss_pct=0.02, max_drawdown_pct=0.10,
                            max_consecutive_losses=3),
    "two_per_day": RiskGates(max_trades_per_day=2, max_daily_loss_pct=0.02,
                             max_drawdown_pct=0.10, max_consecutive_losses=3),
    "tight": RiskGates(max_trades_per_day=2, max_daily_loss_pct=0.01,
                       max_drawdown_pct=0.05, max_consecutive_losses=2),
    # The same gates, but modelling an operator who restarts the bot a week
    # after a drawdown halt. The gap between this and `tight` is how much of
    # `tight`'s apparent improvement was the account simply stopping.
    "tight_resume_7d": RiskGates(max_trades_per_day=2, max_daily_loss_pct=0.01,
                                 max_drawdown_pct=0.05, max_consecutive_losses=2,
                                 resume_after_days=7),
}


def build_books(symbols, family, minutes, cache, confirm_minutes: int | None = None,
                stop_mult: float | None = None,
                target_r: float | None = None,
                hold_hours: int | None = None):
    books, funding = {}, {}
    catalog = ProductCatalog()
    spec = build_spec(family, minutes, confirm_minutes, stop_mult, target_r)
    for sym in symbols:
        data = cache.get(sym) or load_symbol(sym)
        if data is None:
            continue
        cache[sym] = data
        rs = cache.setdefault(f"_rs_{sym}", {})
        primary, mark, tradable = _resampled(data, minutes, rs)
        if len(primary) < spec.warmup_bars * 3:
            continue
        confirm, _, _ = (_resampled(data, spec.confirm_minutes, rs)
                         if spec.confirm.enabled else (None, None, None))
        sig = rulecore.to_engine_signals(rulecore.compute(primary, confirm, spec))
        try:
            costs = SymbolCosts.from_spec(catalog.get(sym))
        except (KeyError, LookupError):
            continue
        books[sym] = Book(symbol=sym, bars=primary, signals=sig, costs=costs,
                          mark=mark, tradable=tradable)
        funding[sym] = data["funding"]
    return spec, books, funding


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="*",
                    default=["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"])
    ap.add_argument("--families", nargs="*", default=list(FAMILIES))
    ap.add_argument("--timeframes", nargs="*", type=int, default=[15, 30, 60, 240])
    ap.add_argument("--capital", type=float, default=10_000.0)
    # The wide-stop candidate needs all four of these; the defaults reproduce
    # every previously recorded run unchanged.
    ap.add_argument("--confirm", type=int, default=None)
    ap.add_argument("--stop-mult", type=float, default=None)
    ap.add_argument("--target-r", type=float, default=None)
    ap.add_argument("--hold-hours", type=int, default=None)
    args = ap.parse_args()

    cache: dict = {}
    rows = []
    t0 = time.time()
    total = len(args.families) * len(args.timeframes) * len(GATE_SETS)
    done = 0

    for minutes in args.timeframes:
        for family in args.families:
            spec, books, funding = build_books(
                args.symbols, family, minutes, cache,
                args.confirm, args.stop_mult, args.target_r, args.hold_hours)
            if not books:
                continue
            params = params_for(spec, minutes, args.hold_hours)
            for gname, gates in GATE_SETS.items():
                res = run_portfolio(books, params, gates,
                                    initial_capital=args.capital, funding=funding)
                m = compute(res)
                df = res.to_frame()
                days = ((res.equity_time[-1] - res.equity_time[0]) / 86_400
                        if res.equity_time is not None and len(res.equity_time) else np.nan)
                # HOW MUCH OF THE WINDOW THE ACCOUNT ACTUALLY TRADED.
                # max_drawdown_pct compares equity to its PEAK and equity only
                # moves when a trade closes, so once the limit is reached with
                # no position open, every entry is refused, nothing can close,
                # and the drawdown can never recover. The halt is permanent --
                # the same failure app/risk/engine.py documents and fixes for
                # max_consecutive_losses, and does NOT fix for drawdown. A run
                # that "lost less" may simply have stopped early, so this
                # column has to sit next to every P&L figure.
                t_start = int(res.equity_time[0]) if len(res.equity_time) else 0
                t_end = int(res.equity_time[-1]) if len(res.equity_time) else 0
                last_exit = max((t.exit_time for t in res.trades), default=t_start)
                window_used = (100.0 * (last_exit - t_start) / (t_end - t_start)
                               if t_end > t_start else np.nan)
                rows.append({
                    "family": family, "timeframe_min": minutes, "gates": gname,
                    "symbols": len(books), "trades": m.trades,
                    "window_used_pct": window_used,
                    "halts": len(getattr(res, "halts", [])),
                    "trades_per_day": m.trades / days if days else np.nan,
                    "total_pnl": m.total_pnl, "final_equity": res.final_equity,
                    "return_pct": m.return_pct, "max_dd_pct": m.max_drawdown_pct,
                    "win_rate": m.win_rate, "net_r": m.expectancy_r,
                    "net_r_lo": m.expectancy_r_lo, "net_r_hi": m.expectancy_r_hi,
                    "profit_factor": m.profit_factor, "fees": m.total_fees,
                    "funding_paid": m.total_funding,
                    "gross_r": float((df["r_multiple"] + df["cost_per_r"]).mean())
                    if len(df) else np.nan,
                    **{f"rej_{k}": v for k, v in res.rejects.items()},
                })
                done += 1
            log.info("%s @%dm  (%d/%d, %.0fs)", family, minutes, done, total,
                     time.time() - t0)
        # resampled frames for this timeframe are no longer needed
        for k in [k for k in cache if str(k).startswith("_rs_")]:
            cache[k].clear()

    out = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT / "gated.csv", index=False)

    pd.set_option("display.width", 240)
    pd.set_option("display.float_format", lambda v: f"{v:,.2f}")
    print("\n" + "=" * 110)
    print(f"PORTFOLIO BACKTESTS  --  one ${args.capital:,.0f} account, one position "
          f"slot, {len(args.symbols)} symbols competing")
    print("=" * 110)

    print("\n=== BY GATE SET (all families and timeframes pooled) ===")
    g = out.groupby("gates").agg(
        runs=("trades", "size"), trades=("trades", "sum"),
        total_pnl=("total_pnl", "sum"), mean_return_pct=("return_pct", "mean"),
        mean_max_dd=("max_dd_pct", "mean"), worst_dd=("max_dd_pct", "max"),
        worst_return=("return_pct", "min"),
        window_used_pct=("window_used_pct", "mean"),
        halts=("halts", "sum"),
        halted_early=("window_used_pct", lambda s: int((s < 90).sum())),
        profitable=("total_pnl", lambda s: int((s > 0).sum())))
    print(g.reindex(list(GATE_SETS)).to_string())
    print("\n`window_used_pct` is how much of the data the account was still")
    print("trading through. A run below ~90% hit the drawdown halt and stopped:")
    print("its P&L is a truncated sample, not a risk-managed outcome.")

    print("\n\n=== P&L BY FAMILY x GATE SET ===")
    print(out.pivot_table(index=["family", "timeframe_min"], columns="gates",
                          values="total_pnl")
          .reindex(columns=list(GATE_SETS)).to_string())

    print("\n\n=== MAX DRAWDOWN % BY FAMILY x GATE SET ===")
    print(out.pivot_table(index=["family", "timeframe_min"], columns="gates",
                          values="max_dd_pct")
          .reindex(columns=list(GATE_SETS)).to_string())

    print("\n\n=== TOP 15 RUNS BY P&L ===")
    cols = ["family", "timeframe_min", "gates", "trades", "trades_per_day",
            "win_rate", "total_pnl", "return_pct", "max_dd_pct", "window_used_pct",
            "profit_factor", "gross_r", "net_r"]
    print(out.nlargest(15, "total_pnl")[cols].to_string(index=False))

    print("\n\n=== EXPECTANCY: GATED vs UNGATED (the censoring check) ===")
    piv = out.pivot_table(index=["family", "timeframe_min"], columns="gates",
                          values="net_r").reindex(columns=list(GATE_SETS))
    piv["gated - ungated"] = piv["two_per_day"] - piv["ungated"]
    print(piv.to_string())
    d = piv["gated - ungated"].dropna()
    if len(d):
        print(f"\nmean shift in per-trade expectancy from gating: {d.mean():+.4f} R")
        print("A positive shift is the censoring effect, not an improvement:")
        print("gating removes trades taken after the day already went badly.")

    print(f"\nwritten to {OUT / 'gated.csv'}")


if __name__ == "__main__":
    main()
