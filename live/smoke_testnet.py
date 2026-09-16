"""Prove the adapter against Delta's TESTNET. Places one real order.

    DELTA_API_KEY=... DELTA_API_SECRET=... \
        python -m live.smoke_testnet BTCUSD

WHAT THIS IS FOR

Every byte of live/auth.py and live/orders.py was written from documentation,
not from a live call. Unit tests prove the code does what I believe the venue
wants; they cannot prove I believe the right thing. Three assumptions can only
be settled by asking the venue:

  1. THE SIGNATURE. method + timestamp + path + query + body, HMAC-SHA256. If
     the concatenation order or the exact query/body bytes are wrong, every
     authenticated call fails with an error that reads like bad credentials.
  2. THE LOOKUP BY client_order_id. `GET /v2/orders/client-oid` is what the
     whole double-fill protection rests on: after an unobserved write, this is
     the only way to learn whether an order landed. It was implemented from a
     navigation entry, and its parameter name is a guess until this runs.
  3. THE ORDER PAYLOAD. Field names, prices-as-strings, and whether the
     bracket fields are accepted on entry.

WHAT IT DELIBERATELY DOES NOT DO

No strategy, no position management, no reconciliation. This exercises the
execution primitives and nothing else. A bot that could trade testnet
end-to-end would need the wiring that does not exist yet -- app/runtime/bot.py
drives PaperBroker and cannot import this package, by design.

SAFETY

Refuses to run against prod. Places ONE order, the minimum size, priced far
enough from the market that it should rest rather than fill, and cancels it.
If anything fails mid-way the order id is printed so it can be cancelled by
hand -- an orphaned resting order on testnet is harmless, but leaving one
silently is the habit that becomes expensive on prod.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

from live.auth import Credentials
from live.client import PROD, TESTNET, LiveClient, VenueError
from live.orders import (OrderRequest, OrderType, Side, TimeInForce,
                         client_order_id)

#: How far from the market to rest the probe order, as a fraction. Far enough
#: that it will not fill during the run; close enough that the venue does not
#: reject it as absurd.
AWAY = Decimal("0.5")


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
        print(f"refusing to smoke-test against {base!r}: testnet only.\n"
              "This script places a real order.", file=sys.stderr)
        return 2

    creds = Credentials(key=key, secret=secret, label="testnet")
    client = LiveClient(creds, base_url=base)
    print(f"client: {client}")   # redacted key, never the secret

    step(1, "authenticated read -- proves the signature is right")
    balances = client.get_balance()
    for b in balances[:5]:
        print(f"    {b.get('asset_symbol', '?'):6} "
              f"balance={b.get('balance', '?')} "
              f"available={b.get('available_balance', '?')}")

    step(2, "open positions (the source of truth reconciliation will use)")
    # NOT fatal. Steps 4 and 5 are the ones that can only be answered here --
    # whether an order places, and whether it can be found again afterwards.
    # Losing those to a positions hiccup wastes the run.
    try:
        positions = client.get_positions()
        print(f"    {len(positions)} position(s)")
        for p in positions[:5]:
            print(f"    product={p.get('product_id')} size={p.get('size')} "
                  f"entry={p.get('entry_price')}")
    except VenueError as exc:
        print(f"    *** FAILED: {exc}")
        print("    *** reconciliation cannot enumerate positions. Continuing, "
              "because steps 4-5 still need answering -- but this must be "
              "fixed before anything trades.")

    step(3, f"find {symbol} and its mark price")
    # FILTER BY CONTRACT TYPE. Unfiltered, the first 500 rows on testnet are
    # all `move_options` and BTCUSD -- which exists, id 84 -- is nowhere in
    # them. An unfiltered page read reports "not found" for a product that is
    # live and trading.
    #
    # And the key is `id`, not `product_id`. The products endpoint names it
    # one way; positions, orders and tickers name it the other.
    product = None
    for row in client._read("/v2/products",
                            {"contract_types": "perpetual_futures",
                             "page_size": 200}) or []:
        if row.get("symbol") == symbol:
            product = row
            break
    if product is None:
        print(f"    {symbol} is not a live perpetual on this venue",
              file=sys.stderr)
        return 1
    pid = int(product["id"])
    tick = Decimal(str(product.get("tick_size") or "0.5"))
    ticker = client._read(f"/v2/tickers/{symbol}") or {}
    mark = Decimal(str(ticker.get("mark_price") or ticker.get("close") or 0))
    if mark <= 0:
        print("    no mark price available", file=sys.stderr)
        return 1
    print(f"    product_id={pid} mark={mark} tick={tick}")

    # A BUY far BELOW the market rests; it cannot fill at that price.
    raw = mark * (Decimal(1) - AWAY)
    limit = (raw / tick).to_integral_value() * tick
    cid = client_order_id("smoke", symbol, str(os.getpid()))
    order = OrderRequest(
        product_id=pid, size=1, side=Side.BUY, order_type=OrderType.LIMIT,
        client_order_id=cid, limit_price=limit,
        time_in_force=TimeInForce.GTC,
        note="testnet smoke probe")

    step(4, f"place a resting BUY at {limit} (mark {mark}), cid={cid}")
    placed = client.place_order(order)
    oid = placed.get("id")
    print(f"    accepted: id={oid} state={placed.get('state')}")

    fell_back = False
    try:
        step(5, "look it up by client_order_id -- THE assumption that matters")

        # THE FALLBACK MUST NOT COUNT AS A PASS. On the first testnet run the
        # dedicated endpoint was rejected, the page scan found the order
        # anyway -- because it was still resting on page one -- and this script
        # printed "lookup-by-client-oid works". It did not. The fallback
        # succeeds precisely in the case that does not matter and fails in the
        # one that does: a filled order is in paginated history.
        import logging

        class _CaughtFallback(logging.Handler):
            def emit(self, record):
                nonlocal fell_back
                if "falling back" in record.getMessage():
                    fell_back = True

        handler = _CaughtFallback()
        logging.getLogger("live.client").addHandler(handler)
        try:
            found = client.get_order_by_client_id(cid)
        finally:
            logging.getLogger("live.client").removeHandler(handler)

        if fell_back:
            print("    *** the dedicated endpoint was REJECTED and this fell "
                  "back to a page scan. Any result below is from the scan, "
                  "which cannot see a filled order beyond page one.",
                  file=sys.stderr)
        if not found:
            print("    *** NOT FOUND. The double-fill protection cannot work: "
                  "after an unobserved write this client would report 'never "
                  "arrived' for an order that DID land.", file=sys.stderr)
            return 1
        print(f"    found: id={found.get('id')} state={found.get('state')}")
        if str(found.get("id")) != str(oid):
            print(f"    *** lookup returned a DIFFERENT order ({found.get('id')} "
                  f"!= {oid})", file=sys.stderr)
            return 1
    finally:
        step(6, "cancel it")
        try:
            client.cancel_order(int(oid), pid)
            print("    cancelled")
        except VenueError as exc:
            print(f"    *** CANCEL FAILED: {exc}\n"
                  f"    *** cancel order id {oid} by hand", file=sys.stderr)

    step(7, "confirm it is gone")
    still = [o for o in client.get_open_orders(pid) if str(o.get("id")) == str(oid)]
    print(f"    still open: {len(still)}")

    if fell_back:
        print("\nsigning, order placement and cancel work. THE LOOKUP DOES "
              "NOT: it answered from the fallback scan, so an ambiguous write "
              "cannot be resolved and nothing should trade yet.",
              file=sys.stderr)
        return 1

    print("\nsigning, order placement, lookup-by-client_order_id and cancel "
          "all work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
