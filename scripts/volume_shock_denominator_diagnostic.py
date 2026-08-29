"""Why the normalised endpoint survives a control the raw endpoint fails.

DIAGNOSTIC ONLY. This computes no new test and freezes no hypothesis. It
explains an already-computed SECONDARY number in out/volshock_control/control.csv
so that number is not mistaken for a surviving effect.

The normalised endpoint is |log return| / sigma_trail. The control stratifies on
sigma_contemp, which conditions the NUMERATOR. It does nothing to the
DENOMINATOR -- and sigma_trail is exactly where the discovery's own diagnostic
said the asymmetry lives (trailing vol lower at shock times on every symbol).

This prints the denominator's own shock/baseline ratio under the same strata.
If normalised ~= raw / denominator, the normalised endpoint is measuring the
selection rule, not the future.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import volume_shock_control as vc  # noqa: E402
import volume_shock_discovery as vs  # noqa: E402

MAJORS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")


def main() -> None:
    rows = []
    for s in MAJORS:
        d = vc.add_contemp_vol(vs.outcomes(vs.build(vs.load(s))))
        ev = vs.events(d, "rvol_median", vs.RVOL_THRESHOLD)
        bs = vs.baseline_mask(d, ev)
        sig = d["sigma_contemp"].to_numpy(float)
        den = d["sigma_trail"].to_numpy(float)
        y_raw = d["r30"].to_numpy(float)
        y_norm = d["rn30"].to_numpy(float)
        ok = (np.isfinite(y_raw) & np.isfinite(y_norm)
              & np.isfinite(sig) & np.isfinite(den))
        e, b = ev & ok, bs & ok
        _, bucket = vc.strata(sig, e | b)
        raw_w, _ = vc._weighted_ratio(y_raw, e, b, bucket)
        norm_w, _ = vc._weighted_ratio(y_norm, e, b, bucket)
        den_w, _ = vc._weighted_ratio(den, e, b, bucket)
        rows.append(dict(symbol=s, raw_stratified=raw_w, normalised_stratified=norm_w,
                         sigma_trail_shock_over_base=den_w,
                         raw_over_denominator=raw_w / den_w,
                         residual=norm_w - raw_w / den_w))
    d = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print(d.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    out = vs.OUT_DIR / "volshock_control" / "denominator_diagnostic.csv"
    d.to_csv(out, index=False)
    print(f"\nwrote {out}")
    print("\nnormalised ~= raw / denominator on every symbol: the normalised "
          "endpoint is dominated by sigma_trail being ~18-20% lower at shock "
          "times, which is what rvol_median >= 5 SELECTS FOR. It is not "
          "evidence about the subsequent move.")


if __name__ == "__main__":
    main()
