"""Driver for the contemporaneous-volatility control. Frozen spec, no tuning."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import volume_shock_control as vc  # noqa: E402
import volume_shock_discovery as vs  # noqa: E402

OUT = vs.OUT_DIR / "volshock_control"
OUT.mkdir(parents=True, exist_ok=True)
MAJORS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")


def main() -> None:
    prep = {}
    for s in MAJORS:
        prep[s] = vc.add_contemp_vol(vs.outcomes(vs.build(vs.load(s))))

    rows, decile_rows = [], []
    # PRIMARY: raw, h=30, all four majors (heavy stats on all of them --
    # criterion 4 requires >1.05 on 3 of 4, so all four need the full test)
    for s in MAJORS:
        r = vc.control(prep[s], vs.PRIMARY_HORIZON, "raw", heavy=True)
        rows.append({"symbol": s, "tier": "PRIMARY", **{k: v for k, v in r.items()
                                                        if k != "deciles"}})
        for d in r["deciles"]:
            decile_rows.append({"symbol": s, "endpoint": "raw",
                                "horizon": vs.PRIMARY_HORIZON, **d})
    # SECONDARY: normalised endpoint, and other horizons
    for s in MAJORS:
        r = vc.control(prep[s], vs.PRIMARY_HORIZON, "normalised", heavy=True)
        rows.append({"symbol": s, "tier": "secondary", **{k: v for k, v in r.items()
                                                          if k != "deciles"}})
        for d in r["deciles"]:
            decile_rows.append({"symbol": s, "endpoint": "normalised",
                                "horizon": vs.PRIMARY_HORIZON, **d})
    for s in ("BTCUSD", "ETHUSD"):
        for h in (15, 60):
            r = vc.control(prep[s], h, "raw", heavy=False)
            rows.append({"symbol": s, "tier": "secondary",
                         **{k: v for k, v in r.items() if k != "deciles"}})

    pd.DataFrame(rows).to_csv(OUT / "control.csv", index=False)
    pd.DataFrame(decile_rows).to_csv(OUT / "deciles.csv", index=False)

    # PRE-SPECIFIED criterion 5: chronological thirds, primary symbols
    thirds = []
    for s in ("BTCUSD", "ETHUSD"):
        d = prep[s]
        for k, idx in enumerate(np.array_split(np.arange(len(d)), 3), 1):
            sub = d.iloc[idx].reset_index(drop=True)
            r = vc.control(sub, vs.PRIMARY_HORIZON, "raw", heavy=False)
            thirds.append({"symbol": s, "third": k,
                           "start": str(pd.Timestamp(int(sub.time.iloc[0]),
                                                     unit="s", tz="UTC").date()),
                           "end": str(pd.Timestamp(int(sub.time.iloc[-1]),
                                                   unit="s", tz="UTC").date()),
                           **{kk: v for kk, v in r.items() if kk != "deciles"}})
    pd.DataFrame(thirds).to_csv(OUT / "thirds.csv", index=False)

    root = Path(__file__).resolve().parents[1]
    (OUT / "run.json").write_text(json.dumps({
        "spec": "docs/volume_shock_control_spec.md",
        "spec_sha256": hashlib.sha256(
            (root / "docs" / "volume_shock_control_spec.md").read_bytes()).hexdigest(),
        "discovery_spec_sha256": hashlib.sha256(
            (root / "docs" / "volume_shock_discovery_spec.md").read_bytes()).hexdigest(),
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                           text=True).strip(),
        "parameters": {"contemp_bars": vc.CONTEMP_BARS, "deciles": vc.N_DECILES,
                       "min_shocks_per_stratum": vc.MIN_SHOCKS_PER_STRATUM,
                       "n_boot": vc.N_BOOT, "n_perm": vc.N_PERM,
                       "inherited_rvol_threshold": vs.RVOL_THRESHOLD,
                       "inherited_lookback": vs.LOOKBACK,
                       "inherited_cooldown_min": vs.COOLDOWN_MIN,
                       "primary_horizon": vs.PRIMARY_HORIZON},
        "symbols": list(MAJORS),
        "outputs": ["out/volshock_control/control.csv",
                    "out/volshock_control/deciles.csv",
                    "out/volshock_control/thirds.csv"],
        "note": ("KILL TEST on the discovery window. No untouched historical "
                 "window exists. A negative result is dispositive; a positive "
                 "one authorises only a forward test. Not a validation."),
    }, indent=2))
    print("wrote", OUT / "run.json")


if __name__ == "__main__":
    main()
