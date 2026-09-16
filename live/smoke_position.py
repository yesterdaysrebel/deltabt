"""Open a REAL position on testnet, then prove poll() and reconcile() see it.

    DELTA_API_KEY=... DELTA_API_SECRET=... \
        python -m live.smoke_position BTCUSD

WHY A SECOND SCRIPT

`smoke_testnet.py` proves the CLIENT works: signing, placing, looking up,
cancelling. It never opens a position, so it never exercises the two pieces
that decide whether this system can be trusted with one:

  * LiveBroker.poll() -- the diff that turns venue state into events. Its unit
    tests feed it a FakeClient returning dicts I wrote. That proves the diff
    logic and nothing about the shape of a real position row.
  * reconcile() -- which is the whole safety story, and has likewise only ever
    seen dicts of my own construction.

Every field name in both came from documentation. Testnet has already caught
four places where I believed the wrong thing about this API, so neither is
trustworthy until a real position has passed through them.

THE STEP THAT MATTERS MOST is 6: with a REAL open position at the venue and an
EMPTY ledger, reconcile() must say HALT / UNKNOWN_POSITION. That is the crash
case -- the venue holds something we have no record of -- and if it does not
fire against real data then reconciliation is decoration.

SAFETY

Refuses prod. One contract, the minimum. Brackets are set far enough away
that they cannot fire during the run, because the point here is the position
lifecycle and not the exits. The close is in a `finally`, and if it fails the
script says so loudly and names the position -- an open position left behind on
testnet costs nothing, but leaving one SILENTLY is the habit that is expensive
later.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from decimal import Decimal

from live.auth import Credentials
from live.broker import LiveBroker
from live.client import PROD, TESTNET, LiveClient, VenueError
from live.reconcile import Verdict, reconcile

#: How far the brackets sit from entry, as a fraction of price. Wide enough
#: not to fire in the seconds this takes.
BRACKET_AWAY = Decimal("0.25")


@dataclass
class _Intent:
    """The attributes LiveBroker.submit_order reads.

    Deliberately NOT an ApprovedOrderIntent: that type refuses to exist
    without a risk_evaluation_id, and manufacturing a fake one to satisfy a
    smoke test would be exactly the wrong lesson. This is a probe, not a
    trade the risk engine approved, and the type says so.
    """

    symbol: str
    side: int
    quantity: int
    order_type: str
    limit_price: float | None
    stop_price: float
    target_price: float
    risk_per_unit: float
    intent_id: str
    signal_key: str
    #: Required since leverage is chosen per trade from the stop distance
    #: (LiveBroker._set_safe_leverage); without it the probe is refused.
    entry_reference: float = 0.0


def step(n: int, what: str) -> None:
    print(f"\n[{n}] {what}", flush=True)


def main(argv: list[str]) -> int:
    symbol = (argv[1] if len(argv) > 1 else "BTCUSD").upper()
    key = os.environ.get("DELTA_API_KEY", "")
    secret = os.environ.get("DELTA_API_SECRET", "")
    if not key or not secret:
        print("set DELTA_API_KEY and DELTA_API_SECRET (TESTNET keys)",
              file=sys.stderr)
        return 2

    base = os.environ.get("DELTA_BASE_URL", TESTNET)
    if base == PROD or "testnet" not in base:
        print(f"refusing to run against {base!r}: testnet only.\n"
              "This script OPENS A POSITION.", file=sys.stderr)
        return 2

    client = LiveClient(Credentials(key=key, secret=secret, label="testnet"),
                        base_url=base)
    print(f"client: {client}")

    step(1, f"find {symbol}")
    product = None
    for row in client._read("/v2/products",
                            {"contract_types": "perpetual_futures",
                             "page_size": 200}) or []:
        if row.get("symbol") == symbol:
            product = row
            break
    if product is None:
        print(f"    {symbol} is not a live perpetual here", file=sys.stderr)
        return 1
    pid = int(product["id"])
    tick = Decimal(str(product.get("tick_size") or "0.1"))
    ticker = client._read(f"/v2/tickers/{symbol}") or {}
    mark = Decimal(str(ticker.get("mark_price") or 0))
    print(f"    product_id={pid} mark={mark} tick={tick}")

    broker = LiveBroker(client, product_ids={symbol: pid},
                        experiment_id="SMOKE", tick_size={symbol: tick})

    step(2, "reconcile BEFORE doing anything -- the account should be flat")
    before = reconcile(client.get_positions(), [], symbol_for={pid: symbol})
    if not before.ok:
        print("    account is not flat; this script needs a clean account so "
              "the position it opens is unambiguous:", file=sys.stderr)
        print("    " + before.render().replace("\n", "\n    "), file=sys.stderr)
        return 1
    print("    flat")

    stop = mark * (Decimal(1) - BRACKET_AWAY)
    target = mark * (Decimal(1) + BRACKET_AWAY)
    intent = _Intent(symbol=symbol, side=1, quantity=1, order_type="market",
                     limit_price=None, stop_price=float(stop),
                     target_price=float(target),
                     risk_per_unit=float(mark - stop),
                     entry_reference=float(mark),
                     intent_id=f"smoke-{os.getpid()}",
                     signal_key=f"{symbol}:smoke")

    step(3, f"open 1 contract at market, bracket {stop:.1f} / {target:.1f}")
    placed = broker.submit_order(intent)
    print(f"    order id={placed.get('id')} state={placed.get('state')}")

    opened = False
    try:
        step(4, "poll() -- does the broker see the venue's position?")
        events = broker.poll()
        print(f"    events: {[(e.kind, e.symbol) for e in events]}")
        if not any(e.kind == "POSITION_OPENED" for e in events):
            print("    *** poll() did not report POSITION_OPENED. Either the "
                  "order did not fill, or the position row is not shaped the "
                  "way LiveBroker reads it.", file=sys.stderr)
            print(f"    raw positions: {client.get_positions()}", file=sys.stderr)
            return 1
        opened = True
        pos = broker.positions[symbol]
        print(f"    position: side={pos.side} contracts={pos.contracts} "
              f"entry={pos.entry_price}")

        step(5, "reconcile with a ledger that AGREES -- expect AGREED")
        ours = [{"symbol": symbol, "size": pos.side * pos.contracts}]
        agreed = reconcile(client.get_positions(), ours, symbol_for={pid: symbol})
        print(f"    {agreed.verdict.value}: {agreed.render()}")
        if not agreed.ok:
            print("    *** a matching ledger did not reconcile", file=sys.stderr)
            return 1

        step(6, "reconcile with an EMPTY ledger -- expect HALT, the crash case")
        halt = reconcile(client.get_positions(), [], symbol_for={pid: symbol})
        if halt.verdict is not Verdict.HALT:
            print("    *** reconciliation did NOT flag a real position the "
                  "ledger knows nothing about. This is the case it exists for; "
                  "if it does not fire here it is decoration.", file=sys.stderr)
            return 1
        kinds = [d.kind for d in halt.discrepancies]
        print(f"    HALT as required: {kinds}")
        if "UNKNOWN_POSITION" not in kinds:
            print(f"    *** wrong classification: {kinds}", file=sys.stderr)
            return 1

    finally:
        if opened:
            step(7, "close it (reduce_only)")
            try:
                out = broker.close_position(symbol, "smoke test complete")
                print(f"    close order id={out.get('id')}")
            except VenueError as exc:
                print(f"    *** CLOSE FAILED: {exc}", file=sys.stderr)
                print(f"    *** CLOSE THE {symbol} POSITION BY HAND",
                      file=sys.stderr)
                return 1

    step(8, "poll() again -- does it see the close?")
    events = broker.poll()
    print(f"    events: {[(e.kind, e.symbol) for e in events]}")
    if not any(e.kind == "POSITION_CLOSED" for e in events):
        print("    *** poll() did not report POSITION_CLOSED; the position may "
              "still be open. CHECK BY HAND.", file=sys.stderr)
        return 1

    step(9, "reconcile at the end -- flat again")
    after = reconcile(client.get_positions(), [], symbol_for={pid: symbol})
    if not after.ok:
        print("    *** not flat after the close:", file=sys.stderr)
        print("    " + after.render().replace("\n", "\n    "), file=sys.stderr)
        return 1
    print("    flat")

    print("\nposition lifecycle, poll() and reconcile() all work against the "
          "real venue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
