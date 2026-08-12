"""Paths, exchange constants, and the parameter dataclasses.

Every constant here that describes Delta Exchange India was verified against
the live API rather than taken from documentation. Where the docs and the API
disagreed, the API won.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

# --- paths ------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DELTABT_DATA", ROOT / "data"))
CACHE_DIR = DATA_DIR / "candles"
META_DIR = DATA_DIR / "meta"
OUT_DIR = Path(os.environ.get("DELTABT_OUT", ROOT / "out"))

# --- exchange ---------------------------------------------------------------

BASE_URL = "https://api.india.delta.exchange"

#: Indian GST applied on top of the exchange commission. Delta's own fee
#: article states 18% on futures and options trading fees. This is why the
#: standard 0.05% taker rate shows up as 0.059% in practice.
GST_MULTIPLIER = 1.18

#: The candle endpoint returns at most this many bars, truncated on the OLD
#: side of the requested window. Pagination therefore walks `end` backwards.
MAX_CANDLES_PER_REQUEST = 4000

#: Documented quota is 10k units / 5min with candles at weight 3, i.e. ~3333
#: candle calls per window. We stay well under it.
REQUESTS_PER_SECOND = 8.0

#: Resolutions the API actually accepts. TradingView-style bare numerics
#: ("1", "5", "60") are rejected with bad_schema.
RESOLUTION_1M = "1m"
RESOLUTION_5M = "5m"

#: Funding rates are only ever read at settlement instants that are 4-8 hours
#: apart, so storing the rate series at 1m would cost ~60x the requests for
#: identical results.
RESOLUTION_FUNDING = "1h"

#: Series prefixes. MARK carries no synthetic zero-volume bars and is what
#: stop orders trigger on by default; LTP is what fills price off.
SERIES_LTP = ""
SERIES_MARK = "MARK:"
SERIES_FUNDING = "FUNDING:"

#: A run of identical o=h=l=c bars with zero volume this long or longer is
#: treated as an exchange halt rather than merely an illiquid stretch.
HALT_MIN_RUN_BARS = 20

#: Symbols whose 1m series is more than this fraction synthetic are unusable
#: for a 1-minute strategy regardless of headline turnover.
MAX_SYNTHETIC_RATIO = 0.05

#: Screening also requires at least this much usable history.
MIN_USABLE_DAYS = 180


# --- strategy parameters ----------------------------------------------------


@dataclass(frozen=True)
class WprLatch:
    """Parameters for the stateful band-traverse gate.

    Disabled by default. Measured on BTCUSD at a 15m base with the cost gate
    on, the gate cut the sample from 401 trades to 57 (-86%) *and* worsened
    expectancy from -0.078R to -0.212R. It is retained as an option, and
    remains a sweep candidate, but nothing in the data has justified turning
    it on.
    """

    enabled: bool = False
    length: int = 14
    #: Long arms below this, short arms above its mirror.
    arm_long: float = -80.0
    arm_short: float = -20.0
    #: Long fires crossing up through this, short crossing down through it.
    fire_long: float = -20.0
    fire_short: float = -80.0
    #: Bars the arm survives after WPR last left the arming zone. Effectively a
    #: dead parameter at short lengths and a live one at long lengths.
    expiry_bars: int = 30
    #: Clearing while in a position forces every entry to come from a fresh
    #: traverse initiated after going flat.
    clear_in_position: bool = True
    #: Clearing on an adverse Supertrend flip is cheap insurance. Note it must
    #: never clear on a flip *toward* the setup -- WPR only reaches the long
    #: arming zone during a downtrend, so that would disable longs entirely.
    clear_on_adverse_flip: bool = False

    def validate(self) -> None:
        if self.length < 2:
            raise ValueError(f"WPR length must be >= 2, got {self.length}")
        if not (-100.0 <= self.arm_long < self.fire_long <= 0.0):
            raise ValueError(
                f"long fire level {self.fire_long} must sit above arm level "
                f"{self.arm_long} within [-100, 0]"
            )
        if not (-100.0 <= self.fire_short < self.arm_short <= 0.0):
            raise ValueError(
                f"short fire level {self.fire_short} must sit below arm level "
                f"{self.arm_short} within [-100, 0]"
            )
        if self.expiry_bars < 1:
            raise ValueError(f"expiry_bars must be >= 1, got {self.expiry_bars}")


@dataclass(frozen=True)
class StrategyParams:
    """Full strategy configuration.

    Defaults reproduce the *corrected* variant. `StrategyParams.parity()`
    returns the as-written Pine configuration instead.
    """

    mode: str = "corrected"

    #: Base bar size in minutes. Candles are always fetched at 1m and
    #: resampled, so this is free to sweep.
    #:
    #: This is the most consequential parameter in the whole configuration.
    #: Cost per R is set by how far the Supertrend sits from price, which
    #: scales with bar range. Measured on BTCUSD with ST(3.0, 10): median R is
    #: 12 bps at 1m, 32 bps at 5m, 62 bps at 15m and 138 bps at 1h -- against
    #: a 15.8 bps round trip. At 1m the strategy pays ~1.3R per trade in costs
    #: and cannot be rescued by any indicator setting; 1h pays 0.11R.
    base_minutes: int = 1
    #: Higher timeframe used for trend confirmation.
    confirm_minutes: int = 5

    # Supertrend
    st_atr_period: int = 10
    st_factor: float = 2.0

    # DMI / ADX
    di_length: int = 14
    adx_smoothing: int = 28

    #: When set, ADX thresholds are absolute (Pine parity). When None, they are
    #: computed per symbol as a percentile of that symbol's own ADX
    #: distribution -- 25 is not a trend filter at DI=14, where the driftless
    #: noise floor is already ~23.
    adx_threshold_1m: float | None = None
    adx_threshold_5m: float | None = None
    adx_percentile_1m: float = 0.70
    #: Calibrated separately from the 1m threshold. Measured on BTCUSD the two
    #: distributions are near-identical (median 23.3 on both, P(>25) 40.6% vs
    #: 41.3%), so this is not currently correcting a large skew -- but the ADX
    #: level depends on DI length and bar size, both of which are swept, so a
    #: shared threshold would not stay comparable across the grid.
    adx_percentile_5m: float = 0.75
    require_adx_rising: bool = True
    use_5m_adx: bool = True

    # Williams %R
    wpr: WprLatch = field(default_factory=WprLatch)

    # Structure
    #: `close > supertrend` is implied by the Supertrend direction itself and
    #: rejects nothing. Kept as a switch only so parity mode can include it.
    use_structure: bool = False
    use_5m_confirmation: bool = True
    #: Level conditions form plateaus; without an edge trigger the strategy
    #: re-enters on the first flat bar after every stop-out.
    edge_trigger: bool = True
    cooldown_bars: int = 10

    # Risk / exits
    risk_percent: float = 0.5
    reward_risk: float = 2.0
    max_leverage: float = 3.0
    #: Floors the stop distance so a near-zero denominator cannot explode size.
    min_stop_atr_mult: float = 0.5
    min_stop_ticks: int = 10
    #: In BARS, so its wall-clock meaning scales with base_minutes
    #: (240 bars is 4h at 1m but 10 days at 1h).
    max_hold_bars: int = 240
    exit_on_trend_flip: bool = True

    #: Reject signals whose modelled round-trip cost exceeds this fraction of
    #: R. The single most important addition: on 1m with a 2xATR stop, cost
    #: routinely runs 0.36R-0.71R, which no win rate can overcome.
    max_cost_per_r: float | None = 0.15

    def validate(self) -> None:
        if self.mode not in ("parity", "corrected"):
            raise ValueError(f"mode must be 'parity' or 'corrected', got {self.mode!r}")
        if self.base_minutes < 1:
            raise ValueError(f"base_minutes must be >= 1, got {self.base_minutes}")
        if self.confirm_minutes % self.base_minutes != 0:
            raise ValueError(
                f"confirm_minutes ({self.confirm_minutes}) must be a whole multiple "
                f"of base_minutes ({self.base_minutes}); otherwise higher-timeframe "
                f"bars do not align with base bars and the confirmed-value read "
                f"would silently straddle boundaries"
            )
        if self.confirm_minutes <= self.base_minutes:
            raise ValueError(
                f"confirm_minutes ({self.confirm_minutes}) must exceed base_minutes "
                f"({self.base_minutes})"
            )
        for name in ("st_atr_period", "di_length", "adx_smoothing"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1, got {getattr(self, name)}")
        if self.st_factor <= 0:
            raise ValueError(f"st_factor must be > 0, got {self.st_factor}")
        if not 0 < self.risk_percent <= 100:
            raise ValueError(f"risk_percent must be in (0, 100], got {self.risk_percent}")
        if self.reward_risk <= 0:
            raise ValueError(f"reward_risk must be > 0, got {self.reward_risk}")
        if self.max_leverage <= 0:
            raise ValueError(f"max_leverage must be > 0, got {self.max_leverage}")
        for name in ("adx_percentile_1m", "adx_percentile_5m"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {getattr(self, name)}")
        self.wpr.validate()

    @property
    def warmup_bars(self) -> int:
        """Bars to discard before signals are trustworthy.

        ADX needs roughly `di_length + 2 * adx_smoothing` to settle after
        Wilder seeding; the other indicators need their own window.
        """
        return max(
            self.wpr.length,
            self.st_atr_period,
            self.di_length + 2 * self.adx_smoothing,
        ) + 1

    @classmethod
    def parity(cls, **overrides) -> "StrategyParams":
        """The strategy exactly as written in the Pine source.

        Reproduces every flaw on purpose. Its value is as a correctness check:
        the author's TradingView tester produced zero closed trades at these
        settings, so a faithful port must too.
        """
        base = cls(
            mode="parity",
            st_atr_period=10,
            st_factor=2.0,
            di_length=14,
            adx_smoothing=28,
            adx_threshold_1m=25.0,
            adx_threshold_5m=25.0,
            require_adx_rising=False,
            use_5m_adx=True,
            wpr=WprLatch(
                enabled=True,
                length=140,
                # Parity mode ignores the latch and uses the original
                # single-bar-uptick rule; these are carried only so the
                # dataclass validates.
                arm_long=-80.0,
                arm_short=-20.0,
                fire_long=-20.0,
                fire_short=-80.0,
                clear_in_position=False,
            ),
            use_structure=True,
            use_5m_confirmation=True,
            edge_trigger=False,
            cooldown_bars=0,
            risk_percent=0.5,
            reward_risk=2.0,
            max_leverage=float("inf"),
            min_stop_atr_mult=0.0,
            min_stop_ticks=1,
            max_hold_bars=0,
            exit_on_trend_flip=False,
            max_cost_per_r=None,
        )
        return replace(base, **overrides) if overrides else base
