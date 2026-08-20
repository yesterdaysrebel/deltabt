"""The reversal confluence: %R leaving its extreme AND the Supertrend flipping.

WHAT THIS IS, AND WHY IT EXISTS

    Entirely on the 1m chart, both events on the SAME closed bar:

        LONG   %R(140) crosses UP through -80    and Supertrend(10,2) flips
               bullish
        SHORT  %R(140) crosses DOWN through -20  and Supertrend(10,2) flips
               bearish

    stop     a fixed 1.5% of entry
    target   2R of that stop

    No 5m filter of any kind. No ADX. No DI.

WHY A SEPARATE MODULE, AGAIN

    StrategyConfig.config_hash hashes the whole resolved dataclass, so adding a
    field moves every variant's hash. V1 already lost comparability that way
    once, and V3's hash is pinned in monitor.yml, in deploy.yml and in tests.
    So this arm carries its own config object and its own evaluator, exactly as
    the ATR arm does, and touches neither app/config/strategy.py nor
    app/strategy/rules.py.

WHY THIS PARTICULAR RULE, OUT OF 213 MEASURED

    Three readings of the same idea were measured, and only this one is a
    reversal:

      run_user_rule    %R cross + Supertrend STATE. State holds for dozens of
                       consecutive bars, so it fires deep inside a move that
                       already happened -- a continuation entry wearing a
                       reversal's clothes. 0 of 22 settings positive.
      run_flip_entry   the flip alone, %R dropped. Takes every false flip, and
                       on a 1m chart in chop most flips are false. Entering ON
                       the flip was the worst of 30 cells at every reward
                       ratio.
      THIS             both, together. Beat either component alone at every
                       matched setting -- -0.149/-0.109 against -0.175/-0.129
                       at a 1% stop and 2R.

WHY 1.5% AND 2R SPECIFICALLY

    Not because they are the best numbers. At 2R the stop-width sweep reads:

        SL 1.0%   train -0.1485   valid -0.1086     agree
        SL 1.5%   train -0.0988   valid -0.0583     agree
        SL 2.0%   train -0.0247   valid -0.1684     disagree
        SL 3.0%   train +0.0192   valid -0.0232     disagree in sign

    3.0% has the best training number and reverses on validation, which is what
    selection looks like. 1.5% is the WIDEST stop whose two windows still agree,
    on 795 validation trades, and cost falls with width -- cost_r x stop_pct is
    flat at 0.159 across a twentyfold range. Agreement across windows is the
    property demanded of every other result in this investigation and it decides
    this one too.

WHAT IS EXPECTED TO HAPPEN, WRITTEN DOWN BEFORE IT RUNS

    About -0.07R per trade. The measured win rate is 34.6%/34.9% against the
    33.3% a coin flip returns at 2R, so the entry is at best marginally better
    than random and cost does the rest. THIS ARM IS NOT EXPECTED TO EARN.

    It runs because the held-out set is spent -- 213 configurations have seen
    train and validation -- so live paper is the only remaining source of data
    nobody has mined. A pre-registered arm on fresh data is the honest way to
    settle it; a better backtest is not available at any price.
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
class FlipArmConfig:
    """The flip arm's resolved rule set, with its own identity."""

    name: str = "H-FLIP-1"

    #: BOTH ARE 1m, and that is the point. Every other arm in this repository
    #: decides on 5m and confirms on 1m, or decides on 1m with 5m as a regime
    #: filter. This one has no second timeframe at all: the 5m filter was
    #: measured as close to inert -- dropping it alone moved net from -0.350 to
    #: -0.353 on train and IMPROVED validation from -0.471 to -0.421 -- while
    #: costing 35% of the signals.
    primary_timeframe: str = "1m"
    confirmation_timeframe: str = "1m"

    #: Supertrend. NOTE THE ARGUMENT ORDER at the call site: ind.supertrend
    #: takes (factor, atr_period), matching Pine. Transposing them yields a
    #: factor-10 Supertrend on a 2-period ATR, which barely ever flips.
    supertrend_multiplier: float = 2.0
    supertrend_atr_period: int = 10

    wpr_period: int = 140
    #: The two levels %R must cross. Long leaves oversold, short leaves
    #: overbought -- mirror images, both 80 points wide.
    wpr_long_level: float = -80.0
    wpr_short_level: float = -20.0

    #: 0 means both events on the SAME closed bar. The research swept 0 to 10
    #: bars and the tightest window was best at every reward ratio, decaying
    #: monotonically as it loosened. A window > 0 would also need state carried
    #: between bars, which this evaluator deliberately does not have: it reads
    #: a frame and returns a verdict, so it cannot drift out of step with a
    #: restart.
    confluence_window_bars: int = 0

    #: A fixed fraction of entry. Not structural, not ATR -- so it cannot be an
    #: artifact of where a bounded window starts, and cost_r is known in advance
    #: at 0.159 / 1.5 = 0.106R.
    stop_pct: float = 0.015
    target_r: float = 2.0

    #: Both conditions are one-bar crossings, so the conjunction is already
    #: edge-triggered and cannot repeat while it "stays true" -- there is no
    #: staying true. fire_once would be a no-op and is not offered.
    max_stop_pct: float = 0.10
    window_bars: int = 3000

    def validate(self) -> None:
        if (self.primary_timeframe, self.confirmation_timeframe) != ("1m", "1m"):
            raise ValueError(
                f"this arm is 1m only, got {self.primary_timeframe}/"
                f"{self.confirmation_timeframe}")
        if not -100.0 < self.wpr_long_level < 0.0:
            raise ValueError(f"wpr_long_level out of range: {self.wpr_long_level}")
        if not -100.0 < self.wpr_short_level < 0.0:
            raise ValueError(f"wpr_short_level out of range: {self.wpr_short_level}")
        if self.wpr_long_level >= self.wpr_short_level:
            raise ValueError(
                f"the long level must sit BELOW the short level: a long leaves "
                f"oversold and a short leaves overbought, got "
                f"{self.wpr_long_level} >= {self.wpr_short_level}")
        if self.confluence_window_bars != 0:
            raise ValueError(
                "only a same-bar confluence is implemented; a wider window "
                "needs state carried between bars, which this evaluator has "
                "none of by design")
        if not 0 < self.stop_pct <= self.max_stop_pct:
            raise ValueError(
                f"stop_pct must be in (0, max_stop_pct={self.max_stop_pct}], "
                f"got {self.stop_pct}")
        if self.target_r <= 0:
            raise ValueError("target_r must be > 0")
        if not 0 < self.max_stop_pct <= 1:
            raise ValueError(f"max_stop_pct must be in (0, 1], got {self.max_stop_pct}")
        if self.window_bars < 400:
            raise ValueError(
                f"window_bars={self.window_bars} is too small for a "
                f"{self.wpr_period}-period oscillator plus Supertrend warm-up")

    @property
    def version(self) -> str:
        return f"{self.name}@{self.config_hash}"

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def config_hash(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


FLIP_ARM = FlipArmConfig()


def warmup_bars(cfg: FlipArmConfig) -> int:
    # Supertrend is a recursive band: it converges from any start but does so
    # over tens of bars, so the +5 that suffices for a windowed oscillator is
    # not enough on its own. window_bars carries the real margin.
    return max(cfg.wpr_period, cfg.supertrend_atr_period * 4) + 5


def _finite(*vals) -> bool:
    return all(v is not None and np.isfinite(v) for v in vals)


def _snapshot(df: pd.DataFrame, cfg: FlipArmConfig) -> dict:
    """Indicator values at the last TWO closed bars.

    Two, because every condition here is a CROSSING: a value alone cannot say
    whether it just crossed. Both come from deltabt.indicators, the same numba
    functions the research measured with -- nothing is reimplemented.
    """
    h = df["high"].to_numpy("float64")
    l = df["low"].to_numpy("float64")
    c = df["close"].to_numpy("float64")
    st, direction = ind.supertrend(h, l, c,
                                   cfg.supertrend_multiplier,   # factor FIRST
                                   cfg.supertrend_atr_period)
    wpr = ind.wpr(h, l, c, cfg.wpr_period)
    i = len(df) - 1
    return {
        "close": float(c[i]),
        "supertrend": float(st[i]),
        "direction": float(direction[i]),
        "direction_prev": float(direction[i - 1]),
        "wpr": float(wpr[i]),
        "wpr_prev": float(wpr[i - 1]),
        "bar_open": int(df["time"].iloc[i]),
    }


def _checks(s: dict, cfg: FlipArmConfig) -> tuple[list, list]:
    """The long and short conjunctions at one bar.

    Pine's Supertrend returns direction -1 for an UPTREND, so a bullish flip is
    the bar where direction goes from >= 0 to < 0. Reading that backwards
    inverts every trade the arm takes, which is why it is asserted in tests
    against the research module rather than left to a comment.
    """
    flip_long = s["direction"] < 0 <= s["direction_prev"]
    flip_short = s["direction"] > 0 >= s["direction_prev"]
    cross_up = s["wpr"] > cfg.wpr_long_level >= s["wpr_prev"]
    cross_dn = s["wpr"] < cfg.wpr_short_level <= s["wpr_prev"]

    checks_long = [
        ("supertrend_flipped_bullish", bool(flip_long)),
        (f"wpr_crossed_up_through_{cfg.wpr_long_level:.0f}", bool(cross_up)),
    ]
    checks_short = [
        ("supertrend_flipped_bearish", bool(flip_short)),
        (f"wpr_crossed_down_through_{cfg.wpr_short_level:.0f}", bool(cross_dn)),
    ]
    return checks_long, checks_short


def evaluate_flip(one_minute: pd.DataFrame, cfg: FlipArmConfig, *,
                  symbol: str) -> Explanation:
    """Evaluate the flip arm on one closed 1m bar.

    ``one_minute`` is closed 1m bars. Only the last two rows decide anything,
    and the caller must never include a forming bar.
    """
    cfg.validate()
    exp = Explanation(
        symbol=symbol,
        bar_open=int(one_minute["time"].iloc[-1]) if len(one_minute) else 0,
        primary_timeframe=cfg.primary_timeframe,
        confirmation_timeframe=cfg.confirmation_timeframe,
        strategy_version=cfg.version,
        strategy_config_hash=cfg.config_hash,
        outcome=Outcome.NO_SETUP,
    )

    need = warmup_bars(cfg)
    if len(one_minute) < need:
        exp.outcome = Outcome.SUPPRESSED
        exp.rejection_reason = (
            f"warm-up incomplete: have {len(one_minute)} 1m bars, need {need}")
        return exp

    s = _snapshot(one_minute.tail(cfg.window_bars).reset_index(drop=True), cfg)
    exp.indicators = {"primary": s, "confirmation": s}

    if not _finite(s["direction"], s["direction_prev"], s["wpr"],
                   s["wpr_prev"], s["supertrend"], s["close"]):
        exp.outcome = Outcome.SUPPRESSED
        exp.rejection_reason = "indicator warm-up produced NaN"
        return exp

    checks_long, checks_short = _checks(s, cfg)
    long_ok = all(v for _, v in checks_long)
    short_ok = all(v for _, v in checks_short)

    if long_ok and short_ok:
        # Not reachable with sane levels -- the Supertrend cannot flip both
        # ways on one bar -- but refusing beats guessing if it ever is.
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

    # ---- a fixed-percentage stop, and a target that is target_r OF IT ------
    entry = s["close"]
    risk_per_unit = entry * cfg.stop_pct
    stop = (entry - risk_per_unit) if side == LONG else (entry + risk_per_unit)
    exp.entry_price, exp.stop_price = entry, stop

    if not _finite(risk_per_unit) or risk_per_unit <= 0:
        exp.outcome = Outcome.REJECTED
        exp.rejection_reason = f"stop distance {risk_per_unit} is not positive"
        return exp

    exp.stop_distance_pct = cfg.stop_pct
    # The width cap cannot bind while stop_pct <= max_stop_pct, which validate()
    # enforces. It is still evaluated rather than assumed, because "cannot
    # happen" and "is not checked" have been the same sentence before.
    if cfg.stop_pct > cfg.max_stop_pct:
        exp.outcome = Outcome.REJECTED
        exp.rejection_reason = (
            f"stop {100*cfg.stop_pct:.2f}% exceeds max_stop_pct "
            f"{100*cfg.max_stop_pct:.2f}%")
        return exp

    target = (entry + cfg.target_r * risk_per_unit if side == LONG
              else entry - cfg.target_r * risk_per_unit)
    exp.target_price = target
    exp.reward_risk = cfg.target_r
    exp.detail["risk_per_unit"] = risk_per_unit
    exp.detail["stop_rule"] = f"fixed {100*cfg.stop_pct:.2f}% of entry"
    exp.outcome = Outcome.DETECTED
    return exp
