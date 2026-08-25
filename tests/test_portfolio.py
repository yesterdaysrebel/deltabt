"""``run_portfolio`` against ``run_backtest``, and the gates against each other.

THE LOAD-BEARING TEST IS THE FIRST ONE
    ``run_portfolio`` reimplements the entry, exit, fee and funding arithmetic
    so it can arbitrate between symbols. That is exactly the duplication this
    repository keeps getting burned by, so it is pinned: with ONE symbol and
    every gate disabled, the portfolio simulator must reproduce
    ``run_backtest``'s trade list field for field. Any later difference is then
    attributable to gates or to concurrency, and never to drift.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.engine import run_backtest
from deltabt.harness import load_symbol, params_for
from deltabt.portfolio import Book, RiskGates, run_portfolio
from deltabt.strategy import resample_ohlcv

CANDLES = Path("data/candles")
SYMBOL = "BTCUSD"
BARS = 60_000

pytestmark = pytest.mark.skipif(
    not (CANDLES / SYMBOL / "ltp_1m.parquet").exists(),
    reason="BTCUSD 1m candles not cached")


def _book(symbol: str, family: str, minutes: int):
    data = load_symbol(symbol)
    data["ltp"] = data["ltp"].tail(BARS).reset_index(drop=True)
    data["tradable"] = data["tradable"][-BARS:]

    spec = build_spec(family, minutes)
    primary = resample_ohlcv(data["ltp"], minutes).iloc[:-1].reset_index(drop=True)
    tradable = data["tradable"][: len(primary)] if minutes == 1 else None
    if tradable is None:
        from deltabt.strategy import resample_tradable
        tradable = resample_tradable(data["ltp"], data["tradable"], minutes)[: len(primary)]
    confirm = (data["ltp"] if spec.confirm_minutes == 1
               else resample_ohlcv(data["ltp"], spec.confirm_minutes)
               .iloc[:-1].reset_index(drop=True)) if spec.confirm.enabled else None
    mark = resample_ohlcv(data["mark"], minutes) if minutes > 1 else data["mark"]

    sig = rulecore.to_engine_signals(rulecore.compute(primary, confirm, spec))
    costs = SymbolCosts.from_spec(ProductCatalog().get(symbol))
    book = Book(symbol=symbol, bars=primary, signals=sig, costs=costs,
                mark=mark, tradable=tradable)
    book.funding_df = data["funding"]
    return data, spec, book, primary, mark, costs, tradable


@pytest.mark.parametrize("family,minutes", [("hwpr_v2", 15), ("trend_wide_stop", 60),
                                            ("adx_only", 30)])
def test_matches_run_backtest_with_one_symbol_and_no_gates(family, minutes):
    """The whole basis for trusting this module."""
    data, spec, book, primary, mark, costs, tradable = _book(SYMBOL, family, minutes)
    params = params_for(spec, minutes)

    solo = run_backtest(primary, mark, data["funding"], book.signals, params,
                        costs, tradable=tradable)
    port = run_portfolio({SYMBOL: book}, params, RiskGates.off(),
                         funding={SYMBOL: data["funding"]})

    assert len(port.trades) == len(solo.trades), (
        f"{family}@{minutes}m: {len(port.trades)} portfolio trades against "
        f"{len(solo.trades)} from run_backtest")
    assert solo.trades, "no trades -- this test would be vacuous"

    for a, b in zip(solo.trades, port.trades):
        for f in ("entry_time", "exit_time", "side", "contracts", "exit_reason"):
            assert getattr(a, f) == getattr(b, f), f"{f} differs on {a.entry_time}"
        for f in ("entry_price", "exit_price", "stop_price", "target_price",
                  "pnl", "fees", "funding", "r_multiple"):
            assert getattr(a, f) == pytest.approx(getattr(b, f), rel=1e-9, abs=1e-9), \
                f"{f} differs on {a.entry_time}"


def test_one_slot_across_symbols_takes_fewer_trades_than_the_sum():
    """Concurrency is the whole point: N symbols do not get N position slots."""
    symbols = ["BTCUSD", "ETHUSD", "SOLUSD"]
    books, solo_total = {}, 0
    for s in symbols:
        data, spec, book, primary, mark, costs, tradable = _book(s, "trend_wide_stop", 60)
        books[s] = book
        params = params_for(spec, 60)
        solo_total += len(run_backtest(primary, mark, data["funding"], book.signals,
                                       params, costs, tradable=tradable).trades)

    port = run_portfolio(books, params, RiskGates.off(),
                         funding={s: b.funding_df for s, b in books.items()})
    assert 0 < len(port.trades) < solo_total, (
        f"one slot took {len(port.trades)} of {solo_total} per-symbol trades")
    assert port.rejects["max_open_positions"] > 0, "no contention observed"


def test_gates_only_ever_reduce_trades():
    """Every gate is a refusal. None of them can create a trade.

    Run at 5m, where the trade rate is high enough for the daily gates to bind
    at all -- see ``test_daily_reset_gates_are_inert_at_a_low_trade_rate``.
    """
    books = {}
    for s in ["BTCUSD", "ETHUSD"]:
        _, spec, book, *_ = _book(s, "trend_wide_stop", 5)
        books[s] = book
    params = params_for(spec, 5)
    fund = {s: b.funding_df for s, b in books.items()}

    ungated = run_portfolio(books, params, RiskGates.off(), funding=fund)
    gated = run_portfolio(books, params, RiskGates(), funding=fund)
    assert len(gated.trades) <= len(ungated.trades)
    assert sum(gated.rejects[k] for k in
               ("max_trades_per_day", "max_daily_loss", "max_drawdown",
                "max_consecutive_losses")) > 0, "no gate ever fired"


def test_a_daily_trade_cap_is_respected():
    books = {}
    for s in ["BTCUSD", "ETHUSD", "SOLUSD"]:
        _, spec, book, *_ = _book(s, "trend_wide_stop", 60)
        books[s] = book
    params = params_for(spec, 60)
    cap = 2
    res = run_portfolio(books, params,
                        RiskGates(max_trades_per_day=cap, max_daily_loss_pct=1.0,
                                  max_drawdown_pct=1.0, max_consecutive_losses=0),
                        funding={s: b.funding_df for s, b in books.items()})
    assert res.trades
    per_day = pd.Series([t.entry_time // 86_400 for t in res.trades]).value_counts()
    assert per_day.max() <= cap, f"{per_day.max()} entries on one day, cap was {cap}"


def test_daily_reset_gates_are_inert_at_a_low_trade_rate():
    """A 3-loss daily breaker cannot fire on a system taking ~2 trades a day.

    Not a defect -- an arithmetic consequence worth pinning, because the gate
    looks like protection and provides none at this frequency. Measured on
    BTCUSD+ETHUSD at 60m: 22 trades over 19 days, at most 2 entries on any one
    day, and therefore zero days on which three losses could accumulate. At 5m
    the same pair takes 2.13 trades/day with a maximum of 5, and 5 of 31 days
    carry three or more losses.
    """
    books = {}
    for s in ["BTCUSD", "ETHUSD"]:
        _, spec, book, *_ = _book(s, "trend_wide_stop", 60)
        books[s] = book
    params = params_for(spec, 60)
    res = run_portfolio(books, params,
                        RiskGates(max_trades_per_day=10**9, max_daily_loss_pct=1.0,
                                  max_drawdown_pct=1.0, max_consecutive_losses=3),
                        funding={s: b.funding_df for s, b in books.items()})
    assert res.trades
    per_day = pd.Series([t.entry_time // 86_400 for t in res.trades]).value_counts()
    assert per_day.max() < 3, "trade rate is now high enough for the gate to bind"
    assert res.rejects["max_consecutive_losses"] == 0, (
        "the streak gate fired despite fewer than three entries on every day")


def test_the_loss_streak_resets_on_the_day_roll():
    """A streak breaker that only clears on a win is a permanent silent halt.

    ``app/risk/engine.py`` resets it on the day roll for exactly this reason;
    without that, clearing requires a win and a win requires an entry.
    """
    books = {}
    for s in ["BTCUSD", "ETHUSD"]:
        _, spec, book, *_ = _book(s, "trend_wide_stop", 5)
        books[s] = book
    params = params_for(spec, 5)
    res = run_portfolio(books, params,
                        RiskGates(max_trades_per_day=10**9, max_daily_loss_pct=1.0,
                                  max_drawdown_pct=1.0, max_consecutive_losses=2),
                        funding={s: b.funding_df for s, b in books.items()})
    assert res.rejects["max_consecutive_losses"] > 0, "the streak gate never fired"
    days = sorted({t.entry_time // 86_400 for t in res.trades})
    assert len(days) > 5, (
        "trading stopped after the first streak -- the breaker is behaving as "
        "a permanent halt rather than a daily one")


def test_a_drawdown_halt_is_terminal_without_a_resume_policy():
    """The halt cannot lift on its own -- that is the whole defect.

    Entries are refused, so nothing closes, so equity never moves, so the
    drawdown never recovers. A backtest that ignores this reports a smaller
    loss for a run that simply stopped trading.
    """
    books = {}
    for s in ["BTCUSD", "ETHUSD", "SOLUSD"]:
        _, spec, book, *_ = _book(s, "trend_wide_stop", 60)
        books[s] = book
    params = params_for(spec, 60)
    fund = {s: b.funding_df for s, b in books.items()}
    # 1%, not 10%: the fixture window is ~41 days at 60m and takes 25 trades,
    # which never reaches a wider limit. The point is to exercise the halt.
    tight = RiskGates(max_trades_per_day=2, max_daily_loss_pct=1.0,
                      max_drawdown_pct=0.01, max_consecutive_losses=0)

    res = run_portfolio(books, params, tight, funding=fund)
    assert res.halts, "the drawdown limit was never reached; pick a tighter one"
    halt_ts = res.halts[0][0]
    assert all(t.entry_time <= halt_ts for t in res.trades), (
        "an entry was taken after the halt latched")

    span = int(res.equity_time[-1]) - int(res.equity_time[0])
    used = (max(t.exit_time for t in res.trades) - int(res.equity_time[0])) / span
    assert used < 0.95, (
        f"the account traded {100*used:.0f}% of the window -- this test is "
        f"meant to exercise a run that stopped early")


def test_a_resume_policy_puts_the_account_back_to_work():
    """Modelling an operator resume must actually extend the run."""
    books = {}
    for s in ["BTCUSD", "ETHUSD", "SOLUSD"]:
        _, spec, book, *_ = _book(s, "trend_wide_stop", 60)
        books[s] = book
    params = params_for(spec, 60)
    fund = {s: b.funding_df for s, b in books.items()}
    base = dict(max_trades_per_day=2, max_daily_loss_pct=1.0,
                max_drawdown_pct=0.01, max_consecutive_losses=0)

    terminal = run_portfolio(books, params, RiskGates(**base), funding=fund)
    resumed = run_portfolio(books, params,
                            RiskGates(**base, resume_after_days=7), funding=fund)

    assert len(resumed.trades) > len(terminal.trades), (
        "resuming after 7 days did not produce a single extra trade")
    assert len(resumed.halts) > len(terminal.halts), (
        "a resumed account should be able to halt more than once")
    last_terminal = max(t.exit_time for t in terminal.trades)
    last_resumed = max(t.exit_time for t in resumed.trades)
    assert last_resumed > last_terminal, "the resumed run did not reach further"
