"""Closed-bar assembly, validation, gap detection and 5m derivation."""

from __future__ import annotations

import pytest

from app.market_data.candle_builder import SymbolCandleBuilder, validate_ohlc
from app.market_data.normalize import (
    CandleUpdate,
    NormalizeError,
    normalize_candle,
    normalize_ticker,
    normalize_trade,
    us_to_s,
)

US = 1_000_000


def upd(start, o=100.0, h=None, l=None, c=100.5, v=10.0, updated=None, sym="BTCUSD"):
    """A valid candle update. High/low widen to contain open and close unless
    given explicitly, so that varying `c` in a test cannot accidentally
    produce an impossible bar."""
    h = max(o, c) + 0.5 if h is None else h
    l = min(o, c) - 0.5 if l is None else l
    return CandleUpdate(symbol=sym, start=start, open=o, high=h, low=l, close=c,
                        volume=v, updated_us=updated if updated is not None
                        else (start + 30) * US)


# =====================================================================
# NORMALIZATION -- the microsecond/second boundary
# =====================================================================


class TestNormalize:
    def test_socket_timestamps_are_microseconds(self):
        # Observed live: candle_start_time 1786560120000000 is 2026-08-12.
        assert us_to_s(1786560120000000) == 1786560120

    def test_candle_start_converted_to_seconds(self):
        c = normalize_candle({
            "symbol": "BTCUSD", "candle_start_time": 1786560120000000,
            "open": 63437.0, "high": 63437.0, "low": 63431.0,
            "close": 63432.5, "volume": 891.0, "last_updated": 1786560149686139,
        })
        assert c.start == 1786560120
        assert c.start % 60 == 0

    def test_misaligned_candle_start_is_rejected(self):
        with pytest.raises(NormalizeError, match="minute-aligned"):
            normalize_candle({"symbol": "BTCUSD",
                              "candle_start_time": 1786560123000000,
                              "open": 1, "high": 1, "low": 1, "close": 1})

    def test_ticker_keeps_ltp_and_mark_distinct(self):
        t = normalize_ticker({
            "symbol": "ETHUSD", "timestamp": 1786560150804227,
            "close": 1887.2, "mark_price": "1887.40326807",
            "funding_rate": "0.0032573025785064755",
            "quotes": {"best_bid": "1887.35", "best_ask": "1887.45"},
        })
        assert t.ltp == 1887.2
        assert t.mark == pytest.approx(1887.40326807)
        assert t.ltp != t.mark, "conflating LTP and mark mistimes every stop"
        assert t.best_bid == 1887.35 and t.best_ask == 1887.45

    def test_trade_normalizes_string_price(self):
        tr = normalize_trade({"symbol": "BTCUSD", "timestamp": 1786560151204655,
                              "price": "63432.5", "size": 5})
        assert tr.price == 63432.5 and tr.size == 5

    def test_missing_symbol_raises(self):
        with pytest.raises(NormalizeError):
            normalize_ticker({"close": 1, "mark_price": 1})


# =====================================================================
# OHLC VALIDATION -- section 13, do not silently repair
# =====================================================================


class TestOhlcValidation:
    def test_good_bar_passes(self):
        assert validate_ohlc(upd(60)) is None

    @pytest.mark.parametrize("kw,frag", [
        (dict(h=98.0, l=99.0), "high"),                    # high < low
        (dict(o=200.0, h=101.0, l=99.0), "open"),          # open outside range
        (dict(c=1.0, h=101.0, l=99.0), "close"),           # close outside range
        (dict(l=-1.0, h=101.0), "low"),                    # non-positive
        (dict(v=-5.0), "volume"),
    ])
    def test_impossible_bars_are_named(self, kw, frag):
        reason = validate_ohlc(upd(60, **kw))
        assert reason is not None and frag in reason

    def test_nan_close_rejected(self):
        assert validate_ohlc(upd(60, c=float("nan"))) is not None

    def test_builder_drops_impossible_bar_and_counts_it(self):
        b = SymbolCandleBuilder("BTCUSD")
        b.ingest(upd(60, h=1.0, l=100.0))
        assert b.stats.invalid_ohlc == 1
        assert b.forming_start is None


# =====================================================================
# CLOSED-BAR ASSEMBLY
# =====================================================================


class TestClosing:
    def test_forming_bar_is_never_emitted(self):
        b = SymbolCandleBuilder("BTCUSD")
        assert b.ingest(upd(60)) == []
        assert b.ingest(upd(60, c=100.9, updated=95 * US)) == []
        assert b.last_closed_1m is None

    def test_bar_closes_when_a_later_bar_appears(self):
        b = SymbolCandleBuilder("BTCUSD")
        b.ingest(upd(60, c=100.5))
        closed = b.ingest(upd(120))
        assert len(closed) == 1
        assert closed[0].start == 60 and closed[0].close == 100.5

    def test_updates_accumulate_into_the_closed_bar(self):
        b = SymbolCandleBuilder("BTCUSD")
        b.ingest(upd(60, o=100, h=100.2, l=99.9, c=100.1, updated=61 * US))
        b.ingest(upd(60, o=100, h=103.0, l=98.0, c=102.5, updated=110 * US))
        closed = b.ingest(upd(120))[0]
        assert (closed.high, closed.low, closed.close) == (103.0, 98.0, 102.5)

    def test_identical_update_is_a_duplicate(self):
        b = SymbolCandleBuilder("BTCUSD")
        u = upd(60, updated=90 * US)
        b.ingest(u)
        b.ingest(u)
        assert b.stats.duplicates == 1

    def test_out_of_order_update_within_bar_ignored(self):
        b = SymbolCandleBuilder("BTCUSD")
        b.ingest(upd(60, c=100.5, updated=110 * US))
        b.ingest(upd(60, c=999.0, h=1000.0, updated=70 * US))   # older
        closed = b.ingest(upd(120))[0]
        assert closed.close == 100.5
        assert b.stats.out_of_order == 1

    def test_closed_bar_is_immutable(self):
        """A late update for a closed bar must never rewrite it.

        By the time it arrives a signal may already have been emitted from
        that bar.
        """
        b = SymbolCandleBuilder("BTCUSD")
        b.ingest(upd(60, c=100.5))
        b.ingest(upd(120))
        b.ingest(upd(60, c=500.0, h=600.0, updated=119 * US))
        assert b.bars[0].close == 100.5
        assert b.stats.out_of_order == 1

    def test_clock_rollover_needs_the_grace_period(self):
        b = SymbolCandleBuilder("BTCUSD")
        b.ingest(upd(60))
        assert b.roll_on_clock(121, grace=5.0) == []      # 60+60+5 = 125
        closed = b.roll_on_clock(126, grace=5.0)
        assert len(closed) == 1 and closed[0].start == 60

    def test_non_monotonic_close_refused(self):
        b = SymbolCandleBuilder("BTCUSD")
        b.ingest(upd(120))
        b.ingest(upd(180))
        b._last_closed_start = None                       # simulate corruption
        b._forming = upd(60)
        assert b._close_forming() == []
        assert b.stats.non_monotonic == 1


# =====================================================================
# GAPS
# =====================================================================


class TestGaps:
    def test_contiguous_bars_produce_no_gap(self):
        b = SymbolCandleBuilder("BTCUSD")
        for t in range(60, 60 + 60 * 6, 60):
            b.ingest(upd(t))
        assert b.stats.gaps == 0

    def test_missing_minutes_are_detected_and_counted(self):
        b = SymbolCandleBuilder("BTCUSD")
        b.ingest(upd(60))
        b.ingest(upd(120))
        b.ingest(upd(420))            # 180..360 missing => 4 minutes
        assert b.stats.gaps == 1
        assert b.stats.missing_minutes == 4
        assert b.gaps[0].missing == 4

    def test_recent_gap_count_windows_correctly(self):
        b = SymbolCandleBuilder("BTCUSD")
        b.ingest(upd(60))
        b.ingest(upd(600))
        assert b.recent_gap_count(within_seconds=300, now=700) == 1
        assert b.recent_gap_count(within_seconds=300, now=2000) == 0


# =====================================================================
# BACKFILL SPLICING
# =====================================================================


class TestBackfill:
    def test_rest_bars_never_overwrite_live_bars(self):
        from app.market_data.normalize import Candle
        b = SymbolCandleBuilder("BTCUSD")
        b.ingest(upd(60, c=100.5))
        b.ingest(upd(120))                       # closes bar 60 from the socket
        rest = [Candle("BTCUSD", 60, 1, 1, 1, 1, 0, source="rest")]
        assert b.ingest_backfill(rest) == 0
        assert b.bars[0].close == 100.5

    def test_older_history_is_spliced_in_order(self):
        from app.market_data.normalize import Candle
        b = SymbolCandleBuilder("BTCUSD")
        b.ingest(upd(600))
        b.ingest(upd(660))
        rest = [Candle("BTCUSD", t, 100, 101, 99, 100, 5, source="rest")
                for t in range(60, 600, 60)]
        assert b.ingest_backfill(rest) == 9
        starts = [x.start for x in b.bars]
        assert starts == sorted(starts)
        assert starts[0] == 60


# =====================================================================
# 5m DERIVATION
# =====================================================================


class TestFiveMinute:
    def _fill(self, b, first, count):
        for i in range(count):
            b.ingest(upd(first + i * 60, c=100 + i))
        b.ingest(upd(first + count * 60))     # close the last one

    def test_no_5m_bar_mid_bucket(self):
        b = SymbolCandleBuilder("BTCUSD")
        self._fill(b, 300, 3)
        bar, missing = b.closed_5m_for(420)     # 420 is not 540
        assert bar is None

    def test_5m_closes_on_the_final_minute(self):
        b = SymbolCandleBuilder("BTCUSD")
        self._fill(b, 300, 5)
        bar, missing = b.closed_5m_for(540)
        assert bar is not None and missing == 0
        assert bar.start == 300

    def test_5m_ohlc_aggregates_exactly(self):
        b = SymbolCandleBuilder("BTCUSD")
        vals = [(100, 105, 98, 102), (102, 110, 101, 108), (108, 109, 99, 100),
                (100, 104, 95, 103), (103, 107, 102, 106)]
        for i, (o, h, l, c) in enumerate(vals):
            b.ingest(upd(300 + i * 60, o=o, h=h, l=l, c=c, v=2.0))
        b.ingest(upd(600))
        bar, missing = b.closed_5m_for(540)
        assert missing == 0
        assert bar.open == 100 and bar.close == 106
        assert bar.high == 110 and bar.low == 95
        assert bar.volume == pytest.approx(10.0)

    def test_incomplete_bucket_is_flagged_not_repaired(self):
        b = SymbolCandleBuilder("BTCUSD")
        for t in (300, 360, 540):
            b.ingest(upd(t))
        b.ingest(upd(600))
        bar, missing = b.closed_5m_for(540)
        assert missing == 2
        assert b.stats.incomplete_5m == 1

    def test_frame_5m_matches_the_backtester_resampler(self):
        """Live 5m and research 5m must be the same code path."""
        from deltabt.strategy import resample_ohlcv
        b = SymbolCandleBuilder("BTCUSD")
        for i in range(30):
            b.ingest(upd(300 + i * 60, o=100 + i, h=101 + i, l=99 + i,
                         c=100.5 + i, v=1.0 + i))
        b.ingest(upd(300 + 30 * 60))
        live = b.frame_5m()
        expected = resample_ohlcv(b.frame(), 5)
        expected = expected[expected["time"].isin(live["time"])].reset_index(drop=True)
        assert live[["time", "open", "high", "low", "close", "volume"]].equals(
            expected[["time", "open", "high", "low", "close", "volume"]]
        )

    def test_frame_5m_drops_incomplete_buckets(self):
        b = SymbolCandleBuilder("BTCUSD")
        for t in (300, 360, 420, 480, 540,        # complete bucket 300
                  600, 660):                       # partial bucket 600
            b.ingest(upd(t))
        b.ingest(upd(720))
        out = b.frame_5m()
        assert out["time"].tolist() == [300]


# =====================================================================
# WARM-UP CONTIGUITY -- found by the final preflight
# =====================================================================


class TestWarmUpRepair:
    """A bulk paginated fetch can drop a minute a narrow refetch returns.

    Observed on BTCUSD at 2026-08-10 09:57 UTC: the 7-day pull returned 10,079
    of 10,080 minutes, and a targeted request for that single minute returned
    it immediately. The data exists; the bulk path loses it.

    A hole makes the 5m bucket containing it incomplete, so it is dropped from
    the resampled series, and the Wilder chains then treat two non-adjacent
    bars as adjacent -- so indicator values differ from what the research code
    produces on the same window. That equivalence is the point of the forward
    test.
    """

    def test_find_gaps_locates_holes(self):
        from app.market_data.backfill import find_gaps
        from app.market_data.normalize import Candle
        bars = [Candle("BTCUSD", t, 1, 1, 1, 1, 1) for t in (60, 120, 300, 360)]
        assert find_gaps(bars) == [(180, 240)]

    def test_find_gaps_on_contiguous_data(self):
        from app.market_data.backfill import find_gaps
        from app.market_data.normalize import Candle
        bars = [Candle("BTCUSD", t, 1, 1, 1, 1, 1) for t in range(60, 600, 60)]
        assert find_gaps(bars) == []

    @pytest.mark.asyncio
    async def test_warm_up_refetches_a_dropped_minute(self):
        from app.market_data.backfill import Backfiller, find_gaps
        from app.market_data.normalize import Candle

        full = {t: Candle("BTCUSD", t, 100, 101, 99, 100, 5)
                for t in range(0, 600, 60)}
        calls: list[tuple[int, int]] = []

        class Flaky(Backfiller):
            def __init__(self):
                pass

            async def fetch(self, symbol, start, end):
                calls.append((start, end))
                bars = [b for t, b in sorted(full.items()) if start <= t <= end]
                # the BULK pull drops one minute; a narrow refetch does not
                if end - start > 300:
                    bars = [b for b in bars if b.start != 300]
                return bars

        bars = await Flaky().warm_up("BTCUSD", 1, now=600)
        assert find_gaps(bars) == [], "the hole must be repaired"
        assert any(b.start == 300 for b in bars)
        assert len(calls) >= 2, "a targeted refetch must have been issued"

    @pytest.mark.asyncio
    async def test_an_unrecoverable_hole_is_returned_not_hidden(self):
        """A minute the exchange never served is a fact about the market."""
        from app.market_data.backfill import Backfiller, find_gaps
        from app.market_data.normalize import Candle

        class Missing(Backfiller):
            def __init__(self):
                pass

            async def fetch(self, symbol, start, end):
                return [Candle("BTCUSD", t, 100, 101, 99, 100, 5)
                        for t in range(0, 600, 60) if t != 300
                        and start <= t <= end]

        bars = await Missing().warm_up("BTCUSD", 1, now=600, repair_passes=2)
        assert find_gaps(bars) == [(300, 300)], "reported, not papered over"
