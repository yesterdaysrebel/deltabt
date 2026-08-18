"""H-REL-1 manifest freeze: event census + hashes. NO RETURNS ARE READ.

    PYTHONPATH=. python3 -m deltabt.research.run_hrel1_census
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

from deltabt.data.store import CandleStore
from deltabt.research import hcompress, hstructure2
from deltabt.research import hrel1 as r1


def sha256(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load(sym: str) -> pd.DataFrame:
    d = CandleStore().read(sym, "ltp", "1m")
    if d.empty:
        raise SystemExit(f"no cached candles for {sym}")
    return d[(d.time >= r1.STUDY) & (d.time <= r1.DATA_END)].reset_index(drop=True)


def main() -> int:
    r1.OUT.mkdir(parents=True, exist_ok=True)
    print("H-REL-1 MANIFEST FREEZE -- event census, counts only\n")
    print(f"  leader {r1.LEADER} | followers {', '.join(r1.FOLLOWERS)}")
    print(f"  shock = |r| >= {r1.SHOCK_PERCENTILE:.0%} pctile of trailing "
          f"{r1.PCT_LOOKBACK} bars ending at t-1 | {r1.TF_MIN}m")
    print(f"  primary horizon +{r1.PRIMARY_HORIZON_MIN}m | test LOCKED\n")

    lead = r1.leader_shock(r1.bars15(load(r1.LEADER)))
    n_shock = int(lead["shock"].sum())
    print(f"  {r1.LEADER:8} {len(lead):>8,} 15m bars   {n_shock:,} shocks "
          f"({n_shock / len(lead):.1%})")

    frames, per_symbol = [], {}
    for f in r1.FOLLOWERS:
        ev = r1.events(load(f), f, lead)
        frames.append(ev)
        per_symbol[f] = {k: int((ev.event == k).sum()) for k in r1.EVENTS}
        print(f"  {f:8} " + "  ".join(
            f"{k}={int((ev.event == k).sum()):,}" for k in r1.EVENTS))

    ev = pd.concat(frames, ignore_index=True).sort_values("t0").reset_index(drop=True)

    census = {}
    print(f"\n  {'horizon':>8} | {'train n':>8} {'clusters':>9} | "
          f"{'valid n':>8} {'clusters':>9}")
    for hzn in r1.HORIZONS_MIN:
        rowc = {}
        for name, split in (("train", r1.TRAIN), ("valid", r1.VALID)):
            d = r1.in_split(ev, split, hzn)
            rowc[name] = dict(
                n=int(len(d)),
                n_clusters=int(pd.unique(r1.day_cluster(d.t0.to_numpy())).size)
                if len(d) else 0,
                long=int((d.direction == 1).sum()),
                short=int((d.direction == -1).sum()))
        census[f"+{hzn}m"] = rowc
        print(f"  {'+' + str(hzn) + 'm':>8} | {rowc['train']['n']:>8,} "
              f"{rowc['train']['n_clusters']:>9,} | {rowc['valid']['n']:>8,} "
              f"{rowc['valid']['n_clusters']:>9,}")

    manifest = dict(
        experiment_id="H-REL-1",
        phase="MARKET PHENOMENON DISCOVERY",
        protocol_sha256=sha256("out/phase_discovery/research_protocol.md"),
        preregistration_sha256=sha256(r1.OUT / "hrel1_preregistration.md"),
        module_sha256=sha256(r1.__file__),
        inherited=dict(
            causal_quantile=dict(source="deltabt/research/hcompress.py",
                                 sha256=sha256(hcompress.__file__),
                                 function="_rolling_quantile_causal",
                                 window=r1.PCT_LOOKBACK,
                                 property="window ends at t-1 and excludes t"),
            stage_a_machinery=dict(source="deltabt/research/hstructure2.py",
                                   sha256=sha256(hstructure2.__file__),
                                   note="imported unchanged")),
        frozen=dict(leader=r1.LEADER, followers=list(r1.FOLLOWERS),
                    shock_percentile=r1.SHOCK_PERCENTILE, tf_min=r1.TF_MIN,
                    under_response="sign(r_lead) * (r_lead - r_foll) > 0, a sign "
                                   "test against zero -- no gap threshold",
                    events=list(r1.EVENTS),
                    families={k: list(v) for k, v in r1.FAMILIES.items()},
                    trigger="oneshot per follower",
                    horizons_min=list(r1.HORIZONS_MIN),
                    primary_horizon_min=r1.PRIMARY_HORIZON_MIN,
                    cluster_unit="calendar UTC day, pooled across symbols",
                    inference_primary="cluster", mde_k=r1.MDE_K,
                    control_seed=r1.CONTROL_SEED,
                    a5_symbols_required=r1.SYMBOLS_REQUIRED_A5,
                    a5_note="2 of 3 -- the leader is excluded, so 3-of-4 is "
                            "unreachable and would fail automatically. Declared "
                            "before TRAIN; A1-A4 and A6 unchanged."),
        splits=dict(train=list(r1.TRAIN), valid=list(r1.VALID),
                    test="LOCKED - 2026-04-16 onward, never computed"),
        leader_shocks=n_shock, per_symbol=per_symbol, census=census,
        census_is_counts_only=True)
    out = r1.OUT / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\n  prereg sha256 {manifest['preregistration_sha256']}")
    print(f"  module sha256 {manifest['module_sha256']}")
    print(f"\n  frozen -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
