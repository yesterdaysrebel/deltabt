"""The live entry point (one signal, closed bars) gives the lab's readings."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from deltabt import signal_features as SF
from deltabt.catalog import build_spec
from deltabt.strategy import resample_complete
from tests import signal_lab_fixture as fx

SPEC = build_spec("manual_scalp_both_t3_st", 5, 1)


def _frames():
    c = fx.coin(7, 1.25)
    b = fx.coin(11, 60_000.0)
    l = c["ltp"]
    return dict(p5=resample_complete(l, 5), h1=resample_complete(l, 60),
                h4=resample_complete(l, 240), btc_1m=b["ltp"],
                btc_60m=resample_complete(b["ltp"], 60), funding=c["funding"], oi=fx.oi())


def test_one_signal_at_a_time_matches_the_lab_rows():
    gold = pd.read_parquet(fx.GOLDEN)
    gp = pd.read_parquet(fx.GOLDEN_PATHS)
    fr = _frames()
    pk = fx.picks()
    picks = {int(r.pick_day): {"change": r.change, "rank": r["rank"], "turnover": r.turnover}
             for _, r in pk.iterrows()}
    for k, row in gold.iterrows():
        # the stop the lab used is recoverable from stop_pct; take it from the path instead
        entry = None
        p5 = fr["p5"]
        ii = int(np.searchsorted(p5.time.to_numpy("int64"), int(row.entry_time)))
        entry = float(p5.close.iloc[ii])
        stop = entry - row.side * row.stop_pct / 100 * entry
        feats, pre = SF.signal_features(
            bar_open=int(row.entry_time), side=int(row.side), entry_price=entry,
            stop_price=stop, spec=SPEC, picks=picks, launch=fx.META["launch"],
            tag=fx.META["tag"], **fr)
        assert "feature_errors" not in feats, feats.get("feature_errors")
        for col in SF.FEATURES:
            a, b = feats[col], float(row[col])
            assert (math.isnan(a) and math.isnan(b)) or a == pytest.approx(b, rel=1e-9, abs=1e-9), (k, col, a, b)
        np.testing.assert_allclose(pre, gp.pre_24h.iloc[k], rtol=1e-5, atol=1e-5)


def test_missing_inputs_give_nan_not_an_exception():
    fr = _frames()
    t = int(fr["p5"].time.iloc[-10])
    feats, pre = SF.signal_features(p5=fr["p5"], h1=None, h4=None, bar_open=t, side=1,
                                    entry_price=1.0, stop_price=0.98, spec=SPEC)
    assert math.isnan(feats["st_1h_agrees"]) and math.isnan(feats["btc_ret_1h"])
    assert math.isnan(feats["funding_side"]) and math.isnan(feats["day_rank"])
    assert not math.isnan(feats["wpr"]) and pre is not None
    # no bars at all, or a bar that is not there
    feats, pre = SF.signal_features(p5=None, h1=None, h4=None, bar_open=t, side=1,
                                    entry_price=1.0, stop_price=0.98, spec=SPEC)
    assert all(math.isnan(feats[k]) for k in SF.FEATURES) and feats["feature_errors"] and pre is None
    feats, _ = SF.signal_features(p5=fr["p5"], h1=None, h4=None, bar_open=t + 1, side=-1,
                                  entry_price=1.0, stop_price=1.02, spec=SPEC)
    assert all(math.isnan(feats[k]) for k in SF.FEATURES)


def test_book_readings():
    ticker = {"mark_price": "100.1", "close": 100.0, "funding_rate": "0.01", "oi": "5",
              "oi_change_usd_6h": "-300", "quotes": {"best_bid": "99.9", "best_ask": "100.1"}}
    book = {"buy": [{"price": "99.9", "size": 10}, {"price": "99.6", "size": 20},
                    {"price": "98.5", "size": 1000}],
            "sell": [{"price": "100.1", "size": 5}, {"price": "100.9", "size": 7},
                     {"price": "102", "size": 1000}]}
    r = SF.book_readings(ticker, book, contract_value=0.1)
    assert r["mark_last_gap_bps"] == pytest.approx(10.0)
    assert r["spread_bps"] == pytest.approx(20.0)
    assert r["bid_usd_0p5pct"] == pytest.approx(10 * 0.1 * 99.9 + 20 * 0.1 * 99.6)
    assert r["ask_usd_0p5pct"] == pytest.approx(5 * 0.1 * 100.1)
    assert r["ask_usd_1pct"] == pytest.approx(5 * 0.1 * 100.1 + 7 * 0.1 * 100.9)
    assert r["funding_rate"] == 0.01 and r["oi_change_usd_6h"] == -300
    empty = SF.book_readings(None, None, None)
    assert all(math.isnan(v) for v in empty.values())


def test_tags():
    assert SF.tag_of(["smart_contracts", "layer_2"]) == "layer"
    assert SF.tag_of(["defi", "new"]) == "defi"
    assert SF.tag_of(None) == "other"
