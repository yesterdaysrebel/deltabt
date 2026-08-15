"""Maintenance/halt handling, and WebSocket reconnect + stale-feed detection."""

from __future__ import annotations

import asyncio
import json

import pandas as pd
import pytest

from app.market_data.candle_builder import SymbolCandleBuilder
from app.market_data.delta_ws import DeltaMarketFeed, StaleFeedError
from app.market_data.market_state import (HaltDetector, MarketState,
                                          halt_min_run)
from deltabt.config import HALT_MIN_RUN_BARS
from app.market_data.normalize import Candle


def bar(start, o=100.0, h=101.0, l=99.0, c=100.5, v=10.0):
    return Candle("BTCUSD", start, o, h, l, c, v)


def flat_bar(start, px=100.0):
    """A forward-filled maintenance bar: o=h=l=c, zero volume."""
    return Candle("BTCUSD", start, px, px, px, px, 0.0)


# =====================================================================
# HALT DETECTION -- section 12
# =====================================================================


class TestPerSymbolHaltThreshold:
    """A threshold calibrated on maintenance does not fit thin liquidity.

    20 comes from a real maintenance window: 148 consecutive flat bars on
    2026-04-12. It assumes flat bars arrive in long runs. On a thinly traded
    symbol they arrive constantly in short bursts instead, because a minute
    with no trade is forward-filled whatever the reason. BANKUSD measured 39.2%
    flat bars over 24h across 322 runs with a maximum run of 10, so the default
    never fires and the indicator stack silently reads fabricated prices.
    """

    def test_the_default_is_unchanged_for_normal_symbols(self):
        for s in ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "BEATUSD", "AKEUSD"):
            assert halt_min_run(s) == HALT_MIN_RUN_BARS

    def test_bankusd_gets_the_shorter_threshold(self):
        assert halt_min_run("BANKUSD") == 5
        assert halt_min_run("bankusd") == 5, "lookup must not be case sensitive"

    def test_a_run_that_the_default_ignores_halts_bankusd(self):
        """The measured case: runs of up to 10, never reaching 20."""
        default = HaltDetector("BTCUSD", min_run=halt_min_run("BTCUSD"))
        thin = HaltDetector("BANKUSD", min_run=halt_min_run("BANKUSD"))
        for d in (default, thin):
            d.observe(bar(0))
            for i in range(1, 11):
                d.observe(flat_bar(i * 60))
        assert default.state is MarketState.LIVE, (
            "10 flat bars is below the 20-bar default, as designed")
        assert default.can_trade
        assert thin.state is MarketState.HALTED
        assert not thin.can_trade, (
            "THE POINT: BANKUSD must stop evaluating on the same run that "
            "BTCUSD correctly trades through")

    def test_the_shorter_threshold_still_tolerates_a_brief_gap(self):
        """4 flat bars must not halt it, or it would never trade at all."""
        d = HaltDetector("BANKUSD", min_run=halt_min_run("BANKUSD"))
        d.observe(bar(0))
        for i in range(1, 5):
            d.observe(flat_bar(i * 60))
        assert d.state is MarketState.LIVE
        assert d.can_trade


class TestHaltDetection:
    def test_normal_market_stays_live(self):
        d = HaltDetector("BTCUSD")
        for i in range(50):
            assert d.observe(bar(i * 60, c=100 + i * 0.01)) is MarketState.LIVE
        assert d.can_trade

    def test_short_illiquid_run_is_not_a_halt(self):
        """19 flat bars is thin liquidity; 20 is maintenance."""
        d = HaltDetector("BTCUSD", min_run=20)
        d.observe(bar(0))
        for i in range(1, 20):
            d.observe(flat_bar(i * 60))
        assert d.state is MarketState.LIVE
        assert d.can_trade

    def test_long_flat_run_is_a_halt(self):
        d = HaltDetector("BTCUSD", min_run=20)
        d.observe(bar(0))
        for i in range(1, 25):
            d.observe(flat_bar(i * 60))
        assert d.state is MarketState.HALTED
        assert not d.can_trade

    def test_reopen_bar_is_not_tradable(self):
        """The +0.32% one-minute auction gap must never be read as a breakout."""
        d = HaltDetector("BTCUSD", min_run=20)
        d.observe(bar(0, c=100.0))
        for i in range(1, 25):
            d.observe(flat_bar(i * 60))
        state = d.observe(bar(25 * 60, o=100.32, h=100.4, l=100.0, c=100.32))
        assert state is MarketState.REOPENING
        assert not d.can_trade

    def test_trading_resumes_only_after_a_post_reopen_bar(self):
        d = HaltDetector("BTCUSD", min_run=20)
        d.observe(bar(0))
        for i in range(1, 25):
            d.observe(flat_bar(i * 60))
        d.observe(bar(25 * 60, o=100.32, h=100.4, l=100.0, c=100.32))
        assert not d.can_trade
        assert d.observe(bar(26 * 60, c=100.35)) is MarketState.LIVE
        assert d.can_trade

    def test_halt_event_records_the_gap(self):
        d = HaltDetector("BTCUSD", min_run=20)
        d.observe(bar(0, c=100.0))
        for i in range(1, 25):
            d.observe(flat_bar(i * 60))
        d.observe(bar(25 * 60, o=100.32, h=100.4, l=100.0, c=100.32))
        d.observe(bar(26 * 60))
        assert len(d.history) == 1
        ev = d.history[0]
        assert ev.flat_bars == 24
        assert ev.reopen_bar == 25 * 60
        assert ev.reopen_gap_pct == pytest.approx(0.32, abs=0.01)

    def test_restarting_inside_a_halt_comes_up_halted(self):
        """Otherwise a restart during maintenance trades the reopen bar."""
        rows = [{"time": i * 60, "open": 100.0, "high": 100.0, "low": 100.0,
                 "close": 100.0, "volume": 0.0} for i in range(30)]
        rows[0].update(high=101.0, low=99.0, close=100.5, volume=5.0)
        d = HaltDetector("BTCUSD", min_run=20)
        d.prime_from_history(pd.DataFrame(rows))
        assert d.state is MarketState.HALTED
        assert not d.can_trade

    def test_restarting_in_a_normal_market_comes_up_live(self):
        rows = [{"time": i * 60, "open": 100.0 + i, "high": 101.0 + i,
                 "low": 99.0 + i, "close": 100.5 + i, "volume": 5.0}
                for i in range(30)]
        d = HaltDetector("BTCUSD")
        d.prime_from_history(pd.DataFrame(rows))
        assert d.state is MarketState.LIVE

    def test_halt_state_agrees_with_the_research_halt_rule(self):
        """Live detection and deltabt.data.quality must not disagree."""
        from deltabt.data.quality import halt_mask
        rows = [{"time": i * 60, "open": 100.0, "high": 101.0, "low": 99.0,
                 "close": 100.5, "volume": 5.0} for i in range(10)]
        rows += [{"time": (10 + i) * 60, "open": 100.0, "high": 100.0,
                  "low": 100.0, "close": 100.0, "volume": 0.0} for i in range(25)]
        rows += [{"time": 35 * 60, "open": 100.3, "high": 100.4, "low": 100.0,
                  "close": 100.32, "volume": 8.0}]
        df = pd.DataFrame(rows)
        mask = halt_mask(df)
        assert mask[10:35].all(), "research rule should flag the flat run"
        assert mask[35], "research rule should flag the reopen bar"

        d = HaltDetector("BTCUSD", min_run=20)
        states = []
        for r in rows:
            states.append(d.observe(Candle("BTCUSD", r["time"], r["open"],
                                           r["high"], r["low"], r["close"],
                                           r["volume"])))
        assert states[34] is MarketState.HALTED
        assert states[35] is MarketState.REOPENING


# =====================================================================
# WEBSOCKET -- reconnect, stale feed
# =====================================================================


class FakeWS:
    """Scripted socket. Strings are frames; exceptions are raised in order."""

    def __init__(self, script, sent):
        self.script = list(script)
        self.sent = sent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def recv(self):
        if not self.script:
            await asyncio.sleep(3600)          # silent socket: still "open"
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def until(predicate, timeout=5.0, interval=0.01):
    """Wait for a condition instead of sleeping a guessed interval.

    Fixed sleeps make async tests flaky under load; every wait here is on the
    thing the test actually cares about.
    """
    import time as _t
    deadline = _t.monotonic() + timeout
    while _t.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


async def shutdown(feed, task, timeout=5.0):
    """Stop the feed and wait for its task to finish.

    Not `task.cancel()` + `pytest.raises(CancelledError)`: stop() lets run()
    exit cleanly, so whether the cancel lands is a race. Asserting on that race
    made this test flaky roughly one run in three.
    """
    feed.stop()
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except asyncio.TimeoutError:
        task.cancel()
        raise AssertionError("feed task did not stop when asked")
    except asyncio.CancelledError:
        pass


class TestFeed:
    def test_subscribe_payload_matches_the_live_protocol(self):
        f = DeltaMarketFeed(["BTCUSD", "ETHUSD"], lambda m: None)
        p = f.subscribe_payload()
        assert p["type"] == "subscribe"
        names = {c["name"] for c in p["payload"]["channels"]}
        assert names == {"v2/ticker", "candlestick_1m", "all_trades"}
        for ch in p["payload"]["channels"]:
            assert ch["symbols"] == ["BTCUSD", "ETHUSD"]

    @pytest.mark.asyncio
    async def test_messages_reach_the_handler(self):
        got, sent = [], []
        script = ['{"type":"v2/ticker","symbol":"BTCUSD"}',
                  '{"type":"all_trades","symbol":"BTCUSD"}']
        f = DeltaMarketFeed(["BTCUSD"], got.append, recv_timeout=0.05,
                            connect=lambda: FakeWS(script, sent))
        task = asyncio.create_task(f.run())
        assert await until(lambda: len(got) >= 2)
        await shutdown(f, task)
        assert [m["type"] for m in got][:2] == ["v2/ticker", "all_trades"]
        assert sent and sent[0]["type"] == "subscribe"

    @pytest.mark.asyncio
    async def test_stop_interrupts_a_pending_receive(self):
        """A quiet market must not delay shutdown until the receive timeout.

        Otherwise SIGTERM is ignored for up to `recv_timeout` and the
        orchestrator SIGKILLs the process mid-write.
        """
        f = DeltaMarketFeed(["BTCUSD"], lambda m: None, recv_timeout=30.0,
                            connect=lambda: FakeWS([], []))
        task = asyncio.create_task(f.run())
        assert await until(lambda: f.stats.connected)
        f.stop()
        await asyncio.wait_for(task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_silent_socket_raises_stale_not_hang(self):
        """The failure mode that every process-level probe misses."""
        f = DeltaMarketFeed(["BTCUSD"], lambda m: None, recv_timeout=0.05,
                            connect=lambda: FakeWS([], []))
        with pytest.raises(StaleFeedError):
            async with f._connect() as ws:
                await f._pump(ws)

    @pytest.mark.asyncio
    async def test_stale_feed_triggers_a_reconnect(self):
        sent = []
        f = DeltaMarketFeed(["BTCUSD"], lambda m: None, recv_timeout=0.02,
                            max_backoff=0.01,
                            connect=lambda: FakeWS([], sent))
        task = asyncio.create_task(f.run())
        assert await until(lambda: f.stats.stale_events >= 1 and len(sent) >= 2)
        await shutdown(f, task)
        assert f.stats.stale_events >= 1
        assert f.stats.reconnects >= 1
        assert len(sent) >= 2, "each reconnect must resubscribe"

    @pytest.mark.asyncio
    async def test_connection_error_counts_and_resubscribes(self):
        sent = []
        scripts = [[ConnectionError("dropped")], ['{"type":"v2/ticker"}']]

        def connect():
            return FakeWS(scripts.pop(0) if scripts else [], sent)

        f = DeltaMarketFeed(["BTCUSD"], lambda m: None, recv_timeout=0.05,
                            max_backoff=0.01, connect=connect)
        task = asyncio.create_task(f.run())
        assert await until(lambda: f.stats.errors >= 1 and f.stats.reconnects >= 1), (
            f"errors={f.stats.errors} reconnects={f.stats.reconnects}")
        await shutdown(f, task)
        assert f.stats.errors >= 1
        assert f.stats.reconnects >= 1

    @pytest.mark.asyncio
    async def test_undecodable_frame_is_dropped_not_fatal(self):
        got, sent = [], []
        f = DeltaMarketFeed(["BTCUSD"], got.append, recv_timeout=0.05,
                            connect=lambda: FakeWS(
                                ["not json", '{"type":"v2/ticker"}'], sent))
        task = asyncio.create_task(f.run())
        assert await until(lambda: len(got) >= 1 and f.stats.errors >= 1)
        await shutdown(f, task)
        assert got[0]["type"] == "v2/ticker" and f.stats.errors >= 1

    def test_staleness_is_measured_from_the_last_message(self):
        f = DeltaMarketFeed(["BTCUSD"], lambda m: None)
        assert f.stats.seconds_since_last_message == float("inf")
        import time
        f.stats.last_message_at = time.time() - 45
        assert 44 < f.stats.seconds_since_last_message < 47


# =====================================================================
# HALT + BUILDER together
# =====================================================================


def test_builder_and_halt_detector_agree_on_a_maintenance_window():
    from app.market_data.normalize import CandleUpdate
    b = SymbolCandleBuilder("BTCUSD")
    d = HaltDetector("BTCUSD", min_run=20)

    def push(start, o, h, l, c, v):
        return b.ingest(CandleUpdate("BTCUSD", start, o, h, l, c, v,
                                     (start + 30) * 1_000_000))

    for i in range(5):
        for x in push(i * 60, 100, 101, 99, 100.5, 5.0):
            d.observe(x)
    for i in range(5, 30):
        for x in push(i * 60, 100.5, 100.5, 100.5, 100.5, 0.0):
            d.observe(x)
    for x in push(30 * 60, 100.8, 100.9, 100.5, 100.82, 9.0):
        d.observe(x)
    for x in push(31 * 60, 100.8, 100.9, 100.7, 100.85, 7.0):
        d.observe(x)

    assert b.stats.gaps == 0, "a halt is flat bars, not missing bars"
    assert d.state in (MarketState.REOPENING, MarketState.LIVE)
