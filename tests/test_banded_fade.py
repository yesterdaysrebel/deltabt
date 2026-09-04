"""``manual_scalp_st_banded_fade`` is the exact inverse of ``manual_scalp_st_banded``.

The family exists to fade the majors' 5m Supertrend flip, and its whole
claim rests on being the SAME bars with the side swapped. If the two ever
drift apart -- a level nudged, the edge trigger firing on a different bar --
the walk-forward numbers recorded in the catalog stop describing it.

It also pins the deployed family's ``config_hash``: the fade was added as new
vocabulary VALUES rather than a new spec field precisely so that hash does
not move under the running paper experiment.
"""
import numpy as np

from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.spec import SUPERTREND_MODES, WPR_RULES

DEPLOYED_HASH = "d6c319a387f656677a8614d66809a0c8af59a5c1c5e75d2fd9588e6026082df6"


def _bars(n: int = 3000, seed: int = 7):
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    spread = np.abs(rng.normal(0, 0.003, n)) * close
    high, low = close + spread, close - spread
    open_ = np.r_[close[0], close[:-1]]
    import pandas as pd
    return pd.DataFrame(dict(time=np.arange(n, dtype="int64") * 300 + 1_700_000_000,
                             open=open_, high=high, low=low, close=close,
                             volume=np.ones(n)))


def test_vocabulary():
    assert "banded_fade" in WPR_RULES
    assert "counter" in SUPERTREND_MODES


def test_deployed_family_hash_unchanged():
    assert build_spec("manual_scalp_st_banded", 5, 1).config_hash == DEPLOYED_HASH


def test_fade_is_exact_inverse_of_banded():
    bars = _bars()
    live = rulecore.to_engine_signals(
        rulecore.compute(bars, None, build_spec("manual_scalp_st_banded", 5, 1)))
    fade = rulecore.to_engine_signals(
        rulecore.compute(bars, None, build_spec("manual_scalp_st_banded_fade", 5, 1)))
    assert live.long_entry.sum() > 10 and live.short_entry.sum() > 10, "fixture fires nothing"
    np.testing.assert_array_equal(fade.long_entry, live.short_entry)
    np.testing.assert_array_equal(fade.short_entry, live.long_entry)
    # A bar can never be both a long and a short setup on either family.
    assert not np.any(fade.long_entry & fade.short_entry)


def test_fade_differs_from_banded_only_in_gate_vocabulary():
    a = build_spec("manual_scalp_st_banded", 5, 1)
    b = build_spec("manual_scalp_st_banded_fade", 5, 1)
    da, db = a.to_dict(), b.to_dict()
    changed = {k for k in da if da[k] != db[k]}
    assert changed == {"name", "primary"}, changed
    assert da["primary"] | {"supertrend": "counter", "wpr_rule": "banded_fade"} == db["primary"]
