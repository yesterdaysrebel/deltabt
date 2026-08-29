"""Driver for the volume-shock discovery gate. Frozen spec, no tuning."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import volume_shock_discovery as vs  # noqa: E402

vs.OUT.mkdir(parents=True, exist_ok=True)
DEFS = {"C1_rvol_median": ("rvol_median", vs.RVOL_THRESHOLD),
        "C2_logvol_z": ("logvol_z", vs.LOGZ_THRESHOLD)}


def fingerprint(sym: str) -> dict:
    p = vs.CACHE_DIR / sym / "ltp_1m.parquet"
    h = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    d = pd.read_parquet(p, columns=["time"])
    return {"symbol": sym, "sha256_16": h, "bars": int(len(d)),
            "first": int(d.time.min()), "last": int(d.time.max())}


def main() -> None:
    cells: list[vs.Cell] = []
    prep, fps, counts = {}, [], {}
    for sym in vs.PRIMARY_SYMBOLS + vs.ROBUSTNESS_SYMBOLS + vs.EXPLORATORY_SYMBOLS:
        d = vs.outcomes(vs.build(vs.load(sym)))
        prep[sym] = d
        fps.append(fingerprint(sym))
        row = {}
        for name, (col, thr) in DEFS.items():
            ev = vs.events(d, col, thr)
            bs = vs.baseline_mask(d, ev)
            ok60 = np.isfinite(d["r60"].to_numpy())
            row[name] = {
                "candidate_bars": int(((d[col] >= thr) & d["window_ok"]).sum()),
                "events_after_cooldown": int(ev.sum()),
                "events_with_complete_outcome": int((ev & ok60).sum()),
                "excluded_no_outcome": int((ev & ~ok60).sum()),
                "baseline_bars": int((bs & ok60).sum()),
                "invalid_window_bars": int((~d["window_ok"]).sum()),
            }
        counts[sym] = row

    # ---- PRIMARY: C1, h=30, raw, BTC + ETH
    for sym in vs.PRIMARY_SYMBOLS:
        cells.append(vs.measure(prep[sym], sym, "C1_rvol_median", "rvol_median",
                                vs.RVOL_THRESHOLD, vs.PRIMARY_HORIZON, "raw",
                                tier="PRIMARY", heavy=True))
    # ---- PRE-SPECIFIED: normalised endpoint at the primary horizon
    for sym in vs.PRIMARY_SYMBOLS:
        cells.append(vs.measure(prep[sym], sym, "C1_rvol_median", "rvol_median",
                                vs.RVOL_THRESHOLD, vs.PRIMARY_HORIZON,
                                "normalised", tier="pre-specified", heavy=True))
    # ---- PRE-SPECIFIED: C2 at the primary horizon, both endpoints
    for sym in vs.PRIMARY_SYMBOLS:
        for ep in ("raw", "normalised"):
            cells.append(vs.measure(prep[sym], sym, "C2_logvol_z", "logvol_z",
                                    vs.LOGZ_THRESHOLD, vs.PRIMARY_HORIZON, ep,
                                    tier="pre-specified", heavy=True))
    # ---- PRE-SPECIFIED: other horizons
    for sym in vs.PRIMARY_SYMBOLS:
        for h in vs.HORIZONS:
            if h == vs.PRIMARY_HORIZON:
                continue
            for ep in ("raw", "normalised"):
                cells.append(vs.measure(prep[sym], sym, "C1_rvol_median",
                                        "rvol_median", vs.RVOL_THRESHOLD, h, ep,
                                        tier="pre-specified", heavy=False))
    # ---- PRE-SPECIFIED: symbol robustness
    for sym in vs.ROBUSTNESS_SYMBOLS:
        for ep in ("raw", "normalised"):
            cells.append(vs.measure(prep[sym], sym, "C1_rvol_median",
                                    "rvol_median", vs.RVOL_THRESHOLD,
                                    vs.PRIMARY_HORIZON, ep,
                                    tier="pre-specified", heavy=True))
    # ---- EXPLORATORY: BEATUSD
    for sym in vs.EXPLORATORY_SYMBOLS:
        for ep in ("raw", "normalised"):
            cells.append(vs.measure(prep[sym], sym, "C1_rvol_median",
                                    "rvol_median", vs.RVOL_THRESHOLD,
                                    vs.PRIMARY_HORIZON, ep,
                                    tier="exploratory", heavy=False))

    df = pd.DataFrame([asdict(c) for c in cells])
    df.to_csv(vs.OUT / "cells.csv", index=False)

    # ---- PRE-SPECIFIED: three chronological thirds (primary symbols, C1, h=30)
    thirds = []
    for sym in vs.PRIMARY_SYMBOLS:
        d = prep[sym]
        edges = np.array_split(np.arange(len(d)), 3)
        for k, idx in enumerate(edges, 1):
            sub = d.iloc[idx].reset_index(drop=True)
            for ep in ("raw", "normalised"):
                c = vs.measure(sub, sym, "C1_rvol_median", "rvol_median",
                               vs.RVOL_THRESHOLD, vs.PRIMARY_HORIZON, ep,
                               tier=f"third_{k}", heavy=False)
                thirds.append({**asdict(c), "third": k,
                               "start": str(pd.Timestamp(int(sub.time.iloc[0]),
                                                         unit="s", tz="UTC").date()),
                               "end": str(pd.Timestamp(int(sub.time.iloc[-1]),
                                                       unit="s", tz="UTC").date())})
    pd.DataFrame(thirds).to_csv(vs.OUT / "thirds.csv", index=False)

    # ---- EXPLORATORY: intensity buckets
    buckets = []
    for sym in vs.PRIMARY_SYMBOLS:
        d = prep[sym]
        ev = vs.events(d, "rvol_median", vs.RVOL_THRESHOLD)
        bs = vs.baseline_mask(d, ev)
        y = d[f"r{vs.PRIMARY_HORIZON}"].to_numpy()
        yn = d[f"rn{vs.PRIMARY_HORIZON}"].to_numpy()
        rv = d["rvol_median"].to_numpy()
        base_med = float(np.nanmedian(y[bs]))
        base_medn = float(np.nanmedian(yn[bs]))
        for lo, hi, lab in ((5, 10, "[5,10)"), (10, 20, "[10,20)"),
                            (20, np.inf, ">=20")):
            m = ev & (rv >= lo) & (rv < hi) & np.isfinite(y)
            if m.sum() >= 10:
                buckets.append({"symbol": sym, "bucket": lab, "n": int(m.sum()),
                                "median_raw": float(np.median(y[m])),
                                "ratio_raw": float(np.median(y[m]) / base_med),
                                "median_norm": float(np.nanmedian(yn[m])),
                                "ratio_norm": float(np.nanmedian(yn[m]) / base_medn)})
    pd.DataFrame(buckets).to_csv(vs.OUT / "intensity.csv", index=False)

    (vs.OUT / "run.json").write_text(json.dumps({
        "spec": "docs/volume_shock_discovery_spec.md",
        "spec_sha256": hashlib.sha256(
            (Path(__file__).resolve().parents[1] /
             "docs" / "volume_shock_discovery_spec.md").read_bytes()).hexdigest(),
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                           text=True).strip(),
        "parameters": {"lookback": vs.LOOKBACK, "rvol_threshold": vs.RVOL_THRESHOLD,
                       "logz_threshold": vs.LOGZ_THRESHOLD,
                       "horizons": list(vs.HORIZONS),
                       "primary_horizon": vs.PRIMARY_HORIZON,
                       "cooldown_min": vs.COOLDOWN_MIN, "block_min": vs.BLOCK_MIN,
                       "n_perm": vs.N_PERM, "n_boot": vs.N_BOOT},
        "input_fingerprints": fps,
        "sample_counts": counts,
        "outputs": ["out/volshock/cells.csv", "out/volshock/thirds.csv",
                    "out/volshock/intensity.csv"],
        "note": "DISCOVERY ONLY. Not a validated finding. No P&L, no execution.",
    }, indent=2))
    print("wrote", vs.OUT / "run.json")


if __name__ == "__main__":
    main()
