"""FIXED PERCENTAGE STOP AND TARGET, ON TRAIN + VALIDATION. TEST STAYS LOCKED.

    PYTHONPATH=. python3 -u -m deltabt.research.run_fixed_sl

WHY THIS IS THE RIGHT INSTRUMENT FOR THE QUESTION. The ATR sweep in
run_atr1m established that `cost_r x stop_pct` is flat at ~0.20 across a
fourfold range of stop widths -- cost per R is set almost entirely by how wide
the stop is, because cost is a fixed fraction of notional and R is not. A fixed
percentage stop turns that relationship into a dial: choose the stop width and
cost/R follows. Nothing else measures the gross edge this cleanly.

It also removes a selection effect the other stops carry. A structural or ATR
stop is rejected when `r_price / entry > max_stop_pct`, so the surviving trades
are a stop-width-filtered subset of the signals. A fixed stop below that
threshold is never rejected, so the population here is exactly the signal
population -- no filtering, no survivorship.

THE HYPOTHESIS BEING TESTED IS NOT "does a fixed stop win". It is: as cost/R is
driven toward zero by widening the stop, does NET converge on something
positive? If gross edge exists, net must approach it. If gross is zero, net
approaches zero from below and no stop rule anywhere can fix it.

The grid is reported whole. NO CELL SHOULD BE PICKED as a strategy: choosing
the best of thirty is selection on the same data that measured them, which is
what validation columns exist to expose. TEST IS NOT TOUCHED.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.quality import tradable_mask
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research import hwpr
from deltabt.research.run_hwpr import summarise

OUT = OUT_DIR / "fixed_sl"
STUDY = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())

#: Comfortably above the widest stop tested, so nothing is refused for width.
MAX_STOP_PCT = 0.10

#: Extended to 5% AFTER the first pass, because net was still improving
#: monotonically at the 2% edge and stopping there would have reported the
#: boundary of the grid as if it were the shape of the curve. The extra cells
#: are more looks at train+validation and are counted as such.
SL_GRID = (0.0025, 0.005, 0.0075, 0.010, 0.015, 0.020, 0.030, 0.040, 0.050)
TP_GRID = (1.0, 1.5, 2.0, 3.0)


def _fixed_stop(C: dict, pct: float, *, drop_5m: bool) -> dict:
    """Conditions whose stop is a fixed fraction of the signal-bar close.

    The simulator takes `min(st1[i], leg_lo[i])` for longs and
    `max(st1[i], leg_hi[i])` for shorts, so st1 is set neutral at the close and
    the fixed band supplies the stop. Entry is still the NEXT bar's open, so
    r_price carries the open/close gap exactly as a live fill would.
    """
    c = C["close"]
    D = dict(C)
    D["st1"] = c
    D["leg_lo"] = c * (1.0 - pct)
    D["leg_hi"] = c * (1.0 + pct)
    if drop_5m:
        D["f5_long"] = np.ones_like(C["f5_long"], dtype=bool)
        D["f5_short"] = np.ones_like(C["f5_short"], dtype=bool)
    return D


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(OUT_DIR / "hwpr_universe.csv").symbol.tolist()
    store, cat = CandleStore(), ProductCatalog()
    print(f"FROZEN UNIVERSE: {universe}")

    data = {}
    for s in universe:
        ltp = store.read(s, "ltp", "1m")
        ltp = ltp[ltp.time >= STUDY].reset_index(drop=True)
        data[s] = dict(df=ltp, mark=store.read(s, "mark", "1m"),
                       funding=store.read(s, "funding", "1h"),
                       costs=SymbolCosts.from_spec(cat.get(s), slippage_bps=2.0),
                       tradable=tradable_mask(ltp))
    last = min(int(d["df"].time.iloc[-1]) for d in data.values())
    span = last - STUDY
    TR = (STUDY, STUDY + int(span * 0.6))
    VA = (STUDY + int(span * 0.6), STUDY + int(span * 0.8))
    print(f"  train {pd.Timestamp(TR[0],unit='s').date()} -> {pd.Timestamp(TR[1],unit='s').date()}")
    print(f"  valid {pd.Timestamp(VA[0],unit='s').date()} -> {pd.Timestamp(VA[1],unit='s').date()}")
    print(f"  test  {pd.Timestamp(VA[1],unit='s').date()} -> {pd.Timestamp(last,unit='s').date()}  [LOCKED]\n")

    for s, d in data.items():
        d["C"] = hwpr.build_conditions(d["df"])

    # Each window starts with its OWN equity. run_hwpr's start=STUDY convention
    # carries train's bankrupt account into validation, where the simulator --
    # which compounds with no floor and sizes from equity -- then reports a
    # handful of trades that are the residue of a ruin, not a sample. See
    # run_atr1m for the case that surfaced it.
    def measure(arm, pct, target_r, drop_5m, window):
        frames = []
        for s, d in data.items():
            C = _fixed_stop(d["C"], pct, drop_5m=drop_5m)
            r = hwpr.run(d["df"], d["mark"], d["funding"], d["costs"], C,
                         arm=arm, wpr_variant="A", target_r=target_r,
                         start=window[0], end=window[1],
                         tradable=d["tradable"], max_stop_pct=MAX_STOP_PCT)
            f = r.to_frame()
            if len(f):
                f = f[(f.entry_time >= window[0]) & (f.entry_time < window[1])]
            if len(f):
                frames.append(f)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    rows = []

    def row(label, arm, pct, target_r, drop_5m):
        got = {}
        for nm, win in (("train", TR), ("valid", VA)):
            m = summarise(measure(arm, pct, target_r, drop_5m, win), nm)
            got[nm] = m
            rows.append(dict(config=label, arm=arm, sl_pct=pct, target_r=target_r,
                             drop_5m=drop_5m, **m))
        t, v = got["train"], got["valid"]
        if not t["trades"] or not v["trades"]:
            print(f"  {label:30} INSUFFICIENT")
            return got
        print(f"  {label:30} "
              f"train n={t['trades']:>6,} gross={t['gross_r']:+.4f} "
              f"cost={t['cost_r']:.3f} NET={t['net_r']:+.4f}  |  "
              f"valid n={v['trades']:>6,} gross={v['gross_r']:+.4f} "
              f"cost={v['cost_r']:.3f} NET={v['net_r']:+.4f} "
              f"[{v['ci_low']:+.3f},{v['ci_high']:+.3f}]")
        return got

    print("=" * 132)
    print("FIXED STOP SWEEP at 2R  —  frozen Arm A (5m regime + 1m stack)")
    print("=" * 132)
    for pct in SL_GRID:
        row(f"ArmA  SL={pct:.2%}  TP=2R", "A", pct, 2.0, False)

    print("\n" + "=" * 132)
    print("FIXED STOP SWEEP at 2R  —  1m decision only (5m regime filter removed)")
    print("=" * 132)
    for pct in SL_GRID:
        row(f"1m    SL={pct:.2%}  TP=2R", "A", pct, 2.0, True)

    print("\n" + "=" * 132)
    print("TARGET SWEEP at SL=1.00%  —  does the target multiple matter at all?")
    print("=" * 132)
    for tr_ in TP_GRID:
        row(f"1m    SL=1.00%  TP={tr_}R", "A", 0.010, tr_, True)

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "grid.csv", index=False)
    json.dump(rows, open(OUT / "grid.json", "w"), indent=2, default=str)
    print(f"\nwrote {OUT/'grid.csv'}")

    # The relationship the whole exercise turns on, stated as a number.
    g = df[(df.label == "train") & df.cost_r.notna()]
    if len(g):
        prod = (g.cost_r * g.stop_pct_median / 100).round(4)
        print(f"\ncost_r x stop_pct across the grid: mean {prod.mean():.4f}  "
              f"min {prod.min():.4f}  max {prod.max():.4f}")
    print("\nTEST WAS NOT TOUCHED. No cell above is a strategy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
