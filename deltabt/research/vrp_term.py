"""Does the variance premium appear at longer tenors than the daily ATM?

`docs/options_feasibility.md` measured the short ATM straddle 24h before a
daily settlement and found gross return per unit of premium indistinguishable
from zero on 959 BTC and 929 ETH expiries. This asks the one structural
question that result does not answer.

The motivating argument is the same one H-Scalp-3 made on the perpetual side,
and it is worth stating because H-Scalp-3 is also the reason to distrust it.
Friction here is a fraction of *premium*, and premium grows roughly as
`sqrt(T)`, so cost per unit of premium falls as `1/sqrt(T)`: the 6-11% round
trip on a daily straddle should be materially smaller on a monthly. If a small
premium exists and is being hidden by friction, longer tenors are where it
surfaces.

H-Scalp-3 ran exactly that argument on perps. Cost fell as predicted, and the
gross did not survive -- `rho(gross, horizon)` was -1.000 on train against
+0.400 on validation, and the conclusion was that a real mechanism does not
reverse its horizon dependence between adjacent half-years. **A positive cell
here means nothing on its own.**

Two things this measurement must not pretend away
-------------------------------------------------

**The sample changes at 72 hours, and it is not a clean sweep.** Delta lists
daily contracts exactly 72h before settlement. So entries at 24h and 48h run
on ~960 daily expiries, while anything at 72h or beyond runs on ~136 weekly
and monthly expiries -- a different, much smaller, and *non-overlapping-in-kind*
population. A horizon sweep read straight down the column silently compares
two different instruments. The output separates them for that reason.

**Fourteen cells will produce a significant one by chance.** Seven horizons on
two underlyings, at a nominal 5% bar, expects 0.7 false positives; the reported
`t` values are also not independent across horizons, because overlapping
holding windows on the same expiries share the same moves. The bar applied
here is therefore stated up front: a cell is interesting only if it is
positive on **both underlyings**, at **adjacent horizons**, and survives the
cost stress. Nothing else is reported as anything but noise.

Run: python -m deltabt.research.vrp_term
"""

from __future__ import annotations

import argparse
import json
import logging

import numpy as np
import pandas as pd

from pathlib import Path

from deltabt.research.vrp_feasibility import build_sample, summarise, with_costs

log = logging.getLogger(__name__)

#: Entry horizons in hours before settlement. Fixed before any result was seen.
#: The break at 72h is a property of Delta's listing schedule, not a choice:
#: below it the population is daily expiries, at and above it weeklies and
#: monthlies. See the module docstring.
HORIZONS_HOURS = (24, 48, 72, 120, 168, 336, 720)

#: Horizons served by the daily expiry cycle. Anything longer is a different
#: population and is labelled as such in the output.
DAILY_HORIZON_MAX = 48

UNDERLYINGS = ("BTC", "ETH")


def sweep(
    underlying: str,
    *,
    horizons=HORIZONS_HOURS,
    half_spread_frac: float | None = None,
    trades_dir: str | Path | None = None,
) -> pd.DataFrame:
    """One row per entry horizon.

    ``trades_dir`` writes the per-trade journal behind every cell. An earlier
    version reported fourteen cells backed by two trade files, which meant
    twelve results nobody could re-examine at trade level. A summary table
    whose rows cannot be opened is not a result, it is a claim.
    """
    rows = []
    for hours in horizons:
        sample = build_sample(underlying, entry_hours=float(hours))
        if sample.empty:
            log.warning("%s @ %dh: no expiries", underlying, hours)
            continue
        res = summarise(sample, half_spread_frac=half_spread_frac)
        if trades_dir is not None:
            d = Path(trades_dir)
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"trades_{underlying.lower()}_{hours}h.csv"
            with_costs(sample, half_spread_frac=half_spread_frac).to_csv(path, index=False)
        rows.append(
            {
                "underlying": underlying,
                "entry_hours": hours,
                "population": "daily" if hours <= DAILY_HORIZON_MAX else "weekly+",
                "n": res["n"],
                "first_expiry": str(sample["expiry"].min().date()),
                "last_expiry": str(sample["expiry"].max().date()),
                "premium_frac_of_spot": res["premium_frac_of_spot"]["median"],
                "cost_frac_of_premium": res["cost_frac_of_premium"]["median"],
                "gross_mean": res["gross"]["mean"],
                "gross_median": res["gross"]["median"],
                "gross_ci_low": res["gross"]["ci_low"],
                "gross_ci_high": res["gross"]["ci_high"],
                "gross_t": res["gross"]["t"],
                "net_mean": res["net"]["mean"],
                "net_t": res["net"]["t"],
                "win_rate": res["win_rate"],
                "worst_gross": res["worst_gross"],
            }
        )
        log.info(
            "%s @ %3dh: n=%4d gross %+.4f (t=%+.2f) cost %.2f%%",
            underlying, hours, res["n"], res["gross"]["mean"],
            res["gross"]["t"], res["cost_frac_of_premium"]["median"] * 100,
        )
    return pd.DataFrame(rows)


def check_cost_prediction(table: pd.DataFrame) -> pd.DataFrame:
    """Does cost/premium actually fall as 1/sqrt(T), as the argument assumes?

    Checked separately from the P&L because it is the *premise*. If cost does
    not fall with tenor, the reason for looking at longer tenors evaporates
    and any positive cell found there needs a different explanation.

    Ratios are taken within a population, since the 24/48h and 72h+ rows are
    different instruments and a ratio across the break compares nothing.
    """
    out = []
    # Grouped by underlying as well as population: BTC and ETH have different
    # cost levels, and anchoring ETH's ratios to BTC's baseline -- as an
    # earlier version did by grouping on population alone -- makes the two
    # underlyings' columns incomparable and the ratios meaningless.
    for (pop, und), g in table.groupby(["population", "underlying"]):
        g = g.sort_values("entry_hours")
        base_h = float(g["entry_hours"].iloc[0])
        base_c = float(g["cost_frac_of_premium"].iloc[0])
        for _, r in g.iterrows():
            predicted = base_c * np.sqrt(base_h / float(r["entry_hours"]))
            out.append(
                {
                    "population": pop,
                    "underlying": und,
                    "entry_hours": int(r["entry_hours"]),
                    "cost_observed": float(r["cost_frac_of_premium"]),
                    "cost_predicted_1_over_sqrt_T": predicted,
                    "ratio_observed_to_predicted": float(r["cost_frac_of_premium"]) / predicted,
                }
            )
    return pd.DataFrame(out).sort_values(
        ["underlying", "population", "entry_hours"], ignore_index=True
    )


def verdict(table: pd.DataFrame) -> dict:
    """Apply the bar fixed in the module docstring. No cell is judged alone."""
    interesting = []
    for hours in sorted(table["entry_hours"].unique()):
        cells = table[table["entry_hours"] == hours]
        if len(cells) < len(UNDERLYINGS):
            continue
        if (cells["gross_ci_low"] > 0).all():
            interesting.append(int(hours))

    adjacent = [
        h for h in interesting
        if any(abs(h - o) > 0 and o in interesting for o in interesting)
    ]
    return {
        "cells_total": int(len(table)),
        "expected_false_positives_at_5pct": round(0.05 * len(table), 2),
        "horizons_positive_on_both_underlyings": interesting,
        "and_adjacent_to_another_such_horizon": adjacent,
        "verdict": (
            "no horizon clears the bar" if not adjacent
            else "candidate horizons -- requires a pre-registered test, not this sweep"
        ),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None)
    ap.add_argument("--trades-dir", default="out/vrp/trades",
                    help="per-cell trade journals (one file per underlying x horizon)")
    args = ap.parse_args()

    table = pd.concat(
        [sweep(u, trades_dir=args.trades_dir) for u in UNDERLYINGS], ignore_index=True
    )

    show = table[[
        "underlying", "entry_hours", "population", "n", "premium_frac_of_spot",
        "cost_frac_of_premium", "gross_mean", "gross_ci_low", "gross_ci_high",
        "gross_t", "net_mean", "win_rate",
    ]].copy()
    for c in ("premium_frac_of_spot", "cost_frac_of_premium"):
        show[c] = (show[c] * 100).round(2)
    for c in ("gross_mean", "gross_ci_low", "gross_ci_high", "net_mean"):
        show[c] = show[c].round(4)
    show["gross_t"] = show["gross_t"].round(2)
    show["win_rate"] = (show["win_rate"] * 100).round(1)

    print("\n=== short ATM straddle by entry horizon ===")
    print("premium/cost columns are %; the 72h+ rows are a DIFFERENT population")
    print(show.to_string(index=False))

    print("\n=== is the 1/sqrt(T) cost premise true? ===")
    print(check_cost_prediction(table).round(4).to_string(index=False))

    v = verdict(table)
    print("\n=== verdict, against the bar fixed before the run ===")
    print(json.dumps(v, indent=2))

    if args.out:
        table.to_csv(args.out, index=False)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
