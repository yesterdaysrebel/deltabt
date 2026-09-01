"""Runtime settings.

Note what is absent: there is no API key, no API secret, no signing seed and no
live-trading flag. V1 reads public market data only, so none of those fields
have anywhere to be used. tests/live/test_no_live_trading.py asserts that they
stay absent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

#: The corrected research universe. XRPUSD is present deliberately: the
#: original universe audit excluded it using a recent-30-day liquidity proxy
#: (6.82% synthetic) instead of the study-window measure (1.43%).
DEFAULT_SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")

WS_URL = "wss://socket.india.delta.exchange"

#: Health thresholds, in seconds. From the operating brief.
MAX_WS_SILENCE = 30.0
MAX_CLOSED_1M_AGE = 90.0
GAP_LOOKBACK = 300.0

#: A forming 1m candle is declared closed this long after its minute ends if
#: the feed has not already rolled it. Covers the case where a symbol prints no
#: trades in a minute, so no candlestick_1m update arrives to roll the bar.
CANDLE_ROLL_GRACE = 5.0


def _as_bool(raw: str) -> bool:
    """Parse a DELTABOT_* flag.

    THE LOOP THAT CALLS THIS SKIPS FALSEY STRINGS -- ``if env.get(key)`` -- so
    an empty value never reaches here and the code default stands. But "0" and
    "false" are NON-EMPTY strings and therefore truthy in Python, so a naive
    ``bool(raw)`` would turn DELTABOT_WPR_BAND_EXIT=0 into True and switch on
    the very thing the operator wrote 0 to switch off. The cooldowns rely on
    the same truthiness for ints, where "0" legitimately means zero seconds;
    for a flag it means OFF, and only an explicit list can tell the two apart.
    """
    return raw.strip().lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class RiskConfig:
    """Every limit is configurable; none may be overridden by the strategy."""

    starting_equity: float = 10_000.0
    risk_per_trade: float = 0.005          # 0.5%
    minimum_rr: float = 2.0
    max_open_positions: int = 1
    #: DISABLED ON 2026-08-20 (1.0 = equity would have to reach zero in a day).
    #: It was 2% of start-of-day equity.
    #:
    #: IT WAS CENSORING THE MEASUREMENT, WHICH IS WHY IT WENT. At 0.5% risk per
    #: trade a 2% daily cap halts the day after roughly four net-R of loss, and
    #: at a 33% win rate with losses near -1.16R a run of four or five is
    #: ordinary. So the gate fired often, and each time it removed the trades
    #: that would have FOLLOWED a bad start. The run stopped estimating the
    #: strategy and started estimating "the strategy, given the day had not
    #: already gone badly" -- which is not a quantity anyone wants and not one
    #: that can confirm or contradict a backtest. Observed live: 27 refusals,
    #: then daily_loss_remaining at 0.0 with the bot unable to trade for the
    #: remaining 18 hours of the UTC day.
    #:
    #: THIS LEAVES NO CIRCUIT BREAKER OF ANY KIND. max_drawdown_pct is 1.0,
    #: max_consecutive_losses is 0, and now this. Nothing stops losses
    #: compounding. That is defensible for a paper run whose entire purpose is
    #: an unbiased expectancy estimate, and indefensible for real capital: ALL
    #: THREE MUST BE RESTORED BEFORE ANYTHING TRADES REAL MONEY. There is no
    #: "off" sentinel here because the natural bound already is one.
    max_daily_loss_pct: float = 1.0
    #: 10% from peak equity. 1.0 disables it: equity would have to reach zero.
    #: There is no "off" sentinel because the natural bound already is one.
    max_drawdown_pct: float = 0.10
    #: RAISED FROM 6 TO 20 ON 2026-08-19, BECAUSE 6 WAS SIZED FOR A DIFFERENT
    #: HOLD TIME. V3 held positions for hours, so six entries filled a day. The
    #: ATR arm's stops are narrow enough that trades resolve in 5 to 58 minutes:
    #: it spent all six between 11:36 and 15:22 and then sat refusing every
    #: setup for the remaining twenty hours, 31 refusals and counting.
    #:
    #: THE COST WAS SELECTION, NOT THROUGHPUT. Thirty trades at 6/day still
    #: reaches the stopping rule in five days. But a cap that binds by mid-
    #: morning means the sample is whatever fires EARLIEST in the UTC day, not
    #: a fair draw from the signal population -- and that bias is invisible in
    #: the results it produces.
    #:
    #: 20 does not make the gate vestigial. The 15-minute post-trade and
    #: 60-minute post-loss cooldowns are global across all symbols, so the
    #: practical ceiling is lower than 20 on most days, and max_daily_loss_pct
    #: (2% NET of the day's wins) is the backstop that actually stops a bad
    #: day -- roughly four net-R down at 0.5% risk per trade.
    #:
    #: It lives here and not in Terraform, unlike its siblings max_open,
    #: max_drawdown and max_consecutive_losses. Those reach the container
    #: through /opt/deltabt/env, which user-data writes -- so adding one more
    #: means a user_data change, which means REPLACING the instance, in a
    #: region that ran out of capacity for three hours the same morning. An
    #: image-only change carries none of that risk. DELTABOT_MAX_TRADES_PER_DAY
    #: is still read if something ever sets it.
    max_trades_per_day: int = 20
    #: A DAILY circuit breaker -- the streak resets on the UTC day roll. 0
    #: disables the gate entirely; see the guard in RiskEngine.evaluate, which
    #: must skip the check rather than compare against it, because
    #: `losses >= 0` is true for a fresh state and would reject everything.
    max_consecutive_losses: int = 3
    #: Close a position that has been open this long, at market, whatever it
    #: is doing. 0 disables it.
    #:
    #: NOTHING BUT STOP OR TARGET CLOSED A POSITION BEFORE THIS.
    #: ExitReason.TIME_EXIT was declared and never emitted, so a target that
    #: could not be reached held its symbol's slot forever -- and one open
    #: position per symbol is enforced in the engine AND by
    #: ux_positions_open_symbol. Measured on 2026-08-17: a BTCUSD short opened
    #: 2026-08-14 was 66.9 hours old and had refused 75 BTCUSD setups in a
    #: single day. The run was no longer measuring the strategy; it was
    #: measuring the strategy with a symbol switched off.
    #:
    #: It lives in RISK rather than in StrategyConfig deliberately. It is not a
    #: signal rule -- it is a policy about carrying inventory -- and putting it
    #: here leaves the strategy hash alone, so the rules under test stay
    #: identifiable as d7837e445bc74781.
    max_hold_seconds: int = 0

    # --- CLOSE WHEN THE SETUP STOPPED BEING TRUE ---------------------------
    #
    # A long is entered because %R sits inside the band and is rising. If %R
    # then drops out of the band's FLOOR the reason for the trade is gone, and
    # waiting for the stop costs the rest of the move plus the same fees.
    #
    # Leaving the band the FAVOURABLE way is NOT an exit: a long whose %R
    # climbs past the midpoint toward overbought is the trade working, and
    # closing there would cut exactly the trades that reach target.
    #
    # THIS LIVES IN RISK, NOT IN THE STRATEGY, for the reason given above for
    # max_hold_seconds: it is a policy about carrying an open position, not a
    # signal rule, so the strategy hash still identifies the rules under test.
    # It DOES move the risk hash, which ends the running experiment by design.
    #
    # MEASURED BEFORE BEING BUILT (2026-09-01, ungated portfolio):
    #     thin 3   -6.56% -> -7.32%, max drawdown 14.85% -> 12.40%
    #     all 7   -60.43% -> -81.47%, drawdown 63.67% -> 83.14%
    # It helps where trading is cheap and hurts where it is not, because it
    # roughly doubles turnover and the majors cost 0.19-0.30R per round trip.
    exit_on_wpr_band_exit: bool = False
    #: A long closes when %R falls BELOW this. Mirrors the entry band's floor.
    wpr_exit_long_level: float = -80.0
    #: A short closes when %R rises ABOVE this. Mirrors the band's ceiling.
    wpr_exit_short_level: float = -20.0
    max_position_notional: float = 50_000.0
    max_total_notional: float = 50_000.0
    max_leverage: float = 3.0
    cooldown_after_trade_seconds: int = 900     # 15m
    cooldown_after_loss_seconds: int = 3600     # 60m
    #: Slippage assumption in basis points of notional, applied to taker fills.
    slippage_bps: float = 2.0
    #: When a limit is breached, new entries are blocked. Existing positions
    #: are left alone unless this is switched on.
    close_positions_on_breach: bool = False
    #: Sessions in which entries are permitted, as UTC "HH:MM-HH:MM" ranges.
    #: Empty means 24/7, which is the default for crypto perps.
    sessions_utc: tuple[str, ...] = ()

    def validate(self) -> None:
        if not 0 < self.risk_per_trade <= 0.1:
            raise ValueError(f"risk_per_trade must be in (0, 0.1], got {self.risk_per_trade}")
        if self.minimum_rr <= 0:
            raise ValueError("minimum_rr must be > 0")
        if self.max_open_positions < 1:
            raise ValueError("max_open_positions must be >= 1")
        if self.starting_equity <= 0:
            raise ValueError("starting_equity must be > 0")
        for name in ("max_daily_loss_pct", "max_drawdown_pct"):
            v = getattr(self, name)
            if not 0 < v <= 1:
                raise ValueError(f"{name} must be in (0, 1], got {v}")
        # 0 means "no streak limit". Negative is a typo, not a stronger 0, and
        # the engine's guard would treat it identically -- so it is refused
        # here rather than silently accepted as another way to spell disabled.
        if self.max_consecutive_losses < 0:
            raise ValueError(
                f"max_consecutive_losses must be >= 0 (0 disables the gate), "
                f"got {self.max_consecutive_losses}")
        if self.max_trades_per_day < 1:
            raise ValueError("max_trades_per_day must be >= 1")
        if self.max_hold_seconds < 0:
            raise ValueError(
                f"max_hold_seconds must be >= 0 (0 disables the time stop), "
                f"got {self.max_hold_seconds}")


@dataclass(frozen=True)
class Settings:
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    ws_url: str = WS_URL
    database_url: str = "postgresql://paper:paper@localhost:5432/paper"
    #: Backfill this many days of 1m history on startup. Warm-up needs ~12h of
    #: 5m bars; 7 days gives a wide margin and covers a weekend outage.
    backfill_days: int = 7
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    #: Presentation timezone. Storage is always UTC.
    display_tz: str = "Asia/Kolkata"
    risk: RiskConfig = field(default_factory=RiskConfig)

    def validate(self) -> None:
        if not self.symbols:
            raise ValueError("at least one symbol is required")
        self.risk.validate()

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        env = os.environ
        overrides: dict = {}
        if env.get("DELTABOT_SYMBOLS"):
            overrides["symbols"] = tuple(
                x.strip().upper() for x in env["DELTABOT_SYMBOLS"].split(",") if x.strip()
            )
        for key, field_name, cast in (
            ("DATABASE_URL", "database_url", str),
            ("DELTABOT_WS_URL", "ws_url", str),
            ("DELTABOT_BACKFILL_DAYS", "backfill_days", int),
            ("DELTABOT_API_PORT", "api_port", int),
            ("DELTABOT_LOG_LEVEL", "log_level", str),
            ("DELTABOT_DISPLAY_TZ", "display_tz", str),
        ):
            if env.get(key):
                overrides[field_name] = cast(env[key])

        risk_overrides: dict = {}
        for key, field_name, cast in (
            ("DELTABOT_EQUITY", "starting_equity", float),
            ("DELTABOT_RISK_PER_TRADE", "risk_per_trade", float),
            ("DELTABOT_MIN_RR", "minimum_rr", float),
            ("DELTABOT_MAX_OPEN", "max_open_positions", int),
            ("DELTABOT_MAX_DAILY_LOSS", "max_daily_loss_pct", float),
            ("DELTABOT_MAX_DRAWDOWN", "max_drawdown_pct", float),
            ("DELTABOT_MAX_TRADES_PER_DAY", "max_trades_per_day", int),
            ("DELTABOT_MAX_CONSEC_LOSSES", "max_consecutive_losses", int),
            ("DELTABOT_MAX_HOLD", "max_hold_seconds", int),
            ("DELTABOT_WPR_BAND_EXIT", "exit_on_wpr_band_exit", _as_bool),
            ("DELTABOT_WPR_EXIT_LONG", "wpr_exit_long_level", float),
            ("DELTABOT_WPR_EXIT_SHORT", "wpr_exit_short_level", float),
            # CONFIGURATION PLUMBING ONLY, ADDED 2026-08-29. The two cooldowns
            # were the only risk fields with no environment path, so an ungated
            # observation run could not be configured without editing a default.
            # The DEFAULTS ARE UNCHANGED (900s / 3600s) and the enforcement in
            # app/risk/engine.py is untouched -- this adds a way to say a
            # different number, not a different number.
            #
            # WHY IT MATTERS THAT THEY ARE GLOBAL: both cooldowns apply across
            # ALL symbols, not per symbol, so leaving them on makes the sample
            # whatever fires EARLIEST rather than a fair draw from the signal
            # population. That is the censoring an ungated run exists to avoid.
            ("DELTABOT_COOLDOWN_AFTER_TRADE", "cooldown_after_trade_seconds", int),
            ("DELTABOT_COOLDOWN_AFTER_LOSS", "cooldown_after_loss_seconds", int),
        ):
            # Guard left EXACTLY as it was. "0" is a non-empty string and so is
            # already truthy, which is what this experiment needs to set the
            # cooldowns to zero; changing it would have been a behaviour change
            # dressed up as a fix.
            if env.get(key):
                risk_overrides[field_name] = cast(env[key])
        if risk_overrides:
            overrides["risk"] = replace(s.risk, **risk_overrides)

        out = replace(s, **overrides) if overrides else s
        out.validate()
        return out
