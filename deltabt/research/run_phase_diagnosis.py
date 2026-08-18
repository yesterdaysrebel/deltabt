"""Venue and horizon characterisation for the phase's strategic diagnosis.

    PYTHONPATH=. python3 -m deltabt.research.run_phase_diagnosis

NOT a hypothesis test. Protocol section 20 requires a strategic diagnosis after
three failed families, covering data resolution, instruments, execution costs,
trading horizon and signal-to-noise. This measures the unconditional properties
of the data needed to write it: how far price actually moves over each horizon,
against what the round trip costs.

No signal, no direction, no event. TRAIN window only, TEST untouched.
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from deltabt.data.store import CandleStore
from deltabt.research.hstructure2 import HORIZONS_MIN, STUDY, SYMBOLS, TRAIN

ROUND_TRIP_BPS = 10_000 * 2 * (0.0005 * 1.18 + 0.0002)
OUTP = "out/phase_discovery/venue_characterisation.json"


def main() -> int:
    print("VENUE CHARACTERISATION -- unconditional move size vs the cost floor")
    print(f"TRAIN window only. Round-trip cost {ROUND_TRIP_BPS:.1f} bps.\n")

    rows = []
    for sym in SYMBOLS:
        df = CandleStore().read(sym, "ltp", "1m")
        df = df[(df.time >= STUDY) & (df.time < TRAIN[1])].reset_index(drop=True)
        t = df["time"].to_numpy("int64")
        c = df["close"].to_numpy("float64")
        for h in HORIZONS_MIN:
            j = np.searchsorted(t, t + h * 60, side="left")
            ok = j < t.size
            j = np.where(ok, np.minimum(j, t.size - 1), 0)
            ok &= np.abs(t[j] - (t + h * 60)) <= 60
            r = np.where(ok, c[j] / c - 1.0, np.nan)
            a = np.abs(r[np.isfinite(r)])
            rows.append(dict(symbol=sym, horizon_min=h, n=int(a.size),
                             median_abs_bps=float(1e4 * np.median(a)),
                             mean_abs_bps=float(1e4 * a.mean()),
                             p90_abs_bps=float(1e4 * np.quantile(a, 0.90))))

    d = pd.DataFrame(rows)
    agg = d.groupby("horizon_min").agg(
        median_abs_bps=("median_abs_bps", "mean"),
        p90_abs_bps=("p90_abs_bps", "mean")).reset_index()
    agg["cost_pct_of_median_move"] = 100 * ROUND_TRIP_BPS / agg["median_abs_bps"]
    agg["cost_pct_of_p90_move"] = 100 * ROUND_TRIP_BPS / agg["p90_abs_bps"]

    print(f"  {'horizon':>8} {'median |move|':>14} {'p90 |move|':>12} "
          f"{'cost/median':>12} {'cost/p90':>10}")
    for _, r in agg.iterrows():
        print(f"  {'+' + str(int(r.horizon_min)) + 'm':>8} "
              f"{r.median_abs_bps:>11.1f} bps {r.p90_abs_bps:>9.1f} bps "
              f"{r.cost_pct_of_median_move:>11.1f}% {r.cost_pct_of_p90_move:>9.1f}%")

    out = dict(round_trip_cost_bps=ROUND_TRIP_BPS,
               window="TRAIN only, 2025-01-01 -> 2025-12-20",
               per_symbol=rows, pooled=agg.to_dict("records"))
    with open(OUTP, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"\nwritten -> {OUTP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
