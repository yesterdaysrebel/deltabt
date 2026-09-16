"""What mainnet requires that a paper run does not.

WHY THIS MODULE EXISTS, IN THE CODEBASE'S OWN WORDS

app/config/settings.py, on max_daily_loss_pct:

    THIS LEAVES NO CIRCUIT BREAKER OF ANY KIND. max_drawdown_pct is 1.0,
    max_consecutive_losses is 0, and now this. Nothing stops losses
    compounding. That is defensible for a paper run whose entire purpose is an
    unbiased expectancy estimate, and indefensible for real capital: ALL THREE
    MUST BE RESTORED BEFORE ANYTHING TRADES REAL MONEY.

They were disabled for a good reason. A halt CENSORS the sample: the run stops
estimating the strategy and starts estimating "the strategy, given the day had
not already gone badly", which is not a quantity anyone wants. Correct for
measurement, wrong for money.

That leaves the requirement as a thing somebody has to REMEMBER, on the day
they flip an environment variable, months after the comment was written. This
module makes it a thing the process checks instead.

WHAT IT DOES NOT DO: PICK YOUR NUMBERS

It refuses the DISABLED SENTINEL, not a value it dislikes. 1.0 drawdown means
"equity would have to reach zero"; 0 consecutive losses means "never halt".
Those are off switches, and off is what mainnet may not be. Whether the halt
belongs at 5% or 15% is a judgement about capital, and the code has no standing
to make it -- only to insist the judgement was made.

THE KILL SWITCH IS A FILE, NOT A FLAG

Stopping a bot that is misbehaving should not require a deploy, a restart, or
an API call that may itself be what is broken. A file on the host can be
created by anyone with SSM access in one command, and is checked before every
order.
"""

from __future__ import annotations

import logging
import os
import pathlib

log = logging.getLogger(__name__)

#: Where the operator drops a file to stop the bot opening anything new.
#: /run is tmpfs, so it clears on reboot -- a kill switch that silently
#: survived a host replacement would be worse than none, because the bot would
#: come back refusing every trade for a reason nobody remembers setting.
#:
#: ITS OWN DIRECTORY, NOT /run/deltabt. That directory holds the venue
#: credentials and is 0700 root, and the bot runs as uid 10001
#: (Dockerfile.live). So the bot could not even stat /run/deltabt/HALT: every
#: check raised PermissionError, kill_switch_engaged() correctly treated an
#: unreadable switch as ENGAGED, and tnet refused every entry on 2026-09-16
#: with no HALT file anywhere. A control directory the bot can traverse, holding
#: nothing secret, keeps both properties: only root can create HALT, and the bot
#: can see whether it did. deploy/aws/run_live.sh mounts it and passes this path
#: explicitly; the default here must match what it passes.
KILL_SWITCH_PATH = os.environ.get("DELTA_KILL_SWITCH",
                                  "/run/deltabt-control/HALT")

#: The sentinel value that means "this breaker is off", per limit.
#: These come from app/config/settings.py's own comments, not from taste.
DISABLED = {
    "max_drawdown_pct": 1.0,        # 1.0 = equity would have to reach zero
    "max_daily_loss_pct": 1.0,      # same
    "max_consecutive_losses": 0,    # 0 = never halt on a streak
}


class GuardError(RuntimeError):
    """A refusal to start. Never contains a credential."""


def circuit_breaker_failures(risk, venue: str) -> list[str]:
    """Which required breakers are switched off. Empty means fit to trade.

    Testnet is deliberately exempt: it is a rehearsal of the machinery on
    instruments the arm does not trade, and halting it early would only cut
    the rehearsal short.
    """
    if (venue or "").strip().lower() != "mainnet":
        return []

    bad: list[str] = []
    for name, off in DISABLED.items():
        value = getattr(risk, name, None)
        if value is None:
            bad.append(f"{name} is not configured at all")
        elif name == "max_consecutive_losses":
            if int(value) <= 0:
                bad.append(f"{name}={value} disables the streak halt")
        elif float(value) >= off:
            bad.append(f"{name}={value} disables the halt "
                       f"({off} means equity would have to reach zero)")
    return bad


def require_circuit_breakers(risk, venue: str) -> None:
    """Raise unless mainnet has its three breakers switched on."""
    bad = circuit_breaker_failures(risk, venue)
    if not bad:
        return
    raise GuardError(
        "refusing to trade real money with the circuit breakers off:\n  "
        + "\n  ".join(bad)
        + "\nThese were disabled deliberately for the paper measurement, "
          "because a halt censors the sample. That reason does not apply to "
          "capital. Set them to the drawdown you are willing to take -- this "
          "check does not choose the numbers, only insists they were chosen.")


def kill_switch_engaged(path: str | None = None) -> bool:
    """True when the operator has asked the bot to stop opening positions.

    Checked before every order rather than once at start-up: the point of a
    kill switch is the trade that has not happened yet.

    OPENING is what it stops. Positions already open keep their
    exchange-held brackets, which is the protection that matters once
    something has gone wrong enough to reach for this.
    """
    p = pathlib.Path(path or KILL_SWITCH_PATH)
    try:
        return p.exists()
    except OSError as exc:
        # An unreadable path is not a reason to keep trading: whatever is
        # wrong with the filesystem is a better argument for stopping than
        # against it.
        log.error("could not read the kill switch at %s: %s -- treating as "
                  "ENGAGED", p, exc)
        return True


def reason_to_refuse(risk, venue: str, *, kill_switch: str | None = None
                     ) -> str | None:
    """One call for everything that should stop an order before it is sent."""
    if kill_switch_engaged(kill_switch):
        return f"kill switch present at {kill_switch or KILL_SWITCH_PATH}"
    bad = circuit_breaker_failures(risk, venue)
    if bad:
        return "circuit breakers disabled: " + "; ".join(bad)
    return None
