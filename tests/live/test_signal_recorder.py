"""Readings at every signal: recorded after the decision, never in its way."""
from __future__ import annotations

import asyncio
import math
import time

import pytest

from app.config.settings import RiskConfig, Settings
from app.config.variants import resolve_strategy
from app.learning.recorder import SignalRecorder, skip_reason
from app.market_data.normalize import Candle, CandleUpdate
from app.persistence.repository import InMemoryRepository
from app.runtime.bot import TradingBot
from deltabt import signal_features as SF
from deltabt.costs import SymbolCosts
from tests import signal_lab_fixture as fx
from tests.live.test_recovery import DeadFeed

pytestmark = pytest.mark.asyncio

SYM = fx.SYMBOL
COSTS = {SYM: SymbolCosts(symbol=SYM, tick_size=0.0001, contract_value=1.0, maker_fee=0.0002,
                          taker_fee=0.0005, max_leverage=50.0, position_size_limit=1e9,
                          funding_interval_seconds=14400, slippage_bps=2.0)}
STRATEGY = resolve_strategy({"DELTABOT_VARIANT": "SPEC:manual_scalp_both_t3_st@5"})
US = 1_000_000
COIN = fx.coin(7, 1.25)
BTC = fx.coin(11, 60_000.0)


def _rows(df, lo, hi, value="close"):
    d = df[(df.time >= lo) & (df.time <= hi)]
    return [{"time": int(r.time), "open": r.open, "high": r.high, "low": r.low,
             "close": getattr(r, value), "volume": r.volume} for r in d.itertuples()]


class FakeDelta:
    """Public REST, served from the synthetic market."""

    def __init__(self, fail: bool = False):
        self.fail, self.calls = fail, []

    def _get(self, path, params=None):
        self.calls.append(path)
        if self.fail:
            raise RuntimeError("venue down")
        if path.startswith("/v2/tickers/"):
            return {"result": {"mark_price": "1.2501", "close": 1.25, "funding_rate": "0.01",
                               "oi": "1000", "oi_change_usd_6h": "50", "tags": ["meme"],
                               "quotes": {"best_bid": "1.2499", "best_ask": "1.2501"}}}
        if path.startswith("/v2/l2orderbook/"):
            return {"result": {"buy": [{"price": "1.2499", "size": 100}],
                               "sell": [{"price": "1.2501", "size": 80}]}}
        raise AssertionError(path)

    def candles(self, symbol, resolution, start, end):
        self.calls.append(symbol)
        if self.fail:
            raise RuntimeError("venue down")
        if symbol == "BTCUSD" and resolution == "1m":
            return _rows(BTC["ltp"], start, end)
        if symbol == "BTCUSD" and resolution == "1h":
            from deltabt.strategy import resample_complete
            return _rows(resample_complete(BTC["ltp"], 60), start, end)
        if symbol.startswith("OI:"):
            oi = fx.oi().rename(columns={"oi": "close"}).assign(open=0, high=0, low=0, volume=0)
            return _rows(oi, start, end)
        if symbol.startswith("FUNDING:"):
            return _rows(COIN["funding"], start, end)
        raise AssertionError(symbol)


def _bars(lo=0, hi=None):
    df = COIN["ltp"].iloc[lo:hi]
    return [Candle(SYM, int(r.time), float(r.open), float(r.high), float(r.low),
                   float(r.close), float(r.volume), source="rest") for r in df.itertuples()]


def _recorder(client, **kw):
    pk = fx.picks().iloc[2]
    return SignalRecorder(STRATEGY, client=client, picks={SYM: {"change": 12.5, "rank": 3,
                                                               "turnover": 2e6}},
                          pick_day=int(pk.pick_day), launches={SYM: fx.META["launch"]},
                          contract_values={SYM: 1.0}, **kw)


# --- the recorder alone ------------------------------------------------------

def _a_signal():
    """A real signal bar of the lab's golden set, with its entry and stop."""
    import pandas as pd
    from deltabt.strategy import resample_complete
    gold = pd.read_parquet(fx.GOLDEN)
    p5 = resample_complete(COIN["ltp"], 5)
    row = gold[gold.entry_time > COIN["ltp"].time.iloc[0] + 3 * 86400].iloc[0]
    entry = float(p5.close[p5.time == row.entry_time].iloc[0])
    stop = entry - row.side * row.stop_pct / 100 * entry
    end = int(row.entry_time) + 300
    bars = [b for b in _bars() if b.start < end]
    return row, bars, entry, stop


async def test_compute_fills_the_lab_readings_and_the_book():
    row, bars, entry, stop = _a_signal()
    out = _recorder(FakeDelta()).compute(symbol=SYM, bars=bars, bar_open=int(row.entry_time),
                                         side=int(row.side), entry=entry, stop=stop)
    assert out["errors"] == []
    lab = out["lab"]
    assert set(lab) == set(SF.FEATURES)
    for k in ("wpr", "st_dist_atr", "ret_24h", "st_1h_agrees", "st_4h_agrees", "funding_side",
              "oi_chg_6h", "btc_ret_24h", "btc_st_1h_agrees", "days_listed", "run_4h_atr"):
        assert math.isfinite(lab[k]), k
    assert lab["tag_meme"] == 1.0
    # same bars, same definitions: the readings that do not depend on how
    # much history is held equal the lab's
    for k in ("wpr", "ret_1h", "ret_24h", "stop_pct", "funding_side", "oi_chg_1h",
              "btc_ret_1h", "btc_ret_24h", "hour_ist", "weekday", "with_trade_share_1h"):
        assert lab[k] == pytest.approx(float(row[k]), rel=1e-6, abs=1e-9), k
    assert len(out["pre_24h"]) == 288 and out["pre_24h"][-1] == pytest.approx(0.0, abs=1e-6)
    assert out["live"]["spread_bps"] == pytest.approx(1.6, rel=1e-3)
    assert out["live"]["bid_usd_0p5pct"] == pytest.approx(100 * 1.2499)


async def test_a_dead_venue_costs_readings_not_an_exception():
    row, bars, entry, stop = _a_signal()
    out = _recorder(FakeDelta(fail=True)).compute(
        symbol=SYM, bars=bars, bar_open=int(row.entry_time), side=int(row.side),
        entry=entry, stop=stop)
    assert out["errors"] and math.isnan(out["lab"]["btc_ret_1h"])
    assert math.isfinite(out["lab"]["wpr"])          # the bars are local
    assert math.isnan(out["live"]["spread_bps"])


async def test_a_slow_venue_is_cut_off_and_still_recorded():
    class Slow(SignalRecorder):
        def compute(self, **kw):
            time.sleep(1.0)
            return {}
    repo = InMemoryRepository()
    rec = Slow(STRATEGY, client=FakeDelta(), timeout=0.1)
    t0 = time.monotonic()
    got = await rec.record(repo, key="k", symbol=SYM, bars=[], bar_open=0, side=1,
                           entry=1.0, stop=0.9, outcome="APPROVED", reason=None,
                           limit_name=None)
    assert time.monotonic() - t0 < 0.9
    assert got["errors"] and "Timeout" in got["errors"][0]
    assert got["skip"] == "taken"


async def test_skip_reasons():
    assert skip_reason("APPROVED", None, None) == "taken"
    assert skip_reason("REJECTED", "already took 2 trades today, limit is 2",
                       "max_trades_per_day") == "daily_cap"
    assert skip_reason("REJECTED", "max_open_positions 1 already reserved (...)", None) == "open_position"
    assert skip_reason("REJECTED", "already holding an open position in X", None) == "open_position"
    assert skip_reason("REJECTED", "X paused after a loss: 10s elapsed of 14400s",
                       "pause_symbol_after_loss_seconds") == "coin_pause"
    assert skip_reason("REJECTED", "position rounds to zero contracts: ...",
                       "min_contract_size") == "zero_contracts"
    assert skip_reason("REJECTED", "broker: X: spread too wide", None) == "venue_check"
    assert skip_reason("SUPPRESSED", "market state is HALTED", None) == "halt"
    assert skip_reason("REJECTED", "something new", None) == "other"


# --- in the bot ----------------------------------------------------------------

WARM = 3 * 1440
DRIVE = 2 * 1440


def _bot(store, recorder):
    class Backfill:
        async def warm_up(self, symbol, days, now=None):
            return _bars(0, WARM)

        async def fetch(self, *a, **k):
            return []

        async def fill_gap(self, *a, **k):
            return []

    settings = Settings(symbols=(SYM,), risk=RiskConfig(
        starting_equity=10_000.0, max_open_positions=1, max_trades_per_day=2))
    return TradingBot(settings, InMemoryRepository(store), COSTS, strategy=STRATEGY,
                      backfiller=Backfill(), feed=DeadFeed(), signal_recorder=recorder)


async def _drive(bot):
    assert await bot.start(), bot.recovery_error
    for b in _bars(WARM, WARM + DRIVE):
        upd = CandleUpdate(SYM, b.start, b.open, b.high, b.low, b.close, b.volume,
                           (b.start + 59) * US)
        for closed in bot.builder.ingest(upd):
            await bot.on_closed_1m(closed)
    if bot.signal_recorder is not None:
        await asyncio.wait(list(bot.signal_recorder._tasks) or [asyncio.sleep(0)], timeout=60)


def _decisions(store):
    return [(k, r.outcome, r.direction, r.rejection_reason, r.entry_price)
            for k, r in sorted(store["signals"].items(), key=lambda kv: kv[1].bar_open)]


async def test_every_signal_with_a_direction_gets_readings():
    store = {}
    bot = _bot(store, _recorder(FakeDelta()))
    await _drive(bot)
    directed = [k for k, r in store["signals"].items() if r.direction is not None]
    assert len(directed) >= 3, "the synthetic market produced too few signals"
    feats = store["signal_features"]
    assert set(feats) == set(directed), "readings only, and always, for signals with a direction"
    skips = {feats[k]["skip"] for k in directed}
    assert "taken" in skips and len(skips) > 1, skips
    for k in directed:
        f = feats[k]
        assert f["errors"] == [] and math.isfinite(f["lab"]["wpr"])
        assert f["final_outcome"] in ("APPROVED", "REJECTED", "SUPPRESSED")


async def test_a_broken_recorder_changes_no_decision():
    """Same bars, one bot recording through a venue that fails every call and
    whose compute is slow, one bot recording nothing: identical decisions."""
    class Broken(SignalRecorder):
        def compute(self, **kw):
            raise RuntimeError("boom")

    a, b = {}, {}
    await _drive(_bot(a, Broken(STRATEGY, client=FakeDelta(fail=True))))
    await _drive(_bot(b, None))
    assert _decisions(a) == _decisions(b)
    assert [(o.signal_key, o.quantity, o.status) for o in a["orders"].values()] == \
        [(o.signal_key, o.quantity, o.status) for o in b["orders"].values()]
    assert a["signal_features"] and all(f["errors"] for f in a["signal_features"].values())
    assert not b["signal_features"]
