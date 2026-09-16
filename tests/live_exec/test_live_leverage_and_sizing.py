"""Liquidation must sit well beyond the stop, and size must match the account.

WHAT HAPPENED. tnet's first live trades, 2026-09-16. The bot never set leverage,
so every order used the account's: ETH 100x, SOL 50x, BTC 50x. Liquidation lands
about 1/leverage - maintenance_margin from entry -- 0.50% for ETH -- and the
strategy's stops sat 0.53-0.66% away. Delta's own order history:

    ETH short 392 @ 2403.4   liquidation_order at 2415.25   stop was 2416.2
    SOL short  95 @ 98.463   liquidation_order at 99.508    (no brackets)
    BTC short 183 @ 75890    stop_loss_order   at 76163     liq ~1.75% away
    ETH long  313 @ 2413.71  OPEN: liquidation 2401.64, stop 2397.85

BTC's stop worked only because its liquidation happened to be further out.
And every trade was sized against an internal 10,000 while the account held
$738.78, so a 0.5% budget was 6.8% of the real money.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.config.settings import RiskConfig
from app.risk.engine import RiskEngine, RiskState
from app.strategy.explanation import LONG, Explanation, Outcome
from deltabt.costs import SymbolCosts
from live.broker import LIQUIDATION_BUFFER, OpeningRefused
from live.client import VenueError
from live.runtime import BALANCE_TTL_SECONDS, BRACKET_GRACE_SECONDS
from tests.live_exec.test_live_order_placement import MKT, PIDS, a_bot, approve
from tests.live_exec.test_live_position_protection import (Venue, flattens,
                                                           kinds, now,
                                                           open_position)

ETH = {"initial_margin": "1", "maintenance_margin": "0.5", "contract_value": "0.01"}


class Recording(Venue):
    """Venue that records the ORDER in which leverage and orders happen."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls: list[str] = []
        self.set_error: Exception | None = None
        self.report_leverage: float | None = None

    def set_order_leverage(self, product_id, leverage):
        self.calls.append(f"set_leverage:{leverage}")
        if self.set_error:
            raise self.set_error
        return super().set_order_leverage(product_id, leverage)

    def get_order_leverage(self, product_id):
        if self.report_leverage is not None:
            return self.report_leverage
        return super().get_order_leverage(product_id)

    def place_order(self, order):
        self.calls.append("place_order")
        return super().place_order(order)


def eth_short_intent(bot):
    """The ETH short that was liquidated: entry 2403.4, stop 2416.2."""
    exp, decision = approve("ETHUSD", side=-1)
    i = decision.intent
    object.__setattr__(i, "entry_reference", 2403.4)
    object.__setattr__(i, "stop_price", 2416.2)
    object.__setattr__(i, "risk_per_unit", 12.8)
    object.__setattr__(i, "quantity", 392)
    return exp, decision


# --- leverage is chosen from the stop, and liquidation sits beyond it -------

@pytest.mark.asyncio
async def test_the_liquidated_eth_short_now_gets_a_leverage_whose_liquidation_clears_its_stop():
    venue = Recording()
    venue.margin_spec = ETH
    bot = a_bot(venue)
    exp, decision = eth_short_intent(bot)
    await bot._place(exp, decision, MKT)

    (lev,) = [int(c.split(":")[1]) for c in venue.calls if c.startswith("set_leverage")]
    stop_distance = abs(2416.2 - 2403.4) / 2403.4
    liq_distance = 1 / lev - 0.005
    assert liq_distance >= LIQUIDATION_BUFFER * stop_distance, (
        f"{lev}x puts liquidation {liq_distance:.3%} from entry against a "
        f"{stop_distance:.3%} stop")
    assert lev < 100, "the account's 100x -- the one that liquidated it -- was kept"
    assert len(venue.placed) == 1


@pytest.mark.asyncio
async def test_leverage_is_set_and_confirmed_before_the_order_is_sent():
    """Liquidation depends on the leverage the ORDER used. Setting it after
    placing would size the position at whatever the account already held."""
    venue = Recording()
    venue.margin_spec = ETH
    bot = a_bot(venue)
    exp, decision = eth_short_intent(bot)
    await bot._place(exp, decision, MKT)
    assert venue.calls[0].startswith("set_leverage"), venue.calls
    assert venue.calls.index("place_order") > 0


@pytest.mark.parametrize("stop_pct", [0.001, 0.0035, 0.0053, 0.0066, 0.01, 0.03, 0.08])
@pytest.mark.parametrize("im,mm", [("0.5", "0.25"), ("1", "0.5"), ("2", "1")])
def test_the_chosen_leverage_always_clears_the_stop_by_the_buffer(stop_pct, im, mm):
    """A property, over the margins of BTC, ETH and SOL and a range of stops."""
    venue = Recording()
    venue.margin_spec = {"initial_margin": im, "maintenance_margin": mm,
                         "contract_value": "0.001"}
    bot = a_bot(venue)
    _, decision = approve("BTCUSD", side=1)
    i = decision.intent
    object.__setattr__(i, "entry_reference", 100.0)
    object.__setattr__(i, "stop_price", 100.0 * (1 - stop_pct))
    lev = bot.broker._set_safe_leverage(i, PIDS["BTCUSD"])
    assert 1 <= lev <= int(100 / float(im)), "outside what the product allows"
    assert 1 / lev - float(mm) / 100 >= LIQUIDATION_BUFFER * stop_pct - 1e-12


def test_leverage_is_capped_at_the_products_maximum():
    venue = Recording()
    venue.margin_spec = {"initial_margin": "0.5", "maintenance_margin": "0.25",
                         "contract_value": "0.001"}
    bot = a_bot(venue)
    _, decision = approve("BTCUSD", side=1)
    i = decision.intent
    object.__setattr__(i, "entry_reference", 100.0)
    object.__setattr__(i, "stop_price", 99.99)          # a very tight stop
    assert bot.broker._set_safe_leverage(i, PIDS["BTCUSD"]) == 200


# --- every way the leverage step can fail refuses, and sends nothing --------

@pytest.mark.parametrize("breakage", ["no_margin", "set_fails", "readback_differs",
                                      "no_spec", "no_reference", "balance_unreadable"])
@pytest.mark.asyncio
async def test_a_failed_leverage_step_refuses_before_anything_is_sent(breakage):
    venue = Recording()
    venue.margin_spec = ETH
    bot = a_bot(venue)
    exp, decision = eth_short_intent(bot)
    if breakage == "no_margin":
        venue.available_usd = 5.0
    elif breakage == "set_fails":
        venue.set_error = VenueError("400 leverage not allowed")
    elif breakage == "readback_differs":
        venue.report_leverage = 100.0
    elif breakage == "no_spec":
        venue.margin_spec = {}
    elif breakage == "no_reference":
        object.__setattr__(decision.intent, "entry_reference", None)
    elif breakage == "balance_unreadable":
        def boom(asset="USD"):
            raise VenueError("503")
        venue.get_wallet_balance = boom

    await bot._place(exp, decision, MKT)                 # must not raise

    assert "place_order" not in venue.calls, f"{breakage}: an order was sent"
    assert await bot.repo.effective_exposure() == 0, (
        f"{breakage}: nothing was sent, but the exposure slot was kept")
    assert "ENTRY_NOT_OPENED" in kinds(bot)


def test_the_refusal_says_no_position_can_result():
    """What lets the runtime release the slot. See live.client.VenueError."""
    assert OpeningRefused("x").no_position_can_result is True


# --- sizing from the account that exists -----------------------------------

@pytest.mark.parametrize("internal,venue_usd,expected", [
    (10_000.0, 738.78, 738.78),     # tnet: size from the real, smaller account
    (10_000.0, 50_000.0, 10_000.0), # a big account must not oversize an experiment
    (10_000.0, 10_000.0, 10_000.0),
])
@pytest.mark.asyncio
async def test_live_sizes_from_the_smaller_of_ledger_and_venue(internal, venue_usd, expected):
    venue = Venue()
    venue.available_usd = venue_usd
    bot = a_bot(venue)
    bot.state = SimpleNamespace(equity=internal, trades_today=0)
    assert bot._sizing_equity() == pytest.approx(expected)


@pytest.mark.asyncio
async def test_an_unreadable_balance_sizes_at_zero_not_at_the_ledger():
    venue = Venue()

    def boom(asset="USD"):
        raise VenueError("503")
    venue.get_wallet_balance = boom
    bot = a_bot(venue)
    assert bot._sizing_equity() == 0.0, "fell back to the internal 10,000"


@pytest.mark.asyncio
async def test_one_balance_read_serves_a_bar_of_signals():
    venue = Venue()
    reads = []
    real = venue.get_wallet_balance

    def counted(asset="USD"):
        reads.append(1)
        return real(asset)
    venue.get_wallet_balance = counted
    bot = a_bot(venue)
    bot._sizing_equity()
    bot._sizing_equity()
    assert len(reads) == 1
    bot.__dict__["_balance_cache"] = (now() - BALANCE_TTL_SECONDS - 1, 1.0)
    bot._sizing_equity()
    assert len(reads) == 2, "a stale balance was reused past its TTL"


def _setup() -> Explanation:
    exp = Explanation(symbol="BTCUSD", bar_open=MKT, primary_timeframe="5m",
                      confirmation_timeframe="1m", strategy_version="t",
                      strategy_config_hash="h", outcome=Outcome.DETECTED)
    exp.direction = LONG
    exp.entry_price, exp.stop_price, exp.target_price = 100_000.0, 99_000.0, 103_000.0
    exp.detail["risk_per_unit"] = 1_000.0
    return exp


def _engine():
    costs = {"BTCUSD": SymbolCosts(
        symbol="BTCUSD", tick_size=0.5, contract_value=0.001, maker_fee=0.0002,
        taker_fee=0.0005, max_leverage=50.0, position_size_limit=1_000_000,
        funding_interval_seconds=3600)}
    return RiskEngine(RiskConfig(), costs)


def test_the_paper_engine_sizes_exactly_as_before_when_given_nothing():
    """The paper arms are mid-experiment and pass no sizing_equity."""
    before = _engine().evaluate(_setup(), RiskState.fresh(10_000.0),
                                open_positions=[], now=MKT)
    after = _engine().evaluate(_setup(), RiskState.fresh(10_000.0),
                               open_positions=[], now=MKT, sizing_equity=None)
    assert before.approved and after.approved
    assert before.intent.quantity == after.intent.quantity
    assert before.intent.risk_amount == after.intent.risk_amount


def test_sizing_equity_changes_the_budget_and_nothing_else():
    small = _engine().evaluate(_setup(), RiskState.fresh(10_000.0),
                               open_positions=[], now=MKT, sizing_equity=738.78)
    full = _engine().evaluate(_setup(), RiskState.fresh(10_000.0),
                              open_positions=[], now=MKT)
    # No branch on approval: a test that accepts either outcome asserts nothing.
    assert small.approved, small.reason
    assert small.intent.quantity < full.intent.quantity
    assert small.intent.risk_amount <= 738.78 * RiskConfig().risk_per_trade + 1e-9
    # The ledger's equity is still what the trade is recorded against.
    assert small.intent.equity_before == 10_000.0


# --- a stop the venue liquidates before is not protection ------------------

async def _protected_long(stop, liquidation):
    venue = Venue()
    bot = a_bot(venue)
    await open_position(bot, venue, "BTCUSD", side=1)
    venue.pending = [{"product_id": PIDS["BTCUSD"], "reduce_only": True,
                      "stop_order_type": "stop_loss_order", "stop_price": str(stop)},
                     {"product_id": PIDS["BTCUSD"], "reduce_only": True,
                      "stop_order_type": "take_profit_order", "stop_price": "80000"}]
    bot.broker.positions["BTCUSD"].liquidation_price = liquidation
    return venue, bot


@pytest.mark.asyncio
async def test_the_open_eth_long_is_flattened_its_stop_is_beyond_liquidation():
    """ETH long @ 2413.71: liquidation 2401.64, stop 2397.85."""
    venue, bot = await _protected_long(stop=2397.85, liquidation=2401.64)
    await bot._verify_brackets(now=now() + BRACKET_GRACE_SECONDS + 1)
    assert len(flattens(bot, venue, "BTCUSD")) == 1
    (event,) = [e for e in bot.events if e[0] == "POSITION_FLATTENED_UNSAFE"]
    assert event[3]["reason"] == "stop_beyond_liquidation"


@pytest.mark.asyncio
async def test_a_stop_inside_liquidation_is_kept():
    venue, bot = await _protected_long(stop=74_600.0, liquidation=73_000.0)
    await bot._verify_brackets(now=now() + BRACKET_GRACE_SECONDS + 1)
    assert flattens(bot, venue, "BTCUSD") == []


@pytest.mark.asyncio
async def test_an_unknown_liquidation_price_warns_and_does_not_flatten():
    venue, bot = await _protected_long(stop=74_600.0, liquidation=0.0)
    await bot._verify_brackets(now=now() + BRACKET_GRACE_SECONDS + 1)
    assert flattens(bot, venue, "BTCUSD") == []
    assert "LIQUIDATION_UNVERIFIABLE" in kinds(bot)


@pytest.mark.asyncio
async def test_the_leg_that_fires_first_is_the_one_compared():
    """Two stop legs on a long: the higher one fires first. Comparing the lower
    one would call a safe position unsafe, or the reverse."""
    venue, bot = await _protected_long(stop=74_600.0, liquidation=74_000.0)
    venue.pending.append({"product_id": PIDS["BTCUSD"], "reduce_only": True,
                          "stop_order_type": "stop_loss_order", "stop_price": "73000"})
    await bot._verify_brackets(now=now() + BRACKET_GRACE_SECONDS + 1)
    assert flattens(bot, venue, "BTCUSD") == [], (
        "the 74,600 leg fires before liquidation at 74,000; it is protected")


@pytest.mark.asyncio
async def test_a_short_whose_stop_is_beyond_liquidation_is_flattened():
    """The ETH short, in this harness's prices."""
    venue = Venue()
    bot = a_bot(venue)
    await open_position(bot, venue, "BTCUSD", side=-1)
    venue.pending = [{"product_id": PIDS["BTCUSD"], "reduce_only": True,
                      "stop_order_type": "stop_loss_order", "stop_price": "75400"}]
    bot.broker.positions["BTCUSD"].liquidation_price = 75_300.0
    await bot._verify_brackets(now=now() + BRACKET_GRACE_SECONDS + 1)
    assert len(flattens(bot, venue, "BTCUSD")) == 1


def test_poll_reads_the_venues_liquidation_price():
    venue = Venue()
    bot = a_bot(venue)
    venue.positions = [{"product_id": PIDS["ETHUSD"], "product_symbol": "ETHUSD",
                        "size": 313, "entry_price": "2413.71",
                        "liquidation_price": "2401.64"}]
    bot.broker.poll()
    assert bot.broker.positions["ETHUSD"].liquidation_price == pytest.approx(2401.64)
