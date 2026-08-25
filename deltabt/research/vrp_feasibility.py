"""Feasibility measurement for the variance risk premium on Delta India options.

**This is a measurement, not a hypothesis test.** It is run before any
pre-registration is written, exactly as the coverage measurement for H-XSec-1
was, so that the prereg can state its power honestly instead of discovering it
afterwards. Nothing here is a verdict and nothing here goes in the registry.

The question in its most model-free form
----------------------------------------

A short straddle is the cleanest expression of "implied exceeds realised". At
some time ``t`` before expiry ``E``, sell the at-the-money call and put; at
``E``, pay out their intrinsic value. The premium collected is the market's
price of the move; the payout is the move that happened.

Crucially this needs **no volatility model at all**. Delta publishes
``settlement_price`` on every expired option product, so the payout is ground
truth rather than something inferred from a last print, and the entry is the
exchange's own ``MARK:`` series rather than a trade that may not have occurred.
The IV inversion in :mod:`deltabt.options_pricing` is validated separately and
is not on this path -- if the two disagree later, this one is the arbiter.

What would make the family worth pursuing
-----------------------------------------

Gross short-straddle P&L per unit of premium collected must exceed the round
trip from :mod:`deltabt.options_costs`, which for an ATM contract priced at
1-2% of spot is roughly **3.9-5.0% of premium**. That is the bar. A gross edge
below it is the H-Scalp-2 outcome again -- a real mechanism that friction eats
-- and should be recorded as such rather than dressed up.

What this measurement deliberately does NOT do
----------------------------------------------

* It does not hedge delta. An unhedged straddle earns the variance premium
  *and* a directional lottery; the directional part is noise with a huge
  variance and will dominate the standard error. That is a power problem, not
  a bias, and it is stated here rather than discovered later.
* It does not sweep entry times, strike selection or tenor. Those are free
  parameters and choosing them by looking at results is the exact failure this
  repo's registry exists to prevent. One fixed rule, fixed in this file before
  the run.
* It does not gate on liquidity at ``t``. Delta serves no historical quotes,
  so any such filter would use today's liquidity on a past date -- the
  survivorship trap that `run_hxsec1.py` documents.

Run: python -m deltabt.research.vrp_feasibility
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging

import numpy as np
import pandas as pd

from deltabt.data.client import DeltaClient
from deltabt.data.options import OptionCatalog
from deltabt.data.store import CandleStore
from deltabt.options_costs import OptionCosts
from deltabt.research.stats import block_bootstrap_mean

log = logging.getLogger(__name__)

#: Spot index symbols, read from each option product's own ``spot_index``
#: field rather than guessed. The naming is NOT regular and guessing gets it
#: wrong: BTC is `.DEXBTUSD` but ETH is `.DEETHUSD` (no X) and XAUT is
#: `.DEXAUTUSD`. All three were confirmed to serve 5m candles.
SPOT_INDEX = {"BTC": ".DEXBTUSD", "ETH": ".DEETHUSD", "XAUT": ".DEXAUTUSD"}

#: Entry is a fixed number of hours before settlement, identical for every
#: expiry. Fixed here, before any result is seen.
ENTRY_HOURS_BEFORE = 24.0

#: Resolution for the mark series. 5m is fine for a single point read and
#: costs a fraction of the requests 1m would.
RESOLUTION = "5m"

#: A mark read is accepted only if a bar exists within this many seconds of
#: the intended entry instant. Wider than one bar so a single missing minute
#: does not drop an expiry, tight enough that a stale mark cannot masquerade
#: as a live one.
MAX_STALENESS_SECONDS = 1800


def _read_at(df: pd.DataFrame, ts: int, column: str = "close") -> float:
    """Last value at or before ``ts``, or NaN if none is close enough.

    Strictly backward-looking: taking the nearest bar in either direction
    would read a price from after the decision instant, which is the same
    same-bar look-ahead that manufactured this program's one false positive.
    """
    if df.empty:
        return np.nan
    prior = df.loc[df["time"] <= ts]
    if prior.empty:
        return np.nan
    row = prior.iloc[-1]
    if ts - int(row["time"]) > MAX_STALENESS_SECONDS:
        return np.nan
    return float(row[column])


def build_sample(
    underlying: str = "BTC",
    *,
    entry_hours: float = ENTRY_HOURS_BEFORE,
    max_expiries: int | None = None,
    store: CandleStore | None = None,
    catalog: OptionCatalog | None = None,
) -> pd.DataFrame:
    """One row per settled expiry: ATM straddle premium in, intrinsic out."""
    catalog = catalog or OptionCatalog()
    store = store or CandleStore()
    df = catalog.read()
    if df.empty:
        raise RuntimeError(
            "option catalog is empty -- run OptionCatalog().refresh() first"
        )

    spot_symbol = SPOT_INDEX.get(underlying)
    if spot_symbol is None:
        raise ValueError(f"no spot index configured for {underlying}")

    settled = df[(df["underlying"] == underlying) & df["settlement_price"].notna()]
    expiries = sorted(settled["expiry_ts"].unique().tolist())
    if max_expiries:
        expiries = expiries[-max_expiries:]
    log.info("%s: %d settled expiries in catalog", underlying, len(expiries))

    entry_offset = int(entry_hours * 3600)
    rows: list[dict] = []

    for expiry in expiries:
        entry_ts = expiry - entry_offset
        chain = settled[settled["expiry_ts"] == expiry]
        if chain.empty:
            continue

        # Spot at entry, from the index the options settle against.
        spot_df = store.load(
            spot_symbol, "ltp", RESOLUTION, entry_ts - 7200, entry_ts + 60
        )
        spot = _read_at(spot_df, entry_ts)
        if not np.isfinite(spot) or spot <= 0:
            log.debug("no spot for %s at %s", underlying, entry_ts)
            continue

        # Only strikes already LISTED at the entry instant are selectable.
        # Delta adds strikes through an expiry's life as spot moves, and the
        # final chain therefore contains contracts that did not exist at t.
        # Picking ATM from the final chain and then dropping it for want of a
        # mark would silently keep only the expiries where spot barely moved
        # between listing and entry -- a selection effect, not a data gap.
        listed = chain[(chain["launch_ts"] > 0) & (chain["launch_ts"] <= entry_ts)]
        if listed.empty:
            continue

        # ATM = the listed strike nearest spot that has BOTH a call and a put.
        strikes = listed.groupby("strike")["is_call"].nunique()
        both = strikes[strikes == 2].index.to_numpy(dtype=float)
        if both.size == 0:
            continue
        atm = float(both[np.argmin(np.abs(both - spot))])

        leg = {}
        ok = True
        for is_call in (True, False):
            m = (listed["strike"] == atm) & (listed["is_call"] == is_call)
            if not m.any():
                ok = False
                break
            row = listed[m].iloc[0]
            mark_df = store.load(
                row["symbol"], "mark", RESOLUTION, entry_ts - 7200, entry_ts + 60
            )
            premium = _read_at(mark_df, entry_ts)
            if not np.isfinite(premium) or premium <= 0:
                ok = False
                break
            leg["C" if is_call else "P"] = dict(
                symbol=row["symbol"],
                premium=premium,
                settlement=float(row["settlement_price"]),
                contract_value=float(row["contract_value"]),
                tick_size=float(row["tick_size"]),
                taker_fee=float(row["taker_fee"]),
            )
        if not ok:
            continue

        collected = leg["C"]["premium"] + leg["P"]["premium"]
        paid_out = leg["C"]["settlement"] + leg["P"]["settlement"]
        if collected <= 0:
            continue

        rows.append(
            dict(
                underlying=underlying,
                expiry_ts=int(expiry),
                expiry=pd.Timestamp(expiry, unit="s", tz="UTC"),
                entry_ts=int(entry_ts),
                spot_at_entry=spot,
                strike=atm,
                call_symbol=leg["C"]["symbol"],
                put_symbol=leg["P"]["symbol"],
                call_premium=leg["C"]["premium"],
                put_premium=leg["P"]["premium"],
                straddle_premium=collected,
                call_settlement=leg["C"]["settlement"],
                put_settlement=leg["P"]["settlement"],
                straddle_payout=paid_out,
                contract_value=leg["C"]["contract_value"],
                tick_size=leg["C"]["tick_size"],
                taker_fee=leg["C"]["taker_fee"],
                # The realised absolute move the straddle was pricing. For an
                # ATM straddle the payout IS |S_E - K|, so this is a
                # cross-check on the settlement figures, not a second source.
                realised_abs_move=paid_out,
                premium_frac_of_spot=collected / spot,
                # Gross short-straddle return per unit of premium collected.
                gross_return_on_premium=(collected - paid_out) / collected,
            )
        )

    return pd.DataFrame(rows)


def cost_per_expiry(sample: pd.DataFrame, *, half_spread_frac=None) -> np.ndarray:
    """Round trip as a fraction of straddle premium, one value per expiry.

    Public and separate from :func:`summarise` so it can be persisted into the
    per-trade file rather than only existing inside an aggregate. The perpetual
    side's ``trades_*.csv`` carries ``cost_r`` as its own column precisely so
    friction can never be folded invisibly into a reported return, and the
    options journals must meet the same bar.

    The fee rate stepped 0.0003 -> 0.00015 -> 0.0001 during the sample, so
    costs are built PER EXPIRY from that expiry's own catalog rate. Taking one
    rate for the whole sample -- as an earlier version did, using the first row
    -- applied 2024's 0.03% to 2026 and inflated modelled friction roughly
    threefold at the recent end.
    """
    rates = sample["taker_fee"].to_numpy(dtype=float)
    spot = sample["spot_at_entry"].to_numpy(dtype=float)
    contract_value = float(sample["contract_value"].iloc[0])
    tick_size = float(sample["tick_size"].iloc[0])

    rt = np.zeros(len(sample))
    for col in ("call_premium", "put_premium"):
        prem = sample[col].to_numpy(dtype=float)
        leg_rt = np.empty(len(sample))
        # Each leg pays its own round trip, at its own premium, because the
        # fee cap is per contract rather than per strategy.
        for rate in np.unique(rates):
            m = rates == rate
            costs = OptionCosts(
                symbol="ATM",
                contract_value=contract_value,
                tick_size=tick_size,
                fee_rate=float(rate),
                **({} if half_spread_frac is None else {"half_spread_frac": half_spread_frac}),
            )
            leg_rt[m] = costs.round_trip_frac_of_premium(prem[m], spot[m])
        rt += leg_rt * prem
    return rt / sample["straddle_premium"].to_numpy(dtype=float)


def with_costs(sample: pd.DataFrame, *, half_spread_frac=None) -> pd.DataFrame:
    """The sample plus the per-trade cost and net columns, ready to persist."""
    if sample.empty:
        return sample
    out = sample.copy()
    out["cost_frac_of_premium"] = cost_per_expiry(sample, half_spread_frac=half_spread_frac)
    out["net_return_on_premium"] = (
        out["gross_return_on_premium"] - out["cost_frac_of_premium"]
    )
    # The venue is Indian and every prior per-trade file in this repository
    # carries an IST column alongside UTC; operators read IST.
    out["entry_time_utc"] = pd.to_datetime(out["entry_ts"], unit="s", utc=True)
    out["entry_time_ist"] = out["entry_time_utc"].dt.tz_convert("Asia/Kolkata")
    out["exit_time_utc"] = pd.to_datetime(out["expiry_ts"], unit="s", utc=True)
    out["exit_reason"] = "settlement"
    out["win"] = out["gross_return_on_premium"] > 0
    return out


def summarise(sample: pd.DataFrame, *, half_spread_frac: float | None = None) -> dict:
    """Gross vs net short-straddle economics, with serial-dependence-aware CIs."""
    if sample.empty:
        return {"n": 0}

    cost_frac = cost_per_expiry(sample, half_spread_frac=half_spread_frac)

    gross = sample["gross_return_on_premium"].to_numpy()
    net = gross - cost_frac

    out = {"n": int(len(sample))}
    for label, series in (("gross", gross), ("net", net)):
        b = block_bootstrap_mean(series, mean_block=5.0, n_boot=5000, seed=7)
        out[label] = {
            "mean": float(np.mean(series)),
            "median": float(np.median(series)),
            "ci_low": b["ci_low"],
            "ci_high": b["ci_high"],
            "t": b["t"],
        }
    out["cost_frac_of_premium"] = {
        "mean": float(np.mean(cost_frac)),
        "median": float(np.median(cost_frac)),
    }
    out["premium_frac_of_spot"] = {
        "median": float(np.median(sample["premium_frac_of_spot"])),
    }
    out["win_rate"] = float(np.mean(gross > 0))
    out["worst_gross"] = float(np.min(gross))
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--underlying", default="BTC")
    ap.add_argument("--entry-hours", type=float, default=ENTRY_HOURS_BEFORE)
    ap.add_argument("--max-expiries", type=int, default=None)
    ap.add_argument("--out", default=None, help="write the per-expiry sample here")
    args = ap.parse_args()

    sample = build_sample(
        args.underlying,
        entry_hours=args.entry_hours,
        max_expiries=args.max_expiries,
    )
    print(f"\nsample: {len(sample)} settled expiries with a complete ATM straddle")
    if sample.empty:
        return
    print(f"date range: {sample['expiry'].min()} -> {sample['expiry'].max()}")

    res = summarise(sample)
    print(f"\nmedian ATM straddle premium: {res['premium_frac_of_spot']['median']:.3%} of spot")
    print(f"modelled round trip        : {res['cost_frac_of_premium']['median']:.3%} of premium")
    print()
    print("short ATM straddle, entry 24h before settlement, held to settlement")
    print(f"  n                 : {res['n']}")
    for label in ("gross", "net"):
        r = res[label]
        print(
            f"  {label:<5s} return/premium: {r['mean']:+.4f}  "
            f"median {r['median']:+.4f}  "
            f"CI [{r['ci_low']:+.4f}, {r['ci_high']:+.4f}]  t={r['t']:+.2f}"
        )
    print(f"  win rate (gross)  : {res['win_rate']:.1%}")
    print(f"  worst single expiry: {res['worst_gross']:+.3f} of premium")

    if args.out:
        with_costs(sample).to_csv(args.out, index=False)
        print(f"\nwrote {args.out}  ({len(sample)} trades, cost and net per row)")


if __name__ == "__main__":
    main()
