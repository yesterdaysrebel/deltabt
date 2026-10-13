"""Labelling recorded signals (as the lab did) and the weekly model grade."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from app.persistence.models import SignalRecord
from app.persistence.repository import InMemoryRepository
from deltabt import rulecore
from deltabt import signal_features as SF
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.strategy import resample_complete
from live import learning
from tests import signal_lab_fixture as fx

SYM = fx.SYMBOL
COIN = fx.coin(7, 1.25)
COSTS = SymbolCosts.from_spec(fx.PRODUCT)
SPEC = build_spec("manual_scalp_both_t3_st", 5, 1)
FAR = int(COIN["ltp"].time.iloc[-1]) + 30 * 86400


def _rows(df, lo, hi):
    d = df[(df.time >= lo) & (df.time <= hi)]
    return [{"time": int(r.time), "open": r.open, "high": r.high, "low": r.low,
             "close": r.close, "volume": r.volume} for r in d.itertuples()]


class FakeDelta:
    def __init__(self, empty=False):
        self.empty, self.calls = empty, []

    def candles(self, symbol, resolution, start, end):
        self.calls.append((symbol, resolution, start, end))
        if self.empty:
            return []
        if symbol == SYM:
            return _rows(COIN["ltp"], start, end)
        if symbol == "MARK:" + SYM:
            return _rows(COIN["mark"], start, end)
        if symbol == "FUNDING:" + SYM:
            return _rows(COIN["funding"], start, end)
        raise AssertionError(symbol)


def _record(repo, bar_open, side, entry, stop, outcome="REJECTED"):
    key = f"k{bar_open}"
    rec = SignalRecord(
        idempotency_key=key, instance_uid="i", symbol=SYM, bar_open=int(bar_open),
        primary_timeframe="5m", confirmation_timeframe="1m", direction=int(side),
        outcome=outcome, strategy_version="manual_scalp_both_t3_st", strategy_config_hash="h",
        conditions_passed=[], conditions_failed=[], indicators={}, entry_price=entry,
        stop_price=stop, target_price=entry + side * 3 * abs(entry - stop))
    asyncio.run(repo.record_signal(rec))
    return key


def _lab_signals():
    """The golden trades with the RAW stop rulecore produced (as the bot records it)."""
    l = COIN["ltp"]
    P, C = resample_complete(l, 5), l
    sig = rulecore.compute(P, C, SPEC)
    t = P["time"].to_numpy("int64")
    gold = pd.read_parquet(fx.GOLDEN)
    out = []
    for _, g in gold.iterrows():
        i = int(np.searchsorted(t, int(g.entry_time)))
        stop = float(sig.stop_long[i] if g.side > 0 else sig.stop_short[i])
        out.append((int(g.entry_time), int(g.side), float(P.close.iloc[i]), stop, g))
    return out


def test_labels_reproduce_the_lab_on_the_same_market():
    repo = InMemoryRepository()
    sigs = _lab_signals()
    for bo, side, entry, stop, _ in sigs:
        _record(repo, bo, side, entry, stop)
    got = asyncio.run(learning.label_signals(repo, FakeDelta(), lambda s: COSTS, now=FAR))
    assert got["labelled"] == len(sigs) and got["errors"] == 0, got
    gp = pd.read_parquet(fx.GOLDEN_PATHS).set_index("entry_time")
    outs = repo.store["signal_outcomes"]
    for bo, side, entry, stop, g in sigs:
        o = outs[f"k{bo}"]
        assert o.r_net == pytest.approx(float(g.R), rel=1e-9, abs=1e-9), (bo, o.r_net, g.R)
        assert o.exit_reason == g.exit_reason and o.win == bool(g.win)
        p = gp.loc[bo]
        assert o.bars_held == p.bars_held and o.mfe_r == pytest.approx(p.mfe_R, rel=1e-9)
        assert o.mae_r == pytest.approx(p.mae_R, rel=1e-9)
        np.testing.assert_allclose(o.fav, p.fav, rtol=1e-6)


def test_idempotent_and_waits_for_unresolved_signals():
    repo = InMemoryRepository()
    bo, side, entry, stop, g = _lab_signals()[5]
    _record(repo, bo, side, entry, stop)
    exit_in = int(pd.read_parquet(fx.GOLDEN_PATHS).set_index("entry_time").loc[bo].bars_held)
    early = bo + 300 + max(exit_in - 3, 1) * 300        # before its exit bar closes
    got = asyncio.run(learning.label_signals(repo, FakeDelta(), lambda s: COSTS, now=early))
    assert got["labelled"] == 0 and got["waiting"] == 1 and not repo.store["signal_outcomes"]
    got = asyncio.run(learning.label_signals(repo, FakeDelta(), lambda s: COSTS, now=FAR))
    assert got["labelled"] == 1
    first = repo.store["signal_outcomes"][f"k{bo}"]
    got = asyncio.run(learning.label_signals(repo, FakeDelta(), lambda s: COSTS, now=FAR))
    assert got["candidates"] == 0 and repo.store["signal_outcomes"][f"k{bo}"] is first


def test_a_signal_with_no_data_ever_is_closed_off_not_refetched_forever():
    repo = InMemoryRepository()
    bo, side, entry, stop, _ = _lab_signals()[0]
    _record(repo, bo, side, entry, stop)
    got = asyncio.run(learning.label_signals(repo, FakeDelta(empty=True), lambda s: COSTS,
                                             now=bo + 3600))
    assert got["waiting"] == 1
    got = asyncio.run(learning.label_signals(repo, FakeDelta(empty=True), lambda s: COSTS,
                                             now=FAR))
    assert got["unlabellable"] == 1
    o = repo.store["signal_outcomes"][f"k{bo}"]
    assert o.r_net is None and o.exit_reason.startswith("unlabellable")
    assert asyncio.run(repo.signals_to_label()) == []


# --- grading -------------------------------------------------------------------

MON = int(pd.Timestamp("2026-11-02", tz="UTC").value // 10**9)     # a Monday


def test_last_complete_week():
    wed = MON + 2 * 86400 + 5 * 3600
    assert learning.last_complete_week(wed) == MON - 7 * 86400
    assert learning.last_complete_week(MON) == MON - 7 * 86400
    assert learning.last_complete_week(MON - 1) == MON - 14 * 86400


def _synthetic(n, t0, t1, seed, informative=True):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({k: rng.normal(size=n) for k in SF.FEATURES})
    df["symbol"] = "X"
    df["entry_time"] = rng.integers(t0, t1, n)
    df["side"] = rng.choice([-1.0, 1.0], n)
    p = 1 / (1 + np.exp(-3 * df["wpr"])) if informative else np.full(n, 0.3)
    df["win"] = (rng.random(n) < p).astype(int)
    df["R"] = np.where(df.win == 1, 3.0, -1.0)
    df["exit_reason"] = np.where(df.win == 1, "target", "stop")
    return df


def _live_rows(df):
    return [{"symbol": r.symbol, "bar_open": int(r.entry_time), "direction": int(r.side),
             "r_net": float(r.R), "win": bool(r.win), "exit_reason": r.exit_reason,
             "features": {"lab": {k: float(getattr(r, k)) for k in SF.FEATURES}}}
            for r in df.itertuples()]


def test_grade_pools_history_with_live_and_tests_only_the_unseen_week():
    hist = _synthetic(2000, MON - 200 * 86400, MON - 30 * 86400, 1)
    live = _synthetic(400, MON - 30 * 86400, MON + 7 * 86400, 2)
    rec = learning.grade(hist, learning.live_frame(_live_rows(live)), MON)
    in_week = live[(live.entry_time >= MON) & (live.entry_time < MON + 7 * 86400)]
    assert rec.n_test == len(in_week)
    purged = live.entry_time < MON - 72 * 3600
    assert rec.n_train == len(hist) + purged.sum() and rec.n_train_live == purged.sum()
    assert rec.auc > 0.75 and rec.random_p < 0.05 and rec.top_mean_r > rec.all_mean_r
    assert "did better than chance" in rec.summary and "does not filter trades" in rec.summary


def test_grade_without_history_or_signals_says_so():
    rec = learning.grade(None, learning.live_frame([]), MON)
    assert rec.n_test == 0 and rec.auc is None and "not graded" in rec.summary
    noise = _synthetic(600, MON - 60 * 86400, MON + 7 * 86400, 3, informative=False)
    rec = learning.grade(None, learning.live_frame(_live_rows(noise)), MON)
    assert rec.n_test > 0 and rec.top_mean_r is not None


def test_grade_model_writes_one_row_per_week(tmp_path):
    repo = InMemoryRepository()
    hist = _synthetic(800, MON - 100 * 86400, MON + 7 * 86400, 4)
    path = tmp_path / "signals.parquet"
    hist.to_parquet(path)
    rec = asyncio.run(learning.grade_model(repo, history_path=path, now=MON + 8 * 86400))
    assert rec.week_start == MON and rec.n_test > 0
    again = asyncio.run(learning.grade_model(repo, history_path=path, now=MON + 9 * 86400))
    assert len(repo.store["model_grades"]) == 1
    assert asyncio.run(repo.latest_model_grade())["auc"] == again.auc
    # a missing history file is tolerated
    asyncio.run(learning.grade_model(repo, history_path=tmp_path / "nope.parquet",
                                     now=MON + 8 * 86400))


def test_cli_dispatch_reaches_the_learning_commands():
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for cmd in ("label-signals", "grade-model"):
        proc = subprocess.run([sys.executable, "-m", "live", cmd, "--help"], cwd=root,
                              capture_output=True, text=True, timeout=60,
                              env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(root), "HOME": "/tmp"})
        assert proc.returncode == 0 and cmd in proc.stdout, proc.stderr
