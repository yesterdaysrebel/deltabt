"""Order requests, and the idempotency that stops a retry becoming a double fill.

THE FAILURE THIS MODULE EXISTS TO PREVENT

    client sends POST /v2/orders
    venue accepts it, opens a position
    the response is lost -- socket reset, ALB timeout, container OOM
    client retries
    venue accepts it again

That is two positions where the risk engine authorised one, and no amount of
local bookkeeping detects it, because the client never learned about the first
fill. `app/persistence` already learned this lesson for its own writes -- its
idempotency is "a UNIQUE CONSTRAINT, not an in-memory set", because "that set
is empty exactly when duplicates matter most" (a fresh process after a crash).

The venue-side equivalent is `client_order_id`. It must be:

  * DETERMINISTIC from the intent, so the retry carries the SAME id and the
    venue rejects it as a duplicate rather than filling it;
  * UNIQUE across intents, so two legitimately different orders never collide;
  * STABLE ACROSS PROCESS RESTARTS, so a bot that dies mid-send and comes back
    reconstructs the same id and can ask "did this one land?" instead of
    guessing.

A random uuid fails the first and third. A counter fails the third. So the id
is DERIVED -- a hash of the things that identify the intent, which the caller
already has in its database.

NUMBERS ARE STRINGS. The venue documents this for price fields to preserve
precision. `Decimal` in, string out, no float ever touches a price. BEATUSD
trades at 0.0939812 with a 1e-7 tick; float rounding is not theoretical here.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

#: Venue cap on the field. Delta accepts a free-form string; we keep it short
#: and alphanumeric so it survives any logging or URL-encoding in between.
CLIENT_ORDER_ID_BYTES = 16

#: HOW FAR BEYOND THE STOP THE EXIT LIMIT SITS, in R.
#:
#: Delta offers NO slippage protection on a market order and has no BBO or
#: pegged order type, so a marketable limit is the only way to put a ceiling
#: on how bad a stop fill can be. On 2026-01-19 BEATUSD fell 18% in two
#: minutes and a market stop filled 6.83R from entry on a 1.0R stop.
#:
#: 1.5R is chosen to be INSURANCE, not a strategy change. Measured over 265
#: stop events on the corrected engine it binds on 2 of them, moves the mean
#: by +0.0000R, improves the worst trade from -1.97R to -1.73R, and -- the
#: reason for this number rather than a tighter one -- gives an IDENTICAL
#: answer under both fill-rate assumptions (t=+0.02 either way), so it does
#: not depend on the one quantity 1m candles cannot resolve.
#:
#: Tighter caps are NOT free: 0.25R-1.0R all measured slightly negative. They
#: bind too often to be insurance and are not tight enough to be the
#: stop-limit bet (that one is worth +0.036R with t=+7.42 optimistically and
#: +0.001R pessimistically, and needs tick telemetry before it is taken).
#: Do not tighten this without that data.
STOP_LIMIT_CAP_R = Decimal("1.5")


class Side(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"

    @classmethod
    def for_position(cls, direction: int) -> "Side":
        """+1 (long) opens with a buy; -1 (short) opens with a sell."""
        if direction not in (1, -1):
            raise ValueError(f"direction must be +1 or -1, got {direction!r}")
        return cls.BUY if direction > 0 else cls.SELL

    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(str, enum.Enum):
    LIMIT = "limit_order"
    MARKET = "market_order"


class TimeInForce(str, enum.Enum):
    GTC = "gtc"
    IOC = "ioc"


class StopTriggerMethod(str, enum.Enum):
    """Which price the venue watches to trigger a stop.

    This is a VENUE-NATIVE setting, and it is the same question the research
    in docs/ spent a long time on: the paper engine triggers stops on MARK
    because that is Delta's default, while fills happen at LAST TRADED, which
    is where the overshoot comes from. Here it is one field.

    Do not change it away from MARK_PRICE to match a backtest without
    re-running that backtest -- the recorded arm is a mark-triggered arm.
    """

    MARK = "mark_price"
    LAST_TRADED = "last_traded_price"
    SPOT = "spot_price"


def client_order_id(*parts: str) -> str:
    """A deterministic venue-side idempotency key for one intent.

    Pass the things that identify the intent and never change for it -- for
    example the experiment id, the position uid and the leg ("entry"/"exit").
    The same inputs always produce the same id, including in a brand new
    process after a crash, which is exactly when it matters.

    It is a hash rather than a join because the inputs are internal uids that
    are longer than the field should carry, and because a hash cannot
    accidentally leak an account or strategy name to the venue.
    """
    if not parts or any(not p for p in parts):
        raise ValueError(f"every id part must be non-empty, got {parts!r}")
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest[:CLIENT_ORDER_ID_BYTES]


def _price(value: Decimal | str | None) -> str | None:
    """Prices cross the wire as strings. Floats are refused, not coerced."""
    if value is None:
        return None
    if isinstance(value, float):
        raise TypeError(
            "prices must be Decimal or str, never float -- float rounding at a "
            "1e-7 tick size silently moves the order")
    return str(Decimal(value))


@dataclass(frozen=True)
class OrderRequest:
    """One order, ready to be signed and sent.

    `bracket_stop_loss_price` / `bracket_take_profit_price` attach the exit
    legs TO THE ENTRY, so the venue holds them. That matters more live than it
    reads: if this process dies, or loses its database the way the paper bot
    did during the RDS failover on 2026-09-13, an exchange-held bracket still
    protects the position. A bot-managed stop does not.
    """

    product_id: int
    size: int
    side: Side
    order_type: OrderType
    client_order_id: str
    limit_price: Decimal | str | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    post_only: bool = False
    #: Exchange-held bracket legs, in price terms.
    bracket_stop_loss_price: Decimal | str | None = None
    #: The marketable-limit CAP on the stop leg. The stop still triggers at
    #: `bracket_stop_loss_price`; this is the worst price it may fill at. See
    #: STOP_LIMIT_CAP_R. Leave None for a market stop (no protection).
    bracket_stop_loss_limit_price: Decimal | str | None = None
    bracket_take_profit_price: Decimal | str | None = None
    stop_trigger_method: StopTriggerMethod = StopTriggerMethod.MARK
    #: Free-form, for our own audit trail. Not sent to the venue.
    note: str = ""

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError(f"size must be positive, got {self.size}")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("a limit order requires limit_price")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("a market order must not carry limit_price")
        if self.post_only and self.order_type is OrderType.MARKET:
            raise ValueError("post_only is meaningless on a market order")
        if self.post_only and self.time_in_force is TimeInForce.IOC:
            raise ValueError("post_only with IOC can only ever cancel")
        # Reject floats early, at construction, rather than at send.
        for name in ("limit_price", "bracket_stop_loss_price",
                     "bracket_take_profit_price"):
            _price(getattr(self, name))

    def to_payload(self) -> dict[str, Any]:
        """The request body. Only fields that are set are included."""
        payload: dict[str, Any] = {
            "product_id": self.product_id,
            "size": self.size,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "time_in_force": self.time_in_force.value,
            "client_order_id": self.client_order_id,
        }
        if self.limit_price is not None:
            payload["limit_price"] = _price(self.limit_price)
        if self.reduce_only:
            payload["reduce_only"] = True
        if self.post_only:
            payload["post_only"] = True
        if self.bracket_stop_loss_price is not None:
            payload["bracket_stop_loss_price"] = _price(self.bracket_stop_loss_price)
        if self.bracket_stop_loss_limit_price is not None:
            payload["bracket_stop_loss_limit_price"] = _price(
                self.bracket_stop_loss_limit_price)
        if self.bracket_take_profit_price is not None:
            payload["bracket_take_profit_price"] = _price(self.bracket_take_profit_price)
        if (self.bracket_stop_loss_price is not None
                or self.bracket_take_profit_price is not None):
            payload["stop_trigger_method"] = self.stop_trigger_method.value
        return payload
