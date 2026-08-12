"""Performance metrics with honest uncertainty.

Trade count leads every summary because it decides whether anything else means
anything. At a 2R target and a 40% win rate, N=216 is the minimum for a
two-sigma result and roughly 486 for a swept one; below ~50 trades the 95%
interval on E[R] is about +/-0.42R, wider than any edge worth trading.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

from deltabt.engine import BacktestResult

#: Below this, results are labelled uninterpretable rather than reported as if
#: they meant something.
MIN_MEANINGFUL_TRADES = 200


@dataclass
class Metrics:
    symbol: str
    mode: str
    trades: int
    interpretable: bool
    win_rate: float
    expectancy_r: float
    expectancy_r_lo: float
    expectancy_r_hi: float
    total_pnl: float
    return_pct: float
    max_drawdown_pct: float
    profit_factor: float
    avg_cost_per_r: float
    median_r_bps: float
    total_fees: float
    total_funding: float
    ambiguous_pct: float
    avg_leverage: float
    max_leverage: float
    avg_bars_held: float
    optimistic_pnl: float
    rejects: dict

    def as_dict(self) -> dict:
        return asdict(self)


def _bootstrap_ci(
    values: np.ndarray, *, n_boot: int = 2000, alpha: float = 0.05, seed: int = 0
) -> tuple[float, float]:
    """Percentile bootstrap CI for the mean."""
    if values.size == 0:
        return (float("nan"), float("nan"))
    if values.size == 1:
        return (float(values[0]), float(values[0]))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, values.size, size=(n_boot, values.size))
    means = values[idx].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def max_drawdown(equity: np.ndarray) -> float:
    """Peak-to-trough decline as a fraction of the running peak."""
    if equity is None or equity.size == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, (peak - equity) / peak, 0.0)
    return float(np.nanmax(dd))


def compute(result: BacktestResult) -> Metrics:
    df = result.to_frame()
    n = len(df)

    if n == 0:
        return Metrics(
            symbol=result.symbol, mode=result.mode, trades=0, interpretable=False,
            win_rate=float("nan"), expectancy_r=float("nan"),
            expectancy_r_lo=float("nan"), expectancy_r_hi=float("nan"),
            total_pnl=0.0, return_pct=0.0,
            max_drawdown_pct=max_drawdown(result.equity_curve) * 100.0,
            profit_factor=float("nan"), avg_cost_per_r=float("nan"),
            median_r_bps=float("nan"), total_fees=0.0, total_funding=0.0,
            ambiguous_pct=float("nan"), avg_leverage=0.0, max_leverage=0.0,
            avg_bars_held=0.0, optimistic_pnl=result.optimistic_pnl,
            rejects=dict(result.rejects),
        )

    r = df["r_multiple"].to_numpy(dtype="float64")
    pnl = df["pnl"].to_numpy(dtype="float64")
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    lo, hi = _bootstrap_ci(r)

    # R expressed in basis points of entry price -- the number that decides
    # whether costs are survivable, independent of position size.
    r_bps = (
        df["risk_per_unit"].to_numpy(dtype="float64")
        / df["entry_price"].to_numpy(dtype="float64")
        * 10_000.0
    )

    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())

    return Metrics(
        symbol=result.symbol,
        mode=result.mode,
        trades=n,
        interpretable=n >= MIN_MEANINGFUL_TRADES,
        win_rate=float((pnl > 0).mean()),
        expectancy_r=float(r.mean()),
        expectancy_r_lo=lo,
        expectancy_r_hi=hi,
        total_pnl=float(pnl.sum()),
        return_pct=float(pnl.sum() / result.initial_capital * 100.0),
        max_drawdown_pct=max_drawdown(result.equity_curve) * 100.0,
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        avg_cost_per_r=float(df["cost_per_r"].mean()),
        median_r_bps=float(np.median(r_bps)),
        total_fees=float(df["fees"].sum()),
        total_funding=float(df["funding"].sum()),
        ambiguous_pct=float(df["ambiguous"].mean() * 100.0),
        avg_leverage=float(df["leverage"].mean()),
        max_leverage=float(df["leverage"].max()),
        avg_bars_held=float(df["bars_held"].mean()),
        optimistic_pnl=result.optimistic_pnl,
        rejects=dict(result.rejects),
    )


def format_summary(m: Metrics) -> str:
    """Human-readable block, trade count first."""
    lines = [
        f"  {m.symbol} [{m.mode}]",
        f"    trades           {m.trades}"
        + ("" if m.interpretable else f"   <-- under {MIN_MEANINGFUL_TRADES}, NOT interpretable"),
    ]
    if m.trades == 0:
        rej = ", ".join(f"{k}={v}" for k, v in m.rejects.items() if v)
        lines.append(f"    rejects          {rej or 'none'}")
        return "\n".join(lines)

    lines += [
        f"    win rate         {m.win_rate:6.1%}",
        f"    E[R]             {m.expectancy_r:+6.3f}   95% CI [{m.expectancy_r_lo:+.3f}, {m.expectancy_r_hi:+.3f}]",
        f"    net PnL          {m.total_pnl:+12,.2f}  ({m.return_pct:+.2f}%)",
        f"    max drawdown     {m.max_drawdown_pct:6.2f}%",
        f"    profit factor    {m.profit_factor:6.2f}",
        f"    cost / R         {m.avg_cost_per_r:6.3f}   (median R = {m.median_r_bps:.1f} bps)",
        f"    fees / funding   {m.total_fees:,.2f} / {m.total_funding:,.2f}",
        f"    leverage         avg {m.avg_leverage:.2f}x  max {m.max_leverage:.2f}x",
        f"    bars held        {m.avg_bars_held:.1f}",
        f"    same-bar ambig.  {m.ambiguous_pct:.1f}%  (optimistic PnL {m.optimistic_pnl:+,.2f})",
    ]
    rej = ", ".join(f"{k}={v}" for k, v in m.rejects.items() if v)
    if rej:
        lines.append(f"    rejects          {rej}")
    return "\n".join(lines)
