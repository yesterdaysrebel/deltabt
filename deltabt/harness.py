"""Loading, resampling and single-cell execution for backtest sweeps.

The scripts under ``scripts/`` are thin CLIs over this module. Keeping the
machinery in the package means a sweep and a walk-forward run the identical
cell -- if ``run_cell`` lived in one script and were imported by the other,
"the same backtest" would be a property of an import path.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from deltabt import rulecore
from deltabt.catalog import CONFIRM_RATIO, build_spec
from deltabt.config import CACHE_DIR, StrategyParams
from deltabt.costs import SymbolCosts
from deltabt.data.quality import tradable_mask
from deltabt.engine import run_backtest
from deltabt.metrics import compute
from deltabt.strategy import (Signals, resample_complete,
                              resample_ohlcv, resample_tradable)

#: Primary bar sizes, spanning the range where this venue's cost law bites
#: (1m) through to where PROGRAM_SUMMARY says it stops binding (~1 day).
TIMEFRAMES = (1, 5, 15, 30, 60, 240)

#: ``max_hold_bars`` is scaled to this constant wall-clock span. The default
#: 240 BARS is 4 hours at 1m and 40 days at 240m, so an unscaled grid would
#: compare a scalp against a swing trade and call the difference "timeframe".
HOLD_HOURS = 48


def params_for(spec, minutes: int) -> StrategyParams:
    """Execution settings for a cell. Not part of the strategy definition."""
    return StrategyParams(
        base_minutes=minutes,
        confirm_minutes=max(minutes * CONFIRM_RATIO, minutes + 1),
        max_hold_bars=max(20, (HOLD_HOURS * 60) // minutes),
        reward_risk=spec.target_r,
    )


def slice_signals(sig: Signals, mask: np.ndarray) -> Signals:
    """Restrict every per-bar array to ``mask``.

    Safe because ``rulecore`` is causal -- bar t reads only bars <= t, asserted
    for every family in tests/test_rulecore_invariance.py -- so a signal
    computed over full history equals one computed up to that bar. Slicing
    after the fact therefore restricts the BACKTEST window without leaking
    anything into the signal.
    """
    return Signals(**{
        f: (v[mask] if isinstance(v, np.ndarray) and v.shape[:1] == mask.shape else v)
        for f, v in vars(sig).items()
    })


def load_symbol(symbol: str) -> dict | None:
    d = CACHE_DIR / symbol
    ltp_p, mark_p, fund_p = d / "ltp_1m.parquet", d / "mark_1m.parquet", d / "funding_1h.parquet"
    if not ltp_p.exists():
        return None
    ltp = pd.read_parquet(ltp_p).sort_values("time").reset_index(drop=True)
    mark = pd.read_parquet(mark_p).sort_values("time").reset_index(drop=True) if mark_p.exists() else None
    fund = pd.read_parquet(fund_p) if fund_p.exists() else pd.DataFrame()
    return dict(symbol=symbol, ltp=ltp, mark=mark, funding=fund,
                tradable=tradable_mask(ltp))


def _resampled(data: dict, minutes: int, cache: dict) -> tuple:
    """Primary frames at ``minutes``, with the partial trailing bar dropped."""
    key = (data["symbol"], minutes)
    if key in cache:
        return cache[key]
    if minutes == 1:
        px = data["ltp"].reset_index(drop=True)
        mk = data["mark"]
        tr = data["tradable"]
    else:
        # Buckets missing too many of their minutes are dropped, INCLUDING the
        # partial trailing one. The live evaluator applies the identical rule,
        # so the two see the same bars; they diverged once and the stop differed
        # on 4.8% of 240m bars.
        px = resample_complete(data["ltp"], minutes)
        mk = resample_ohlcv(data["mark"], minutes) if data["mark"] is not None else None
        tr = resample_tradable(data["ltp"], data["tradable"], minutes)[: len(px)]
    cache[key] = (px, mk, tr)
    return cache[key]


def run_cell(data: dict, family: str, minutes: int, costs: SymbolCosts,
             cache: dict) -> dict:
    spec = build_spec(family, minutes)
    primary, mark, tradable = _resampled(data, minutes, cache)
    confirm, _, _ = (_resampled(data, spec.confirm_minutes, cache)
                     if spec.confirm.enabled else (None, None, None))

    row = dict(symbol=data["symbol"], family=family, timeframe_min=minutes,
               confirm_min=spec.confirm_minutes, spec_hash=spec.config_hash[:12],
               bars=len(primary))
    if len(primary) < spec.warmup_bars * 3:
        return {**row, "status": "too_short"}

    sig = rulecore.compute(primary, confirm, spec)
    row["signals"] = int(sig.long_entry.sum() + sig.short_entry.sum())

    params = StrategyParams(
        base_minutes=minutes,
        confirm_minutes=max(minutes * CONFIRM_RATIO, minutes + 1),
        max_hold_bars=max(20, (HOLD_HOURS * 60) // minutes),
        reward_risk=spec.target_r,
    )
    res = run_backtest(primary, mark, data["funding"],
                       rulecore.to_engine_signals(sig), params, costs,
                       tradable=tradable)
    m = compute(res)

    df = res.to_frame()
    gross = float((df["r_multiple"] + df["cost_per_r"]).mean()) if len(df) else float("nan")
    wins = df[df["pnl"] > 0]["pnl"] if len(df) else pd.Series(dtype=float)
    losses = df[df["pnl"] <= 0]["pnl"] if len(df) else pd.Series(dtype=float)
    days = len(primary) * minutes / (60 * 24)
    return {
        **row, "status": "ok",
        # --- counts -------------------------------------------------------
        "trades": m.trades,
        "trades_per_day": m.trades / days if days else float("nan"),
        "days": days,
        "rej_cost": res.rejects.get("cost_per_r", 0),
        "rej_cooldown": res.rejects.get("cooldown", 0),
        # --- P&L in money, on `initial_capital` at `risk_percent` per trade -
        "initial_capital": res.initial_capital,
        "total_pnl": m.total_pnl,
        "final_equity": res.final_equity,
        "return_pct": m.return_pct,
        "max_dd_pct": m.max_drawdown_pct,
        "total_fees": m.total_fees,
        "total_funding": m.total_funding,
        # Same trades, same-bar conflicts resolved target-first instead of
        # stop-first. The truth lies between this and total_pnl.
        "optimistic_pnl": res.optimistic_pnl,
        "gross_win": float(wins.sum()), "gross_loss": float(losses.sum()),
        "avg_win": float(wins.mean()) if len(wins) else float("nan"),
        "avg_loss": float(losses.mean()) if len(losses) else float("nan"),
        "best_trade": float(df["pnl"].max()) if len(df) else float("nan"),
        "worst_trade": float(df["pnl"].min()) if len(df) else float("nan"),
        "profit_factor": m.profit_factor,
        # --- per-trade normalised ------------------------------------------
        "win_rate": m.win_rate,
        "net_r": m.expectancy_r, "net_r_lo": m.expectancy_r_lo,
        "net_r_hi": m.expectancy_r_hi, "gross_r": gross,
        "cost_r": m.avg_cost_per_r,
        "bars_held": m.avg_bars_held, "median_r_bps": m.median_r_bps,
        "avg_leverage": m.avg_leverage, "ambiguous_pct": m.ambiguous_pct,
    }
