"""RiskGates.entry_filter: a research hook that can refuse an entry after the cost gate.

Same fixture as test_portfolio_pause: three long signals on one symbol (bars
1, 4, 20), each trade time-exits three bars after it opens.
"""
from __future__ import annotations

import dataclasses

from deltabt.portfolio import RiskGates

from tests.test_portfolio_pause import _entry_bars, _run
from tests.test_portfolio_ladder import T0


def test_no_filter_takes_every_signal():
    trades, rejects = _run(RiskGates.off())
    assert _entry_bars(trades) == [1, 4, 20]
    assert rejects["entry_filter"] == 0


def test_the_filter_sees_symbol_time_and_cost_per_r_and_can_refuse():
    seen = []

    def keep(sym, ts, cpr):
        seen.append((sym, int((ts - T0) // 300), cpr))
        return int((ts - T0) // 300) != 4

    trades, rejects = _run(dataclasses.replace(RiskGates.off(), entry_filter=keep))
    assert _entry_bars(trades) == [1, 20]
    assert rejects["entry_filter"] == 1
    assert [b for _, b, _ in seen] == [1, 4, 20]
    assert all(sym == "TEST" and 0 < cpr < float("inf") for sym, _, cpr in seen)


def test_a_refused_entry_leaves_the_slot_free_for_the_next_signal():
    trades, _ = _run(dataclasses.replace(RiskGates.off(), entry_filter=lambda s, t, c: int((t - T0) // 300) != 1))
    assert _entry_bars(trades) == [4, 20]
