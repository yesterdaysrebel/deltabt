"""Turn what the venue reports into the facts the database records.

WHY THIS IS NOT A FIELD RENAME

The paper bot's persistence path reads PaperPosition objects, which the
simulator built and therefore knew everything about: it chose the fill price,
so it knew the slippage; it closed the position, so it knew why. Against a real
venue half those facts come back from the exchange and half are ours, and the
interesting one -- WHY a position closed -- is reported by neither. It has to
be derived.

WHERE EACH FACT COMES FROM

    ours, at submit time      stop, target, risk_per_unit, notional,
                              equity_before, signal_key, strategy_version,
                              planned_r -- we chose these, so we record them
    the venue, afterwards     entry_price, exit_price, commission, realized
                              pnl, timestamps
    derived                   exit_reason

EXIT REASON, WHICH IS THE WHOLE PROBLEM

With exchange-held brackets the position closes without this process being
involved, so nothing in memory knows whether the stop fired, the target filled,
or we flattened it ourselves. `/v2/orders/history` answers it: the closing
order carries `stop_order_type`, which is `stop_loss_order` or
`take_profit_order` for a bracket leg and null for anything we sent directly.

Guessing instead -- comparing the exit price to the stop and target -- would be
wrong exactly when it matters. A stop that fills 0.66R past its trigger (which
the live paper arm has done) can land nearer the target than the stop on a
tight bracket, and a flatten at market can land anywhere.

THE SHAPES HERE WERE OBSERVED, NOT ASSUMED. Every field name below was read off
a real testnet fill and a real order-history row on 2026-09-15, because four
earlier rounds of believing the documentation cost four bugs.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Mapping

log = logging.getLogger(__name__)

#: `stop_order_type` on the closing order -> the reason we record.
#: Matches app.execution.paper_broker.ExitReason so both bots write the same
#: vocabulary and a report cannot tell them apart by accident.
_BRACKET_REASON = {
    "stop_loss_order": "STOP_LOSS",
    "take_profit_order": "TAKE_PROFIT",
}

#: What we call a close nobody can explain. Deliberately not "TIME_EXIT" or
#: any other real reason: an unexplained close must be visible as unexplained,
#: not quietly filed under a plausible heading.
UNKNOWN_REASON = "UNKNOWN_EXIT"


def _num(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_venue_time(value: Any) -> int | None:
    """Venue timestamps are ISO-8601 with a Z, e.g. 2026-09-15T06:55:54.634665Z.

    Returned as epoch seconds. `fills.created_at` and `orders.created_at` both
    use this; position rows elsewhere in the API use epoch microseconds, which
    is why nothing here guesses from the value's magnitude.
    """
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        text = str(value).replace("Z", "+00:00")
        return int(_dt.datetime.fromisoformat(text).timestamp())
    except ValueError:
        log.warning("unparseable venue timestamp %r", value)
        return None


def exit_reason(order: Mapping[str, Any], *, requested: str | None = None) -> str:
    """Why did this position close?

    `requested` is the reason WE asked for when we flattened deliberately --
    "time_exit", "setup_invalidated". It is used only when the venue confirms
    the close was not a bracket leg, so a bracket that fires while we are also
    trying to close is still recorded as the bracket.
    """
    kind = order.get("stop_order_type")
    if kind in _BRACKET_REASON:
        return _BRACKET_REASON[kind]
    if order.get("reduce_only") and requested:
        return requested.upper()
    if order.get("reduce_only"):
        return "MANUAL_CLOSE"
    return UNKNOWN_REASON


def close_facts(order: Mapping[str, Any], *,
                requested: str | None = None) -> dict[str, Any]:
    """The venue-side facts about a close, from an order-history row.

    `meta_data.pnl` is the venue's own realised figure and is preferred to
    anything recomputed here: it is what the account was actually credited,
    including the venue's rounding, and a number we derive that disagrees with
    the balance is worse than no number.
    """
    meta = order.get("meta_data") or {}
    pnl = meta.get("pnl")
    return {
        "exit_price": _num(order.get("average_fill_price")
                           or meta.get("avg_exit_price")),
        "exit_fee": _num(order.get("paid_commission") or order.get("commission")),
        "realized_pnl": _num(pnl) if pnl is not None else None,
        "closed_at": parse_venue_time(order.get("updated_at")
                                      or order.get("created_at")),
        "exit_reason": exit_reason(order, requested=requested),
        "exit_order_id": order.get("id"),
        "exit_client_order_id": order.get("client_order_id"),
    }


def entry_facts(order: Mapping[str, Any]) -> dict[str, Any]:
    """The venue-side facts about an entry, from an order-history row."""
    meta = order.get("meta_data") or {}
    return {
        "entry_price": _num(order.get("average_fill_price")
                            or meta.get("entry_price")),
        "entry_fee": _num(order.get("paid_commission") or order.get("commission")),
        "opened_at": parse_venue_time(order.get("created_at")),
        "filled": int(_num(order.get("size")) - _num(order.get("unfilled_size"))),
        "entry_order_id": order.get("id"),
    }


def r_multiple(entry: float, exit_: float, side: int,
               risk_per_unit: float) -> float | None:
    """R on the ACTUAL fills, which is the number the forward test measures.

    None rather than 0.0 when risk_per_unit is missing: a zero R reads as a
    scratch trade and would be averaged in as one.
    """
    if not risk_per_unit:
        return None
    return side * (exit_ - entry) / risk_per_unit


def find_by_client_order_id(rows, client_order_id: str) -> dict | None:
    """Our deterministic id is the link back from a venue order to our intent.

    The venue assigns its own `id`; `client_order_id` is the one we chose and
    can reconstruct after a crash, which is what makes attribution survive a
    restart.
    """
    for row in rows or []:
        if isinstance(row, Mapping) and row.get("client_order_id") == client_order_id:
            return dict(row)
    return None
