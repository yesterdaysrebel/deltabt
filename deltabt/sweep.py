"""Parameter sweep and walk-forward validation.

Two guardrails are built in rather than left to the analyst:

1. **WPR OFF is always a candidate and is allowed to win.** On simulated data
   the no-WPR baseline out-earned every WPR variant on net R/day by roughly
   10x. A grid that cannot express "drop this indicator" will always
   rediscover a reason to keep it.
2. **Configurations below a trade-count floor are excluded from selection**,
   not merely flagged. A cell with 32 trades and a spectacular E[R] is the
   single most likely thing to win a grid search and the least likely to
   survive contact with reality -- across ~37 cells the expected maximum
   t-statistic under the null is already ~2.5-2.8.

The grid deliberately searches stop width and base timeframe, not just
oscillator settings, because cost per R is the binding constraint and only
those dimensions move it.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from deltabt.config import StrategyParams, WprLatch
from deltabt.metrics import MIN_MEANINGFUL_TRADES
from deltabt.runner import SymbolData, backtest_symbol

log = logging.getLogger(__name__)


def _mirror_fire(fire_long: float) -> float:
    """Reflect a long fire level about the midpoint of the WPR range.

    WPR is bounded [-100, 0], so -20 mirrors to -80 and -50 to itself. Keeping
    the two sides symmetric matters: the panel verified the bands are
    genuinely symmetric in trigger probability, so an asymmetric grid would
    introduce a directional bias that looks like edge.
    """
    return -100.0 - fire_long


@dataclass(frozen=True)
class GridSpec:
    """Sweep axes.

    The default is deliberately coarse (~400 cells, not ~16,000). That is a
    statistical choice as much as a compute one: with ~37 cells the expected
    maximum t-statistic under the null is already 2.5-2.8, so a grid two
    orders of magnitude larger would all but guarantee a spurious winner. Use
    :meth:`fine` only to refine a neighbourhood that a coarse pass and
    walk-forward have already justified.
    """

    #: Base timeframe, paired with its confirmation timeframe. This is the
    #: dominant axis: median R is 12 bps at 1m but 138 bps at 1h on BTCUSD,
    #: against a fixed ~16 bps round trip. No indicator setting compensates.
    timeframes: tuple[tuple[int, int], ...] = ((1, 5), (5, 15), (15, 60), (60, 240))
    #: Stop width is a first-class axis because cost per R is the binding
    #: constraint, and this is the other knob that moves it.
    st_factor: tuple[float, ...] = (1.5, 2.5, 3.5)
    st_atr_period: tuple[int, ...] = (10, 21)
    di_length: tuple[int, ...] = (14, 28)
    #: Single value: the review's finding was that 28 is over-smoothed (~40
    #: bars of lag) and 14 is the sane default. Sweeping it too would multiply
    #: the grid for a parameter with a clear prior.
    adx_smoothing: tuple[int, ...] = (14,)
    adx_percentile_1m: tuple[float, ...] = (0.50, 0.80)
    #: None disables the 5m ADX gate entirely -- it rejects only ~4% of
    #: already-passing bars at a shared threshold.
    adx_percentile_5m: tuple[float | None, ...] = (None, 0.75)
    #: 0 means WPR off, and it must stay in the candidate set: on simulated
    #: data no WPR variant beat simply not using it. Length 140 is excluded
    #: because every 140 cell is sample-starved by construction and cannot be
    #: compared fairly against cells yielding thousands of trades.
    wpr_length: tuple[int, ...] = (0, 14, 28)
    wpr_fire_long: tuple[float, ...] = (-20.0,)
    wpr_expiry: tuple[int, ...] = (30,)
    reward_risk: tuple[float, ...] = (2.0,)
    max_cost_per_r: tuple[float | None, ...] = (0.15,)

    @classmethod
    def fine(cls) -> "GridSpec":
        """Wider search. Only justified after a coarse pass narrows the region."""
        return cls(
            st_factor=(1.5, 2.0, 2.5, 3.0, 3.5, 4.0),
            st_atr_period=(7, 14, 21),
            di_length=(14, 21, 28),
            adx_smoothing=(7, 14, 21),
            adx_percentile_1m=(0.50, 0.65, 0.80),
            wpr_length=(0, 14, 21, 28, 50),
            wpr_fire_long=(-50.0, -20.0),
            wpr_expiry=(30, 60),
        )

    def combinations(self) -> list[StrategyParams]:
        out: list[StrategyParams] = []
        seen: set[tuple] = set()

        axes = (
            self.timeframes,
            self.st_factor, self.st_atr_period, self.di_length,
            self.adx_smoothing, self.adx_percentile_1m, self.adx_percentile_5m,
            self.wpr_length, self.wpr_fire_long, self.wpr_expiry,
            self.reward_risk, self.max_cost_per_r,
        )
        for (tf, stf, stp, di, adxs, p1, p5, wl, wfl, wex, rr, cpr) in itertools.product(*axes):
            base_min, confirm_min = tf
            wpr_on = wl > 0
            # When WPR is off, its sub-parameters are meaningless; collapse
            # them so the grid does not silently test the same config 4 times
            # and inflate the multiple-comparison burden.
            key = (tf, stf, stp, di, adxs, p1, p5, wl, wfl if wpr_on else None,
                   wex if wpr_on else None, rr, cpr)
            if key in seen:
                continue
            seen.add(key)

            out.append(StrategyParams(
                mode="corrected",
                base_minutes=base_min,
                confirm_minutes=confirm_min,
                st_factor=stf,
                st_atr_period=stp,
                di_length=di,
                adx_smoothing=adxs,
                adx_percentile_1m=p1,
                use_5m_adx=p5 is not None,
                adx_percentile_5m=p5 if p5 is not None else 0.75,
                wpr=WprLatch(
                    enabled=wpr_on,
                    length=wl if wpr_on else 14,
                    fire_long=wfl,
                    fire_short=_mirror_fire(wfl),
                    expiry_bars=wex,
                ),
                reward_risk=rr,
                max_cost_per_r=cpr,
            ))
        return out


def describe(p: StrategyParams) -> dict:
    """Flat, sortable description of one configuration."""
    return {
        "base_min": p.base_minutes,
        "confirm_min": p.confirm_minutes,
        "st_factor": p.st_factor,
        "st_atr": p.st_atr_period,
        "di_len": p.di_length,
        "adx_sm": p.adx_smoothing,
        "adx_p1m": p.adx_percentile_1m,
        "adx_5m": p.adx_percentile_5m if p.use_5m_adx else None,
        "wpr_len": p.wpr.length if p.wpr.enabled else 0,
        "wpr_fire": p.wpr.fire_long if p.wpr.enabled else None,
        "wpr_exp": p.wpr.expiry_bars if p.wpr.enabled else None,
        "rr": p.reward_risk,
        "cost_gate": p.max_cost_per_r,
    }


def evaluate(
    datasets: dict[str, SymbolData],
    params: StrategyParams,
    *,
    initial_capital: float = 10_000.0,
    time_slice: tuple[int, int] | None = None,
) -> dict:
    """Aggregate one configuration across symbols.

    Aggregation is trade-weighted over the pooled trade list rather than a
    mean of per-symbol means, so a symbol that produced three trades cannot
    swing the result as hard as one that produced three hundred.
    """
    all_r: list[np.ndarray] = []
    total_pnl = 0.0
    per_symbol: dict[str, int] = {}
    costs: list[float] = []
    dds: list[float] = []

    for sym, data in datasets.items():
        d = data
        if time_slice is not None:
            lo, hi = time_slice
            mask = (data.ltp["time"] >= lo) & (data.ltp["time"] <= hi)
            if mask.sum() < params.warmup_bars + 100:
                continue
            d = SymbolData(
                symbol=data.symbol,
                ltp=data.ltp.loc[mask].reset_index(drop=True),
                mark=data.mark,
                funding=data.funding,
                tradable=data.tradable[mask.to_numpy()],
                costs=data.costs,
            )
        try:
            result, m = backtest_symbol(d, params, initial_capital=initial_capital)
        except Exception as exc:
            log.warning("%s: evaluation failed (%s)", sym, exc)
            continue

        per_symbol[sym] = m.trades
        total_pnl += m.total_pnl
        dds.append(m.max_drawdown_pct)
        if m.trades:
            all_r.append(result.to_frame()["r_multiple"].to_numpy(dtype="float64"))
            costs.append(m.avg_cost_per_r)

    pooled = np.concatenate(all_r) if all_r else np.zeros(0)
    n = int(pooled.size)
    # A configuration must clear the floor on the POOLED sample and trade on
    # at least four symbols -- an edge that only appears on one instrument is
    # not an edge.
    symbols_traded = sum(1 for v in per_symbol.values() if v > 0)

    return {
        **describe(params),
        "trades": n,
        "symbols_traded": symbols_traded,
        "win_rate": float((pooled > 0).mean()) if n else float("nan"),
        "expectancy_r": float(pooled.mean()) if n else float("nan"),
        "expectancy_se": float(pooled.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan"),
        "t_stat": (
            float(pooled.mean() / (pooled.std(ddof=1) / np.sqrt(n)))
            if n > 1 and pooled.std(ddof=1) > 0 else float("nan")
        ),
        "total_pnl": total_pnl,
        "avg_cost_per_r": float(np.mean(costs)) if costs else float("nan"),
        "max_dd_pct": float(np.max(dds)) if dds else 0.0,
        "per_symbol_trades": per_symbol,
        "eligible": n >= MIN_MEANINGFUL_TRADES and symbols_traded >= 4,
    }


def run_grid(
    datasets: dict[str, SymbolData],
    grid: GridSpec | None = None,
    *,
    time_slice: tuple[int, int] | None = None,
    initial_capital: float = 10_000.0,
    progress_every: int = 25,
) -> pd.DataFrame:
    """Evaluate every configuration in the grid."""
    grid = grid or GridSpec()
    combos = grid.combinations()
    log.info("evaluating %d configurations across %d symbols", len(combos), len(datasets))

    rows = []
    for i, p in enumerate(combos, 1):
        rows.append(evaluate(datasets, p, initial_capital=initial_capital,
                             time_slice=time_slice))
        if progress_every and i % progress_every == 0:
            log.info("  %d/%d", i, len(combos))

    df = pd.DataFrame(rows)
    return df.sort_values(
        ["eligible", "expectancy_r"], ascending=[False, False]
    ).reset_index(drop=True)


def select_best(df: pd.DataFrame) -> pd.Series | None:
    """Highest expectancy among configurations that cleared the floor.

    Returns None rather than falling back to the best ineligible row: "nothing
    qualified" is a real and important answer.
    """
    eligible = df.loc[df["eligible"]]
    if eligible.empty:
        return None
    return eligible.sort_values("expectancy_r", ascending=False).iloc[0]


def params_from_row(row: pd.Series) -> StrategyParams:
    """Rebuild a StrategyParams from a grid result row."""
    wpr_len = int(row["wpr_len"])
    on = wpr_len > 0
    fire_long = float(row["wpr_fire"]) if on and pd.notna(row["wpr_fire"]) else -20.0
    return StrategyParams(
        mode="corrected",
        base_minutes=int(row["base_min"]),
        confirm_minutes=int(row["confirm_min"]),
        st_factor=float(row["st_factor"]),
        st_atr_period=int(row["st_atr"]),
        di_length=int(row["di_len"]),
        adx_smoothing=int(row["adx_sm"]),
        adx_percentile_1m=float(row["adx_p1m"]),
        use_5m_adx=pd.notna(row["adx_5m"]),
        adx_percentile_5m=float(row["adx_5m"]) if pd.notna(row["adx_5m"]) else 0.75,
        wpr=WprLatch(
            enabled=on,
            length=wpr_len if on else 14,
            fire_long=fire_long,
            fire_short=-80.0 if fire_long == -20.0 else -50.0,
            expiry_bars=int(row["wpr_exp"]) if on and pd.notna(row["wpr_exp"]) else 30,
        ),
        reward_risk=float(row["rr"]),
        max_cost_per_r=float(row["cost_gate"]) if pd.notna(row["cost_gate"]) else None,
    )


def walk_forward(
    datasets: dict[str, SymbolData],
    grid: GridSpec | None = None,
    *,
    n_splits: int = 4,
    initial_capital: float = 10_000.0,
) -> pd.DataFrame:
    """Anchored walk-forward: select on train, report on test.

    Anchored rather than rolling so each training window includes everything
    known up to that point, which is what a live deployment would actually
    have available.
    """
    times = np.concatenate([d.ltp["time"].to_numpy() for d in datasets.values()])
    t0, t1 = int(times.min()), int(times.max())
    edges = np.linspace(t0, t1, n_splits + 2).astype(np.int64)

    rows = []
    for k in range(n_splits):
        train = (t0, int(edges[k + 1]))
        test = (int(edges[k + 1]) + 1, int(edges[k + 2]))

        train_df = run_grid(datasets, grid, time_slice=train,
                            initial_capital=initial_capital, progress_every=0)
        best = select_best(train_df)
        if best is None:
            rows.append({
                "split": k, "train_end": train[1], "test_start": test[0],
                "test_end": test[1], "selected": None,
                "oos_trades": 0, "oos_expectancy_r": float("nan"),
                "note": "no configuration cleared the trade-count floor in training",
            })
            continue

        oos = evaluate(datasets, params_from_row(best),
                       initial_capital=initial_capital, time_slice=test)
        rows.append({
            "split": k,
            "train_end": train[1],
            "test_start": test[0],
            "test_end": test[1],
            "selected": {k2: best[k2] for k2 in describe(params_from_row(best))},
            "is_trades": int(best["trades"]),
            "is_expectancy_r": float(best["expectancy_r"]),
            "oos_trades": oos["trades"],
            "oos_expectancy_r": oos["expectancy_r"],
            "oos_symbols": oos["symbols_traded"],
            "note": "",
        })

    return pd.DataFrame(rows)
