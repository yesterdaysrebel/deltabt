"""Cost model for Delta Exchange India options, and the cost law it implies.

The perpetual side of this program produced exactly one durable positive
result, and it was a cost identity rather than a strategy::

    cost_r = round_trip_rate / stop_pct

Options need the same treatment before any hypothesis is tested, because the
fee is charged on a **different base** and the law comes out a different shape.

Facts, each verified against the live API except where marked:

* Fees are charged on **notional** -- ``contract_value * spot`` -- not on the
  premium paid. There is no maker/taker split on options the way there is on
  perps: ``maker_commission_rate == taker_commission_rate`` on all 145,406
  option products in the catalog, without exception.
* **The rate is not constant, and it is not constant over time.** Measured
  across the catalog by expiry month:

  ===================  ==========
  expiries             fee rate
  ===================  ==========
  2024-01 .. 2025-07   0.0300%
  2025-07 .. 2025-12   0.0150%
  2025-12 onward       0.0100%
  ===================  ==========

  A **3x fee cut in two steps** inside the available history. Hardcoding
  today's 0.01% and backtesting 2024 understates friction threefold, and any
  study spanning the change is comparing two cost regimes. Always construct
  costs per contract from the catalog row -- :meth:`OptionCosts.from_catalog_row`
  -- never from :data:`DEFAULT_OPTION_FEE_RATE`, which is only the rate that
  happens to be current.
* 18% GST applies on top, as it does on perps.
* The fee is **capped as a percentage of premium**. This is the one input NOT
  confirmable from the API: `delta.exchange/fees` states a cap but renders two
  figures (10% and 3.5%). :data:`DEFAULT_PREMIUM_FEE_CAP` therefore defaults to
  the *higher* figure, which is the conservative choice -- a higher cap means a
  higher fee. Anything sensitive to this must be reported at both values.
  Measured on the ATM-straddle sample the cap never binds (minimum leg premium
  was 0.107% of spot against a 0.1%-of-spot threshold at the 0.01% rate), so
  the ambiguity is currently harmless there -- but it dominates any study that
  touches the cheap OTM wing.
* Quoted half-spread on the traded subset measured **1.34% of mid** (p25 0.90%,
  p75 2.75%) on a live snapshot of 720 contracts. Delta serves no spread
  history, so this is an assumption, not a measurement, on any past date.

The law
-------

Write ``p = premium / spot`` -- the premium as a fraction of spot, which is the
natural moneyness-and-tenor summary of an option's price. Per side::

    fee / premium = min(fee_rate / p, cap) * gst
    round_trip / premium = 2 * (fee/premium + half_spread_frac)

Two consequences that have no perpetual analogue:

1. **Cost per unit of premium rises as the option gets cheaper.** On a perp,
   friction is a fixed fraction of notional. On an option, a contract priced at
   0.1% of spot pays ten times the premium-relative fee of one priced at 1%.
2. **The cap creates a hard ceiling regime.** Below ``p = fee_rate / cap``
   the notional-based fee would exceed the cap, so the fee becomes a flat
   percentage of premium -- at the 10% cap that is a **20% round trip before
   spread**. Cheap far-OTM daily options sit squarely in that regime, and no
   edge of any plausible size survives there.

Which is to say: the cheap lottery-ticket end of this surface is
uninvestable on fees alone, and that can be asserted before any backtest runs.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from deltabt.config import GST_MULTIPLIER

#: The rate current as of 2026-08. **A fallback, not a constant** -- the rate
#: has stepped 0.0003 -> 0.00015 -> 0.0001 inside the available history, so
#: anything touching a date before 2025-12 must read the rate from the
#: contract's own catalog row instead. See the module docstring.
DEFAULT_OPTION_FEE_RATE = 0.0001

#: Fee ceiling as a fraction of premium. See the module docstring -- this is
#: the one number not confirmed against the API. The higher of the two
#: published figures is the default because it is the pessimistic one.
DEFAULT_PREMIUM_FEE_CAP = 0.10

#: The promotional / alternative figure, kept so sensitivity can be reported
#: at both without a magic number appearing at the call site.
ALT_PREMIUM_FEE_CAP = 0.035

#: Median quoted half-spread as a fraction of mid, measured live across the
#: 720 contracts turning over more than $10k/24h. NOT a historical figure:
#: Delta publishes no quote history, so every backtest that uses this is
#: assuming today's liquidity applied on the test date.
DEFAULT_HALF_SPREAD_FRAC = 0.0134


@dataclass(frozen=True)
class OptionCosts:
    """Per-contract cost specification for one option product."""

    symbol: str
    contract_value: float
    tick_size: float
    fee_rate: float = DEFAULT_OPTION_FEE_RATE
    premium_fee_cap: float = DEFAULT_PREMIUM_FEE_CAP
    half_spread_frac: float = DEFAULT_HALF_SPREAD_FRAC
    gst_multiplier: float = GST_MULTIPLIER

    @classmethod
    def from_catalog_row(cls, row, **overrides) -> "OptionCosts":
        base = dict(
            symbol=row["symbol"],
            contract_value=float(row["contract_value"]),
            tick_size=float(row["tick_size"]),
            fee_rate=float(row["taker_fee"]),
        )
        base.update(overrides)
        return cls(**base)

    # -- fees ---------------------------------------------------------------

    def fee_per_contract(self, premium, spot):
        """One-side fee in USD per contract, cap and GST applied.

        ``premium`` and ``spot`` are both per unit of underlying; the contract
        multiplier is applied once, here, so callers never double-count it.
        """
        premium = np.asarray(premium, dtype=np.float64)
        spot = np.asarray(spot, dtype=np.float64)
        notional_fee = self.fee_rate * spot * self.contract_value
        capped = self.premium_fee_cap * premium * self.contract_value
        return np.minimum(notional_fee, capped) * self.gst_multiplier

    def fee_frac_of_premium(self, premium, spot):
        """One-side fee as a fraction of the premium -- the law's core term.

        Equals ``min(fee_rate / p, cap) * gst`` where ``p = premium / spot``,
        independent of contract size. Returns NaN at zero premium rather than
        infinity, because an untraded contract has no defined relative cost.
        """
        premium = np.asarray(premium, dtype=np.float64)
        spot = np.asarray(spot, dtype=np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            p = np.where(premium > 0, premium / spot, np.nan)
            return np.minimum(self.fee_rate / p, self.premium_fee_cap) * self.gst_multiplier

    # -- the law ------------------------------------------------------------

    def round_trip_frac_of_premium(self, premium, spot, *, half_spread_frac=None):
        """Round-trip friction as a fraction of premium: the options cost law.

        This is the direct analogue of ``cost_r`` on the perpetual side. For a
        long option held to a defined-risk outcome, premium *is* the risk, so
        this number is cost per unit of R.
        """
        hs = self.half_spread_frac if half_spread_frac is None else half_spread_frac
        return 2.0 * (self.fee_frac_of_premium(premium, spot) + hs)

    @property
    def cap_binds_below(self) -> float:
        """Premium/spot ratio below which the premium cap replaces the fee.

        Below this the fee is a flat ``premium_fee_cap`` of premium per side,
        regardless of how cheap the contract gets.
        """
        return self.fee_rate / self.premium_fee_cap

    # -- rounding -----------------------------------------------------------

    def round_premium(self, price, *, direction: int = 0):
        """Snap a premium to the tick grid. ``-1`` floors, ``+1`` ceils."""
        if self.tick_size <= 0:
            return price
        q = np.asarray(price, dtype=np.float64) / self.tick_size
        if direction < 0:
            q = np.floor(q)
        elif direction > 0:
            q = np.ceil(q)
        else:
            q = np.round(q)
        return q * self.tick_size


def break_even_move(premium_frac_of_spot, cost_frac_of_premium):
    """How far the option must gain, in premium terms, to clear friction.

    Expressed as a multiple of premium so it is directly comparable to the
    perpetual side's break-even win rate table.
    """
    return np.asarray(cost_frac_of_premium, dtype=np.float64)
