"""Fill identity and fill -> position aggregation.

AUDIT FINDING F1. ``TradingBot._persist_fill`` resolved the position for a fill
by scanning for the first one matching the symbol. Closed positions are never
removed from the broker's map, so from the second trade in a symbol onward that
matched a *previously closed* position and copied its ``side`` and
``quantity``. A short was recorded as a long.

Two things were wrong, and fixing only the obvious one would have made the
system worse:

1. **The association was inferred rather than stated.** Fixed by carrying
   ``position_uid`` on the fill itself, from the broker that already knows it.

2. **Fill identity was random** (``new_uid("fill")``), so the only thing
   preventing a replayed fill from being inserted twice was a
   ``UNIQUE(order_uid)`` index -- which simultaneously forbade an order from
   ever having a second fill. Relaxing that index to allow multiple fills,
   without first making identity deterministic, would have silently removed
   replay protection.

Everything here is a pure function so the invariants can be tested directly,
independently of the broker or the database.

SCOPE NOTE, stated rather than left implicit: V1's paper broker fills whole
orders and emits exactly one fill per order. It has no partial-fill model and
this phase does not add one, because that would change execution semantics. The
functions below are correct for many fills per order, and are tested that way,
so the persistence and association layer is ready if a future execution adapter
produces partial fills.
"""

from __future__ import annotations

from dataclasses import dataclass


def fill_uid(order_uid: str, seq: int) -> str:
    """Deterministic identity for the ``seq``-th fill of an order.

    Replaying the same fill produces the same identifier, so the database's
    UNIQUE constraint rejects it. A random identifier would sail straight
    through, which is exactly what made the old ``UNIQUE(order_uid)`` index
    load-bearing for two unrelated jobs at once.
    """
    if seq < 1:
        raise ValueError(f"fill sequence starts at 1, got {seq}")
    return f"{order_uid}:f{seq}"


@dataclass(frozen=True)
class Allocation:
    """Aggregate of a set of fills against one order or position."""

    quantity: int
    avg_price: float
    fee: float
    slippage: float
    fills: int
    first_exchange_ts: int | None
    last_exchange_ts: int | None

    @property
    def notional_at_avg(self) -> float:
        return self.quantity * self.avg_price


def aggregate(fills) -> Allocation:
    """Combine fills into one position-level allocation.

    Order-independent by construction: quantity is a sum and price is a
    quantity-weighted mean, both commutative. That is what makes out-of-order
    delivery safe -- the result cannot depend on arrival sequence, so there is
    no need to buffer or re-sort, and a late fill simply joins the average.

    Duplicates are removed by ``fill_uid`` first. Passing the same fill twice
    must not move the average, which is the in-memory half of the same
    guarantee the UNIQUE constraint gives in the database.
    """
    seen: dict[str, object] = {}
    for f in fills:
        uid = getattr(f, "fill_uid", None) or f["fill_uid"]
        if uid not in seen:
            seen[uid] = f

    qty = 0
    notional = 0.0
    fee = 0.0
    slip = 0.0
    stamps: list[int] = []
    for f in seen.values():
        q = int(_get(f, "quantity"))
        px = float(_get(f, "price"))
        qty += q
        notional += q * px
        fee += float(_get(f, "fee", 0.0) or 0.0)
        slip += float(_get(f, "slippage", 0.0) or 0.0)
        ts = _get(f, "exchange_ts", None) or _get(f, "filled_at", None)
        if ts:
            stamps.append(int(ts))

    return Allocation(
        quantity=qty,
        avg_price=(notional / qty) if qty else 0.0,
        fee=fee,
        slippage=slip,
        fills=len(seen),
        first_exchange_ts=min(stamps) if stamps else None,
        last_exchange_ts=max(stamps) if stamps else None,
    )


def _get(obj, name: str, default=...):
    if isinstance(obj, dict):
        return obj[name] if default is ... else obj.get(name, default)
    return getattr(obj, name) if default is ... else getattr(obj, name, default)


class UnmatchedFill(Exception):
    """A fill that cannot be tied to a known order and open position.

    Raised rather than guessed. The audit finding was precisely a guess that
    looked like an answer, so the correct behaviour for an unresolvable fill is
    to quarantine it and shout, never to attach it to whatever happens to be
    nearby.
    """

    def __init__(self, reason: str, payload: dict) -> None:
        super().__init__(reason)
        self.reason = reason
        self.payload = payload


def resolve_position(payload: dict, positions: dict) -> str:
    """Position a fill belongs to, or raise.

    ``positions`` maps ``position_uid`` -> position. The fill must name its
    position and that position must exist; a symbol match is not evidence.
    """
    uid = payload.get("position_uid")
    if not uid:
        raise UnmatchedFill("fill does not name a position_uid", payload)
    pos = positions.get(uid)
    if pos is None:
        raise UnmatchedFill(f"position {uid} is not known to this instance",
                            payload)
    sym = payload.get("symbol")
    if sym and getattr(pos, "symbol", sym) != sym:
        raise UnmatchedFill(
            f"fill symbol {sym} does not match position {uid} "
            f"({getattr(pos, 'symbol', '?')})", payload)
    return uid
