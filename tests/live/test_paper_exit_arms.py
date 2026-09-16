"""The two exit arms, in the PAPER execution path.

Both mechanisms existed only in deltabt/engine.py, the backtest engine. The
paper broker -- which is what every paper stack actually runs -- had neither,
so deploying an arm that named one would have run the stock rule and produced
a third identical result set. These tests drive real ticks through the broker.

Ordering is the part worth being careful about: a tick must not be able to both
promote the stop and be stopped out by its own promotion. deltabt/engine.py
tests the stop first and promotes afterwards, and so does this.
"""
from __future__ import annotations

import pytest

from app.execution.paper_broker import ExitReason, PaperBroker
from tests.live.test_paper_execution import BAR_CLOSE, COSTS, US, intent, tick

LADDER = ((0.5, 0.0), (1.0, 0.5), (1.5, 1.0), (2.0, 1.5))


def broker(*, ladder=(), stop_trigger="mark", equity=10_000.0):
    return PaperBroker(COSTS, starting_equity=equity, slippage_bps=2.0,
                       ladder_rungs=ladder, stop_trigger=stop_trigger)


def open_long(b, entry=63_000.0, stop=62_500.0, target=64_500.0):
    """Fill a long and return its position.

    R IS DERIVED FROM THE POSITION, never assumed. The entry fills with
    slippage -- 63,000 becomes 63,012.6 -- so risk_per_unit is 512.6, not the
    500 the intent asked for, and a rung hardcoded at +250 lands at 0.488R and
    silently fails to trigger.
    """
    b.submit_order(intent(entry=entry, stop=stop, target=target))
    b.process_market_event(tick(entry, ts=BAR_CLOSE))
    pos = list(b.positions.values())[0]
    assert pos.status == "OPEN"
    return pos


class TestLadder:
    def test_no_ladder_leaves_the_stop_where_it_was(self):
        """The regression guard: every recorded result was measured this way."""
        b = broker()
        pos = open_long(b)
        before = pos.stop_price
        b.process_market_event(tick(64_000.0, ts=BAR_CLOSE + 60))
        assert pos.stop_price == before
        assert pos.stop_promoted is False

    def test_first_rung_moves_the_stop_to_breakeven(self):
        b = broker(ladder=LADDER)
        pos = open_long(b)
        entry = pos.entry_price
        b.process_market_event(
            tick(entry + 0.6 * pos.risk_per_unit, ts=BAR_CLOSE + 60))
        assert pos.stop_promoted is True
        assert pos.stop_price == pytest.approx(entry, abs=0.5)

    def test_a_later_rung_locks_in_profit(self):
        b = broker(ladder=LADDER)
        pos = open_long(b)
        entry = pos.entry_price
        r = pos.risk_per_unit
        b.process_market_event(tick(entry + 2.0 * r, ts=BAR_CLOSE + 60))
        # +2.0R earns the (2.0, 1.5) rung: stop at entry + 1.5R.
        assert pos.stop_price == pytest.approx(entry + 1.5 * r, abs=0.5)

    def test_the_stop_never_moves_against_the_position(self):
        """Price coming back must not pull the stop back with it."""
        b = broker(ladder=LADDER)
        pos = open_long(b)
        entry = pos.entry_price
        r = pos.risk_per_unit
        b.process_market_event(tick(entry + 2.0 * r, ts=BAR_CLOSE + 60))
        high_water = pos.stop_price
        b.process_market_event(tick(entry + 0.6 * r, ts=BAR_CLOSE + 120))
        assert pos.stop_price == high_water

    def test_a_tick_cannot_both_promote_the_stop_and_be_stopped_by_it(self):
        """THE ordering test.

        A long that is already laddered to breakeven, on a tick that is far
        enough in front to earn a HIGHER rung but whose mark is at-or-below the
        stop it currently holds, must book the stop it actually had. Promoting
        first would let the same observation raise the stop and then exit at
        the raise -- booking a profit on a tick that really took the stop.
        """
        b = broker(ladder=LADDER)
        pos = open_long(b)
        entry = pos.entry_price
        r = pos.risk_per_unit
        # Earn the breakeven rung first.
        b.process_market_event(tick(entry + 0.6 * r, ts=BAR_CLOSE + 60))
        assert pos.stop_price == pytest.approx(entry, abs=0.5)
        promoted_to = pos.stop_price
        # Now a tick whose MARK is through that stop. LTP is far in front, so
        # a promote-first implementation would ratchet the stop up instead.
        b.process_market_event(tick(entry + 2.0 * r, mark=promoted_to - 10.0,
                                    ts=BAR_CLOSE + 120))
        assert pos.status == "CLOSED"
        assert pos.exit_reason == ExitReason.STOP_LOSS.value
        assert pos.stop_price == pytest.approx(promoted_to, abs=0.5)

    def test_a_promoted_stop_is_recorded_on_the_position(self):
        """So the arm's damage is attributable rather than inferred."""
        b = broker(ladder=LADDER)
        pos = open_long(b)
        entry = pos.entry_price
        b.process_market_event(
            tick(entry + 0.6 * pos.risk_per_unit, ts=BAR_CLOSE + 60))
        b.process_market_event(tick(entry - 10.0, mark=entry - 10.0,
                                    ts=BAR_CLOSE + 120))
        assert pos.status == "CLOSED"
        assert pos.stop_promoted is True

    def test_a_short_ladders_downward(self):
        b = broker(ladder=LADDER)
        b.submit_order(intent(side=-1, entry=63_000.0, stop=63_500.0,
                              target=61_500.0))
        b.process_market_event(tick(63_000.0, ts=BAR_CLOSE))
        pos = list(b.positions.values())[0]
        entry = pos.entry_price
        b.process_market_event(
            tick(entry - 0.6 * pos.risk_per_unit, ts=BAR_CLOSE + 60))
        assert pos.stop_promoted is True
        assert pos.stop_price == pytest.approx(entry, abs=0.5)
        assert pos.stop_price < 63_500.0


class TestStopTrigger:
    def test_mark_is_the_default_and_ltp_does_not_trigger_it(self):
        b = broker()
        pos = open_long(b)
        # LTP through the stop, mark comfortably above it.
        b.process_market_event(tick(62_400.0, mark=63_000.0,
                                    ts=BAR_CLOSE + 60))
        assert pos.status == "OPEN"

    def test_ltp_trigger_fires_where_mark_would_not(self):
        b = broker(stop_trigger="ltp")
        pos = open_long(b)
        b.process_market_event(tick(62_400.0, mark=63_000.0,
                                    ts=BAR_CLOSE + 60))
        assert pos.status == "CLOSED"
        assert pos.exit_reason == ExitReason.STOP_LOSS.value

    def test_ltp_trigger_ignores_a_mark_that_dipped_alone(self):
        """The mirror: mark through the stop, LTP not. An LTP arm holds."""
        b = broker(stop_trigger="ltp")
        pos = open_long(b)
        b.process_market_event(tick(63_000.0, mark=62_400.0,
                                    ts=BAR_CLOSE + 60))
        assert pos.status == "OPEN"

    def test_an_unknown_trigger_is_refused(self):
        with pytest.raises(ValueError, match="stop_trigger"):
            broker(stop_trigger="index")


class TestIdentity:
    """Each arm must be distinguishable from the baseline, and the baseline
    must not move -- a changed hash makes the RUNNING experiment unbindable."""

    def test_the_baseline_hash_is_unchanged_by_these_fields(self):
        from deltabt.catalog import build_spec
        assert build_spec("manual_scalp_both_t3", 5).config_hash.startswith(
            "41e764beceaf")

    def test_each_arm_hashes_differently(self):
        from deltabt.catalog import build_spec
        names = ("manual_scalp_both_t3", "manual_scalp_both_t3_ladder",
                 "manual_scalp_both_t3_ltp")
        hashes = {n: build_spec(n, 5).config_hash for n in names}
        assert len(set(hashes.values())) == 3, hashes

    def test_the_arms_differ_from_the_baseline_in_one_field_each(self):
        from dataclasses import replace

        from deltabt.catalog import build_spec
        base = build_spec("manual_scalp_both_t3", 5)
        ladder = build_spec("manual_scalp_both_t3_ladder", 5)
        ltp = build_spec("manual_scalp_both_t3_ltp", 5)
        # `name` carries the catalog key, so normalise it too: the claim is
        # that ONE behavioural field differs, not that the names match.
        assert replace(ladder, ladder_rungs=(), name=base.name) == base
        assert replace(ltp, stop_trigger="mark", name=base.name) == base
