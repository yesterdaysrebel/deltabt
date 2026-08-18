"""Apply the pre-declared feasibility gate to paths A, B and C.

    PYTHONPATH=. python3 -m deltabt.research.run_feasibility_gate

THE GATE
    A path may proceed to strategy research only if

        required predictive edge <= 2 x the largest credible effect observed
        in the existing research

    OR there is a concrete, evidence-based reason the new regime can produce
    materially larger effects.

WHY THE GATE IS EVALUATED FOUR WAYS
    "Largest credible effect" has no single defensible value, and picking one
    would let the answer depend on my choice. The strict reading is that NOTHING
    in nineteen experiments was credibly established -- every measured effect
    sits below its own MDE and inside its own control distribution -- which puts
    the bar at zero and fails everything by definition. The generous readings
    use confidence-interval upper bounds, which are large mainly because the
    data is noisy, not because an effect was found. All four are reported.
"""

from __future__ import annotations

import json
import sys

import numpy as np

from deltabt.research.hstructure2 import HORIZONS_MIN

ECON = json.loads(open("out/phase_discovery/feasibility_economics.json").read())
SRC = {"H-STRUCTURE-2": "out/hstructure2/train_results.json",
       "H-VOL-1": "out/hvol1/train_results.json",
       "H-REL-1": "out/hrel1/train_results.json"}


def observed_effects() -> dict:
    """Every directional effect the discovery phase actually measured."""
    rows = []
    for exp, path in SRC.items():
        for fam, a in json.loads(open(path).read()).items():
            for hz, v in a["horizons"].items():
                p = v["pooled"]
                rows.append(dict(experiment=exp, family=fam, horizon=hz,
                                 effect_bps=1e4 * p["effect"],
                                 ci_high_bps=1e4 * p["ci_high"],
                                 ci_low_bps=1e4 * p["ci_low"],
                                 mde_bps=1e4 * p["mde"], t=p["t"],
                                 primary=hz == "+60m"))
    prim = [r for r in rows if r["primary"]]
    return dict(
        rows=rows,
        definitions={
            "D1_strict_credible": dict(
                value=0.0,
                basis=("nothing was credibly established: every effect is below "
                       "its own MDE and inside its own control distribution, and "
                       "nineteen experiments produced zero positive verdicts")),
            "D2_largest_point_estimate_primary": dict(
                value=float(max(abs(r["effect_bps"]) for r in prim)),
                basis="largest |effect| at the pre-declared +1h horizon"),
            "D3_largest_ci_upper_primary": dict(
                value=float(max(r["ci_high_bps"] for r in prim)),
                basis="largest 95% CI upper bound at +1h -- the biggest effect "
                      "the data cannot exclude, not one it supports"),
            "D4_largest_ci_upper_any_horizon": dict(
                value=float(max(r["ci_high_bps"] for r in rows)),
                basis="largest 95% CI upper bound at ANY horizon; large mainly "
                      "because long-horizon returns are noisy"),
        })


#: Fraction of available bars on which a real signal actually fires. Taken from
#: the discovery phase's BEST observed event rate: H-STRUCTURE-2's 3,811 events
#: against 33,892 non-overlapping +1h bars. Using 100% would assume a signal
#: that trades every bar, which no tested phenomenon came close to.
DUTY_CYCLE = 3811 / 33892

#: Cluster inflation of the standard error over the iid one. Calibrated, not
#: assumed: at DUTY_CYCLE the iid MDE at +1h is 3.55 bps and H-STRUCTURE-2's
#: measured cluster MDE was 4.98, a factor of 1.40.
CLUSTER_INFLATION = 1.40


def realistic_mde(sd_bps: float, n_available: int) -> float:
    """MDE a real signal would face: fires on DUTY_CYCLE of bars, cluster SE."""
    n = max(DUTY_CYCLE * n_available, 1.0)
    return 2.8 * sd_bps / np.sqrt(n) * CLUSTER_INFLATION


def paths(obs: dict) -> list[dict]:
    A = {r["horizon"]: r for r in ECON["path_a"]}
    C = ECON["costs"]
    B = ECON["path_b"]["pooled"]
    adverse = B["adverse_selection_bps_per_leg"]

    out = []

    # ---- A: longer horizon, taker execution unchanged
    for hz in ("+4h", "+12h", "+1d", "+3d"):
        r = A[hz]
        req = C["rt_taker"] + max(r["funding_biased_bps"], 0.0)
        out.append(dict(
            path="A", variant=f"horizon {hz}, taker",
            horizon_or_data=hz, cost_bps=req,
            typical_move_bps=r["median_move_bps"],
            cost_over_move=req / r["median_move_bps"],
            min_viable_edge_bps=req,
            n_obs_per_year=r["n_independent_obs"],
            n_events_realistic=int(DUTY_CYCLE * r["n_independent_obs"]),
            mde_bps=realistic_mde(r["sd_move_bps"], r["n_independent_obs"]),
            mde_over_required=realistic_mde(r["sd_move_bps"],
                                            r["n_independent_obs"]) / req,
            measurable=bool(realistic_mde(r["sd_move_bps"],
                                          r["n_independent_obs"]) < req)))

    # ---- B: maker execution, +1h unchanged
    h1 = A["+1h"]
    for label, cost in (("maker both legs", C["rt_maker"]),
                        ("maker both legs + adverse selection", C["rt_maker"] + 2 * adverse),
                        ("maker in / taker out", C["rt_mixed"])):
        out.append(dict(
            path="B", variant=label, horizon_or_data="+1h",
            cost_bps=cost, typical_move_bps=h1["median_move_bps"],
            cost_over_move=cost / h1["median_move_bps"],
            min_viable_edge_bps=cost,
            n_obs_per_year=h1["n_independent_obs"],
            n_events_realistic=int(DUTY_CYCLE * h1["n_independent_obs"]),
            mde_bps=realistic_mde(h1["sd_move_bps"], h1["n_independent_obs"]),
            mde_over_required=realistic_mde(h1["sd_move_bps"],
                                            h1["n_independent_obs"]) / cost,
            measurable=bool(realistic_mde(h1["sd_move_bps"],
                                          h1["n_independent_obs"]) < cost)))

    # ---- C: microstructure data. Cost is UNCHANGED; only the signal source moves.
    for hz, mv in (("+5m", 10.0), ("+15m", 17.3)):
        out.append(dict(
            path="C", variant=f"L2 + trade prints, taker, {hz}",
            horizon_or_data=f"{hz} / L2+prints",
            cost_bps=C["rt_taker"], typical_move_bps=mv,
            cost_over_move=C["rt_taker"] / mv,
            min_viable_edge_bps=C["rt_taker"],
            n_obs_per_year=None, mde_bps=None, mde_over_required=None,
            measurable=None))
    out.append(dict(
        path="C", variant="L2 + trade prints, MAKER, +1h",
        horizon_or_data="+1h / L2+prints",
        cost_bps=C["rt_maker"] + 2 * adverse,
        typical_move_bps=h1["median_move_bps"],
        cost_over_move=(C["rt_maker"] + 2 * adverse) / h1["median_move_bps"],
        min_viable_edge_bps=C["rt_maker"] + 2 * adverse,
        n_obs_per_year=h1["n_independent_obs"],
        n_events_realistic=int(DUTY_CYCLE * h1["n_independent_obs"]),
        mde_bps=realistic_mde(h1["sd_move_bps"], h1["n_independent_obs"]),
        mde_over_required=realistic_mde(h1["sd_move_bps"],
                                        h1["n_independent_obs"])
        / (C["rt_maker"] + 2 * adverse),
        measurable=bool(realistic_mde(h1["sd_move_bps"], h1["n_independent_obs"])
                        < C["rt_maker"] + 2 * adverse)))

    for p in out:
        for k, d in obs["definitions"].items():
            p[f"gate_{k}"] = bool(p["min_viable_edge_bps"] <= 2 * d["value"])
    return out


def main() -> int:
    obs = observed_effects()
    ps = paths(obs)

    print("=" * 100)
    print("LARGEST CREDIBLE EFFECT -- four readings, because there is no single one")
    print("=" * 100)
    for k, d in obs["definitions"].items():
        print(f"  {k:36} {d['value']:>7.2f} bps   gate = {2 * d['value']:>6.2f} bps")
        print(f"    {d['basis']}")

    print("\n" + "=" * 100)
    print("DECISION TABLE")
    print("=" * 100)
    print(f"  duty cycle {DUTY_CYCLE:.1%} of bars (the phase's best observed "
          f"event rate); cluster inflation {CLUSTER_INFLATION:.2f}x\n")
    hdr = (f"  {'path':<5} {'variant':<38} {'cost':>7} {'move':>8} {'c/m':>7} "
           f"{'events':>8} {'MDE':>8} {'MDE/req':>8} {'meas':>5}")
    print(hdr)
    for p in ps:
        mde = f"{p['mde_bps']:.1f}" if p["mde_bps"] is not None else "n/a"
        mor = f"{p['mde_over_required']:.2f}x" if p["mde_over_required"] is not None else "n/a"
        meas = {True: "YES", False: "NO", None: "n/a"}[p["measurable"]]
        ev = f"{p.get('n_events_realistic'):,}" if p.get("n_events_realistic") else "n/a"
        print(f"  {p['path']:<5} {p['variant']:<38} {p['cost_bps']:>6.2f} "
              f"{p['typical_move_bps']:>7.1f} {100 * p['cost_over_move']:>6.1f}% "
              f"{ev:>8} {mde:>8} {mor:>8} {meas:>5}")

    print("\n" + "=" * 100)
    print("GATE CLAUSE 1 -- required edge <= 2 x largest credible effect")
    print("=" * 100)
    keys = list(obs["definitions"])
    print(f"  {'path':<5} {'variant':<38} " + " ".join(f"{k.split('_')[0]:>6}" for k in keys))
    for p in ps:
        print(f"  {p['path']:<5} {p['variant']:<38} "
              + " ".join(f"{'PASS' if p[f'gate_{k}'] else 'fail':>6}" for k in keys))

    json.dump(dict(observed=obs, paths=ps),
              open("out/phase_discovery/feasibility_gate.json", "w"), indent=2)
    print("\nwritten -> out/phase_discovery/feasibility_gate.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
