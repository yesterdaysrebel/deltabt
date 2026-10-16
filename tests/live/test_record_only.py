"""Record-only coins (owner, 2026-10-16): watched and recorded, NEVER traded.

The bot trades today's top-10 gainers and also watches gainers ranked 11-30
plus a fixed watchlist, recording their signals only. Safety comes first: a
record-only coin must never produce an order, a simulated position, a
reservation, or touch trades_today / daily loss / consecutive losses / the
per-coin pause. Each layer that could open a position refuses it on its own:
the risk engine (first, by name), TradingBot._place (before the reservation),
the paper broker, the dry-run broker and the live broker.
"""
from __future__ import annotations

import asyncio
import copy
import math
from dataclasses import replace

import numpy as np
import pytest

from app.config.settings import RiskConfig, Settings
from app.execution.intents import ApprovedOrderIntent
from app.execution.paper_broker import NotTradeable, PaperBroker
from app.learning.recorder import SignalRecorder, skip_reason
from app.market_data.normalize import Candle, CandleUpdate, Tick
from app.persistence.repository import InMemoryRepository
from app.risk.engine import RECORD_ONLY, RiskEngine, RiskState
from app.runtime.bot import TradingBot
from app.strategy.explanation import Explanation, Outcome
from live import universe
from tests import signal_lab_fixture as fx
from tests.live.test_recovery import DeadFeed
from tests.live.test_signal_recorder import COSTS, STRATEGY, FakeDelta, _a_signal

pytestmark = pytest.mark.asyncio

SYM = fx.SYMBOL                 # the trade universe
ROA = "ROAUSD"                  # gainer ranked 15: record-only
ROB = "ROBUSD"                  # watchlist coin outside the ranks: record-only
US = 1_000_000
WARM = 3 * 1440
DRIVE = 2 * 1440
MARKETS = {SYM: fx.coin(7, 1.25)["ltp"], ROA: fx.coin(23, 0.42)["ltp"],
           ROB: fx.coin(31, 3.10)["ltp"]}
ALL_COSTS = {**COSTS, ROA: replace(COSTS[SYM], symbol=ROA), ROB: replace(COSTS[SYM], symbol=ROB)}
PICKS = {SYM: {"rank": 3, "change": 12.5, "turnover": 2e6, "record_only": False},
         ROA: {"rank": 15, "change": 6.0, "turnover": 9e5, "record_only": True},
         ROB: {"rank": None, "change": -2.0, "turnover": 5e6, "record_only": True}}


def _candles(sym, lo=0, hi=None):
    df = MARKETS[sym].iloc[lo:hi]
    return [Candle(sym, int(r.time), float(r.open), float(r.high), float(r.low),
                   float(r.close), float(r.volume), source="rest") for r in df.itertuples()]


class Backfill:
    def __init__(self):
        self.asked = []

    async def warm_up(self, symbol, days, now=None):
        self.asked.append(symbol)
        return _candles(symbol, 0, WARM)

    async def fetch(self, *a, **k):
        return []

    async def fill_gap(self, *a, **k):
        return []


def _bot(store, record_only=(), recorder=None, backfill=None, record_backfill=None):
    settings = Settings(symbols=(SYM,), risk=RiskConfig(
        starting_equity=10_000.0, max_open_positions=1, max_trades_per_day=2,
        pause_symbol_after_loss_seconds=14400))
    costs = {s: c for s, c in ALL_COSTS.items() if s == SYM or s in record_only}
    return TradingBot(settings, InMemoryRepository(store), costs, strategy=STRATEGY,
                      backfiller=backfill or Backfill(), feed=DeadFeed(),
                      signal_recorder=recorder, record_only=record_only,
                      record_backfiller=record_backfill)


async def _drive(bot, symbols):
    """Minute by minute, through the bot's own paths: candles via _on_candle,
    the bar loop's ordering via _process_pending_bars, then ticks along the
    forming bar (open, high, low, close) and the broker's events."""
    assert await bot.start(), bot.recovery_error
    await bot.warm_record_only()
    frames = {s: MARKETS[s].iloc[WARM:WARM + DRIVE] for s in symbols}
    for i in range(DRIVE):
        for s in symbols:
            r = frames[s].iloc[i]
            bot._on_candle(CandleUpdate(s, int(r.time), float(r.open), float(r.high),
                                        float(r.low), float(r.close), float(r.volume),
                                        (int(r.time) + 59) * US))
        await bot._process_pending_bars()
        for s in symbols:
            r = frames[s].iloc[i]
            for k, px in enumerate((r.open, r.high, r.low, r.close), 1):
                bot._on_tick(Tick(s, (int(r.time) + k) * US, float(px), float(px)))
        await bot.drain_broker_events()
    if bot.signal_recorder is not None:
        await asyncio.wait(list(bot.signal_recorder._tasks) or [asyncio.sleep(0)], timeout=120)


def _decisions(store, symbol=SYM):
    return [(k, r.outcome, r.direction, r.rejection_reason, r.entry_price, r.stop_price)
            for k, r in sorted(store["signals"].items(), key=lambda kv: (kv[1].bar_open, kv[0]))
            if r.symbol == symbol]


def _orders(store, symbol=SYM):
    return sorted((o.signal_key, o.purpose, o.quantity, o.status, o.filled_price)
                  for o in store["orders"].values() if o.symbol == symbol)


def _positions(store, symbol=SYM):
    return sorted((p.signal_key, p.side, p.quantity, p.entry_price, p.exit_price,
                   p.exit_reason, p.r_multiple)
                  for p in store["positions"].values() if p.symbol == symbol)


# --- the headline: watching record-only coins changes NOTHING about trading ---

async def test_record_only_coins_change_no_trade_decision_and_never_trade():
    plain, wide = {}, {}
    rec_a = SignalRecorder(STRATEGY, client=FakeDelta(), picks={SYM: PICKS[SYM]},
                           contract_values={SYM: 1.0})
    rec_b = SignalRecorder(STRATEGY, client=FakeDelta(), picks=PICKS,
                           contract_values={s: 1.0 for s in PICKS}, record_only=(ROA, ROB))
    bot_a = _bot(plain, recorder=rec_a)
    bot_b = _bot(wide, record_only=(ROA, ROB), recorder=rec_b)
    await _drive(bot_a, [SYM])
    await _drive(bot_b, [SYM, ROA, ROB])

    # the trade universe: every decision, order, position and risk counter identical
    assert _decisions(plain), "the synthetic market produced no signals on the trade coin"
    assert _decisions(wide) == _decisions(plain)
    assert _orders(wide) == _orders(plain)
    assert _positions(wide) == _positions(plain)
    assert _positions(plain), "no position opened: the test would prove nothing about trading"
    assert bot_b.state.to_dict() == bot_a.state.to_dict()
    assert bot_b.clock.now() == bot_a.clock.now()
    assert [asdict_risk(r) for r in wide["risk_events"]] == [asdict_risk(r) for r in plain["risk_events"]]
    for name in ("signals_detected", "signals_rejected", "orders", "fills", "closed_positions",
                 "candles_1m", "reservations_refused"):
        assert getattr(bot_b.metrics, name) == getattr(bot_a.metrics, name), name

    # the record-only coins: many signals, every one refused by name, nothing opened
    for ro in (ROA, ROB):
        sigs = [r for r in wide["signals"].values() if r.symbol == ro and r.direction is not None]
        assert len(sigs) >= 5, f"{ro} fired too few signals to prove anything"
        assert {r.outcome for r in sigs} == {"REJECTED"}
        assert all(r.rejection_reason.startswith("record_only:") for r in sigs)
        assert not _orders(wide, ro) and not _positions(wide, ro)
        # no-setup bars of a record-only coin are not stored (never labelled)
        assert all(r.direction is not None for r in wide["signals"].values() if r.symbol == ro)
        assert ro not in bot_b.state.last_loss_at_by_symbol
    assert bot_b.metrics.record_only_signals >= 10
    assert not any(o.symbol in (ROA, ROB) for o in wide["orders"].values())
    assert not bot_b.broker.get_positions(ROA) and not bot_b.broker.get_positions(ROB)

    # ...and recorded with their readings
    feats = wide["signal_features"]
    for ro, rank in ((ROA, 15), (ROB, None)):
        keys = [k for k, r in wide["signals"].items() if r.symbol == ro and r.direction is not None]
        for k in keys:
            f = feats[k]
            assert f["record_only"] is True and f["skip"] == "record_only" and f["rank"] == rank
            assert f["final_outcome"] == "REJECTED" and f["limit_name"] == RECORD_ONLY
            assert f["errors"] == [] and math.isfinite(f["lab"]["wpr"])
            assert "trades_5m" in f["flow"]
            assert f["live"] and f["pre_24h"]
    for k, r in wide["signals"].items():
        if r.symbol == SYM and r.direction is not None:
            assert feats[k]["record_only"] is False and feats[k]["skip"] != "record_only"
            assert feats[k]["rank"] == 3


async def test_negative_control_the_same_coin_made_tradeable_does_change_trading():
    """Proves the comparison above can fail: with ROA TRADEABLE it takes the
    single slot sometimes, and the trade coin's decisions and positions move."""
    plain, traded = {}, {}
    await _drive(_bot(plain), [SYM])
    settings = Settings(symbols=(SYM, ROA), risk=RiskConfig(
        starting_equity=10_000.0, max_open_positions=1, max_trades_per_day=2,
        pause_symbol_after_loss_seconds=14400))
    bot = TradingBot(settings, InMemoryRepository(traded), {s: ALL_COSTS[s] for s in (SYM, ROA)},
                     strategy=STRATEGY, backfiller=Backfill(), feed=DeadFeed())
    await _drive(bot, [SYM, ROA])
    assert _decisions(traded) != _decisions(plain)
    assert _positions(traded) != _positions(plain)


def asdict_risk(r):
    return (r.symbol, r.event_type, r.reason, r.limit_name, r.exchange_ts)


async def test_record_only_coins_warm_after_ready_and_trading_never_waits():
    trade_bf, record_bf = Backfill(), Backfill()
    bot = _bot({}, record_only=(ROA, ROB), backfill=trade_bf, record_backfill=record_bf)
    assert await bot.start()
    # READY on the trade universe alone; nothing of the record-only coins asked yet
    assert bot.ready and trade_bf.asked == [SYM] and record_bf.asked == []
    assert bot._cold == {ROA, ROB}
    # a cold coin's candles are ignored: it cannot evaluate on a short history
    r = MARKETS[ROA].iloc[WARM]
    bot._on_candle(CandleUpdate(ROA, int(r.time), r.open, r.high, r.low, r.close, r.volume,
                                (int(r.time) + 59) * US))
    assert len(bot.builder[ROA].bars) == 0 and bot.builder[ROA].stats.updates == 0
    await bot.warm_record_only()
    assert sorted(record_bf.asked) == [ROA, ROB] and trade_bf.asked == [SYM]
    assert not bot._cold and len(bot.builder[ROA].bars) == WARM
    h = bot.health_snapshot()
    assert h["record_only_cold"] == [] and h["record_only_gaps"] == 0


async def test_a_failing_or_short_record_only_warm_up_never_raises_and_stays_cold():
    class Bad(Backfill):
        async def warm_up(self, symbol, days, now=None):
            if symbol == ROA:
                raise RuntimeError("venue down")
            return _candles(symbol, 0, 30)               # far too short
    bot = _bot({}, record_only=(ROA, ROB), record_backfill=Bad())
    assert await bot.start()
    await bot.warm_record_only()                         # does not raise
    assert bot._cold == {ROA, ROB} and bot.ready and not bot.recovery_error


async def test_record_only_gaps_do_not_hold_health_red():
    bot = _bot({}, record_only=(ROA,))
    assert await bot.start()
    await bot.warm_record_only()
    b = bot.builder[ROA]
    last = b.bars[-1].start
    for t in (last + 60, last + 300):                    # a 3-minute hole
        bot._on_candle(CandleUpdate(ROA, t, 1.0, 1.0, 1.0, 1.0, 5.0, (t + 59) * US))
    now = last + 400
    import app.runtime.bot as botmod
    real = botmod.wall_now
    botmod.wall_now = lambda: now
    try:
        h = bot.health_snapshot()
    finally:
        botmod.wall_now = real
    assert h["record_only_gaps"] >= 1 and h["recent_gaps"] == 0


async def test_record_only_bars_never_move_the_clock_or_reach_the_broker():
    bot = _bot({}, record_only=(ROA,))
    assert await bot.start()
    await bot.warm_record_only()
    before = bot.clock.now()
    called = []
    bot.broker.process_market_event = lambda tick: called.append(tick) or []
    bot._on_tick(Tick(ROA, (before + 10_000) * US, 0.5, 0.5))
    assert bot.clock.now() == before and not called


async def test_the_bar_loop_handles_trade_coins_first():
    bot = _bot({}, record_only=(ROA,))
    seen = []

    async def note(bar):
        seen.append((bar.symbol, bar.start))
    bot.on_closed_1m = note
    batch = [Candle(ROA, 60, 1, 1, 1, 1, 1), Candle(SYM, 120, 1, 1, 1, 1, 1),
             Candle(ROA, 0, 1, 1, 1, 1, 1), Candle(SYM, 60, 1, 1, 1, 1, 1)]
    bot._pending_bars = list(batch)
    await bot._process_pending_bars()
    assert seen == [(SYM, 60), (SYM, 120), (ROA, 0), (ROA, 60)]
    # the loop's two phases: trade coins alone, the record-only coins left queued
    seen.clear()
    bot._pending_bars = list(batch)
    await bot._process_pending_bars(record_only=False)
    assert seen == [(SYM, 60), (SYM, 120)] and {b.symbol for b in bot._pending_bars} == {ROA}
    await bot._process_pending_bars(record_only=True)
    assert seen[2:] == [(ROA, 0), (ROA, 60)] and bot._pending_bars == []


async def test_the_bar_loop_repairs_trade_coins_and_hands_out_their_bars_first(monkeypatch):
    """One pass of the real _bar_loop: a record-only coin's gap repair (REST)
    happens only after every trade-coin bar has been handled."""
    bot = _bot({}, record_only=(ROA,))
    order = []

    async def repair(symbols=None):
        order.append(("repair", tuple(symbols)))
        return []

    async def closed(bar):
        order.append(("bar", bar.symbol))

    async def drain():
        bot._stopping.set()
    bot._repair_new_gaps = repair
    bot.on_closed_1m = closed
    bot.drain_broker_events = drain
    bot._pending_bars = [Candle(ROA, 0, 1, 1, 1, 1, 1), Candle(SYM, 0, 1, 1, 1, 1, 1)]
    await asyncio.wait_for(bot._bar_loop(interval=0.01), timeout=5)
    assert order == [("repair", (SYM,)), ("bar", SYM), ("repair", (ROA,)), ("bar", ROA)]


# --- each layer refuses on its own ------------------------------------------------

def _exp(symbol, **kw):
    return Explanation(symbol=symbol, bar_open=1_792_000_000, primary_timeframe="5m",
                       confirmation_timeframe="1m", strategy_version="t",
                       strategy_config_hash="h", outcome=Outcome.DETECTED, direction=1,
                       entry_price=1.0, stop_price=0.95, target_price=1.15, **kw)


async def test_the_risk_engine_refuses_record_only_first_and_touches_no_state():
    cfg = RiskConfig(starting_equity=250.0, max_open_positions=1, max_trades_per_day=2)
    eng = RiskEngine(cfg, ALL_COSTS, allowed_symbols=(SYM,), record_only_symbols=(ROA,))
    state = RiskState.fresh(250.0)
    state.day = "2000-01-01"                 # a day roll WOULD change state
    state.halted_at = 0
    snap = copy.deepcopy(state.to_dict())
    exp = _exp(ROA)
    d = eng.evaluate(exp, state, open_positions=[], now=1_792_000_300)
    assert not d.approved and d.limit_name == RECORD_ONLY and d.checks_passed == []
    assert exp.outcome is Outcome.REJECTED and exp.rejection_reason.startswith("record_only:")
    assert state.to_dict() == snap, "the refusal must come before the day roll and every gate"
    assert skip_reason(exp.outcome.value, exp.rejection_reason, d.limit_name) == "record_only"
    # the trade universe is unchanged, and still refuses anything else by its own name
    assert eng.allowed_symbols == (SYM,)
    d2 = eng.evaluate(_exp("OTHERUSD"), RiskState.fresh(250.0), open_positions=[], now=1)
    assert d2.limit_name == "allowed_symbols"
    with pytest.raises(ValueError, match="both tradeable and record-only"):
        RiskEngine(cfg, ALL_COSTS, allowed_symbols=(SYM,), record_only_symbols=(SYM,))
    with pytest.raises(ValueError, match="both tradeable and record-only"):
        TradingBot(Settings(symbols=(SYM,)), InMemoryRepository({}), ALL_COSTS,
                   strategy=STRATEGY, feed=DeadFeed(), record_only=(SYM,))


def _intent(symbol):
    return ApprovedOrderIntent(
        intent_id=f"i-{symbol}", signal_key=f"k-{symbol}", risk_evaluation_id="r",
        symbol=symbol, side=1, order_type="market", quantity=10, limit_price=None,
        entry_reference=1.0, stop_price=0.95, target_price=1.15, risk_per_unit=0.05,
        risk_amount=0.5, notional=10.0, equity_before=250.0, estimated_fee=0.0,
        estimated_slippage=0.0, strategy_version="t", bar_open=1_792_000_000,
        checks_passed=("forged upstream approval",))


async def test_the_paper_broker_refuses_a_coin_outside_the_trade_universe():
    pb = PaperBroker(ALL_COSTS, starting_equity=250.0, tradeable_symbols=(SYM,))
    with pytest.raises(NotTradeable) as e:
        pb.submit_order(_intent(ROA), now=1)
    assert e.value.no_position_can_result
    assert not pb.orders and not pb.positions
    assert pb.submit_order(_intent(SYM), now=1) is not None


async def test_place_refuses_before_reserving_even_if_upstream_approved():
    store = {}
    bot = _bot(store, record_only=(ROA,))
    assert await bot.start()

    class Approved:
        approved = True
        intent = _intent(ROA)
    for sym in (ROA, "STRANGERUSD"):
        Approved.intent = _intent(sym)
        exp = _exp(sym)
        await bot._place(exp, Approved, 1_792_000_300)
        assert exp.outcome is Outcome.REJECTED and exp.rejection_reason.startswith("record_only:")
    assert not store["orders"] and await bot.repo.effective_exposure() == 0
    assert not bot.broker.orders
    assert any(e.event_type == "ENTRY_OUTSIDE_TRADE_UNIVERSE" for e in store["system_events"])


class _NoVenue:
    """A venue that fails the test on ANY call."""

    def __getattr__(self, name):
        raise AssertionError(f"the venue was contacted: {name}")


async def test_the_live_broker_refuses_before_contacting_the_venue():
    from live.broker import LiveBroker, OpeningRefused
    lb = LiveBroker(_NoVenue(), product_ids={SYM: 1, ROA: 2}, experiment_id="x",
                    tradeable_symbols=(SYM,))
    with pytest.raises(OpeningRefused, match="record_only") as e:
        lb.submit_order(_intent(ROA), now=1, order_uid="o1")
    assert e.value.no_position_can_result


async def test_the_live_bot_gives_its_broker_the_trade_universe_only():
    from live.runtime import LiveTradingBot
    bot = LiveTradingBot(Settings(symbols=(SYM,)), InMemoryRepository({}),
                         {s: ALL_COSTS[s] for s in (SYM, ROA)},
                         strategy=STRATEGY, feed=DeadFeed(), record_only=(ROA,),
                         client=_NoVenue(), product_ids={SYM: 1})
    assert bot.broker.tradeable_symbols == frozenset({SYM})
    assert bot.risk.allowed_symbols == (SYM,) and bot.risk.record_only_symbols == {ROA}
    assert set(bot.costs) == {SYM}


async def test_the_dry_run_broker_refuses_and_journals_it():
    from live.broker import OpeningRefused
    from live.config import Product
    from live.dry_run import build
    from decimal import Decimal
    products = {s: Product(s, i, Decimal("0.0001"), Decimal("1"))
                for i, s in enumerate((SYM, ROA), 1)}
    bot = build(Settings(symbols=(SYM,)), client=_NoVenue(), venue="prod", symbols=(SYM,),
                products=products, strategy=STRATEGY, costs=ALL_COSTS,
                repo=InMemoryRepository({}), lock=None, notifier=None, backfiller=Backfill(),
                kill_switch_path="/nonexistent/kill", record_only=(ROA,))
    assert bot.broker._live.product_ids == {SYM: 1}          # no record-only product id
    assert bot.broker._live.tradeable_symbols == frozenset({SYM})
    with pytest.raises(OpeningRefused, match="record_only"):
        bot.broker.submit_order(_intent(ROA), now=1, order_uid="o1")
    recs = bot.broker.drain_dry_run_records()
    assert [r["outcome"] for r in recs] == ["DRY_RUN_REFUSED"] and recs[0]["symbol"] == ROA
    assert not bot.broker._paper.orders
    # and the paper broker behind it refuses on its own
    with pytest.raises(NotTradeable):
        bot.broker._paper.submit_order(_intent(ROA), now=1)


# --- the recorder ---------------------------------------------------------------

async def test_a_watchlist_coin_gets_its_24h_change_and_turnover_but_no_rank():
    import pandas as pd
    row, bars, entry, stop = _a_signal()
    day = int(row.entry_time) // 86400 * 86400
    rec = SignalRecorder(STRATEGY, client=FakeDelta(), pick_day=day,
                         picks={SYM: {"rank": None, "change": -2.0, "turnover": 5e6,
                                      "record_only": True}},
                         contract_values={SYM: 1.0})
    out = rec.compute(symbol=SYM, bars=bars, bar_open=int(row.entry_time),
                      side=int(row.side), entry=entry, stop=stop)
    assert out["lab"]["day_gain_pct"] == -2.0
    assert out["lab"]["log_turnover"] == pytest.approx(math.log10(5e6))
    assert math.isnan(out["lab"]["day_rank"])
    repo = InMemoryRepository({})
    got = await rec.record(repo, key="k", symbol=SYM, bars=bars, bar_open=int(row.entry_time),
                           side=int(row.side), entry=entry, stop=stop, outcome="REJECTED",
                           reason="market state is HALTED", limit_name=None)
    # a record-only coin reads "record_only" whatever the refusal said
    assert got["record_only"] is True and got["skip"] == "record_only" and got["rank"] is None
    assert pd.isna(got["lab"]["day_rank"])


async def test_btc_context_is_fetched_once_per_bar():
    row, bars, entry, stop = _a_signal()
    client = FakeDelta()
    rec = SignalRecorder(STRATEGY, client=client, contract_values={SYM: 1.0})
    kw = dict(symbol=SYM, bars=bars, bar_open=int(row.entry_time), side=int(row.side),
              entry=entry, stop=stop)
    first = rec.compute(**kw)
    second = rec.compute(**kw)
    assert client.calls.count("BTCUSD") == 2              # 1m + 1h, once each
    assert first["lab"] == second["lab"] or all(
        (a == b) or (isinstance(a, float) and math.isnan(a) and math.isnan(b))
        for a, b in zip(first["lab"].values(), second["lab"].values()))


# --- the universe ---------------------------------------------------------------

def _row(sym, change, turnover=1_000_000, **kw):
    r = {"symbol": sym, "ltp_change_24h": str(change), "turnover_usd": turnover,
         "top_tag": "crypto", "product_trading_status": "operational",
         "contract_type": "perpetual_futures", "tags": ["x"]}
    r.update(kw)
    return r


async def test_pick_wide_splits_trade_and_record_only():
    rows = [_row(f"G{i:02d}USD", 100 - i) for i in range(1, 41)]     # 40 gainers
    rows += [_row("BTCUSD", -1.5, 9e9), _row("ETHUSD", 0.5, 8e9),
             _row("AKEUSD", 99.5, 2e6)]                              # AKE ranks 1st
    trade, ro, info = universe.pick_wide(rows, held=["G25USD", "OLDUSD"], env={})
    # the trade universe is exactly what it always was: top 10 + held
    assert trade == universe.pick(rows, ["G25USD", "OLDUSD"], env={})
    assert trade[:10] == ("AKEUSD",) + tuple(f"G{i:02d}USD" for i in range(1, 10))
    assert "G25USD" in trade and "OLDUSD" in trade
    # ranks 11-30 minus the held one, then the listed watchlist coins
    assert ro == tuple(f"G{i:02d}USD" for i in range(10, 30) if i != 25) + ("BTCUSD", "ETHUSD")
    assert not set(trade) & set(ro)
    assert info["G10USD"] == {"rank": 11, "change": 90.0, "turnover": 1e6, "tags": ["x"],
                              "record_only": True}
    assert info["BTCUSD"] == {"rank": None, "change": -1.5, "turnover": 9e9, "tags": ["x"],
                              "record_only": True}
    assert info["AKEUSD"]["record_only"] is False and info["G25USD"]["record_only"] is False
    assert "G31USD" not in info and "BEATUSD" not in ro      # not listed -> skipped
    assert "OLDUSD" not in info


async def test_pick_wide_settings_can_turn_recording_off():
    rows = [_row(f"G{i:02d}USD", 100 - i) for i in range(1, 41)] + [_row("BTCUSD", 1.0)]
    t, ro, _ = universe.pick_wide(rows, [], env={"DELTABOT_RECORD_TOP_N": "10",
                                                 "DELTABOT_RECORD_EXTRA": ""})
    assert len(t) == 10 and ro == ()
    _, ro, _ = universe.pick_wide(rows, [], env={"DELTABOT_RECORD_TOP_N": "12",
                                                 "DELTABOT_RECORD_EXTRA": "btcusd, NOPEUSD"})
    assert ro == ("G11USD", "G12USD", "BTCUSD")
    assert universe.record_settings_from_env({}) == (30, ("BTCUSD", "ETHUSD", "BEATUSD", "AKEUSD"))


async def test_resolve_products_drops_an_unlisted_record_only_coin_but_never_a_trade_coin():
    from live.config import ConfigError, resolve_products

    class Client:
        def _read(self, path, params=None):
            return [{"symbol": "BTCUSD", "id": 84, "state": "live", "tick_size": "0.5"},
                    {"symbol": "AKEUSD", "id": 9, "state": "live", "tick_size": "0.0001"}]
    got = resolve_products(Client(), ["BTCUSD"], optional=["AKEUSD", "GONEUSD"])
    assert set(got) == {"BTCUSD", "AKEUSD"}
    with pytest.raises(ConfigError):
        resolve_products(Client(), ["GONEUSD"], optional=["BTCUSD"])


async def test_the_entry_point_wires_record_only_coins_and_keeps_product_ids_trade_only(monkeypatch):
    import live.__main__ as entry
    from live.config import Product
    from decimal import Decimal
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/x")
    monkeypatch.delenv("DELTABOT_DRY_RUN", raising=False)
    captured = {}

    class _Stop(Exception):
        pass

    class _Client:
        is_prod = False

    def fake_bot(settings, repo, costs, **kw):
        captured.update(settings=settings, costs=costs, **kw)
        raise _Stop

    async def fake_universe(client, venue, url):
        return ("AUSD",), ("BUSD", "CUSD"), {"AUSD": {"rank": 1}, "BUSD": {"rank": 11},
                                             "CUSD": {"rank": None}}

    def fake_resolve(client, symbols, optional=()):
        return {s: Product(s, i, Decimal("0.1"), Decimal("1"))
                for i, s in enumerate(list(symbols) + ["BUSD"], 1)}          # CUSD unlisted

    monkeypatch.setattr(entry, "venue_name", lambda *a, **k: "prod")
    monkeypatch.setattr(entry, "client_from_env", lambda *a, **k: _Client())
    monkeypatch.setattr(entry, "_universe", fake_universe)
    monkeypatch.setattr(entry, "resolve_products", fake_resolve)
    monkeypatch.setattr(entry, "load_costs",
                        lambda s, b, optional=(): {x: ALL_COSTS[SYM] for x in [*s, *optional]})
    monkeypatch.setattr(entry, "signal_recorder", lambda *a, **k: captured.setdefault("rec", (a, k)))
    monkeypatch.setattr(entry, "PostgresRepository", lambda *a, **k: object())
    monkeypatch.setattr(entry, "SingleInstanceLock", lambda *a, **k: object())
    monkeypatch.setattr(entry, "Backfiller", lambda *a, **k: object())
    monkeypatch.setattr(entry, "LiveTradingBot", fake_bot)
    with pytest.raises(_Stop):
        await entry.main()
    assert captured["settings"].symbols == ("AUSD",)
    assert captured["record_only"] == ("BUSD",)
    assert captured["product_ids"] == {"AUSD": 1}            # the broker never sees BUSD's id
    assert captured["record_backfiller"] is not None
    (args, kw) = captured["rec"]
    assert args[1] == ("AUSD", "BUSD") and kw["record_only"] == ("BUSD",)
