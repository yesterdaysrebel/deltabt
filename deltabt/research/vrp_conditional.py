"""Is the variance premium zero everywhere, or only on average?

The unconditional result is settled: short ATM straddles earn nothing gross, at
every tenor, on both underlyings. A zero average is not the same as zero
everywhere, and the standard next question in the volatility literature is
whether the premium is *state-dependent* -- large when volatility is richly
priced, absent or negative otherwise.

This is a conditioning search, which is precisely where this repository has
been burned before. `PROGRAM_SUMMARY.md` lesson 6: H-Scalp-3's 120m cell showed
`t = 2.339`, a clean confidence interval and survival of a cost stress -- on
one cell out of 360, with train at -0.0259. It was noise. The defences here are
fixed before the run and are not negotiable afterwards:

1. **Three conditioning variables, chosen for economic reasons, not scanned.**
   IV level, IV minus trailing realised vol, and trailing realised vol. No
   others will be added after seeing results. Anything else would be a scan.
2. **Terciles, not optimised cutpoints.** A threshold fitted to the data is a
   free parameter that manufactures significance.
3. **The bar is stated in advance:** a state is interesting only if the
   top-minus-bottom spread is positive on **both underlyings**, with a
   bootstrap CI excluding zero on both, **and** the effect exceeds the cost in
   that state. Six tests at a nominal 5% expect 0.3 false positives; one
   significant cell means nothing.
4. **Costs are applied per state**, because cheap states and rich states have
   different premium levels and therefore different cost/premium ratios. A
   gross spread that vanishes net is not an edge.

Everything runs on data already collected -- the straddle samples and the
IV/realised-vol table -- so this adds no API load and can run alongside a fetch.

Run: python -m deltabt.research.vrp_conditional
"""

from __future__ import annotations

import argparse
import json
import logging

import numpy as np
import pandas as pd
from pathlib import Path

from deltabt.options_costs import OptionCosts
from deltabt.research.stats import block_bootstrap_mean

log = logging.getLogger(__name__)

UNDERLYINGS = ("BTC", "ETH")

#: Fixed before the run. Each has a reason to be here; none was chosen by
#: looking at what worked.
CONDITIONS = {
    # If volatility is ever richly priced, it should be when implied is high.
    "iv_level": "atm_iv",
    # The direct signal: implied minus a backward-looking forecast. This is
    # the closest thing to a tradeable "vol is expensive" indicator.
    "iv_minus_trailing": "iv_minus_trailing",
    # Control: premium may simply track the volatility environment rather than
    # any richness in the pricing of it.
    "trailing_rv": "rv_trailing",
}

N_BUCKETS = 3


def load(straddle_csv: str, ivic_csv: str, underlying: str) -> pd.DataFrame:
    """Join the straddle outcome to the state variables observed at entry."""
    s = pd.read_csv(straddle_csv, parse_dates=["expiry"])
    v = pd.read_csv(ivic_csv, parse_dates=["expiry"])
    v = v[v["underlying"] == underlying]
    df = s.merge(
        v[["expiry", "atm_iv", "rv_trailing", "rv_forward"]], on="expiry", how="inner"
    )
    df["iv_minus_trailing"] = df["atm_iv"] - df["rv_trailing"]
    return df.dropna(subset=["atm_iv", "rv_trailing"])


def _cost_frac(df: pd.DataFrame) -> np.ndarray:
    """Round trip as a fraction of straddle premium, per expiry, per fee era."""
    rates = df["taker_fee"].to_numpy(dtype=float)
    spot = df["spot_at_entry"].to_numpy(dtype=float)
    cv = float(df["contract_value"].iloc[0])
    ts = float(df["tick_size"].iloc[0])
    rt = np.zeros(len(df))
    for col in ("call_premium", "put_premium"):
        prem = df[col].to_numpy(dtype=float)
        leg = np.empty(len(df))
        for rate in np.unique(rates):
            m = rates == rate
            c = OptionCosts(symbol="ATM", contract_value=cv, tick_size=ts, fee_rate=float(rate))
            leg[m] = c.round_trip_frac_of_premium(prem[m], spot[m])
        rt += leg * prem
    return rt / df["straddle_premium"].to_numpy(dtype=float)


def bucket_test(df: pd.DataFrame, state_col: str) -> dict:
    """Tercile the state variable and compare top against bottom."""
    x = df[state_col].to_numpy(dtype=float)
    gross = df["gross_return_on_premium"].to_numpy(dtype=float)
    cost = _cost_frac(df)
    net = gross - cost

    # Terciles by rank so the split is balanced regardless of distribution.
    q = pd.qcut(pd.Series(x).rank(method="first"), N_BUCKETS, labels=False).to_numpy()

    out = {"state": state_col, "buckets": [], "_assignment": q, "_cost": cost, "_net": net}
    for b in range(N_BUCKETS):
        m = q == b
        gb = block_bootstrap_mean(gross[m], mean_block=5.0, n_boot=4000, seed=13 + b)
        out["buckets"].append(
            {
                "bucket": int(b),
                "n": int(m.sum()),
                "state_range": [float(np.min(x[m])), float(np.max(x[m]))],
                "gross_mean": float(np.mean(gross[m])),
                "gross_ci_low": gb["ci_low"],
                "gross_ci_high": gb["ci_high"],
                "gross_t": gb["t"],
                "cost_median": float(np.median(cost[m])),
                "net_mean": float(np.mean(net[m])),
            }
        )

    top, bot = q == (N_BUCKETS - 1), q == 0
    spread = gross[top].mean() - gross[bot].mean()
    # Bootstrap the difference directly rather than differencing two CIs,
    # which would overstate the uncertainty of the contrast.
    rng = np.random.default_rng(29)
    gt, gb_ = gross[top], gross[bot]
    boots = np.array([
        gt[rng.integers(0, gt.size, gt.size)].mean()
        - gb_[rng.integers(0, gb_.size, gb_.size)].mean()
        for _ in range(4000)
    ])
    se = float(np.std(boots))
    out["top_minus_bottom"] = {
        "gross": float(spread),
        "se": se,
        "t": float(spread / se) if se > 0 else float("nan"),
        "ci_low": float(np.quantile(boots, 0.025)),
        "ci_high": float(np.quantile(boots, 0.975)),
        "net": float(net[top].mean() - net[bot].mean()),
    }
    return out


def verdict(results: dict) -> dict:
    """The bar, applied exactly as written before the run."""
    interesting = []
    for state in CONDITIONS:
        cells = [results[u][state]["top_minus_bottom"] for u in results if state in results[u]]
        if len(cells) < len(UNDERLYINGS):
            continue
        if all(c["ci_low"] > 0 for c in cells) and all(c["net"] > 0 for c in cells):
            interesting.append(state)
    n_tests = sum(len(v) for v in results.values())
    return {
        "tests_run": n_tests,
        "expected_false_positives_at_5pct": round(0.05 * n_tests, 2),
        "states_clearing_the_bar": interesting,
        "verdict": (
            "no conditioning state clears the bar" if not interesting
            else "candidate state -- requires a pre-registered test, not this search"
        ),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--straddle-dir", default="out/vrp")
    ap.add_argument("--ivic", default="out/vrp/iv_ic.csv")
    ap.add_argument("--out", default=None)
    ap.add_argument("--trades-dir", default="out/vrp/trades")
    args = ap.parse_args()

    results: dict = {}
    journals: dict = {}
    for u in UNDERLYINGS:
        df = load(f"{args.straddle_dir}/{u.lower()}_atm_straddle.csv", args.ivic, u)
        if df.empty:
            log.warning("%s: empty join", u)
            continue
        results[u] = {}
        print(f"\n=== {u} === n={len(df)}  {df['expiry'].min().date()} -> {df['expiry'].max().date()}")
        journal = df.copy()
        for name, col in CONDITIONS.items():
            r = bucket_test(df, col)
            # Persist the bucket each expiry landed in, so a cell that clears
            # the bar can be reopened at trade level rather than taken on
            # trust. The iv_level candidate died on exactly this kind of
            # re-examination.
            journal[f"bucket_{name}"] = r.pop("_assignment")
            journal["cost_frac_of_premium"] = r.pop("_cost")
            journal["net_return_on_premium"] = r.pop("_net")
            results[u][name] = r
            journals[u] = journal
            print(f"\n  state: {name}")
            for b in r["buckets"]:
                print(
                    f"    tercile {b['bucket']} n={b['n']:<4d} "
                    f"[{b['state_range'][0]:+.3f},{b['state_range'][1]:+.3f}]  "
                    f"gross {b['gross_mean']:+.4f} CI[{b['gross_ci_low']:+.4f},{b['gross_ci_high']:+.4f}] "
                    f"cost {b['cost_median']:.3f}  net {b['net_mean']:+.4f}"
                )
            t = r["top_minus_bottom"]
            print(f"    top - bottom: gross {t['gross']:+.4f} "
                  f"CI[{t['ci_low']:+.4f},{t['ci_high']:+.4f}] t={t['t']:+.2f}  net {t['net']:+.4f}")

    v = verdict(results)
    print("\n=== verdict, against the bar fixed before the run ===")
    print(json.dumps(v, indent=2))

    if args.out:
        with open(args.out, "w") as f:
            json.dump({"results": results, "verdict": v}, f, indent=2, default=float)
        print(f"\nwrote {args.out}")
    if args.trades_dir:
        d = Path(args.trades_dir)
        d.mkdir(parents=True, exist_ok=True)
        for u, j in journals.items():
            path = d / f"trades_conditional_{u.lower()}.csv"
            j.to_csv(path, index=False)
            print(f"wrote {path}  ({len(j)} trades with bucket assignment)")


if __name__ == "__main__":
    main()
