"""A live entry, driven through the REAL runtime into the REAL LiveBroker.

WHY THIS FILE EXISTS. On 2026-09-16 tnet was bound, `(healthy)`, all seven
readiness checks green, approving valid 3R setups -- and it had never placed a
single order. Every approved entry reserved an exposure slot and then died in
`LiveBroker.submit_order` with

    TypeError: submit_order() got an unexpected keyword argument 'order_uid'

which the bar loop swallowed. Six approvals leaked all six slots, and every
entry after that was refused at the reservation gate for hours.

Nothing caught it because nothing drove this path. The live-broker tests call
LiveBroker directly; the runtime tests build a bot with __new__ and never call
`_place`; test_broker_surface checks that methods EXIST, not what they accept
or return. Each half was correct against its own tests.

So these tests run `TradingBot._place` -- the actual caller -- into an actual
LiveBroker, against the actual InMemoryRepository whose exposure count is the
one the gate uses. Only the venue is fake, and its behaviour is scripted per
test so every way an entry can end is exercised.
"""
from __future__ import annotations

import dataclasses
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.execution.intents import ApprovedOrderIntent
from app.persistence.repository import InMemoryRepository
from live.broker import LiveBroker
from live.client import AmbiguousWrite, VenueError, VenueRejected
from live.runtime import LiveTradingBot

MKT = 1_789_540_000
PIDS = {"BTCUSD": 84, "ETHUSD": 1699, "SOLUSD": 92572}
TICKS = {"BTCUSD": Decimal("0.1"), "ETHUSD": Decimal("0.05"),
         "SOLUSD": Decimal("0.0001")}


# --- the venue -------------------------------------------------------------

class FakeVenue:
    """Delta, scripted. `outcome` decides what place_order does."""

    def __init__(self, outcome="fill"):
        self.outcome = outcome
        self.placed: list = []
        self.open_orders: list[dict] = []
        self.positions: list[dict] = []
        self.read_error: Exception | None = None
        self._rows: dict[str, dict] = {}

    def place_order(self, order):
        self.placed.append(order)
        if self.outcome == "reject":
            raise VenueRejected(400, "/v2/orders",
                                {"error": {"code": "insufficient_margin"}})
        if self.outcome == "ambiguous":
            raise AmbiguousWrite("sent, outcome unobserved, lookup failed")
        if self.outcome == "bug":
            raise RuntimeError("something nobody anticipated")
        size = order.size
        state, unfilled = {
            "fill": ("closed", 0),
            "ioc_miss": ("cancelled", size),
            "pending": ("open", size),
        }[self.outcome]
        row = {"id": len(self.placed), "client_order_id": order.client_order_id,
               "product_id": order.product_id, "size": size,
               "unfilled_size": unfilled, "state": state,
               "average_fill_price": "75000.0", "paid_commission": "0.5",
               "created_at": MKT * 1_000_000}
        self._rows[order.client_order_id] = row
        return row

    def get_order_by_client_id(self, cid):
        return self._rows.get(cid)

    def get_open_orders(self, product_id=None):
        if self.read_error:
            raise self.read_error
        return list(self.open_orders)

    def get_positions(self):
        if self.read_error:
            raise self.read_error
        return list(self.positions)

    def cancel_order(self, order_id, product_id):
        return {"id": order_id}


class Notifier:
    def __init__(self):
        self.sent = []

    async def send(self, subject, body):
        self.sent.append(subject)


def a_bot(venue, *, max_open=6, kill_switch_path=None):
    """A LiveTradingBot whose `_place` runs for real.

    __new__ keeps TradingBot.__init__ from building a feed and a backfiller.
    What `_place` and `_persist_open` touch is real: the broker, the
    repository, the exposure gate.
    """
    bot = LiveTradingBot.__new__(LiveTradingBot)
    bot.settings = SimpleNamespace(
        risk=SimpleNamespace(max_open_positions=max_open))
    bot.repo = InMemoryRepository()
    bot.client = venue
    bot.broker = LiveBroker(venue, product_ids=PIDS, experiment_id="EXP",
                            tick_size=TICKS,
                            kill_switch_path=kill_switch_path or "/nonexistent")
    bot._symbol_for = {v: k for k, v in PIDS.items()}
    bot.instance_uid = "bot_test"
    bot.metrics = SimpleNamespace(orders=0, reservations_refused=0)
    bot.notifier = Notifier()
    bot.events = []
    bot.experiment_id = "EXP"
    bot.identity = None
    bot.venue = "testnet"
    bot.strategy = SimpleNamespace(version="manual_scalp_both_t3@5m")
    bot.state = SimpleNamespace(equity=10_000.0, trades_today=0)

    async def _event(component, event_type, *, symbol=None, severity="INFO",
                     payload=None):
        bot.events.append((event_type, symbol, severity, payload or {}))

    async def _save_state():
        return None

    bot._event = _event
    bot._save_state = _save_state
    return bot


_seq = iter(range(1, 10_000))


def approve(symbol="BTCUSD", side=1):
    """An exp and a risk-approved decision, as the runtime hands to _place."""
    n = next(_seq)
    entry, rpu = 75_000.0, 400.0
    intent = ApprovedOrderIntent(
        intent_id=f"int-{n}", signal_key=f"sig-{n}", risk_evaluation_id=f"risk-{n}",
        symbol=symbol, side=side, order_type="market", quantity=100,
        limit_price=None, entry_reference=entry,
        stop_price=entry - side * rpu, target_price=entry + side * 3 * rpu,
        risk_per_unit=rpu, risk_amount=40.0, notional=7500.0,
        equity_before=10_000.0, estimated_fee=1.0, estimated_slippage=0.5,
        strategy_version="v", bar_open=MKT - 300,
        checks_passed=("max_open_positions",))
    exp = SimpleNamespace(symbol=symbol, outcome=None, rejection_reason=None)
    return exp, SimpleNamespace(intent=intent, approved=True)


async def place(bot, symbol="BTCUSD", side=1):
    exp, decision = approve(symbol, side)
    await bot._place(exp, decision, MKT)
    return exp, decision


def kinds(bot):
    return [e[0] for e in bot.events]


# --- the regression --------------------------------------------------------

@pytest.mark.asyncio
async def test_a_live_entry_reaches_the_venue_with_its_brackets():
    """The path that raised TypeError on every entry tnet ever approved."""
    venue = FakeVenue("fill")
    bot = a_bot(venue)
    await place(bot)

    assert len(venue.placed) == 1, "the entry never reached the venue"
    sent = venue.placed[0]
    assert sent.bracket_stop_loss_price is not None
    assert sent.bracket_take_profit_price is not None
    assert "PAPER_ORDER_CREATED" in kinds(bot)
    assert await bot.repo.effective_exposure() == 1


@pytest.mark.asyncio
async def test_the_order_row_and_the_venue_order_are_linked():
    """The two ids were unrelated; `_pending` is the only link between them."""
    venue = FakeVenue("fill")
    bot = a_bot(venue)
    await place(bot)
    (row,) = await bot.repo.load_reserving_entry_orders()
    cid = venue.placed[0].client_order_id
    assert bot.broker._pending[cid]["order_uid"] == row.order_uid


# --- every way an entry ends, and what happens to its slot -----------------

@pytest.mark.asyncio
async def test_a_venue_rejection_releases_the_slot():
    venue = FakeVenue("reject")
    bot = a_bot(venue)
    await place(bot)                       # must not raise

    assert await bot.repo.effective_exposure() == 0
    assert "ENTRY_NOT_OPENED" in kinds(bot)
    assert bot.broker._pending == {}, "a rejected order left bookkeeping behind"
    assert "BTCUSD" not in bot.broker._entry_cid


@pytest.mark.asyncio
async def test_a_kill_switch_releases_the_slot_and_never_calls_the_venue(tmp_path):
    kill = tmp_path / "KILL"
    kill.write_text("stop")
    venue = FakeVenue("fill")
    bot = a_bot(venue, kill_switch_path=str(kill))
    await place(bot)

    assert venue.placed == [], "an order left the process past the kill switch"
    assert await bot.repo.effective_exposure() == 0


@pytest.mark.asyncio
async def test_an_ioc_entry_that_did_not_fill_releases_the_slot():
    """It reached the venue, but an IOC cannot rest: nothing can come of it."""
    venue = FakeVenue("ioc_miss")
    bot = a_bot(venue)
    await place(bot)

    assert len(venue.placed) == 1
    assert await bot.repo.effective_exposure() == 0
    assert bot.broker._pending == {}


@pytest.mark.asyncio
async def test_an_order_still_open_keeps_its_slot():
    """Not final, so it may still fill. Releasing it could double a position."""
    venue = FakeVenue("pending")
    bot = a_bot(venue)
    await place(bot)
    assert await bot.repo.effective_exposure() == 1


@pytest.mark.asyncio
async def test_an_unknown_outcome_holds_the_slot_and_blocks_the_symbol():
    venue = FakeVenue("ambiguous")
    bot = a_bot(venue)
    with pytest.raises(AmbiguousWrite):
        await place(bot, "BTCUSD")

    assert await bot.repo.effective_exposure() == 1, "the slot was released"
    assert "ENTRY_OUTCOME_UNKNOWN" in kinds(bot)
    assert bot.notifier.sent, "an unknown entry outcome went unannounced"

    # The gate is a global count, so it alone would let this through. If the
    # first order did land, the venue would add to that position.
    venue.outcome = "fill"
    await place(bot, "BTCUSD")
    assert len(venue.placed) == 1, "a second entry was sent on the same symbol"

    # Other symbols are unaffected.
    await place(bot, "ETHUSD")
    assert len(venue.placed) == 2


@pytest.mark.asyncio
async def test_an_exception_that_does_not_say_is_treated_as_unknown():
    """A bug is not evidence that nothing was placed."""
    venue = FakeVenue("bug")
    bot = a_bot(venue)
    with pytest.raises(RuntimeError):
        await place(bot)
    assert await bot.repo.effective_exposure() == 1


@pytest.mark.asyncio
async def test_repeated_refusals_do_not_accumulate_into_a_full_book():
    """The incident, as a property.

    With max_open 6, six refusals that each leaked a slot left the bot able to
    reach the venue zero more times. Here ten consecutive definite refusals
    leave the book empty, and the tenth still reaches the venue.
    """
    venue = FakeVenue("reject")
    bot = a_bot(venue, max_open=6)
    for _ in range(10):
        await place(bot)
    assert len(venue.placed) == 10
    assert await bot.repo.effective_exposure() == 0


# --- once the venue fills it ----------------------------------------------

@pytest.mark.asyncio
async def test_a_filled_entry_is_counted_once_and_frees_its_slot_on_close():
    """Recording the POSITION alone left the order row WORKING.

    That counted every open trade twice, and every closed trade once forever:
    one leaked slot per round trip, so a bot that could place orders would
    still have blocked itself after max_open_positions trades.
    """
    venue = FakeVenue("fill")
    bot = a_bot(venue)
    await place(bot, "BTCUSD")
    assert await bot.repo.effective_exposure() == 1

    (order_row,) = await bot.repo.load_reserving_entry_orders()
    await bot._persist_open(SimpleNamespace(symbol="BTCUSD", payload={}))

    (position,) = await bot.repo.load_open_positions()
    assert await bot.repo.effective_exposure() == 1, (
        "the open trade is counted twice: position AND unfilled order row")
    assert await bot.repo.load_reserving_entry_orders() == []

    # BOTH HALVES, ASSERTED SEPARATELY. Either one alone is enough to drop the
    # row out of the exposure count above -- a terminal status and a linked
    # position_uid are each excluded -- so the count cannot tell a correct
    # close-out from half of one. Mutation testing showed exactly that: each
    # half could be deleted with this test still green. They are not
    # redundant as DATA: without FILLED the row claims to be working at the
    # venue, and without position_uid nothing records which order opened the
    # position, which is what a forward test is read back by.
    stored = bot.repo._s["orders"][order_row.order_uid]
    assert stored.status == "FILLED", (
        f"the filled entry's row is still {stored.status!r}")
    assert stored.position_uid == position.position_uid, (
        "the order row is not linked to the position it opened")

    await bot.repo.update_position(dataclasses.replace(position, status="CLOSED"))
    assert await bot.repo.effective_exposure() == 0, (
        "the closed trade still holds a slot through its order row")


# --- the startup sweep -----------------------------------------------------

async def _orphans(bot, symbols):
    """Entry rows a dead process left WORKING, as tnet's six were."""
    for sym in symbols:
        exp, decision = approve(sym)
        from app.persistence.models import OrderRecord
        i = decision.intent
        await bot.repo.create_order(OrderRecord(
            order_uid=f"ord-dead-{i.intent_id}", idempotency_key=i.intent_id,
            signal_key=i.signal_key, instance_uid="bot_dead", symbol=sym,
            side=i.side, order_type="market", purpose="entry",
            quantity=i.quantity, limit_price=None, status="WORKING",
            equity_before=10_000.0, risk_amount=40.0))


@pytest.mark.asyncio
async def test_the_sweep_frees_the_slots_that_blocked_tnet():
    """Six orphans, a quiet venue: exactly the 2026-09-16 state."""
    venue = FakeVenue("fill")
    bot = a_bot(venue, max_open=6)
    await _orphans(bot, ["ETHUSD", "ETHUSD", "SOLUSD", "BTCUSD", "ETHUSD",
                         "SOLUSD"])
    assert await bot.repo.effective_exposure() == 6

    await place(bot)
    assert venue.placed == [], "the full book should refuse before the sweep"

    await bot._release_orphaned_entries()
    assert await bot.repo.effective_exposure() == 0
    assert kinds(bot).count("ORPHANED_ENTRY_RELEASED") == 6

    await place(bot)
    assert len(venue.placed) == 1, "the bot still cannot trade after the sweep"


@pytest.mark.asyncio
async def test_the_sweep_leaves_a_row_whose_symbol_is_busy_at_the_venue():
    """Something is there and this process cannot tell which order it was."""
    venue = FakeVenue("fill")
    venue.open_orders = [{"product_id": PIDS["ETHUSD"], "id": 9}]
    venue.positions = [{"product_symbol": "SOLUSD", "size": 3}]
    bot = a_bot(venue)
    await _orphans(bot, ["ETHUSD", "SOLUSD", "BTCUSD"])

    await bot._release_orphaned_entries()

    held = {o.symbol for o in await bot.repo.load_reserving_entry_orders()}
    assert held == {"ETHUSD", "SOLUSD"}
    assert kinds(bot).count("ORPHANED_ENTRY_HELD") == 2
    assert kinds(bot).count("ORPHANED_ENTRY_RELEASED") == 1


@pytest.mark.asyncio
async def test_the_sweep_releases_nothing_when_the_venue_cannot_be_read():
    venue = FakeVenue("fill")
    venue.read_error = VenueError("503")
    bot = a_bot(venue)
    await _orphans(bot, ["BTCUSD", "ETHUSD"])

    await bot._release_orphaned_entries()
    assert await bot.repo.effective_exposure() == 2


# --- stale entries cancelled at the venue ----------------------------------

@pytest.mark.asyncio
async def test_a_stale_cancel_reports_the_order_row_it_belongs_to():
    """The runtime persists ORDER_CANCELLED by order_uid and indexes the key
    unconditionally; the event used to carry only the venue's id."""
    venue = FakeVenue("pending")
    bot = a_bot(venue)
    await place(bot)
    (row,) = await bot.repo.load_reserving_entry_orders()
    cid = venue.placed[0].client_order_id
    venue.open_orders = [{"id": 1, "product_id": PIDS["BTCUSD"],
                          "client_order_id": cid, "created_at": MKT}]

    (ev,) = bot.broker.expire_stale_entries(MKT + 3600)
    assert ev.kind == "ORDER_CANCELLED"
    assert ev.payload["order_uid"] == row.order_uid
    assert bot.broker._pending == {}


@pytest.mark.asyncio
async def test_a_stale_cancel_with_no_known_row_is_not_reported_half_formed():
    venue = FakeVenue("fill")
    bot = a_bot(venue)
    venue.open_orders = [{"id": 1, "product_id": PIDS["BTCUSD"],
                          "client_order_id": "from-a-dead-process",
                          "created_at": MKT}]
    assert bot.broker.expire_stale_entries(MKT + 3600) == []


@pytest.mark.asyncio
async def test_recover_runs_the_sweep_so_a_deploy_clears_the_stuck_rows():
    """The sweep being correct is worth nothing if startup never calls it.

    Deleting the call from recover() left the whole suite green, because every
    other sweep test invokes the method directly. This is the test that makes
    "the next deploy clears tnet's six rows" true rather than hoped.
    """
    venue = FakeVenue("fill")
    bot = a_bot(venue)
    bot.costs = {s: object() for s in PIDS}
    bot.product_ids = dict(PIDS)
    bot.recovery_error = None
    bot._state_loaded = False
    await _orphans(bot, ["BTCUSD", "ETHUSD", "SOLUSD"])
    assert await bot.repo.effective_exposure() == 3

    await bot.recover()

    assert bot.recovery_error is None, bot.recovery_error
    assert bot._state_loaded, "recover() did not complete"
    assert await bot.repo.effective_exposure() == 0, (
        "recover() finished without releasing orphaned entry orders")
