"""The frozen H-WPR-1 Variant A rule set, and its content hash.

FROZEN. Nothing in this module may be tuned during a forward test. The values
here are the pre-registered research configuration, not an optimised one, and
the research verdict on this family was NO ECONOMIC EDGE. V1 forward-tests it
to validate the execution engine, not to make money.

TIMEFRAME INVERSION (deliberate, see docs/architecture.md section 3.1)
    The research implementation evaluates on a 1m grid with 5m supplying
    confirmed trend context. V1 inverts this by explicit instruction: the
    PRIMARY timeframe is 5m and 1m is the CONFIRMATION. Concretely:

        research H-WPR-1 Arm A:  5m regime AND 1m Supertrend AND 1m ADX/DI
                                 AND 1m Williams %R, evaluated every 1m close
        V1:                      5m Supertrend AND 5m ADX/DI AND 5m Williams %R
                                 AND 1m Supertrend AND 1m ADX/DI,
                                 evaluated every 5m close

    The rule STRUCTURE is identical -- a full indicator stack on the signal
    timeframe, plus trend agreement from the other timeframe. Only the roles
    are swapped. This is not a silent reinterpretation: the backtester is
    untouched, and the two are not expected to produce the same trades.

ADX PERIOD -- A CONFLICT IN THE BRIEF, RESOLVED IN FAVOUR OF THE RESEARCH
    Section 2 of the V1 brief says "ADX: period = 14" while its own heading
    says "FREEZE THE RESEARCH RULE" and its body says the configuration is
    used "because it is the previously frozen/researched configuration". The
    frozen H-WPR-1 constant is ADX_PERIOD = 28 (Wilder smoothing of DX) with
    DI_PERIOD = 14.

    The two instructions cannot both be satisfied. This module follows the
    stated intent -- freeze the research rule -- and uses 28, because a value
    invented at implementation time would make the live signal incomparable to
    every measured backtest, which is the one thing V1 must not do.

    Changing it is a one-line edit to ADX_SMOOTHING below. Doing so changes
    the config hash, so every signal recorded afterwards is distinguishable in
    the audit trail from every signal recorded before. That is the intended
    way to make this decision, not a code change that hides itself.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

# --- frozen indicator constants (mirror deltabt.research.hwpr) --------------

ST_PERIOD = 10
ST_MULT = 2.0
DI_PERIOD = 14
#: Wilder smoothing applied to DX to obtain ADX. The research constant is 28.
#: See the module docstring for why this is not 14.
ADX_SMOOTHING = 28
WPR_PERIOD = 140
ADX_MIN = 25.0

#: Trailing bars handed to the indicator functions on every evaluation.
#: Warm-up for this rule set is max(140, 14 + 2*28, 10) + margin = ~145 bars,
#: so 1500 leaves a >10x margin and makes the tail values window-invariant.
#: Enforced by tests/live/test_window_invariance.py.
WINDOW_BARS = 1500


@dataclass(frozen=True)
class Supertrend:
    atr_period: int = ST_PERIOD
    multiplier: float = ST_MULT


@dataclass(frozen=True)
class Adx:
    period: int = ADX_SMOOTHING
    di_period: int = DI_PERIOD
    minimum: float = ADX_MIN


@dataclass(frozen=True)
class WilliamsR:
    period: int = WPR_PERIOD
    #: "variant_a" is `wpr > -80 and rising` for longs, mirrored for shorts.
    #: It is the only rule V1 accepts; the loader rejects anything else rather
    #: than silently falling back, because the four candidate WPR rules differ
    #: by orders of magnitude in firing rate.
    rule: str = "variant_a"


@dataclass(frozen=True)
class StrategyConfig:
    """The complete, resolved rule set. Hashed into every idempotency key."""

    name: str = "H-WPR-1-VariantA-V2"
    primary_timeframe: str = "5m"
    confirmation_timeframe: str = "1m"

    supertrend: Supertrend = field(default_factory=Supertrend)
    adx: Adx = field(default_factory=Adx)
    williams_r: WilliamsR = field(default_factory=WilliamsR)

    #: Components of the confirmation timeframe.
    confirm_supertrend: bool = True
    confirm_adx_di: bool = True
    #: V2. The 1m Williams %R is now part of the confirmation. V1 computed it
    #: and even persisted it, but never put it in the decision, so the gate was
    #: 5m-only while the specification called for both timeframes.
    confirm_wpr: bool = True

    #: V2. Emit a signal only on the bar the COMPLETE setup goes FALSE -> TRUE.
    #: V1 was level-triggered: it re-emitted on every bar the setup stayed true,
    #: so one setup could be entered repeatedly. Masked in V1 by
    #: max_open_positions=1, which refused the repeats at the risk gate -- but a
    #: risk gate absorbing a signalling defect is not the same as not having it.
    fire_once: bool = True

    #: Target as a multiple of the structural risk distance.
    target_r: float = 2.0
    #: Reject a setup whose stop is further than this fraction of price. The
    #: research used the same 5% guard.
    max_stop_pct: float = 0.05
    window_bars: int = WINDOW_BARS

    def validate(self) -> None:
        if self.williams_r.rule != "variant_a":
            raise ValueError(
                f"williams_r.rule must be 'variant_a', got "
                f"{self.williams_r.rule!r}. The other candidate rules (original "
                f"Pine uptick, traverse latch, variant C crossover) fire at "
                f"wildly different rates and are not interchangeable."
            )
        if self.primary_timeframe != "5m" or self.confirmation_timeframe != "1m":
            raise ValueError(
                f"V2 is frozen at 5m primary / 1m confirmation, got "
                f"{self.primary_timeframe}/{self.confirmation_timeframe}"
            )
        # The oscillator on both timeframes is what V2 IS. A config that turns
        # it off has reverted to V1's rule set, so it must not still be called
        # V2 -- that combination is the exact drift the config hash exists to
        # make impossible. Naming it V1 is allowed and is how the variants
        # module reaches the older rule set.
        if not self.confirm_wpr and "V2" in self.name:
            raise ValueError(
                f"confirm_wpr=False is V1's rule (5m-only oscillator) but the "
                f"name is {self.name!r}. Name it for the rules it actually "
                f"implements -- see app/config/variants.py."
            )
        if self.adx.minimum <= 0:
            raise ValueError("adx.minimum must be a positive absolute threshold")
        if self.target_r <= 0:
            raise ValueError("target_r must be > 0")
        if self.window_bars < 400:
            raise ValueError(
                f"window_bars={self.window_bars} is too small for a "
                f"{self.williams_r.period}-period oscillator plus Wilder "
                f"warm-up; tail values would not be window-invariant"
            )

    # -- identity ----------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def config_hash(self) -> str:
        """Content hash of the fully resolved rule set.

        Deliberately not a version string. A hand-maintained version label is
        exactly how a parameter change becomes invisible in an audit trail --
        someone edits a threshold and forgets to bump it. A content hash cannot
        be forgotten.
        """
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    @property
    def version(self) -> str:
        return f"{self.name}@{self.config_hash}"


FROZEN = StrategyConfig()
FROZEN.validate()
