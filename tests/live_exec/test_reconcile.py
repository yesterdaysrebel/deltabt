"""Reconciliation must catch every way the venue and the ledger can disagree,
and must never "fix" one.
"""

from __future__ import annotations

import pytest

from live.reconcile import Verdict, reconcile


def venue(symbol, size, side=None, product_id=None):
    row = {"product_symbol": symbol, "size": size}
    if side:
        row["side"] = side
    if product_id:
        row["product_id"] = product_id
        row.pop("product_symbol")
    return row


def ours(symbol, size):
    return {"symbol": symbol, "size": size}


def test_agreement_is_silent():
    r = reconcile([venue("BEATUSD", 565)], [ours("BEATUSD", 565)])
    assert r.ok and r.verdict is Verdict.AGREED and not r.discrepancies
    assert "agree" in r.render()


def test_flat_rows_are_not_positions():
    """The venue returns rows for products with zero size."""
    r = reconcile([venue("BEATUSD", 0), venue("AKEUSD", 0)], [])
    assert r.ok


def test_a_position_we_do_not_know_about_halts():
    """The case a crash between fill and database write produces."""
    r = reconcile([venue("BEATUSD", 565)], [])
    assert not r.ok
    assert r.discrepancies[0].kind == "UNKNOWN_POSITION"
    assert "NOT closed automatically" in r.discrepancies[0].detail


def test_a_position_that_vanished_halts():
    """An exchange-held bracket fired, or we were liquidated, unseen."""
    r = reconcile([], [ours("BEATUSD", 565)])
    assert not r.ok
    assert r.discrepancies[0].kind == "VANISHED_POSITION"


def test_opposite_directions_halt():
    r = reconcile([venue("BEATUSD", -565)], [ours("BEATUSD", 565)])
    assert not r.ok
    assert r.discrepancies[0].kind == "WRONG_DIRECTION"


def test_a_partial_fill_that_was_never_recorded_halts():
    r = reconcile([venue("BEATUSD", 300)], [ours("BEATUSD", 565)])
    assert not r.ok
    assert r.discrepancies[0].kind == "SIZE_MISMATCH"


def test_a_short_is_not_read_as_a_long():
    """Reading an unsigned size as a long turns a short into a phantom
    double position -- agreement here must depend on the side field."""
    r = reconcile([venue("BEATUSD", 565, side="sell")], [ours("BEATUSD", -565)])
    assert r.ok, r.render()
    bad = reconcile([venue("BEATUSD", 565, side="sell")], [ours("BEATUSD", 565)])
    assert not bad.ok


def test_a_position_we_cannot_name_is_still_reported():
    """An unmappable product must not be silently dropped: unnameable is the
    most dangerous kind, because it cannot be matched to anything."""
    r = reconcile([venue(None, 10, product_id=27)], [], symbol_for={})
    assert not r.ok
    assert "product_id=27" in r.discrepancies[0].symbol


def test_product_ids_are_mapped_to_symbols_when_possible():
    r = reconcile([venue(None, 565, product_id=27)], [ours("BEATUSD", 565)],
                  symbol_for={27: "BEATUSD"})
    assert r.ok, r.render()


def test_ledger_rows_may_use_contracts_and_side():
    r = reconcile([venue("BEATUSD", -565)],
                  [{"symbol": "BEATUSD", "contracts": 565, "side": "SHORT"}])
    assert r.ok, r.render()


def test_sizes_that_arrive_as_strings_or_floats_compare_equal():
    r = reconcile([venue("BEATUSD", "565")], [ours("BEATUSD", 565.0)])
    assert r.ok


def test_every_disagreeing_symbol_is_listed_not_just_the_first():
    r = reconcile([venue("BEATUSD", 565), venue("AKEUSD", 10)],
                  [ours("BANKUSD", 20)])
    kinds = {(d.symbol, d.kind) for d in r.discrepancies}
    assert kinds == {("BEATUSD", "UNKNOWN_POSITION"),
                     ("AKEUSD", "UNKNOWN_POSITION"),
                     ("BANKUSD", "VANISHED_POSITION")}
    assert r.checked == 3


def test_the_report_says_nothing_was_changed():
    """The whole design is that it does not act. That must be legible to
    whoever reads the alarm at 3am."""
    r = reconcile([venue("BEATUSD", 565)], [])
    assert "Nothing has been closed or adopted automatically" in r.render()


@pytest.mark.parametrize("bad", [None, "", 0])
def test_ledger_rows_without_a_symbol_are_ignored_not_crashed_on(bad):
    r = reconcile([], [{"symbol": bad, "size": 10}])
    assert r.ok
