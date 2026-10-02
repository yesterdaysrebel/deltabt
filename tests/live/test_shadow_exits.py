"""Shadow exits: every real position followed under baseline, ladder and trail.

The real position here is a baseline paper position (hold to 3R), as on the
pilot's dry-run host. The recorder must (a) agree with the real position for
the baseline rule, (b) reproduce the ladder and trail stops of the paper arms,
and (c) close any shadow still open when the real position closes otherwise.
"""
from __future__ import annotations

import pytest

from app.execution.paper_broker import PaperBroker
from app.execution.shadow_exits import PILOT_RULES, ShadowExits
from tests.live.test_paper_execution import BAR_CLOSE, COSTS, intent, tick


def setup():
    b = PaperBroker(COSTS, starting_equity=10_000.0, slippage_bps=2.0)
    sh = ShadowExits(COSTS, PILOT_RULES, slippage_bps=2.0)
    b.submit_order(intent(entry=63_000.0, stop=62_500.0, target=64_500.0))
    t0 = tick(63_000.0, ts=BAR_CLOSE)
    b.process_market_event(t0)
    rows = sh.observe(t0, b.get_positions())
    assert rows == []
    pos = b.get_positions()[0]
    return b, sh, pos


def drive(b, sh, prices, start=BAR_CLOSE + 60, mark=None):
    rows = []
    for k, px in enumerate(prices):
        t = tick(px, mark=mark, ts=start + 60 * k)
        b.process_market_event(t)
        rows += sh.observe(t, b.get_positions())
    return {r.rule: r for r in rows}


def test_each_rule_exits_where_its_paper_arm_would():
    b, sh, pos = setup()
    e, r = pos.entry_price, pos.risk_per_unit
    # up to +1.2R, then all the way down through the original stop
    rows = drive(b, sh, [e + 0.6 * r, e + 1.2 * r, e + 0.8 * r, e + 0.6 * r,
                         e + 0.2 * r, e - 1.1 * r])
    assert set(rows) == {"baseline", "ladder", "trail"}
    assert rows["trail"].exit_reason == "STOP_LOSS"
    assert rows["trail"].final_stop == pytest.approx(e + 0.7 * r, abs=1.0)
    assert rows["ladder"].final_stop == pytest.approx(e + 0.5 * r, abs=1.0)
    assert rows["baseline"].final_stop == pytest.approx(pos.entry_price - r, abs=1.0)
    # trail exits on the +0.6R tick (mark <= +0.7R), ladder on the +0.2R tick;
    # each fills at last-traded less 2 bps of slippage, ~0.025R here.
    assert rows["trail"].gross_r == pytest.approx(0.6 - 0.025, abs=0.005)
    assert rows["ladder"].gross_r == pytest.approx(0.2 - 0.025, abs=0.005)
    assert rows["baseline"].gross_r < -1.0
    assert all(x.observed_from_entry for x in rows.values())


def test_the_baseline_shadow_agrees_with_the_real_position():
    """Same simulation, same rule: the self-check the pilot reads first."""
    b, sh, pos = setup()
    e, r = pos.entry_price, pos.risk_per_unit
    rows = drive(b, sh, [e + 0.3 * r, e - 0.5 * r, e - 1.05 * r])
    assert not pos.is_open and pos.exit_reason == "STOP_LOSS"
    assert rows["baseline"].exit_reason == "STOP_LOSS", "judged by its own rule"
    assert rows["baseline"].exit_price == pytest.approx(pos.exit_price, rel=1e-9)
    assert rows["baseline"].closed_at == pos.closed_at


def test_a_runner_reaches_the_target_under_every_rule():
    b, sh, pos = setup()
    e, r = pos.entry_price, pos.risk_per_unit
    rows = drive(b, sh, [e + 0.8 * r, e + 1.6 * r, e + 2.4 * r, e + 3.05 * r])
    assert {x.exit_reason for x in rows.values()} == {"TAKE_PROFIT"}
    assert rows["baseline"].net_r < rows["baseline"].gross_r, "fees not charged"


def test_the_trail_records_what_the_bracket_edit_would_send():
    b, sh, pos = setup()
    e, r = pos.entry_price, pos.risk_per_unit
    rows = drive(b, sh, [e + 0.3 * r, e + 0.6 * r, e + 1.4 * r, e - 1.2 * r])
    trail, ladder, base = rows["trail"], rows["ladder"], rows["baseline"]
    assert trail.armed_at == BAR_CLOSE + 60 * 2          # the +0.6R tick
    assert trail.trail_amount == pytest.approx(0.5 * r, abs=0.5)
    assert trail.promotions == 2
    assert ladder.trail_amount is None and ladder.promotions == 2
    assert base.armed_at is None and base.promotions == 0


def test_shadows_still_open_close_at_the_real_exit():
    """A time stop or a flatten closes the real position by another route."""
    b, sh, pos = setup()
    e, r = pos.entry_price, pos.risk_per_unit
    drive(b, sh, [e + 0.2 * r])
    pos.status, pos.exit_price, pos.exit_reason = "CLOSED", e + 0.1 * r, "TIME_EXIT"
    pos.closed_at = BAR_CLOSE + 999
    t = tick(e + 0.1 * r, ts=BAR_CLOSE + 1000)
    rows = {x.rule: x for x in sh.observe(t, [])}
    assert set(rows) == {"baseline", "ladder", "trail"}
    assert all(x.exit_reason == "REAL_EXIT:TIME_EXIT" for x in rows.values())
    assert rows["trail"].gross_r == pytest.approx(0.1, abs=0.001)


def test_nothing_triggers_on_the_entry_tick_itself():
    b, sh, pos = setup()
    t = tick(pos.entry_price - 2 * pos.risk_per_unit, us=pos.armed_after_us)
    assert sh.observe(t, b.get_positions()) == []


@pytest.mark.asyncio
async def test_rows_reach_the_journal_once():
    from app.config.settings import RiskConfig, Settings
    from app.config.strategy import FROZEN
    from app.persistence.repository import InMemoryRepository
    from app.runtime.bot import TradingBot
    from tests.live.test_recovery import COSTS as BOT_COSTS, DeadFeed

    bot = TradingBot(Settings(symbols=("BTCUSD",), risk=RiskConfig()),
                     InMemoryRepository(), BOT_COSTS, strategy=FROZEN,
                     feed=DeadFeed(), shadow_exits=ShadowExits(BOT_COSTS))
    b, sh, pos = setup()
    e, r = pos.entry_price, pos.risk_per_unit
    produced = list(drive(b, sh, [e + 0.6 * r, e - 1.2 * r]).values())
    bot._pending_shadow = produced + produced          # a duplicate drain
    await bot.drain_shadow_exits()
    rows = await bot.repo.recent_shadow_exits()
    assert len(rows) == 3, "idempotent on (position, rule)"
    assert {x["rule"] for x in rows} == {"baseline", "ladder", "trail"}
    assert all(x["instance_uid"] == bot.instance_uid for x in rows)
