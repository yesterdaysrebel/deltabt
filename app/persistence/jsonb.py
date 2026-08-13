"""JSON encoding for PostgreSQL, and the type codecs that make it round-trip.

AUDIT FINDING F2. ``asyncpg`` returns ``jsonb`` as ``str`` unless a codec is
registered, and none was. So ``conditions_passed``, ``conditions_failed``,
``indicators`` and ``detail`` -- the entire "why did it do that" payload -- came
back as strings. The dashboard's ``(x.conditions_failed || []).slice(0,2)``
silently produced nothing, and any analysis of the forward-test dataset would
have had to re-parse by hand.

The tests missed it because the in-memory repository returns real lists and the
API tests run against that. The shared PostgreSQL scenarios did run, but
asserted only on ``outcome`` and ``rejection_reason``, both plain text.

Registering the codec surfaced two further problems that had nothing to do with
strings:

**Non-finite floats cannot be stored at all.** ``json.dumps`` emits a bare
``NaN``, which is not valid JSON and which PostgreSQL rejects:

    InvalidTextRepresentationError: invalid input syntax for type json
    DETAIL: Token "NaN" is invalid.

That is not hypothetical. ``rules.evaluate`` records the indicator snapshot
before returning ``SUPPRESSED`` on the "indicator warm-up produced NaN" branch,
so a legitimate evaluation carries NaN values. The insert would have thrown
inside the bar loop and the evaluation would have been lost -- silently, since
the loop catches and logs.

Non-finite values are therefore encoded as ``null``, which is what JSON has for
"no number here". A null in ``indicators`` means the indicator was not finite on
that bar, which is both true and queryable.

**NUMERIC comes back as Decimal.** Mixing ``Decimal`` with the engine's floats
raises ``TypeError`` on the first arithmetic operation, and plain
``json.dumps`` refuses it outright. The whole engine is float-based, so the
codec returns floats and the types stay consistent end to end. Exactness is not
being given up here that was ever held: the paper broker computes in float.
"""

from __future__ import annotations

import json
import math
from typing import Any


class UnserialisableValue(TypeError):
    """A payload contained something JSON cannot represent.

    Raised loudly with the offending type rather than letting a cryptic
    ``TypeError`` surface from deep inside the driver.
    """


def _sanitise(value: Any) -> Any:
    """Replace non-finite floats with None, recursively.

    NaN and Infinity are valid Python floats and invalid JSON. PostgreSQL
    rejects them, so a payload containing one cannot be stored at all.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _sanitise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitise(v) for v in value]
    return value


def _default(obj: Any) -> Any:
    raise UnserialisableValue(
        f"cannot store {type(obj).__name__} in a JSONB column: {obj!r}. "
        f"Convert it at the call site rather than relying on a coercion here, "
        f"so the stored shape stays predictable."
    )


def dumps(value: Any) -> str:
    """Encode for a JSONB column. Non-finite floats become null."""
    return json.dumps(_sanitise(value), default=_default,
                      allow_nan=False, separators=(",", ":"))


def loads(raw: str) -> Any:
    return json.loads(raw)


async def register_codecs(con) -> None:
    """Teach one asyncpg connection to speak JSON and numbers.

    Registered via the pool's ``init`` hook so every pooled connection has it;
    setting it on one connection would give different behaviour depending on
    which connection a query happened to acquire.
    """
    await con.set_type_codec("jsonb", encoder=dumps, decoder=loads,
                             schema="pg_catalog")
    await con.set_type_codec("json", encoder=dumps, decoder=loads,
                             schema="pg_catalog")
    # NUMERIC -> float, matching the float-based engine. Without this the same
    # field is a float in memory and a Decimal after a round trip, and the two
    # cannot be added together.
    await con.set_type_codec("numeric", encoder=str, decoder=float,
                             schema="pg_catalog")
