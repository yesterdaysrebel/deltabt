"""Black-76 pricing and implied-volatility inversion for Delta India options.

This module exists for one reason: **Delta publishes no history of implied
volatility, quotes or greeks.** `/v2/tickers` carries `mark_iv`, `bid_iv`,
`ask_iv` and a full greek set, but only as a live snapshot -- there is no
historical endpoint for any of them. What *is* historical is the exchange's
own mark price, available as `MARK:C-BTC-...` candles back to mid-2024, and
the spot index (`.DEXBTUSD`).

So an IV history has to be *reconstructed*: invert the exchange's mark price
back through the pricing model it was produced by. That reconstruction is only
trustworthy if it reproduces the exchange's own published `mark_iv` on live
contracts, which is what `research/validate_iv.py` measures. Nothing downstream
should be believed before that check passes.

Conventions, all verified against the live API rather than assumed:

* Delta India options are **European, cash-settled in USD**, on a spot index
  (`.DEXBTUSD`), settling at 12:00 UTC on the expiry date.
* Premium is quoted in USD per unit of underlying; one contract is
  ``contract_value`` units (0.001 BTC, 0.01 ETH).
* The forward is modelled as ``F = S * exp(r * T)``. Delta's product spec
  carries ``annualized_funding: 10.95`` and ``basis_factor_max_limit: 10.95``,
  which is a *percent* figure; the effective ``r`` is calibrated empirically in
  the validation script rather than taken from that field.

Time is measured in **calendar** years (365-day), not trading time. Crypto
trades continuously, so there is no trading-day convention to apply -- but note
this is exactly the assumption a weekend/term-structure experiment would be
testing, so it must not be quietly changed.
"""

from __future__ import annotations

import math

import numpy as np
from numba import njit

#: Seconds in a 365-day year. Crypto has no market calendar, so calendar time
#: and trading time coincide by construction.
SECONDS_PER_YEAR = 365.0 * 24.0 * 3600.0

#: Inversion bounds. An IV outside this range is not a volatility, it is a
#: stale mark or a data error, and is returned as NaN rather than clipped --
#: clipping would silently manufacture a plausible number.
IV_LOW = 1e-4
IV_HIGH = 10.0

#: Bisection tolerance on the price residual, in USD per unit of underlying.
#: Tighter than a tick on any listed strike.
PRICE_TOL = 1e-8
MAX_ITER = 100

#: A price must exceed intrinsic by at least this fraction of the forward
#: before it is considered to carry recoverable volatility information.
#:
#: Without it the inversion returns a confident wrong answer at the wings. A
#: deep-ITM call at F=80,000 / K=40,000 has a time value around 1e-30, which
#: rounds to exactly zero in float64 -- the price *is* the intrinsic. Bisection
#: on a flat function then converges on whatever sits mid-bracket and reported
#: 0.604 for a contract priced at 0.20 vol. There is no volatility in that
#: price to recover, and NaN is the only honest answer.
MIN_TIME_VALUE_FRAC = 1e-12


@njit(cache=True)
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@njit(cache=True)
def _black76(forward: float, strike: float, tte: float, vol: float, is_call: bool) -> float:
    """Undiscounted Black-76 premium in units of the quoting currency.

    Undiscounted on purpose: Delta's mark price is the *forward* premium, not a
    present value. Applying a discount factor here and not in the inversion
    would bias every reconstructed IV in the same direction.
    """
    if tte <= 0.0 or vol <= 0.0 or forward <= 0.0 or strike <= 0.0:
        # At or past expiry the option is worth its intrinsic value.
        if is_call:
            return forward - strike if forward > strike else 0.0
        return strike - forward if strike > forward else 0.0

    sqrt_t = math.sqrt(tte)
    d1 = (math.log(forward / strike) + 0.5 * vol * vol * tte) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    if is_call:
        return forward * _norm_cdf(d1) - strike * _norm_cdf(d2)
    return strike * _norm_cdf(-d2) - forward * _norm_cdf(-d1)


@njit(cache=True)
def _implied_vol(
    price: float, forward: float, strike: float, tte: float, is_call: bool
) -> float:
    """Invert :func:`_black76` for volatility by bisection.

    Bisection rather than Newton deliberately. Vega collapses toward zero for
    deep-OTM and near-expiry contracts -- which is most of a daily-expiry
    surface -- and Newton diverges there. Bisection is slower and cannot fail.
    """
    if not (price > 0.0) or tte <= 0.0 or forward <= 0.0 or strike <= 0.0:
        return np.nan

    # No volatility can produce a price below intrinsic or above the forward
    # (call) / strike (put). Outside those arbitrage bounds there is no root,
    # and returning a bound would be inventing one.
    if is_call:
        intrinsic = forward - strike if forward > strike else 0.0
        upper_bound = forward
    else:
        intrinsic = strike - forward if strike > forward else 0.0
        upper_bound = strike
    if price < intrinsic - PRICE_TOL or price >= upper_bound:
        return np.nan

    # Vega vanishes at the wings. Below this the price carries no information
    # about volatility and any root bisection lands on is an artifact of the
    # search bracket rather than a measurement.
    if price - intrinsic <= MIN_TIME_VALUE_FRAC * forward:
        return np.nan

    lo, hi = IV_LOW, IV_HIGH
    if _black76(forward, strike, tte, hi, is_call) < price:
        return np.nan

    for _ in range(MAX_ITER):
        mid = 0.5 * (lo + hi)
        diff = _black76(forward, strike, tte, mid, is_call) - price
        if diff > 0.0:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-10:
            break
    return 0.5 * (lo + hi)


@njit(cache=True)
def _implied_vol_vec(
    price: np.ndarray,
    forward: np.ndarray,
    strike: np.ndarray,
    tte: np.ndarray,
    is_call: np.ndarray,
) -> np.ndarray:
    n = price.shape[0]
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        out[i] = _implied_vol(price[i], forward[i], strike[i], tte[i], is_call[i])
    return out


# --- public API -------------------------------------------------------------


def year_fraction(now_seconds, expiry_seconds) -> np.ndarray:
    """Calendar-time year fraction to expiry, floored at zero."""
    now = np.asarray(now_seconds, dtype=np.float64)
    exp = np.asarray(expiry_seconds, dtype=np.float64)
    return np.maximum(exp - now, 0.0) / SECONDS_PER_YEAR


def forward_price(spot, tte, rate: float = 0.0) -> np.ndarray:
    """``F = S * exp(r * T)``. ``rate`` is a continuous annualised rate."""
    s = np.asarray(spot, dtype=np.float64)
    t = np.asarray(tte, dtype=np.float64)
    if rate == 0.0:
        return s.astype(np.float64)
    return s * np.exp(rate * t)


def black76_price(forward, strike, tte, vol, is_call) -> np.ndarray:
    """Vectorised undiscounted Black-76 premium."""
    f, k, t, v = (np.asarray(x, dtype=np.float64) for x in (forward, strike, tte, vol))
    c = np.asarray(is_call, dtype=np.bool_)
    f, k, t, v, c = np.broadcast_arrays(f, k, t, v, c)
    out = np.empty(f.shape, dtype=np.float64)
    flat = out.reshape(-1)
    ff, kk, tt, vv, cc = (a.reshape(-1) for a in (f, k, t, v, c))
    for i in range(flat.shape[0]):
        flat[i] = _black76(ff[i], kk[i], tt[i], vv[i], cc[i])
    return out


def implied_vol(price, forward, strike, tte, is_call) -> np.ndarray:
    """Vectorised IV inversion. Returns NaN where no root exists.

    NaN is a real answer here and must be propagated, not filled: a mark price
    below intrinsic is an exchange artifact, and any downstream statistic that
    silently imputes over it is measuring the imputation.
    """
    p, f, k, t = (np.asarray(x, dtype=np.float64) for x in (price, forward, strike, tte))
    c = np.asarray(is_call, dtype=np.bool_)
    p, f, k, t, c = np.broadcast_arrays(p, f, k, t, c)
    shape = p.shape
    return _implied_vol_vec(
        np.ascontiguousarray(p).reshape(-1),
        np.ascontiguousarray(f).reshape(-1),
        np.ascontiguousarray(k).reshape(-1),
        np.ascontiguousarray(t).reshape(-1),
        np.ascontiguousarray(c).reshape(-1),
    ).reshape(shape)
