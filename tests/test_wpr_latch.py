"""Tests for the stateful WPR band-traverse gate.

The latch is path-dependent, so its failure modes are ordering bugs rather
than arithmetic ones. Each test below pins one property that, if broken,
silently changes trade counts rather than raising.

Note the leading bar in most fixtures: arming requires the previous bar to be
valid, mirroring Pine's ``wprValid = not na(wpr) and not na(wpr[1])`` guard.
"""

from __future__ import annotations

import numpy as np
import pytest

from deltabt import wpr_latch as wl

ARM, FIRE = -80.0, -20.0


def fires(values, *, expiry=30, long_side=True, arm=ARM, fire=FIRE) -> int:
    return int(
        wl.latch_fires(
            np.array(values, dtype=float),
            arm_level=arm,
            fire_level=fire,
            expiry_bars=expiry,
            long_side=long_side,
        ).sum()
    )


class TestLatchBehaviour:
    def test_no_fire_without_arming(self):
        assert fires([-50, -40, -30, -10, -5]) == 0

    def test_fires_after_arming(self):
        assert fires([-50, -90, -70, -50, -10]) == 1

    def test_consume_on_fire(self):
        """One arm cycle yields at most one fire.

        Without this the gate stays true on later bars and rebuilds exactly
        the entry plateau the latch exists to remove.
        """
        assert fires([-50, -90, -10, -30, -10, -30, -10]) == 1

    def test_rearm_allows_another_fire(self):
        assert fires([-50, -90, -10, -90, -10]) == 2

    def test_expiry_kills_a_stale_arm(self):
        assert fires([-50, -90] + [-50] * 40 + [-10], expiry=5) == 0

    def test_expiry_clock_restamps_while_in_zone(self):
        """Expiry counts from the LAST bar in the zone, not the first dip.

        Counting from the first dip would kill valid setups after a long
        basing period.
        """
        assert fires([-50] + [-90] * 40 + [-50, -10], expiry=5) == 1

    def test_exact_threshold_touch_is_not_a_cross(self):
        assert fires([-50, -90, -20, -20]) == 0

    def test_cross_requires_strict_move_through(self):
        assert fires([-50, -90, -20, -19]) == 1

    def test_nan_warmup_produces_no_fires(self):
        assert fires([np.nan] * 20) == 0

    def test_fires_after_nan_warmup(self):
        assert fires([np.nan] * 10 + [-90, -90, -10]) == 1

    def test_nan_gap_does_not_break_the_arm(self):
        assert fires([-50, -90, np.nan, -50, -10]) == 1

    def test_short_side_mirrors(self):
        assert fires([-50, -10, -30, -50, -90], long_side=False,
                     arm=-20.0, fire=-80.0) == 1

    def test_short_side_does_not_fire_on_long_setup(self):
        assert fires([-50, -90, -70, -50, -10], long_side=False,
                     arm=-20.0, fire=-80.0) == 0


class TestVectorisedEquivalence:
    @pytest.mark.parametrize("seed", range(12))
    def test_matches_fsm_on_random_walks(self, seed):
        rng = np.random.default_rng(seed)
        n = int(rng.integers(50, 800))
        v = np.clip(np.cumsum(rng.standard_normal(n) * 8) - 50, -100, 0)
        v[rng.choice(n, size=int(rng.integers(0, n // 4 + 1)), replace=False)] = np.nan

        for long_side in (True, False):
            arm, fire = (ARM, FIRE) if long_side else (FIRE, ARM)
            for expiry in (5, 30, 120, 10**6):
                a = wl.latch_fires(
                    v, arm_level=arm, fire_level=fire,
                    expiry_bars=expiry, long_side=long_side,
                )
                b = wl.latch_fires_vectorised(
                    v, arm_level=arm, fire_level=fire,
                    expiry_bars=expiry, long_side=long_side,
                )
                assert np.array_equal(a, b), (
                    f"seed={seed} long={long_side} expiry={expiry}"
                )


class TestStepState:
    def test_step_state_matches_batch_scan(self):
        """The incremental driver must agree with the batch scan.

        The engine uses step_state because the clear-in-position reset depends
        on state downstream of the fires; it must not diverge from the array
        path when no resets apply.
        """
        rng = np.random.default_rng(99)
        v = np.clip(np.cumsum(rng.standard_normal(500) * 8) - 50, -100, 0)

        batch = wl.latch_fires(
            v, arm_level=ARM, fire_level=FIRE, expiry_bars=30, long_side=True
        )

        arm_bar = -1
        stepped = np.zeros(len(v), dtype=bool)
        for i in range(len(v)):
            prev = v[i - 1] if i > 0 else np.nan
            arm_bar, fired = wl.step_state(
                arm_bar, i, v[i], prev, ARM, FIRE, 30, True
            )
            stepped[i] = fired

        assert np.array_equal(batch, stepped)


class TestHtfMapping:
    def test_does_not_forward_fill(self):
        """One HTF event must not become five base-timeframe opportunities.

        Forward-filling the boolean is the most likely way a port silently
        disagrees with the Pine original.
        """
        htf_time = np.array([0, 300, 600, 900])
        ltf_time = np.arange(0, 1200, 60)
        out = wl.map_htf_fires_to_ltf(
            np.array([False, True, False, False]), htf_time, ltf_time
        )
        assert out.sum() == 1

    def test_fire_lands_after_the_htf_bar_closed(self):
        htf_time = np.array([0, 300, 600, 900])
        ltf_time = np.arange(0, 1200, 60)
        out = wl.map_htf_fires_to_ltf(
            np.array([False, True, False, False]), htf_time, ltf_time
        )
        # The fire occurred on the HTF bar opening at 300, so it may only act
        # from 600 onward -- never inside the bar that produced it.
        assert ltf_time[out][0] == 600

    def test_empty_input(self):
        out = wl.map_htf_fires_to_ltf(
            np.array([], dtype=bool), np.array([]), np.arange(0, 300, 60)
        )
        assert out.sum() == 0
