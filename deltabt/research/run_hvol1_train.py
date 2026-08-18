"""H-VOL-1 STAGE A on TRAIN. VALID IS NOT CONSTRUCTED IN THIS FILE.

    PYTHONPATH=. python3 -m deltabt.research.run_hvol1_train

``row`` and ``gate`` are imported from the H-STRUCTURE-2 runner rather than
re-implemented, so the two hypotheses cannot drift into being judged by
subtly different arithmetic.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from deltabt.data.store import CandleStore
from deltabt.research import hvol1 as v1
from deltabt.research.run_hstructure2_train import fmt, gate, row

MAN = json.loads((v1.OUT / "manifest.json").read_text())
PRIMARY = v1.PRIMARY_HORIZON_MIN


def check_manifest() -> None:
    got = hashlib.sha256(Path(v1.__file__).read_bytes()).hexdigest()
    if got != MAN["module_sha256"]:
        raise SystemExit(
            f"hvol1.py has changed since the manifest was frozen.\n"
            f"  frozen {MAN['module_sha256']}\n  now    {got}\n"
            f"Once TRAIN starts the hypothesis definition is immutable.")


def main() -> int:
    check_manifest()
    print("=" * 92)
    print("H-VOL-1  STAGE A  --  TRAIN ONLY.  VALID NOT COMPUTED.  TEST LOCKED.")
    print("=" * 92)
    print(f"  compression inherited from H-Compress-1: ATR({v1.ATR_PERIOD})/close < "
          f"{v1.PERCENTILE:.0%} pctile / {v1.PCT_LOOKBACK} bars, "
          f">={v1.MIN_DURATION} bars, range/ATR<={v1.RANGE_MAX}")
    print(f"  primary horizon +{PRIMARY}m | cluster = UTC day\n")

    frames = []
    for sym in v1.SYMBOLS:
        df = CandleStore().read(sym, "ltp", "1m")
        m = (df.time >= v1.STUDY) & (df.time <= v1.DATA_END)
        frames.append(v1.events(df[m].reset_index(drop=True), sym))
    ev = pd.concat(frames, ignore_index=True).sort_values("t0").reset_index(drop=True)

    fam = v1.family_frame(ev, "V1-EXP")
    a = {"family": "V1-EXP", "horizons": {}, "per_symbol": {}, "halves": {}}
    for hzn in v1.HORIZONS_MIN:
        d = v1.in_split(fam, v1.TRAIN, hzn)
        a["horizons"][f"+{hzn}m"] = dict(pooled=row(d, hzn),
                                         long=row(d[d.direction == 1], hzn),
                                         short=row(d[d.direction == -1], hzn))
    d = v1.in_split(fam, v1.TRAIN, PRIMARY)
    for sym in v1.SYMBOLS:
        a["per_symbol"][sym] = row(d[d.symbol == sym], PRIMARY)
    mid = (v1.TRAIN[0] + v1.TRAIN[1]) // 2
    a["halves"]["H1"] = row(d[d.t0 < mid], PRIMARY)
    a["halves"]["H2"] = row(d[d.t0 >= mid], PRIMARY)
    a["control"] = v1.control(d[f"y_{PRIMARY}"].to_numpy("float64"),
                              d["direction"].to_numpy("float64"),
                              d["symbol"].to_numpy())
    a["n_long"] = int((d.direction == 1).sum())
    a["n_short"] = int((d.direction == -1).sum())
    a["gate"] = gate(a)

    print("-" * 92)
    print(f"V1-EXP   EXP_UP + EXP_DOWN   long={a['n_long']:,} short={a['n_short']:,}")
    print("-" * 92)
    print(f"  {'horizon':>8} {'n':>7} {'effect':>10} {'95% CI':>22} "
          f"{'t':>8} {'MDE':>10} {'eff/MDE':>8} {'win':>7}")
    for hzn in v1.HORIZONS_MIN:
        p = a["horizons"][f"+{hzn}m"]["pooled"]
        ci = f"[{fmt(p['ci_low'])}, {fmt(p['ci_high'])}]"
        star = "  <-- PRIMARY" if hzn == PRIMARY else ""
        print(f"  {'+' + str(hzn) + 'm':>8} {p['n']:>7,} {fmt(p['effect'])} "
              f"{ci:>22} {fmt(p['t'], False)} {fmt(p['mde'])} "
              f"{fmt(p['effect_over_mde'], False)} {p['win_rate']:>6.3f}{star}")

    pr = a["horizons"][f"+{PRIMARY}m"]
    print(f"\n  at +{PRIMARY}m   long {fmt(pr['long']['effect'])} (n={pr['long']['n']:,})"
          f"   short {fmt(pr['short']['effect'])} (n={pr['short']['n']:,})")
    print("  per symbol   " + "   ".join(
        f"{s} {fmt(a['per_symbol'][s]['effect'])}" for s in v1.SYMBOLS))
    print(f"  halves       H1 {fmt(a['halves']['H1']['effect'])} "
          f"H2 {fmt(a['halves']['H2']['effect'])}")
    c = a["control"]
    print(f"  control      mean {fmt(c['mean'])} "
          f"95% [{fmt(c['ci_low'])}, {fmt(c['ci_high'])}]  p={c['p_value']:.4f}")

    g = a["gate"]
    print()
    for k in ("A1_train_effect", "A2_power", "A3_control", "A4_temporal",
              "A5_cross_sectional"):
        print(f"  {'PASS' if g[k]['passed'] else 'FAIL'}  {k}")
    print(f"\n  STAGE A (TRAIN): {g['verdict']}\n")

    (v1.OUT / "train_results.json").write_text(
        json.dumps({"V1-EXP": a}, indent=2, default=float) + "\n")
    print("=" * 92)
    print("VALID NOT COMPUTED. TEST NOT COMPUTED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
