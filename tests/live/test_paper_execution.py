"""Paper broker: fills, fees, slippage, position lifecycle, look-ahead guard."""

from __future__ import annotations

import pytest

from app.execution.intents import ApprovedOrderIntent
from app.execution.paper_broker import ExitReason, OrderStatus, PaperBroker
from app.market_data.normalize import Candle, Tick
from deltabt.costs import SymbolCosts

BTC = SymbolCosts(symbol="BTCUSD", tick_size=0.5, contract_value=0.001,
                  maker_fee=0.0002, taker_fee=0.0005, max_leverage=200.0,
                  position_size_limit=125_000, funding_interval_seconds=28800,
                  slippage_bps=2.0)
COSTS = {"BTCUSD": BTC}
US = 1_000_000


#: The instant the signal bar closed, which is when the order is created.
#: Entry ticks arrive within seconds of it -- an order still resting minutes
#: later is stale by construction and the broker expires it.
BAR_CLOSE = 1786560300


def intent(side=1, entry=63000.0, stop=62500.0, target=64000.0, qty=100,
           order_type="market", limit=None, iid="i1", bar_open=BAR_CLOSE):
    return ApprovedOrderIntent(
        intent_id=iid, signal_key="sig1", risk_evaluation_id="risk1",
        symbol="BTCUSD", side=side, order_type=order_type, quantity=qty,
        limit_price=limit, entry_reference=entry, stop_price=stop,
        target_price=target, risk_per_unit=abs(entry - stop),
        # Derived, not hardcoded: the broker resizes the fill to keep realised
        # risk inside this budget, so an inconsistent pair would silently make
        # every fixture a resize case.
        risk_amount=qty * BTC.contract_value * abs(entry - stop),
        notional=BTC.notional(qty, entry),
        equity_before=10_000.0, estimated_fee=1.0, estimated_slippage=0.5,
        strategy_version="H-WPR-1-VariantA@abc", bar_open=bar_open,
        checks_passed=("minimum_rr", "max_open_positions"))


def tick(ltp, mark=None, ts=1786560300, us=None):
    return Tick("BTCUSD", us if us is not None else ts * US, ltp,
                mark if mark is not None else ltp)


def bar(start, o, h, l, c, v=10.0):
    return Candle("BTCUSD", start, o, h, l, c, v)


def broker(equity=10_000.0):
    return PaperBroker(COSTS, starting_equity=equity, slippage_bps=2.0)


# =====================================================================
# THE SAFETY BOUNDARY
# =====================================================================


class TestSafetyBoundary:
    def test_broker_refuses_anything_but_an_approved_intent(self):
        b = broker()
        with pytest.raises(TypeError, match="ApprovedOrderIntent"):
            b.submit_order({"symbol": "BTCUSD", "side": 1, "quantity": 1})

    def test_intent_cannot_exist_without_a_risk_evaluation(self):
        with pytest.raises(ValueError, match="risk evaluation"):
            ApprovedOrderIntent(
                intent_id="x", signal_key="s", risk_evaluation_id="",
                symbol="BTCUSD", side=1, order_type="market", quantity=1,
                limit_price=None, entry_reference=100.0, stop_price=99.0,
                target_price=102.0, risk_per_unit=1.0, risk_amount=1.0,
                notional=100.0, equity_before=1000.0, estimated_fee=0.1,
                estimated_slippage=0.1, strategy_version="v", bar_open=0,
                checks_passed=("a",))

    def test_intent_must_record_its_risk_checks(self):
        with pytest.raises(ValueError, match="which risk checks"):
            ApprovedOrderIntent(
                intent_id="x", signal_key="s", risk_evaluation_id="r",
                symbol="BTCUSD", side=1, order_type="market", quantity=1,
                limit_price=None, entry_reference=100.0, stop_price=99.0,
                target_price=102.0, risk_per_unit=1.0, risk_amount=1.0,
                notional=100.0, equity_before=1000.0, estimated_fee=0.1,
                estimated_slippage=0.1, strategy_version="v", bar_open=0,
                checks_passed=())

    @pytest.mark.parametrize("side,stop,target", [
        (1, 64000.0, 65000.0),      # long with stop above entry
        (-1, 62000.0, 61000.0),     # short with stop below entry
    ])
    def test_inverted_geometry_never_becomes_an_intent(self, side, stop, target):
        with pytest.raises(ValueError, match="geometry inverted"):
            intent(side=side, stop=stop, target=target)

    def test_broker_has_no_exchange_order_method(self):
        for forbidden in ("place_order", "submit_live_order", "send_signed_order",
                          "place_real_order", "sign", "api_key"):
            assert not hasattr(PaperBroker, forbidden)


# =====================================================================
# THE SAME-BAR LOOK-AHEAD REGRESSION -- brief section 22.24 / research bug
# =====================================================================


class TestSameBarLookAhead:
    def test_passive_entry_cannot_claim_the_same_bar_target(self):
        """The exact bug from the research program.

        A long limit at 62,900 fills because the bar's LOW reached it. That
        same bar's HIGH is above the 2R target. Booking the target would mean
        the favourable extreme happened after the fill, which is precisely
        backwards: price had to come DOWN to fill the limit.
        """
        b = broker()
        b.submit_order(intent(order_type="limit", limit=62_900.0,
                              entry=62_900.0, stop=62_400.0, target=63_900.0))
        # Low 62,850 fills the limit; high 64,500 is far past the target.
        b.process_bar(bar(1786560000, 63_500.0, 64_500.0, 62_850.0, 63_400.0))
        pos = b.get_positions("BTCUSD")
        assert len(pos) == 1, "the entry should have filled"
        assert pos[0].status == "OPEN", (
            "the entry bar's own high must not close the position at target")
        assert pos[0].exit_reason is None

    def test_the_target_is_claimable_on_the_NEXT_bar(self):
        b = broker()
        b.submit_order(intent(order_type="limit", limit=62_900.0,
                              entry=62_900.0, stop=62_400.0, target=63_900.0))
        b.process_bar(bar(1786560000, 63_500.0, 64_500.0, 62_850.0, 63_400.0))
        b.process_bar(bar(1786560060, 63_400.0, 64_000.0, 63_300.0, 63_950.0))
        pos = list(b.positions.values())[0]
        assert pos.status == "CLOSED"
        assert pos.exit_reason == ExitReason.TAKE_PROFIT.value

    def test_the_stop_IS_claimable_on_the_entry_bar(self):
        """The guard is asymmetric on purpose.

        An adverse move after a passive fill is entirely ordinary -- price
        reached the limit and kept going. Suppressing that too would flatter
        the record in the opposite direction.
        """
        b = broker()
        b.submit_order(intent(order_type="limit", limit=62_900.0,
                              entry=62_900.0, stop=62_400.0, target=63_900.0))
        b.process_bar(bar(1786560000, 63_000.0, 63_100.0, 62_300.0, 62_350.0))
        pos = list(b.positions.values())[0]
        assert pos.status == "CLOSED"
        assert pos.exit_reason == ExitReason.STOP_LOSS.value

    def test_ambiguous_bar_resolves_to_the_stop(self):
        """1m OHLC cannot order the high and the low. Assume the worse."""
        b = broker()
        b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_000.0))
        b.process_bar(bar(1786560000, 63_000.0, 63_010.0, 62_990.0, 63_000.0))
        b.process_bar(bar(1786560060, 63_000.0, 64_500.0, 62_400.0, 63_000.0))
        pos = list(b.positions.values())[0]
        assert pos.exit_reason == ExitReason.STOP_LOSS.value

    def test_live_tick_path_records_the_ordering_it_observed(self):
        """Live, ordering is observed, so the fill carries proof."""
        b = broker()
        b.submit_order(intent())
        b.process_market_event(tick(63_000.0, ts=1786560300))
        pos = list(b.positions.values())[0]
        assert pos.armed_after_us == 1786560300 * US
        # A tick at the same microsecond cannot close it.
        b.process_market_event(tick(64_500.0, ts=1786560300))
        assert pos.status == "OPEN"
        # A later tick can.
        b.process_market_event(tick(64_500.0, ts=1786560360))
        assert pos.exit_reason == ExitReason.TAKE_PROFIT.value


# =====================================================================
# TRIGGERS AND PRICING
# =====================================================================


class TestTriggers:
    def test_stop_triggers_on_mark_not_last_traded(self):
        """Delta triggers stops on mark by default."""
        b = broker()
        b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_000.0))
        b.process_market_event(tick(63_000.0, ts=1786560300))
        # LTP well above the stop, MARK below it: the stop must fire.
        b.process_market_event(tick(62_800.0, mark=62_450.0, ts=1786560360))
        pos = list(b.positions.values())[0]
        assert pos.exit_reason == ExitReason.STOP_LOSS.value

    def test_ltp_below_stop_but_mark_above_does_not_trigger(self):
        b = broker()
        b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_000.0))
        b.process_market_event(tick(63_000.0, ts=1786560300))
        b.process_market_event(tick(62_400.0, mark=62_600.0, ts=1786560360))
        assert list(b.positions.values())[0].status == "OPEN"

    def test_short_stop_and_target_mirror(self):
        b = broker()
        b.submit_order(intent(side=-1, entry=63_000.0, stop=63_500.0,
                              target=62_000.0))
        b.process_market_event(tick(63_000.0, ts=1786560300))
        b.process_market_event(tick(61_900.0, ts=1786560360))
        pos = list(b.positions.values())[0]
        assert pos.exit_reason == ExitReason.TAKE_PROFIT.value
        assert pos.realized_pnl > 0

    def test_market_entry_pays_adverse_slippage(self):
        b = broker()
        b.submit_order(intent(side=1, entry=63_000.0))
        b.process_market_event(tick(63_000.0))
        pos = list(b.positions.values())[0]
        assert pos.entry_price > 63_000.0, "a long buys above the touch"
        assert pos.entry_price == pytest.approx(63_000.0 * 1.0002)

    def test_short_entry_slips_the_other_way(self):
        b = broker()
        b.submit_order(intent(side=-1, entry=63_000.0, stop=63_500.0,
                              target=62_000.0))
        b.process_market_event(tick(63_000.0))
        assert list(b.positions.values())[0].entry_price < 63_000.0

    def test_target_exit_pays_maker_fee_stop_pays_taker(self):
        won = broker()
        won.submit_order(intent(iid="w"))
        won.process_market_event(tick(63_000.0, ts=1786560300))
        won.process_market_event(tick(64_500.0, ts=1786560360))
        w = list(won.positions.values())[0]

        lost = broker()
        lost.submit_order(intent(iid="l"))
        lost.process_market_event(tick(63_000.0, ts=1786560300))
        lost.process_market_event(tick(62_400.0, ts=1786560360))
        l = list(lost.positions.values())[0]

        assert w.exit_fee < l.exit_fee, "maker exit must be cheaper than taker"

    def test_fees_include_gst(self):
        b = broker()
        b.submit_order(intent(qty=100, entry=63_000.0))
        b.process_market_event(tick(63_000.0, ts=BAR_CLOSE + 2))
        pos = list(b.positions.values())[0]
        # Charged on the quantity actually filled, not the one approved.
        notional = BTC.notional(pos.quantity, pos.entry_price)
        expected = notional * (0.0005 * 1.18 + 0.0002)
        assert pos.entry_fee == pytest.approx(expected)


# =====================================================================
# LIFECYCLE, IDEMPOTENCY, ACCOUNTING
# =====================================================================


class TestLifecycle:
    def test_replaying_an_intent_does_not_double_fill(self):
        b = broker()
        i = intent()
        assert b.submit_order(i) is not None
        assert b.submit_order(i) is None, "replay must be a no-op"
        assert len(b.get_open_orders()) == 1

    def test_second_position_in_the_same_symbol_is_refused(self):
        b = broker()
        b.submit_order(intent(iid="a"))
        b.process_market_event(tick(63_000.0))
        assert b.submit_order(intent(iid="b")) is None
        assert len(b.get_positions()) == 1

    def test_position_reopens_after_the_first_closes(self):
        b = broker()
        b.submit_order(intent(iid="a"))
        b.process_market_event(tick(63_000.0, ts=1786560300))
        b.process_market_event(tick(64_500.0, ts=1786560360))
        assert b.submit_order(intent(iid="b")) is not None

    def test_r_multiple_is_about_two_on_a_target_hit(self):
        b = broker()
        b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_000.0))
        b.process_market_event(tick(63_000.0, ts=1786560300))
        b.process_market_event(tick(64_500.0, ts=1786560360))
        pos = list(b.positions.values())[0]
        assert 1.5 < pos.r_multiple < 2.0, (
            "2R gross minus fees and entry slippage")

    def test_r_multiple_is_about_minus_one_on_a_stop(self):
        b = broker()
        b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_000.0))
        b.process_market_event(tick(63_000.0, ts=1786560300))
        b.process_market_event(tick(62_500.0, mark=62_500.0, ts=1786560360))
        pos = list(b.positions.values())[0]
        assert -1.3 < pos.r_multiple < -1.0

    def test_equity_moves_by_realized_pnl(self):
        b = broker(10_000.0)
        b.submit_order(intent())
        b.process_market_event(tick(63_000.0, ts=1786560300))
        b.process_market_event(tick(64_500.0, ts=1786560360))
        pos = list(b.positions.values())[0]
        assert b.equity == pytest.approx(10_000.0 + pos.realized_pnl, rel=1e-9)

    def test_unrealized_pnl_tracks_price(self):
        b = broker()
        b.submit_order(intent())
        b.process_market_event(tick(63_000.0, ts=1786560300))
        b.process_market_event(tick(63_500.0, ts=1786560360))
        bal = b.get_balance()
        assert bal["unrealized"] > 0 and bal["open_positions"] == 1

    def test_cancel_and_modify(self):
        b = broker()
        o = b.submit_order(intent(order_type="limit", limit=62_900.0,
                                  entry=62_900.0, stop=62_400.0, target=63_900.0))
        assert b.modify_order(o.order_uid, limit_price=62_800.0) is True
        assert b.orders[o.order_uid].limit_price == 62_800.0
        assert b.cancel_order(o.order_uid) is True
        assert b.orders[o.order_uid].status is OrderStatus.CANCELLED
        assert b.cancel_order(o.order_uid) is False

    def test_quantity_is_whole_contracts_and_never_above_approval(self):
        b = broker()
        b.submit_order(intent(qty=137))
        b.process_market_event(tick(63_000.0, ts=BAR_CLOSE + 2))
        pos = list(b.positions.values())[0]
        assert isinstance(pos.quantity, int)
        # Even the modelled 2bps entry slippage widens the stop distance, so
        # the size comes down to hold the risk budget. It never goes up.
        assert 0 < pos.quantity <= 137


# =====================================================================
# HALT BEHAVIOUR
# =====================================================================


class TestHaltBehaviour:
    def test_suspended_positions_do_not_trigger_stops(self):
        """Delta does not trigger stops during maintenance; neither do we."""
        b = broker()
        b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_000.0))
        b.process_market_event(tick(63_000.0, ts=1786560300))
        assert b.suspend("BTCUSD") == 1
        b.process_market_event(tick(60_000.0, mark=60_000.0, ts=1786560360))
        pos = list(b.positions.values())[0]
        assert pos.status == "SUSPENDED"
        assert pos.exit_reason is None, "no fill is possible during a halt"

    def test_suspended_positions_survive_the_bar_path_too(self):
        b = broker()
        b.submit_order(intent())
        b.process_market_event(tick(63_000.0, ts=1786560300))
        b.suspend("BTCUSD")
        b.process_bar(bar(1786560360, 63_000.0, 64_500.0, 60_000.0, 61_000.0))
        assert list(b.positions.values())[0].exit_reason is None

    def test_resume_restores_triggering(self):
        b = broker()
        b.submit_order(intent())
        b.process_market_event(tick(63_000.0, ts=1786560300))
        b.suspend("BTCUSD")
        assert b.resume("BTCUSD") == 1
        b.process_market_event(tick(62_400.0, mark=62_400.0, ts=1786560420))
        assert list(b.positions.values())[0].exit_reason == ExitReason.STOP_LOSS.value

    def test_stop_and_position_are_preserved_across_a_halt(self):
        b = broker()
        b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_000.0))
        b.process_market_event(tick(63_000.0, ts=1786560300))
        before = list(b.positions.values())[0]
        stop, target, qty = before.stop_price, before.target_price, before.quantity
        b.suspend("BTCUSD"); b.resume("BTCUSD")
        after = list(b.positions.values())[0]
        assert (after.stop_price, after.target_price, after.quantity) == (stop, target, qty)

    def test_force_close_records_a_system_reason(self):
        b = broker()
        b.submit_order(intent())
        b.process_market_event(tick(63_000.0, ts=1786560300))
        pos = list(b.positions.values())[0]
        b.force_close(pos, 62_900.0, ExitReason.SYSTEM_SAFETY, 1786560400)
        assert pos.exit_reason == ExitReason.SYSTEM_SAFETY.value
        assert pos.status == "CLOSED"


# =====================================================================
# ENTRY DISCIPLINE -- found by replaying real data end to end
# =====================================================================


class TestEntryDiscipline:
    """A 4000-bar replay accumulated 37 unfilled entry orders.

    Live, a market entry fills on the next tick, so this never showed up in the
    tick tests. But a feed that stops delivering is exactly the case where
    orders pile up silently -- and then all fill at once, at prices minutes
    away from the ones the risk engine sized against, when it resumes.
    """

    def test_an_unfilled_entry_expires(self):
        b = broker()
        o = b.submit_order(intent())
        b.process_market_event(tick(63_000.0, ts=BAR_CLOSE + 200))
        assert b.orders[o.order_uid].status is OrderStatus.EXPIRED
        assert b.get_positions() == []

    def test_expiry_is_swept_even_with_no_ticks_at_all(self):
        b = broker()
        o = b.submit_order(intent())
        assert b.expire_stale_entries(BAR_CLOSE + 30) == []
        events = b.expire_stale_entries(BAR_CLOSE + 200)
        assert len(events) == 1 and events[0].kind == "ORDER_EXPIRED"
        assert b.orders[o.order_uid].status is OrderStatus.EXPIRED

    def test_a_prompt_tick_still_fills(self):
        b = broker()
        b.submit_order(intent())
        b.process_market_event(tick(63_000.0, ts=BAR_CLOSE + 2))
        assert len(b.get_positions()) == 1

    def test_the_bot_does_not_chase_a_price_that_ran_away(self):
        """The stop was sized against the reference price.

        Filling 1% above it keeps the position size but widens the real stop
        distance, so realised risk silently exceeds the budget -- and chasing a
        move that already happened is the behaviour this bot exists to prevent.
        """
        b = broker()
        o = b.submit_order(intent(entry=63_000.0))
        b.process_market_event(tick(63_700.0, ts=BAR_CLOSE + 2))
        assert b.orders[o.order_uid].status is OrderStatus.CANCELLED
        assert b.get_positions() == []

    def test_a_small_move_is_tolerated(self):
        b = broker()
        b.submit_order(intent(entry=63_000.0))
        b.process_market_event(tick(63_005.0, ts=BAR_CLOSE + 2))
        assert len(b.get_positions()) == 1

    def test_the_refusal_reason_is_recorded(self):
        b = broker()
        b.submit_order(intent(entry=63_000.0))
        evs = b.process_market_event(tick(63_700.0, ts=BAR_CLOSE + 2))
        cancels = [e for e in evs if e.kind == "ORDER_CANCELLED"]
        assert cancels and "refusing to chase" in cancels[0].payload["reason"]

    def test_expiry_frees_the_symbol_for_a_later_setup(self):
        b = broker()
        b.submit_order(intent(iid="a"))
        b.expire_stale_entries(BAR_CLOSE + 200)
        assert b.submit_order(intent(iid="b", bar_open=BAR_CLOSE + 300)) is not None

    def test_ttl_can_be_disabled_for_replay(self):
        b = PaperBroker(COSTS, starting_equity=10_000.0, entry_ttl_seconds=0,
                        max_entry_deviation=0.0)
        b.submit_order(intent())
        b.process_market_event(tick(63_000.0, ts=BAR_CLOSE + 100_000))
        assert len(b.get_positions()) == 1


class TestRiskPreservingFills:
    """The risk engine sizes against a reference price; the fill lands elsewhere.

    Measured on a real BTCUSD short: the entry slipped 12.4 points -- 0.019% of
    price, well inside any sane price band -- but the stop was 143 points away,
    so realised risk went from a $50 budget to $54.35. A price-based guard
    cannot catch that. The size has to come down instead.
    """

    def test_adverse_slip_reduces_the_size_not_the_budget(self):
        b = broker()
        # $50 budget over a $500 stop buys 100 contracts at the reference.
        b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_000.0,
                              qty=100))
        b.process_market_event(tick(63_010.0, ts=BAR_CLOSE + 2))
        pos = list(b.positions.values())[0]
        assert pos.quantity < 100, "an adverse fill must shrink the position"
        assert pos.initial_risk <= 50.0 * 1.000001, (
            f"realised risk {pos.initial_risk} breached the $50 budget")

    def test_a_favourable_fill_does_not_licence_a_bigger_position(self):
        b = broker()
        b.submit_order(intent(side=-1, entry=63_000.0, stop=63_500.0,
                              target=62_000.0, qty=100))
        # A short filling higher than reference is favourable.
        b.process_market_event(tick(63_100.0, ts=BAR_CLOSE + 2))
        pos = list(b.positions.values())[0]
        assert pos.quantity == 100, "never size above what risk approved"

    def test_the_resize_is_recorded(self):
        b = broker()
        b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_000.0,
                              qty=100))
        evs = b.process_market_event(tick(63_010.0, ts=BAR_CLOSE + 2))
        resized = [e for e in evs if e.kind == "ORDER_RESIZED"]
        assert resized, "a size change must never be silent"
        assert resized[0].payload["approved"] == 100
        assert resized[0].payload["filled"] < 100

    def test_a_fill_past_the_stop_is_refused_outright(self):
        b = broker()
        b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_000.0))
        b.process_market_event(tick(62_400.0, ts=BAR_CLOSE + 2))
        assert b.get_positions() == [], "entering already beyond the stop is absurd"

    def test_chasing_is_measured_in_R_not_percent(self):
        """0.019% of price was 8.7% of R on the case that exposed this."""
        b = broker()
        # Stop is $500 away; a $150 slip is 0.30R, past the 0.25R limit.
        b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_000.0))
        b.process_market_event(tick(63_150.0, ts=BAR_CLOSE + 2))
        assert b.get_positions() == []
        # The same $150 slip against a $5000 stop is 0.03R -- ordinary.
        b2 = broker()
        b2.submit_order(intent(entry=63_000.0, stop=58_000.0, target=73_000.0,
                               qty=10))
        b2.process_market_event(tick(63_150.0, ts=BAR_CLOSE + 2))
        assert len(b2.get_positions()) == 1


class TestFillTimeRewardRisk:
    """`minimum_rr` gates the PLAN; this gates the FILL.

    The strategy sets target = entry + 2R, so the planned reward/risk is
    exactly 2.0 on every signal and the risk engine's check passes at the
    boundary by construction. Once the entry slips the stop widens and the
    target narrows at the same time, so realised RR is always lower --
    measured at 1.75 on real data. Without a fill-time floor, "maintain
    minimum risk/reward" would be enforced against a number that can never
    fail.
    """

    def test_realised_rr_is_recorded_on_the_position(self):
        b = broker()
        b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_000.0))
        b.process_market_event(tick(63_000.0, ts=BAR_CLOSE + 2))
        pos = list(b.positions.values())[0]
        assert pos.fill_rr is not None
        assert pos.fill_rr < 2.0, "an adverse fill always degrades the plan"
        assert pos.fill_rr >= b.min_fill_rr

    def test_a_fill_below_the_floor_is_refused(self):
        b = broker()
        b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_000.0))
        # Fill at 63,100: RR = 900/600 = 1.50, under the 1.7 floor.
        b.process_market_event(tick(63_087.0, ts=BAR_CLOSE + 2))
        assert b.get_positions() == []

    def test_the_refusal_names_both_figures(self):
        b = broker()
        b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_000.0))
        evs = b.process_market_event(tick(63_087.0, ts=BAR_CLOSE + 2))
        killed = [e for e in evs if e.kind in ("ORDER_CANCELLED", "ORDER_EXPIRED")]
        assert killed
        reason = killed[0].payload["reason"]
        assert "reward/risk at the actual fill" in reason and "planned" in reason

    def test_a_favourable_fill_improves_rr(self):
        b = broker()
        b.submit_order(intent(side=-1, entry=63_000.0, stop=63_500.0,
                              target=62_000.0))
        b.process_market_event(tick(63_050.0, ts=BAR_CLOSE + 2))
        pos = list(b.positions.values())[0]
        assert pos.fill_rr > 2.0

    def test_the_floor_can_be_disabled_for_replay(self):
        b = PaperBroker(COSTS, starting_equity=10_000.0, min_fill_rr=0.0,
                        max_entry_deviation=0.0)
        b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_000.0))
        b.process_market_event(tick(63_200.0, ts=BAR_CLOSE + 2))
        assert len(b.get_positions()) == 1
