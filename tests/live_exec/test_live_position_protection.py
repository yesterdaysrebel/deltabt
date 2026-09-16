"""Every open live position is protected at the venue, or it is closed.

WHAT HAPPENED. The first live trades on tnet, 2026-09-16. SOLUSD was approved
short at 97.299, stop 97.8215, target 95.7314. The market sell filled on a thin
testnet book at 98.463 -- 2.2R from its reference and BEYOND ITS OWN STOP --
and Delta silently dropped both bracket legs, because a buy-stop below a
short's entry is on the wrong side. A 95-contract short sat open with no
stop-loss and no take-profit while the ledger recorded a stop of 97.8215.
BTCUSD and ETHUSD, filled within a tick of their references, had both legs.

Nothing noticed, for three reasons these tests pin:

  * nothing checked a live fill against the geometry risk approved -- the
    paper broker refuses such a fill; the live path had no equivalent;
  * nothing checked the brackets actually existed after the fill;
  * the one query that might have shown it, get_open_orders(), asks for
    `states=open`, and Delta keeps untriggered bracket legs as `pending`.

These drive the real LiveBroker and LiveTradingBot through a scripted venue,
reusing the harness in test_live_order_placement.py.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from live.client import VenueError
from live.runtime import BRACKET_ALERT_SECONDS, BRACKET_GRACE_SECONDS
from tests.live_exec.test_live_order_placement import (MKT, PIDS, FakeVenue,
                                                        a_bot, approve)

ENTRY, RPU = 75_000.0, 400.0       # what approve() builds: stop = entry -/+ 1R


class Venue(FakeVenue):
    """FakeVenue plus the pending book, an adjustable fill, and history."""

    def __init__(self, outcome="fill"):
        super().__init__(outcome)
        self.fill_price: float = ENTRY
        self.pending: list[dict] = []
        self.pending_error: Exception | None = None
        self.history: list[dict] = []

    def place_order(self, order):
        row = super().place_order(order)
        if isinstance(row, dict):
            row["average_fill_price"] = str(self.fill_price)
            row["reduce_only"] = bool(getattr(order, "reduce_only", False))
        return row

    def get_pending_orders(self, product_id=None):
        if self.pending_error:
            raise self.pending_error
        return [r for r in self.pending
                if product_id is None or r.get("product_id") == product_id]

    def order_history(self, product_id):
        return list(self.history)

    def protect(self, symbol, *, stop_loss=True, take_profit=True):
        pid = PIDS[symbol]
        if stop_loss:
            self.pending.append({"product_id": pid, "reduce_only": True,
                                 "stop_order_type": "stop_loss_order"})
        if take_profit:
            self.pending.append({"product_id": pid, "reduce_only": True,
                                 "stop_order_type": "take_profit_order"})


def now():
    return asyncio.get_event_loop().time()


def flattens(bot, venue, symbol):
    return [o for o in venue.placed
            if getattr(o, "reduce_only", False)
            and o.product_id == PIDS[symbol]]


def kinds(bot):
    return [e[0] for e in bot.events]


async def open_position(bot, venue, symbol="BTCUSD", side=1, fill=ENTRY):
    """Place an entry, have the venue report the position, and persist it."""
    venue.fill_price = fill
    exp, decision = approve(symbol, side)
    await bot._place(exp, decision, MKT)
    venue.positions = [{"product_id": PIDS[symbol], "product_symbol": symbol,
                        "size": side * decision.intent.quantity,
                        "entry_price": str(fill)}]
    (ev,) = [e for e in bot.broker.poll() if e.kind == "POSITION_OPENED"]
    bot.__dict__["_polled_once"] = True
    await bot._persist_open(ev)
    return decision.intent


# --- a fill that broke the approved geometry is closed ---------------------

@pytest.mark.asyncio
async def test_the_solusd_fill_beyond_its_stop_is_flattened():
    """The 2026-09-16 trade, in this harness's prices: a short approved at
    75,000 with its stop at 75,400, filled 2.2R away at 75,880."""
    venue = Venue()
    bot = a_bot(venue)
    await open_position(bot, venue, "BTCUSD", side=-1, fill=ENTRY + 2.2 * RPU)

    assert len(flattens(bot, venue, "BTCUSD")) == 1, "the position was left open"
    (event,) = [e for e in bot.events if e[0] == "POSITION_FLATTENED_UNSAFE"]
    assert event[2] == "CRITICAL"
    assert event[3]["reason"] == "entry_deviation"
    assert event[3]["beyond_stop"] is True
    assert any("FLATTENING" in s for s in bot.notifier.sent)


@pytest.mark.asyncio
async def test_a_fill_too_far_from_its_reference_is_flattened_even_inside_the_stop():
    """0.5R adverse on a short: not beyond the stop, but past the 0.25R the
    paper broker would refuse. Same gate, enforced the only way live can."""
    venue = Venue()
    bot = a_bot(venue)
    await open_position(bot, venue, "BTCUSD", side=-1, fill=ENTRY - 0.5 * RPU)

    assert len(flattens(bot, venue, "BTCUSD")) == 1
    (event,) = [e for e in bot.events if e[0] == "POSITION_FLATTENED_UNSAFE"]
    assert event[3]["beyond_stop"] is False
    assert event[3]["deviation_r"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_a_fill_within_tolerance_is_kept():
    venue = Venue()
    bot = a_bot(venue)
    await open_position(bot, venue, "BTCUSD", side=1, fill=ENTRY + 0.1 * RPU)
    assert flattens(bot, venue, "BTCUSD") == []
    assert "BTCUSD" in bot._bracket_checks(), "its protection is never checked"


@pytest.mark.asyncio
async def test_a_deliberate_flatten_is_recorded_with_its_reason():
    """Otherwise live.ledger calls a reduce-only close MANUAL_CLOSE, and the
    forward test cannot tell the guard firing from someone clicking a button."""
    venue = Venue()
    bot = a_bot(venue)
    await open_position(bot, venue, "BTCUSD", side=-1, fill=ENTRY + 2.2 * RPU)

    venue.positions = []
    (closed,) = [e for e in bot.broker.poll() if e.kind == "POSITION_CLOSED"]
    assert closed.payload["requested_reason"] == "entry_deviation"


# --- the stop-loss must exist at the venue ---------------------------------

@pytest.mark.asyncio
async def test_a_position_without_a_stop_loss_is_flattened_after_the_grace():
    venue = Venue()
    bot = a_bot(venue)
    await open_position(bot, venue, "BTCUSD")         # clean fill, no legs

    await bot._verify_brackets(now=now() + 1)          # inside the grace
    assert flattens(bot, venue, "BTCUSD") == [], (
        "flattened before the venue had time to create the brackets")

    await bot._verify_brackets(now=now() + BRACKET_GRACE_SECONDS + 1)
    assert len(flattens(bot, venue, "BTCUSD")) == 1
    (event,) = [e for e in bot.events if e[0] == "POSITION_FLATTENED_UNSAFE"]
    assert event[3]["reason"] == "unprotected"


@pytest.mark.asyncio
async def test_a_protected_position_is_left_alone():
    venue = Venue()
    bot = a_bot(venue)
    await open_position(bot, venue, "BTCUSD")
    venue.protect("BTCUSD")

    await bot._verify_brackets(now=now() + BRACKET_GRACE_SECONDS + 1)
    assert flattens(bot, venue, "BTCUSD") == []
    assert "BTCUSD" not in bot._bracket_checks(), "verified, but still re-checked"


@pytest.mark.asyncio
async def test_brackets_are_read_from_pending_not_open_orders():
    """The query that hid this: brackets are `pending`, never `open`."""
    venue = Venue()
    bot = a_bot(venue)
    await open_position(bot, venue, "BTCUSD")
    venue.protect("BTCUSD")
    venue.open_orders = []                            # what states=open returns

    await bot._verify_brackets(now=now() + BRACKET_GRACE_SECONDS + 1)
    assert flattens(bot, venue, "BTCUSD") == [], (
        "a protected position was flattened because the check read open orders")


@pytest.mark.asyncio
async def test_a_missing_take_profit_alone_is_a_warning_not_a_flatten():
    """The stop-loss is what limits loss. A lost target caps upside only."""
    venue = Venue()
    bot = a_bot(venue)
    await open_position(bot, venue, "BTCUSD")
    venue.protect("BTCUSD", take_profit=False)

    await bot._verify_brackets(now=now() + BRACKET_GRACE_SECONDS + 1)
    assert flattens(bot, venue, "BTCUSD") == []
    assert "TAKE_PROFIT_MISSING" in kinds(bot)


@pytest.mark.asyncio
async def test_an_unreadable_venue_alerts_but_does_not_flatten():
    """Closing needs the same venue that just failed, and flattening protected
    positions during an outage is its own harm."""
    venue = Venue()
    bot = a_bot(venue)
    await open_position(bot, venue, "BTCUSD")
    venue.pending_error = VenueError("503")

    await bot._verify_brackets(now=now() + BRACKET_GRACE_SECONDS + 1)
    assert flattens(bot, venue, "BTCUSD") == []
    assert "PROTECTION_UNVERIFIABLE" not in kinds(bot), "alerted on one blip"

    await bot._verify_brackets(
        now=now() + BRACKET_GRACE_SECONDS + BRACKET_ALERT_SECONDS + 1)
    assert flattens(bot, venue, "BTCUSD") == []
    assert "PROTECTION_UNVERIFIABLE" in kinds(bot)


@pytest.mark.asyncio
async def test_nothing_is_checked_before_the_venue_has_been_polled():
    """Before a poll, broker.positions is empty because nobody has looked --
    not because nothing is open. A check then would drop the position it
    exists to protect."""
    venue = Venue()
    bot = a_bot(venue)
    await open_position(bot, venue, "BTCUSD")
    bot.__dict__.pop("_polled_once")
    bot.broker.positions = {}

    await bot._verify_brackets(now=now() + BRACKET_GRACE_SECONDS + 1)
    assert "BTCUSD" in bot._bracket_checks(), "the check was dropped unexamined"


@pytest.mark.asyncio
async def test_a_flatten_is_attempted_once_not_every_poll():
    venue = Venue()
    bot = a_bot(venue)
    await open_position(bot, venue, "BTCUSD")
    for extra in (1, 6, 11):
        bot._schedule_bracket_check("BTCUSD", now=now() - BRACKET_GRACE_SECONDS)
        await bot._verify_brackets(now=now() + extra)
    assert len(flattens(bot, venue, "BTCUSD")) == 1


@pytest.mark.asyncio
async def test_a_failed_flatten_is_reported_and_not_hammered():
    venue = Venue()
    bot = a_bot(venue)
    await open_position(bot, venue, "BTCUSD")

    def refuse(symbol, reason):
        raise VenueError("insufficient liquidity")
    bot.broker.close_position = refuse

    await bot._verify_brackets(now=now() + BRACKET_GRACE_SECONDS + 1)
    assert "FLATTEN_FAILED" in kinds(bot)
    assert "BTCUSD" in bot._flattening()


# --- positions opened before this check existed ----------------------------

@pytest.mark.asyncio
async def test_a_restart_checks_positions_it_did_not_open():
    """How tnet's unprotected SOLUSD short is caught: the next process to start
    schedules a check for every position reconciliation confirms."""
    venue = Venue()
    first = a_bot(venue)
    await open_position(first, venue, "BTCUSD")        # never protected

    restarted = a_bot(venue)
    restarted.repo = first.repo
    restarted.costs = {s: object() for s in PIDS}
    restarted.product_ids = dict(PIDS)
    restarted.recovery_error = None
    restarted._state_loaded = False
    await restarted.recover()
    assert restarted.recovery_error is None, restarted.recovery_error
    assert "BTCUSD" in restarted._bracket_checks(), (
        "recover() did not schedule a protection check for an open position")

    restarted.broker.poll()
    restarted.__dict__["_polled_once"] = True
    await restarted._verify_brackets(now=now() + BRACKET_GRACE_SECONDS + 1)
    assert len(flattens(restarted, venue, "BTCUSD")) == 1


@pytest.mark.asyncio
async def test_a_fill_beyond_its_stop_is_flattened_even_without_a_reference():
    """The beyond-stop check is not redundant with the deviation check.

    Deviation needs `entry_reference`, which is optional on the pending intent
    (not every caller builds a full ApprovedOrderIntent). Without it no
    deviation can be computed -- but a fill on the wrong side of its stop is
    still one the venue will not protect. Mutation testing found this: with
    the SOLUSD replay alone, the beyond-stop check could be deleted and every
    test still passed, because that fill also deviated 2.2R.
    """
    venue = Venue()
    bot = a_bot(venue)
    await open_position(bot, venue, "BTCUSD", side=1, fill=ENTRY)   # clean
    venue.placed.clear()
    bot._flattening().clear()

    record = SimpleNamespace(entry_price=ENTRY - 1.5 * RPU)     # long, below stop
    intent = {"side": 1, "stop_price": ENTRY - RPU, "risk_per_unit": RPU,
              "entry_reference": None}
    closed = await bot._flatten_if_entry_invalid("BTCUSD", record, intent)

    assert closed is True
    assert len(flattens(bot, venue, "BTCUSD")) == 1
