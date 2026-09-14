"""Live order execution against Delta Exchange India.

THIS PACKAGE CAN SPEND REAL MONEY. Everything else in this repository cannot,
and that is deliberate: `app/safety.py` defines a paper-only boundary that
`tests/live/test_no_live_trading.py` enforces against `app/` and `deltabt/`,
and `app/forwardtest/preflight.py` re-asserts it at the deploy gate.

WHY THIS IS A SEPARATE TOP-LEVEL PACKAGE, AND MUST STAY ONE

    deploy/docker/Dockerfile copies `deltabt` and `app`. It does not copy
    `live`. pyproject's packages.find includes "deltabt*" and "app*". It does
    not include "live*".

So the paper bot's image does not contain this code and its process cannot
import it. That is a stronger guarantee than a runtime flag, and it is the
same reasoning app/safety.py gives for refusing to have a flag at all:

    "A flag-gated live mode is explicitly forbidden: the ABSENCE of the
     capability is the boundary, not a runtime toggle."

The paper bot keeps that property unchanged. This package is a SECOND
deployable that does not exist in the paper bot's world.
`tests/live_exec/test_boundary_preserved.py` fails if any of that drifts --
if `app/` grows an import of `live`, if the Dockerfile starts copying it, or
if the packaging globs widen.

STATUS: execution primitives only. No strategy is wired to this, and nothing
schedules it. Placing an order requires calling it deliberately, with
credentials that are not in this repository.
"""
