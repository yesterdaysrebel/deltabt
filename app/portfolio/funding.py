"""Funding settlement for open paper positions.

AUDIT FINDING F4. The `positions.funding` column existed, `deltabt.costs`
modelled snapshot funding, and `FUNDING:<SYMBOL>` history was available -- but
nothing in the live loop ever charged it. Any position held across a settlement
understated its cost.

THE MODEL IS THE FROZEN RESEARCH MODEL. Nothing here is invented:

* **Snapshot, not pro-rata.** Delta charges the full interval to whatever is
  open at the settlement instant. A position opened one second before pays in
  full; one closed one second before pays nothing. `deltabt.costs.funding_charge`
  and `deltabt.engine` both work this way, and so does this.
* **The grid is anchored to the UTC epoch**, not to when a position opened, so
  8h symbols settle at 00:00 / 08:00 / 16:00 UTC. Same as
  `deltabt.costs.funding_timestamps`.
* **The interval is per symbol**, read from the product specification. Delta
  runs 8h on roughly 80 perps and 4h on roughly 140; assuming 8h globally would
  be wrong for most of the venue.
* **The rate is a PERCENT per interval**, matching the `FUNDING:` candle series.
  Verified against the live feed before this was written: the `v2/ticker`
  `funding_rate` and the research series agree in scale (BTCUSD 0.0100 live
  against a -0.0274 series value, both in the same 0.01-0.05 band), and BTCUSD
  sitting at exactly 0.01 corroborates the +/-0.01% pin the research measured on
  21-44% of observations. Reading it as a fraction would overstate every charge
  by 100x.
* **Sign convention: positive means PAID by this position.** A long pays when
  funding is positive. `cash = side * notional * rate/100`.

DOCUMENTED LIMITATION, stated rather than papered over. Delta's public feed
carries the *current* funding rate on `v2/ticker`; it publishes no
"funding applied" event naming the rate actually charged at a settlement. So
the rate used here is the last one observed before the settlement instant, and
the mark price is likewise the last one seen. For an 8h interval on a rate that
moves slowly this is close, but it is an approximation and the stored event
records `rate_source` so the reconstruction later is honest about it. Exactness
would need an authenticated endpoint, which V1 deliberately does not have.
"""

from __future__ import annotations

from dataclasses import dataclass


def settlement_grid(start: int, end: int, interval: int) -> list[int]:
    """Settlement instants in ``(start, end]``, anchored to the UTC epoch.

    Half-open at the start so a settlement is never charged twice when this is
    called repeatedly with a moving window, and closed at the end so a position
    open exactly at the instant is charged.
    """
    if interval <= 0 or end <= start:
        return []
    first = ((start // interval) + 1) * interval
    return list(range(first, end + 1, interval))


def funding_amount(side: int, quantity: int, contract_value: float,
                   mark_price: float, rate_percent: float) -> float:
    """Cash flow for one settlement. Positive means this position PAID.

    Mirrors ``deltabt.costs.funding_charge`` exactly; kept as its own function
    only so the live path can be tested without constructing a SymbolCosts.
    """
    notional = abs(quantity) * contract_value * mark_price
    return side * notional * (rate_percent / 100.0)


def funding_event_id(position_uid: str, settlement_ts: int) -> str:
    """Deterministic, so a restart across a settlement cannot double-charge."""
    return f"{position_uid}:fund:{settlement_ts}"


@dataclass(frozen=True)
class FundingSettlement:
    """One charge against one position at one settlement instant."""

    event_id: str
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

    @property
    def paid(self) -> bool:
        return self.funding_amount > 0


def settlements_for_position(
    *,
    position_uid: str,
    symbol: str,
    side: int,
    quantity: int,
    contract_value: float,
    opened_at: int,
    checked_through: int,
    now: int,
    interval: int,
    rate_percent: float,
    mark_price: float,
    rate_source: str = "ticker",
) -> list[FundingSettlement]:
    """Every settlement this position has crossed since it was last checked.

    ``checked_through`` is the watermark. Advancing it only after the events
    are durable is what makes a restart mid-settlement safe: the work is
    redone, and the deterministic event id makes the redo a no-op.
    """
    since = max(checked_through, opened_at)
    out: list[FundingSettlement] = []
    for ts in settlement_grid(since, now, interval):
        amount = funding_amount(side, quantity, contract_value, mark_price,
                                rate_percent)
        out.append(FundingSettlement(
            event_id=funding_event_id(position_uid, ts),
            position_uid=position_uid, symbol=symbol, side=side,
            quantity=quantity, exchange_ts=ts, funding_rate=rate_percent,
            mark_price=mark_price,
            notional=abs(quantity) * contract_value * mark_price,
            funding_amount=amount, interval_seconds=interval,
            rate_source=rate_source))
    return out
