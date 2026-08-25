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
