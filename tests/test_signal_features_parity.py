"""The signal lab's features are unchanged by moving them into deltabt/signal_features.py.

The golden files were written by the lab's ORIGINAL `_coin` (commit before the
refactor) on the synthetic market in tests/signal_lab_fixture.py. The live
recorder computes features with the same module the lab now calls, so live
rows and the historical rows can be pooled; this test is what makes "the same
definitions" a checked fact rather than a claim.

    REGENERATE (only if the lab's definitions are deliberately changed):
    DELTABT_WRITE_GOLDEN=1 .venv/bin/python -m pytest tests/test_signal_features_parity.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests import signal_lab_fixture as fx

ROOT = Path(__file__).resolve().parent.parent


def _lab():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("signal_features_lab_under_test",
                                                  ROOT / "scripts" / "signal_features_lab.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Catalog:
    def get(self, sym):
        return dict(fx.PRODUCT, symbol=sym)


def run_lab_coin(monkeypatch, tmp_path):
    lab = _lab()
    data = {fx.SYMBOL: fx.coin(7, 1.25), "BTCUSD": fx.coin(11, 60_000.0)}

    def load_symbol(sym):
        d = data.get(sym)
        if d is None:
            return None
        from deltabt.data.quality import tradable_mask
        return dict(symbol=sym, ltp=d["ltp"].copy(), mark=d["mark"].copy(),
                    funding=d["funding"].copy(), tradable=tradable_mask(d["ltp"]))

    monkeypatch.setattr(lab, "load_symbol", load_symbol)
    monkeypatch.setattr(lab, "ProductCatalog", _Catalog)
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "data" / "candles" / fx.SYMBOL
    p.mkdir(parents=True)
    fx.oi().to_parquet(p / "oi_1h.parquet", index=False)
    pk = fx.picks()
    btc = lab._btc_frame()
    return lab._coin((fx.SYMBOL, set(pk.pick_day.astype("int64")), pk, fx.META, btc))


def test_lab_features_match_the_pre_refactor_golden(monkeypatch, tmp_path):
    got = run_lab_coin(monkeypatch, tmp_path)
    assert got is not None, "the synthetic market produced no trades"
    f, paths = got
    if os.environ.get("DELTABT_WRITE_GOLDEN"):
        fx.GOLDEN.parent.mkdir(exist_ok=True)
        f.to_parquet(fx.GOLDEN, index=False)
        paths.to_parquet(fx.GOLDEN_PATHS, index=False)
    gold = pd.read_parquet(fx.GOLDEN)
    gp = pd.read_parquet(fx.GOLDEN_PATHS)
    assert len(gold) >= 10, "too few signals for the parity test to mean anything"
    assert list(f.columns) == list(gold.columns)
    assert len(f) == len(gold)
    for col in gold.columns:
        a, b = f[col].to_numpy(), gold[col].to_numpy()
        if a.dtype.kind in "fiu" and b.dtype.kind in "fiu":
            np.testing.assert_allclose(a.astype(float), b.astype(float), rtol=1e-12, atol=1e-12,
                                       equal_nan=True, err_msg=col)
        else:
            assert list(a) == list(b), col
    assert list(paths.columns) == list(gp.columns)
    for col in ("mfe_R", "mae_R", "bars_to_mfe", "bars_held", "entry_time"):
        np.testing.assert_allclose(paths[col].to_numpy(float), gp[col].to_numpy(float),
                                   rtol=1e-12, err_msg=col)
    for col in ("pre_24h", "fav", "adv"):
        for x, y in zip(paths[col], gp[col]):
            np.testing.assert_allclose(np.asarray(x, float), np.asarray(y, float),
                                       rtol=1e-6, equal_nan=True, err_msg=col)


def test_every_feature_family_is_exercised():
    """The fixture must reach every reading, or parity on it proves nothing."""
    gold = pd.read_parquet(fx.GOLDEN)
    for col in ("funding_side", "oi_chg_1h", "oi_chg_24h", "btc_ret_24h", "btc_st_1h_agrees",
                "st_4h_agrees", "adx_1h", "day_gain_pct", "days_listed", "run_4h_atr"):
        assert gold[col].notna().any(), col
    assert set(gold.side.unique()) == {-1.0, 1.0}, "need longs and shorts (side adjustment)"
