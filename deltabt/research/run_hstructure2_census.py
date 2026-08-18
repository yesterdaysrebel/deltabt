"""H-STRUCTURE-2 manifest freeze: event census + hashes. NO RETURNS ARE READ.

    PYTHONPATH=. python3 -m deltabt.research.run_hstructure2_census

This runs BEFORE TRAIN and exists to make the frozen definition auditable: how
many events each pre-declared type produces, per symbol, per split, per horizon.

It prints COUNTS ONLY. Per pre-registration §2.2 the census may not change the
swing strength, the timeframe or the trigger. If the counts are too small the
verdict is INSUFFICIENT POWER -- which is an answer, not a licence to re-pick a
parameter until the sample looks comfortable.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

from deltabt.data.store import CandleStore
from deltabt.research import hstructure2 as h2

PREREG = h2.OUT / "hstructure2_preregistration.md"
MODULE = Path(h2.__file__)
ARCHIVE = h2._ARCHIVE


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(symbol: str) -> pd.DataFrame:
    df = CandleStore().read(symbol, "ltp", "1m")
    if df.empty:
        raise SystemExit(f"no cached 1m candles for {symbol}; run `deltabt fetch`")
    m = (df.time >= h2.STUDY) & (df.time <= h2.DATA_END)
    return df[m].reset_index(drop=True)


def main() -> int:
    h2.OUT.mkdir(parents=True, exist_ok=True)
    print("H-STRUCTURE-2 MANIFEST FREEZE -- event census, counts only\n")
    print(f"  structure {h2.STRUCT_TF_MIN}m | swing N={h2.SWING_N} | "
          f"trigger {h2.TRIGGER} | primary horizon +{h2.PRIMARY_HORIZON_MIN}m")
    print(f"  train {pd.Timestamp(h2.TRAIN[0], unit='s').date()} -> "
          f"{pd.Timestamp(h2.TRAIN[1], unit='s').date()}")
    print(f"  valid {pd.Timestamp(h2.VALID[0], unit='s').date()} -> "
          f"{pd.Timestamp(h2.VALID[1], unit='s').date()}")
    print("  test  LOCKED, not computed\n")

    frames, per_symbol = [], {}
    for sym in h2.SYMBOLS:
        df = load(sym)
        ev = h2.events(df, sym)
        frames.append(ev)
        per_symbol[sym] = dict(
            bars_1m=int(len(df)),
            first=str(pd.Timestamp(int(df.time.iloc[0]), unit="s")),
            last=str(pd.Timestamp(int(df.time.iloc[-1]), unit="s")),
            events={k: int((ev.event == k).sum()) for k in h2.EVENTS})
        print(f"  {sym:8} {len(df):>9,} 1m bars  events "
              + "  ".join(f"{k}={int((ev.event == k).sum()):,}" for k in h2.EVENTS))

    ev = pd.concat(frames, ignore_index=True).sort_values("t0").reset_index(drop=True)

    census = {}
    print("\n  events measurable ENTIRELY inside each split, per horizon")
    print(f"  {'horizon':>8} | {'S2-CONT train':>14} {'valid':>7} | "
          f"{'S2-FAIL train':>14} {'valid':>7}")
    for hzn in h2.HORIZONS_MIN:
        row = {}
        for fam in h2.FAMILIES:
            f = h2.family_frame(ev, fam)
            for name, split in (("train", h2.TRAIN), ("valid", h2.VALID)):
                d = h2.in_split(f, split, hzn)
                row[f"{fam}|{name}"] = dict(
                    n=int(len(d)),
                    n_clusters=int(pd.unique(h2.day_cluster(d.t0.to_numpy())).size)
                    if len(d) else 0,
                    long=int((d.direction == 1).sum()),
                    short=int((d.direction == -1).sum()))
        census[f"+{hzn}m"] = row
        print(f"  {'+' + str(hzn) + 'm':>8} | {row['S2-CONT|train']['n']:>14,} "
              f"{row['S2-CONT|valid']['n']:>7,} | {row['S2-FAIL|train']['n']:>14,} "
              f"{row['S2-FAIL|valid']['n']:>7,}")

    manifest = dict(
        experiment_id="H-STRUCTURE-2",
        phase="MARKET PHENOMENON DISCOVERY",
        protocol_sha256=sha256(Path("out/phase_discovery/research_protocol.md")),
        preregistration_sha256=sha256(PREREG),
        module_sha256=sha256(MODULE),
        reused_swing_detector=dict(path=str(ARCHIVE.relative_to(Path.cwd())),
                                   sha256=sha256(ARCHIVE),
                                   provenance="H-Structure-1, anti-lookahead audited"),
        frozen=dict(structure_tf_min=h2.STRUCT_TF_MIN, swing_n=h2.SWING_N,
                    trigger=h2.TRIGGER, events=list(h2.EVENTS),
                    families={k: list(v) for k, v in h2.FAMILIES.items()},
                    horizons_min=list(h2.HORIZONS_MIN),
                    primary_horizon_min=h2.PRIMARY_HORIZON_MIN,
                    cluster_unit="calendar UTC day, pooled across symbols",
                    inference_primary="cluster", mde_k=h2.MDE_K,
                    control="within-symbol direction permutation",
                    control_seed=h2.CONTROL_SEED,
                    control_permutations=h2.CONTROL_PERMUTATIONS),
        splits=dict(study=h2.STUDY, data_end=h2.DATA_END,
                    train=list(h2.TRAIN), valid=list(h2.VALID),
                    test="LOCKED - 2026-04-16 onward, never computed"),
        universe=list(h2.SYMBOLS),
        per_symbol=per_symbol,
        census=census,
        census_is_counts_only=True,
        census_may_not_change_parameters=(
            "per pre-registration §2.2: the census reads no forward return and "
            "may not change N, the timeframe or the trigger"),
    )
    out = h2.OUT / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\n  prereg  sha256 {manifest['preregistration_sha256']}")
    print(f"  module  sha256 {manifest['module_sha256']}")
    print(f"  archive sha256 {manifest['reused_swing_detector']['sha256']}")
    print(f"\n  frozen -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
