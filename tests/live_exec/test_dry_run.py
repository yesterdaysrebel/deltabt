"""The prod dry run: live venue checks on every entry, and NO order ever sent.

The venue here fails the test on any write. A dry run that placed an order, set
leverage or cancelled anything would be a live bot its operator believes is
only observing -- the one failure this mode must not be able to have.
"""
from __future__ import annotations

import pytest

from app.execution.paper_broker import EXECUTION_FIELDS, PaperBroker
from app.persistence.repository import InMemoryRepository
from deltabt.costs import SymbolCosts
from live.broker import LiveBroker, OpeningRefused
from live.dry_run import DryRunBot, DryRunBroker
from tests.live_exec.test_live_order_placement import (PIDS, TICKS, FakeVenue,
                                                       approve)

COSTS = {"BTCUSD": SymbolCosts(symbol="BTCUSD", tick_size=0.1, contract_value=0.001,
                               maker_fee=0.0002, taker_fee=0.0005, max_leverage=100,
                               position_size_limit=100_000,
                               funding_interval_seconds=28_800)}


class ReadOnlyVenue(FakeVenue):
    """A read-only key: every write is a test failure."""

    def place_order(self, order):
        pytest.fail("the dry run placed an order")

    def set_order_leverage(self, product_id, leverage):
        pytest.fail("the dry run changed leverage on the venue")

    def cancel_order(self, order_id, product_id):
        pytest.fail("the dry run cancelled an order")

    available_usd = 0.0          # the account behind a read-only key may be empty


def dry(venue, tmp_path, *, equity=250.0, halt=False):
    paper = PaperBroker(COSTS, starting_equity=equity, slippage_bps=2.0)
    reader = LiveBroker(venue, product_ids=PIDS, experiment_id="dry-run",
                        tick_size=TICKS, kill_switch_path=str(tmp_path / "HALT"))
    reader.max_entry_deviation = paper.max_entry_deviation
    if halt:
        (tmp_path / "HALT").write_text("")
    return DryRunBroker(paper, reader, equity=equity,
                        kill_switch_path=str(tmp_path / "HALT"))


def test_an_entry_is_checked_recorded_and_simulated_without_any_write(tmp_path):
    b = dry(ReadOnlyVenue(), tmp_path)
    _, decision = approve("BTCUSD", 1)
    order = b.submit_order(decision.intent, now=1, order_uid="o1")
    assert order is not None, "the paper broker should hold the simulated entry"
    (rec,) = b.drain_dry_run_records()
    assert rec["outcome"] == "DRY_RUN_ORDER"
    ws = rec["would_send"]
    assert ws["order_type"] == "market_order" and ws["time_in_force"] == "ioc"
    assert ws["bracket_stop_loss_price"] == 75_000.0 - 400.0
    assert ws["bracket_stop_loss_limit_price"] == pytest.approx(75_000.0 - 400.0 - 1.5 * 400.0)
    assert rec["book"]["mark"] == 75_000.0 and rec["book"]["spread_r"] == 0.0
    assert rec["leverage"]["leverage"] >= 1
    # Planned against the configured equity, not the empty venue account.
    assert rec["leverage"]["available"] == 250.0
    assert rec["venue_usd_balance"] == 0.0


def test_a_book_the_live_bot_would_refuse_is_refused_and_recorded(tmp_path):
    venue = ReadOnlyVenue()
    venue.touch = 75_000.0 + 1.5 * 400.0          # 1.5R from the reference
    b = dry(venue, tmp_path)
    _, decision = approve("BTCUSD", 1)
    with pytest.raises(OpeningRefused) as e:
        b.submit_order(decision.intent, now=1, order_uid="o1")
    assert e.value.no_position_can_result, "the bot must release the slot"
    (rec,) = b.drain_dry_run_records()
    assert rec["outcome"] == "DRY_RUN_REFUSED" and "1.50R" in rec["reason"]
    assert not b.get_positions() and not b._paper.orders


def test_the_kill_switch_refuses_in_a_dry_run_too(tmp_path):
    b = dry(ReadOnlyVenue(), tmp_path, halt=True)
    _, decision = approve("BTCUSD", 1)
    with pytest.raises(OpeningRefused, match="kill switch"):
        b.submit_order(decision.intent, now=1, order_uid="o1")


def test_the_bot_reads_the_paper_execution_surface_through_the_wrapper(tmp_path):
    """The dry run registers the PAPER profile; the bot must read paper fields."""
    from app.config.settings import RiskConfig, Settings
    from app.config.strategy import FROZEN
    from tests.live.test_recovery import DeadFeed

    bot = DryRunBot(Settings(symbols=("BTCUSD",), risk=RiskConfig()),
                    InMemoryRepository(), COSTS, strategy=FROZEN, feed=DeadFeed())
    bot.broker = DryRunBroker(bot.broker, None, equity=250.0, kill_switch_path="/x")
    assert bot._execution_fields() == EXECUTION_FIELDS
    values = bot._execution_values()
    assert values["max_entry_deviation"] == bot.broker._paper.max_entry_deviation


@pytest.mark.asyncio
async def test_records_are_journaled_as_system_events(tmp_path):
    from app.config.settings import RiskConfig, Settings
    from app.config.strategy import FROZEN
    from tests.live.test_recovery import DeadFeed

    bot = DryRunBot(Settings(symbols=("BTCUSD",), risk=RiskConfig()),
                    InMemoryRepository(), COSTS, strategy=FROZEN, feed=DeadFeed())
    b = dry(ReadOnlyVenue(), tmp_path)
    bot.broker = b
    _, decision = approve("BTCUSD", 1)
    b.submit_order(decision.intent, now=1, order_uid="o1")
    await bot.drain_broker_events()
    events = [e for e in await bot.repo.recent_system_events()
              if e["component"] == "dry_run"]
    assert [e["event_type"] for e in events] == ["DRY_RUN_ORDER"]
    assert events[0]["payload"]["would_send"]["size"] == 100


def test_the_cli_registers_the_paper_profile_on_a_dry_run_host(monkeypatch):
    import live.__main__ as entry

    seen = {}
    monkeypatch.setenv("DELTA_ENV", "prod")
    monkeypatch.setenv("DELTABOT_DRY_RUN", "1")
    monkeypatch.setattr("app.cli.main", lambda argv: seen.setdefault(
        "profile", __import__("os").environ["DELTABOT_EXECUTION_PROFILE"]) and 0)
    entry.cli_entry(["forward-test", "status"])
    assert seen["profile"] == "paper"
    monkeypatch.setenv("DELTABOT_DRY_RUN", "0")
    seen.clear()
    entry.cli_entry(["forward-test", "status"])
    assert seen["profile"] == "live"
