"""Persisted record shapes, shared by every repository implementation.

All timestamps are UTC unix seconds in Python and become ``timestamptz`` in
Postgres. Nothing here carries a local time; IST appears only in the UI.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def new_uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def utc(ts: int | float) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


def now_ts() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


@dataclass
class InstanceRecord:
    instance_uid: str
    hostname: str
    pid: int
    strategy_version: str
    strategy_config: dict
    risk_config: dict
    symbols: list[str]


@dataclass
class SignalRecord:
    idempotency_key: str
    instance_uid: str
    symbol: str
    bar_open: int
    primary_timeframe: str
    confirmation_timeframe: str
    direction: int | None
    outcome: str                     # DETECTED | NO_SETUP | SUPPRESSED | REJECTED | APPROVED
    strategy_version: str
    strategy_config_hash: str
    conditions_passed: list[str]
    conditions_failed: list[str]
    indicators: dict
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    stop_distance_pct: float | None = None
    reward_risk: float | None = None
    rejection_reason: str | None = None
    detail: dict = field(default_factory=dict)
    #: EXCHANGE time the decision refers to (the closed bar's end), and the
    #: WALL time this process recorded it. Both, always -- see app/clock.py.
    exchange_ts: int | None = None
    received_ts: float | None = None
    event_type: str = "SIGNAL_EVALUATED"
    #: Which experiment produced this decision, and under what composite
    #: configuration. Stamped so a run cannot be silently mixed with another.
    experiment_id: str | None = None
    config_hash: str | None = None
    git_sha: str | None = None


@dataclass
class OrderRecord:
    order_uid: str
    idempotency_key: str
    signal_key: str
    instance_uid: str
    symbol: str
    side: int
    order_type: str                  # market | limit
    purpose: str                     # entry | stop | target
    quantity: int
    limit_price: float | None
    status: str
    equity_before: float
    risk_amount: float
    #: EXCHANGE time. Expiry compares this against tick timestamps, which are
    #: also exchange time. Comparing it against a wall clock is audit F8.
    created_exchange_ts: int | None = None
    expires_exchange_ts: int | None = None
    filled_exchange_ts: int | None = None
    fill_delay_seconds: float | None = None
    requested_price: float | None = None
    filled_price: float | None = None
    position_uid: str | None = None
    reject_reason: str | None = None
    received_ts: float | None = None
    event_type: str = "ORDER_CREATED"


@dataclass
class FillRecord:
    fill_uid: str
    order_uid: str
    #: Stated by the broker, never inferred from the symbol (audit F1).
    position_uid: str
    seq: int
    purpose: str                     # entry | exit
    instance_uid: str
    symbol: str
    side: int
    quantity: int
    price: float
    notional: float
    fee: float
    slippage: float
    liquidity: str                   # maker | taker
    filled_at: int
    tick_ts_us: int | None = None
    exchange_ts: int | None = None
    received_ts: float | None = None
    event_type: str = "ORDER_FILLED"


@dataclass
class PositionRecord:
    position_uid: str
    signal_key: str
    instance_uid: str
    symbol: str
    side: int
    status: str
    quantity: int
    entry_price: float
    stop_price: float
    target_price: float
    initial_risk: float
    risk_per_unit: float
    notional: float
    equity_before: float
    opened_at: int
    strategy_version: str
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    funding: float = 0.0
    exit_price: float | None = None
    realized_pnl: float | None = None
    r_multiple: float | None = None
    exit_reason: str | None = None
    closed_at: int | None = None
    hold_seconds: int | None = None
    requested_entry: float | None = None
    #: planned_r is what risk APPROVED; fill_rr is what the fill produced.
    #: They differ by entry slippage; reporting one hides the degradation.
    planned_r: float | None = None
    fill_rr: float | None = None
    entry_slippage: float = 0.0
    exit_slippage: float = 0.0
    gross_pnl: float | None = None
    #: Stamped so a position can never be silently attributed to the wrong run.
    experiment_id: str | None = None
    config_hash: str | None = None

    OPEN_STATES = ("OPENING", "OPEN", "SUSPENDED", "CLOSING")

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATES


@dataclass
class RiskEventRecord:
    event_id: str
    instance_uid: str
    event_type: str
    reason: str
    symbol: str | None = None
    limit_name: str | None = None
    limit_value: float | None = None
    observed_value: float | None = None
    payload: dict = field(default_factory=dict)
    exchange_ts: int | None = None
    received_ts: float | None = None


@dataclass
class SystemEventRecord:
    event_id: str
    instance_uid: str
    component: str
    event_type: str
    severity: str = "INFO"
    symbol: str | None = None
    payload: dict = field(default_factory=dict)
    strategy_version: str | None = None
    exchange_ts: int | None = None
    received_ts: float | None = None


@dataclass
class QuarantinedFillRecord:
    """A fill that could not be tied to a known order and position.

    Quarantined rather than attached to a best guess. The audit finding was a
    guess that looked like an answer.
    """

    quarantine_uid: str
    instance_uid: str
    reason: str
    payload: dict
    symbol: str | None = None
    order_uid: str | None = None
    position_uid: str | None = None
    exchange_ts: int | None = None
    received_ts: float | None = None


@dataclass
class FundingEventRecord:
    """One funding settlement charged to one position."""

    event_id: str
    instance_uid: str
    position_uid: str
    symbol: str
    side: int
    quantity: int
    exchange_ts: int
    funding_rate: float          # percent per interval
    mark_price: float
    notional: float
    funding_amount: float        # positive = paid by this position
    interval_seconds: int
    rate_source: str = "ticker"
    received_ts: float | None = None


class DuplicateRecord(Exception):
    """A uniqueness constraint rejected the write.

    Not an error condition in normal operation -- it is how idempotency is
    enforced after a restart replays work that was already durable.
    """
