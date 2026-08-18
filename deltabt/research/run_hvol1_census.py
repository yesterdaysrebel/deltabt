"""H-VOL-1 manifest freeze: event census + hashes. NO RETURNS ARE READ.

    PYTHONPATH=. python3 -m deltabt.research.run_hvol1_census

Counts only. Per pre-registration §3 the census may not change the compression
definition. If the counts are small the verdict is INSUFFICIENT POWER, which is
an answer rather than a reason to loosen the percentile.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

from deltabt.data.store import CandleStore
from deltabt.research import hcompress, hstructure2
from deltabt.research import hvol1 as v1


def sha256(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main() -> int:
    v1.OUT.mkdir(parents=True, exist_ok=True)
    print("H-VOL-1 MANIFEST FREEZE -- event census, counts only\n")
    print(f"  {v1.TF_MIN}m | ATR({v1.ATR_PERIOD})/close < {v1.PERCENTILE:.0%} pctile "
          f"of trailing {v1.PCT_LOOKBACK} | >={v1.MIN_DURATION} bars | "
          f"range/ATR <= {v1.RANGE_MAX}")
    print(f"  primary horizon +{v1.PRIMARY_HORIZON_MIN}m | test LOCKED\n")

    frames, per_symbol = [], {}
    for sym in v1.SYMBOLS:
        df = CandleStore().read(sym, "ltp", "1m")
        if df.empty:
            raise SystemExit(f"no cached candles for {sym}")
        df = df[(df.time >= v1.STUDY) & (df.time <= v1.DATA_END)].reset_index(drop=True)
        ev = v1.events(df, sym)
        frames.append(ev)
        per_symbol[sym] = dict(bars_1m=int(len(df)),
                               events={k: int((ev.event == k).sum()) for k in v1.EVENTS})
        print(f"  {sym:8} {len(df):>9,} 1m bars  "
              + "  ".join(f"{k}={int((ev.event == k).sum()):,}" for k in v1.EVENTS))

    ev = pd.concat(frames, ignore_index=True).sort_values("t0").reset_index(drop=True)

    census = {}
    print(f"\n  {'horizon':>8} | {'train n':>8} {'clusters':>9} | "
          f"{'valid n':>8} {'clusters':>9}")
    for hzn in v1.HORIZONS_MIN:
        row = {}
        for name, split in (("train", v1.TRAIN), ("valid", v1.VALID)):
            d = v1.in_split(ev, split, hzn)
            row[name] = dict(n=int(len(d)),
                             n_clusters=int(pd.unique(v1.day_cluster(d.t0.to_numpy())).size)
                             if len(d) else 0,
                             long=int((d.direction == 1).sum()),
                             short=int((d.direction == -1).sum()))
        census[f"+{hzn}m"] = row
        print(f"  {'+' + str(hzn) + 'm':>8} | {row['train']['n']:>8,} "
              f"{row['train']['n_clusters']:>9,} | {row['valid']['n']:>8,} "
              f"{row['valid']['n_clusters']:>9,}")

    manifest = dict(
        experiment_id="H-VOL-1",
        phase="MARKET PHENOMENON DISCOVERY",
        protocol_sha256=sha256("out/phase_discovery/research_protocol.md"),
        preregistration_sha256=sha256(v1.OUT / "hvol1_preregistration.md"),
        module_sha256=sha256(v1.__file__),
        inherited=dict(
            compression_state=dict(
                source="deltabt/research/hcompress.py",
                sha256=sha256(hcompress.__file__),
                functions=["_rolling_quantile_causal", "_compression_zones"],
                provenance="H-Compress-1 frozen pre-registration",
                constants=dict(tf_min=v1.TF_MIN, atr_period=v1.ATR_PERIOD,
                               pct_lookback=v1.PCT_LOOKBACK,
                               percentile=v1.PERCENTILE,
                               min_duration=v1.MIN_DURATION,
                               range_max=v1.RANGE_MAX),
                dropped=["retest entry", "3-bar order lifetime",
                         "volume multiple", "body-size filter"]),
            stage_a_machinery=dict(
                source="deltabt/research/hstructure2.py",
                sha256=sha256(hstructure2.__file__),
                note="imported unchanged; H-VOL-1 defines events and nothing else")),
        frozen=dict(events=list(v1.EVENTS),
                    families={k: list(v) for k, v in v1.FAMILIES.items()},
                    horizons_min=list(v1.HORIZONS_MIN),
                    primary_horizon_min=v1.PRIMARY_HORIZON_MIN,
                    trigger="oneshot",
                    cluster_unit="calendar UTC day, pooled across symbols",
                    inference_primary="cluster", mde_k=v1.MDE_K,
                    control="within-symbol direction permutation",
                    control_seed=v1.CONTROL_SEED,
                    control_permutations=v1.CONTROL_PERMUTATIONS),
        splits=dict(train=list(v1.TRAIN), valid=list(v1.VALID),
                    test="LOCKED - 2026-04-16 onward, never computed"),
        universe=list(v1.SYMBOLS), per_symbol=per_symbol, census=census,
        census_is_counts_only=True)
    out = v1.OUT / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\n  prereg sha256 {manifest['preregistration_sha256']}")
    print(f"  module sha256 {manifest['module_sha256']}")
    print(f"\n  frozen -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
