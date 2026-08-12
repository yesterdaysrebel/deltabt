"""The only object the paper broker will act on.

THE SAFETY BOUNDARY BETWEEN STRATEGY AND EXECUTION

``PaperBroker.submit_order`` accepts an ``ApprovedOrderIntent`` and nothing
else. An intent cannot exist without a completed risk evaluation: its
``risk_evaluation_id`` and ``checks_passed`` are required and validated at
construction.

This is enforced structurally rather than by convention:

* the strategy package does not import the broker or this module at all, which
  ``tests/live/test_no_live_trading.py::test_strategy_cannot_reach_execution``
  asserts against the shipped source;
* an intent with no risk evaluation raises at construction;
* the broker rejects anything that is not an ``ApprovedOrderIntent``.

Separately and more importantly: no object in this process, approved or not,
can reach a real exchange order endpoint, because no such method exists
anywhere in the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ApprovedOrderIntent:
    """A risk-approved instruction to open one paper position."""

    intent_id: str
    signal_key: str
    risk_evaluation_id: str
    symbol: str
    side: int                       # +1 long, -1 short
    order_type: str                 # "market" | "limit"
    quantity: int                   # whole contracts
    limit_price: float | None
    entry_reference: float          # price the sizing was computed against
    stop_price: float
    target_price: float
    risk_per_unit: float
    risk_amount: float
    notional: float
    equity_before: float
    estimated_fee: float
    estimated_slippage: float
    strategy_version: str
    bar_open: int
    checks_passed: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.risk_evaluation_id:
            raise ValueError(
                "an order intent cannot exist without a risk evaluation; "
                "only the risk engine may construct one")
        if not self.checks_passed:
            raise ValueError(
                "an order intent must record which risk checks it passed")
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {self.quantity}")
        if self.side not in (1, -1):
            raise ValueError(f"side must be +1 or -1, got {self.side}")
        if self.order_type not in ("market", "limit"):
            raise ValueError(f"unsupported order type {self.order_type!r}")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("a limit order needs a limit price")
        if self.risk_per_unit <= 0:
            raise ValueError("risk_per_unit must be positive")
        # Geometry sanity: a long whose stop sits above entry is a bug that
        # would invert the risk calculation, so it never becomes an intent.
        if self.side == 1 and not (self.stop_price < self.entry_reference < self.target_price):
            raise ValueError(
                f"long geometry inverted: stop {self.stop_price}, entry "
                f"{self.entry_reference}, target {self.target_price}")
        if self.side == -1 and not (self.target_price < self.entry_reference < self.stop_price):
            raise ValueError(
                f"short geometry inverted: stop {self.stop_price}, entry "
                f"{self.entry_reference}, target {self.target_price}")
