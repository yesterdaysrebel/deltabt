"""H-WPR-1 Arm A with the FROZEN research timeframe architecture.

WHY THIS IS A SEPARATE MODULE AND NOT A FLAG ON ``rules.py``

    ``app/strategy/rules.py`` evaluates on closed 5m bars and derives the
    structural stop from 5m data. The frozen research does the opposite: it
    decides on closed 1m bars, derives the stop from the 1m Supertrend and the
    1m leg extreme, and uses 5m only as a CONFIRMED REGIME FILTER shifted one
    5m bar onto the 1m grid. The 2026-08-18 correctness audit measured the
    consequence -- the 5m stop runs 2.5-3.5x wider than the 1m stop.

    Those are different rule sets, not two settings of one. V3 is running and
    must not change, so this lives beside it and rules.py is untouched.

NOTHING IS REIMPLEMENTED HERE

    Every indicator, every condition and the arm composition come from
    ``deltabt.research.hwpr`` by direct call -- ``build_conditions`` and
    ``arm_signals``. That is the whole point: parity with the frozen module is
    guaranteed by construction rather than argued from a reading of it. This
    module contributes exactly three things the research does differently
    because it is a backtest and this is not:

      1. it reads only the LAST closed bar rather than a whole array;
      2. it works on a bounded window rather than full history;
      3. it reports an ``Explanation``, so the runner's persistence, risk
         engine and broker need no knowledge of which strategy produced it.

THE ENTRY-PRICE DIVERGENCE, STATED UP FRONT

    ``hwpr._simulate`` computes ``r_price`` from ``o[j]``, the open of the bar
    AFTER the signal. That is causally sound in a backtest and unavailable to a
    live evaluator, which is standing at the close of bar i. So this module
    reports ``entry_price`` as that close, and the realised risk-per-unit is
    whatever the fill produces. Signals, stop PRICES, direction and timestamps
    reproduce exactly; the risk-per-unit and therefore the target price differ
    by the open-to-close gap. This is a property of live execution, not a
    defect, and the parity harness measures it rather than hiding it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.config.strategy import StrategyConfig
from app.strategy.explanation import LONG, SHORT, Explanation, Outcome
from deltabt.research import hwpr

#: The frozen arm. H-WPR-1's pre-registered baseline.
ARM = "A"
WPR_VARIANT = "A"

#: The frozen research's own stop cap -- `hwpr.run` defaults to 0.05 and the
#: H-WPR-1 run used that default. NOT V3's 0.10: reproducing the research means
#: reproducing its admission rule too.
FROZEN_MAX_STOP_PCT = 0.05


def warmup_bars() -> int:
    """The research's own warm-up, read from it rather than restated."""
    return max(hwpr.WPR_PERIOD,
               hwpr.DI_PERIOD + 2 * hwpr.ADX_PERIOD,
               hwpr.ST_PERIOD) + 5


def _at(arr, i: int) -> float:
    v = arr[i]
    return float(v) if v is not None else float("nan")


def evaluate_frozen(one_minute: pd.DataFrame, cfg: StrategyConfig, *,
                    symbol: str,
                    max_stop_pct: float = FROZEN_MAX_STOP_PCT) -> Explanation:
    """Evaluate the frozen rule set on the last closed 1m bar.

    ``one_minute`` is closed 1m bars only; the caller must never include a
    forming bar. The 5m regime is derived inside ``build_conditions`` by
    resampling and shifting, exactly as the research does -- this module never
    resamples on its own, so the two cannot drift apart.
    """
    exp = Explanation(
        symbol=symbol,
        bar_open=int(one_minute["time"].iloc[-1]) if len(one_minute) else 0,
        primary_timeframe="1m",
        confirmation_timeframe="5m",
        strategy_version=cfg.version,
        strategy_config_hash=cfg.config_hash,
        outcome=Outcome.NO_SETUP,
    )

    need = warmup_bars()
    if len(one_minute) < need:
        exp.outcome = Outcome.SUPPRESSED
        exp.rejection_reason = (
            f"warm-up incomplete: have {len(one_minute)} 1m bars, need {need}")
        return exp

    C = hwpr.build_conditions(one_minute.reset_index(drop=True))
    lo, sh = hwpr.arm_signals(C, ARM, WPR_VARIANT)

    i = len(C["t1"]) - 1
    long_ok, short_ok = bool(lo[i]), bool(sh[i])

    exp.indicators = {"one_minute": {
        "close": _at(C["close"], i), "supertrend": _at(C["st1"], i),
        "wpr": _at(C["wpr"], i),
        "leg_low": _at(C["leg_lo"], i), "leg_high": _at(C["leg_hi"], i),
        "f5_long": bool(C["f5_long"][i]), "f5_short": bool(C["f5_short"][i]),
        "st1_long": bool(C["st1_long"][i]), "st1_short": bool(C["st1_short"][i]),
        "adx1_long": bool(C["adx1_long"][i]), "adx1_short": bool(C["adx1_short"][i]),
        "wpr_long": bool(C["wprA_long"][i]), "wpr_short": bool(C["wprA_short"][i]),
    }}

    # The named conjunction, in the research's own composition order, so a
    # persisted signal says which of the four legs held.
    checks_long = [
        ("regime_5m_confirmed_long", bool(C["f5_long"][i])),
        ("supertrend_1m_bullish", bool(C["st1_long"][i])),
        ("adx_di_1m_long", bool(C["adx1_long"][i])),
        ("wpr_1m_variant_a_long", bool(C["wprA_long"][i])),
    ]
    checks_short = [
        ("regime_5m_confirmed_short", bool(C["f5_short"][i])),
        ("supertrend_1m_bearish", bool(C["st1_short"][i])),
        ("adx_di_1m_short", bool(C["adx1_short"][i])),
        ("wpr_1m_variant_a_short", bool(C["wprA_short"][i])),
    ]

    if long_ok and short_ok:
        exp.outcome = Outcome.SUPPRESSED
        exp.rejection_reason = "long and short both satisfied -- refusing both"
        return exp

    if not (long_ok or short_ok):
        chosen = checks_long if sum(v for _, v in checks_long) >= sum(
            v for _, v in checks_short) else checks_short
        exp.conditions_passed = [n for n, v in chosen if v]
        exp.conditions_failed = [n for n, v in chosen if not v]
        return exp

    checks = checks_long if long_ok else checks_short
    side = LONG if long_ok else SHORT
    exp.direction = side
    exp.conditions_passed = [n for n, _ in checks]

    # ---- the frozen structural stop, from 1m --------------------------------
    # hwpr._simulate: stop = min(s, leg_lo[i]) long / max(s, leg_hi[i]) short,
    # where s = st1[i]. Identical expression, same arrays, same bar.
    st1 = _at(C["st1"], i)
    entry = _at(C["close"], i)          # see the module docstring on entry
    if side == LONG:
        stop = min(st1, _at(C["leg_lo"], i))
    else:
        stop = max(st1, _at(C["leg_hi"], i))

    exp.entry_price, exp.stop_price = entry, stop
    if not np.isfinite(stop) or not np.isfinite(entry):
        exp.outcome = Outcome.SUPPRESSED
        exp.rejection_reason = "stop or entry is not finite at this bar"
        return exp

    risk_per_unit = (entry - stop) if side == LONG else (stop - entry)
    if risk_per_unit <= 0:
        # hwpr treats this as skipped_stop, together with the width rejection.
        exp.outcome = Outcome.REJECTED
        exp.rejection_reason = (
            f"stop distance {risk_per_unit} is not positive (entry {entry}, "
            f"stop {stop})")
        return exp

    stop_pct = risk_per_unit / entry
    exp.stop_distance_pct = 100.0 * stop_pct
    if stop_pct > max_stop_pct:
        exp.outcome = Outcome.REJECTED
        exp.rejection_reason = (
            f"stop {100*stop_pct:.2f}% exceeds max_stop_pct "
            f"{100*max_stop_pct:.2f}%")
        return exp

    exp.target_price = (entry + cfg.target_r * risk_per_unit if side == LONG
                        else entry - cfg.target_r * risk_per_unit)
    exp.reward_risk = cfg.target_r
    exp.detail["risk_per_unit"] = risk_per_unit
    exp.detail["stop_basis"] = (
        "supertrend_1m" if (stop == st1) else "leg_extreme_1m")
    exp.detail["arm"] = ARM
    exp.outcome = Outcome.DETECTED
    return exp


# ---------------------------------------------------------------------------
# IDENTITY
# ---------------------------------------------------------------------------
# StrategyConfig CANNOT express this arm. Its validate() hard-rejects anything
# but 5m primary / 1m confirmation:
#
#     if self.primary_timeframe != "5m" or self.confirmation_timeframe != "1m":
#         raise ValueError("V2 is frozen at 5m primary / 1m confirmation")
#
# Relaxing that would edit a frozen file and move V1/V2/V3's validation
# surface, so this arm carries its OWN config object instead. It exposes the
# three attributes `build_identity` reads -- config_hash, version, to_dict --
# and nothing in app/config/ is touched.

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FrozenHwprConfig:
    """The frozen H-WPR-1 Arm A rule set, as its own identity.

    Every field is transcribed from `deltabt.research.hwpr` at import time
    rather than restated, so a change to the frozen module moves this hash
    instead of silently disagreeing with it.
    """

    name: str = "H-WPR-1-FROZEN-1M"
    arm: str = ARM
    wpr_variant: str = WPR_VARIANT
    primary_timeframe: str = "1m"          # decides
    confirmation_timeframe: str = "5m"     # confirmed regime filter only
    supertrend_atr_period: int = hwpr.ST_PERIOD
    supertrend_multiplier: float = hwpr.ST_MULT
    adx_period: int = hwpr.ADX_PERIOD
    di_period: int = hwpr.DI_PERIOD
    adx_minimum: float = hwpr.ADX_MIN
    wpr_period: int = hwpr.WPR_PERIOD
    target_r: float = 2.0
    max_stop_pct: float = FROZEN_MAX_STOP_PCT
    #: 3000 and not 1500. At 1500 the 5m ADX has not converged enough for the
    #: `>= 25.0` comparison to reproduce the full-history result on every bar:
    #: one signal in 450 disagreed, with adx5 at 25.000203 full-history against
    #: 24.999818 windowed. At 3000 the same sample reproduces 450/450.
    window_bars: int = 3000
    stop_source: str = "1m supertrend and 1m leg extreme"
    entry_rule: str = "signal on closed 1m bar, entry next 1m open"

    def validate(self) -> None:
        """Assert the arm still IS the frozen research configuration.

        Called by the bot before it will start, so a drift between this module
        and `deltabt.research.hwpr` stops the process rather than quietly
        running a rule set that no longer matches the thing it claims to
        reproduce.
        """
        pairs = (
            ("supertrend atr period", self.supertrend_atr_period, hwpr.ST_PERIOD),
            ("supertrend multiplier", self.supertrend_multiplier, hwpr.ST_MULT),
            ("adx period", self.adx_period, hwpr.ADX_PERIOD),
            ("di period", self.di_period, hwpr.DI_PERIOD),
            ("adx minimum", self.adx_minimum, hwpr.ADX_MIN),
            ("wpr period", self.wpr_period, hwpr.WPR_PERIOD),
        )
        for label, mine, frozen in pairs:
            if mine != frozen:
                raise ValueError(
                    f"{label} is {mine} here but {frozen} in "
                    f"deltabt.research.hwpr; this arm exists to reproduce that "
                    f"module and no longer does")
        if self.arm != "A" or self.wpr_variant != "A":
            raise ValueError(
                f"the frozen baseline is Arm A / WPR variant A, got "
                f"{self.arm} / {self.wpr_variant}")
        if (self.primary_timeframe, self.confirmation_timeframe) != ("1m", "5m"):
            raise ValueError(
                f"the frozen arm decides on 1m and confirms on 5m, got "
                f"{self.primary_timeframe}/{self.confirmation_timeframe}")
        if self.max_stop_pct != FROZEN_MAX_STOP_PCT:
            raise ValueError(
                f"max_stop_pct is {self.max_stop_pct}; the research used "
                f"{FROZEN_MAX_STOP_PCT} and reproducing it means reproducing "
                f"its admission rule too")
        if self.target_r <= 0:
            raise ValueError("target_r must be > 0")
        # Below this the 5m ADX has not converged enough for `>= 25.0` to
        # reproduce the full-history comparison; measured at 1500, where one
        # signal in 450 disagreed. See reports/hwpr1_frozen_paper_readiness.md.
        if self.window_bars < 3000:
            raise ValueError(
                f"window_bars={self.window_bars} is below the 3000 at which "
                f"parity with the frozen module was established")

    @property
    def version(self) -> str:
        return f"{self.name}@{self.config_hash}"

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def config_hash(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


FROZEN_1M = FrozenHwprConfig()
