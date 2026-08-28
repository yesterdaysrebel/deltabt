"""Harvest the cross-symbol funding spread, hedged, on cached data.

WHAT THIS IS, AND WHAT IT IS NOT
    A BACKTEST. Nothing here is pre-registered, nothing is written to
    out/experiments.jsonl, and no market data is fetched -- it reads the
    funding and candle parquet already in data/candles/.

WHY IT IS NOT A StrategySpec
    StrategySpec plus rulecore plus run_backtest is a per-symbol signal with a
    stop and a target. This has no stop, no target and no per-symbol signal: it
    is a dollar-neutral portfolio whose return is a CASH FLOW, not a price
    move. There is no R to denominate anything in, which is exactly why it is
    worth measuring -- every other test in this repository died against a
    round-trip cost of ~0.10R, and a carry trade amortises that cost over the
    holding period instead of paying it per signal.

    scripts/xsec_momentum.py is the precedent for a portfolio-level test that
    does not fit the single-symbol harness.

THE MECHANISM, AND THE ONE THING ALREADY KNOWN ABOUT IT
    Positive funding means longs pay shorts. So to RECEIVE funding you are
    short the high-funding symbols and long the low-funding ones. H-Funding-1
    established the half of this that matters:

        "the funding CASH FLOW is real and collectable -- positive on all four
        symbols (+2.5 to +7.7 bps) with confirmed persistence. What is absent
        is any predictive relationship between funding extremes and forward
        PRICE returns. Carry is real; the crowding signal is not."

    That experiment traded funding DIRECTIONALLY and the price move swamped
    the carry. This hedges the price away and keeps only the carry.

WHAT WILL PROBABLY KILL IT, STATED BEFORE THE RUN
    1. BEATUSD's median funding is +15.7 bps per 4h, ~344% annualised, against
       under 0.4 bps for every major. It will be the short leg on nearly every
       rebalance, so "the funding spread" is at risk of being "short BEATUSD"
       wearing a portfolio costume. The per-symbol leg census below exists to
       show that rather than hide it.
    2. Dollar-neutral is not risk-neutral. Correlation among the majors is
       0.77-0.80, which leaves ~20% residual variance, and the residual is
       large next to a spread measured in single-digit bps per interval.
    3. Only seven symbols have funding history and three of them have 21 days.

LIQUIDITY IS MEASURED IN NOTIONAL, WHICH NEEDS contract_value
    `close * volume` is CONTRACTS, not dollars. BTCUSD's contract_value is
    0.001, so omitting it understates BTC turnover by 1000x while leaving a
    contract_value=1 micro-cap untouched -- exactly inverting the screen it is
    supposed to be. Caught after a first run reported a Sharpe near 4 built on
    names with 250-400%/yr funding.
"""

from __future__ import annotations

import argparse
import glob
import json
import os

import numpy as np
import pandas as pd

from deltabt.config import CACHE_DIR, OUT_DIR

#: Taker + slippage, one leg, one way. Matches deltabt.costs: 0.05% taker x
#: 1.18 GST + 2bps. A pair trade pays this four times per round trip.
LEG_COST = 0.00059 + 0.00020

MIN_FUNDING_OBS = 400


def load() -> tuple[dict, dict, dict]:
    """Funding rate (percent per interval), daily close, daily quote volume."""
    funding, price, volume, interval = {}, {}, {}, {}
    products = json.loads((CACHE_DIR.parent / "meta" / "products.json").read_text())
    for path in sorted(glob.glob(str(CACHE_DIR / "*" / "funding_1h.parquet"))):
        sym = os.path.basename(os.path.dirname(path))
        f = pd.read_parquet(path)
        if len(f) < MIN_FUNDING_OBS:
            continue
        # Prefer the DAILY series. The book carries daily and rebalances
        # weekly, so a 1m file is ~860k rows per symbol that nothing reads --
        # and requiring 1m silently limited this to the seven symbols that
        # happened to have it, which is the constraint the whole result was
        # about.
        daily = CACHE_DIR / sym / "ltp_1d.parquet"
        if daily.exists():
            c = pd.read_parquet(daily)
            c["t"] = pd.to_datetime(c["time"], unit="s", utc=True)
            c = c.set_index("t").sort_index()
            cv = products.get(sym, {}).get("contract_value", 1.0)
            close, vol_usd = c["close"], c["close"] * c["volume"] * cv
        else:
            candles = CACHE_DIR / sym / "mark_1m.parquet"
            if not candles.exists():
                candles = CACHE_DIR / sym / "ltp_1m.parquet"
            if not candles.exists():
                continue
            c = pd.read_parquet(candles)
            c["t"] = pd.to_datetime(c["time"], unit="s", utc=True)
            c = c.set_index("t")
            cv = products.get(sym, {}).get("contract_value", 1.0)
            close = c["close"].resample("1D").last()
            vol_usd = (c["close"] * c["volume"] * cv).resample("1D").sum()
        f["t"] = pd.to_datetime(f["time"], unit="s", utc=True)
        funding[sym] = f.set_index("t")["close"] / 100.0     # percent -> fraction
        price[sym] = close.resample("1D").last()
        volume[sym] = vol_usd.resample("1D").sum()
        interval[sym] = products.get(sym, {}).get("funding_interval_seconds", 28800)
    return funding, price, volume, interval


def realised_funding(rate: pd.Series, interval_s: int) -> pd.Series:
    """Funding actually PAID per day.

    The parquet samples the prevailing rate hourly; settlement happens every
    ``interval_s``. Summing the hourly series would over-count by 4-8x, which
    would make any result meaningless in the flattering direction.
    """
    step = f"{interval_s // 3600}h"
    settled = rate.resample(step).last().dropna()
    return settled.resample("1D").sum()


def run(lookback_days: int, hold_days: int, legs: int,
        min_volume: float, exclude: set[str], weight: str = "dollar") -> dict:
    funding, price, volume, interval = load()
    for sym in exclude:
        funding.pop(sym, None)

    daily_f = pd.DataFrame({s: realised_funding(r, interval[s])
                            for s, r in funding.items()}).sort_index()
    px = pd.DataFrame({s: price[s] for s in funding}).reindex(daily_f.index)
    vol = pd.DataFrame({s: volume[s] for s in funding}).reindex(daily_f.index)
    ret = px.pct_change()

    # Trailing MEAN funding, shifted one day: the rank on day t uses only data
    # available before day t. Without the shift the portfolio is chosen with
    # the funding it is about to collect.
    signal = daily_f.rolling(lookback_days).mean().shift(1)
    liquid = vol.rolling(7).mean().shift(1) >= min_volume
    # EQUAL DOLLARS IS NOT EQUAL RISK. SOLUSD's daily vol runs well above
    # BTCUSD's, so a dollar-neutral pair is short whichever leg moves more --
    # a directional bet wearing a hedge's clothing. Inverse-vol weights size
    # each leg to contribute the same variance, which is the standard fix and
    # the one the price-P&L column below is measuring.
    sigma = ret.rolling(30).std().shift(1)

    rows, census = [], []
    held: dict[str, float] = {}
    for i, day in enumerate(daily_f.index):
        if i % hold_days == 0:
            s = signal.loc[day].where(liquid.loc[day]).dropna()
            if len(s) >= 2 * legs:
                n = min(legs, len(s) // 2)
                shorts = s.nlargest(n).index      # pay funding -> we short
                longs = s.nsmallest(n).index      # receive -> we long
                if weight == "invvol":
                    sg = sigma.loc[day]
                    iv = {k: (1.0 / sg[k] if np.isfinite(sg.get(k, np.nan))
                              and sg.get(k, 0) > 0 else np.nan)
                          for k in list(shorts) + list(longs)}
                    if any(not np.isfinite(v) for v in iv.values()):
                        new = {}
                    else:
                        ls = sum(iv[k] for k in longs)
                        ss = sum(iv[k] for k in shorts)
                        new = {**{k: -0.5 * iv[k] / ss for k in shorts},
                               **{k: +0.5 * iv[k] / ls for k in longs}}
                else:
                    new = {**{k: -0.5 / n for k in shorts},
                           **{k: +0.5 / n for k in longs}}
            else:
                new = {}
            turnover = sum(abs(new.get(k, 0) - held.get(k, 0))
                           for k in set(new) | set(held))
            cost = turnover * LEG_COST
            held = new
            census.extend((day, k, "LONG" if w > 0 else "SHORT")
                          for k, w in held.items())
        else:
            cost = 0.0

        # Funding sign: positive rate = longs PAY. A long weight collects
        # -rate, a short weight collects +rate.
        carry = sum(-w * daily_f.loc[day].get(k, 0.0) for k, w in held.items())
        pnl = sum(w * ret.loc[day].get(k, 0.0) for k, w in held.items())
        if not np.isfinite(carry):
            carry = 0.0
        if not np.isfinite(pnl):
            pnl = 0.0
        rows.append(dict(day=day, carry=carry, price_pnl=pnl, cost=cost,
                         total=carry + pnl - cost, n_legs=len(held)))

    d = pd.DataFrame(rows).set_index("day")
    d = d[d["n_legs"] > 0]
    return dict(daily=d, census=pd.DataFrame(census, columns=["day", "symbol", "side"]))


def report(res: dict, label: str) -> None:
    d = res["daily"]
    if not len(d):
        print(f"{label}: no days with a position")
        return
    yrs = len(d) / 365.25
    tot = d["total"].sum()
    ann = tot / yrs
    vol = d["total"].std() * np.sqrt(365.25)
    sharpe = ann / vol if vol else float("nan")
    eq = d["total"].cumsum()
    dd = (eq - eq.cummax()).min()
    print(f"\n=== {label} ===")
    print(f"  days {len(d):5d} ({yrs:.2f}y)   rebalances {int(d['cost'].gt(0).sum())}")
    print(f"  carry      {100*d['carry'].sum():+8.2f}%   ({100*d['carry'].sum()/yrs:+7.2f}%/yr)")
    print(f"  price P&L  {100*d['price_pnl'].sum():+8.2f}%   <- should be ~0 if the hedge works")
    print(f"  fees       {100*d['cost'].sum():+8.2f}%")
    print(f"  TOTAL      {100*tot:+8.2f}%   ({100*ann:+7.2f}%/yr)")
    print(f"  vol {100*vol:.1f}%/yr   Sharpe {sharpe:+.2f}   max drawdown {100*dd:+.2f}%")
    c = res["census"]
    if len(c):
        print("  leg census (how often each symbol was picked):")
        for (sym, side), n in c.groupby(["symbol", "side"]).size().sort_values(
                ascending=False).items():
            print(f"     {sym:9s} {side:5s} {n:4d} rebalances "
                  f"({100*n/c['day'].nunique():5.1f}% of them)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lookback", type=int, default=7)
    ap.add_argument("--hold", type=int, default=7)
    ap.add_argument("--legs", type=int, default=1)
    ap.add_argument("--min-volume", type=float, default=0.0)
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--weight", choices=("dollar", "invvol"), default="dollar")
    args = ap.parse_args()

    res = run(args.lookback, args.hold, args.legs,
              args.min_volume, set(args.exclude), args.weight)
    label = (f"lookback {args.lookback}d, hold {args.hold}d, {args.legs} leg(s)/side"
             + f", {args.weight}-weighted"
             + (f", volume >= ${args.min_volume:,.0f}/day" if args.min_volume else "")
             + (f", excluding {sorted(args.exclude)}" if args.exclude else ""))
    report(res, label)
    out = OUT_DIR / "sweep" / "funding_spread.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    res["daily"].to_csv(out)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
