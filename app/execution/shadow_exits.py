"""Shadow exits: where each exit rule WOULD have closed every real position.

WHY (2026-10-02, prod pilot). The owner's dry run asks how the baseline, the
stop ladder and Delta's trailing stop behave on the SAME trades. The repo's
own lesson is that exit rules can only be compared on identical entries --
unpaired arms put the entry difference inside the result. So one bot takes
the entries and this follows each of its positions under every rule at once,
on the same ticks, and writes one row per position per rule.

THE SIMULATION IS THE PAPER BROKER'S, on purpose, so a shadow and the paper
arm of the same rule describe the same thing:
  * a stop triggers on MARK and fills at last-traded moved by the slippage;
  * a target triggers and fills on last-traded at the target price;
  * if one tick hits both, the stop wins (the pessimistic order);
  * nothing triggers until a tick strictly after the entry fill;
  * the stop moves AFTER that tick's tests, never before, through the one
    shared rule in deltabt/exits.py, rounded protectively, tighten-only.
A rule never exits LATER than the position it shadows: ladder and trail stops
are at or inside the baseline's, all share the target, and a position that
closes for any other reason (time stop, flatten) closes every shadow still
open at the same price, recorded as REAL_EXIT so a reader can tell.

WHAT IT CANNOT KNOW. Turnover: one entry stream feeds every rule, so a rule
that exits early does not take the trades it would have freed a slot for.
And the venue's own trailing stop: this is our model of it, not Delta's.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.persistence.models import ShadowExitRecord
from deltabt.exits import earned_stop_r, tightens


@dataclass(frozen=True)
class ShadowRule:
    name: str
    ladder_rungs: tuple[tuple[float, float], ...] = ()
    trail_after_r: float | None = None
    trail_r: float | None = None


#: The three exits the pilot reads, as defined in deltabt/catalog.py.
PILOT_RULES = (
    ShadowRule("baseline"),
    ShadowRule("ladder", ladder_rungs=((0.5, 0.0), (1.0, 0.5), (1.5, 1.0), (2.0, 1.5))),
    ShadowRule("trail", trail_after_r=0.5, trail_r=0.5),
)


@dataclass
class _Leg:
    rule: ShadowRule
    stop: float
    armed_at: int | None = None
    promotions: int = 0
    trail_amount: float | None = None
    record: ShadowExitRecord | None = None


@dataclass
class _Tracked:
    pos: object
    initial_stop: float
    observed_from_entry: bool
    legs: list[_Leg] = field(default_factory=list)
    #: The real position has closed by ITS OWN rule (stop or target) and the
    #: other legs are still being followed on ticks (see observe()).
    real_gone: bool = False


#: Real exits that apply to every rule alike. When the real position closes
#: this way the remaining legs close with it; when it closes by its own stop
#: or target, the other rules may still be in the trade and keep running.
SHARED_EXITS = ("TIME_EXIT", "MANUAL_CLOSE", "SETUP_INVALIDATED", "SYSTEM_SAFETY",
                "DATA_FAILURE")


class ShadowExits:
    """Follows open positions under several exit rules; returns closed rows."""

    def __init__(self, costs: dict, rules: tuple[ShadowRule, ...] = PILOT_RULES,
                 slippage_bps: float = 2.0, max_hold_seconds: int | None = None) -> None:
        self.costs = costs
        self.rules = rules
        self.slip = slippage_bps / 10_000.0
        #: The paper broker's time stop, applied to a leg that outlives the
        #: real position (0/None = never). Legs closed BY the real position's
        #: own time exit do not need it: SHARED_EXITS handles that.
        self.max_hold_seconds = int(max_hold_seconds) if max_hold_seconds else None
        self._tracked: dict[str, _Tracked] = {}
        #: Positions restored from the journal at startup. Their path before
        #: this process started was never seen, so the ladder and trail legs
        #: start from the initial stop without the peak they may already have
        #: made; their rows say so with observed_from_entry = False.
        self._adopted: set[str] = set()

    # -- public -----------------------------------------------------------

    def adopt(self, position_uid: str) -> None:
        """Mark a position recovered after a restart (see `_adopted`)."""
        self._adopted.add(position_uid)

    def observe(self, tick, positions, *, from_entry: bool = True) -> list[ShadowExitRecord]:
        """Advance every shadow on `tick`'s symbol; return rows that closed.

        `positions` is the broker's open positions after it processed this
        tick. A position seen for the first time starts being shadowed; one
        that has gone (closed by its own rule) closes its remaining shadows
        at its real exit. `from_entry=False` marks positions adopted after a
        restart, whose path before now this process never saw.
        """
        out: list[ShadowExitRecord] = []
        open_now = {p.position_uid: p for p in positions if p.is_open}
        for uid, p in open_now.items():
            if uid not in self._tracked:
                self._start(p, from_entry)
        for uid, tr in list(self._tracked.items()):
            # THIS TICK FIRST, even for a position its own rule just closed on
            # it: the broker runs before this, so a target hit on this tick has
            # already removed the position, and its shadows must still judge
            # the same tick by their own rules before falling back to the real
            # exit (found by test, 2026-10-02).
            if tr.pos.symbol == tick.symbol:
                out.extend(self._advance(tr, tick))
            if uid not in open_now and not tr.real_gone:
                # THE REAL POSITION HAS CLOSED. If by a route every rule shares
                # (time stop, flatten, halt) the other legs close with it. If
                # by ITS OWN stop or target, the rules still in the trade keep
                # running on ticks until each exits by its own rule: the dry
                # run's account follows the TRAIL (2026-10-03), which exits
                # first, and closing hold-to-3R and the ladder at the trail's
                # exit would have made the comparison worthless. (These legs
                # live only in this process: a restart loses them, and the
                # report then says "lost: restart".)
                reason = str(getattr(tr.pos, "exit_reason", None) or "UNKNOWN")
                if reason in ("STOP_LOSS", "TAKE_PROFIT"):
                    tr.real_gone = True
                else:
                    out.extend(self._close_with_real_exit(tr))
            if all(leg.record is not None for leg in tr.legs) or (
                    uid not in open_now and not tr.real_gone):
                del self._tracked[uid]
        return out

    # -- internals --------------------------------------------------------

    def _start(self, p, from_entry: bool) -> None:
        initial = p.entry_price - p.side * p.risk_per_unit
        from_entry = from_entry and p.position_uid not in self._adopted
        tr = _Tracked(pos=p, initial_stop=initial, observed_from_entry=from_entry)
        for rule in self.rules:
            tr.legs.append(_Leg(rule=rule, stop=initial))
        self._tracked[p.position_uid] = tr

    def _advance(self, tr: _Tracked, tick) -> list[ShadowExitRecord]:
        p = tr.pos
        armed_after = getattr(p, "armed_after_us", None)
        if armed_after is not None and tick.ts_us <= armed_after:
            return []
        out = []
        opened = getattr(p, "opened_at", None)
        timed_out = (self.max_hold_seconds is not None and opened is not None
                     and tick.ts - int(opened) >= self.max_hold_seconds)
        for leg in tr.legs:
            if leg.record is not None:
                continue
            if timed_out:
                # The paper broker's time stop, for a leg that outlived the
                # real position: filled as the broker would, at last traded.
                px = tick.ltp * (1.0 - p.side * self.slip)
                leg.record = self._row(tr, leg, px, "TIME_EXIT", tick.ts, maker=False)
                out.append(leg.record)
                continue
            hit_stop = tick.mark <= leg.stop if p.side > 0 else tick.mark >= leg.stop
            hit_target = (tick.ltp >= p.target_price if p.side > 0
                          else tick.ltp <= p.target_price)
            if hit_stop:
                px = tick.ltp * (1.0 - p.side * self.slip)
                leg.record = self._row(tr, leg, px, "STOP_LOSS", tick.ts, maker=False)
                out.append(leg.record)
                continue
            if hit_target:
                leg.record = self._row(tr, leg, p.target_price, "TAKE_PROFIT",
                                       tick.ts, maker=True)
                out.append(leg.record)
                continue
            self._promote(tr, leg, tick)
        return out

    def _promote(self, tr: _Tracked, leg: _Leg, tick) -> None:
        p, rule = tr.pos, leg.rule
        if not (rule.ladder_rungs or rule.trail_r is not None) or p.risk_per_unit <= 0:
            return
        fav = (tick.ltp - p.entry_price) * p.side / p.risk_per_unit
        stop_r = earned_stop_r(fav, ladder_rungs=rule.ladder_rungs,
                               trail_after_r=rule.trail_after_r, trail_r=rule.trail_r)
        if stop_r is None:
            return
        costs = self.costs.get(p.symbol)
        raw = p.entry_price + p.side * stop_r * p.risk_per_unit
        new = (costs.round_price(raw, direction=-1 if p.side > 0 else 1)
               if costs is not None else raw)
        if tightens(p.side, new, leg.stop):
            if leg.armed_at is None:
                leg.armed_at = tick.ts
                if rule.trail_r is not None:
                    # What the live bracket edit would send: bracket_trail_amount.
                    amt = rule.trail_r * p.risk_per_unit
                    leg.trail_amount = (costs.round_price(amt, direction=1)
                                        if costs is not None else amt)
            leg.stop = new
            leg.promotions += 1

    def _close_with_real_exit(self, tr: _Tracked) -> list[ShadowExitRecord]:
        p = tr.pos
        out = []
        px = getattr(p, "exit_price", None)
        at = getattr(p, "closed_at", None) or getattr(p, "opened_at", 0)
        reason = f"REAL_EXIT:{getattr(p, 'exit_reason', None) or 'UNKNOWN'}"
        for leg in tr.legs:
            if leg.record is None and px is not None:
                leg.record = self._row(tr, leg, float(px), reason, int(at),
                                       maker=str(reason).endswith("TAKE_PROFIT"))
                out.append(leg.record)
        return out

    def _row(self, tr: _Tracked, leg: _Leg, exit_price: float, reason: str,
             at: int, *, maker: bool) -> ShadowExitRecord:
        p = tr.pos
        rpu = p.risk_per_unit
        gross_r = (exit_price - p.entry_price) * p.side / rpu if rpu > 0 else 0.0
        costs = self.costs.get(p.symbol)
        fee_r = 0.0
        if costs is not None and rpu > 0 and p.quantity > 0:
            unit_risk = rpu * p.quantity * costs.contract_value
            fees = (costs.entry_cost(p.quantity, p.entry_price)
                    + costs.exit_cost(p.quantity, exit_price, maker=maker))
            fee_r = fees / unit_risk if unit_risk > 0 else 0.0
        return ShadowExitRecord(
            position_uid=p.position_uid, rule=leg.rule.name, symbol=p.symbol,
            side=p.side, quantity=p.quantity, entry_price=p.entry_price,
            initial_stop=tr.initial_stop, target_price=p.target_price,
            risk_per_unit=rpu, opened_at=p.opened_at, armed_at=leg.armed_at,
            promotions=leg.promotions, trail_amount=leg.trail_amount,
            final_stop=leg.stop, exit_price=exit_price, exit_reason=reason,
            closed_at=at, gross_r=gross_r, net_r=gross_r - fee_r,
            observed_from_entry=tr.observed_from_entry)
