"""H-WPR-1 entry conditions with an ATR stop and an ATR-derived 2R target.

WHY THIS IS A SEPARATE MODULE AND NOT FIELDS ON StrategyConfig

    StrategyConfig.config_hash hashes the whole resolved dataclass, so ADDING A
    FIELD MOVES EVERY VARIANT'S HASH -- V1, V2, V2_LEVEL and V3 alike. That is
    not hypothetical: app/config/variants.py records V1 moving off
    d7837e445bc74781 for exactly that reason when confirm_wpr and fire_once
    were added, and it cost the comparability of every signal recorded before
    the change.

    V3 is pinned at 11461f2a11a96f8a in monitor.yml, in the deploy workflow and
    in tests. So this arm carries its own config object and its own evaluator,
    and app/config/strategy.py and app/strategy/rules.py are not touched.

WHAT DIFFERS FROM V3, AND IT IS MORE THAN THE STOP

    5m entry     Supertrend(2.0, ATR 10) + DI direction + WPR(140) > -80 rising
                 NO ADX THRESHOLD -- V3 requires adx >= 25; this does not.
    1m confirm   Supertrend + WPR(140) Variant A
                 NO ADX/DI -- V3 confirms on ADX/DI and NOT on WPR. This is
                 the mirror image, and the combination has never been measured.
    stop         2 x ATR(10) from entry, NOT the structural leg extreme
    target       2R of that ATR stop

    Measured consequence, recorded so it is not rediscovered: 2 x ATR(10) runs
    0.62-0.78x the width of V3's structural stop, so round-trip cost as a
    fraction of R RISES -- BTCUSD 0.422 -> 0.562, ETHUSD 0.269 -> 0.346. The
    10% cap also goes nearly inert, refusing 0-2.5% of bars against V3's 16.8%
    on AKEUSD.

WHAT THE ATR STOP BUYS

    It consults no leg extreme, so `leg_truncated` cannot suppress a setup here
    and the stop cannot be an artifact of where a bounded window happens to
    start. Every setup's stop is measured from volatility at the bar it fires
    on.

THIS IS NOT A CLAIM THAT IT EARNS. The entry family measured NO ECONOMIC EDGE
with a gross indistinguishable from zero, and changing an exit redistributes
outcomes around a mean that is already zero. This arm exists to be observed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from app.strategy.explanation import LONG, SHORT, Explanation, Outcome
from deltabt import indicators as ind


@dataclass(frozen=True)
class AtrArmConfig:
    """The ATR arm's resolved rule set, with its own identity."""

    name: str = "H-WPR-1-ATR"
    primary_timeframe: str = "5m"
    confirmation_timeframe: str = "1m"

    #: Supertrend. NOTE THE ARGUMENT ORDER at the call site: ind.supertrend
    #: takes (factor, atr_period), matching Pine. Transposing them yields a
    #: factor-10 Supertrend on a 2-period ATR, which barely ever flips.
    supertrend_multiplier: float = 2.0
    supertrend_atr_period: int = 10

    #: DI direction is used; the ADX STRENGTH THRESHOLD is not. That is the
    #: deliberate difference from V3 and it widens the gate considerably --
    #: `+DI > -DI` is satisfied on nearly any bar with a directional lean.
    di_period: int = 14
    adx_period: int = 28
    require_primary_adx: bool = False

    wpr_period: int = 140

    #: 1m confirmation: Supertrend and Williams %R, NOT ADX/DI.
    confirm_supertrend: bool = True
    confirm_wpr: bool = True
    confirm_adx_di: bool = False

    #: The stop. 2 x ATR(10) from entry.
    stop_atr_period: int = 10
    stop_atr_mult: float = 2.0

    target_r: float = 2.0
    max_stop_pct: float = 0.10
    #: Level-triggered, as V3 is: re-emits every bar the conjunction holds and
    #: the per-symbol position lock absorbs the repeats.
    fire_once: bool = False
    window_bars: int = 1500

    def validate(self) -> None:
        if (self.primary_timeframe, self.confirmation_timeframe) != ("5m", "1m"):
            raise ValueError(
                f"this arm is 5m primary / 1m confirmation, got "
                f"{self.primary_timeframe}/{self.confirmation_timeframe}")
        if self.stop_atr_mult <= 0 or self.stop_atr_period < 1:
            raise ValueError(
                f"stop_atr_mult must be > 0 and stop_atr_period >= 1, got "
                f"{self.stop_atr_mult} / {self.stop_atr_period}")
        if self.target_r <= 0:
            raise ValueError("target_r must be > 0")
        if not 0 < self.max_stop_pct <= 1:
            raise ValueError(f"max_stop_pct must be in (0, 1], got {self.max_stop_pct}")
        if self.window_bars < 400:
            raise ValueError(
                f"window_bars={self.window_bars} is too small for a "
                f"{self.wpr_period}-period oscillator plus Wilder warm-up")

    @property
    def version(self) -> str:
        return f"{self.name}@{self.config_hash}"

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def config_hash(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


ATR_ARM = AtrArmConfig()


def warmup_bars(cfg: AtrArmConfig) -> int:
    return max(cfg.wpr_period,
               cfg.di_period + 2 * cfg.adx_period,
               cfg.supertrend_atr_period,
               cfg.stop_atr_period) + 5


def _finite(*vals) -> bool:
    return all(v is not None and np.isfinite(v) for v in vals)


def _snapshot(df: pd.DataFrame, cfg: AtrArmConfig) -> dict:
    """Indicator values at the LAST closed bar of ``df``.

    Every series comes from deltabt.indicators -- the same numba functions the
    backtester and every research experiment used. Nothing is reimplemented.
    """
    h = df["high"].to_numpy("float64")
    l = df["low"].to_numpy("float64")
    c = df["close"].to_numpy("float64")
    st, direction = ind.supertrend(h, l, c,
                                   cfg.supertrend_multiplier,   # factor FIRST
                                   cfg.supertrend_atr_period)
    plus_di, minus_di, adx = ind.dmi(h, l, c, cfg.di_period, cfg.adx_period)
    wpr = ind.wpr(h, l, c, cfg.wpr_period)
    atr = ind.atr(h, l, c, cfg.stop_atr_period)
    i = len(df) - 1
    return {
        "close": float(c[i]), "supertrend": float(st[i]),
        "direction": float(direction[i]),
        "plus_di": float(plus_di[i]), "minus_di": float(minus_di[i]),
        "adx": float(adx[i]), "wpr": float(wpr[i]),
        "wpr_prev": float(wpr[i - 1]) if len(df) > 1 else float("nan"),
        "atr": float(atr[i]), "bar_open": int(df["time"].iloc[i]),
    }


def _checks(p: dict, c: dict, cfg: AtrArmConfig) -> tuple[list, list]:
    """The long and short conjunctions at one bar."""
    st_long = p["direction"] < 0            # Pine: direction < 0 is bullish
    st_short = p["direction"] > 0
    di_long = p["plus_di"] > p["minus_di"]
    di_short = p["minus_di"] > p["plus_di"]
    # The ADX gate is a no-op in this arm. It is kept as a NAMED entry rather
    # than dropped so a persisted signal's condition list keeps the same shape
    # across arms and stays comparable.
    adx_ok = (p["adx"] >= 25.0) if cfg.require_primary_adx else True

    wpr_rising = _finite(p["wpr_prev"]) and p["wpr"] > p["wpr_prev"]
    wpr_falling = _finite(p["wpr_prev"]) and p["wpr"] < p["wpr_prev"]
    cwpr_rising = _finite(c["wpr_prev"]) and c["wpr"] > c["wpr_prev"]
    cwpr_falling = _finite(c["wpr_prev"]) and c["wpr"] < c["wpr_prev"]

    conf_long = ((c["direction"] < 0 if cfg.confirm_supertrend else True)
                 and ((c["adx"] >= 25.0 and c["plus_di"] > c["minus_di"])
                      if cfg.confirm_adx_di else True)
                 and ((c["wpr"] > -80.0 and cwpr_rising) if cfg.confirm_wpr else True))
    conf_short = ((c["direction"] > 0 if cfg.confirm_supertrend else True)
                  and ((c["adx"] >= 25.0 and c["minus_di"] > c["plus_di"])
                       if cfg.confirm_adx_di else True)
                  and ((c["wpr"] < -20.0 and cwpr_falling) if cfg.confirm_wpr else True))

    checks_long = [
        ("primary_supertrend_bullish", st_long),
        ("primary_adx_ge_min", adx_ok),
        ("primary_di_plus_dominant", di_long),
        ("primary_wpr_above_-80", p["wpr"] > -80.0),
        ("primary_wpr_rising", wpr_rising),
        ("confirm_1m_agrees_long", conf_long),
    ]
    checks_short = [
        ("primary_supertrend_bearish", st_short),
        ("primary_adx_ge_min", adx_ok),
        ("primary_di_minus_dominant", di_short),
        ("primary_wpr_below_-20", p["wpr"] < -20.0),
        ("primary_wpr_falling", wpr_falling),
        ("confirm_1m_agrees_short", conf_short),
    ]
    return checks_long, checks_short


def evaluate_atr(primary: pd.DataFrame, confirmation: pd.DataFrame,
                 cfg: AtrArmConfig, *, symbol: str) -> Explanation:
    """Evaluate the ATR arm on one closed 5m bar.

    ``primary`` is closed 5m bars, ``confirmation`` closed 1m bars. Only the
    last row of each is read, and the caller must never include a forming bar.
    """
    cfg.validate()
    exp = Explanation(
        symbol=symbol,
        bar_open=int(primary["time"].iloc[-1]) if len(primary) else 0,
        primary_timeframe=cfg.primary_timeframe,
        confirmation_timeframe=cfg.confirmation_timeframe,
        strategy_version=cfg.version,
        strategy_config_hash=cfg.config_hash,
        outcome=Outcome.NO_SETUP,
    )

    need = warmup_bars(cfg)
    if len(primary) < need or len(confirmation) < need:
        exp.outcome = Outcome.SUPPRESSED
        exp.rejection_reason = (
            f"warm-up incomplete: have {len(primary)} primary / "
            f"{len(confirmation)} confirmation bars, need {need}")
        return exp

    p = _snapshot(primary.tail(cfg.window_bars).reset_index(drop=True), cfg)
    c = _snapshot(confirmation.tail(cfg.window_bars).reset_index(drop=True), cfg)
    exp.indicators = {"primary": p, "confirmation": c}

    # ATR is in the guard: a NaN there would otherwise produce a zero-width
    # stop, which the positivity check below would report as a rejection rather
    # than as the warm-up problem it actually is.
    if not _finite(p["adx"], p["wpr"], p["direction"], p["supertrend"], p["atr"],
                   c["adx"], c["direction"], c["wpr"]):
        exp.outcome = Outcome.SUPPRESSED
        exp.rejection_reason = "indicator warm-up produced NaN"
        return exp

    checks_long, checks_short = _checks(p, c, cfg)
    long_ok = all(v for _, v in checks_long)
    short_ok = all(v for _, v in checks_short)

    if cfg.fire_once and (long_ok or short_ok):
        prev_p = _snapshot(primary.tail(cfg.window_bars).reset_index(drop=True).iloc[:-1], cfg)
        prev_c = _snapshot(confirmation.tail(cfg.window_bars).reset_index(drop=True).iloc[:-1], cfg)
        pl, ps = _checks(prev_p, prev_c, cfg)
        if (long_ok and all(v for _, v in pl)) or (short_ok and all(v for _, v in ps)):
            exp.outcome = Outcome.NO_SETUP
            exp.rejection_reason = (
                "setup was already true on the previous closed bar; "
                "one signal per FALSE->TRUE transition")
            exp.detail["suppressed_repeat"] = True
            return exp

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

    # ---- the ATR stop, and a target that is 2R OF IT ----------------------
    entry = p["close"]
    risk_per_unit = cfg.stop_atr_mult * p["atr"]
    stop = (entry - risk_per_unit) if side == LONG else (entry + risk_per_unit)
    exp.entry_price, exp.stop_price = entry, stop

    if not _finite(risk_per_unit) or risk_per_unit <= 0:
        exp.outcome = Outcome.REJECTED
        exp.rejection_reason = (
            f"ATR stop distance {risk_per_unit} is not positive "
            f"(atr {p['atr']}, mult {cfg.stop_atr_mult})")
        return exp

    stop_pct = risk_per_unit / entry
    exp.stop_distance_pct = 100.0 * stop_pct
    if stop_pct > cfg.max_stop_pct:
        exp.outcome = Outcome.REJECTED
        exp.rejection_reason = (
            f"stop {100*stop_pct:.2f}% exceeds max_stop_pct "
            f"{100*cfg.max_stop_pct:.2f}%")
        return exp

    exp.target_price = (entry + cfg.target_r * risk_per_unit if side == LONG
                        else entry - cfg.target_r * risk_per_unit)
    exp.reward_risk = cfg.target_r
    exp.detail["risk_per_unit"] = risk_per_unit
    exp.detail["stop_basis"] = f"{cfg.stop_atr_mult}xATR({cfg.stop_atr_period})"
    exp.detail["atr"] = p["atr"]
    exp.outcome = Outcome.DETECTED
    return exp
