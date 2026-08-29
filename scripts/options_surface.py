"""Causal surface construction for Delta India option quotes. NO P&L HERE.

WHY THIS EXISTS AT A GATE RATHER THAN IN AN EXPERIMENT
    Section 10 of the options research gate requires automated proof that a
    surface can be built at time t from information available at t. That is a
    property of the DATA and the CONSTRUCTION CODE, not of any strategy, so it
    is established before a hypothesis is frozen rather than after.

    Nothing in this module computes a return, a fill or a P&L. It answers one
    question: can the quantities a volatility hypothesis needs -- ATM implied
    vol, a term-structure slope, a 25-delta skew -- be formed at each snapshot
    without touching a later one?

THE ONE PROPERTY EVERYTHING RESTS ON
    Delta publishes `mark_iv`, `delta`, `gamma`, `vega`, `theta` and
    `spot_price` IN EACH SNAPSHOT ROW. Nothing here inverts a price, fits a
    smile or interpolates between strikes. That removes the largest leakage
    surface in options research -- a fitted surface silently borrowing a later
    quote -- because there is no fit. The cost of that is that every measure is
    limited to strikes the exchange actually quoted at that instant.

WHAT IS DELIBERATELY REFUSED
    `nearest_delta` returns None rather than the closest available contract
    when nothing lies inside the tolerance band. An options study that quietly
    accepts a 0.40-delta option as "the 25-delta" is measuring a different
    quantity on the days its data is thin, which is precisely when the answer
    matters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

SYMBOL_RE = re.compile(r"^([CP])-([A-Z0-9]+)-([0-9.]+)-(\d{2})(\d{2})(\d{2})$")

#: Expiries settle at 12:00 UTC on Delta India.
SETTLE_HOUR = 12


def parse_symbol(sym: str) -> dict | None:
    """Strike, right and expiry read off the CONTRACT NAME.

    Deliberately not read from `data/meta/options_catalog.parquet`: that file
    is a snapshot taken 2026-08-24 and 892 of the 1,929 quoted contracts were
    listed after it, so a catalog join silently drops 23.3% of rows. The name
    is self-describing and complete.
    """
    m = SYMBOL_RE.match(sym)
    if not m:
        return None
    right, under, strike, dd, mm, yy = m.groups()
    return {
        "right": right,
        "underlying": under,
        "strike": float(strike),
        "expiry": pd.Timestamp(f"20{yy}-{mm}-{dd} {SETTLE_HOUR:02d}:00", tz="UTC"),
    }


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Add ts, right, strike, expiry, tte and mid. No cross-row information."""
    p = df["symbol"].map(parse_symbol)
    keep = p.notna()
    out = df[keep].copy()
    p = p[keep]
    out["ts"] = pd.to_datetime(out["snapshot_ts"], unit="s", utc=True)
    out["right"] = [x["right"] for x in p]
    out["strike"] = [x["strike"] for x in p]
    out["expiry"] = [x["expiry"] for x in p]
    out["tte_years"] = (out["expiry"] - out["ts"]).dt.total_seconds() / (365 * 86400)
    out["mid"] = (out["best_bid"] + out["best_ask"]) / 2.0
    out["half_spread_frac"] = (out["best_ask"] - out["best_bid"]) / 2.0 / out["mid"]
    return out


def two_sided(df: pd.DataFrame) -> pd.DataFrame:
    """Rows a trader could actually have hit on both sides at that instant."""
    return df[(df["best_bid"] > 0) & (df["best_ask"] > 0)
              & (df["best_bid"] <= df["best_ask"])
              & df["mark_iv"].notna() & df["delta"].notna()
              & (df["spot_price"] > 0) & (df["tte_years"] > 0)]


def surface_at(df: pd.DataFrame, ts: pd.Timestamp) -> pd.DataFrame:
    """Every quote AT this snapshot and nothing else.

    Equality, not `<=`: a surface built from "everything up to t" would carry
    stale quotes from earlier snapshots forward and make the age of a quote a
    free parameter.
    """
    return df[df["ts"] == ts]


def select_expiry(surface: pd.DataFrame, min_days: float,
                  max_days: float) -> pd.Timestamp | None:
    """Nearest expiry whose tenor falls in [min_days, max_days] at this snapshot.

    The rule is predefined and uses only this snapshot's own expiry ladder. It
    never consults liquidity, open interest or anything dated later -- picking
    the expiry that turned out to be liquid is the classic options look-ahead.
    """
    tte_days = surface["tte_years"] * 365
    ok = surface[(tte_days >= min_days) & (tte_days <= max_days)]
    if ok.empty:
        return None
    return pd.Timestamp(ok.loc[(ok["tte_years"] * 365 - min_days).abs().idxmin(), "expiry"])


def nearest_delta(chain: pd.DataFrame, right: str, target_abs_delta: float,
                  tolerance: float) -> pd.Series | None:
    """The contract whose PUBLISHED |delta| is closest to the target.

    Uses the exchange's own delta from this snapshot's row. Returns None when
    nothing lies within ``tolerance`` rather than substituting the closest
    available strike.
    """
    side = chain[chain["right"] == right]
    if side.empty:
        return None
    gap = (side["delta"].abs() - target_abs_delta).abs()
    if gap.min() > tolerance:
        return None
    return side.loc[gap.idxmin()]


@dataclass
class SurfacePoint:
    ts: pd.Timestamp
    underlying: str
    expiry: pd.Timestamp
    tte_years: float
    spot: float
    atm_iv: float
    iv_25d_put: float | None
    iv_25d_call: float | None
    skew_25d: float | None            # put IV minus call IV
    atm_half_spread: float


def surface_point(df: pd.DataFrame, ts: pd.Timestamp, underlying: str,
                  min_days: float, max_days: float, *,
                  delta_tol: float = 0.05) -> SurfacePoint | None:
    """One causal observation of the surface. Returns None if it cannot be formed."""
    snap = two_sided(surface_at(df, ts))
    snap = snap[snap["underlying"] == underlying]
    if snap.empty:
        return None
    exp = select_expiry(snap, min_days, max_days)
    if exp is None:
        return None
    chain = snap[snap["expiry"] == exp]
    atm = nearest_delta(chain, "C", 0.50, delta_tol)
    if atm is None:
        return None
    p25 = nearest_delta(chain, "P", 0.25, delta_tol)
    c25 = nearest_delta(chain, "C", 0.25, delta_tol)
    skew = (float(p25["mark_iv"] - c25["mark_iv"])
            if p25 is not None and c25 is not None else None)
    return SurfacePoint(
        ts=ts, underlying=underlying, expiry=exp,
        tte_years=float(atm["tte_years"]), spot=float(atm["spot_price"]),
        atm_iv=float(atm["mark_iv"]),
        iv_25d_put=None if p25 is None else float(p25["mark_iv"]),
        iv_25d_call=None if c25 is None else float(c25["mark_iv"]),
        skew_25d=skew, atm_half_spread=float(atm["half_spread_frac"]),
    )
