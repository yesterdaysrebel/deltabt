"""Compare what the venue holds against what we think we hold.

THE ASYMMETRY THAT MAKES THIS NECESSARY

PaperBroker's positions ARE the positions: it simulates the venue, so its
memory cannot disagree with reality. Against a real exchange that stops being
true the moment anything is interrupted -- a container OOM between the fill and
the database write, the RDS maintenance reboot that blinded the paper bot for
90 seconds on 2026-09-13, a deploy landing mid-order.

After any of those the venue holds a position and we may not know it. The
danger is not the outage; it is trading on afterwards with a wrong picture.

WHAT THIS DOES NOT DO: FIX ANYTHING

It is tempting to have a mismatch auto-flatten. That is how a reconciliation
bug becomes a market order. A position the venue reports and we do not
recognise might be ours from before a crash, might be a manual trade someone
placed, or might be this code misreading a field -- and "close it" is the
wrong answer to two of those three. So this classifies and reports, and the
caller HALTS. A human decides.

VENUE IS TRUTH, IN ONE DIRECTION ONLY

Where the two disagree the venue is right about WHAT IS HELD -- it is the thing
holding it. It is not right about what we intended, which is why a venue-only
position is an alarm rather than something to adopt into the ledger.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

log = logging.getLogger(__name__)

#: Contract counts are integers, but sizes arrive as strings or floats
#: depending on the endpoint. Compared as integers after an explicit cast so a
#: float that is 2.9999999 never silently reads as 2.
def _size(value: Any) -> int:
    if value in (None, ""):
        return 0
    return int(round(float(value)))


class Verdict(str, enum.Enum):
    #: Venue and ledger agree. Trading may continue.
    AGREED = "AGREED"
    #: They disagree. The bot must not trade until a human has looked.
    HALT = "HALT"


@dataclass(frozen=True)
class Discrepancy:
    kind: str
    symbol: str
    venue_size: int
    ledger_size: int
    detail: str

    def __str__(self) -> str:  # pragma: no cover - formatting
        return (f"{self.kind} {self.symbol}: venue {self.venue_size:+d} vs "
                f"ledger {self.ledger_size:+d} -- {self.detail}")


@dataclass
class Reconciliation:
    verdict: Verdict
    discrepancies: list[Discrepancy] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return self.verdict is Verdict.AGREED

    def render(self) -> str:
        if self.ok:
            return f"reconciled: {self.checked} symbol(s), venue and ledger agree"
        lines = [f"RECONCILIATION FAILED -- {len(self.discrepancies)} "
                 f"discrepancy(ies) across {self.checked} symbol(s):"]
        lines += [f"  {d}" for d in self.discrepancies]
        lines.append("The bot must not trade until these are resolved by hand. "
                     "Nothing has been closed or adopted automatically.")
        return "\n".join(lines)


def _signed(position: Mapping[str, Any]) -> int:
    """A venue position as a signed contract count.

    Delta reports `size` already signed for perpetuals (negative is short). A
    `side` field is honoured when present rather than assumed, because reading
    an unsigned size as a long is the error that turns a short into a
    phantom double position.
    """
    size = _size(position.get("size"))
    side = str(position.get("side") or "").lower()
    if side == "sell" and size > 0:
        return -size
    if side == "buy" and size < 0:
        return abs(size)
    return size


def reconcile(venue_positions: Iterable[Mapping[str, Any]],
              ledger_positions: Iterable[Mapping[str, Any]],
              *, symbol_for: Mapping[int, str] | None = None) -> Reconciliation:
    """Classify every disagreement between the venue and our own records.

    `venue_positions` are rows from `LiveClient.get_positions()`.
    `ledger_positions` are our open positions: dicts with `symbol` and a
    signed `size` (or `contracts` plus `side`).
    `symbol_for` maps product_id -> symbol, because the venue identifies
    positions by product and we reason in symbols.
    """
    symbol_for = symbol_for or {}

    venue: dict[str, int] = {}
    for row in venue_positions:
        size = _signed(row)
        if size == 0:
            continue  # a flat row is not a position
        sym = row.get("product_symbol") or symbol_for.get(
            _size(row.get("product_id")))
        if not sym:
            # A position we cannot even name is the most dangerous kind: it
            # cannot be matched, so it must not be silently dropped.
            sym = f"product_id={row.get('product_id')}"
        venue[sym] = venue.get(sym, 0) + size

    ledger: dict[str, int] = {}
    for row in ledger_positions:
        sym = row.get("symbol")
        if not sym:
            continue
        if "size" in row and row.get("size") is not None:
            size = _size(row["size"])
        else:
            size = _size(row.get("contracts"))
            if str(row.get("side", "")).upper() in {"SHORT", "SELL", "-1"}:
                size = -abs(size)
        if size == 0:
            continue
        ledger[sym] = ledger.get(sym, 0) + size

    out: list[Discrepancy] = []
    for sym in sorted(set(venue) | set(ledger)):
        v, l = venue.get(sym, 0), ledger.get(sym, 0)
        if v == l:
            continue
        if l == 0:
            out.append(Discrepancy(
                "UNKNOWN_POSITION", sym, v, l,
                "the venue holds a position this bot has no record of. It may "
                "predate a crash, or be a manual trade. NOT closed "
                "automatically -- closing someone else's position is worse "
                "than stopping."))
        elif v == 0:
            out.append(Discrepancy(
                "VANISHED_POSITION", sym, v, l,
                "we believe we hold this and the venue does not. It was "
                "probably closed by an exchange-held bracket, or liquidated, "
                "while we were not watching. The exit needs recording before "
                "the ledger means anything."))
        elif (v > 0) != (l > 0):
            out.append(Discrepancy(
                "WRONG_DIRECTION", sym, v, l,
                "venue and ledger disagree on DIRECTION. Treat every number "
                "in the ledger as suspect until this is explained."))
        else:
            out.append(Discrepancy(
                "SIZE_MISMATCH", sym, v, l,
                "same direction, different size -- a partial fill that was "
                "never recorded, or a partial close."))

    checked = len(set(venue) | set(ledger))
    verdict = Verdict.AGREED if not out else Verdict.HALT
    result = Reconciliation(verdict, out, checked)
    (log.info if result.ok else log.error)("%s", result.render())
    return result
