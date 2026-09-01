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
    # --- the TARGET, which nothing had ever varied ------------------------
    #
    # gross_r = p*T - (1-p)*1 has two free terms and roughly 1,150 cells of
    # entry-filter and timeframe work attacked only p. Holding T at 2.0 was
    # never a finding, it was a default.
    #
    # In-sample over 4 majors at 15m/5m, pooled across stop widths and holds,
    # gross_r by target: 1.0R +0.038, 2.0R +0.032, 3.0R +0.057, 4.0R +0.099 --
    # and 4R beats 2R on BTCUSD, ETHUSD, SOLUSD and XRPUSD INDEPENDENTLY. The
    # exit decomposition says why: at a 2R cap 31.8% of trades reach target
    # and 62.3% stop, when gross break-even alone needs 32.9%. The cap is
    # truncating the right tail of a trend rule.
    #
    # THAT IS AN IN-SAMPLE PATTERN FOUND BY SWEEPING, which is exactly how the
    # XRPUSD@45m cell appeared before it dissolved. It is here to be walked
    # forward, not because it is believed.
    "atr_t3": dict(
        desc="atr_arm with a 3R target instead of 2R",
        primary=_tf_rules(supertrend="aligned", di=True, adx_min=None,
                          wpr_rule="variant_a"),
        confirm=_tf_rules(supertrend="aligned", di=False, adx_min=None,
                          wpr_rule="variant_a"),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=2.0,
                  target_r=3.0),
    ),
    "atr_t4": dict(
        desc="atr_arm with a 4R target -- the best in-sample gross measured",
        primary=_tf_rules(supertrend="aligned", di=True, adx_min=None,
                          wpr_rule="variant_a"),
        confirm=_tf_rules(supertrend="aligned", di=False, adx_min=None,
                          wpr_rule="variant_a"),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=2.0,
                  target_r=4.0),
    ),
    # --- the operator's own hand-traded style, encoded -------------------
    #
    # NOT A NEW HYPOTHESIS. This is the configuration recovered from 165
    # hand-placed round trips on the seven symbols (out/manual/), so that the
    # thing traded by hand and the thing the backtester scores are one
    # StrategySpec rather than two descriptions that drift apart.
    #
    # WHY NO SUPERTREND AND NO DI. Both were measured against the operator's
    # own winners and losers and carry no information: Supertrend agreed with
    # 39% of winners and 39% of losers, DI with 51% and 54%. 61% of the trades
    # were taken AGAINST the Supertrend direction and those netted +2,223.
    # Requiring alignment would encode a gate the record says is inert.
    #
    # WHY target_r=1.0. The winners cluster hard at 0.5-1.5R (77 of 83) -- the
    # money is taken near 1R, not held for 2R. This is also the whole of the
    # "50% win rate vs the bot's 29%" gap: a 1R target mechanically produces
    # ~50% (measured 0.496-0.510 across six families), so the hit rate is the
    # target, not entry skill.
    #
    # WHY stop_atr_multiplier=4.0. Stop width is the ONE discriminator that
    # survived: winners were set at a median 150 bps, losers at 122 bps. Tight
    # stops got hit. It is also the only lever that moves cost_r, which is the
    # binding constraint (cost_r = round_trip / stop_pct).
    #
    # WHAT THIS IS WORTH, STATED PLAINLY. Pooled over the seven symbols it
    # scores about -0.04R per trade under the operator's real execution
    # (maker entry, taker stop exit). It is NOT an edge. Its value is that a
    # bot risking 0.5% with a 3x leverage cap cannot be liquidated, and
    # liquidation is what actually cost the money: 165 clean trades made
    # +2,297 while 44 liquidations lost -9,172.
    "manual_scalp": dict(
        desc="the operator's hand-traded style: %R alone, 1R target, wide stop",
        primary=_tf_rules(wpr_rule="variant_a"),
        confirm=_tf_rules(),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=4.0,
                  target_r=1.0, max_stop_pct=0.10),
    ),
    # manual_scalp with the SAME %R rule required on the confirmation
    # timeframe as well. manual_scalp itself gates the primary only -- its
    # confirm rules are all off -- so this family exists to measure how many
    # of its signals ALSO satisfy the rule on 1m. The operator described
    # trading "5 min and 1 min as confirmation, sometimes just the 1 min";
    # this is the strict reading of that, and manual_scalp is the loose one.
    "manual_scalp_both": dict(
        desc="manual_scalp, but %R must agree on the confirmation timeframe too",
        primary=_tf_rules(wpr_rule="variant_a"),
        confirm=_tf_rules(wpr_rule="variant_a"),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=4.0,
                  target_r=1.0, max_stop_pct=0.10),
    ),
    # manual_scalp with a CEILING on %R. `variant_a` is a floor with nothing
    # above it, so a long is valid at %R = -9 -- price at the top of the
    # 140-bar range. The live arm did exactly that on AKEUSD at 2026-09-01
    # 00:05Z: %R -9.35, close 0.0081435 against a leg high of 0.0081825, and
    # ATR had doubled since the previous entry so the stop went 2.46% -> 4.67%.
    # `banded` keeps the direction requirement and adds the midpoint ceiling.
    "manual_scalp_banded": dict(
        desc="manual_scalp with the %R midpoint ceiling (banded, not variant_a)",
        primary=_tf_rules(wpr_rule="banded"),
        confirm=_tf_rules(),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=4.0,
                  target_r=1.0, max_stop_pct=0.10),
    ),
    # manual_scalp plus Supertrend ALIGNMENT and nothing else. No DI, no ADX,
    # no confirmation timeframe -- the operator's question was "just supertrend
    # and wpr". `atr_arm` is the nearest existing family but carries DI=True,
    # so the Supertrend contribution cannot be isolated from it there.
    "manual_scalp_st": dict(
        desc="manual_scalp plus Supertrend alignment on the primary; no DI, no ADX",
        primary=_tf_rules(supertrend="aligned", wpr_rule="variant_a"),
        confirm=_tf_rules(),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=4.0,
                  target_r=1.0, max_stop_pct=0.10),
    ),
    # manual_scalp_st with the %R CEILING as well: Supertrend alignment AND a
    # band-midpoint bound on where in the range an entry may happen.
    #
    # WHY IT EXISTS. On 2026-09-01 09:05Z the live manual_scalp_st arm opened
    # four shorts in one bar at %R -93.51, -93.77, -92.68 and -90.61, with
    # price within a hair of the leg low (BTCUSD entered 77,849 against a leg
    # low of 77,755). variant_a's short leg is `%R < -20 AND falling` -- a
    # ceiling with no floor -- so -93 qualifies exactly as -25 does. The same
    # bar approved a long at %R -14.53, the mirror case. `banded` refuses five
    # of those six signals.
    #
    # In their defence ADX ran 31-46 and Supertrend was bearish, so these are
    # strong-downtrend continuation shorts that a trend follower takes on
    # purpose. The operator's hand-traded style does not, and this family is
    # meant to encode that style.
    "manual_scalp_st_banded": dict(
        desc="manual_scalp plus Supertrend alignment AND the %R midpoint ceiling",
        primary=_tf_rules(supertrend="aligned", wpr_rule="banded"),
        confirm=_tf_rules(),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=4.0,
                  target_r=1.0, max_stop_pct=0.10),
    ),
    # THE OPERATOR'S ACTUAL SETUP, as stated on 2026-09-01 -- and it is not
    # what manual_scalp or manual_scalp_st encode.
    #
    #   %R length 140          matches; there is no indicator mismatch
    #   %R below -80 = LONG    mean reversion, near the OPPOSITE of variant_a
    #   Supertrend = TRIGGER   enter on the FLIP, not a direction filter
    #
    # `cross_levels` is that entry: long as %R leaves oversold, short as it
    # leaves overbought, a one-bar event -- so the trigger is `level`, not
    # `edge`. This is `flip_wide` with the operator's 1R target instead of 2R.
    #
    # A Supertrend FLIP confluent with a %R band cross on the SAME bar is a
    # rare conjunction. Expect very few trades; that is the shape of the rule,
    # not a fault in it, and it is the first thing to check in the results.
    "manual_flip": dict(
        desc="the operator's stated setup: ST flip trigger, %R band cross, 1R",
        primary=_tf_rules(supertrend="flip", wpr_rule="cross_levels"),
        confirm=_tf_rules(),
        over=dict(trigger="level", stop="atr", stop_atr_multiplier=4.0,
                  target_r=1.0, max_stop_pct=0.10),
    ),
    # THE OPERATOR'S SETUP AS FINALLY SPECIFIED, 2026-09-01, after three wrong
    # guesses on my part. Stated directly: "if wpr is banded and supertrend
    # agrees I take trades", %R length 140, Supertrend 10/2.0, no ADX, no DI,
    # 1m as a confirmation timeframe, 1R target.
    #
    # WHAT EACH EARLIER GUESS GOT WRONG, so none is repeated:
    #   manual_scalp      %R variant_a alone -- a floor with no ceiling, so it
    #                     bought at %R -9 and sold at %R -93.
    #   manual_scalp_st   added Supertrend as a filter but kept variant_a, so
    #                     it still sold four majors at %R -90 to -94 in one bar.
    #   manual_flip       read Supertrend as a TRIGGER. It is a FILTER.
    #
    # `banded` is the piece that was missing throughout: it bounds WHERE in the
    # range an entry may happen, which is the objection the operator raised
    # three separate times before I measured the right thing.
    "manual_v2": dict(
        desc="operator's stated setup: ST agrees + %R banded, both timeframes, 1R",
        primary=_tf_rules(supertrend="aligned", wpr_rule="banded"),
        confirm=_tf_rules(wpr_rule="banded"),
        over=dict(trigger="edge", stop="atr", stop_atr_multiplier=4.0,
                  target_r=1.0, max_stop_pct=0.10),
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


def build_spec(family: str, primary_minutes: int,
               confirm_minutes: int | None = None,
               stop_atr_multiplier: float | None = None,
               target_r: float | None = None) -> StrategySpec:
    """Build a family's spec at ``primary_minutes``.

    ``confirm_minutes`` overrides the constant 5:1 ratio. It exists so the
    confirmation timeframe can be held FIXED while the primary widens, which
    is a different question from the one the ratio answers: the ratio asks
    "what does this family do at scale", a fixed confirmation asks "what does
    a 60m primary confirmed by the 5m chart I actually watch do".

    The default is unchanged, so every spec built without this argument keeps
    the hash it had.
    """
    f = FAMILIES[family]
    if confirm_minutes is None:
        confirm_minutes = max(1, primary_minutes // CONFIRM_RATIO)
        if primary_minutes % confirm_minutes:
            confirm_minutes = 1
    elif confirm_minutes > primary_minutes or primary_minutes % confirm_minutes:
        raise ValueError(
            f"confirm_minutes={confirm_minutes} must divide and not exceed "
            f"primary_minutes={primary_minutes}; a confirmation bar that does "
            f"not tile the primary aligns to a different instant on each bar")
    spec = StrategySpec(
        name=f"{family}@{primary_minutes}m",
        primary_minutes=primary_minutes,
        confirm_minutes=confirm_minutes,
        primary=f["primary"],
        confirm=f["confirm"],
    )
    spec = replace(spec, **f["over"])
    # Widening the stop is the one lever that moves cost_r DIRECTLY:
    # cost_r = round_trip_rate / stop_pct, so doubling the stop halves it.
    # Whether that helps is not obvious and must be measured, because R is the
    # unit gross_r is denominated in -- a fixed price edge is worth half as
    # many R when R doubles, so gross and cost may simply shrink together and
    # converge on zero rather than on profit.
    #
    # Only meaningful for ATR-stop families; a fixed-percentage stop ignores
    # it, and saying so beats silently doing nothing.
    if stop_atr_multiplier is not None:
        if f["over"].get("stop") != "atr":
            raise ValueError(
                f"family {family!r} does not use an ATR stop "
                f"(stop={f['over'].get('stop')!r}), so stop_atr_multiplier "
                f"would have no effect")
        spec = replace(spec, stop_atr_multiplier=stop_atr_multiplier)
    # The target and the hold cap interact, which is why this is overridable.
    # A 2R target on an 8xATR stop sits 16xATR away; at a 24h cap that is
    # usually unreachable, so the position times out and the wide stop looks
    # worthless. Shrinking the target is the alternative to lengthening the
    # hold, and the two must be compared rather than assumed equivalent.
    if target_r is not None:
        spec = replace(spec, target_r=target_r)
    spec.validate()
    return spec
