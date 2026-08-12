"""Wiring between the data layer, strategy, engine, and metrics."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from deltabt.config import (
    MAX_SYNTHETIC_RATIO,
    MIN_USABLE_DAYS,
    RESOLUTION_1M,
    StrategyParams,
)
from deltabt.costs import SymbolCosts
from deltabt.data.quality import assess, first_dense_bar, tradable_mask
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.engine import BacktestResult, run_backtest
from deltabt.metrics import Metrics, compute
from deltabt.strategy import build_signals, resample_ohlcv, resample_tradable

log = logging.getLogger(__name__)


@dataclass
class SymbolData:
    """Everything one symbol needs, already screened and aligned."""

    symbol: str
    ltp: pd.DataFrame
    mark: pd.DataFrame
    funding: pd.DataFrame
    tradable: np.ndarray
    costs: SymbolCosts


def load_symbol(
    symbol: str,
    start: int,
    end: int,
    *,
    store: CandleStore,
    catalog: ProductCatalog,
    slippage_bps: float = 2.0,
    refresh: bool = True,
    trim_to_dense: bool = True,
) -> SymbolData:
    """Fetch, cache, and screen one symbol."""
    series = store.load_all_series(symbol, RESOLUTION_1M, start, end, refresh=refresh)
    ltp = series["ltp"]
    if ltp.empty:
        raise RuntimeError(f"no 1m candles returned for {symbol} in the requested window")

    if trim_to_dense:
        dense = first_dense_bar(ltp)
        if dense is not None and dense > int(ltp["time"].iloc[0]):
            log.info(
                "%s: trimming sparse head, starting at %s",
                symbol,
                pd.Timestamp(dense, unit="s", tz="UTC").date(),
            )
            ltp = ltp.loc[ltp["time"] >= dense].reset_index(drop=True)

    spec = catalog.get(symbol)
    return SymbolData(
        symbol=symbol,
        ltp=ltp,
        mark=series["mark"],
        funding=series["funding"],
        tradable=tradable_mask(ltp),
        costs=SymbolCosts.from_spec(spec, slippage_bps=slippage_bps),
    )


def backtest_symbol(
    data: SymbolData,
    params: StrategyParams,
    *,
    initial_capital: float = 10_000.0,
) -> tuple[BacktestResult, Metrics]:
    """Resample to the configured base timeframe, then run.

    Candles are always stored at 1m, so changing ``base_minutes`` costs a
    groupby rather than a refetch. The mark series is resampled to match --
    stops must be tested against mark extremes over the same interval the
    position was actually exposed for.
    """
    params.validate()
    if params.base_minutes > 1:
        ltp = resample_ohlcv(data.ltp, params.base_minutes)
        mark = resample_ohlcv(data.mark, params.base_minutes) if not data.mark.empty else data.mark
        tradable = resample_tradable(data.ltp, data.tradable, params.base_minutes)
    else:
        ltp, mark, tradable = data.ltp, data.mark, data.tradable

    signals = build_signals(ltp, params)
    result = run_backtest(
        ltp,
        mark,
        data.funding,
        signals,
        params,
        data.costs,
        tradable=tradable,
        initial_capital=initial_capital,
    )
    return result, compute(result)


def screen_universe(
    symbols: list[str],
    start: int,
    end: int,
    *,
    store: CandleStore,
    catalog: ProductCatalog,
    refresh: bool = True,
    max_synthetic: float = MAX_SYNTHETIC_RATIO,
) -> pd.DataFrame:
    """Score candidate symbols on 1m data quality.

    Density and history depth are measured separately on purpose. The
    synthetic-bar ratio is a stable property of a symbol's liquidity and is
    well estimated from a recent window, so fetching years of history for a
    symbol that is 50% forward-filled would be wasted requests. Depth comes
    from the product record's ``launch_time`` instead.
    """
    catalog_all = catalog.all()
    now = int(pd.Timestamp.utcnow().timestamp())
    sampled_days = max((end - start) / 86400.0, 1e-9)
    rows = []

    for sym in symbols:
        try:
            df = store.load(sym, "ltp", RESOLUTION_1M, start, end, refresh=refresh)
        except Exception as exc:  # a dead symbol should not abort the screen
            log.warning("%s: fetch failed (%s)", sym, exc)
            rows.append(
                {"symbol": sym, "bars": 0, "synthetic_ratio": 1.0, "passes": False,
                 "reason": f"fetch failed: {exc}"}
            )
            continue

        q = assess(sym, df).as_dict()

        spec = catalog_all.get(sym) or {}
        launch = spec.get("launch_time")
        age_days = float("nan")
        if launch:
            try:
                age_days = (now - int(pd.Timestamp(launch).timestamp())) / 86400.0
            except (ValueError, TypeError):
                pass
        q["age_days"] = age_days

        # Fraction of the sampled window over which the symbol was
        # continuously liquid. A symbol that only became dense partway through
        # is not reliably tradable even if its recent density looks fine.
        coverage = q["usable_days"] / sampled_days if q["usable_days"] else 0.0
        q["coverage"] = coverage

        # Rebuild the verdict: density from the sampled window, depth from age.
        reasons = []
        if q["synthetic_ratio"] > max_synthetic:
            reasons.append(f"synthetic {q['synthetic_ratio']:.1%} > {max_synthetic:.0%}")
        if q["usable_start"] is None:
            reasons.append("never reaches continuous liquidity")
        elif coverage < 0.9:
            reasons.append(f"only liquid for {coverage:.0%} of the window")
        if pd.notna(age_days) and age_days < MIN_USABLE_DAYS:
            reasons.append(f"listed {age_days:.0f}d ago < {MIN_USABLE_DAYS}d")
        q["passes"] = not reasons
        q["reason"] = "; ".join(reasons) if reasons else "ok"
        rows.append(q)

    out = pd.DataFrame(rows)
    if "synthetic_ratio" in out:
        out = out.sort_values(["passes", "synthetic_ratio"], ascending=[False, True])
    return out.reset_index(drop=True)


def rank_by_turnover(catalog: ProductCatalog, limit: int = 40) -> list[str]:
    """Candidate symbols, most liquid first.

    Turnover is only a candidate filter -- it does not imply the 1m series is
    usable. DOGEUSD and LINKUSD are top-30 by turnover and 43%/35% synthetic.
    """
    tickers = catalog.client.tickers()
    rows = []
    for t in tickers:
        try:
            turnover = float(t.get("turnover_usd") or 0.0)
        except (TypeError, ValueError):
            turnover = 0.0
        rows.append((turnover, t.get("symbol")))
    rows.sort(reverse=True)
    return [s for _, s in rows[:limit] if s]
