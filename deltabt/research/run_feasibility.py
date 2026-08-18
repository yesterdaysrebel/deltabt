"""MARKET/EXECUTION FEASIBILITY -- economics of paths A, B and C.

    PYTHONPATH=. python3 -m deltabt.research.run_feasibility

NOT strategy research. No signal, no direction, no indicator, no parameter
search, no VALID. This measures the trading ENVIRONMENT: what a round trip
costs, how far price moves, how much funding accrues, how many independent
observations a horizon yields, and what a passive order actually experiences.

The question is whether any environment change makes another research cycle
worth funding -- decided BEFORE any signal is tested.

TRAIN window only. TEST is never touched.
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research.hstructure2 import STUDY, SYMBOLS, TRAIN

TAKER_BPS = 10_000 * 0.0005 * 1.18          # 5.90
MAKER_BPS = 10_000 * 0.0002 * 1.18          # 2.36
SLIP_BPS = 2.0
RT_TAKER = 2 * (TAKER_BPS + SLIP_BPS)       # 15.80
RT_MAKER = 2 * MAKER_BPS                    # 4.72
RT_MIXED = MAKER_BPS + TAKER_BPS + SLIP_BPS  # maker in, taker out

#: Path A horizons, in minutes.
HORIZONS = [(60, "+1h"), (240, "+4h"), (720, "+12h"), (1440, "+1d"), (4320, "+3d")]

TRAIN_DAYS = (TRAIN[1] - TRAIN[0]) / 86400.0
Z = 2.8                                      # the frozen MDE constant


def load(sym: str) -> pd.DataFrame:
    d = CandleStore().read(sym, "ltp", "1m")
    return d[(d.time >= STUDY) & (d.time < TRAIN[1])].reset_index(drop=True)


def fwd(t: np.ndarray, c: np.ndarray, h_min: int) -> np.ndarray:
    j = np.searchsorted(t, t + h_min * 60, side="left")
    ok = j < t.size
    j = np.where(ok, np.minimum(j, t.size - 1), 0)
    ok &= np.abs(t[j] - (t + h_min * 60)) <= 60
    return np.where(ok, c[j] / c - 1.0, np.nan)


def path_a(data: dict, funding: dict) -> list[dict]:
    """Horizon economics: move size, funding drag, observations, detectability."""
    rows = []
    for h_min, label in HORIZONS:
        moves, sds = [], []
        for sym in SYMBOLS:
            r = fwd(data[sym]["t"], data[sym]["c"], h_min)
            r = r[np.isfinite(r)]
            moves.append(1e4 * np.median(np.abs(r)))
            sds.append(1e4 * r.std())
        med = float(np.mean(moves))
        sd = float(np.mean(sds))

        # funding: settlements crossed by a hold of this length, x mean rate
        n_settle = h_min * 60 / 28800.0
        mean_rate = float(np.mean([funding[s]["mean_bps"] for s in SYMBOLS]))
        abs_rate = float(np.mean([funding[s]["mean_abs_bps"] for s in SYMBOLS]))
        f_biased = n_settle * mean_rate       # persistently long
        f_balanced = 0.0                      # 50/50 long/short: a transfer, not a fee

        cost_biased = RT_TAKER + f_biased
        # independent (non-overlapping) observations available per year
        n_obs = int(TRAIN_DAYS * 24 * 60 / h_min) * len(SYMBOLS)
        mde = Z * sd / np.sqrt(n_obs)

        rows.append(dict(
            horizon=label, horizon_min=h_min,
            median_move_bps=med, sd_move_bps=sd,
            funding_settlements=n_settle,
            funding_biased_bps=f_biased, funding_balanced_bps=f_balanced,
            cost_balanced_bps=RT_TAKER, cost_biased_bps=cost_biased,
            cost_pct_of_move=100 * RT_TAKER / med,
            cost_biased_pct_of_move=100 * cost_biased / med,
            n_independent_obs=n_obs, mde_bps=float(mde),
            mde_over_cost=float(mde / RT_TAKER),
            detectable=bool(mde < RT_TAKER)))
    return rows


def path_b(data: dict) -> dict:
    """What a resting passive order actually experiences, from 1m OHLC.

    THE UPPER BOUND, NOT THE ESTIMATE. 'the low touched my limit' is not 'my
    order filled': with no queue depth and no trade prints there is no way to
    know whether size traded at that level ahead of us. Every number here is
    therefore the most favourable case, and the real one is worse.
    """
    out = {}
    for sym in SYMBOLS:
        t, o, h, l, c = (data[sym][k] for k in ("t", "o", "h", "l", "c"))
        prev_c = c[:-1]
        lo, hi, cl = l[1:], h[1:], c[1:]

        touched_bid = lo <= prev_c              # a buy limit at the prior close
        touched_ask = hi >= prev_c

        # adverse selection: where the passive buy filled, what happened next?
        nxt = np.full(cl.size, np.nan)
        nxt[:-1] = cl[1:] / prev_c[:-1] - 1.0

        fb = touched_bid & np.isfinite(nxt)
        fa = touched_ask & np.isfinite(nxt)
        out[sym] = dict(
            touch_rate_bid=float(touched_bid.mean()),
            touch_rate_ask=float(touched_ask.mean()),
            fill_conditional_ret_bid_bps=float(1e4 * np.nanmean(nxt[fb])),
            fill_conditional_ret_ask_bps=float(1e4 * np.nanmean(nxt[fa])),
            unconditional_ret_bps=float(1e4 * np.nanmean(nxt)))
    agg = {k: float(np.mean([out[s][k] for s in SYMBOLS])) for k in out[SYMBOLS[0]]}
    # a passive BUY that fills is followed by a fall; a passive SELL by a rise.
    adverse = agg["unconditional_ret_bps"] - agg["fill_conditional_ret_bid_bps"]
    agg["adverse_selection_bps_per_leg"] = float(adverse)
    agg["fee_saving_maker_both_legs_bps"] = RT_TAKER - RT_MAKER
    agg["fee_saving_maker_entry_only_bps"] = RT_TAKER - RT_MIXED
    agg["net_if_adverse_selection_applies_both_legs"] = float(
        (RT_TAKER - RT_MAKER) - 2 * adverse)
    return dict(per_symbol=out, pooled=agg)


def main() -> int:
    data, funding = {}, {}
    cat = ProductCatalog()
    for sym in SYMBOLS:
        df = load(sym)
        data[sym] = dict(t=df["time"].to_numpy("int64"),
                         o=df["open"].to_numpy("float64"),
                         h=df["high"].to_numpy("float64"),
                         l=df["low"].to_numpy("float64"),
                         c=df["close"].to_numpy("float64"))
        f = CandleStore().read(sym, "funding", "1h")
        f = f[(f.time >= STUDY) & (f.time < TRAIN[1])]
        r = f["close"].to_numpy("float64") * 100.0     # percent -> bps
        r = r[np.isfinite(r)]
        funding[sym] = dict(n=int(r.size), mean_bps=float(r.mean()),
                            mean_abs_bps=float(np.abs(r).mean()),
                            interval_s=int(cat.get(sym)["funding_interval_seconds"]))

    print("=" * 96)
    print("FEASIBILITY ECONOMICS -- environment only. No signal is tested.")
    print("=" * 96)
    print(f"  taker {TAKER_BPS:.2f} bps (incl 1.18 GST) | maker {MAKER_BPS:.2f} bps | "
          f"slippage {SLIP_BPS:.1f} bps")
    print(f"  round trip: taker {RT_TAKER:.2f} | maker/maker {RT_MAKER:.2f} | "
          f"maker-in/taker-out {RT_MIXED:.2f}")
    print(f"  TRAIN {TRAIN_DAYS:.0f} days x {len(SYMBOLS)} symbols\n")

    print("  funding, per 8h settlement (bps of notional)")
    for s in SYMBOLS:
        print(f"    {s:8} mean {funding[s]['mean_bps']:+7.3f}   "
              f"mean|.| {funding[s]['mean_abs_bps']:6.3f}   n={funding[s]['n']:,}")

    A = path_a(data, funding)
    print("\n" + "-" * 96)
    print("PATH A -- LONGER HORIZON")
    print("-" * 96)
    print(f"  {'horizon':>8} {'median move':>12} {'cost/move':>10} {'fund(long)':>11} "
          f"{'obs':>7} {'MDE':>9} {'MDE/cost':>9} {'detectable':>11}")
    for r in A:
        print(f"  {r['horizon']:>8} {r['median_move_bps']:>9.1f} bps "
              f"{r['cost_pct_of_move']:>9.1f}% {r['funding_biased_bps']:>+10.2f} "
              f"{r['n_independent_obs']:>7,} {r['mde_bps']:>6.1f} bps "
              f"{r['mde_over_cost']:>8.2f}x {'YES' if r['detectable'] else 'NO':>11}")

    B = path_b(data)
    p = B["pooled"]
    print("\n" + "-" * 96)
    print("PATH B -- MAKER EXECUTION  (upper bound: touch is not fill)")
    print("-" * 96)
    print(f"  passive bid touch rate         {p['touch_rate_bid']:.1%}")
    print(f"  passive ask touch rate         {p['touch_rate_ask']:.1%}")
    print(f"  return after a passive BUY     {p['fill_conditional_ret_bid_bps']:+.3f} bps")
    print(f"  return after a passive SELL    {p['fill_conditional_ret_ask_bps']:+.3f} bps")
    print(f"  unconditional next-bar return  {p['unconditional_ret_bps']:+.3f} bps")
    print(f"  ADVERSE SELECTION per leg      {p['adverse_selection_bps_per_leg']:+.3f} bps")
    print(f"  fee saving, maker both legs    {p['fee_saving_maker_both_legs_bps']:+.2f} bps")
    print(f"  fee saving, maker entry only   {p['fee_saving_maker_entry_only_bps']:+.2f} bps")
    print(f"  NET saving after adverse sel.  "
          f"{p['net_if_adverse_selection_applies_both_legs']:+.2f} bps")

    out = dict(costs=dict(taker_bps=TAKER_BPS, maker_bps=MAKER_BPS,
                          slippage_bps=SLIP_BPS, rt_taker=RT_TAKER,
                          rt_maker=RT_MAKER, rt_mixed=RT_MIXED),
               train_days=TRAIN_DAYS, symbols=list(SYMBOLS),
               funding=funding, path_a=A, path_b=B)
    with open("out/phase_discovery/feasibility_economics.json", "w") as fh:
        json.dump(out, fh, indent=2)
    print("\nwritten -> out/phase_discovery/feasibility_economics.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
