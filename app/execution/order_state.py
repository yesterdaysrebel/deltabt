"""The paper-order state machine.

AUDIT FINDING F3. ``PaperBroker._close`` built an exit fill with
``order_uid=new_uid("exit")`` -- an identifier with no row in ``paper_orders``.
The foreign key rejects it:

    ForeignKeyViolationError: insert or update on table "paper_fills"
    violates foreign key constraint "paper_fills_order_uid_fkey"

In practice it never even fired, because the drain loop only persisted
``kind == "FILL"`` and ``_close`` emitted ``POSITION_CLOSED``. So exit fills
were never written at all. The exit price and reason survived on the position
row, but the exit fee, the maker/taker classification and the tick timestamp
that proves stop-vs-target ordering did not.

The fix is not to loosen the foreign key. It is to give exits real orders, so
both sides of a trade exist at fill granularity and the lifecycle is complete:

    SIGNAL -> APPROVED -> ORDER_CREATED -> ORDER_FILLED -> POSITION_OPEN
           -> EXIT_SIGNAL -> EXIT_ORDER_CREATED -> EXIT_FILLED -> POSITION_CLOSED

HONEST SCOPE. ``SUBMITTED``, ``ACCEPTED`` and ``REJECTED`` are venue concepts:
they describe an order's passage through an exchange that V1 does not talk to.
They are defined here because the state machine should be complete and because
a future execution adapter will need them, but paper execution uses only
``NEW -> WORKING -> FILLED | CANCELLED | EXPIRED``. Which states are actually
reachable in V1 is asserted by a test, so the unused ones cannot quietly start
appearing in the forward-test record.
"""

from __future__ import annotations

from enum import Enum


class OrderStatus(str, Enum):
    NEW = "NEW"
    SUBMITTED = "SUBMITTED"              # venue-only
    WORKING = "WORKING"                  # accepted and live on the book
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"                # venue-only
    EXPIRED = "EXPIRED"


#: Once here, an order is finished. Nothing may move it out.
TERMINAL = frozenset({OrderStatus.FILLED, OrderStatus.CANCELLED,
                      OrderStatus.REJECTED, OrderStatus.EXPIRED})

#: An entry order in one of these states still HOLDS EXPOSURE: it has not
#: filled, so no position exists yet, but it can still create one. Counting
#: only positions is what let two entries pass a max_open_positions=1 gate
#: 1.1 seconds apart -- between the first approval and its fill there was an
#: approved order and no position, so the gate saw zero.
#:
#: Deliberately the complement of TERMINAL rather than a hand-listed set: a
#: state added later is reserving until someone declares it terminal, which is
#: the safe direction to be wrong in.
RESERVING = frozenset(s for s in OrderStatus if s not in TERMINAL)

#: States paper execution can actually produce. Enforced by a test so the
#: venue-only states cannot silently appear in the forward-test dataset.
PAPER_REACHABLE = frozenset({OrderStatus.NEW, OrderStatus.WORKING,
                             OrderStatus.FILLED, OrderStatus.CANCELLED,
                             OrderStatus.EXPIRED})

_ALLOWED: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.NEW: frozenset({
        OrderStatus.SUBMITTED, OrderStatus.WORKING, OrderStatus.CANCEL_REQUESTED,
        OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}),
    OrderStatus.SUBMITTED: frozenset({
        OrderStatus.WORKING, OrderStatus.REJECTED, OrderStatus.CANCELLED,
        OrderStatus.EXPIRED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED}),
    OrderStatus.WORKING: frozenset({
        OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED,
        OrderStatus.CANCEL_REQUESTED, OrderStatus.CANCELLED,
        OrderStatus.EXPIRED}),
    OrderStatus.PARTIALLY_FILLED: frozenset({
        OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED,
        OrderStatus.CANCEL_REQUESTED, OrderStatus.CANCELLED,
        OrderStatus.EXPIRED}),
    # A cancel request races the book. The fill can still win, and pretending
    # otherwise would lose a real fill.
    OrderStatus.CANCEL_REQUESTED: frozenset({
        OrderStatus.CANCELLED, OrderStatus.FILLED,
        OrderStatus.PARTIALLY_FILLED}),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}


class IllegalTransition(Exception):
    """An order was asked to move somewhere it cannot go.

    Raised rather than tolerated. A terminal order drifting back to active is
    how a cancelled order acquires a fill, and the forward-test dataset would
    then contain a trade that never happened.
    """

    def __init__(self, order_uid: str, current: OrderStatus,
                 requested: OrderStatus) -> None:
        super().__init__(
            f"order {order_uid} cannot move {current.value} -> "
            f"{requested.value}"
            + (" (terminal)" if current in TERMINAL else ""))
        self.order_uid, self.current, self.requested = order_uid, current, requested


def is_terminal(status: OrderStatus) -> bool:
    return status in TERMINAL


def holds_exposure(status: OrderStatus) -> bool:
    """True if an entry order in this state can still become a position."""
    return status in RESERVING


def can_transition(current: OrderStatus, requested: OrderStatus) -> bool:
    """True if the move is legal. Re-entering the same state is not a move."""
    if current == requested:
        return True                       # idempotent redelivery
    return requested in _ALLOWED[current]


def transition(order_uid: str, current: OrderStatus,
               requested: OrderStatus) -> OrderStatus:
    """Apply a transition, or raise.

    Re-applying the state an order is already in is a no-op rather than an
    error: a duplicate exchange event must be idempotent, not fatal.
    """
    if current == requested:
        return current
    if not can_transition(current, requested):
        raise IllegalTransition(order_uid, current, requested)
    return requested
