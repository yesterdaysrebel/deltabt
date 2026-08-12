"""The structured explanation attached to every evaluation.

Section 18 of the brief: "Do not simply return True/False." Every evaluation --
including the overwhelming majority that produce no setup -- yields one of
these, and it carries enough to answer, from the database alone:

    why did the bot enter / not enter, why this size, why this stop, why this
    target, what was the market data, what configuration was active.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class Outcome(str, Enum):
    NO_SETUP = "NO_SETUP"          # conditions not met
    SUPPRESSED = "SUPPRESSED"      # halt, warm-up, stale data, incomplete bar
    DETECTED = "DETECTED"          # setup valid, not yet risk-checked
    REJECTED = "REJECTED"          # risk engine said no
    APPROVED = "APPROVED"          # became an order intent
    DUPLICATE = "DUPLICATE"        # idempotency key already recorded


LONG, SHORT = 1, -1


@dataclass
class Explanation:
    """One evaluation of one symbol on one closed primary bar."""

    symbol: str
    bar_open: int                       # the CLOSED primary bar evaluated
    primary_timeframe: str
    confirmation_timeframe: str
    strategy_version: str
    strategy_config_hash: str
    outcome: Outcome
    direction: int | None = None

    conditions_passed: list[str] = field(default_factory=list)
    conditions_failed: list[str] = field(default_factory=list)
    indicators: dict = field(default_factory=dict)

    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    stop_distance_pct: float | None = None
    reward_risk: float | None = None

    # Filled in by the risk engine when the setup gets that far.
    risk_amount: float | None = None
    quantity: int | None = None
    notional: float | None = None
    estimated_fee: float | None = None
    estimated_slippage: float | None = None
    equity: float | None = None
    rejection_reason: str | None = None

    detail: dict = field(default_factory=dict)

    @property
    def is_setup(self) -> bool:
        return self.outcome in (Outcome.DETECTED, Outcome.APPROVED)

    def fail(self, reason: str) -> "Explanation":
        self.conditions_failed.append(reason)
        return self

    def passed(self, name: str) -> "Explanation":
        self.conditions_passed.append(name)
        return self

    def to_dict(self) -> dict:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        return d

    def summary(self) -> str:
        side = {LONG: "LONG", SHORT: "SHORT"}.get(self.direction or 0, "-")
        if self.outcome is Outcome.NO_SETUP:
            miss = ", ".join(self.conditions_failed[:3]) or "no conditions met"
            return f"{self.symbol} {self.outcome.value}: {miss}"
        if self.outcome in (Outcome.REJECTED, Outcome.SUPPRESSED):
            return (f"{self.symbol} {side} {self.outcome.value}: "
                    f"{self.rejection_reason or '; '.join(self.conditions_failed)}")
        return (f"{self.symbol} {side} {self.outcome.value} entry={self.entry_price} "
                f"stop={self.stop_price} target={self.target_price} "
                f"RR={self.reward_risk} qty={self.quantity}")
