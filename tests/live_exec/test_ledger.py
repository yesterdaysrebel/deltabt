"""Mapping venue facts to ledger facts.

THE FIXTURES BELOW ARE REAL. Every field name and value came off testnet on
2026-09-15 -- a genuine fill and a genuine order-history row from the position
smoke test -- rather than from documentation or from what I expected. Four
earlier rounds of believing the docs cost four bugs.
"""

from __future__ import annotations

import pytest

from live.ledger import (UNKNOWN_REASON, close_facts, entry_facts,
                         exit_reason, find_by_client_order_id, parse_venue_time,
                         r_multiple)

#: Verbatim from /v2/orders/history after the smoke test closed its position.
REAL_CLOSE = {
    "id": 2172327703,
    "client_order_id": "d91de447cdfe70e1",
    "product_id": 84,
    "product_symbol": "BTCUSD",
    "side": "sell",
    "size": 1,
    "unfilled_size": 0,
    "order_type": "market_order",
    "stop_order_type": None,
    "reduce_only": True,
    "state": "closed",
    "average_fill_price": "77194.5",
    "commission": "0",
    "paid_commission": "0.04554476",
    "created_at": "2026-09-15T06:55:54.630597Z",
    "updated_at": "2026-09-15T06:55:54.700000Z",
    "meta_data": {"avg_exit_price": "77194.5", "entry_price": "77195.00000000",
                  "pnl": "-0.0005", "cashflow": "-0.0005"},
}


def a_bracket_close(kind):
    row = dict(REAL_CLOSE, stop_order_type=kind, reduce_only=False)
    row["client_order_id"] = None      # bracket legs are created by the venue
    return row


# -- exit reason, the derived one -------------------------------------------

def test_a_stop_leg_is_recorded_as_a_stop():
    assert exit_reason(a_bracket_close("stop_loss_order")) == "STOP_LOSS"


def test_a_target_leg_is_recorded_as_a_take_profit():
    assert exit_reason(a_bracket_close("take_profit_order")) == "TAKE_PROFIT"


def test_our_own_flatten_records_the_reason_we_asked_for():
    assert exit_reason(REAL_CLOSE, requested="time_exit") == "TIME_EXIT"


def test_a_reduce_only_close_with_no_stated_reason_is_a_manual_close():
    assert exit_reason(REAL_CLOSE) == "MANUAL_CLOSE"


def test_a_bracket_wins_over_a_reason_we_asked_for():
    """If a bracket fires while we are also trying to flatten, the bracket is
    what actually closed it and the record must say so."""
    row = dict(a_bracket_close("stop_loss_order"), reduce_only=True)
    assert exit_reason(row, requested="time_exit") == "STOP_LOSS"


def test_an_unexplained_close_is_visibly_unexplained():
    """Not filed under a plausible heading. A close nobody can explain is a
    fact about the system, and hiding it as TIME_EXIT loses it."""
    row = dict(REAL_CLOSE, reduce_only=False, stop_order_type=None)
    assert exit_reason(row) == UNKNOWN_REASON == "UNKNOWN_EXIT"


# -- the venue-side facts ----------------------------------------------------

def test_close_facts_read_the_real_row():
    f = close_facts(REAL_CLOSE, requested="time_exit")
    assert f["exit_price"] == 77194.5
    assert f["exit_fee"] == 0.04554476     # paid_commission, not commission
    assert f["realized_pnl"] == -0.0005
    assert f["exit_reason"] == "TIME_EXIT"
    assert f["exit_client_order_id"] == "d91de447cdfe70e1"


def test_the_fee_comes_from_paid_commission_not_commission():
    """`commission` was "0" on the real row while `paid_commission` was the
    number actually charged. Reading the wrong one records every trade as free."""
    assert close_facts(REAL_CLOSE)["exit_fee"] == 0.04554476


def test_the_venues_own_pnl_is_preferred_to_anything_recomputed():
    """It is what the account was credited, rounding included. A number we
    derive that disagrees with the balance is worse than no number."""
    assert close_facts(REAL_CLOSE)["realized_pnl"] == -0.0005


def test_a_missing_pnl_is_none_not_zero():
    row = dict(REAL_CLOSE, meta_data={})
    assert close_facts(row)["realized_pnl"] is None


def test_entry_facts_read_the_real_row():
    f = entry_facts(REAL_CLOSE)
    assert f["entry_price"] == 77194.5
    assert f["filled"] == 1
    assert f["opened_at"] == parse_venue_time("2026-09-15T06:55:54.630597Z")


def test_a_partial_fill_is_visible_in_the_entry_facts():
    assert entry_facts(dict(REAL_CLOSE, size=10, unfilled_size=4))["filled"] == 6


# -- timestamps --------------------------------------------------------------

def test_iso_timestamps_parse_to_epoch_seconds():
    t = parse_venue_time("2026-09-15T06:55:54.634665Z")
    assert isinstance(t, int) and 1_700_000_000 < t < 2_000_000_000


@pytest.mark.parametrize("bad", [None, "", "not a time"])
def test_an_unparseable_timestamp_is_none_not_a_crash(bad):
    assert parse_venue_time(bad) is None


def test_an_epoch_that_is_already_a_number_is_passed_through():
    assert parse_venue_time(1788768000) == 1788768000


# -- R -----------------------------------------------------------------------

def test_r_is_computed_on_the_actual_fills():
    assert r_multiple(100.0, 97.0, side=1, risk_per_unit=3.0) == pytest.approx(-1.0)
    assert r_multiple(100.0, 103.0, side=-1, risk_per_unit=3.0) == pytest.approx(-1.0)


def test_missing_risk_gives_none_not_a_scratch_trade():
    """0.0 would average in as a flat trade and quietly drag the mean."""
    assert r_multiple(100.0, 97.0, side=1, risk_per_unit=0.0) is None


# -- attribution -------------------------------------------------------------

def test_our_own_id_is_what_links_a_venue_order_back_to_an_intent():
    rows = [{"client_order_id": "other"}, REAL_CLOSE]
    assert find_by_client_order_id(rows, "d91de447cdfe70e1")["id"] == 2172327703
    assert find_by_client_order_id(rows, "nope") is None
    assert find_by_client_order_id(None, "x") is None
