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
             "takeable": True,
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
    # History rows inside the graded week are never scored (2026-10-15): with
    # no live signals that week there is nothing to grade, but the row exists.
    assert rec.week_start == MON and rec.n_test == 0 and rec.n_train > 0
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


def test_one_fetch_per_coin_gives_the_same_labels_as_one_per_signal(monkeypatch):
    """2026-10-13: live records ~2 signals per coin per hour, so the labeller
    fetches each coin's window once per run and slices it. The labels must be
    exactly what a fetch per signal gives."""
    sigs = _lab_signals()[:12]

    def labels(per_signal):
        repo = InMemoryRepository()
        for bo, side, entry, stop, _ in sigs:
            _record(repo, bo, side, entry, stop)
        fake = FakeDelta()
        if per_signal:
            monkeypatch.setattr(learning, "_slice", lambda frames, s, e: learning._fetch(fake, SYM, s, e))
        asyncio.run(learning.label_signals(repo, fake, lambda s: COSTS, now=FAR))
        monkeypatch.undo()
        rows = sorted(asyncio.run(repo.labelled_signals()), key=lambda r: r["bar_open"])
        return [(r["bar_open"], r["r_net"], r["exit_reason"]) for r in rows], fake.calls

    cached, cached_calls = labels(per_signal=False)
    single, _ = labels(per_signal=True)
    assert cached == single and len(cached) == len(sigs)
    assert len([c for c in cached_calls if c[0] == SYM]) == 1   # one LTP window for the coin


# --- switch-on rule and the second target (2026-10-15) ---------------------

def _grade_week(n, predictive, seed, base=1792000000, wrong_way=False):
    rng = np.random.default_rng(seed)
    win = rng.random(n) < 0.3
    R = np.where(win, 2.9, -1.05)
    reach = (win | (rng.random(n) < 0.3)).astype(int)
    noise = rng.random(n)
    sign = -1.0 if wrong_way else 1.0
    score = noise + sign * (1.5 * win if predictive else 0.0)
    s1 = noise + sign * (1.5 * reach if predictive else 0.0)
    return {"detail": {
        "scored": [[base + i, "X", float(score[i]), float(R[i])] for i in range(n)],
        "scored_1r": [[base + i, "X", float(s1[i]), int(reach[i])] for i in range(n)]}}


def test_switch_rule_needs_four_qualifying_weeks():
    chk = learning.switch_check([_grade_week(80, True, s) for s in range(3)])
    assert chk["3R"] == {"weeks": 3, "needs_weeks": 4, "eligible": False}
    small = [_grade_week(20, True, s) for s in range(6)]          # < 30 signals each
    assert learning.switch_check(small)["3R"]["weeks"] == 0


def test_switch_rule_passes_a_real_signal_and_refuses_noise():
    good = learning.switch_check([_grade_week(80, True, s) for s in range(4)])
    assert good["3R"]["eligible"] and good["3R"]["p"] <= 0.05 and good["3R"]["top"] > 0
    assert good["1R"]["eligible"] and good["1R"]["top"] > learning.REACH_1R_BREAK_EVEN
    # a model that ranks the LOSERS first must never pass (random scores would
    # pass 5% of the time by construction, which is the rule's false-alarm rate)
    wrong = learning.switch_check([_grade_week(80, True, s, wrong_way=True) for s in range(4)])
    assert not wrong["3R"]["eligible"] and not wrong["1R"]["eligible"]
    assert "Switch-on rule:" in learning.switch_line(wrong)


def test_outcome_columns_are_never_model_inputs():
    from deltabt import signal_model as SM
    df = pd.DataFrame({"symbol": ["A"], "entry_time": [1], "R": [1.0], "win": [1],
                       "exit_reason": ["target"], "reach_1r": [1.0], "mfe_R": [1.2], "wpr": [-50.0]})
    assert list(SM.features(df).columns) == ["wpr"]


def test_the_graded_week_scores_live_signals_only():
    """The history file runs to 2026-10-12, inside the first graded week."""
    from deltabt import signal_features as SF
    rng = np.random.default_rng(0)
    ws = 1792000000

    def frame(n, t0, live):
        d = {"symbol": "X", "entry_time": t0 + np.arange(n) * 600, "side": 1.0,
             "R": rng.choice([2.9, -1.05], n), "exit_reason": "stop",
             "reach_1r": rng.integers(0, 2, n).astype(float)}
        for k in SF.FEATURES:
            d[k] = rng.random(n)
        f = pd.DataFrame(d)
        f["win"] = (f.R > 0).astype(int)
        return f
    hist = pd.concat([frame(400, ws - 30 * 86400, 0), frame(25, ws + 3600, 0)])   # 25 inside the week
    live = frame(40, ws + 7200, 1)
    rec = learning.grade(hist, live, ws)
    assert rec.n_test == 40 and len(rec.detail["scored"]) == 40
    assert len(rec.detail["scored_1r"]) == 40


# --- amendment 2026-10-16: record-only coins and the trade-universe subset ----

def _with_flag(week, trade_flags):
    """A graded week whose rows carry the 5th element (trade-universe flag)."""
    d = week["detail"]
    return {"detail": {k: [r + [int(f)] for r, f in zip(d[k], trade_flags)]
                       for k in ("scored", "scored_1r")}}


def test_amendment_eligible_needs_all_signals_and_the_trade_universe_subset():
    # predictive on every coin: both sets pass
    good = [_with_flag(_grade_week(120, True, s), [i % 2 for i in range(120)]) for s in range(4)]
    chk = learning.switch_check(good)
    assert chk["3R"]["eligible"] and chk["trade_universe"]["3R"]["eligible"]
    assert chk["trade_universe"]["3R"]["weeks"] == 4
    # predictive on the record-only coins, backwards on the trade coins: all
    # signals pass, the trade-universe subset fails -> NOT eligible
    mixed = []
    for s in range(4):
        rec_only = _grade_week(80, True, s)
        trade = _grade_week(40, True, 100 + s, base=1792100000, wrong_way=True)
        mixed.append({"detail": {k: [r + [0] for r in rec_only["detail"][k]]
                                 + [r + [1] for r in trade["detail"][k]]
                                 for k in ("scored", "scored_1r")}})
    chk = learning.switch_check(mixed)
    assert chk["3R"]["p"] <= 0.05 and chk["3R"]["top"] > 0        # all signals would pass
    assert not chk["trade_universe"]["3R"]["eligible"]
    assert not chk["3R"]["eligible"] and not chk["1R"]["eligible"]
    line = learning.switch_line(chk)
    assert "all signals" in line and "trade coins only" in line and "ELIGIBLE" not in line


def test_amendment_subset_weeks_qualify_on_their_own_count():
    # 100 signals a week but only 20 on trade coins: the subset never qualifies
    weeks = [_with_flag(_grade_week(100, True, s), [1] * 20 + [0] * 80) for s in range(5)]
    chk = learning.switch_check(weeks)
    assert chk["trade_universe"]["3R"] == {"weeks": 0, "needs_weeks": 4, "eligible": False}
    assert not chk["3R"]["eligible"]
    assert "trade coins only 0 of 4 qualifying weeks so far" in learning.switch_line(chk)


def test_rows_graded_before_the_amendment_count_as_trade_universe():
    old = [_grade_week(80, True, s) for s in range(4)]           # 4-element rows
    chk = learning.switch_check(old)
    assert chk["trade_universe"]["3R"]["weeks"] == 4 and chk["3R"]["eligible"]


def test_the_grade_flags_each_scored_signal_and_never_learns_from_the_flag():
    rng = np.random.default_rng(1)
    ws = 1792000000

    def rows(n, t0, record_only, reason=None, features=True):
        out = []
        for i in range(n):
            lab = {k: float(rng.random()) for k in SF.FEATURES}
            r = float(rng.choice([2.9, -1.05]))
            out.append({"symbol": "R" if record_only else "T", "bar_open": t0 + i * 600,
                        "direction": 1, "r_net": r, "win": r > 0, "exit_reason": "stop",
                        "mfe_r": float(rng.choice([0.4, 1.2])), "takeable": True,
                        "rejection_reason": reason,
                        "features": ({"lab": lab, "record_only": record_only}
                                     if features else None)})
        return out
    live_rows = (rows(300, ws - 30 * 86400, False) + rows(30, ws + 60, False)
                 + rows(20, ws + 120, True)
                 + rows(5, ws + 180, True, reason="record_only: R is watched", features=False))
    frame = learning.live_frame(live_rows)
    assert frame["_trade"].tolist().count(0) == 25          # readings, or the refusal, say so
    rec = learning.grade(None, frame, ws)
    sc = rec.detail["scored"]
    assert len(sc) == 55 and all(len(r) == 5 for r in sc)
    assert sum(r[4] for r in sc) == 30 and rec.detail["test_trade_universe"] == 30
    assert {r[1] for r in sc if r[4] == 0} == {"R"}
    assert all(len(r) == 5 for r in rec.detail["scored_1r"])
    # the flag is not a model input: flipping it changes no score
    flipped = learning.grade(None, frame.assign(_trade=1 - frame["_trade"]), ws)
    assert [r[2] for r in flipped.detail["scored"]] == [r[2] for r in sc]
