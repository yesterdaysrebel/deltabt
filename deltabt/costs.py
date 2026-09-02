"""Cost model for Delta Exchange India perpetual futures.

Cost is the binding constraint for this strategy, not signal quality. A 1m
Supertrend(10, 2.0) stop sits a median ~21.6 bps from entry; a taker round trip
is 0.118% plus slippage, which is roughly 0.55R. That moves the break-even win
rate at a 2R target from 33.3% to 53.3% before any signal has been evaluated.
Modelling this accurately is therefore the difference between a useful
backtest and a fantasy.

Facts encoded here, all verified against the live API:

* Fees are not uniform. 186 crypto perps are 0.02/0.05% maker/taker, 31
  tokenised-equity perps are 0.02/0.02%, 3 metals are 0.01/0.01%.
* 18% GST applies on top for Indian users, so 0.05% taker bills as 0.059%.
* ``size`` is an integer number of contracts, each worth ``contract_value`` of
  the underlying.
* Funding is snapshot-based, not pro-rata: a position paying funding pays the
  full interval regardless of how long it was open, and a position closed one
  second before the snapshot pays nothing.
* Funding cadence is per-symbol -- 8h for ~80 perps, 4h for ~140.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from deltabt.config import GST_MULTIPLIER


@dataclass(frozen=True)
class SymbolCosts:
    """Per-symbol contract and cost specification."""

    symbol: str
    tick_size: float
    contract_value: float
    maker_fee: float
    taker_fee: float
    max_leverage: float
    position_size_limit: float
    funding_interval_seconds: int
    #: Modelled execution slippage, in basis points of notional. Deliberately
    #: NOT in ticks: a fixed tick count spans >100x in relative cost across
    #: symbols (2 ticks is 0.03 bps on BTCUSD but 200 bps on a
    #: micro-priced alt), which would make any cross-symbol ranking an
    #: artifact of tick size rather than of edge.
    slippage_bps: float = 2.0
    gst_multiplier: float = GST_MULTIPLIER

    @property
    def effective_taker(self) -> float:
        return self.taker_fee * self.gst_multiplier

    @property
    def effective_maker(self) -> float:
        return self.maker_fee * self.gst_multiplier

    @property
    def slippage_rate(self) -> float:
        return self.slippage_bps / 10_000.0

    @classmethod
    def from_spec(cls, spec: dict, *, slippage_bps: float = 2.0) -> "SymbolCosts":
        return cls(
            symbol=spec["symbol"],
            tick_size=float(spec["tick_size"]),
            contract_value=float(spec["contract_value"]),
            maker_fee=float(spec["maker_fee"]),
            taker_fee=float(spec["taker_fee"]),
            max_leverage=float(spec["max_leverage"]),
            position_size_limit=float(spec["position_size_limit"]),
            funding_interval_seconds=int(spec["funding_interval_seconds"]),
            slippage_bps=slippage_bps,
        )

    # -- rounding -----------------------------------------------------------

    def round_price(self, price: float, *, direction: int = 0) -> float:
        """Snap to the tick grid. ``direction`` -1 floors, +1 ceils, 0 rounds."""
        if self.tick_size <= 0:
            return price
        q = price / self.tick_size
        if direction < 0:
            q = np.floor(q)
        elif direction > 0:
            q = np.ceil(q)
        else:
            q = np.round(q)
        return float(q * self.tick_size)

    def contracts_for(self, units: float) -> int:
        """Convert a desired position in underlying units to whole contracts.

        Rounds down, so realised risk is always at or below the budget. For
        cheap contracts the quantisation is negligible; for SOLUSD (1 contract
        is ~$76 of notional) on a small account it materially bounds the
        achievable position.
        """
        if self.contract_value <= 0:
            return 0
        n = int(np.floor(units / self.contract_value))
        n = max(n, 0)
        if np.isfinite(self.position_size_limit):
            n = min(n, int(self.position_size_limit))
        return n

    def notional(self, contracts: int, price: float) -> float:
        return abs(contracts) * self.contract_value * price

    # -- costs --------------------------------------------------------------

    #: Model the ENTRY as a resting limit order rather than a market order.
    #:
    #: The operator's own Delta history says this is how they traded: 1,074 of
    #: 1,437 executed orders (74.7%) were limit_order, and those paid a median
    #: 2.36 bps -- exactly 0.02% x 1.18 GST, the maker rate to the decimal.
    #: The bot pays 7.9 bps instead (5.9 taker + 2.0 slippage), so a maker
    #: entry saves ~5.5 bps per leg.
    #:
    #: THIS FLAG MODELS ONLY THE PRICE, NOT THE FILL. A resting order does not
    #: always fill: 32.1% of the operator's limit orders were cancelled unfilled
    #: (1,582 placed, 1,074 filled). Turning this on therefore measures an
    #: UPPER BOUND -- every signal still becomes a trade, at a better price.
    #: The real question is whether the 68% that fill are as good as the 100%
    #: that fill at market, and that needs a fill model this backtester does
    #: not have. Do not read a result from this flag as achievable.
    maker_entry: bool = False

    def entry_cost(self, contracts: int, price: float) -> float:
        """Cost on the way in.

        A market entry crosses the spread: taker fee plus modelled slippage.
        A resting limit entry earns the maker rate and, by construction, pays
        no slippage -- it fills at its own price or not at all.
        """
        n = self.notional(contracts, price)
        if self.maker_entry:
            return n * self.effective_maker
        return n * (self.effective_taker + self.slippage_rate)

    def exit_cost(self, contracts: int, price: float, *, maker: bool) -> float:
        """Exit cost.

        A limit take-profit rests and earns the maker rate with no slippage; a
        stop converts to a market order and pays taker plus slippage.
        """
        n = self.notional(contracts, price)
        if maker:
            return n * self.effective_maker
        return n * (self.effective_taker + self.slippage_rate)

    def round_trip_rate(self, *, maker_exit: bool = False) -> float:
        """Total cost as a fraction of notional, for the cost-per-R gate."""
        entry = self.effective_taker + self.slippage_rate
        exit_ = self.effective_maker if maker_exit else (
            self.effective_taker + self.slippage_rate
        )
        return entry + exit_

    def cost_per_r(self, entry_price: float, risk_per_unit: float) -> float:
        """Round-trip cost expressed as a multiple of R.

        Uses the pessimistic (taker exit) leg, since the stop is the exit that
        the risk budget is defined against.
        """
        if risk_per_unit <= 0:
            return float("inf")
        return (entry_price * self.round_trip_rate(maker_exit=False)) / risk_per_unit


def funding_timestamps(start: int, end: int, interval: int) -> np.ndarray:
    """Settlement instants in ``[start, end]``.

    Delta settles on UTC-aligned boundaries (00:00/08:00/16:00 for 8h symbols),
    so the grid is anchored to the epoch rather than to the window start.
    """
    if interval <= 0:
        return np.zeros(0, dtype=np.int64)
    first = ((start + interval - 1) // interval) * interval
    if first > end:
        return np.zeros(0, dtype=np.int64)
    return np.arange(first, end + 1, interval, dtype=np.int64)


def funding_charge(
    contracts: int,
    side: int,
    price: float,
    rate_percent: float,
    contract_value: float,
) -> float:
    """Funding paid (positive) or received (negative) at one snapshot.

    ``rate_percent`` is the instantaneous rate in percent per interval, as
    served by the ``FUNDING:`` candle series. A long pays when the rate is
    positive.
    """
    notional = abs(contracts) * contract_value * price
    return side * notional * (rate_percent / 100.0)
