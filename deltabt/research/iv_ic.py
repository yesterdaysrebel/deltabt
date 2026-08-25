"""Does implied vol carry any information about realised vol at all?

The variance-premium work answered an economic question -- can you *earn*
anything selling volatility here -- and answered it no, at every tenor from
24h to 30 days on both underlyings. This answers the prior question, which is
diagnostic rather than economic: **does the surface know anything?**

The two come apart, and which one is true matters:

* ``IC = 0``. Implied vol is uninformative about subsequent realised vol. The
  surface is noise, and the flat P&L is a consequence rather than a
  coincidence. Every volatility strategy on this venue is closed, not just the
  short straddle.
* ``IC > 0`` with flat P&L. The surface *is* informative and is priced
  correctly. That is a well-functioning options market, and a much more
  interesting result than "nothing works" -- it would mean the absence of a
  premium is efficiency, not noise.

This is the H-Regime-1 move applied to options. That diagnostic settled a
question thirteen P&L tests could not, precisely because the information
coefficient isolates signal from portfolio construction, breadth and cost. The
same reasoning applies here: a straddle's P&L confounds the surface's accuracy
with friction, strike selection, and a directional lottery with enormous
variance. Rank correlation confounds none of them.

Method
------

At ``t`` hours before each settlement, take the ATM implied vol reconstructed
from the exchange's mark price, and the realised vol of the spot index over
``[t, expiry]``. Correlate them across expiries.

**Spearman, not Pearson.** Both series are heavily right-skewed and realised
vol has the fat upper tail that produced the -11.42R straddle outcome; a
Pearson correlation on that would be a statement about two or three days.

**Two coefficients are reported, and the second is the real one:**

* ``IC(IV, RV)`` -- does implied rank with realised? A high value here is
  nearly guaranteed and nearly meaningless, because both track the slow-moving
  volatility level. Reported as a sanity check that the reconstruction works.
* ``IC(IV, RV - forecast)`` -- does implied beat a naive backward-looking
  forecast? This is the question. The benchmark is trailing realised vol over
  a window matched to the forecast horizon, which is the cheapest possible
  volatility forecast and the one implied vol must beat to be adding anything.

Run: python -m deltabt.research.iv_ic
"""

from __future__ import annotations

import argparse
import json
import logging

import numpy as np
import pandas as pd

from deltabt.data.options import OptionCatalog
from deltabt.data.store import CandleStore
from deltabt.options_pricing import implied_vol, year_fraction
from deltabt.research.stats import block_bootstrap_mean
from deltabt.research.vrp_feasibility import RESOLUTION, SPOT_INDEX, _read_at

log = logging.getLogger(__name__)

OBSERVE_HOURS_BEFORE = 24.0
UNDERLYINGS = ("BTC", "ETH")

#: Annualisation for 5m log returns. Calendar time throughout -- crypto has no
#: market calendar, so trading time and calendar time coincide.
BARS_PER_YEAR_5M = 365.0 * 24.0 * 12.0


def realised_vol(spot: pd.DataFrame, start_ts: int, end_ts: int) -> float:
    """Annualised close-to-close realised vol over ``[start, end]``.

    Zero-return bars are kept. Dropping them would inflate the estimate by
    removing exactly the quiet minutes that make volatility low.
    """
    m = (spot["time"] >= start_ts) & (spot["time"] <= end_ts)
    px = spot.loc[m, "close"].to_numpy(dtype=float)
    if px.size < 12 or np.any(px <= 0):
        return np.nan
    r = np.diff(np.log(px))
    if r.size < 8:
        return np.nan
    return float(np.std(r, ddof=1) * np.sqrt(BARS_PER_YEAR_5M))


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 10:
        return np.nan
    rx = pd.Series(x[ok]).rank().to_numpy()
    ry = pd.Series(y[ok]).rank().to_numpy()
    return float(np.corrcoef(rx, ry)[0, 1])


def build_sample(
    underlying: str = "BTC",
    *,
    observe_hours: float = OBSERVE_HOURS_BEFORE,
    store: CandleStore | None = None,
    catalog: OptionCatalog | None = None,
) -> pd.DataFrame:
    """One row per expiry: ATM IV at t, realised vol to expiry, trailing vol."""
    catalog = catalog or OptionCatalog()
    store = store or CandleStore()
    df = catalog.read()
    if df.empty:
        raise RuntimeError("option catalog is empty")

    spot_symbol = SPOT_INDEX[underlying]
    settled = df[(df["underlying"] == underlying) & df["settlement_price"].notna()]
    expiries = sorted(settled["expiry_ts"].unique().tolist())
    offset = int(observe_hours * 3600)
    horizon = offset  # forecast horizon == the option's remaining life
    rows: list[dict] = []

    for expiry in expiries:
        obs_ts = expiry - offset
        chain = settled[settled["expiry_ts"] == expiry]
        listed = chain[(chain["launch_ts"] > 0) & (chain["launch_ts"] <= obs_ts)]
        if listed.empty:
            continue

        # Spot window covers the trailing benchmark AND the forward horizon.
        spot = store.load(
            spot_symbol, "ltp", RESOLUTION, obs_ts - horizon - 7200, expiry + 60
        )
        s_t = _read_at(spot, obs_ts)
        if not np.isfinite(s_t) or s_t <= 0:
            continue

        strikes = listed.groupby("strike")["is_call"].nunique()
        both = strikes[strikes == 2].index.to_numpy(dtype=float)
        if both.size == 0:
            continue
        atm = float(both[np.argmin(np.abs(both - s_t))])

        tte = float(year_fraction(obs_ts, expiry))
        ivs = []
        for is_call in (True, False):
            m = (listed["strike"] == atm) & (listed["is_call"] == is_call)
            if not m.any():
                continue
            sym = listed[m].iloc[0]["symbol"]
            mark = _read_at(
                store.load(sym, "mark", RESOLUTION, obs_ts - 7200, obs_ts + 60), obs_ts
            )
            if not np.isfinite(mark) or mark <= 0:
                continue
            # r = 0: calibrated against Delta's own mark_iv in validate_iv.py.
            v = float(implied_vol(mark, s_t, atm, tte, is_call))
            if np.isfinite(v):
                ivs.append(v)
        if not ivs:
            continue

        rv_fwd = realised_vol(spot, obs_ts, expiry)
        rv_trail = realised_vol(spot, obs_ts - horizon, obs_ts)
        if not np.isfinite(rv_fwd):
            continue

        rows.append(
            dict(
                underlying=underlying,
                expiry=pd.Timestamp(expiry, unit="s", tz="UTC"),
                obs_ts=int(obs_ts),
                strike=atm,
                spot=s_t,
                atm_iv=float(np.mean(ivs)),
                rv_forward=rv_fwd,
                rv_trailing=rv_trail,
                vrp_vol_points=float(np.mean(ivs)) - rv_fwd,
            )
        )

    return pd.DataFrame(rows)


def summarise(sample: pd.DataFrame) -> dict:
    """Rank correlations, and whether IV beats the trailing-vol benchmark."""
    if len(sample) < 30:
        return {"n": int(len(sample)), "verdict": "underpowered"}

    iv = sample["atm_iv"].to_numpy(dtype=float)
    rv = sample["rv_forward"].to_numpy(dtype=float)
    tr = sample["rv_trailing"].to_numpy(dtype=float)

    ok = np.isfinite(iv) & np.isfinite(rv) & np.isfinite(tr)
    iv, rv, tr = iv[ok], rv[ok], tr[ok]

    # Residualise both predictors on nothing -- the comparison of interest is
    # which one ranks future vol better, and whether IV adds to trailing.
    ic_iv = _spearman(iv, rv)
    ic_trail = _spearman(tr, rv)
    # Incremental: does IV rank future vol once trailing vol is accounted for?
    resid_rv = rv - np.polyval(np.polyfit(tr, rv, 1), tr)
    resid_iv = iv - np.polyval(np.polyfit(tr, iv, 1), tr)
    ic_incremental = _spearman(resid_iv, resid_rv)

    n = len(iv)
    # Rough SE for a rank correlation; the bootstrap below is the real check.
    rng = np.random.default_rng(5)
    boots = np.empty(3000)
    for i in range(3000):
        idx = rng.integers(0, n, n)
        boots[i] = _spearman(resid_iv[idx], resid_rv[idx])
    se_inc = float(np.nanstd(boots))

    spread = block_bootstrap_mean(iv - rv, mean_block=5.0, seed=9)

    return {
        "n": int(n),
        "ic_iv_vs_future_rv": ic_iv,
        "ic_trailing_vs_future_rv": ic_trail,
        "ic_iv_incremental_over_trailing": ic_incremental,
        "ic_incremental_se": se_inc,
        "ic_incremental_t": ic_incremental / se_inc if se_inc > 0 else float("nan"),
        "mean_iv": float(np.mean(iv)),
        "mean_rv": float(np.mean(rv)),
        "iv_minus_rv_vol_points": {
            "mean": spread["mean"], "ci_low": spread["ci_low"],
            "ci_high": spread["ci_high"], "t": spread["t"],
        },
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    results = {}
    frames = []
    for u in UNDERLYINGS:
        s = build_sample(u)
        if s.empty:
            log.warning("%s: empty sample", u)
            continue
        frames.append(s)
        r = summarise(s)
        results[u] = r
        print(f"\n=== {u} === n={r['n']}  {s['expiry'].min().date()} -> {s['expiry'].max().date()}")
        print(f"  mean ATM IV            : {r['mean_iv']:.4f}")
        print(f"  mean realised vol      : {r['mean_rv']:.4f}")
        v = r["iv_minus_rv_vol_points"]
        print(f"  IV - RV (vol points)   : {v['mean']:+.4f}  CI [{v['ci_low']:+.4f},{v['ci_high']:+.4f}]  t={v['t']:+.2f}")
        print(f"  IC(IV, future RV)      : {r['ic_iv_vs_future_rv']:+.4f}   <- sanity check, not the question")
        print(f"  IC(trailing, future RV): {r['ic_trailing_vs_future_rv']:+.4f}   <- the benchmark to beat")
        print(f"  IC(IV | trailing)      : {r['ic_iv_incremental_over_trailing']:+.4f}  "
              f"se {r['ic_incremental_se']:.4f}  t={r['ic_incremental_t']:+.2f}   <- THE ANSWER")

    if args.out and frames:
        pd.concat(frames, ignore_index=True).to_csv(args.out, index=False)
        print(f"\nwrote {args.out}")
    print("\n" + json.dumps(results, indent=1, default=float))


if __name__ == "__main__":
    main()
