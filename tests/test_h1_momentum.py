"""The eight leakage assertions §16 requires, plus the wiring checks.

Nothing here reads a performance result. If any of it fails the kill test
stops before a single return is computed.

The one prediction worth stating in advance: 7d momentum is the exact negative
of the factor panel's `reversal_7d`, which measured a gross of +21.46%/yr on
the pooled universe. A 7d momentum gross near -21%/yr is therefore not a
finding, it is a wiring check against a number already on disk.
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import h1_momentum as hm  # noqa: E402

from tests._live_data import require_live_data  # noqa: E402


@pytest.fixture(scope="module")
def panel():
    require_live_data("data/meta/products.json", "data/candles")
    return hm.load_panel()


# ------------------------------------------------------------- signal timing

def test_momentum_at_t_uses_no_price_from_t_onward():
    """Prices explode from day 20. The signal read on day 20 must not see it."""
    idx = pd.date_range("2026-01-01", periods=40, freq="D", tz="UTC")
    v = np.concatenate([np.full(20, 100.0), np.full(20, 1e6)])
    px = pd.DataFrame({"A": v}, index=idx)
    sig = hm.momentum(px, 7)
    assert sig["A"].iloc[20] == pytest.approx(0.0), (
        "the day-20 signal saw the day-20 explosion; momentum is not shifted")
    assert sig["A"].iloc[21] > 100, "the shift is off by more than one day"


def test_momentum_spans_exactly_the_formation_window():
    idx = pd.date_range("2026-01-01", periods=20, freq="D", tz="UTC")
    px = pd.DataFrame({"A": np.arange(1.0, 21.0)}, index=idx)
    sig = hm.momentum(px, 5)
    # at row 10 the value is close(9)/close(4) - 1 = 10/5 - 1
    assert sig["A"].iloc[10] == pytest.approx(10 / 5 - 1)


def test_short_history_gives_no_signal_rather_than_a_guess():
    idx = pd.date_range("2026-01-01", periods=10, freq="D", tz="UTC")
    px = pd.DataFrame({"A": np.arange(1.0, 11.0)}, index=idx)
    assert hm.momentum(px, 30)["A"].isna().all()


# ---------------------------------------------------------- rebalance timing

def test_weights_at_t_cannot_use_returns_after_t():
    """A symbol that rockets AFTER the rebalance must not be selected at it."""
    idx = pd.date_range("2026-01-01", periods=70, freq="D", tz="UTC")
    flat = np.full(70, 100.0)
    late = flat.copy(); late[35:] = 1e4          # explodes after day 35
    early = flat.copy(); early[:35] = np.linspace(50, 100, 35)  # rose before
    px = pd.DataFrame({"LATE": late, "EARLY": early,
                       "F1": flat, "F2": flat, "F3": flat}, index=idx)
    sig = hm.momentum(px, 30)
    row = sig.loc[idx[35]].dropna()
    assert row.idxmax() == "EARLY", (
        "the rebalance ranked on a move that had not happened yet")
    assert row["LATE"] == pytest.approx(0.0)


# -------------------------------------------------------------------- liquidity

def test_liquidity_screen_cannot_use_future_volume(panel):
    raw = {}
    import json
    require_live_data("data/meta/products.json")
    prod = json.loads((ROOT / "data" / "meta" / "products.json").read_text())
    import os
    for s in panel.symbols:
        d = pd.read_parquet(ROOT / "data" / "candles" / s / "ltp_1d.parquet")
        d.index = pd.to_datetime(d["time"], unit="s", utc=True)
        raw[s] = hm.notional_usd(d["close"], d["volume"],
                                 prod[s]["contract_value"])
    R = pd.DataFrame(raw).reindex(panel.px.index)
    i = 400
    day = panel.px.index[i]
    manual = R.iloc[i - 30:i].median()
    got = panel.turnover_med.loc[day]
    both = manual.notna() & got.notna()
    assert np.allclose(manual[both], got[both]), "screen is not causal"
    contaminated = R.iloc[i - 29:i + 1].median()
    assert (contaminated[both] - got[both]).abs().sum() > 0, (
        "the screen is indistinguishable from one that includes day t")


def test_notional_uses_contract_value_not_bare_close_times_volume():
    close, vol = pd.Series([100_000.0]), pd.Series([1_000.0])
    assert hm.notional_usd(close, vol, 0.001).iloc[0] == pytest.approx(1e5)
    assert float(close.iloc[0] * vol.iloc[0]) == pytest.approx(1e8)
    assert hm.notional_usd(pd.Series([0.5]), vol, 1000.0).iloc[0] == pytest.approx(5e5)


def test_real_universe_spans_four_orders_of_contract_value():
    import json
    require_live_data("data/meta/products.json")
    prod = json.loads((ROOT / "data" / "meta" / "products.json").read_text())
    cv = {p["contract_value"] for p in prod.values()}
    assert min(cv) <= 0.001 and max(cv) >= 1000


# ---------------------------------------------------------------------- listing

def test_no_symbol_is_eligible_before_its_launch_time(panel):
    import json
    require_live_data("data/meta/products.json")
    prod = json.loads((ROOT / "data" / "meta" / "products.json").read_text())
    bad = []
    for s in panel.symbols:
        launch = pd.Timestamp(prod[s]["launch_time"]).tz_convert("UTC")
        if bool(panel.listed[s][panel.px.index < launch].any()):
            bad.append(s)
    assert not bad, f"listed before launch_time: {bad[:5]}"


def test_no_daily_bar_precedes_its_products_launch_time(panel):
    import json
    require_live_data("data/meta/products.json")
    prod = json.loads((ROOT / "data" / "meta" / "products.json").read_text())
    bad = [s for s in panel.symbols
           if panel.px[s].dropna().index.min()
           < pd.Timestamp(prod[s]["launch_time"]).tz_convert("UTC").floor("D")]
    assert not bad, f"data precedes listing: {bad[:5]}"


# ----------------------------------------------------------------- missing data

def test_missing_prices_are_not_forward_filled(panel):
    """A gap must stay a gap. Filling one invents a zero return."""
    holes = panel.px.isna().sum().sum()
    assert holes > 0, "expected unlisted-period NaNs in a ragged panel"
    assert panel.px.ffill().isna().sum().sum() < holes, (
        "the panel is already forward-filled")
    assert panel.ret.isna().sum().sum() > 0


def test_universe_is_not_filtered_on_final_sample_liquidity(panel):
    """Eligibility must vary over time, not be one fixed survivor list."""
    score = hm.momentum(panel.px, 30)
    sets = [frozenset(hm.eligible(panel, d, score, 1e6).index)
            for d in panel.px.index[::60] if d > panel.px.index[60]]
    sets = [s for s in sets if s]
    assert len(set(sets)) > 1, "the eligible set never changes; it is a survivor list"


# --------------------------------------------------------------------- costs

def test_cost_is_charged_only_on_rebalance_days(panel):
    r = hm.backtest(panel, 30, 1e6)
    d = r["daily"]
    assert (d["cost"] > 0).sum() == len(r["sizing"].query("n_side > 0")) - \
        int(d["cost"].iloc[0] == 0) or (d["cost"] > 0).sum() > 0
    nonzero = d.index[d["cost"] > 0]
    stride = np.diff([panel.px.index.get_loc(x) for x in nonzero])
    assert set(np.unique(stride)) <= {hm.REBALANCE}, (
        "costs are being charged off the rebalance grid")


def test_cost_uses_per_symbol_taker_fee(panel):
    fees = panel.leg_cost
    assert fees.nunique() >= 2, "per-symbol fees collapsed to a single rate"
    assert fees.max() == pytest.approx(0.0005 * 1.18 + 0.0002)
    assert fees.min() == pytest.approx(0.0001 * 1.18 + 0.0002)


def test_zero_cost_is_not_silently_assumed(panel):
    r = hm.backtest(panel, 30, 1e6)
    assert r["daily"]["cost"].sum() > 0


# ---------------------------------------------------------------- construction

def test_quintiles_are_disjoint_and_dollar_neutral():
    sig = pd.Series(np.arange(20.0), index=[f"S{i}" for i in range(20)])
    w = hm._weights(sig, "real", np.random.default_rng(0))
    assert len(w) == 8 and w.index.is_unique
    assert w.sum() == pytest.approx(0.0)
    assert w.abs().sum() == pytest.approx(1.0)
    assert w[w > 0].index.tolist() == ["S19", "S18", "S17", "S16"]


def test_reverse_control_is_the_exact_negative():
    sig = pd.Series(np.arange(20.0), index=[f"S{i}" for i in range(20)])
    rng = np.random.default_rng(0)
    a = hm._weights(sig, "real", rng).sort_index()
    b = hm._weights(sig, "reverse", rng).sort_index()
    assert np.allclose(a.reindex(b.index).fillna(0.0), -b.fillna(0.0))


def test_long_only_control_is_fully_long():
    sig = pd.Series(np.arange(20.0), index=[f"S{i}" for i in range(20)])
    w = hm._weights(sig, "long_only", np.random.default_rng(0))
    assert (w > 0).all() and w.sum() == pytest.approx(1.0)


def test_tsmom_control_is_a_sign_bet_not_a_ranking():
    sig = pd.Series([-3.0, -1.0, 2.0, 5.0], index=list("ABCD"))
    w = hm._weights(sig, "tsmom", np.random.default_rng(0))
    assert w.abs().sum() == pytest.approx(1.0)
    assert (np.sign(w) == np.sign(sig)).all(), "tsmom is not following its own sign"


def test_newey_west_widens_the_error_under_autocorrelation():
    rng = np.random.default_rng(0)
    e = rng.normal(size=4000)
    ar = np.zeros(4000)
    for i in range(1, 4000):
        ar[i] = 0.85 * ar[i - 1] + e[i]
    ar += 0.05
    iid = ar.mean() / (ar.std(ddof=1) / np.sqrt(len(ar)))
    assert abs(hm.newey_west_t(ar, lag=60)) < abs(iid)
