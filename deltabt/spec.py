"""One declarative strategy definition, consumed by the backtester and the bot.

WHY THIS EXISTS
    Before this module a strategy was written twice: once as a research
    implementation under ``deltabt/research/`` and once as a live arm under
    ``app/strategy/``. ``app/strategy/rules.py``, ``atr_arm.py`` and
    ``flip_arm.py`` are ~940 lines that differ only in which gates are enabled,
    which timeframe decides, and how entry fires -- each with its own config
    dataclass naming the same quantity differently (``supertrend.multiplier``
    against ``supertrend_multiplier``). Keeping them equal was a job for
    hand-written parity tests, which can only detect drift after it happens.

    A :class:`StrategySpec` is the single definition. ``deltabt.rulecore``
    turns it into signal arrays; the backtester feeds those to
    ``deltabt.engine.run_backtest`` and the bot reads their last row. Parity
    stops being a property that is tested and becomes one that is structural.

WHY IT LIVES IN ``deltabt`` AND NOT ``app``
    ``app`` imports ``deltabt``; the reverse would be a cycle. The shared
    definition therefore has to sit on the ``deltabt`` side.

WHAT A SPEC DELIBERATELY DOES NOT CONTAIN
    Risk sizing, exposure limits, circuit breakers, symbol universe and
    execution settings. Those belong to the runtime that consumes signals, not
    to the rule that generates them, and the research programme's own lesson 3
    is that mixing them corrupts measurement -- a breaker changes which trades
    exist, so a spec carrying one would not mean the same thing in a backtest
    as it does live.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

#: Williams %R rule vocabulary. Named rather than free-form because the four
#: candidate %R rules in this repository differ by orders of magnitude in
#: firing rate, so an unrecognised string must fail loudly rather than fall
#: back to a default that quietly changes the strategy.
#: ``banded_fade``  the ``banded`` conditions with the SIDES SWAPPED: a long
#:                  requires the banded SHORT condition (upper half, falling)
#:                  and a short the banded LONG condition (lower half, rising).
#:                  Exists because on BTC/ETH/SOL/XRP the forward move after a
#:                  ``banded`` signal is NEGATIVE at 12-96 bars in both halves
#:                  of 2025-01..2026-08 (scripts/fade_walkforward.py). It is a
#:                  new vocabulary VALUE rather than a new field so no existing
#:                  spec's ``config_hash`` moves.
WPR_RULES = ("variant_a", "cross_levels", "banded", "banded_fade", "none")

#: Entry trigger vocabulary.
#:
#: ``edge``   fire only on the bar the complete setup goes FALSE -> TRUE.
#: ``level``  fire on every bar the setup is true. V1 did this and one setup
#:            could be entered repeatedly; it was masked by a position limit
#:            refusing the repeats at the risk gate, which is not the same as
#:            not having the defect.
TRIGGERS = ("edge", "level")

#: Stop-placement vocabulary.
#:
#: ``leg_extreme``  the extreme since the Supertrend last flipped, bounded by
#:                  the Supertrend line itself. The frozen H-WPR-1 stop. It is
#:                  the only mode that can be undeterminable from a bounded
#:                  window -- see ``rulecore.leg_truncated``.
#: ``atr``          a multiple of ATR on the primary timeframe.
#: ``fixed_pct``    a fixed fraction of entry. The flip arm's choice, made so
#:                  the stop cannot be an artifact of where a bounded window
#:                  starts and so cost_r is known in advance.
STOPS = ("leg_extreme", "atr", "fixed_pct")

#: Supertrend gate vocabulary.
#:
#: ``off``      Supertrend does not gate this timeframe.
#: ``aligned``  direction agrees with the trade. Pine returns direction -1 for
#:              an UPTREND, so bullish is ``direction < 0``.
#: ``flip``     direction agrees AND changed on this bar. Distinct from
#:              ``aligned`` in firing rate by orders of magnitude: alignment is
#:              a level condition true for a whole leg, a flip is one bar.
#: ``counter``  direction DISAGREES with the trade: a long needs a bearish
#:              Supertrend, a short a bullish one. Pairs with ``banded_fade``
#:              to express the exact inverse of ``manual_scalp_st_banded``.
SUPERTREND_MODES = ("off", "aligned", "flip", "counter")


@dataclass(frozen=True)
class TimeframeRules:
    """The gate applied on one timeframe.

    Every field is a switch rather than a threshold wherever the underlying
    arms disagree by presence rather than by value. ``adx_min = None`` is the
    ATR arm's rule: DI direction is used but the ADX STRENGTH threshold is not,
    which widens the gate considerably because ``+DI > -DI`` is satisfied on
    nearly any bar with a directional lean.
    """

    supertrend: str = "aligned"
    #: Require +DI/-DI dominance in the trade's direction.
    di: bool = True
    #: Minimum ADX. ``None`` means no strength gate at all -- distinct from
    #: ``0.0``, which is a gate that every finite ADX passes but which still
    #: rejects a NaN during warm-up.
    adx_min: float | None = 25.0
    wpr_rule: str = "variant_a"
    #: Levels for the %R rules. ``variant_a`` reads ``wpr_long_level`` as the
    #: floor a rising %R must be above; ``cross_levels`` reads both as the
    #: band edges a %R must cross out of.
    #:
    #: ``banded`` reads them as the OUTER edges and splits at their MIDPOINT,
    #: so a long must sit in the lower half and a short in the upper half.
    #: With the defaults that midpoint is -50.0, which is the whole point: it
    #: needs a third number and there is no third field, so it is derived
    #: rather than added. Adding a field would move every spec's
    #: ``config_hash`` and orphan the recorded sweeps -- the same trap
    #: app/config/variants.py records V1 falling into.
    wpr_long_level: float = -80.0
    wpr_short_level: float = -20.0

    def validate(self) -> None:
        if self.wpr_rule not in WPR_RULES:
            raise ValueError(f"wpr_rule must be one of {WPR_RULES}, got {self.wpr_rule!r}")
        if self.supertrend not in SUPERTREND_MODES:
            raise ValueError(
                f"supertrend must be one of {SUPERTREND_MODES}, got {self.supertrend!r}")
        if self.adx_min is not None and self.adx_min < 0:
            raise ValueError(f"adx_min must be non-negative or None, got {self.adx_min}")

    @property
    def enabled(self) -> bool:
        """Whether this timeframe constrains anything at all.

        The flip arm sets primary and confirmation to the same timeframe and
        disables the second gate entirely; an all-off :class:`TimeframeRules`
        must then contribute nothing rather than a vacuous ``True`` that still
        costs an alignment lookup.
        """
        return (self.supertrend != "off" or self.di
                or self.adx_min is not None or self.wpr_rule != "none")


@dataclass(frozen=True)
class StrategySpec:
    """A complete, resolved rule set with a content hash.

    The hash covers every field, so any edit -- including one that looks
    cosmetic -- produces a different identity. That is the intended way to
    change a running strategy: signals recorded afterwards are distinguishable
    in the audit trail from signals recorded before, rather than a code change
    that hides itself.
    """

    name: str

    #: Bar sizes in minutes. Candles are always stored at 1m and resampled, so
    #: both are free to sweep. This is the most consequential pair in the whole
    #: configuration: cost per R is set by how far the stop sits from price,
    #: which scales with bar range, so changing it changes the economics before
    #: it changes anything about the signal.
    primary_minutes: int = 5
    confirm_minutes: int = 1

    # --- indicator parameters, shared by both timeframes -------------------
    #: NOTE THE ARGUMENT ORDER at the call site: ``ind.supertrend`` takes
    #: (factor, atr_period), matching Pine. Transposing them yields a factor-10
    #: Supertrend on a 2-period ATR, which barely ever flips.
    st_multiplier: float = 2.0
    st_atr_period: int = 10
    di_period: int = 14
    #: Wilder smoothing applied to DX to obtain ADX. The research constant is
    #: 28, not 14.
    adx_period: int = 28
    wpr_period: int = 140

    primary: TimeframeRules = field(default_factory=TimeframeRules)
    confirm: TimeframeRules = field(default_factory=TimeframeRules)

    trigger: str = "edge"
    stop: str = "leg_extreme"
    #: Read only when ``stop = "atr"``.
    stop_atr_period: int = 10
    stop_atr_multiplier: float = 2.0
    #: Read only when ``stop = "fixed_pct"``.
    stop_pct: float = 0.015

    target_r: float = 2.0
    #: Reject a setup whose stop is further than this fraction of price.
    max_stop_pct: float = 0.05

    def validate(self) -> None:
        self.primary.validate()
        self.confirm.validate()
        if self.trigger not in TRIGGERS:
            raise ValueError(f"trigger must be one of {TRIGGERS}, got {self.trigger!r}")
        if self.stop not in STOPS:
            raise ValueError(f"stop must be one of {STOPS}, got {self.stop!r}")
        if self.primary_minutes < 1 or self.confirm_minutes < 1:
            raise ValueError("timeframes must be at least one minute")
        if self.confirm.enabled and self.primary_minutes % self.confirm_minutes:
            # Alignment picks the last confirmation bar closing at or before
            # the primary close. If the sizes do not divide, that bar's close
            # lands mid-primary-bar and the gate silently reads stale context.
            raise ValueError(
                f"primary_minutes ({self.primary_minutes}) must be a multiple of "
                f"confirm_minutes ({self.confirm_minutes}) while the confirmation "
                f"gate is enabled")
        if self.target_r <= 0:
            raise ValueError(f"target_r must be positive, got {self.target_r}")
        if self.stop == "fixed_pct" and not 0 < self.stop_pct < 1:
            raise ValueError(f"stop_pct must be in (0, 1), got {self.stop_pct}")
        for rules in (self.primary, self.confirm):
            if rules.wpr_rule != "none" and not (
                -100.0 < rules.wpr_long_level < rules.wpr_short_level < 0.0
            ):
                raise ValueError(
                    f"%R levels must satisfy -100 < long ({rules.wpr_long_level}) "
                    f"< short ({rules.wpr_short_level}) < 0")

    @property
    def warmup_bars(self) -> int:
        """Bars each timeframe needs before any value is trustworthy."""
        return max(
            self.wpr_period,
            self.di_period + 2 * self.adx_period,
            self.st_atr_period,
            self.stop_atr_period,
        ) + 5

    @property
    def window_bars(self) -> int:
        """1m bars of history to retain and hand the evaluator.

        In MINUTES, not primary bars, because that is what the candle buffer
        and the backfill are sized in -- and the two differ by a factor of
        ``primary_minutes``. A 145-bar warm-up is 145 minutes at 1m and 24 DAYS
        at 240m, which is the difference between a buffer that works and a bot
        that never emits a signal.

        Three times the warm-up: enough that Wilder smoothing has long since
        forgotten its seed at the tail, with room for missing minutes.
        """
        return max(3 * self.warmup_bars * self.primary_minutes, 2_000)

    @property
    def version(self) -> str:
        """Identity string for the audit trail: name plus content hash."""
        return f"{self.name}@{self.config_hash[:12]}"

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def config_hash(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "StrategySpec":
        d = dict(d)
        for key in ("primary", "confirm"):
            if key in d and isinstance(d[key], dict):
                d[key] = TimeframeRules(**d[key])
        spec = cls(**d)
        spec.validate()
        return spec
