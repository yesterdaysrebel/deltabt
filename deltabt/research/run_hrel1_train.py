"""H-REL-1 STAGE A on TRAIN. VALID IS NOT CONSTRUCTED IN THIS FILE.

    PYTHONPATH=. python3 -m deltabt.research.run_hrel1_train

``row``, ``gate`` and ``fmt`` are imported from the H-STRUCTURE-2 runner so all
three hypotheses in the phase are judged by identical arithmetic. The only
difference is A5's ``symbols_required``, declared in the pre-registration.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

from deltabt.data.store import CandleStore
from deltabt.research import hrel1 as r1
from deltabt.research.run_hstructure2_train import fmt, gate, row

MAN = json.loads((r1.OUT / "manifest.json").read_text())
PRIMARY = r1.PRIMARY_HORIZON_MIN


def check_manifest() -> None:
    got = hashlib.sha256(Path(r1.__file__).read_bytes()).hexdigest()
    if got != MAN["module_sha256"]:
        raise SystemExit(
            f"hrel1.py has changed since the manifest was frozen.\n"
            f"  frozen {MAN['module_sha256']}\n  now    {got}\n"
            f"Once TRAIN starts the hypothesis definition is immutable.")


def load(sym: str) -> pd.DataFrame:
    d = CandleStore().read(sym, "ltp", "1m")
    return d[(d.time >= r1.STUDY) & (d.time <= r1.DATA_END)].reset_index(drop=True)


def main() -> int:
    check_manifest()
    print("=" * 92)
    print("H-REL-1  STAGE A  --  TRAIN ONLY.  VALID NOT COMPUTED.  TEST LOCKED.")
    print("=" * 92)
    print(f"  leader {r1.LEADER} shock >= {r1.SHOCK_PERCENTILE:.0%} pctile "
          f"(trailing {r1.PCT_LOOKBACK}, ends t-1) | followers under-respond")
    print(f"  primary horizon +{PRIMARY}m | cluster = UTC day | "
          f"A5 requires {r1.SYMBOLS_REQUIRED_A5}/3\n")

    lead = r1.leader_shock(r1.bars15(load(r1.LEADER)))
    ev = pd.concat([r1.events(load(f), f, lead) for f in r1.FOLLOWERS],
                   ignore_index=True).sort_values("t0").reset_index(drop=True)

    fam = r1.family_frame(ev, "R1-LAG")
    a = {"family": "R1-LAG", "horizons": {}, "per_symbol": {}, "halves": {}}
    for hzn in r1.HORIZONS_MIN:
        d = r1.in_split(fam, r1.TRAIN, hzn)
        a["horizons"][f"+{hzn}m"] = dict(pooled=row(d, hzn),
                                         long=row(d[d.direction == 1], hzn),
                                         short=row(d[d.direction == -1], hzn))
    d = r1.in_split(fam, r1.TRAIN, PRIMARY)
    for sym in r1.FOLLOWERS:
        a["per_symbol"][sym] = row(d[d.symbol == sym], PRIMARY)
    mid = (r1.TRAIN[0] + r1.TRAIN[1]) // 2
    a["halves"]["H1"] = row(d[d.t0 < mid], PRIMARY)
    a["halves"]["H2"] = row(d[d.t0 >= mid], PRIMARY)
    a["control"] = r1.control(d[f"y_{PRIMARY}"].to_numpy("float64"),
                              d["direction"].to_numpy("float64"),
                              d["symbol"].to_numpy())
    a["n_long"] = int((d.direction == 1).sum())
    a["n_short"] = int((d.direction == -1).sum())
    a["gate"] = gate(a, symbols_required=r1.SYMBOLS_REQUIRED_A5)

    print("-" * 92)
    print(f"R1-LAG   LAG_UP + LAG_DOWN   long={a['n_long']:,} short={a['n_short']:,}")
    print("-" * 92)
    print(f"  {'horizon':>8} {'n':>7} {'effect':>10} {'95% CI':>22} "
          f"{'t':>8} {'MDE':>10} {'eff/MDE':>8} {'win':>7}")
    for hzn in r1.HORIZONS_MIN:
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
        f"{s} {fmt(a['per_symbol'][s]['effect'])}" for s in r1.FOLLOWERS))
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

    (r1.OUT / "train_results.json").write_text(
        json.dumps({"R1-LAG": a}, indent=2, default=float) + "\n")
    print("=" * 92)
    print("VALID NOT COMPUTED. TEST NOT COMPUTED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
