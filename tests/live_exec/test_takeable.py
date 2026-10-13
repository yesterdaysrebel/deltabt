"""Which recorded signals the signal lab would have had as rows (`takeable`).

The bot records every signal edge (about two an hour per coin on the live
universe); the lab's history holds only the TAKEABLE ones -- each coin run on
its own through run_portfolio, one position at a time, no daily cap. Live
labels pooled with that history must be the same kind of row, or one move is
counted many times. These tests pin the rule and its parity with the lab.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from app.persistence.models import SignalOutcomeRecord, SignalRecord
from app.persistence.repository import InMemoryRepository
from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.data.quality import tradable_mask
from deltabt.harness import _resampled
from live import learning
from tests import signal_lab_fixture as fx
from tests.live_exec.test_learning import COIN, COSTS, FAR, SYM, FakeDelta, _synthetic

SPEC = build_spec("manual_scalp_both_t3_st", 5, 1)
T0 = 1_790_000_100 // 300 * 300


def _signal(repo, key, sym, bar_open, side=1, entry=100.0, stop=98.0, atr=None):
    rec = SignalRecord(
        idempotency_key=key, instance_uid="i", symbol=sym, bar_open=int(bar_open),
        primary_timeframe="5m", confirmation_timeframe="1m", direction=int(side),
        outcome="REJECTED", strategy_version="manual_scalp_both_t3_st",
        strategy_config_hash="h", conditions_passed=[], conditions_failed=[],
        indicators={} if atr is None else {"primary": {"atr": float(atr)}},
        entry_price=entry, stop_price=stop, target_price=entry + side * 3 * abs(entry - stop))
    assert asyncio.run(repo.record_signal(rec))


def _outcome(repo, key, sym, bar_open, *, exit_after=None, r=1.0, tradable=True):
    """An outcome as the labeller writes it: exit `exit_after` seconds after
    the signal bar (None = unlabellable), free 10 bars after the exit bar."""
    sid = repo.store["signal_ids"][key]
    if exit_after is None:
        rec = SignalOutcomeRecord(
            signal_id=sid, idempotency_key=key, symbol=sym, side=1, entry_time=bar_open,
            entry_price=100.0, stop_price=98.0, r_net=None, win=None,
            exit_reason="unlabellable: the engine refused the entry", exit_time=None,
            exit_price=None, mfe_r=None, mae_r=None, bars_to_mfe=None, bars_held=None)
    else:
        ex = bar_open + exit_after
        rec = SignalOutcomeRecord(
            signal_id=sid, idempotency_key=key, symbol=sym, side=1, entry_time=bar_open,
            entry_price=100.0, stop_price=98.0, r_net=r, win=r > 0, exit_reason="stop",
            exit_time=ex, exit_price=98.0, mfe_r=0.1, mae_r=-1.0, bars_to_mfe=1,
            bars_held=exit_after // 300,
            detail={"rest_tradable": tradable, "cooldown_bars": 10,
                    "free_from": ex + 10 * 300, "free_from_exact": True})
    assert asyncio.run(repo.record_signal_outcome(rec))


def _takeable(repo):
    return {k: o.takeable for k, o in repo.store["signal_outcomes"].items()}


# --- the rule on a synthetic sequence -------------------------------------------

def test_overlapping_signals_on_one_coin_only_the_first_is_takeable_until_it_exits():
    repo = InMemoryRepository()
    h = 3600
    # coin A: a0 opens and exits after 2h (free 50 min later); a1, a2 inside
    # it; a3 inside the cooldown; a4 after it
    plan = [("a0", 0, 2 * h), ("a1", 600, h), ("a2", h, 4 * h),
            ("a3", 2 * h + 1200, h), ("a4", 2 * h + 3000, h)]
    for key, dt, ex in plan:
        _signal(repo, key, "AUSD", T0 + dt)
        _outcome(repo, key, "AUSD", T0 + dt, exit_after=ex)
    # coin B: a signal while A is open is unaffected by A
    _signal(repo, "b0", "BUSD", T0 + 600)
    _outcome(repo, "b0", "BUSD", T0 + 600, exit_after=h)
    got = asyncio.run(learning.mark_takeable(repo))
    assert _takeable(repo) == {"a0": True, "a1": False, "a2": False, "a3": False,
                               "a4": True, "b0": True}
    assert got == {"takeable": 3, "not_takeable": 3, "takeable_waiting": 0}
    # idempotent: nothing is decided twice
    assert asyncio.run(learning.mark_takeable(repo)) == {
        "takeable": 0, "not_takeable": 0, "takeable_waiting": 0}


def test_a_signal_waits_while_an_earlier_one_on_its_coin_is_unlabelled():
    repo = InMemoryRepository()
    _signal(repo, "a0", "AUSD", T0)                      # still running: no outcome
    _signal(repo, "a1", "AUSD", T0 + 6 * 3600)
    _outcome(repo, "a1", "AUSD", T0 + 6 * 3600, exit_after=3600)
    _signal(repo, "b0", "BUSD", T0 + 600)
    _outcome(repo, "b0", "BUSD", T0 + 600, exit_after=3600)
    got = asyncio.run(learning.mark_takeable(repo))
    assert _takeable(repo) == {"a1": None, "b0": True}
    assert got["takeable_waiting"] == 1
    # a0 resolves after a1's bar: a1 is not takeable. b1 falls inside b0's
    # trade, which was decided on the earlier run and is read back as stored.
    _outcome(repo, "a0", "AUSD", T0, exit_after=7 * 3600)
    _signal(repo, "b1", "BUSD", T0 + 1200)
    _outcome(repo, "b1", "BUSD", T0 + 1200, exit_after=3600)
    asyncio.run(learning.mark_takeable(repo))
    assert _takeable(repo) == {"a0": True, "a1": False, "b0": True, "b1": False}


def test_refused_and_untradable_signals_are_not_takeable_and_block_nothing():
    repo = InMemoryRepository()
    _signal(repo, "a0", "AUSD", T0)
    _outcome(repo, "a0", "AUSD", T0, exit_after=None)        # engine refused it
    _signal(repo, "a1", "AUSD", T0 + 300)
    _outcome(repo, "a1", "AUSD", T0 + 300, exit_after=3600, tradable=False)
    _signal(repo, "a2", "AUSD", T0 + 600)
    _outcome(repo, "a2", "AUSD", T0 + 600, exit_after=3600)
    asyncio.run(learning.mark_takeable(repo))
    assert _takeable(repo) == {"a0": False, "a1": False, "a2": True}


def test_an_exit_bar_frees_the_coin_after_the_cooldown_exactly():
    repo = InMemoryRepository()
    _signal(repo, "a0", "AUSD", T0)
    _outcome(repo, "a0", "AUSD", T0, exit_after=3600)
    free = T0 + 3600 + 10 * 300
    _signal(repo, "a1", "AUSD", free - 300)
    _outcome(repo, "a1", "AUSD", free - 300, exit_after=3600)
    _signal(repo, "a2", "AUSD", free)
    _outcome(repo, "a2", "AUSD", free, exit_after=3600)
    asyncio.run(learning.mark_takeable(repo))
    assert _takeable(repo) == {"a0": True, "a1": False, "a2": True}


# --- parity with the lab ----------------------------------------------------------

def _every_edge():
    """Every signal edge the bot would record on the golden market: the lab's
    _coin inputs (rulecore on 5m + 1m, the picked days, past warm-up) BEFORE
    run_portfolio chose which to take."""
    l = COIN["ltp"]
    d = dict(symbol=SYM, ltp=l, mark=COIN["mark"], funding=COIN["funding"],
             tradable=tradable_mask(l))
    cache = {}
    P = _resampled(d, 5, cache)[0]
    sig = rulecore.compute(P, _resampled(d, 1, cache)[0], SPEC)
    t = P["time"].to_numpy("int64")
    close_day = ((t + 5 * 60 - 1) // fx.DAY) * fx.DAY
    allow = np.isin(close_day, fx.picks().pick_day.to_numpy("int64"))
    fire = (sig.long_entry | sig.short_entry) & allow
    fire[:SPEC.warmup_bars] = False
    out = []
    for i in np.flatnonzero(fire):
        side = 1 if sig.long_entry[i] else -1
        stop = float(sig.stop_long[i] if side > 0 else sig.stop_short[i])
        out.append((int(t[i]), side, float(P.close.iloc[i]), stop, float(sig.atr[i])))
    return out


def test_the_takeable_set_is_the_labs_own_run_on_the_golden_market():
    edges = _every_edge()
    gold = pd.read_parquet(fx.GOLDEN)
    assert len(edges) > 5 * len(gold), "the market must re-fire while a trade is open"
    repo = InMemoryRepository()
    for bo, side, entry, stop, atr in edges:
        _signal(repo, f"k{bo}", SYM, bo, side=side, entry=entry, stop=stop, atr=atr)
    got = asyncio.run(learning.label_signals(repo, FakeDelta(), lambda s: COSTS, now=FAR,
                                             limit=10_000))
    assert got["errors"] == 0 and got["takeable_waiting"] == 0, got
    outs = repo.store["signal_outcomes"]
    assert len(outs) == len(edges) and all(o.takeable is not None for o in outs.values())
    took = sorted(o.entry_time for o in outs.values() if o.takeable)
    assert took == sorted(gold.entry_time.astype("int64").tolist())
    g = gold.set_index("entry_time")
    for o in outs.values():
        if o.takeable:
            assert o.r_net == pytest.approx(float(g.loc[o.entry_time].R), rel=1e-9, abs=1e-9)
            assert o.side == int(g.loc[o.entry_time].side)


# --- the grade uses takeable signals only -------------------------------------------

MON = int(pd.Timestamp("2026-11-02", tz="UTC").value // 10**9)


def test_grade_model_trains_and_tests_on_takeable_signals_only():
    repo = InMemoryRepository()
    df = _synthetic(600, MON - 60 * 86400, MON + 7 * 86400, 5)
    took = np.random.default_rng(1).random(len(df)) < 0.4
    for k, r in enumerate(df.itertuples()):
        key = f"s{k}"
        _signal(repo, key, "XUSD", int(r.entry_time), side=int(r.side))
        asyncio.run(repo.record_signal_features(
            key, {"lab": {f: float(getattr(r, f)) for f in learning.SF.FEATURES}}))
        rec = SignalOutcomeRecord(
            signal_id=repo.store["signal_ids"][key], idempotency_key=key, symbol="XUSD",
            side=int(r.side), entry_time=int(r.entry_time), entry_price=100.0, stop_price=98.0,
            r_net=float(r.R), win=bool(r.win), exit_reason=r.exit_reason,
            exit_time=int(r.entry_time) + 3600, exit_price=100.0, mfe_r=0.0, mae_r=0.0,
            bars_to_mfe=0, bars_held=12, takeable=None if k % 50 == 0 else bool(took[k]))
        assert asyncio.run(repo.record_signal_outcome(rec))
    rec = asyncio.run(learning.grade_model(repo, history_path=None, now=MON + 8 * 86400))
    keep = took & (np.arange(len(df)) % 50 != 0)
    d = df[keep]
    assert rec.n_test == int(((d.entry_time >= MON) & (d.entry_time < MON + 7 * 86400)).sum())
    assert rec.n_train == int((d.entry_time < MON - 72 * 3600).sum()) == rec.n_train_live
    assert rec.detail["live_rows"] == int(keep.sum())
    assert rec.detail["live_labelled"] == len(df)
    assert rec.detail["live_undecided"] == int((np.arange(len(df)) % 50 == 0).sum())
    assert rec.detail["live_not_takeable"] == len(df) - int(keep.sum()) - rec.detail["live_undecided"]
