"""The seven options-specific look-ahead risks from §10, as executable checks.

Options research leaks in ways perpetual research does not: an expiry can be
chosen because it turned out to be liquid, "ATM" can be defined with a spot
price that had not printed yet, a delta bucket can be formed from a greek
computed after the fact, and a smile fit can borrow a later quote. Each of
those has a test here.

None of these tests reads a return.
"""

from __future__ import annotations

import glob
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import options_surface as osf  # noqa: E402

from tests._live_data import require_live_data  # noqa: E402


@pytest.fixture(scope="module")
def quotes():
    require_live_data("data/quotes")
    fs = sorted(glob.glob(str(ROOT / "data" / "quotes" / "*.parquet")))
    return osf.enrich(pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True))


# ------------------------------------------------------------------- parsing

def test_symbol_parsing_is_self_describing():
    p = osf.parse_symbol("C-BTC-84000-030425")
    assert p["right"] == "C" and p["strike"] == 84000.0
    assert p["expiry"] == pd.Timestamp("2025-04-03 12:00", tz="UTC")
    assert osf.parse_symbol("BTCUSD") is None


def test_every_quoted_symbol_parses(quotes):
    """If any did not, the dropped rows would be a silent survivorship filter."""
    assert quotes["strike"].notna().all() and quotes["expiry"].notna().all()


def test_catalog_join_would_have_dropped_a_quarter_of_rows(quotes):
    """Why the name is parsed instead of joined. Recorded, not worked around."""
    cat = pd.read_parquet(ROOT / "data" / "meta" / "options_catalog.parquet")
    matched = quotes["symbol"].isin(set(cat["symbol"]))
    assert matched.mean() < 0.80, "catalog is no longer stale; revisit this note"


# ------------------------------------------------------- snapshot isolation

def test_surface_at_contains_exactly_one_timestamp(quotes):
    ts = sorted(quotes["ts"].unique())[100]
    s = osf.surface_at(quotes, ts)
    assert len(s) > 0 and s["ts"].nunique() == 1 and s["ts"].iloc[0] == ts


def test_surface_at_is_unchanged_when_the_future_is_deleted(quotes):
    """THE core assertion. Truncate everything after t; the surface must not move."""
    stamps = sorted(quotes["ts"].unique())
    ts = stamps[200]
    full = osf.surface_point(quotes, ts, "BTC", 5, 45)
    truncated = osf.surface_point(quotes[quotes["ts"] <= ts], ts, "BTC", 5, 45)
    assert full is not None and truncated is not None
    assert full == truncated, "the surface at t changed when future rows were removed"


def test_expiry_selection_ignores_future_liquidity(quotes):
    """Deleting all later rows must not change which expiry is chosen."""
    stamps = sorted(quotes["ts"].unique())
    changed = 0
    for ts in stamps[100:160:7]:
        snap = osf.two_sided(osf.surface_at(quotes, ts))
        snap = snap[snap["underlying"] == "BTC"]
        a = osf.select_expiry(snap, 5, 45)
        b = osf.select_expiry(osf.two_sided(
            osf.surface_at(quotes[quotes["ts"] <= ts], ts)).query("underlying == 'BTC'"),
            5, 45)
        changed += int(a != b)
    assert changed == 0


def test_expiry_rule_is_a_declared_window_not_a_search(quotes):
    """The rule takes a tenor band and returns the nearest expiry in it."""
    stamps = sorted(quotes["ts"].unique())
    snap = osf.two_sided(osf.surface_at(quotes, stamps[200]))
    snap = snap[snap["underlying"] == "BTC"]
    e = osf.select_expiry(snap, 5, 45)
    tte = (e - stamps[200]).total_seconds() / 86400
    assert 5 <= tte <= 45
    assert osf.select_expiry(snap, 9000, 10000) is None, (
        "an impossible tenor band must return None, not the closest thing")


# --------------------------------------------------------------- moneyness

def test_atm_uses_the_spot_printed_in_the_same_row(quotes):
    """`spot_price` is carried per row, so ATM cannot use a later print."""
    ts = sorted(quotes["ts"].unique())[200]
    snap = osf.surface_at(quotes, ts)
    assert snap["spot_price"].notna().all()
    later = quotes[quotes["ts"] > ts]["spot_price"].mean()
    assert not np.isclose(snap["spot_price"].mean(), later), (
        "the row spot equals a future average; check the recorder")


def test_delta_buckets_come_from_the_exchange_not_a_refit(quotes):
    """Greeks are published per row. Nothing here recomputes one."""
    ts = sorted(quotes["ts"].unique())[200]
    chain = osf.two_sided(osf.surface_at(quotes, ts))
    chain = chain[chain["underlying"] == "BTC"]
    exp = osf.select_expiry(chain, 5, 45)
    chain = chain[chain["expiry"] == exp]
    p25 = osf.nearest_delta(chain, "P", 0.25, 0.05)
    assert p25 is None or abs(abs(p25["delta"]) - 0.25) <= 0.05


def test_nearest_delta_refuses_rather_than_substitutes():
    """A 0.40-delta option must never be returned as 'the 25-delta'."""
    chain = pd.DataFrame({"right": ["P", "P"], "delta": [-0.40, -0.60],
                          "mark_iv": [0.5, 0.6]})
    assert osf.nearest_delta(chain, "P", 0.25, 0.05) is None
    assert osf.nearest_delta(chain, "P", 0.25, 0.20) is not None


def test_no_interpolation_happens_anywhere():
    """The EXECUTABLE code must contain no smile fit, spline or fill call.

    Docstrings are stripped first: the module explains at length that it does
    not interpolate, and a naive substring scan flags its own prose.
    """
    import ast
    tree = ast.parse((ROOT / "scripts" / "options_surface.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    getattr(body[0], "value", None), ast.Constant):
                body[0].value.value = ""
    src = ast.unparse(tree)
    for banned in ("interp", "spline", "curve_fit", "polyfit",
                   "ffill", "bfill", "fillna", "reindex"):
        assert banned not in src, f"{banned} found; the surface is being fitted"


# ---------------------------------------------------------------- bid/ask

def test_tradeability_is_decided_from_the_same_snapshot(quotes):
    """A row is two-sided or it is not, judged on its own bid and ask.

    The exclusion is asserted over the whole dataset, not one snapshot: any
    individual snapshot may legitimately be fully two-sided, and asserting
    otherwise tests the sample rather than the filter.
    """
    ts = sorted(quotes["ts"].unique())[200]
    ok_snap = osf.two_sided(osf.surface_at(quotes, ts))
    assert (ok_snap["best_bid"] > 0).all()
    assert (ok_snap["best_ask"] >= ok_snap["best_bid"]).all()
    # filtering a snapshot must equal filtering globally then selecting it
    assert set(ok_snap.index) == set(
        osf.two_sided(quotes).pipe(lambda d: d[d["ts"] == ts]).index), (
        "the filter depends on rows outside the snapshot")
    assert len(osf.two_sided(quotes)) < len(quotes), (
        "no row anywhere was excluded; the tradeability filter is inert")


def test_crossed_and_zero_bid_rows_are_excluded_not_repaired(quotes):
    crossed = quotes[(quotes["best_bid"] > quotes["best_ask"])]
    assert len(crossed) > 0, "expected some crossed markets in raw data"
    assert not len(osf.two_sided(crossed)), "a crossed quote survived the filter"


def test_mark_price_is_never_used_as_a_fill_price():
    src = (ROOT / "scripts" / "options_surface.py").read_text()
    assert "mark_price" not in src, (
        "mark_price appears in the construction path; it is a theoretical mark, "
        "not an executable price")


# ------------------------------------------------------- coverage reality

def test_a_surface_point_can_be_formed_at_most_snapshots(quotes):
    """If this fails the data cannot support ANY surface hypothesis."""
    stamps = sorted(quotes["ts"].unique())
    got = [osf.surface_point(quotes, t, "BTC", 5, 45) for t in stamps[::10]]
    rate = np.mean([g is not None for g in got])
    assert rate > 0.5, f"only {100*rate:.1f}% of snapshots yield a BTC surface point"
