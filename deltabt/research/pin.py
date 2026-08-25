"""Does settlement drift toward the strike that hurts option holders most?

Every prior test in this programme -- thirteen on perpetuals and the variance
premium work on options -- asked whether some feature predicts *direction* or
whether *implied exceeds realised*. This asks neither. It asks whether the
settlement print is pulled toward a level determined by where open interest
sits, which is a market-structure question with no analogue on the perpetual
side and no dependence on the volatility surface being mispriced.

It became testable only on discovering that Delta serves **open interest
history** under an ``OI:`` symbol prefix. That is not documented anywhere this
project has seen, and it was verified rather than assumed: ``OI:C-BTC-96000-301026``
returns a series whose latest close (5.941) matches the live ticker's ``oi``
field exactly, while a nonsense prefix returns zero bars -- so the endpoint is
not silently ignoring the prefix and echoing the premium series.

The hypothesis
--------------

**Max pain.** For a candidate settlement level ``K``, total intrinsic value
owed to option holders is

    payout(K) = sum_k [ OI_call(k) * max(K - k, 0) + OI_put(k) * max(k - K, 0) ]

The max-pain strike ``K*`` minimises it. The folk claim is that settlement
gravitates toward ``K*``.

The null that actually matters
------------------------------

``K*`` sits near spot most of the time, because that is where open interest
concentrates. So "settlement lands near ``K*``" is nearly true by construction
and proves nothing. The test has to be **incremental over spot**:

    y = (settle  - spot_t) / spot_t     the move that actually happened
    x = (K*      - spot_t) / spot_t     the direction and size of the alleged pull

Pinning is real only if ``x`` predicts ``y`` -- a positive slope in a
regression of ``y`` on ``x``. If settlement is simply a random walk from
``spot_t``, the slope is zero however close ``K*`` happens to sit to the print.

Extracting the settlement price exactly
---------------------------------------

Rather than reading a 5m index candle at the settlement instant -- Delta
settles on a time-averaged index, so a point read carries ~0.085% of error --
the settlement level is recovered from the catalog itself. For a call struck
at ``k`` that finished in the money, ``settlement_price = S_E - k``, so
``S_E = settlement_price + k``. Taking the median across every in-the-money
call and put in the chain gives the exchange's own settlement index to within
rounding, and disagreement between the two rights is a data-quality check
rather than something to average away silently.

Run: python -m deltabt.research.pin --underlying BTC --every 4

RECONSTRUCTED 2026-08-25 FROM ``__pycache__/pin.cpython-311.pyc``
----------------------------------------------------------------

The source file was removed from the working tree while it was still
untracked, so there is no history to restore it from. The docstrings survive
because CPython stores them in the bytecode. **The inline comments did not,
and are gone.**

The two functions below are decompiled from that bytecode and checked against
the fourteen tests in ``tests/test_options_research.py``, which specify both
completely -- max pain on a three-strike grid from four directions, and index
recovery from calls, from puts, from a consistent chain, from an inconsistent
one, and from a chain with a single bad print.

``build_sample``, ``summarise`` and ``main`` -- the H-Pin-1 driver that pulled
open-interest history and ran the regression of settlement move on max-pain
pull -- are NOT reconstructed. They are the research half; the programme is
closed and CLAUDE.md forbids extending it. What is kept is the pure primitives,
because they are the part with checkable invariants.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Hours before expiry at which the max-pain level was read.
OBSERVE_HOURS_BEFORE = 24.0
#: A chain with fewer listed strikes than this carries no usable geometry.
MIN_STRIKES = 8
#: Minimum settlement price, as a fraction of the index, for a contract to
#: count as in the money for index recovery.
MIN_ITM_FRAC = 0.005


def settlement_index(chain: pd.DataFrame) -> tuple[float, float]:
    """Recover the settlement index from in-the-money settlement prices.

    Returns ``(level, call_put_disagreement)``. The second value is a data
    check: calls and puts must imply the same index, and a large gap means the
    chain is not internally consistent and should be dropped rather than used.
    """
    implied_c: list[float] = []
    implied_p: list[float] = []
    for _, r in chain.iterrows():
        sp = float(r["settlement_price"])
        k = float(r["strike"])
        if not np.isfinite(sp) or sp <= 0:
            continue
        if bool(r["is_call"]):
            implied_c.append(sp + k)
        else:
            implied_p.append(k - sp)

    if not implied_c and not implied_p:
        return np.nan, np.nan

    med_c = float(np.median(implied_c)) if implied_c else np.nan
    med_p = float(np.median(implied_p)) if implied_p else np.nan
    both = [v for v in (med_c, med_p) if np.isfinite(v)]
    level = float(np.median(both))
    # 0.0, not NaN, when only one right is present: the bytecode branches to a
    # single LOAD_CONST here, and test_recovers_the_index_from_in_the_money_calls
    # asserts it. With one side there is nothing to disagree with.
    disagree = abs(med_c - med_p) if (implied_c and implied_p) else 0.0
    return level, disagree


def max_pain(strikes: np.ndarray, oi_call: np.ndarray,
             oi_put: np.ndarray) -> float:
    """Strike minimising total intrinsic value owed to holders.

    Evaluated only on listed strikes. Interpolating between them would invent
    a precision the strike grid does not have.
    """
    k = strikes[:, None]
    s = strikes[None, :]
    call_pay = np.maximum(k - s, 0.0) * oi_call[None, :]
    put_pay = np.maximum(s - k, 0.0) * oi_put[None, :]
    total = call_pay.sum(axis=1) + put_pay.sum(axis=1)
    return float(strikes[int(np.argmin(total))])
