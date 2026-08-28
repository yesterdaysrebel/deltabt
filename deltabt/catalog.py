"""Named strategy definitions, addressable by name.

A family here is a complete :class:`~deltabt.spec.StrategySpec` factory. The
point of the catalog is that the SAME name resolves to the SAME rule for the
backtester and for the paper trader, so a configuration that looks worth
forward-testing is promoted by name rather than reimplemented.

Adding a strategy means adding an entry here. It does not mean writing a class
under ``app/strategy/`` or a ``run_hX.py`` under ``deltabt/research/``.
"""

from __future__ import annotations

from dataclasses import replace

from deltabt.spec import StrategySpec, TimeframeRules

#: Confirmation is always this fraction of the primary bar size, floored at 1m.
#: Pinning it at a constant ratio rather than at 1m keeps the confirmation
#: meaningful as the primary widens -- a 240m primary confirmed by a 1m bar is
#: confirmed by noise.
CONFIRM_RATIO = 5


def _tf_rules(**kw) -> TimeframeRules:
    base = dict(supertrend="off", di=False, adx_min=None, wpr_rule="none")
    base.update(kw)
    return TimeframeRules(**base)


#: Each family is a (description, primary rules, confirmation rules, overrides)
#: tuple. Overrides carry trigger/stop choices that are not timeframe gates.
FAMILIES: dict[str, dict] = {
    # --- the three arms this repository actually ran ----------------------
    "hwpr_v2": dict(
        desc="H-WPR-1 Variant A: ST + DI + ADX>=25 + %R rising, both timeframes",
        primary=_tf_rules(supertrend="aligned", di=True, adx_min=25.0, wpr_rule="variant_a"),
        confirm=_tf_rules(supertrend="aligned", di=True, adx_min=25.0, wpr_rule="variant_a"),
        over=dict(trigger="edge", stop="leg_extreme"),
    ),
    "atr_arm": dict(
        desc="DI direction but no ADX strength gate; ATR stop",
        primary=_tf_rules(supertrend="aligned", di=True, adx_min=None, wpr_rule="variant_a"),
        confirm=_tf_rules(supertrend="aligned", di=False, adx_min=None, wpr_rule="variant_a"),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=2.0),
    ),
    "flip": dict(
        desc="Supertrend FLIP confluent with a %R band exit; fixed 1.5% stop",
        primary=_tf_rules(supertrend="flip", wpr_rule="cross_levels"),
        confirm=_tf_rules(),
        over=dict(trigger="level", stop="fixed_pct", stop_pct=0.015, max_stop_pct=0.10),
    ),
    # --- component attribution: strip one gate at a time ------------------
    "trend_pure": dict(
        desc="Trend only: ST + DI + ADX, no oscillator",
        primary=_tf_rules(supertrend="aligned", di=True, adx_min=25.0),
        confirm=_tf_rules(supertrend="aligned", di=True, adx_min=25.0),
        over=dict(trigger="edge", stop="leg_extreme"),
    ),
    "st_only": dict(
        desc="Supertrend alignment alone, both timeframes",
        primary=_tf_rules(supertrend="aligned"),
        confirm=_tf_rules(supertrend="aligned"),
        over=dict(trigger="edge", stop="leg_extreme"),
    ),
    "adx_only": dict(
        desc="ADX/DI alone: no Supertrend, no oscillator",
        primary=_tf_rules(di=True, adx_min=25.0),
        confirm=_tf_rules(di=True, adx_min=25.0),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=2.0),
    ),
    "wpr_only": dict(
        desc="Williams %R rising alone, single timeframe",
        primary=_tf_rules(wpr_rule="variant_a"),
        confirm=_tf_rules(),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=2.0),
    ),
    # --- structural variants ----------------------------------------------
    "hwpr_no_confirm": dict(
        desc="H-WPR-1 gates on the primary only -- what the confirmation buys",
        primary=_tf_rules(supertrend="aligned", di=True, adx_min=25.0, wpr_rule="variant_a"),
        confirm=_tf_rules(),
        over=dict(trigger="edge", stop="leg_extreme"),
    ),
    "st_flip_atr": dict(
        desc="Supertrend flip alone, ATR stop -- pure trend-change entry",
        primary=_tf_rules(supertrend="flip"),
        confirm=_tf_rules(),
        over=dict(trigger="level", stop="atr", stop_atr_multiplier=2.0),
    ),
    "flip_wide": dict(
        desc="Flip confluence with a 4xATR stop instead of a fixed percentage",
        primary=_tf_rules(supertrend="flip", wpr_rule="cross_levels"),
        confirm=_tf_rules(),
        over=dict(trigger="level", stop="atr", stop_atr_multiplier=4.0,
                  max_stop_pct=0.10),
    ),
    "trend_wide_stop": dict(
        desc="Trend stack with a 4xATR stop -- widening to escape the cost wall",
        primary=_tf_rules(supertrend="aligned", di=True, adx_min=25.0, wpr_rule="variant_a"),
        confirm=_tf_rules(supertrend="aligned"),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=4.0,
                  max_stop_pct=0.10),
    ),
    "counter_trend": dict(
        desc="%R band exit AGAINST an aligned Supertrend -- mean reversion",
        primary=_tf_rules(supertrend="aligned", wpr_rule="cross_levels"),
        confirm=_tf_rules(),
        over=dict(trigger="level", stop="atr", stop_atr_multiplier=2.0),
    ),
    # --- does capping the ENTRY ZONE help? --------------------------------
    #
    # `atr_arm` is the arm running in paper now, and its long gate is
    # `%R > -80 AND rising` -- a FLOOR with no ceiling. Anything from -80 to 0
    # qualifies, so buying the literal high of the 140-bar range is a valid
    # signal by construction. The live run on 2026-08-26/27 entered longs at
    # %R of -4.3, -6.9, -8.6, -11.8 and -12.9, which is the top fifth of the
    # range, with a 2xATR stop worth 0.2-0.5% of price. A normal pullback
    # removes that position.
    #
    # This family is `atr_arm` WITH THE CEILING AND NOTHING ELSE CHANGED:
    # same Supertrend, same DI gate, same absent ADX threshold, same edge
    # trigger, same 2xATR stop, same confirmation shape. Only wpr_rule moves,
    # from variant_a to cross_levels, which fires solely on the bar %R crosses
    # UP through the level and so cannot enter at -11.8 at all.
    #
    # `counter_trend` already suggests the answer is no -- it is this idea
    # measured, and its mean gross_r is +0.019 against atr_arm's +0.023 with
    # cost_r at 0.093 for both. But it also drops DI and uses a level trigger,
    # so it does not isolate the ceiling. This family does.
    "atr_pullback": dict(
        desc="atr_arm with a %R CEILING: cross out of the band, no top-of-range entries",
        # THE CEILING GOES ON THE PRIMARY ONLY. Putting cross_levels on BOTH
        # timeframes was the first attempt and it is not this idea: it demands
        # that the 5m and the 1m %R cross -80 on the same bar close, a
        # conjunction of two rare one-bar events. Measured, it produced ZERO
        # trades at 5m and above on every symbol, and 0 at every timeframe on
        # BTCUSD. That is a broken gate, not a negative result. The
        # confirmation keeps atr_arm's variant_a exactly, so the ONLY thing
        # that differs between the two families is the primary %R rule.
        primary=_tf_rules(supertrend="aligned", di=True, adx_min=None,
                          wpr_rule="cross_levels"),
        confirm=_tf_rules(supertrend="aligned", di=False, adx_min=None,
                          wpr_rule="variant_a"),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=2.0),
    ),
    # --- the SOFT ceiling, between atr_arm and atr_pullback ---------------
    #
    # `atr_pullback` answered "does a ceiling help" with the HARDEST possible
    # ceiling: cross_levels fires only on the single bar %R crosses up through
    # -80, which cut trades 9x (15,419 -> 1,773) and did not lower cost per R.
    # A 9x cut is a different strategy, not the same strategy filtered, and it
    # confounds "the ceiling helps" with "there are barely any trades left".
    #
    # This family is the ceiling WITHOUT that collapse: keep variant_a's
    # `rising` requirement, keep firing on every qualifying bar, and simply
    # refuse entries that have already run past the middle of the band. Long
    # in (-80, -50), short in (-50, -20). It is the narrowest possible change
    # to `atr_arm` that tests the actual complaint -- entering at the top of
    # the range -- so a difference here is attributable to the ceiling alone.
    "atr_banded": dict(
        desc="atr_arm with a %R ceiling at the band midpoint: enter while the move is still early",
        primary=_tf_rules(supertrend="aligned", di=True, adx_min=None,
                          wpr_rule="banded"),
        confirm=_tf_rules(supertrend="aligned", di=False, adx_min=None,
                          wpr_rule="variant_a"),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=2.0),
    ),
    # --- does the ADX STRENGTH gate help? ---------------------------------
    #
    # atr_arm deliberately drops V3's `adx >= 25`, keeping only DI DIRECTION,
    # and the module docstring calls that out as widening the gate
    # considerably. On 2026-08-28 the live banded arm closed 9 trades, 8 of
    # them stop-outs, and a bar-by-bar replay of V3's gates over the same
    # window fired ZERO times -- every entry sat in conditions where ADX had
    # not confirmed on both timeframes. One day proves nothing; these families
    # ask the same question of 21 days.
    #
    # THREE FAMILIES BECAUSE "ADD ADX" IS THREE DIFFERENT CHANGES. The gate
    # can go on the primary, on both timeframes, or on the arm that is
    # actually running. Collapsing them would attribute one result to a
    # change nobody made.
    "atr_adx": dict(
        desc="atr_arm plus V3's ADX>=25 on the primary only",
        primary=_tf_rules(supertrend="aligned", di=True, adx_min=25.0,
                          wpr_rule="variant_a"),
        confirm=_tf_rules(supertrend="aligned", di=False, adx_min=None,
                          wpr_rule="variant_a"),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=2.0),
    ),
    "atr_adx_both": dict(
        desc="atr_arm plus ADX>=25 on BOTH timeframes -- the leg that refused today's entries",
        primary=_tf_rules(supertrend="aligned", di=True, adx_min=25.0,
                          wpr_rule="variant_a"),
        confirm=_tf_rules(supertrend="aligned", di=False, adx_min=25.0,
                          wpr_rule="variant_a"),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=2.0),
    ),
    "atr_banded_adx": dict(
        desc="the RUNNING arm (atr_banded) plus ADX>=25 on the primary",
        primary=_tf_rules(supertrend="aligned", di=True, adx_min=25.0,
                          wpr_rule="banded"),
        confirm=_tf_rules(supertrend="aligned", di=False, adx_min=None,
                          wpr_rule="variant_a"),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=2.0),
    ),
}


def build_spec(family: str, primary_minutes: int) -> StrategySpec:
    f = FAMILIES[family]
    confirm_minutes = max(1, primary_minutes // CONFIRM_RATIO)
    if primary_minutes % confirm_minutes:
        confirm_minutes = 1
    spec = StrategySpec(
        name=f"{family}@{primary_minutes}m",
        primary_minutes=primary_minutes,
        confirm_minutes=confirm_minutes,
        primary=f["primary"],
        confirm=f["confirm"],
    )
    spec = replace(spec, **f["over"])
    spec.validate()
    return spec
