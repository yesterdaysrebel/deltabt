"""A confirmation timeframe SLOWER than the primary is a context chart.

`manual_scalp_banded_h1dir` (decision C) reads the Supertrend direction from
the 1h chart while the %R band, the slope requirement and the edge trigger
stay on 5m. It does that with no new spec field: the confirmation timeframe
is simply allowed to be slower than the primary, and `align_confirm` already
returns the last CLOSED confirmation bar, which is exactly the causal
higher-timeframe read.

Two properties have to hold or the idea is worthless:

  * NO LOOK-AHEAD. The context bar gating a primary bar must close at or
    before that primary bar closes. Two earlier attempts at this analysis
    were wrong in opposite directions -- one read the containing hourly bar
    (look-ahead), one produced a constant mask -- so it is asserted here
    rather than assumed.
  * NO HASH MOVEMENT. Expressing this as a field on StrategySpec would put
    it in `asdict`, move every existing spec's config_hash, orphan the
    recorded sweeps and refuse the running paper experiment.
"""
import numpy as np
import pandas as pd
import pytest

from deltabt import indicators as ind
from deltabt import rulecore
from deltabt.catalog import FAMILIES, build_spec
from deltabt.spec import StrategySpec, TimeframeRules

DEPLOYED = "d6c319a387f656677a8614d66809a0c8af59a5c1c5e75d2fd9588e6026082df6"
PRIMARY_MIN, CTX_MIN = 5, 60


def _bars(n, minutes, seed=11, start=1_760_000_000):
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.0015, n)))
    spread = np.abs(rng.normal(0, 0.0012, n)) * close
    return pd.DataFrame(dict(
        time=(start + np.arange(n, dtype="int64") * minutes * 60),
        open=np.r_[close[0], close[:-1]], high=close + spread,
        low=close - spread, close=close, volume=np.ones(n)))


@pytest.fixture(scope="module")
def frames():
    primary = _bars(4000, PRIMARY_MIN)
    step = CTX_MIN // PRIMARY_MIN
    ctx = (primary.assign(b=primary["time"] // (CTX_MIN * 60))
           .groupby("b").agg(time=("time", "first"), open=("open", "first"),
                             high=("high", "max"), low=("low", "min"),
                             close=("close", "last"), volume=("volume", "sum"))
           .reset_index(drop=True))
    assert len(ctx) >= len(primary) // step - 1
    return primary, ctx


def test_slower_confirmation_is_accepted_and_faster_still_is():
    build_spec("manual_scalp_banded_h1dir", PRIMARY_MIN, CTX_MIN)
    build_spec("manual_scalp_st_banded", PRIMARY_MIN, 1)


def test_non_tiling_timeframes_are_still_refused():
    with pytest.raises(ValueError, match="divide one another"):
        build_spec("manual_scalp_banded_h1dir", PRIMARY_MIN, 7)
    with pytest.raises(ValueError):
        StrategySpec(name="x", primary_minutes=5, confirm_minutes=7,
                     confirm=TimeframeRules(supertrend="aligned")).validate()


def test_deployed_family_hash_has_not_moved():
    assert build_spec("manual_scalp_st_banded", 5, 1).config_hash == DEPLOYED


def test_every_family_still_builds():
    for family in sorted(FAMILIES):
        build_spec(family, 5, 1).validate()


def test_alignment_never_reads_a_bar_that_has_not_closed(frames):
    """The property the whole idea rests on."""
    primary, ctx = frames
    spec = build_spec("manual_scalp_banded_h1dir", PRIMARY_MIN, CTX_MIN)
    sig = rulecore.compute(primary, ctx, spec)
    idx = sig.confirm_index
    have = idx >= 0
    assert have.sum() > 100, "fixture produced almost no aligned bars"

    ctx_close = ctx["time"].to_numpy()[idx[have]] + CTX_MIN * 60
    primary_close = primary["time"].to_numpy()[have] + PRIMARY_MIN * 60
    assert np.all(ctx_close <= primary_close), "a context bar closes after the primary bar it gates"
    # ...and it is the LATEST such bar, not an older one.
    assert np.all(ctx_close + CTX_MIN * 60 > primary_close), "a stale context bar was used"


def test_alignment_matches_an_independent_searchsorted(frames):
    primary, ctx = frames
    spec = build_spec("manual_scalp_banded_h1dir", PRIMARY_MIN, CTX_MIN)
    got = rulecore.compute(primary, ctx, spec).confirm_index
    want = np.searchsorted(
        ctx["time"].to_numpy(),
        primary["time"].to_numpy() + PRIMARY_MIN * 60 - CTX_MIN * 60,
        side="right") - 1
    np.testing.assert_array_equal(got, want)


def test_no_entry_fires_against_the_context_direction(frames):
    primary, ctx = frames
    spec = build_spec("manual_scalp_banded_h1dir", PRIMARY_MIN, CTX_MIN)
    sig = rulecore.compute(primary, ctx, spec)
    e = rulecore.to_engine_signals(sig)
    _, direction = ind.supertrend(ctx["high"].to_numpy(), ctx["low"].to_numpy(),
                                  ctx["close"].to_numpy(),
                                  spec.st_multiplier, spec.st_atr_period)
    idx = sig.confirm_index
    per_bar = np.where(idx >= 0, direction[np.clip(idx, 0, len(direction) - 1)], np.nan)
    # Pine: direction < 0 is an UPTREND.
    assert not np.any(e.long_entry & ~(np.isfinite(per_bar) & (per_bar < 0)))
    assert not np.any(e.short_entry & ~(np.isfinite(per_bar) & (per_bar > 0)))
    assert e.long_entry.sum() + e.short_entry.sum() > 0, "fixture fired nothing"


def test_context_changes_the_signal_at_all(frames):
    """Guard against a gate that is silently vacuous."""
    primary, ctx = frames
    with_ctx = rulecore.to_engine_signals(
        rulecore.compute(primary, ctx, build_spec("manual_scalp_banded_h1dir", PRIMARY_MIN, CTX_MIN)))
    without = rulecore.to_engine_signals(
        rulecore.compute(primary, None, build_spec("manual_scalp_banded", PRIMARY_MIN, 1)))
    fired_with = int(with_ctx.long_entry.sum() + with_ctx.short_entry.sum())
    fired_without = int(without.long_entry.sum() + without.short_entry.sum())
    assert 0 < fired_with < fired_without, (fired_with, fired_without)


def test_warmup_is_sized_off_the_slower_frame():
    """A 60m context needs 145 hourly bars, which is days of 1m history."""
    from app.strategy.spec_arm import warmup_1m_bars
    ctx_spec = build_spec("manual_scalp_banded_h1dir", PRIMARY_MIN, CTX_MIN)
    fast_spec = build_spec("manual_scalp_st_banded", PRIMARY_MIN, 1)
    assert warmup_1m_bars(ctx_spec) == ctx_spec.warmup_bars * CTX_MIN
    assert warmup_1m_bars(fast_spec) == fast_spec.warmup_bars * PRIMARY_MIN
    # Exactly the timeframe ratio, and in wall-clock terms six days of 1m
    # history rather than twelve hours. Sizing this off the primary alone
    # starves the context gate and it never clears warm-up.
    assert warmup_1m_bars(ctx_spec) == (CTX_MIN // PRIMARY_MIN) * warmup_1m_bars(fast_spec)
    assert warmup_1m_bars(ctx_spec) / (60 * 24) > 5.5


# --- the variant string, which is how the deployed bot addresses this -------
#
# The four-link chain (terraform -> user_data -> run.sh -> DELTABOT_VARIANT)
# has broken three times in this repository's history. Here the failure would
# be silent rather than loud: `SPEC:manual_scalp_banded_h1dir@5` parses, and
# validates, and resolves the confirmation to ONE MINUTE, so the bot would
# read its trend direction from the 1m chart, produce a valid-looking
# config_hash and trade a rule nobody measured.

def test_the_variant_string_carries_the_context_timeframe():
    from app.config.variants import resolve_strategy
    spec = resolve_strategy({"DELTABOT_VARIANT": "SPEC:manual_scalp_banded_h1dir@5/60"})
    assert (spec.primary_minutes, spec.confirm_minutes) == (5, 60)
    assert spec.config_hash == build_spec("manual_scalp_banded_h1dir", 5, 60).config_hash


def test_a_context_family_without_its_timeframe_is_refused():
    from app.config.variants import resolve_strategy
    with pytest.raises(ValueError, match="must be stated explicitly"):
        resolve_strategy({"DELTABOT_VARIANT": "SPEC:manual_scalp_banded_h1dir@5"})


@pytest.mark.parametrize("variant", [
    "SPEC:manual_scalp_st_banded@5", "SPEC:atr_arm@5", "SPEC:wpr_only@240",
])
def test_existing_variant_strings_resolve_exactly_as_before(variant):
    """The suffix is additive; nothing already deployed may move."""
    from app.config.variants import resolve_strategy
    family, _, minutes = variant.split(":", 1)[1].partition("@")
    assert resolve_strategy({"DELTABOT_VARIANT": variant}).config_hash == \
        build_spec(family, int(minutes)).config_hash


# The terraform/deploy.yml/monitor.yml cross-check already exists as
# tests/live/test_stack_table_consistency.py and was taught this variant
# format rather than duplicated here.


# --- the 3R variant ---------------------------------------------------------

def test_the_wide_target_family_differs_only_in_its_target():
    """Same entry, harvested wider. If the ENTRY ever drifts between the two,
    the comparison that justified the change stops being about the exit."""
    a = build_spec("manual_scalp_banded_h1dir", PRIMARY_MIN, CTX_MIN)
    b = build_spec("manual_scalp_banded_h1dir_t3", PRIMARY_MIN, CTX_MIN)
    da, db = a.to_dict(), b.to_dict()
    assert {k for k in da if da[k] != db[k]} == {"name", "target_r"}
    assert (da["target_r"], db["target_r"]) == (1.0, 3.0)


def test_the_two_families_fire_on_identical_bars(frames):
    """The exit changed; the entry did not."""
    primary, ctx = frames
    a = rulecore.to_engine_signals(rulecore.compute(
        primary, ctx, build_spec("manual_scalp_banded_h1dir", PRIMARY_MIN, CTX_MIN)))
    b = rulecore.to_engine_signals(rulecore.compute(
        primary, ctx, build_spec("manual_scalp_banded_h1dir_t3", PRIMARY_MIN, CTX_MIN)))
    assert a.long_entry.sum() + a.short_entry.sum() > 0, "fixture fired nothing"
    np.testing.assert_array_equal(a.long_entry, b.long_entry)
    np.testing.assert_array_equal(a.short_entry, b.short_entry)


def test_the_hold_is_paired_with_the_target():
    """A 3R target on a 4xATR stop sits 12xATR away. Under a 24h cap the CAP
    becomes the exit rather than the target, which is how a wide-target arm
    silently turns into 'hold overnight and take whatever'."""
    import pathlib
    import re
    tf = (pathlib.Path(__file__).resolve().parents[1]
          / "infra/terraform/variables.tf").read_text()
    hold = int(re.search(r'variable "max_hold_seconds".*?default\s+=\s+(\d+)', tf, re.S).group(1))
    variant = re.search(r'atr\s*=\s*\{\s*variant\s*=\s*"([^"]+)"', tf).group(1)
    if "_t3@" in variant:
        assert hold >= 48 * 3600, (
            f"the 3R arm is configured with a {hold/3600:.0f}h hold; it was "
            f"measured at 72h, and at 24h the time cap outranks the target")
