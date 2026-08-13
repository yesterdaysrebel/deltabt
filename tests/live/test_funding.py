"""F4 -- funding settlement.

AUDIT FINDING. The `positions.funding` column existed, `deltabt.costs` modelled
snapshot funding and `FUNDING:<SYMBOL>` history was available, but nothing in
the live loop ever charged it. Any position held across a settlement understated
its cost.

THE MODEL IS THE FROZEN RESEARCH MODEL, not a new one:

* snapshot, not pro-rata -- whatever is open at the instant pays the full
  interval;
* the grid is anchored to the UTC epoch, so 8h symbols settle at 00:00/08:00/
  16:00 UTC;
* the interval is per symbol;
* the rate is a PERCENT per interval;
* positive means PAID by this position.

The units were verified against the live feed before any of this was written:
`v2/ticker.funding_rate` and the research `FUNDING:` series agree in scale, and
BTCUSD sitting at exactly 0.01 corroborates the +/-0.01% pin the research
measured. Reading it as a fraction would overstate every charge by 100x.
"""

from __future__ import annotations

import pytest

from app.market_data.normalize import Tick
from app.portfolio.funding import (
    FundingSettlement,
    funding_amount,
    funding_event_id,
    settlement_grid,
    settlements_for_position,
)
from tests.live.conftest import requires_pg
from tests.live.test_fill_association import _close_at, _open
from tests.live.test_recovery import make_bot

pytestmark = pytest.mark.asyncio
US = 1_000_000
H8 = 28_800
#: 2020-09-13 12:26:40 UTC. The next 8h settlement is 16:00:00 UTC.
MKT = 1_600_000_000
S16 = 1_600_012_800          # 2020-09-13 16:00:00 UTC


class TestGridAnchoring:
    async def test_the_grid_is_anchored_to_the_utc_epoch(self):
        """Not to when the position opened -- 8h symbols settle 00/08/16 UTC."""
        import datetime as dt
        for ts in settlement_grid(MKT, MKT + 3 * H8, H8):
            t = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
            assert (t.hour, t.minute, t.second) in {(0, 0, 0), (8, 0, 0), (16, 0, 0)}

    async def test_no_settlement_crossed(self):
        """INVARIANT: a position that spans no settlement is charged nothing."""
        assert settlement_grid(MKT, MKT + 600, H8) == []

    async def test_one_settlement_crossed(self):
        assert settlement_grid(MKT, S16 + 60, H8) == [S16]

    async def test_multiple_settlements_crossed(self):
        got = settlement_grid(MKT, S16 + 2 * H8, H8)
        assert got == [S16, S16 + H8, S16 + 2 * H8]

    async def test_the_window_is_half_open_at_the_start(self):
        """So repeated calls with a moving window cannot charge twice."""
        assert settlement_grid(S16, S16 + 60, H8) == []
        assert settlement_grid(S16 - 1, S16, H8) == [S16]

    async def test_a_position_open_exactly_at_the_instant_is_charged(self):
        assert S16 in settlement_grid(S16 - H8, S16, H8)

    async def test_a_four_hour_symbol_settles_twice_as_often(self):
        """The interval is per symbol; assuming 8h globally is wrong for most
        of the venue."""
        assert len(settlement_grid(MKT, MKT + H8, 14_400)) == 2
        assert len(settlement_grid(MKT, MKT + H8, H8)) == 1


class TestAmount:
    async def test_a_long_pays_when_funding_is_positive(self):
        amt = funding_amount(side=1, quantity=100, contract_value=0.001,
                             mark_price=63_000.0, rate_percent=0.01)
        assert amt > 0
        assert amt == pytest.approx(100 * 0.001 * 63_000.0 * 0.0001)

    async def test_a_short_receives_when_funding_is_positive(self):
        amt = funding_amount(side=-1, quantity=100, contract_value=0.001,
                             mark_price=63_000.0, rate_percent=0.01)
        assert amt < 0

    async def test_a_long_receives_when_funding_is_negative(self):
        assert funding_amount(1, 100, 0.001, 63_000.0, -0.01) < 0

    async def test_a_short_pays_when_funding_is_negative(self):
        assert funding_amount(-1, 100, 0.001, 63_000.0, -0.01) > 0

    async def test_the_rate_is_a_percent_not_a_fraction(self):
        """Reading it as a fraction would overstate every charge by 100x."""
        amt = funding_amount(1, 100, 0.001, 63_000.0, 1.0)   # 1 PERCENT
        assert amt == pytest.approx(63.0), "1% of $6300 notional"

    async def test_it_matches_the_research_cost_model_exactly(self):
        from deltabt.costs import funding_charge
        mine = funding_amount(1, 100, 0.001, 63_000.0, 0.0137)
        theirs = funding_charge(100, 1, 63_000.0, 0.0137, 0.001)
        assert mine == pytest.approx(theirs)

    async def test_the_event_id_is_deterministic(self):
        assert funding_event_id("pos1", S16) == funding_event_id("pos1", S16)
        assert funding_event_id("pos1", S16) != funding_event_id("pos1", S16 + H8)
        assert funding_event_id("pos1", S16) != funding_event_id("pos2", S16)


class TestSettlementsForPosition:
    def _call(self, **over):
        kw = dict(position_uid="pos1", symbol="BTCUSD", side=1, quantity=100,
                  contract_value=0.001, opened_at=MKT, checked_through=0,
                  now=S16 + 60, interval=H8, rate_percent=0.01,
                  mark_price=63_000.0)
        kw.update(over)
        return settlements_for_position(**kw)

    async def test_one_settlement_produces_one_charge(self):
        out = self._call()
        assert len(out) == 1 and out[0].exchange_ts == S16
        assert out[0].paid is True

    async def test_none_before_the_first_settlement(self):
        assert self._call(now=MKT + 600) == []

    async def test_the_watermark_prevents_recharging(self):
        assert self._call(checked_through=S16) == []

    async def test_multiple_settlements_each_get_their_own_event(self):
        out = self._call(now=S16 + 2 * H8)
        assert [s.exchange_ts for s in out] == [S16, S16 + H8, S16 + 2 * H8]
        assert len({s.event_id for s in out}) == 3

    async def test_a_position_opened_after_a_settlement_misses_it(self):
        """Snapshot semantics: it was not open at the instant."""
        assert self._call(opened_at=S16 + 60, now=S16 + 120) == []

    async def test_every_required_field_is_recorded(self):
        s = self._call()[0]
        for f in ("symbol", "position_uid", "exchange_ts", "funding_rate",
                  "notional", "funding_amount", "side", "quantity",
                  "mark_price", "interval_seconds", "rate_source"):
            assert getattr(s, f) is not None, f

    async def test_the_rate_source_is_recorded_as_an_approximation(self):
        """Delta publishes no 'funding applied' event, so the stored row must
        say where the rate came from."""
        assert self._call()[0].rate_source == "ticker"


# =====================================================================
# THROUGH THE LIVE PATH
# =====================================================================


async def _open_at(bot, ts):
    await _open(bot, ts=ts, entry=63_000.0, stop=62_500.0, target=64_000.0)


def _tick(symbol, ts, px=63_000.0, rate=0.01):
    return Tick(symbol, ts * US, px, px, funding_rate=rate)


class TestLivePath:
    async def test_a_position_spanning_no_settlement_pays_nothing(self):
        bot = make_bot({})
        await bot.start()
        await _open_at(bot, MKT)
        bot._on_tick(_tick("BTCUSD", MKT + 600))
        await bot.drain_broker_events()
        assert bot.broker.get_positions()[0].funding == 0.0
        assert bot.metrics.funding_events == 0

    async def test_crossing_a_settlement_charges_once(self):
        bot = make_bot({})
        await bot.start()
        await _open_at(bot, MKT)
        bot._on_tick(_tick("BTCUSD", S16 + 60))
        await bot.drain_broker_events()
        pos = bot.broker.get_positions()[0]
        assert pos.funding > 0, "a long pays positive funding"
        assert bot.metrics.funding_events == 1
        rows = await bot.repo.funding_for_position(pos.position_uid)
        assert len(rows) == 1 and rows[0].exchange_ts == S16

    async def test_a_short_receives_it(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot, ts=MKT, direction=-1, entry=63_000.0,
                    stop=63_500.0, target=62_000.0)
        bot._on_tick(_tick("BTCUSD", S16 + 60))
        await bot.drain_broker_events()
        assert bot.broker.get_positions()[0].funding < 0

    async def test_multiple_settlements_accumulate(self):
        bot = make_bot({})
        await bot.start()
        await _open_at(bot, MKT)
        for ts in (S16 + 60, S16 + H8 + 60, S16 + 2 * H8 + 60):
            bot._on_tick(_tick("BTCUSD", ts))
            await bot.drain_broker_events()
        pos = bot.broker.get_positions()[0]
        assert bot.metrics.funding_events == 3
        rows = await bot.repo.funding_for_position(pos.position_uid)
        assert len(rows) == 3
        assert pos.funding == pytest.approx(sum(r.funding_amount for r in rows))

    async def test_repeated_ticks_do_not_recharge(self):
        bot = make_bot({})
        await bot.start()
        await _open_at(bot, MKT)
        for _ in range(5):
            bot._on_tick(_tick("BTCUSD", S16 + 60))
            await bot.drain_broker_events()
        assert bot.metrics.funding_events == 1

    async def test_funding_reaches_realised_pnl(self):
        """The whole point: net P&L must include it."""
        bot = make_bot({})
        await bot.start()
        await _open_at(bot, MKT)
        bot._on_tick(_tick("BTCUSD", S16 + 60))
        await bot.drain_broker_events()
        pos = bot.broker.get_positions()[0]
        charged = pos.funding
        assert charged > 0
        await _close_at(bot, S16 + 600, 64_500.0)

        closed = list(bot.broker.positions.values())[0]
        gross = (closed.side * (closed.exit_price - closed.entry_price)
                 * closed.quantity * bot.costs["BTCUSD"].contract_value)
        expected = gross - closed.entry_fee - closed.exit_fee - charged
        assert closed.realized_pnl == pytest.approx(expected)
        assert closed.funding == pytest.approx(charged)

    async def test_equity_tracks_the_same_arithmetic(self):
        bot = make_bot({})
        await bot.start()
        start_equity = bot.broker.equity
        await _open_at(bot, MKT)
        bot._on_tick(_tick("BTCUSD", S16 + 60))
        await bot.drain_broker_events()
        await _close_at(bot, S16 + 600, 64_500.0)
        closed = list(bot.broker.positions.values())[0]
        assert bot.broker.equity == pytest.approx(
            start_equity + closed.realized_pnl)

    async def test_nothing_is_charged_without_a_rate(self):
        """No invented numbers: no observed rate means no charge."""
        bot = make_bot({})
        await bot.start()
        await _open_at(bot, MKT)
        bot._on_tick(Tick("BTCUSD", (S16 + 60) * US, 63_000.0, 63_000.0))
        await bot.drain_broker_events()
        assert bot.broker.get_positions()[0].funding == 0.0

    async def test_a_closed_position_stops_being_charged(self):
        bot = make_bot({})
        await bot.start()
        await _open_at(bot, MKT)
        await _close_at(bot, MKT + 600, 64_500.0)
        bot._on_tick(_tick("BTCUSD", S16 + 60))
        await bot.drain_broker_events()
        assert bot.metrics.funding_events == 0


class TestRestartAcrossSettlement:
    async def test_a_restart_does_not_double_charge(self):
        """INVARIANT: restart immediately after settlement."""
        store: dict = {}
        a = make_bot(store)
        await a.start()
        await _open_at(a, MKT)
        a._on_tick(_tick("BTCUSD", S16 + 60))
        await a.drain_broker_events()
        pos_uid = a.broker.get_positions()[0].position_uid
        charged = a.broker.get_positions()[0].funding

        b = make_bot(store)                     # kill -9
        await b.start()
        recovered = b.broker.get_positions()[0]
        assert recovered.funding == pytest.approx(charged)
        assert recovered.funding_checked_through == S16

        # ticking past the same settlement again must add nothing
        b._on_tick(_tick("BTCUSD", S16 + 120))
        await b.drain_broker_events()
        assert len(await b.repo.funding_for_position(pos_uid)) == 1
        assert b.broker.get_positions()[0].funding == pytest.approx(charged)

    async def test_a_restart_just_before_settlement_still_charges(self):
        store: dict = {}
        a = make_bot(store)
        await a.start()
        await _open_at(a, MKT)
        a._on_tick(_tick("BTCUSD", S16 - 60))
        await a.drain_broker_events()
        assert a.metrics.funding_events == 0

        b = make_bot(store)
        await b.start()
        b._on_tick(_tick("BTCUSD", S16 + 60))
        await b.drain_broker_events()
        assert b.metrics.funding_events == 1

    async def test_replaying_a_settlement_leaves_equity_untouched(self):
        """Equity and the ledger must not drift apart.

        The broker refuses to apply a settlement twice, so persistence never
        has to undo anything -- an undo would be wrong whenever the replayed
        event is a stale one the broker did not just apply.
        """
        bot = make_bot({})
        await bot.start()
        await _open_at(bot, MKT)
        bot._on_tick(_tick("BTCUSD", S16 + 60))
        await bot.drain_broker_events()
        pos = bot.broker.get_positions()[0]
        after_first = (pos.funding, bot.broker.equity)

        ev = [e for e in bot.broker.events if e.kind == "FUNDING"][0]
        await bot._persist_funding(ev)          # replay
        assert (pos.funding, bot.broker.equity) == pytest.approx(after_first)


@requires_pg
@pytest.mark.postgres
class TestFundingInPostgres:
    async def test_the_ledger_row_is_complete_and_idempotent(self, pg_repo):
        from app.persistence.models import FundingEventRecord, InstanceRecord
        await pg_repo.register_instance(InstanceRecord(
            instance_uid="i1", hostname="t", pid=1, strategy_version="v",
            strategy_config={}, risk_config={}, symbols=["BTCUSD"]))
        rec = FundingEventRecord(
            event_id=funding_event_id("pos1", S16), instance_uid="i1",
            position_uid="pos1", symbol="BTCUSD", side=1, quantity=100,
            exchange_ts=S16, funding_rate=0.0137, mark_price=63_000.0,
            notional=6300.0, funding_amount=0.86, interval_seconds=H8,
            rate_source="ticker", received_ts=1.0)
        assert await pg_repo.record_funding(rec) is True
        assert await pg_repo.record_funding(rec) is False, "idempotent"

        rows = await pg_repo.funding_for_position("pos1")
        assert len(rows) == 1
        r = rows[0]
        assert r["funding_rate"] == pytest.approx(0.0137)
        assert r["rate_source"] == "ticker"
        assert r["interval_seconds"] == H8
        assert int(r["exchange_ts"].timestamp()) == S16
        assert await pg_repo.total_funding() == pytest.approx(0.86)

    async def test_the_unique_index_blocks_a_second_charge_per_instant(self, pg_repo):
        from app.persistence.models import FundingEventRecord, InstanceRecord
        await pg_repo.register_instance(InstanceRecord(
            instance_uid="i1", hostname="t", pid=1, strategy_version="v",
            strategy_config={}, risk_config={}, symbols=["BTCUSD"]))
        base = dict(instance_uid="i1", position_uid="pos1", symbol="BTCUSD",
                    side=1, quantity=100, exchange_ts=S16, funding_rate=0.01,
                    mark_price=63_000.0, notional=6300.0, funding_amount=0.63,
                    interval_seconds=H8)
        assert await pg_repo.record_funding(
            FundingEventRecord(event_id="a", **base)) is True
        # A different event_id for the same (position, instant) must still be
        # refused, and reported the same way the in-memory twin reports it.
        assert await pg_repo.record_funding(
            FundingEventRecord(event_id="b", **base)) is False
        assert len(await pg_repo.funding_for_position("pos1")) == 1
