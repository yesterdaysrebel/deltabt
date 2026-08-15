"""The risk engine. Authoritative, deterministic, and not overridable.

This is the component the whole project exists for. The stated problem was not
signal quality -- it was inconsistent sizing, ignored stops, overtrading and
revenge trading. So the rules here are hard gates, evaluated in a fixed order,
each producing a named rejection that is persisted.

Two properties are deliberate:

* **The strategy cannot influence any of it.** ``evaluate()`` takes an
  ``Explanation`` (a description of what was observed) and the risk state. No
  field on the explanation can raise a limit, change the risk fraction, or skip
  a check.
* **Every rejection is recorded, not just counted.** "Why did it not enter" has
  to be answerable per bar, which means the reason string names the limit, its
  configured value, and the observed value.

Sizing is quantised to whole contracts by ``deltabt.costs.SymbolCosts``, which
rounds DOWN, so realised risk is always at or below budget.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config.settings import RiskConfig
from app.execution.intents import ApprovedOrderIntent
from app.persistence.models import new_uid
from app.strategy.explanation import Explanation, Outcome
from deltabt.costs import SymbolCosts

log = logging.getLogger(__name__)


@dataclass
class RiskState:
    """Everything the limits are evaluated against. Persisted and restored."""

    equity: float
    peak_equity: float
    day: str = ""                       # UTC date, YYYY-MM-DD
    day_start_equity: float = 0.0
    daily_pnl: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    last_trade_at: int = 0
    last_loss_at: int = 0
    realized_pnl: float = 0.0
    wins: int = 0
    losses: int = 0

    @classmethod
    def fresh(cls, equity: float) -> "RiskState":
        return cls(equity=equity, peak_equity=equity, day_start_equity=equity)

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "RiskState":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def roll_day(self, now: int) -> bool:
        """Reset the daily counters when the UTC date changes.

        UTC, not IST: the exchange's funding and settlement grid is UTC, and a
        daily loss limit that resets at a different boundary from the venue's
        own day is a limit nobody can reason about. IST is a display concern.
        """
        today = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
        if today == self.day:
            return False
        self.day = today
        self.day_start_equity = self.equity
        self.daily_pnl = 0.0
        self.trades_today = 0
        # THE STREAK IS A DAILY CIRCUIT BREAKER, AND RESETTING IT HERE IS WHAT
        # MAKES IT ONE.
        #
        # consecutive_losses was incremented in apply_close on a loss and
        # cleared in exactly one place: apply_close on a WIN. Nothing else
        # touched it. So once it reached max_consecutive_losses the engine
        # rejected every entry, clearing the streak required a win, and a win
        # required an entry. The halt was permanent and silent -- the daily
        # report would have printed "the setup simply did not occur" every
        # morning forever.
        #
        # It is reset here rather than removed because every backtest of this
        # strategy family measured it as a daily breaker (see
        # app/config/variants.py); a permanent halt is a rule no measurement
        # describes. At the measured ~37% win rate, three losses in a row
        # arrives after about eight trades, so a 30-day forward test would have
        # gone quiet in its first week.
        self.consecutive_losses = 0
        return True

    def apply_close(self, pnl: float, now: int) -> None:
        self.equity += pnl
        self.realized_pnl += pnl
        self.daily_pnl += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        if pnl < 0:
            self.consecutive_losses += 1
            self.last_loss_at = now
            self.losses += 1
        else:
            self.consecutive_losses = 0
            self.wins += 1
        self.last_trade_at = now

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity)

    @property
    def daily_loss_pct(self) -> float:
        base = self.day_start_equity or self.equity
        return max(0.0, -self.daily_pnl / base) if base > 0 else 0.0


@dataclass
class RiskDecision:
    approved: bool
    evaluation_id: str
    checks_passed: list[str] = field(default_factory=list)
    reason: str | None = None
    limit_name: str | None = None
    limit_value: float | None = None
    observed_value: float | None = None
    intent: ApprovedOrderIntent | None = None


def _in_session(now: int, sessions: tuple[str, ...]) -> bool:
    if not sessions:
        return True
    t = datetime.fromtimestamp(now, tz=timezone.utc)
    minutes = t.hour * 60 + t.minute
    for window in sessions:
        try:
            a, b = window.split("-")
            ah, am = (int(x) for x in a.split(":"))
            bh, bm = (int(x) for x in b.split(":"))
        except (ValueError, AttributeError):
            log.error("malformed session window %r -- treating as closed", window)
            continue
        lo, hi = ah * 60 + am, bh * 60 + bm
        if lo <= hi:
            if lo <= minutes < hi:
                return True
        elif minutes >= lo or minutes < hi:      # wraps midnight
            return True
    return False


class RiskEngine:
    """Turns a detected setup into an approved intent, or a named rejection."""

    def __init__(self, cfg: RiskConfig, costs: dict[str, SymbolCosts],
                 *, allowed_symbols: tuple[str, ...] | None = None) -> None:
        cfg.validate()
        self.cfg = cfg
        self.costs = costs
        self.allowed_symbols = allowed_symbols

    # -- the gates ---------------------------------------------------------

    def evaluate(
        self,
        exp: Explanation,
        state: RiskState,
        *,
        open_positions: list,
        now: int,
        market_can_trade: bool = True,
    ) -> RiskDecision:
        """Apply every limit in a fixed order. First failure wins."""
        cfg = self.cfg
        ev = new_uid("risk")
        passed: list[str] = []

        def reject(reason, *, name=None, limit=None, observed=None) -> RiskDecision:
            exp.outcome = Outcome.REJECTED
            exp.rejection_reason = reason
            log.info("signal rejected", extra={"symbol": exp.symbol,
                                               "reason": reason})
            return RiskDecision(False, ev, passed, reason, name, limit, observed)

        def ok(name: str) -> None:
            passed.append(name)

        state.roll_day(now)
        exp.equity = state.equity

        if exp.outcome is not Outcome.DETECTED:
            return reject(f"not a detected setup (outcome={exp.outcome.value})")
        ok("setup_detected")

        if not market_can_trade:
            return reject("market is halted or reopening")
        ok("market_live")

        if self.allowed_symbols is not None and exp.symbol not in self.allowed_symbols:
            return reject(f"{exp.symbol} is not in the configured universe",
                          name="allowed_symbols")
        ok("symbol_allowed")

        if not _in_session(now, cfg.sessions_utc):
            return reject("outside the configured trading session",
                          name="sessions_utc")
        ok("in_session")

        # --- portfolio state ---------------------------------------------
        if any(p.symbol == exp.symbol and p.is_open for p in open_positions):
            return reject(f"already holding an open position in {exp.symbol}")
        ok("no_existing_position_in_symbol")

        n_open = sum(1 for p in open_positions if p.is_open)
        if n_open >= cfg.max_open_positions:
            return reject(
                f"max_open_positions {cfg.max_open_positions} reached "
                f"(currently {n_open})",
                name="max_open_positions", limit=cfg.max_open_positions,
                observed=n_open)
        ok("max_open_positions")

        # --- loss / drawdown gates ----------------------------------------
        if state.daily_loss_pct >= cfg.max_daily_loss_pct:
            return reject(
                f"daily loss {100*state.daily_loss_pct:.2f}% has reached the "
                f"{100*cfg.max_daily_loss_pct:.2f}% limit",
                name="max_daily_loss_pct", limit=cfg.max_daily_loss_pct,
                observed=state.daily_loss_pct)
        ok("max_daily_loss")

        if state.drawdown_pct >= cfg.max_drawdown_pct:
            return reject(
                f"drawdown {100*state.drawdown_pct:.2f}% has reached the "
                f"{100*cfg.max_drawdown_pct:.2f}% limit",
                name="max_drawdown_pct", limit=cfg.max_drawdown_pct,
                observed=state.drawdown_pct)
        ok("max_drawdown")

        if state.trades_today >= cfg.max_trades_per_day:
            return reject(
                f"already took {state.trades_today} trades today, limit is "
                f"{cfg.max_trades_per_day}",
                name="max_trades_per_day", limit=cfg.max_trades_per_day,
                observed=state.trades_today)
        ok("max_trades_per_day")

        # `> 0` GUARDS THE COMPARISON, IT IS NOT A STYLE CHOICE.
        # 0 means the gate is disabled, but `consecutive_losses >= 0` is true
        # for a brand new state, so comparing anyway would reject every signal
        # forever -- the same class of permanent silent halt as the streak that
        # never reset, arrived at from the opposite direction.
        if (cfg.max_consecutive_losses > 0
                and state.consecutive_losses >= cfg.max_consecutive_losses):
            return reject(
                f"{state.consecutive_losses} consecutive losses reaches the "
                f"limit of {cfg.max_consecutive_losses}",
                name="max_consecutive_losses",
                limit=cfg.max_consecutive_losses,
                observed=state.consecutive_losses)
        ok("max_consecutive_losses")

        # --- cooldowns -----------------------------------------------------
        since_trade = now - state.last_trade_at if state.last_trade_at else 1 << 30
        if since_trade < cfg.cooldown_after_trade_seconds:
            return reject(
                f"cooldown after trade: {since_trade}s elapsed of "
                f"{cfg.cooldown_after_trade_seconds}s",
                name="cooldown_after_trade_seconds",
                limit=cfg.cooldown_after_trade_seconds, observed=since_trade)
        ok("cooldown_after_trade")

        since_loss = now - state.last_loss_at if state.last_loss_at else 1 << 30
        if since_loss < cfg.cooldown_after_loss_seconds:
            return reject(
                f"cooldown after loss: {since_loss}s elapsed of "
                f"{cfg.cooldown_after_loss_seconds}s",
                name="cooldown_after_loss_seconds",
                limit=cfg.cooldown_after_loss_seconds, observed=since_loss)
        ok("cooldown_after_loss")

        # --- geometry ------------------------------------------------------
        entry = exp.entry_price
        stop = exp.stop_price
        target = exp.target_price
        if entry is None or stop is None or target is None:
            return reject("setup is missing entry/stop/target")
        rpu = exp.detail.get("risk_per_unit") or abs(entry - stop)
        if rpu <= 0:
            return reject(f"stop distance {rpu} is not positive")
        ok("stop_distance_positive")

        rr = abs(target - entry) / rpu
        if rr < cfg.minimum_rr:
            return reject(
                f"reward/risk {rr:.2f} is below minimum_rr {cfg.minimum_rr:.2f}",
                name="minimum_rr", limit=cfg.minimum_rr, observed=rr)
        exp.reward_risk = rr
        ok("minimum_rr")

        # --- sizing --------------------------------------------------------
        costs = self.costs.get(exp.symbol)
        if costs is None:
            return reject(f"no contract specification for {exp.symbol}")
        ok("contract_spec_present")

        risk_amount = state.equity * cfg.risk_per_trade
        units_by_risk = risk_amount / rpu
        units_by_leverage = (state.equity * cfg.max_leverage) / entry
        units_by_notional = cfg.max_position_notional / entry
        units = min(units_by_risk, units_by_leverage, units_by_notional)
        quantity = costs.contracts_for(units)

        exp.risk_amount = risk_amount
        exp.quantity = quantity

        if quantity <= 0:
            return reject(
                f"position rounds to zero contracts: risk budget "
                f"${risk_amount:.2f} over a ${rpu:.2f} stop buys "
                f"{units:.6f} units, below one {costs.contract_value} "
                f"contract",
                name="min_contract_size", observed=units)
        ok("quantity_positive")

        notional = costs.notional(quantity, entry)
        exp.notional = notional

        if notional > cfg.max_position_notional:
            return reject(
                f"notional ${notional:,.0f} exceeds max_position_notional "
                f"${cfg.max_position_notional:,.0f}",
                name="max_position_notional",
                limit=cfg.max_position_notional, observed=notional)
        ok("max_position_notional")

        open_notional = sum(getattr(p, "notional", 0.0) for p in open_positions
                            if p.is_open)
        if open_notional + notional > cfg.max_total_notional:
            return reject(
                f"total notional ${open_notional + notional:,.0f} would exceed "
                f"max_total_notional ${cfg.max_total_notional:,.0f}",
                name="max_total_notional", limit=cfg.max_total_notional,
                observed=open_notional + notional)
        ok("max_total_notional")

        leverage = notional / state.equity if state.equity > 0 else float("inf")
        if leverage > cfg.max_leverage + 1e-9:
            return reject(
                f"leverage {leverage:.2f}x exceeds max_leverage "
                f"{cfg.max_leverage:.2f}x",
                name="max_leverage", limit=cfg.max_leverage, observed=leverage)
        ok("max_leverage")

        # Realised risk after integer rounding. Rounding is downward, so this
        # should never exceed budget -- checked because "should never" is not
        # a guarantee.
        actual_risk = quantity * costs.contract_value * rpu
        if actual_risk > risk_amount * 1.000001:
            return reject(
                f"realised risk ${actual_risk:.2f} exceeds budget "
                f"${risk_amount:.2f} after contract rounding",
                name="risk_per_trade", limit=risk_amount, observed=actual_risk)
        ok("realised_risk_within_budget")

        fee = costs.entry_cost(quantity, entry) + costs.exit_cost(
            quantity, stop, maker=False)
        slippage = notional * costs.slippage_rate * 2
        exp.estimated_fee = fee
        exp.estimated_slippage = slippage

        intent = ApprovedOrderIntent(
            intent_id=new_uid("intent"),
            signal_key=exp.detail.get("idempotency_key", ""),
            risk_evaluation_id=ev,
            symbol=exp.symbol,
            side=exp.direction,
            order_type="market",
            quantity=quantity,
            limit_price=None,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            risk_per_unit=rpu,
            risk_amount=actual_risk,
            notional=notional,
            equity_before=state.equity,
            estimated_fee=fee,
            estimated_slippage=slippage,
            strategy_version=exp.strategy_version,
            bar_open=exp.bar_open,
            checks_passed=tuple(passed),
        )
        exp.outcome = Outcome.APPROVED
        return RiskDecision(True, ev, passed, intent=intent)
