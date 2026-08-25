"""The readiness gate must count the same unit the requirement is stated in.

WHAT THIS PINS
    ``TradingBot.warm_up`` derives ``need`` from the arm. For a spec arm that
    is ``warmup_1m_bars()``, which returns MINUTES -- 145 primary bars x 240 =
    34,800 for wpr_only@240m. The comparison then picked ``have`` by arm, and
    the spec arm fell into the branch that measures the FIVE-minute buffer, so
    34,800 minutes of requirement was tested against a count of 5m bars: 120
    days demanded where 24 were needed.

    It is not a near miss that a longer backfill would paper over. The gate is
    5x out by construction, and ``backfill_days`` is itself derived from
    ``warmup_1m_bars``, so extending the backfill moves both sides and never
    converges.

    Observed on the first roll of v5, 2026-08-25:

        warmed  bars_1m=38879  bars_5m=7775
        refusing to become ready: BTCUSD: only 7775 closed 5m bars after
        backfill, need 34800 for indicator warm-up

    38,879 1m bars clears 34,800. Nothing was missing. deploy.sh saw the
    container die, rolled the host back, and the workflow went red -- the
    fail-safe working correctly on a failure that was not real.
"""

from __future__ import annotations

import pytest

from app.config.settings import RiskConfig, Settings
from app.config.variants import resolve_strategy
from app.persistence.repository import InMemoryRepository
from app.runtime.bot import TradingBot
from app.strategy.spec_arm import warmup_1m_bars
from deltabt.spec import StrategySpec
from tests.live.test_end_to_end import cached_bars
from tests.live.test_recovery import COSTS, DeadFeed

pytestmark = pytest.mark.asyncio

VARIANT = "SPEC:wpr_only@240"


@pytest.fixture(scope="module")
def spec() -> StrategySpec:
    s = resolve_strategy({"DELTABOT_VARIANT": VARIANT})
    assert isinstance(s, StrategySpec), "the variant no longer resolves to a spec"
    return s


def make_spec_bot(spec: StrategySpec, bars) -> TradingBot:
    class Backfill:
        async def warm_up(self, symbol, days, now=None):
            return list(bars)

        async def fetch(self, *a, **k):
            return []

        async def fill_gap(self, *a, **k):
            return []

    settings = Settings(symbols=("BTCUSD",),
                        risk=RiskConfig(starting_equity=10_000.0))
    return TradingBot(settings, InMemoryRepository({}), COSTS, strategy=spec,
                      backfiller=Backfill(), feed=DeadFeed())


async def test_exactly_the_stated_warm_up_is_enough(spec):
    """`need` minutes of 1m history must satisfy a requirement stated in them."""
    need = warmup_1m_bars(spec)
    bars = cached_bars(n=need)
    bot = make_spec_bot(spec, bars)

    await bot.warm_up()

    assert bot.recovery_error is None, bot.recovery_error
    # and the failure it replaces was specifically a unit confusion
    assert bot.spec_arm is not None


async def test_one_bar_short_is_refused_and_says_1m(spec):
    """The gate must still bite -- and name the unit it actually counted."""
    need = warmup_1m_bars(spec)
    bars = cached_bars(n=need)[: need - 1]
    bot = make_spec_bot(spec, bars)

    await bot.warm_up()

    assert bot.recovery_error is not None, "a short warm-up was accepted"
    assert "1m bars" in bot.recovery_error, (
        f"the gate reported a unit it did not measure: {bot.recovery_error}")
    assert str(need) in bot.recovery_error


async def test_the_requirement_is_not_a_five_minute_count(spec):
    """A direct statement of the bug, independent of any bot wiring.

    34,800 5m bars is 120 days. The spec asks for 24. If these ever become
    equal the two sides are being read in the same unit again.
    """
    need = warmup_1m_bars(spec)
    assert need == spec.warmup_bars * spec.primary_minutes
    assert need // 1440 < 30, "24 days of 1m history, not 120"
