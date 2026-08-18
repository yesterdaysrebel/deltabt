"""H-EMA-2 invariant and leakage tests (S 24). These gate any result."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deltabt import indicators as ind
from deltabt.costs import SymbolCosts
from deltabt.research import hema2
from deltabt.research.stops import injection_arrays
from deltabt.strategy import resample_ohlcv

BTC = SymbolCosts(symbol="BTCUSD", tick_size=0.5, contract_value=0.001,
                  maker_fee=0.0002, taker_fee=0.0005, max_leverage=200.0,
                  position_size_limit=125_000, funding_interval_seconds=28800,
                  slippage_bps=2.0)


def make_1m(n=90_000, seed=0, base=60_000.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    drift = np.where((np.arange(n) // 1500) % 2 == 0, 0.00005, -0.00004)
    r = drift + rng.standard_normal(n) * 0.0004
    c = base * np.exp(np.cumsum(r))
    w = np.abs(rng.standard_normal(n)) * 0.0005 * c
    return pd.DataFrame({
        "time": np.arange(n, dtype="int64") * 60,
        "open": np.concatenate(([base], c[:-1])),
        "high": c + w, "low": c - w, "close": c,
        "volume": rng.random(n) * 100 + 10,
    })


def make_sym(df: pd.DataFrame) -> dict:
    h = df["high"].to_numpy("float64"); l = df["low"].to_numpy("float64")
    return dict(df=df, t1=df["time"].to_numpy("int64"),
                o=df["open"].to_numpy("float64"), h=h, l=l,
                c=df["close"].to_numpy("float64"), mh=h, ml=l,
                funding=pd.DataFrame(), costs=BTC,
                tradable=np.ones(len(df), dtype=np.bool_))


@pytest.fixture(scope="module")
def df():
    return make_1m()


@pytest.fixture(scope="module")
def sym(df):
    return make_sym(df)


# --- indicator correctness --------------------------------------------------


class TestEMA:
    def test_matches_an_independent_recursion(self):
        x = np.arange(1, 61, dtype="float64")
        got = hema2.ema(x, 10)
        a = 2.0 / 11.0
        want = np.full(60, np.nan)
        want[9] = x[:10].mean()
        for t in range(10, 60):
            want[t] = a * x[t] + (1 - a) * want[t - 1]
        assert np.allclose(got[9:], want[9:])

    def test_seed_is_the_sma_not_the_first_value(self):
        x = np.arange(1, 21, dtype="float64")
        assert hema2.ema(x, 5)[4] == pytest.approx(3.0)

    def test_nothing_before_the_seed_index(self):
        e = hema2.ema(np.arange(1, 21, dtype="float64"), 5)
        assert np.all(np.isnan(e[:4]))

    def test_matches_pandas_ewm_seeded_identically(self):
        x = pd.Series(np.random.default_rng(3).standard_normal(200).cumsum() + 100)
        got = hema2.ema(x.to_numpy(), 20)
        ref = x.iloc[19:].ewm(span=20, adjust=False).mean()
        ref.iloc[0] = x.iloc[:20].mean()
        ref = x.iloc[19:].copy()
        prev = x.iloc[:20].mean()
        a = 2 / 21
        vals = [prev]
        for v in x.iloc[20:]:
            prev = a * v + (1 - a) * prev
            vals.append(prev)
        assert np.allclose(got[19:], vals)

    def test_is_causal_under_truncation(self):
        x = np.random.default_rng(5).standard_normal(500).cumsum() + 100
        full = hema2.ema(x, 20)
        for cut in (100, 250, 400):
            assert np.allclose(hema2.ema(x[:cut], 20), full[:cut], equal_nan=True)


def test_atr_and_supertrend_are_the_frozen_implementations(df):
    F = hema2.build_tf(df, 15)
    d = resample_ohlcv(df, 15)
    h, l, c = (d[k].to_numpy("float64") for k in ("high", "low", "close"))
    assert np.allclose(F["atr"], ind.atr(h, l, c, 14), equal_nan=True)
    st, dirn = ind.supertrend(h, l, c, hema2.ST_MULT, hema2.ST_PERIOD)
    assert np.allclose(F["supertrend"], st, equal_nan=True)


# --- time alignment ---------------------------------------------------------


@pytest.mark.parametrize("tf", [5, 15, 60])
def test_resampled_bars_are_utc_aligned(df, tf):
    t = hema2.build_tf(df, tf)["time"]
    assert np.all(t % (tf * 60) == 0)


@pytest.mark.parametrize("tf", [5, 15, 60])
def test_entry_is_at_or_after_the_tf_close(df, sym, tf):
    F = hema2.build_tf(df, tf)
    e = hema2.entry_index(F["time"], tf, sym["t1"])
    ok = e > 0
    assert np.all(sym["t1"][e[ok]] >= F["time"][ok] + tf * 60)


def test_regime_never_reads_a_forming_candle(df):
    F = hema2.build_tf(df, 15)
    R = hema2.build_tf(df, 240)
    vals = R["close"]
    got = hema2.project_regime(R["time"], 240, vals, F["time"], 15)
    fin = np.isfinite(got)
    idx = np.searchsorted(R["time"], got[fin], sorter=np.argsort(vals))
    # every projected value must belong to a regime bar CLOSED by the exec close
    for i in np.flatnonzero(fin)[::37]:
        src = np.flatnonzero(vals == got[i])[0]
        assert R["time"][src] + 240 * 60 <= F["time"][i] + 15 * 60


def test_signal_is_written_one_bar_before_the_entry(df, sym):
    F = hema2.build_tf(df, 15)
    X = hema2.crossover_events(F["close"], 9, 21)
    lo, sh = hema2.mech_signals(F, X, "M1", {})
    lo1, sh1, sl1, ss1 = hema2.project(lo, sh, F, sym["t1"], len(sym["t1"]))
    e = hema2.entry_index(F["time"], 15, sym["t1"])
    fired = np.flatnonzero(lo1 | sh1)
    assert fired.size > 0
    assert set((fired + 1).tolist()) <= set(e[e > 0].tolist())


# --- mechanisms -------------------------------------------------------------


def test_crossover_is_an_event_not_a_state(df):
    F = hema2.build_tf(df, 15)
    X = hema2.crossover_events(F["close"], 9, 21)
    above = X["ema_fast"] > X["ema_slow"]
    assert X["x_long"].sum() < above.sum() / 5
    assert not np.any(X["x_long"] & X["x_short"])


def test_pullback_never_fires_on_the_crossover_bar(df):
    F = hema2.build_tf(df, 15)
    X = hema2.crossover_events(F["close"], 9, 21)
    fl, fs = hema2._pullback(X["x_long"], X["x_short"], F["close"],
                             X["ema_fast"], F["atr"], 0.5, 10)
    assert not np.any(fl & X["x_long"])
    assert not np.any(fs & X["x_short"])


def test_pullback_setup_expires(df):
    n = 200
    xl = np.zeros(n, bool); xs = np.zeros(n, bool)
    xl[10] = True
    close = np.full(n, 100.0)
    close[11:] = 101.0          # never near the EMA, never resumes
    efast = np.full(n, 50.0)
    atr = np.full(n, 1.0)
    fl, fs = hema2._pullback(xl, xs, close, efast, atr, 0.5, 10)
    assert not fl.any() and not fs.any()


def test_m5_only_fires_with_the_regime(df):
    F = hema2.build_tf(df, 15)
    R = hema2.build_tf(df, 240)
    rf = hema2.project_regime(R["time"], 240, hema2.ema(R["close"], 20), F["time"], 15)
    rs = hema2.project_regime(R["time"], 240, hema2.ema(R["close"], 50), F["time"], 15)
    bull, bear = rf > rs, rf < rs
    X = hema2.crossover_events(F["close"], 9, 21)
    lo, sh = hema2.mech_signals(F, X, "M5", {}, regime=(bull, bear))
    assert np.all(bull[lo]) and np.all(bear[sh])
    assert lo.sum() <= X["x_long"].sum()


# --- stops ------------------------------------------------------------------


def test_stop_is_the_frozen_structural_composition(df):
    """stop_long/short must be exactly min/max(supertrend, leg extreme)."""
    from deltabt.research.hwpr import _leg_extreme
    F = hema2.build_tf(df, 15)
    d = resample_ohlcv(df, 15)
    h, l = d["high"].to_numpy("float64"), d["low"].to_numpy("float64")
    leg_lo, leg_hi = _leg_extreme(h, l, F["direction"])
    assert np.allclose(F["stop_long"], np.minimum(F["supertrend"], leg_lo),
                       equal_nan=True)
    assert np.allclose(F["stop_short"], np.maximum(F["supertrend"], leg_hi),
                       equal_nan=True)
    ok = hema2.valid_stop_mask(F)
    assert np.all(F["stop_long"][ok] < F["stop_short"][ok])
    assert np.all(F["stop_long"][ok] <= F["supertrend"][ok])
    assert np.all(F["stop_short"][ok] >= F["supertrend"][ok])


def test_injected_stops_survive_the_frozen_contract(df, sym):
    F = hema2.build_tf(df, 15)
    X = hema2.crossover_events(F["close"], 9, 21)
    lo, sh = hema2.mech_signals(F, X, "M1", {})
    lo1, sh1, sl1, ss1 = hema2.project(lo, sh, F, sym["t1"], len(sym["t1"]))
    injection_arrays(lo1, sh1, sl1, ss1)      # must not raise


def test_five_percent_cap_is_applied_and_counted(df, sym):
    F = hema2.build_tf(df, 60)
    X = hema2.crossover_events(F["close"], 9, 21)
    lo, sh = hema2.mech_signals(F, X, "M1", {})
    lo1, sh1, sl1, ss1 = hema2.project(lo, sh, F, sym["t1"], len(sym["t1"]))
    r = hema2.simulate(sym, lo1, sh1, sl1, ss1,
                       window=(0, int(sym["t1"][-1]) + 1), label="t")
    f = r.to_frame()
    if len(f):
        assert (f.stop_pct <= hema2.MAX_STOP_PCT + 1e-12).all()
    assert r.signals >= len(f) + r.skipped_stop - 1


# --- simulator contract -----------------------------------------------------


def test_entry_is_the_next_1m_bar_open(df, sym):
    F = hema2.build_tf(df, 15)
    X = hema2.crossover_events(F["close"], 9, 21)
    lo, sh = hema2.mech_signals(F, X, "M1", {})
    lo1, sh1, sl1, ss1 = hema2.project(lo, sh, F, sym["t1"], len(sym["t1"]))
    f = hema2.simulate(sym, lo1, sh1, sl1, ss1,
                       window=(0, int(sym["t1"][-1]) + 1), label="t").to_frame()
    if f.empty:
        pytest.skip("no trades on the synthetic series")
    assert (f.entry_time - f.signal_time == 60).all()
    idx = np.searchsorted(sym["t1"], f.entry_time.to_numpy("int64"))
    assert np.allclose(f.entry_price.to_numpy(), sym["o"][idx])
    t = f.sort_values("entry_time")
    assert (t.entry_time.to_numpy()[1:] > t.exit_time.to_numpy()[:-1]).all()


def test_r_multiple_geometry_is_exact(df, sym):
    F = hema2.build_tf(df, 15)
    X = hema2.crossover_events(F["close"], 9, 21)
    lo, sh = hema2.mech_signals(F, X, "M1", {})
    lo1, sh1, sl1, ss1 = hema2.project(lo, sh, F, sym["t1"], len(sym["t1"]))
    f = hema2.simulate(sym, lo1, sh1, sl1, ss1,
                       window=(0, int(sym["t1"][-1]) + 1), label="t").to_frame()
    if f.empty:
        pytest.skip("no trades")
    tg = f[f.exit_reason == "target"]
    st = f[f.exit_reason == "stop"]
    if len(tg):
        assert np.allclose(tg.r_gross, 2.0, atol=1e-9)
    if len(st):
        assert np.allclose(st.r_gross, -1.0, atol=1e-9)


def test_future_bars_cannot_change_earlier_trades(df, sym):
    F = hema2.build_tf(df, 15)
    X = hema2.crossover_events(F["close"], 9, 21)
    lo, sh = hema2.mech_signals(F, X, "M1", {})
    lo1, sh1, sl1, ss1 = hema2.project(lo, sh, F, sym["t1"], len(sym["t1"]))
    a = hema2.simulate(sym, lo1, sh1, sl1, ss1,
                       window=(0, int(sym["t1"][-1]) + 1), label="t").to_frame()
    if a.empty:
        pytest.skip("no trades")
    cut = int(a.entry_time.quantile(0.6))
    d2 = df.copy()
    m = d2.time >= cut
    for col in ("open", "high", "low", "close"):
        d2.loc[m, col] *= 1.35
    s2 = make_sym(d2)
    F2 = hema2.build_tf(d2, 15)
    X2 = hema2.crossover_events(F2["close"], 9, 21)
    lo2, sh2 = hema2.mech_signals(F2, X2, "M1", {})
    p2 = hema2.project(lo2, sh2, F2, s2["t1"], len(s2["t1"]))
    b = hema2.simulate(s2, *p2, window=(0, int(s2["t1"][-1]) + 1), label="t").to_frame()
    ea = a[a.exit_time < cut - 3600]
    eb = b[b.exit_time < cut - 3600]
    assert len(ea) > 0
    assert ea.entry_time.tolist() == eb.entry_time.tolist()
    assert np.allclose(ea.r_gross.to_numpy(), eb.r_gross.to_numpy())


# --- controls ---------------------------------------------------------------


def _arm(df, sym, tf=5, pair=(9, 21)):
    F = hema2.build_tf(df, tf)
    X = hema2.crossover_events(F["close"], *pair)
    lo, sh = hema2.mech_signals(F, X, "M1", {})
    lo1, sh1, sl1, ss1 = hema2.project(lo, sh, F, sym["t1"], len(sym["t1"]))
    win = (0, int(sym["t1"][-1]) + 1)
    f = hema2.simulate(sym, lo1, sh1, sl1, ss1, window=win, label="arm").to_frame()
    return F, (lo1, sh1, sl1, ss1), f, win


def test_ca_preserves_entry_bars_and_randomises_direction(df, sym):
    F, (lo1, sh1, sl1, ss1), f, win = _arm(df, sym)
    a_lo, a_sh, _, _ = hema2.control_ca(lo1, sh1, sl1, ss1, seed=11)
    assert np.array_equal(a_lo | a_sh, lo1 | sh1)
    assert not np.array_equal(a_lo, lo1)


def test_cb_matches_the_arm_stop_width_distribution(df, sym):
    F, _, f, win = _arm(df, sym)
    if len(f) < 50:
        pytest.skip("too few arm trades")
    lo1, sh1, sl1, ss1, meta = hema2.control_cb(
        f.stop_pct.to_numpy(), F, sym, win, warmup=80, seed=11)
    g = hema2.simulate(sym, lo1, sh1, sl1, ss1, window=win, label="cb").to_frame()
    if len(g) < 20:
        pytest.skip("too few control trades")
    am, cm = np.median(f.stop_pct), np.median(g.stop_pct)
    assert abs(cm - am) / am < 0.5, (am, cm)
    assert meta["requested"] >= meta["drawn"]


def test_cb_direction_is_not_the_arm_direction(df, sym):
    F, _, f, win = _arm(df, sym)
    if len(f) < 50:
        pytest.skip("too few arm trades")
    lo1, sh1, _, _, _ = hema2.control_cb(
        f.stop_pct.to_numpy(), F, sym, win, warmup=80, seed=11)
    assert lo1.sum() > 0 and sh1.sum() > 0


def test_control_seeds_produce_different_draws(df, sym):
    F, _, f, win = _arm(df, sym)
    if len(f) < 50:
        pytest.skip("too few arm trades")
    a = hema2.control_cb(f.stop_pct.to_numpy(), F, sym, win, 80, seed=11)[0]
    b = hema2.control_cb(f.stop_pct.to_numpy(), F, sym, win, 80, seed=23)[0]
    assert not np.array_equal(a, b)


def test_control_seed_is_deterministic(df, sym):
    F, _, f, win = _arm(df, sym)
    if len(f) < 50:
        pytest.skip("too few arm trades")
    a = hema2.control_cb(f.stop_pct.to_numpy(), F, sym, win, 80, seed=37)[0]
    b = hema2.control_cb(f.stop_pct.to_numpy(), F, sym, win, 80, seed=37)[0]
    assert np.array_equal(a, b)


def test_eligible_population_is_causal_and_capped(df, sym):
    F = hema2.build_tf(df, 15)
    win = (0, int(sym["t1"][-1]) + 1)
    bars, dirs, pcts = hema2.eligible_population(F, sym, win, warmup=80)
    assert bars.size > 0
    assert np.all(pcts > 0) and np.all(pcts <= hema2.MAX_STOP_PCT)
    assert np.all(bars >= 80)
    assert set(np.unique(dirs).tolist()) <= {-1, 1}


# --- journal / accounting (S 18) --------------------------------------------

from deltabt.research import hema2_journal as J  # noqa: E402


def _armed(df, sym, tf=5, pair=(9, 21), warmup=80):
    F = hema2.build_tf(df, tf)
    X = hema2.crossover_events(F["close"], *pair)
    raw_lo, raw_sh = hema2.mech_signals(F, X, "M1", {})
    lo, sh = raw_lo.copy(), raw_sh.copy()
    lo[:warmup] = False
    sh[:warmup] = False
    lo1, sh1, sl1, ss1 = hema2.project(lo, sh, F, sym["t1"], len(sym["t1"]))
    win = (0, int(sym["t1"][-1]) + 1)
    res = hema2.simulate(sym, lo1, sh1, sl1, ss1, window=win, label="M1|5m|9/21")
    frame = res.to_frame()
    fn = J.funnel(raw_lo, raw_sh, F, sym, win, warmup, res, len(frame))
    return F, res, frame, fn, win


def test_funnel_accounts_for_every_signal(df, sym):
    _, res, frame, fn, _ = _armed(df, sym)
    assert fn["setups_detected"] > 0
    assert fn["eligible_setups"] == res.signals
    assert fn["funnel_residual"] >= 0
    assert (fn["eligible_setups"]
            == fn["skipped_stop"] + fn["skipped_size"]
            + fn["rejected_position_open"] + fn["trades_entered"])


def test_trades_never_exceed_eligible_setups(df, sym):
    _, _, frame, fn, _ = _armed(df, sym)
    assert fn["trades_entered"] <= fn["eligible_setups"]


def test_reconciliation_of_r_totals(df, sym):
    _, _, frame, fn, _ = _armed(df, sym)
    assert not frame.empty
    r = J.reconcile(frame, fn)
    assert r["ok"], r["checks"]


def test_skipped_stop_trades_are_not_in_the_journal(df, sym):
    _, res, frame, fn, _ = _armed(df, sym)
    j = J.to_journal(frame, dict(exec_tf=5, mechanism="M1", arm_id="M1|5m|9/21"))
    assert len(j) == fn["trades_entered"]
    assert res.skipped_stop >= 0
    assert (j.stop_distance_pct <= hema2.MAX_STOP_PCT * 100 + 1e-9).all()


def test_journal_costs_are_signed_as_drags(df, sym):
    _, _, frame, fn, _ = _armed(df, sym)
    j = J.to_journal(frame, dict(exec_tf=5, mechanism="M1", arm_id="M1|5m|9/21"))
    assert (j.fee_R <= 0).all() and (j.slippage_R <= 0).all()
    assert np.allclose(j.net_R, j.gross_R + j.fee_R + j.slippage_R + j.funding_R,
                       atol=1e-9)


def test_economics_matches_the_frame(df, sym):
    _, _, frame, fn, _ = _armed(df, sym)
    e = J.economics(frame)
    assert e["trades"] == len(frame)
    assert e["wins"] + e["losses"] == len(frame)
    assert e["longs"] + e["shorts"] == len(frame)
    assert e["net_expectancy"] == pytest.approx(frame.r_net.mean())
    assert e["gross_expectancy"] == pytest.approx(frame.r_gross.mean())


def test_representative_selection_is_deterministic(df, sym):
    _, _, frame, fn, _ = _armed(df, sym)
    j = J.to_journal(frame, dict(exec_tf=5, mechanism="M1", arm_id="M1|5m|9/21"))
    a, b = J.representative(j), J.representative(j)
    assert a["best"].net_R.tolist() == b["best"].net_R.tolist()
    assert a["best"].net_R.iloc[0] >= a["worst"].net_R.iloc[0]


def test_control_journal_cannot_leak_into_arm_results(df, sym):
    """The control writes its own frames; the arm frame must be untouched."""
    F, res, frame, fn, win = _armed(df, sym)
    before = frame.r_net.sum()
    lo1, sh1, sl1, ss1, meta = hema2.control_cb(
        frame.stop_pct.to_numpy(), F, sym, win, warmup=80, seed=11)
    c = hema2.simulate(sym, lo1, sh1, sl1, ss1, window=win, label="cb").to_frame()
    assert frame.r_net.sum() == before
    if not c.empty:
        assert set(c.arm.unique()) == {"cb"}
        assert "cb" not in set(frame.arm.unique())


def test_format_trade_uses_real_values(df, sym):
    _, _, frame, fn, _ = _armed(df, sym)
    j = J.to_journal(frame, dict(exec_tf=5, mechanism="M1", arm_id="M1|5m|9/21"))
    txt = J.format_trade(j.iloc[0])
    r = j.iloc[0]
    assert f"{r.net_R:+.3f}R" in txt
    assert str(r.exit_reason).upper() in txt
    assert "|" in txt.splitlines()[0]


def test_funnel_block_renders(df, sym):
    _, _, _, fn, _ = _armed(df, sym)
    txt = J.funnel_block(fn)
    assert "Setups detected" in txt and "Trades entered" in txt


# --- regression tests for the reviewed defects (D4, D5, D6) -----------------


def test_cb_pool_excludes_stops_outside_the_arms_realised_range(df, sym):
    """D4: an open bottom bin let the control draw stops far tighter than the
    arm's minimum, which dominated its MEAN cost/R while leaving the median
    untouched and inverted the primary metric's sign."""
    F, _, f, win = _arm(df, sym)
    if len(f) < 50:
        pytest.skip("too few arm trades")
    sp = f.stop_pct.to_numpy()
    lo1, sh1, sl1, ss1, meta = hema2.control_cb(sp, F, sym, win, warmup=80, seed=11)
    g = hema2.simulate(sym, lo1, sh1, sl1, ss1, window=win, label="cb").to_frame()
    if g.empty:
        pytest.skip("no control trades")
    assert g.stop_pct.min() >= sp.min() - 1e-12, "control drew a stop tighter than the arm ever traded"
    assert g.stop_pct.max() <= sp.max() + 1e-12
    assert "pool_out_of_range" in meta


def test_cb_mean_cost_tracks_the_arm_not_just_the_median(df, sym):
    """The median matched even when the mean was 70% adrift; assert the mean."""
    F, _, f, win = _arm(df, sym)
    if len(f) < 50:
        pytest.skip("too few arm trades")
    lo1, sh1, sl1, ss1, _ = hema2.control_cb(f.stop_pct.to_numpy(), F, sym, win, 80, 11)
    g = hema2.simulate(sym, lo1, sh1, sl1, ss1, window=win, label="cb").to_frame()
    if len(g) < 20:
        pytest.skip("too few control trades")
    assert g.cost_r.mean() / f.cost_r.mean() < 1.35, (g.cost_r.mean(), f.cost_r.mean())


def test_cb_direction_is_an_independent_coin(df, sym):
    """D5: direction must not be inherited from the width-selected pair."""
    F, _, f, win = _arm(df, sym)
    if len(f) < 50:
        pytest.skip("too few arm trades")
    longs = 0, 0
    tot_l = tot_n = 0
    for seed in (11, 23, 37, 53, 71):
        lo1, sh1, _, _, _ = hema2.control_cb(f.stop_pct.to_numpy(), F, sym, win, 80, seed)
        tot_l += int(lo1.sum()); tot_n += int(lo1.sum() + sh1.sum())
    assert tot_n > 0
    assert abs(tot_l / tot_n - 0.5) < 0.08, f"P(long)={tot_l/tot_n:.3f}"


def test_exit_walk_can_be_truncated_at_the_split_boundary(df, sym):
    """D6: trades otherwise resolve on data belonging to the next segment.

    The cut is placed deliberately INSIDE an open trade, because a fixture where
    everything happens to resolve early would let a broken implementation pass.
    """
    F = hema2.build_tf(df, 15)
    X = hema2.crossover_events(F["close"], 9, 21)
    lo, sh = hema2.mech_signals(F, X, "M1", {})
    p = hema2.project(lo, sh, F, sym["t1"], len(sym["t1"]))
    full_win = (0, int(sym["t1"][-1]) + 1)
    full = hema2.simulate(sym, *p, window=full_win, label="t").to_frame()
    if full.empty:
        pytest.skip("no trades")
    longest = full.loc[full.bars_held.idxmax()]
    cut = int(longest.entry_time) + 60          # one minute into that position
    assert cut < int(longest.exit_time), "cut must land inside the open trade"
    win = (0, cut)

    free = hema2.simulate(sym, *p, window=win, label="t").to_frame()
    trunc = hema2.simulate(sym, *p, window=win, label="t",
                           truncate_at_window=True).to_frame()
    assert not trunc.empty
    # untruncated, the walk resolves the open trade using post-cut data
    assert int((free.exit_time > cut).sum()) >= 1
    # truncated, no trade may resolve past the boundary, and the open one
    # must surface as unresolved rather than silently borrowing future bars
    assert trunc.exit_time.max() <= cut
    assert int((trunc.exit_reason == "end").sum()) >= 1
