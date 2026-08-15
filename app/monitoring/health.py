"""Health and readiness, computed from DATA FRESHNESS rather than liveness.

The failure this exists to catch: a process that is alive, its socket open, its
event loop turning, and no market data arriving. Every process-level probe
reports that as healthy. It is worse than a crash, because a crash restarts.

So /healthz is a statement about the data, not the process.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from app.config.settings import MAX_CLOSED_1M_AGE, MAX_WS_SILENCE

#: Length of the base bar, so age can be measured from close rather than open.
BAR_SECONDS = 60

#: The bar loop runs every second. Twenty passes of grace absorbs a slow
#: database write without tolerating a dead loop.
MAX_LOOP_SILENCE = 20.0


def json_safe(value):
    """Replace inf/NaN with None so a snapshot can always be serialised.

    "No websocket message yet" is naturally infinity, and `json.dumps` refuses
    it -- which would make /healthz return 500 precisely when the feed is dead
    and the endpoint matters most. Recursive because snapshots nest.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


@dataclass
class HealthCheck:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class HealthReport:
    healthy: bool
    checks: list[HealthCheck] = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)

    @property
    def status_code(self) -> int:
        return 200 if self.healthy else 503

    def to_dict(self) -> dict:
        return json_safe({
            "status": "healthy" if self.healthy else "unhealthy",
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail}
                       for c in self.checks],
            **self.snapshot,
        })

    @property
    def failures(self) -> list[str]:
        return [c.name for c in self.checks if not c.ok]


def evaluate_health(snapshot: dict, *, db_writable: bool,
                    now: float | None = None,
                    max_ws_silence: float = MAX_WS_SILENCE,
                    max_1m_age: float = MAX_CLOSED_1M_AGE,
                    max_loop_silence: float = MAX_LOOP_SILENCE) -> HealthReport:
    """The five conditions from section 14. All must hold."""
    now = now if now is not None else time.time()
    checks: list[HealthCheck] = []

    silence = snapshot.get("seconds_since_ws_message", float("inf"))
    checks.append(HealthCheck(
        "websocket_fresh", silence < max_ws_silence,
        f"{silence:.1f}s since the last message (limit {max_ws_silence:.0f}s)"))

    # THIS IS A FEED CHECK, SO IT MEASURES THE FRESHEST SYMBOL, NOT THE STALEST.
    #
    # It used to read builder.last_closed_1m_start, which is a min() across
    # symbols. That is right for four liquid majors, where any one going quiet
    # means the socket has died. It inverts the moment the universe contains a
    # thin instrument: BANKUSD prints no bar for five minutes at a time because
    # nobody traded, and on 2026-08-15 that held candles_fresh red on all three
    # hosts at once while BTCUSD and ETHUSD were 24 seconds old.
    #
    # A dead feed still fails, because when nothing is arriving EVERY symbol
    # goes stale and the freshest goes with them. What is given up is noticing
    # ONE symbol's subscription dying while the rest flow -- which is what
    # no_recent_gaps and the report's per-symbol gap table are for, and which
    # this check could not distinguish from ordinary illiquidity anyway.
    #
    # Age is measured from the bar's CLOSE, not its open. A bar is stamped at
    # its open, so measuring from there makes the youngest possible bar 60s old
    # and leaves only 30s of headroom against a 90s limit -- and a symbol that
    # prints nothing for a minute (rolled by the clock fallback) then reads as
    # ~125s and fails a check it should pass. Observed on the live feed.
    def _age(start) -> float:
        return (now - (start + BAR_SECONDS)) if start else float("inf")

    per_symbol = snapshot.get("last_closed_1m_by_symbol") or {}
    if per_symbol:
        ages = {sym: _age(start) for sym, start in per_symbol.items()}
        age = min(ages.values())
        lagging = sorted((s for s, a in ages.items() if a >= max_1m_age),
                         key=lambda s: -ages[s])
        detail = (f"freshest bar closed {age:.0f}s ago (limit {max_1m_age:.0f}s)"
                  if age != float("inf") else "no closed 1m bar yet")
        if lagging:
            detail += ("; quiet: "
                       + ", ".join(f"{s} {ages[s]:.0f}s" for s in lagging[:4]))
    else:
        # No per-symbol map: an older snapshot, or a caller that supplies only
        # the aggregate. Fall back rather than reporting healthy on no data.
        age = _age(snapshot.get("last_closed_1m"))
        detail = (f"last closed 1m bar closed {age:.0f}s ago "
                  f"(limit {max_1m_age:.0f}s)"
                  if age != float("inf") else "no closed 1m bar yet")
    checks.append(HealthCheck("candles_fresh", age < max_1m_age, detail))

    gaps = snapshot.get("recent_gaps", 0)
    checks.append(HealthCheck("no_recent_gaps", gaps == 0,
                              f"{gaps} gap(s) in the recent window"))

    checks.append(HealthCheck("database_writable", bool(db_writable),
                              "" if db_writable else "write probe failed"))

    running = bool(snapshot.get("strategy_running"))
    checks.append(HealthCheck("strategy_running", running,
                              "" if running else "strategy engine is not running"))

    # A FLAG is not evidence. The evaluation loop can die while the process,
    # the socket and the candle builder all keep working -- observed when a
    # transient DNS failure killed the loop inside its own error handler, and
    # every other health signal stayed green because they are updated by the
    # socket callback. So health asks the loop itself when it last ran.
    since_loop = snapshot.get("seconds_since_bar_loop")
    if since_loop is not None:
        checks.append(HealthCheck(
            "evaluation_loop_alive", since_loop < max_loop_silence,
            f"last pass {since_loop:.1f}s ago (limit {max_loop_silence:.0f}s)"))

    return HealthReport(healthy=all(c.ok for c in checks), checks=checks,
                        snapshot=snapshot)


def evaluate_readiness(snapshot: dict, *, db_connected: bool,
                       lock_held: bool, backfill_complete: bool,
                       indicators_warm: bool, execution_ready: bool) -> HealthReport:
    """Section 14's readiness gate.

    Readiness is about having finished starting up; health is about the data
    staying fresh afterwards. A bot mid-backfill is not broken, it is simply
    not ready, and conflating the two makes a normal restart look like an
    outage.
    """
    checks = [
        HealthCheck("database_connected", bool(db_connected)),
        HealthCheck("advisory_lock_held", bool(lock_held)),
        HealthCheck("backfill_complete", bool(backfill_complete)),
        HealthCheck("indicators_warm", bool(indicators_warm)),
        HealthCheck("candles_synchronized", snapshot.get("last_closed_1m") is not None,
                    "" if snapshot.get("last_closed_1m") else "no closed bar yet"),
        HealthCheck("execution_initialized", bool(execution_ready)),
        HealthCheck("no_unresolved_recovery",
                    snapshot.get("recovery_error") is None,
                    snapshot.get("recovery_error") or ""),
    ]
    return HealthReport(healthy=all(c.ok for c in checks), checks=checks,
                        snapshot=snapshot)
