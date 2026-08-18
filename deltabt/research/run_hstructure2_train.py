"""H-STRUCTURE-2 STAGE A on TRAIN. VALID IS NOT CONSTRUCTED IN THIS FILE.

    PYTHONPATH=. python3 -m deltabt.research.run_hstructure2_train

Runs once against the frozen manifest and applies the pre-declared Stage-A gate
A1-A5. A6 (VALID) is a separate script and is run only if A1-A5 pass.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from deltabt.data.store import CandleStore
from deltabt.research import hstructure2 as h2

MAN = json.loads((h2.OUT / "manifest.json").read_text())
PRIMARY = h2.PRIMARY_HORIZON_MIN


def check_manifest() -> None:
    """The definition must be the one that was frozen, byte for byte."""
    got = hashlib.sha256(Path(h2.__file__).read_bytes()).hexdigest()
    if got != MAN["module_sha256"]:
        raise SystemExit(
            f"hstructure2.py has changed since the manifest was frozen.\n"
            f"  frozen {MAN['module_sha256']}\n  now    {got}\n"
            f"Once TRAIN starts the hypothesis definition is immutable "
            f"(protocol §2). Restore the module or re-freeze deliberately.")


def build_events() -> pd.DataFrame:
    frames = []
    for sym in h2.SYMBOLS:
        df = CandleStore().read(sym, "ltp", "1m")
        m = (df.time >= h2.STUDY) & (df.time <= h2.DATA_END)
        frames.append(h2.events(df[m].reset_index(drop=True), sym))
    return pd.concat(frames, ignore_index=True).sort_values("t0").reset_index(drop=True)


def row(d: pd.DataFrame, hzn: int) -> dict:
    y = d[f"y_{hzn}"].to_numpy("float64")
    t0 = d["t0"].to_numpy("int64")
    r = h2.estimate(y, t0)
    r["effect_over_mde"] = r["effect"] / r["mde"] if r["mde"] else np.nan
    return r


def analyse(ev: pd.DataFrame, family: str, split: tuple[int, int]) -> dict:
    fam = h2.family_frame(ev, family)
    out = {"family": family, "horizons": {}, "per_symbol": {}, "halves": {}}

    for hzn in h2.HORIZONS_MIN:
        d = h2.in_split(fam, split, hzn)
        out["horizons"][f"+{hzn}m"] = dict(
            pooled=row(d, hzn),
            long=row(d[d.direction == 1], hzn),
            short=row(d[d.direction == -1], hzn))

    d = h2.in_split(fam, split, PRIMARY)
    for sym in h2.SYMBOLS:
        out["per_symbol"][sym] = row(d[d.symbol == sym], PRIMARY)

    # A4 -- TRAIN split in half by time
    mid = (split[0] + split[1]) // 2
    out["halves"]["H1"] = row(d[d.t0 < mid], PRIMARY)
    out["halves"]["H2"] = row(d[d.t0 >= mid], PRIMARY)

    out["control"] = h2.control(d[f"y_{PRIMARY}"].to_numpy("float64"),
                                d["direction"].to_numpy("float64"),
                                d["symbol"].to_numpy())
    out["n_long"] = int((d.direction == 1).sum())
    out["n_short"] = int((d.direction == -1).sum())
    return out


def gate(a: dict, *, symbols_required: int = 3) -> dict:
    """The pre-declared Stage-A gate, at the pre-declared primary horizon.

    ``symbols_required`` exists for H-REL-1 only, whose event universe has three
    symbols rather than four because the leader is excluded and cannot lag
    itself. 3-of-4 would be unreachable there and would fail automatically --
    a bug, not a gate. The default is unchanged, so H-STRUCTURE-2 and H-VOL-1
    are judged exactly as before.
    """
    p = a["horizons"][f"+{PRIMARY}m"]["pooled"]
    eff, mde = p["effect"], p["mde"]
    ctl = a["control"]
    h1, h2_ = a["halves"]["H1"]["effect"], a["halves"]["H2"]["effect"]
    syms = [v["effect"] for v in a["per_symbol"].values()]
    agree = sum(1 for s in syms if np.sign(s) == np.sign(eff))

    g = {
        "A1_train_effect": dict(
            value=eff, passed=bool(np.isfinite(eff) and eff != 0.0),
            note="effect at the primary horizon is nonzero"),
        "A2_power": dict(
            effect=eff, mde=mde, ratio=eff / mde if mde else np.nan,
            passed=bool(abs(eff) >= mde),
            note="|effect| >= MDE, else INSUFFICIENT POWER"),
        "A3_control": dict(
            effect=eff, control_mean=ctl["mean"],
            excess=eff - ctl["mean"], mde=mde,
            outside_ci=bool(eff < ctl["ci_low"] or eff > ctl["ci_high"]),
            p_value=ctl["p_value"],
            passed=bool((eff - ctl["mean"]) >= mde
                        and (eff < ctl["ci_low"] or eff > ctl["ci_high"]))),
        "A4_temporal": dict(
            H1=h1, H2=h2_,
            passed=bool(np.isfinite(h1) and np.isfinite(h2_)
                        and np.sign(h1) == np.sign(h2_))),
        "A5_cross_sectional": dict(
            per_symbol={k: v["effect"] for k, v in a["per_symbol"].items()},
            agreeing=agree, required=symbols_required,
            passed=bool(agree >= symbols_required)),
    }
    g["all_passed"] = all(v["passed"] for k, v in g.items() if k.startswith("A"))
    if not g["A2_power"]["passed"]:
        g["verdict"] = "INSUFFICIENT POWER"
    elif not (g["A1_train_effect"]["passed"] and g["A3_control"]["passed"]):
        g["verdict"] = "NO INFORMATION"
    elif not (g["A4_temporal"]["passed"] and g["A5_cross_sectional"]["passed"]):
        g["verdict"] = "NO INFORMATION"
    else:
        g["verdict"] = "PROCEED TO VALID"
    return g


def fmt(x, pct=True):
    if x is None or not np.isfinite(x):
        return "     n/a"
    return f"{100 * x:+8.4f}%" if pct else f"{x:+8.3f}"


def main() -> int:
    check_manifest()
    print("=" * 92)
    print("H-STRUCTURE-2  STAGE A  --  TRAIN ONLY.  VALID NOT COMPUTED.  TEST LOCKED.")
    print("=" * 92)
    print(f"  {h2.STRUCT_TF_MIN}m structure | swing N={h2.SWING_N} | "
          f"{h2.TRIGGER} | primary horizon +{PRIMARY}m | cluster = UTC day\n")

    ev = build_events()
    results = {}
    for family in h2.FAMILIES:
        a = analyse(ev, family, h2.TRAIN)
        a["gate"] = gate(a)
        results[family] = a

        print("-" * 92)
        print(f"{family}   {' + '.join(h2.FAMILIES[family])}"
              f"   long={a['n_long']:,} short={a['n_short']:,}")
        print("-" * 92)
        print(f"  {'horizon':>8} {'n':>7} {'effect':>10} {'95% CI':>22} "
              f"{'t':>8} {'MDE':>10} {'eff/MDE':>8} {'win':>7}")
        for hzn in h2.HORIZONS_MIN:
            p = a["horizons"][f"+{hzn}m"]["pooled"]
            ci = f"[{fmt(p['ci_low'])}, {fmt(p['ci_high'])}]"
            star = "  <-- PRIMARY" if hzn == PRIMARY else ""
            print(f"  {'+' + str(hzn) + 'm':>8} {p['n']:>7,} {fmt(p['effect'])} "
                  f"{ci:>22} {fmt(p['t'], False)} {fmt(p['mde'])} "
                  f"{fmt(p['effect_over_mde'], False)} {p['win_rate']:>6.3f}{star}")

        pr = a["horizons"][f"+{PRIMARY}m"]
        print(f"\n  at +{PRIMARY}m   long {fmt(pr['long']['effect'])} "
              f"(n={pr['long']['n']:,})   short {fmt(pr['short']['effect'])} "
              f"(n={pr['short']['n']:,})")
        print("  per symbol   " + "   ".join(
            f"{s} {fmt(a['per_symbol'][s]['effect'])}" for s in h2.SYMBOLS))
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

    (h2.OUT / "train_results.json").write_text(
        json.dumps(results, indent=2, default=float) + "\n")
    print("=" * 92)
    print("VALID NOT COMPUTED. TEST NOT COMPUTED.")
    print(f"written -> {h2.OUT / 'train_results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
