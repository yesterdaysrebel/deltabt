"""Does inverting Delta's mark price reproduce Delta's own published IV?

Delta serves no IV history, so any volatility study on this venue has to
reconstruct IV from the `MARK:` candle series. That reconstruction is a
modelling assumption, and this script is the only thing standing between it
and a research programme built on a guess.

The test is direct: `/v2/tickers` publishes, for every live option, both the
mark price and the exchange's own `mark_iv`. Invert the former and compare to
the latter. If the residual is small across moneyness and tenor, the same
inversion applied to historical mark candles is sound. If it is not, the
volatility experiments cannot proceed on reconstructed data and that must be
recorded rather than worked around.

The discount rate is *calibrated*, not assumed: Delta prices off a forward and
does not publish the rate it uses, so the script sweeps `r` and reports the
value minimising median absolute IV error, together with how much that choice
actually matters.

Run: python -m deltabt.research.validate_iv
"""

from __future__ import annotations

import datetime as dt
import json

import numpy as np

from deltabt.data.client import DeltaClient
from deltabt.options_pricing import implied_vol, year_fraction

#: Contracts below this mark price carry no information -- a 0.5 tick on a
#: 0.1-priced option is a 500 bps IV move, so a large residual there says
#: nothing about the model.
MIN_MARK_PRICE = 1.0

#: Candidate continuous annualised rates for the forward, as fractions.
RATE_GRID = np.concatenate([np.arange(0.0, 0.31, 0.005)])


def _f(x, default=np.nan) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def collect(client: DeltaClient | None = None) -> list[dict]:
    """Live option tickers with everything the inversion needs."""
    client = client or DeltaClient()
    rows: list[dict] = []
    for ct in ("call_options", "put_options"):
        for t in client.tickers(contract_types=ct):
            greeks = t.get("greeks") or {}
            quotes = t.get("quotes") or {}
            rows.append(
                {
                    "symbol": t["symbol"],
                    "is_call": ct == "call_options",
                    "underlying": t.get("underlying_asset_symbol"),
                    "strike": _f(t.get("strike_price")),
                    "mark_price": _f(t.get("mark_price")),
                    "spot": _f(greeks.get("spot"), _f(t.get("spot_price"))),
                    "mark_iv": _f(quotes.get("mark_iv")),
                    "delta": _f(greeks.get("delta")),
                    "turnover_usd": _f(t.get("turnover_usd"), 0.0),
                    "best_bid": _f(quotes.get("best_bid")),
                    "best_ask": _f(quotes.get("best_ask")),
                }
            )
    return rows


def expiry_seconds(symbol: str) -> float:
    """Settlement instant from the symbol's DDMMYY suffix, at 12:00 UTC."""
    suffix = symbol.rsplit("-", 1)[-1]
    day, month, year = int(suffix[0:2]), int(suffix[2:4]), 2000 + int(suffix[4:6])
    return dt.datetime(year, month, day, 12, 0, tzinfo=dt.timezone.utc).timestamp()


def main() -> None:
    client = DeltaClient()
    rows = collect(client)
    now = dt.datetime.now(dt.timezone.utc).timestamp()

    sym = np.array([r["symbol"] for r in rows])
    is_call = np.array([r["is_call"] for r in rows])
    strike = np.array([r["strike"] for r in rows])
    mark = np.array([r["mark_price"] for r in rows])
    spot = np.array([r["spot"] for r in rows])
    mark_iv = np.array([r["mark_iv"] for r in rows])
    turnover = np.array([r["turnover_usd"] for r in rows])
    exp = np.array([expiry_seconds(s) for s in sym])
    tte = year_fraction(now, exp)

    usable = (
        np.isfinite(mark) & (mark >= MIN_MARK_PRICE)
        & np.isfinite(spot) & (spot > 0)
        & np.isfinite(mark_iv) & (mark_iv > 0)
        & (tte > 0)
    )
    print(f"live option tickers        : {len(rows)}")
    print(f"usable for validation      : {usable.sum()}  (mark >= ${MIN_MARK_PRICE})")
    if usable.sum() == 0:
        print("nothing to validate")
        return

    # -- calibrate the forward rate ----------------------------------------
    best = None
    for rate in RATE_GRID:
        fwd = spot * np.exp(rate * tte)
        iv = implied_vol(mark, fwd, strike, tte, is_call)
        ok = usable & np.isfinite(iv)
        if ok.sum() < 50:
            continue
        err = np.abs(iv[ok] - mark_iv[ok])
        med = float(np.median(err))
        if best is None or med < best[1]:
            best = (float(rate), med, ok.sum())
    if best is None:
        print("calibration failed: too few invertible contracts")
        return
    rate, med_err, n_ok = best
    print(f"calibrated forward rate    : r = {rate:.3f} continuous annualised")
    print(f"invertible at that rate    : {n_ok}")

    # How much does the rate choice matter? If the curve is flat, the whole
    # question is second-order and the assumption is cheap.
    for probe in (0.0, 0.05, 0.10, 0.15):
        fwd = spot * np.exp(probe * tte)
        iv = implied_vol(mark, fwd, strike, tte, is_call)
        ok = usable & np.isfinite(iv)
        print(f"   r={probe:.2f} -> median |IV err| = {np.median(np.abs(iv[ok]-mark_iv[ok])):.5f} vol pts (n={ok.sum()})")

    # -- residuals at the calibrated rate ----------------------------------
    fwd = spot * np.exp(rate * tte)
    iv = implied_vol(mark, fwd, strike, tte, is_call)
    ok = usable & np.isfinite(iv)
    err = iv[ok] - mark_iv[ok]
    aerr = np.abs(err)
    print()
    print("--- IV reconstruction error (reconstructed - exchange mark_iv) ---")
    for q in (0.5, 0.75, 0.9, 0.95, 0.99):
        print(f"   |err| p{int(q*100):<3d} : {np.quantile(aerr, q):.5f} vol points")
    print(f"   max        : {aerr.max():.5f}")
    print(f"   mean signed: {err.mean():+.5f}  (bias)")
    rel = aerr / mark_iv[ok]
    print(f"   median relative error: {np.median(rel)*100:.3f}% of IV")

    # -- does it hold where it matters? ------------------------------------
    print()
    print("--- by tenor ---")
    days = tte[ok] * 365.0
    for lo, hi, label in ((0, 1, "<1d"), (1, 3, "1-3d"), (3, 8, "3-8d"), (8, 32, "8-32d"), (32, 1e9, ">32d")):
        m = (days >= lo) & (days < hi)
        if m.sum():
            print(f"   {label:<6s} n={m.sum():<5d} median |err| = {np.median(aerr[m]):.5f}")
    print()
    print("--- by moneyness (|log(K/F)|) ---")
    mny = np.abs(np.log(strike[ok] / fwd[ok]))
    for lo, hi, label in ((0, 0.02, "ATM"), (0.02, 0.05, "near"), (0.05, 0.15, "OTM"), (0.15, 1e9, "far OTM")):
        m = (mny >= lo) & (mny < hi)
        if m.sum():
            print(f"   {label:<8s} n={m.sum():<5d} median |err| = {np.median(aerr[m]):.5f}")
    print()
    print("--- liquid subset (24h turnover >= $100k) ---")
    liq = turnover[ok] >= 100_000
    if liq.sum():
        print(f"   n={liq.sum()}  median |err| = {np.median(aerr[liq]):.5f}  p95 = {np.quantile(aerr[liq],0.95):.5f}")

    # -- non-invertible contracts ------------------------------------------
    bad = usable & ~np.isfinite(iv)
    print()
    print(f"non-invertible despite usable mark: {bad.sum()} "
          f"({bad.sum()/max(usable.sum(),1)*100:.1f}%) -- mark outside arbitrage bounds")
    if bad.sum():
        for s in sym[bad][:5]:
            print(f"   {s}")

    print()
    print(json.dumps({"calibrated_rate": rate, "median_abs_iv_error": float(np.median(aerr)),
                      "n": int(ok.sum())}))


if __name__ == "__main__":
    main()
