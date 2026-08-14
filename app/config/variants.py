"""Named strategy variants, with what each one actually measured.

WHY THIS EXISTS

    V2 is the configuration being forward-tested. The others are kept reachable
    so that switching is a one-line, hash-recorded change rather than an
    archaeology exercise -- and so nobody re-derives a variant that was already
    measured and rejected.

    Every number below comes from the PORTFOLIO simulator: one account, one
    position slot, and the full risk engine (cooldowns, daily trade cap, daily
    loss limit, drawdown halt). Per-symbol backtests overstate this system
    badly -- they allow four concurrent positions where production allows one,
    which turned a t of +8.30 into a negative result once corrected.

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

from app.config.strategy import StrategyConfig

#: WHAT IS BEING FORWARD-TESTED. Williams %R on BOTH timeframes, one signal per
#: FALSE->TRUE transition of the complete setup, hard 2R target.
#:
#:   TRAIN  n=85   net -0.1988   -8.33%   halted on drawdown
#:   VALID  n=68   net -0.2321   -7.65%   halted on drawdown
#:   TEST   n=76   net -0.2852  -10.44%   halted on drawdown
#:
#: Chosen over the alternatives because it is the specification actually
#: traded by hand. The backtest models none of the discretion that goes with
#: that -- which setup to take when several fire at once, when to stand aside
#: -- and at ~85 trades a year the arbitrary tie-break alone moves the result
#: materially. The forward test exists to close that gap.
V2 = StrategyConfig()

#: The V1 rule set: oscillator on 5m only, level-triggered, hard 2R. This is
#: what ran as H-WPR-1-PAPER-AWS-20260813 and it is the best-measured of the
#: family, which is not the same as good.
#:
#:   TRAIN  n=237  net -0.0414   -5.52%   halted on drawdown
#:   VALID  n=94   net -0.1887   -8.66%   halted on drawdown
#:   TEST   n=111  net -0.0169   -1.18%   did not halt
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
