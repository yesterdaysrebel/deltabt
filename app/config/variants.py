"""Named strategy variants, with what each one actually measured.

WHY THIS EXISTS

    V1 is the configuration being forward-tested. The others are kept reachable
    so that switching is a one-line, hash-recorded change rather than an
    archaeology exercise -- and so nobody re-derives a variant that was already
    measured and rejected.

    Every number below comes from the PORTFOLIO simulator: one account, one
    position slot, and the full risk engine (cooldowns, daily trade cap, daily
    loss limit, drawdown halt). Per-symbol backtests overstate this system
    badly -- they allow four concurrent positions where production allows one,
    which turned a t of +8.30 into a negative result once corrected.

    !! THE TRADE COUNTS BELOW ARE OVERSTATED, AND NOT YET RE-MEASURED !!

    Found 2026-08-14 while measuring max_open_positions. The simulator these
    came from resolves a trade's entry and exit inside ONE loop iteration, with
    the UTC day taken from the ENTRY bar. So a position opened Monday and
    stopped out Tuesday books its loss under Monday, and the day roll then
    clears the consecutive-loss counter. The live engine cannot do that:
    roll_day fires when Tuesday's first bar arrives and apply_close increments
    the streak on Tuesday.

    Net effect: the simulator under-applies max_consecutive_losses and finds
    more trades than production would. On TRAIN, V1 measures n=237 that way
    against n=91 on a bar-by-bar simulator that matches the engine's ordering.

    Direction is known; the corrected per-trade R is not, because the
    replacement simulator has not itself been validated against a replay
    through app/risk/engine.py. Treat the RANKING here as usable and the
    absolute figures as provisional until that is done.

    ON CONCURRENCY, measured 2026-08-14 with the corrected simulator:
    raising max_open_positions does NOT buy more trades. Four correlated
    instruments lose together, the account reaches the 10% drawdown halt
    sooner, and the run ends with roughly a third of the trades. VALID and
    TEST both get materially worse per trade at every N > 1. The ceiling is 4
    regardless -- the engine and ux_positions_open_symbol both allow one
    position per symbol.

    net R is per trade, after fees, slippage and funding. TEST is the window
    2026-04-16 -> 2026-08-12, opened once and now spent; treat further
    measurements against it as in-sample.

HOW TO SWITCH

    Change FROZEN in app/config/strategy.py to one of these. The config hash
    moves, which is the point: every signal recorded afterwards is
    distinguishable in the audit trail from every signal recorded before, and
    the bot refuses to trade an experiment whose hash it does not match.
"""

from __future__ import annotations

import os
from dataclasses import replace

from app.config.strategy import FROZEN, StrategyConfig

#: BACKUP. Williams %R on BOTH timeframes, one signal per FALSE->TRUE
#: transition of the complete setup, hard 2R target.
#:
#:   TRAIN  n=85   net -0.1988   -8.33%   halted on drawdown
#:   VALID  n=68   net -0.2321   -7.65%   halted on drawdown
#:   TEST   n=76   net -0.2852  -10.44%   halted on drawdown
#:
#: Shipped 2026-08-14 and withdrawn the same day. The oscillator on 1m was an
#: interpretation added at implementation time -- the specification reads
#: "supertrend is up in both 5m and 1m ... ADX check also", attaching "both
#: timeframes" to Supertrend and ADX, not to Williams %R. That single filter
#: costs roughly two thirds of the trades and measures worse on every window,
#: which also pushes a 30-trade sample about three times further away.
V2 = StrategyConfig()

#: WHAT IS BEING FORWARD-TESTED, by explicit instruction on 2026-08-14.
#: Oscillator on the 5m signal timeframe only, 1m supplying Supertrend and
#: ADX/DI agreement, level-triggered, hard 2R. This is what ran as
#: H-WPR-1-PAPER-AWS-20260813 and it is the best-measured of the family, which
#: is not the same as good.
#:
#:   TRAIN  n=237  net -0.0414   -5.52%   halted on drawdown
#:   VALID  n=94   net -0.1887   -8.66%   halted on drawdown
#:   TEST   n=111  net -0.0169   -1.18%   did not halt
#:
#: Level triggering comes with it, deliberately: this is V1 exactly as it ran.
#: It re-emits on every bar the conjunction holds, and max_open_positions=1
#: refuses those repeats at the risk gate -- which is why suppressing them
#: (V1 with fire_once=True) measured as a ~0.4% change in trade count and is
#: not worth a second variant to carry.
#:
#: NOTE ON ITS HASH. This reconstruction hashes to d7837e445bc74781, NOT the
#: 5a5412369f3823f3 that the live experiment recorded. The rules are identical;
#: the difference is that StrategyConfig has since gained confirm_wpr and
#: fire_once, so the hashed blob has two more keys. The hash answers "was this
#: the same resolved configuration object", and honestly it was not -- the
#: schema moved. Signals from H-WPR-1-PAPER-AWS-20260813 keep their original
#: hash and stay comparable to each other; they are not comparable to anything
#: recorded after this change, which is the correct outcome.
#:
#: validate() rejects confirm_wpr=False only when the name still says V2, so
#: naming this for the rules it actually implements is what makes it reachable.
V1 = StrategyConfig(name="H-WPR-1-VariantA",
                    confirm_wpr=False, fire_once=False)
assert V1.config_hash == FROZEN.config_hash, (
    "V1 is what FROZEN is set to; if these diverge one of them was edited "
    "without the other and the registry no longer describes what runs")

#: V2's entry with level triggering instead of one-shot. Measured
#: indistinguishable from V2 everywhere -- max_open_positions=1 already refuses
#: the repeats at the risk gate, so suppressing them changes ~0.4% of trades.
V2_LEVEL = StrategyConfig(fire_once=False)

#: NOT AVAILABLE, AND DELIBERATELY SO: the trailing-Supertrend exit.
#:
#: It measured best of everything by a wide margin (+0.107R, t=8.30) until the
#: simulator was corrected. The trail was ratcheting onto the Supertrend line
#: even when a flip put that line on the far side of price, so long exits were
#: booked ABOVE the market and shorts BELOW it -- fills nobody could receive.
#: With the stop constrained to prices the market actually offered:
#:
#:   TRAIN  gross +0.3199 -> +0.0293   net +0.1089 -> -0.1822   win 44.6% -> 26.7%
#:   VALID  gross +0.2742 -> -0.0162   net +0.0446 -> -0.2474   win 41.6% -> 24.4%
#:
#: It is the worst exit of the four tested, not the best. Recorded here so the
#: result is not rediscovered, and not implemented because implementing it
#: would mean building a mutable-stop path through the broker, the repository
#: and recovery for a rule measured at -0.18R.
TRAILING_SUPERTREND = None

#: V1's RULES WITH A WIDER STOP CAP. Added 2026-08-15, measured live.
#:
#: In the first six hours of the six-symbol run, AKEUSD and BEATUSD produced 15
#: setups between them and ALL 15 were refused for stop width. Not most --
#: every one. The established symbols sat at 0.18-0.38% and these at 5-21%,
#: so AKEUSD's TIGHTEST stop was 14x wider than ETHUSD's widest. It is genuine
#: volatility rather than bad data: BEATUSD's seven refusals are all shorts
#: with entry falling 0.4534 -> 0.3923, a 13% decline in progress.
#:
#: THE CAP WAS REFUSING THE ONLY SETUPS WHOSE ECONOMICS WORK. Round-trip cost
#: is a fixed fraction of notional, so cost per R falls as R widens:
#:
#:     ETHUSD     26 bps    0.60 R   trading
#:     SOLUSD     27 bps    0.59 R   trading
#:     BTCUSD     77 bps    0.20 R   trading
#:     BANKUSD   176 bps    0.09 R   trading -- and the only winner so far
#:     AKEUSD    506 bps    0.03 R   REFUSED
#:     BEATUSD  2095 bps    0.01 R   REFUSED
#:
#: The panel's conclusion was that cost, not signal, is the binding constraint,
#: and that 0.15R needs a median R near 80 bps. The two refused symbols are the
#: only ones in the universe that clear it.
#:
#: WHY 10% AND NOT HIGHER. Only STOP_LOSS and TAKE_PROFIT close a position --
#: ExitReason.TIME_EXIT exists and is never emitted, and there is no reversal
#: exit -- so a position resolves only by reaching one of its two prices. At a
#: 20.95% stop the 2R target is a 41.9% move, which would leave the position
#: open indefinitely paying funding every four hours. 10% admits 8 of the 15
#: (AKEUSD 6/8, BEATUSD 2/7) with targets of 10-20%, and still refuses the ones
#: that cannot resolve. 25% would admit all 15 and that is the problem, not the
#: point.
#:
#: This is a SEPARATE VARIANT because max_stop_pct is a strategy parameter, not
#: a risk one: changing it in place would move V1 off d7837e445bc74781 and end
#: its comparability with TRAIN/VALID/TEST and with every earlier run.
V3_WIDE_STOP = replace(V1, name="H-WPR-1-VariantA-WideStop", max_stop_pct=0.10)

ALL = {"V2": V2, "V1": V1, "V2_LEVEL": V2_LEVEL, "V3": V3_WIDE_STOP}

#: Accepted spellings for the frozen 1m arm, H-WPR-1-FROZEN-1M. It reproduces
#: deltabt/research/hwpr.py: 1m decides, 5m is a confirmed regime filter, the
#: structural stop comes from the 1m Supertrend and 1m leg extreme, and the cap
#: is the research's own 5% rather than V3's 10%.
_FROZEN_1M_NAMES = frozenset({"FROZEN_1M", "FROZEN1M"})

#: The ATR arm: V3's entry family with a 2 x ATR(10) stop, a 2R target
#: derived from it, no ADX threshold, and 1m confirmation on Supertrend
#: + Williams %R instead of ADX/DI.
_ATR_ARM_NAMES = frozenset({"V4", "ATR", "V4_ATR"})

#: The reversal confluence, 1m only. Not added to ALL for the same reason the
#: other arms are not: ALL feeds the registry whose hashes V1/V2/V2_LEVEL/V3
#: are pinned against, and a new entry there would move them.
#:
#: DELIBERATELY CLAIMS NO "Vn" NAME. The ATR arm took V4 on 2026-08-19, which
#: is already confusing enough -- the stack called "v3" runs the variant called
#: "V4" -- and it forced the negative tests in test_atr_arm and
#: test_variant_and_limits to give up V4 and adopt V5 as their
#: plausible-looking typo. Claiming V5 here would have broken both of them and
#: pushed the same problem onto V6. The sequence is a legacy of the original
#: registry; new arms get names that say what they are.
_FLIP_ARM_NAMES = frozenset({"FLIP", "H_FLIP_1", "H-FLIP-1"})

#: Environment variable selecting which entry of ALL runs. Unset means V1,
#: which is what FROZEN is, so nothing changes for a host that does not set it.
VARIANT_ENV = "DELTABOT_VARIANT"


def resolve_strategy(env: dict | None = None) -> StrategyConfig:
    """The strategy this process should run.

    ONE IMAGE, TWO EXPERIMENTS. Running V1 and V2 concurrently means the same
    container image has to be able to be either, and the alternative -- two
    images built from two branches -- gives up the single git SHA that ties a
    database row to the code that produced it.

    FAIL CLOSED ON AN UNKNOWN NAME. A typo in DELTABOT_VARIANT must not quietly
    fall back to V1: the process would come up healthy, bind to the V2
    experiment, and record V1's signals under V2's identity. That is worse than
    not starting, because it is not visible anywhere until the data is
    analysed. The composite hash check would NOT catch it either -- the
    experiment is created by the same process, from the same wrong config, so
    the two agree with each other and disagree only with the intent.
    """
    env = os.environ if env is None else env
    name = (env.get(VARIANT_ENV) or "").strip()
    if not name:
        return FROZEN

    # The frozen 1m arm is resolved here rather than placed in ALL, for two
    # reasons. It is not a StrategyConfig -- StrategyConfig.validate() rejects
    # any timeframe pair but 5m/1m, and that file is frozen -- so it cannot sit
    # in a dict typed by the others. And importing it pulls in
    # deltabt.research.hwpr and numba, which every V1/V2/V3 process would
    # otherwise pay for at startup without ever using.
    #
    # ALL is deliberately left untouched: V1, V2, V2_LEVEL and V3 keep the
    # registry they had, and their hashes cannot move because of this.
    if name.upper() in _FROZEN_1M_NAMES:
        from app.strategy.frozen_hwpr import FROZEN_1M
        return FROZEN_1M

    # The ATR arm, resolved here for the same reason: it is not a
    # StrategyConfig. Adding its fields to StrategyConfig would move V1, V2,
    # V2_LEVEL and V3's hashes -- V3 is pinned at 11461f2a11a96f8a in
    # monitor.yml, deploy.yml and tests -- so it carries its own config object.
    if name.upper() in _ATR_ARM_NAMES:
        from app.strategy.atr_arm import ATR_ARM
        return ATR_ARM

    if name.upper() in _FLIP_ARM_NAMES:
        from app.strategy.flip_arm import FLIP_ARM
        return FLIP_ARM

    # A SPEC ARM, ADDRESSED BY CATALOG NAME. `SPEC:wpr_only@240` resolves
    # through deltabt.catalog to the same StrategySpec the backtester ran, so
    # the thing deployed is the thing measured -- no second implementation to
    # keep in step, and the config hash in the audit trail is the spec's own.
    #
    # Resolved here rather than placed in ALL for the reason the arms above
    # are: it is not a StrategyConfig, and StrategyConfig.validate() rejects
    # any timeframe pair but 5m/1m.
    if name.upper().startswith("SPEC:"):
        from deltabt.catalog import FAMILIES, build_spec

        body = name.split(":", 1)[1].strip()
        if "@" not in body:
            raise ValueError(
                f"{VARIANT_ENV}={name!r} is malformed; expected "
                f"SPEC:<family>@<primary_minutes>, e.g. SPEC:wpr_only@240")
        family, _, minutes = body.partition("@")
        family = family.strip()
        if family not in FAMILIES:
            raise ValueError(
                f"{VARIANT_ENV}={name!r} names no catalog family. Known: "
                f"{', '.join(sorted(FAMILIES))}")
        try:
            primary = int(minutes)
        except ValueError:
            raise ValueError(
                f"{VARIANT_ENV}={name!r}: {minutes!r} is not a bar size in minutes"
            ) from None
        spec = build_spec(family, primary)
        spec.validate()
        return spec

    try:
        return ALL[name.upper()]
    except KeyError:
        raise ValueError(
            f"{VARIANT_ENV}={name!r} is not a known variant; expected one of "
            f"{', '.join(sorted(set(ALL) | _FROZEN_1M_NAMES | _ATR_ARM_NAMES | _FLIP_ARM_NAMES))}") from None
