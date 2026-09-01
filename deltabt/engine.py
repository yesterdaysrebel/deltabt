"""Event-driven backtest engine.

One position at a time, evaluated at each 1m close. The design decisions that
separate this from a naive bar loop:

* **Stops trigger on MARK price, fills price off LTP.** Delta triggers stop
  orders on mark price by default, and mark diverges from last-traded by a few
  ticks continuously. Testing a mark-triggered stop against LTP lows
  systematically mistimes exits.
* **Position size is capped by leverage and rounded to integer contracts.**
  The original had neither. With entry at ``close``, a stop at the Supertrend
  line, and no cap, ``qty = risk / (close - stop)`` can put a $27M order on a
  $10k account.
* **Same-bar stop and target resolve pessimistically** (stop first), matching
  Pine's assumption. The ambiguous fraction is counted and reported rather
  than hidden, because 1m OHLC cannot order the two events and Delta serves no
  sub-minute history to disambiguate against.
* **Funding is charged discretely** at settlement instants only.
* **Untradable bars are skipped** -- synthetic forward-filled minutes and
  exchange halts are not fillable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from deltabt.config import StrategyParams
from deltabt.costs import SymbolCosts, funding_timestamps
from deltabt.strategy import Signals
from deltabt.wpr_latch import step_state

LONG, SHORT = 1, -1

# Reasons a signal did not become a trade. Reported so a run that produces no
# trades explains itself instead of looking like a data problem.
REJECT_REASONS = (
    "cost_per_r",
    "stop_too_close",
    "zero_contracts",
    "cooldown",
    "in_position",
    "untradable_bar",
    "size_limit",
)


@dataclass
class Trade:
    symbol: str
    side: int
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    contracts: int
    notional: float
    risk_per_unit: float
    r_multiple: float
    pnl: float
    fees: float
    funding: float
    exit_reason: str
    bars_held: int
    leverage: float
    cost_per_r: float
    ambiguous: bool


@dataclass
class BacktestResult:
    symbol: str
    mode: str
    trades: list[Trade] = field(default_factory=list)
    equity_curve: np.ndarray | None = None
    equity_time: np.ndarray | None = None
    rejects: dict[str, int] = field(default_factory=dict)
    bars: int = 0
    initial_capital: float = 10_000.0
    #: Optimistic counterpart to the headline (pessimistic) equity: same
    #: trades, but same-bar conflicts resolved target-first. The truth lies
    #: between the two.
    optimistic_pnl: float = 0.0

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def final_equity(self) -> float:
        if self.equity_curve is None or len(self.equity_curve) == 0:
            return self.initial_capital
        return float(self.equity_curve[-1])

    def to_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(
                columns=[f.name for f in Trade.__dataclass_fields__.values()]
            )
        return pd.DataFrame([t.__dict__ for t in self.trades])


def _funding_lookup(
    funding_df: pd.DataFrame, stamps: np.ndarray
) -> dict[int, float]:
    """Rate in percent at each settlement instant.

    The FUNDING: candle series carries the instantaneous rate; we sample it at
    the settlement timestamps rather than accruing it, because Delta charges on
    a snapshot basis -- holding duration between intervals is irrelevant, and a
    position opened one second before a snapshot pays the full interval.
    """
    if funding_df is None or funding_df.empty or stamps.size == 0:
        return {}
    ft = funding_df["time"].to_numpy(dtype="int64")
    fv = funding_df["close"].to_numpy(dtype="float64")
    idx = np.searchsorted(ft, stamps, side="right") - 1
    out: dict[int, float] = {}
    for s, i in zip(stamps, idx):
        if i >= 0 and np.isfinite(fv[i]):
            out[int(s)] = float(fv[i])
    return out


def run_backtest(
    df: pd.DataFrame,
    mark_df: pd.DataFrame,
    funding_df: pd.DataFrame,
    signals: Signals,
    params: StrategyParams,
    costs: SymbolCosts,
    *,
    tradable: np.ndarray | None = None,
    initial_capital: float = 10_000.0,
) -> BacktestResult:
    """Run one symbol through the engine."""
    n = len(df)
    time = df["time"].to_numpy(dtype="int64")
    close = df["close"].to_numpy(dtype="float64")
    high = df["high"].to_numpy(dtype="float64")
    low = df["low"].to_numpy(dtype="float64")

    # Stops trigger on mark; fall back to LTP only if mark is unavailable.
    if mark_df is not None and not mark_df.empty:
        m = mark_df.set_index("time").reindex(time)
        mark_high = m["high"].to_numpy(dtype="float64")
        mark_low = m["low"].to_numpy(dtype="float64")
        bad = ~np.isfinite(mark_high) | ~np.isfinite(mark_low)
        mark_high = np.where(bad, high, mark_high)
        mark_low = np.where(bad, low, mark_low)
    else:
        mark_high, mark_low = high, low

    if tradable is None:
        tradable = np.ones(n, dtype=bool)

    stamps = funding_timestamps(
        int(time[0]), int(time[-1]), costs.funding_interval_seconds
    ) if n else np.zeros(0, dtype=np.int64)
    funding_rates = _funding_lookup(funding_df, stamps)
    funding_set = set(funding_rates)

    result = BacktestResult(
        symbol=costs.symbol,
        mode=params.mode,
        rejects={k: 0 for k in REJECT_REASONS},
        bars=n,
        initial_capital=initial_capital,
    )

    equity = initial_capital
    equity_curve = np.full(n, initial_capital, dtype="float64")
    optimistic_equity = initial_capital

    # Open position state
    pos_side = 0
    pos_contracts = 0
    entry_price = 0.0
    entry_index = 0
    stop_price = 0.0
    target_price = 0.0
    risk_per_unit = 0.0
    entry_fee = 0.0
    accrued_funding = 0.0
    entry_cost_per_r = 0.0
    last_exit_index = -(10**9)

    # Incremental latch state, used when the gate must clear on position state.
    use_live_latch = (
        params.mode == "corrected"
        and params.wpr.enabled
        and (params.wpr.clear_in_position or params.wpr.clear_on_adverse_flip)
    )
    arm_long = -1
    arm_short = -1
    wpr_v = signals.wpr

    for i in range(n):
        px = close[i]

        # --- funding on open positions ------------------------------------
        if pos_side != 0 and int(time[i]) in funding_set:
            rate = funding_rates[int(time[i])]
            charge = (
                pos_side
                * abs(pos_contracts)
                * costs.contract_value
                * px
                * (rate / 100.0)
            )
            accrued_funding += charge
            equity -= charge

        # --- manage an open position ---------------------------------------
        if pos_side != 0:
            exit_price = np.nan
            exit_reason = ""
            ambiguous = False

            if pos_side == LONG:
                hit_stop = mark_low[i] <= stop_price
                hit_target = mark_high[i] >= target_price
            else:
                hit_stop = mark_high[i] >= stop_price
                hit_target = mark_low[i] <= target_price

            if hit_stop and hit_target:
                # 1m OHLC cannot order these two events, and there is no
                # sub-minute history on Delta to check against. Pine assumes
                # the stop filled first; so do we, and we count it.
                ambiguous = True
                exit_price, exit_reason = stop_price, "stop"
            elif hit_stop:
                exit_price, exit_reason = stop_price, "stop"
            elif hit_target:
                exit_price, exit_reason = target_price, "target"
            elif params.exit_on_trend_flip and (
                (pos_side == LONG and signals.bear_1m[i])
                or (pos_side == SHORT and signals.bull_1m[i])
            ):
                exit_price, exit_reason = px, "trend_flip"
            elif params.exit_at_adverse_r is not None and (
                (pos_side == LONG
                 and (entry_price - px) >= params.exit_at_adverse_r * (entry_price - stop_price))
                or (pos_side == SHORT
                    and (px - entry_price) >= params.exit_at_adverse_r * (stop_price - entry_price))
            ):
                exit_price, exit_reason = px, "adverse_r"
            elif params.exit_on_wpr_band_exit and np.isfinite(signals.wpr[i]) and (
                (pos_side == LONG and signals.wpr[i] < params.wpr_exit_long_level)
                or (pos_side == SHORT and signals.wpr[i] > params.wpr_exit_short_level)
            ):
                exit_price, exit_reason = px, "wpr_band"
            elif params.max_hold_bars and (i - entry_index) >= params.max_hold_bars:
                exit_price, exit_reason = px, "max_hold"

            if exit_reason:
                maker = exit_reason == "target"
                fee_out = costs.exit_cost(pos_contracts, exit_price, maker=maker)
                gross = (
                    pos_side
                    * (exit_price - entry_price)
                    * pos_contracts
                    * costs.contract_value
                )
                pnl = gross - entry_fee - fee_out - accrued_funding
                equity += gross - fee_out

                # Optimistic counterpart: on an ambiguous bar, assume the
                # target filled instead.
                if ambiguous:
                    opt_gross = (
                        pos_side
                        * (target_price - entry_price)
                        * pos_contracts
                        * costs.contract_value
                    )
                    opt_fee = costs.exit_cost(pos_contracts, target_price, maker=True)
                    optimistic_equity += opt_gross - opt_fee
                else:
                    optimistic_equity += gross - fee_out

                unit_risk = risk_per_unit * pos_contracts * costs.contract_value
                result.trades.append(
                    Trade(
                        symbol=costs.symbol,
                        side=pos_side,
                        entry_time=int(time[entry_index]),
                        exit_time=int(time[i]),
                        entry_price=entry_price,
                        exit_price=exit_price,
                        stop_price=stop_price,
                        target_price=target_price,
                        contracts=pos_contracts,
                        notional=costs.notional(pos_contracts, entry_price),
                        risk_per_unit=risk_per_unit,
                        r_multiple=(pnl / unit_risk) if unit_risk > 0 else 0.0,
                        pnl=pnl,
                        fees=entry_fee + fee_out,
                        funding=accrued_funding,
                        exit_reason=exit_reason,
                        bars_held=i - entry_index,
                        leverage=costs.notional(pos_contracts, entry_price) / max(equity, 1e-9),
                        cost_per_r=entry_cost_per_r,
                        ambiguous=ambiguous,
                    )
                )
                pos_side = 0
                pos_contracts = 0
                accrued_funding = 0.0
                last_exit_index = i

        # --- latch state ----------------------------------------------------
        if use_live_latch:
            prev_w = wpr_v[i - 1] if i > 0 else np.nan
            arm_long, fired_long = step_state(
                arm_long, i, wpr_v[i], prev_w,
                params.wpr.arm_long, params.wpr.fire_long,
                params.wpr.expiry_bars, True,
            )
            arm_short, fired_short = step_state(
                arm_short, i, wpr_v[i], prev_w,
                params.wpr.arm_short, params.wpr.fire_short,
                params.wpr.expiry_bars, False,
            )
            if params.wpr.clear_in_position and pos_side != 0:
                arm_long = arm_short = -1
            if params.wpr.clear_on_adverse_flip:
                # Only clear on a flip AGAINST the setup. Clearing the long
                # latch when the trend turns bullish would disable longs
                # entirely, since WPR only reaches the long arming zone during
                # a downtrend -- a latch armed in a downtrend firing after the
                # flip is the setup, not a bug.
                if i > 0 and signals.bear_1m[i] and not signals.bear_1m[i - 1]:
                    arm_long = -1
                if i > 0 and signals.bull_1m[i] and not signals.bull_1m[i - 1]:
                    arm_short = -1
            # No edge-trigger guard needed here: a latch fire is already a
            # one-bar pulse, and ANDing a pulse with plateaus yields a pulse.
            want_long = bool(signals.long_base[i]) and fired_long
            want_short = bool(signals.short_base[i]) and fired_short
        else:
            want_long = bool(signals.long_entry[i])
            want_short = bool(signals.short_entry[i])

        equity_curve[i] = equity

        # --- entries --------------------------------------------------------
        if pos_side != 0 or not (want_long or want_short):
            continue
        if i < signals.warmup:
            continue
        if not tradable[i]:
            result.rejects["untradable_bar"] += 1
            continue
        if params.cooldown_bars and (i - last_exit_index) < params.cooldown_bars:
            result.rejects["cooldown"] += 1
            continue

        side = LONG if want_long else SHORT
        raw_stop = signals.stop_long[i] if side == LONG else signals.stop_short[i]
        if not np.isfinite(raw_stop):
            continue

        stop_px = costs.round_price(raw_stop, direction=-1 if side == LONG else 1)
        rpu = (px - stop_px) if side == LONG else (stop_px - px)

        # Floor the stop distance. The original guard was `> mintick`, which
        # rejects nothing meaningful and is exactly the regime that produces
        # absurd position sizes.
        min_risk = max(
            params.min_stop_atr_mult * (signals.atr[i] if np.isfinite(signals.atr[i]) else 0.0),
            params.min_stop_ticks * costs.tick_size,
        )
        if not np.isfinite(rpu) or rpu <= 0 or rpu < min_risk:
            result.rejects["stop_too_close"] += 1
            continue

        cpr = costs.cost_per_r(px, rpu)
        if params.max_cost_per_r is not None and cpr > params.max_cost_per_r:
            result.rejects["cost_per_r"] += 1
            continue

        risk_capital = equity * params.risk_percent / 100.0
        units_by_risk = risk_capital / rpu
        if np.isfinite(params.max_leverage):
            units_by_leverage = (equity * params.max_leverage) / px
            units = min(units_by_risk, units_by_leverage)
        else:
            units = units_by_risk

        contracts = costs.contracts_for(units)
        if contracts <= 0:
            result.rejects["zero_contracts"] += 1
            continue
        if contracts >= costs.position_size_limit:
            result.rejects["size_limit"] += 1

        target = (
            px + rpu * params.reward_risk if side == LONG else px - rpu * params.reward_risk
        )

        pos_side = side
        pos_contracts = contracts
        entry_price = px
        entry_index = i
        stop_price = stop_px
        target_price = costs.round_price(target, direction=1 if side == LONG else -1)
        risk_per_unit = rpu
        entry_fee = costs.entry_cost(contracts, px)
        accrued_funding = 0.0
        entry_cost_per_r = cpr
        equity -= entry_fee
        optimistic_equity -= entry_fee
        equity_curve[i] = equity

    result.equity_curve = equity_curve
    result.equity_time = time
    result.optimistic_pnl = optimistic_equity - initial_capital
    return result
