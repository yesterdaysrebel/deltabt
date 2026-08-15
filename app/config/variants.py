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

ALL = {"V2": V2, "V1": V1, "V2_LEVEL": V2_LEVEL}

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
    try:
        return ALL[name.upper()]
    except KeyError:
        raise ValueError(
            f"{VARIANT_ENV}={name!r} is not a known variant; "
            f"expected one of {', '.join(sorted(ALL))}") from None
