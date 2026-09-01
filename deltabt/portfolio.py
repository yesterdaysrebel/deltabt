"""One account, one position slot, the full risk engine -- across all symbols.

WHY THIS EXISTS
    ``deltabt.engine.run_backtest`` simulates ONE symbol with its own capital.
    Running it per symbol and adding up the results describes a system that
    holds N concurrent positions on N independent accounts. Production holds
    ONE position on ONE account. ``app/config/variants.py`` records what that
    difference is worth: correcting it "turned a t of +8.30 into a negative
    result".

    So a per-symbol sweep ranks configurations; it does not size them. Any
    statement about P&L, drawdown or a daily loss limit has to come from here.

THE GATES ARE DAILY, AND THE DAY ROLLS ON THE BAR
    ``app/risk/engine.py`` resets ``trades_today``, the daily loss basis AND
    ``consecutive_losses`` when the first bar of a new UTC day arrives. The
    streak in particular is a DAILY breaker: clearing it only on a win makes it
    a permanent silent halt, because clearing requires a win and a win requires
    an entry.

    ``app/config/variants.py`` records a simulator that got this wrong by
    taking the day from the ENTRY bar, so a position opened Monday and closed
    Tuesday booked under Monday and the roll cleared the streak early. It found
    n=237 where a correct ordering found n=91. This module rolls the day on the
    BAR being processed, which is what the live engine does.

ORDERING WITHIN A TIMESTAMP
    Exits are processed for every symbol before any entry is considered, so
    capital freed by an exit is available to an entry on the same bar -- as it
    is live, where the fill precedes the next signal. Entry arbitration between
    symbols competing for the same slot is by sorted symbol name: arbitrary,
    but fixed, so a run is reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from deltabt.config import StrategyParams
from deltabt.costs import SymbolCosts, funding_timestamps
from deltabt.engine import LONG, SHORT, BacktestResult, Trade
from deltabt.strategy import Signals

#: Gate rejections, reported so a run that takes no trades explains itself.
GATE_REASONS = ("max_open_positions", "max_trades_per_day", "max_daily_loss",
                "max_drawdown", "max_consecutive_losses",
                # Not gates -- sizing outcomes. Counted because on a small
                # account zero_contracts is the dominant reason a signal never
                # becomes a trade: the risk budget divided by the stop distance
                # rounds DOWN to zero contracts. A run that skipped those
                # silently would look like the strategy firing less often
                # rather than like the account being too small to express it.
                "zero_contracts", "cost_per_r", "stop_too_close")


@dataclass(frozen=True)
class RiskGates:
    """Portfolio circuit breakers. Defaults mirror ``app/config/settings.py``.

    A gate BOUNDS DRAWDOWN. It does not improve expectancy, and it biases any
    measurement of expectancy upward: a daily loss limit conditions the sample
    on the day not already having gone badly, so the surviving mean is not an
    unbiased estimate. Measured on the v3 forward test, gated and ungated
    friction were 0.239R and 0.241R -- identical -- while the standard error
    rose from 0.272 to 0.335. Run gated to size a system; run ungated to
    measure one.
    """

    max_open_positions: int = 1
    max_trades_per_day: int = 20
    #: Fraction of the day's opening equity. 1.0 disables it.
    max_daily_loss_pct: float = 1.0
    #: Fraction below peak equity. 1.0 disables it.
    max_drawdown_pct: float = 0.10
    #: 0 disables it. Reset on the day roll -- see the module docstring.
    max_consecutive_losses: int = 3

    #: Days after which a latched drawdown halt is lifted, modelling an
    #: operator running ``deltabt forward-test resume``. 0 means the halt is
    #: terminal, which is what the live engine does without intervention.
    #:
    #: This matters more than it looks. A drawdown halt reached while flat
    #: CANNOT lift on its own -- entries are refused, so nothing closes, so
    #: equity never moves. A backtest that ignores this reports "smaller loss"
    #: for a run that simply stopped trading, and ``window_used_pct`` in
    #: scripts/gated_backtest.py exists to make that visible. Setting a resume
    #: policy here is how you ask what the system would have done had somebody
    #: restarted it.
    resume_after_days: int = 0

    @classmethod
    def off(cls) -> "RiskGates":
        """Everything disabled except the single position slot."""
        return cls(max_open_positions=1, max_trades_per_day=10**9,
                   max_daily_loss_pct=1.0, max_drawdown_pct=1.0,
                   max_consecutive_losses=0)


@dataclass
class Book:
    """Everything one symbol contributes to the portfolio."""

    symbol: str
    bars: pd.DataFrame
    signals: Signals
    costs: SymbolCosts
    mark: pd.DataFrame | None = None
    tradable: np.ndarray | None = None


@dataclass
class _Position:
    symbol: str
    side: int
    contracts: int
    entry_price: float
    entry_index: int
    entry_time: int
    stop_price: float
    target_price: float
    risk_per_unit: float
    entry_fee: float
    cost_per_r: float
    accrued_funding: float = 0.0


@dataclass
class _Series:
    """Per-symbol arrays, prepared once."""

    book: Book
    time: np.ndarray
    close: np.ndarray
    mark_high: np.ndarray
    mark_low: np.ndarray
    tradable: np.ndarray
    funding: dict
    last_exit_index: int = -(10 ** 9)


def _prepare(book: Book) -> _Series:
    bars = book.bars
    time = bars["time"].to_numpy("int64")
    close = bars["close"].to_numpy("float64")
    high = bars["high"].to_numpy("float64")
    low = bars["low"].to_numpy("float64")

    if book.mark is not None and not book.mark.empty:
        m = book.mark.set_index("time").reindex(time)
        mh = m["high"].to_numpy("float64")
        ml = m["low"].to_numpy("float64")
        bad = ~np.isfinite(mh) | ~np.isfinite(ml)
        mh = np.where(bad, high, mh)
        ml = np.where(bad, low, ml)
    else:
        mh, ml = high, low

    tradable = (book.tradable if book.tradable is not None
                else np.ones(len(time), dtype=bool))

    stamps = (funding_timestamps(int(time[0]), int(time[-1]),
                                 book.costs.funding_interval_seconds)
              if len(time) else np.zeros(0, dtype=np.int64))
    rates = _funding_lookup(book, stamps)
    return _Series(book=book, time=time, close=close, mark_high=mh, mark_low=ml,
                   tradable=tradable, funding=rates)


def _funding_lookup(book: Book, stamps: np.ndarray) -> dict:
    from deltabt.engine import _funding_lookup as engine_lookup
    fdf = getattr(book, "funding_df", None)
    if fdf is None:
        fdf = pd.DataFrame()
    return engine_lookup(fdf, stamps)


def run_portfolio(
    books: dict[str, Book],
    params: StrategyParams,
    gates: RiskGates | None = None,
    *,
    initial_capital: float = 10_000.0,
    funding: dict[str, pd.DataFrame] | None = None,
) -> BacktestResult:
    """Simulate every symbol against one shared account."""
    if params.wpr.enabled:
        raise NotImplementedError(
            "the stateful Williams %R latch is not supported here; no "
            "StrategySpec expresses it")
    gates = gates or RiskGates()

    series: dict[str, _Series] = {}
    for sym in sorted(books):
        book = books[sym]
        if funding is not None:
            book.funding_df = funding.get(sym, pd.DataFrame())
        series[sym] = _prepare(book)

    # One merged, time-ordered event stream: (timestamp, symbol, bar index).
    events: list[tuple[int, str, int]] = []
    for sym, s in series.items():
        events.extend((int(t), sym, i) for i, t in enumerate(s.time))
    events.sort(key=lambda e: (e[0], e[1]))

    result = BacktestResult(symbol="PORTFOLIO", mode=params.mode,
                            rejects={k: 0 for k in GATE_REASONS},
                            bars=len(events), initial_capital=initial_capital)
    equity = initial_capital
    peak_equity = initial_capital
    open_positions: dict[str, _Position] = {}

    current_day = None
    day_start_equity = initial_capital
    trades_today = 0
    consecutive_losses = 0
    halted_at: int | None = None
    halts: list[tuple[int, float]] = []

    curve_t: list[int] = []
    curve_v: list[float] = []

    idx = 0
    n = len(events)
    while idx < n:
        ts = events[idx][0]
        same = []
        while idx < n and events[idx][0] == ts:
            same.append(events[idx])
            idx += 1

        # --- day roll, on the BAR, before anything else ---------------------
        day = ts // 86_400
        if current_day is None or day != current_day:
            current_day = day
            day_start_equity = equity
            trades_today = 0
            consecutive_losses = 0

        # --- funding and exits, every symbol, before any entry --------------
        for _, sym, i in same:
            s = series[sym]
            pos = open_positions.get(sym)
            if pos is None:
                continue
            px = s.close[i]

            if int(s.time[i]) in s.funding:
                rate = s.funding[int(s.time[i])]
                charge = (pos.side * abs(pos.contracts) * s.book.costs.contract_value
                          * px * (rate / 100.0))
                pos.accrued_funding += charge
                equity -= charge

            if pos.side == LONG:
                hit_stop = s.mark_low[i] <= pos.stop_price
                hit_target = s.mark_high[i] >= pos.target_price
            else:
                hit_stop = s.mark_high[i] >= pos.stop_price
                hit_target = s.mark_low[i] <= pos.target_price

            exit_price, exit_reason, ambiguous = np.nan, "", False
            if hit_stop and hit_target:
                ambiguous = True
                exit_price, exit_reason = pos.stop_price, "stop"
            elif hit_stop:
                exit_price, exit_reason = pos.stop_price, "stop"
            elif hit_target:
                exit_price, exit_reason = pos.target_price, "target"
            elif params.exit_on_trend_flip and (
                (pos.side == LONG and s.book.signals.bear_1m[i])
                or (pos.side == SHORT and s.book.signals.bull_1m[i])
            ):
                exit_price, exit_reason = px, "trend_flip"
            elif params.exit_at_adverse_r is not None and (
                (pos.side == LONG
                 and (pos.entry_price - px) >= params.exit_at_adverse_r * (pos.entry_price - pos.stop_price))
                or (pos.side == SHORT
                    and (px - pos.entry_price) >= params.exit_at_adverse_r * (pos.stop_price - pos.entry_price))
            ):
                exit_price, exit_reason = px, "adverse_r"
            elif params.exit_on_wpr_band_exit and np.isfinite(s.book.signals.wpr[i]) and (
                (pos.side == LONG and s.book.signals.wpr[i] < params.wpr_exit_long_level)
                or (pos.side == SHORT and s.book.signals.wpr[i] > params.wpr_exit_short_level)
            ):
                exit_price, exit_reason = px, "wpr_band"
            elif params.max_hold_bars and (i - pos.entry_index) >= params.max_hold_bars:
                exit_price, exit_reason = px, "max_hold"
            if not exit_reason:
                continue

            costs = s.book.costs
            fee_out = costs.exit_cost(pos.contracts, exit_price,
                                      maker=exit_reason == "target")
            gross = (pos.side * (exit_price - pos.entry_price)
                     * pos.contracts * costs.contract_value)
            pnl = gross - pos.entry_fee - fee_out - pos.accrued_funding
            equity += gross - fee_out
            peak_equity = max(peak_equity, equity)

            unit_risk = pos.risk_per_unit * pos.contracts * costs.contract_value
            result.trades.append(Trade(
                symbol=sym, side=pos.side, entry_time=pos.entry_time,
                exit_time=int(s.time[i]), entry_price=pos.entry_price,
                exit_price=exit_price, stop_price=pos.stop_price,
                target_price=pos.target_price, contracts=pos.contracts,
                notional=costs.notional(pos.contracts, pos.entry_price),
                risk_per_unit=pos.risk_per_unit,
                r_multiple=(pnl / unit_risk) if unit_risk > 0 else 0.0,
                pnl=pnl, fees=pos.entry_fee + fee_out,
                funding=pos.accrued_funding, exit_reason=exit_reason,
                bars_held=i - pos.entry_index,
                leverage=costs.notional(pos.contracts, pos.entry_price) / max(equity, 1e-9),
                cost_per_r=pos.cost_per_r, ambiguous=ambiguous,
            ))
            del open_positions[sym]
            s.last_exit_index = i
            # The streak is updated at CLOSE, on the day the close happens.
            consecutive_losses = 0 if pnl > 0 else consecutive_losses + 1

        # --- entries --------------------------------------------------------
        for _, sym, i in same:
            s = series[sym]
            sig = s.book.signals
            if sym in open_positions or i < sig.warmup:
                continue
            want_long = bool(sig.long_entry[i])
            want_short = bool(sig.short_entry[i])
            if not (want_long or want_short):
                continue

            if len(open_positions) >= gates.max_open_positions:
                result.rejects["max_open_positions"] += 1
                continue
            if trades_today >= gates.max_trades_per_day:
                result.rejects["max_trades_per_day"] += 1
                continue
            if day_start_equity > 0 and (
                (day_start_equity - equity) / day_start_equity >= gates.max_daily_loss_pct
            ):
                result.rejects["max_daily_loss"] += 1
                continue
            if halted_at is not None:
                if (gates.resume_after_days
                        and ts - halted_at >= gates.resume_after_days * 86_400):
                    # An operator resume. Rebasing the peak is the mechanism:
                    # the halt is measured against it, so clearing the flag
                    # alone would re-halt on the next bar.
                    halted_at = None
                    peak_equity = equity
                else:
                    result.rejects["max_drawdown"] += 1
                    continue
            if peak_equity > 0 and (
                (peak_equity - equity) / peak_equity >= gates.max_drawdown_pct
            ):
                # Breaching with a position still open is recoverable -- it can
                # close green. Breaching while FLAT is not, so latch it, which
                # is what the live engine now does.
                if not open_positions:
                    halted_at = ts
                    halts.append((ts, equity))
                result.rejects["max_drawdown"] += 1
                continue
            if (gates.max_consecutive_losses > 0
                    and consecutive_losses >= gates.max_consecutive_losses):
                result.rejects["max_consecutive_losses"] += 1
                continue

            if not s.tradable[i]:
                continue
            if params.cooldown_bars and (i - s.last_exit_index) < params.cooldown_bars:
                continue

            side = LONG if want_long else SHORT
            raw_stop = sig.stop_long[i] if side == LONG else sig.stop_short[i]
            if not np.isfinite(raw_stop):
                continue

            costs = s.book.costs
            px = s.close[i]
            stop_px = costs.round_price(raw_stop, direction=-1 if side == LONG else 1)
            rpu = (px - stop_px) if side == LONG else (stop_px - px)
            min_risk = max(
                params.min_stop_atr_mult * (sig.atr[i] if np.isfinite(sig.atr[i]) else 0.0),
                params.min_stop_ticks * costs.tick_size)
            if not np.isfinite(rpu) or rpu <= 0 or rpu < min_risk:
                result.rejects["stop_too_close"] += 1
                continue

            cpr = costs.cost_per_r(px, rpu)
            if params.max_cost_per_r is not None and cpr > params.max_cost_per_r:
                result.rejects["cost_per_r"] += 1
                continue

            risk_capital = equity * params.risk_percent / 100.0
            units = risk_capital / rpu
            if np.isfinite(params.max_leverage):
                units = min(units, (equity * params.max_leverage) / px)
            contracts = costs.contracts_for(units)
            if contracts <= 0:
                result.rejects["zero_contracts"] += 1
                continue

            target = (px + rpu * params.reward_risk if side == LONG
                      else px - rpu * params.reward_risk)
            entry_fee = costs.entry_cost(contracts, px)
            equity -= entry_fee
            open_positions[sym] = _Position(
                symbol=sym, side=side, contracts=contracts, entry_price=px,
                entry_index=i, entry_time=int(s.time[i]), stop_price=stop_px,
                target_price=costs.round_price(target, direction=1 if side == LONG else -1),
                risk_per_unit=rpu, entry_fee=entry_fee, cost_per_r=cpr)
            trades_today += 1

        curve_t.append(ts)
        curve_v.append(equity)

    #: (timestamp, equity) for each latched drawdown halt.
    result.halts = halts
    result.equity_curve = np.array(curve_v, dtype="float64")
    result.equity_time = np.array(curve_t, dtype="int64")
    result.optimistic_pnl = equity - initial_capital
    return result
