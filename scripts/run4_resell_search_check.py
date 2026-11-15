"""Independent check of two settings from scripts/run4_resell_search.py, plus how much of the TEST result is noise.

    PYTHONPATH=. python scripts/run4_resell_search_check.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import run4_resell_search as S  # noqa: E402
import clean_compare as C  # noqa: E402


def independent(ev, mode, lenient):
    """Written separately. mode 'A': sell when a 1m low is 5% under the running high since the stop, stop 2.25% above
    the sell, no target, out 24 h after the stop. mode 'B': sell at the close 30 min after the stop, stop 3% above the
    highest high since the stop, take profit at a 12% fall."""
    day, sym, H, L, Cl, s = ev
    if mode == "A":
        hh, k = H[s], None
        for m in range(s + 1, 1440):
            if L[m] <= hh * 0.95:
                k, px = m, min(hh * 0.95, Cl[m])
                break
            hh = max(hh, H[m])
        if k is None:
            return None
        sell = px * 0.999
        stop = sell * 1.0225
        take = None
    else:
        k = s + 30
        if k >= 1440:
            return None
        sell = Cl[k] * 0.999
        stop = max(np.max(H[s:k + 1]) * 1.03, sell * 1.005)
        take = sell * 0.88
    loss_per_dollar = stop * 1.001 / sell - 1 + 0.0005 + 0.0005
    gain = lambda x: (sell - x) / sell - 0.0005 - 0.0005 * x / sell  # noqa: E731
    last = min(len(Cl) - 1, s + 1440)
    for m in range(k + 1, last + 1):
        if H[m] >= stop:
            return gain(stop * 1.001 if lenient else max(stop, Cl[m]) * 1.001) / loss_per_dollar
        if take is not None and L[m] <= take * 0.999:
            return gain(take) / loss_per_dollar
    return gain(Cl[last] * 1.001) / loss_per_dollar


def main():
    C.checks()
    ev = S.events()
    cfg = {"A": (("off", 0.05), ("fixed", 0.0225), None), "B": (("after", 30), ("above_high", 0.03), 0.12)}
    for mode, (w, s, t) in cfg.items():
        for le in (False, True):
            for e in ev:
                a = S.resell(e, w, s, t, le)
                b = independent(e, mode, le)
                assert (a is None) == (b is None) and (a is None or abs(a[0] - b) < 1e-4), (e[0], e[1], a, b)
    print("CHECK PASSED: an independent implementation gives the same R (to 0.0001 R) on every re-sell for both settings.\n")
    rng = np.random.default_rng(0)
    for mode, (w, s, t) in cfg.items():
        print(f"{S.name(w, s, t).strip()}  ({'TRAIN-BEST' if mode == 'A' else 'a good TRAIN setting'}), STRICT:")
        for nm, lo_, hi_ in (("2025 H1", "2025-01-01", "2025-07-01"), ("2025 H2", "2025-07-01", "2026-01-01"),
                             ("2026 H1", "2026-01-01", "2026-07-01"), ("2026 H2 + real", "2026-07-01", "2027-01-01")):
            R = np.array([r[0] for e in ev if pd.Timestamp(lo_) <= e[0] < pd.Timestamp(hi_) for r in [S.resell(e, w, s, t, False)] if r])
            print(f"   {nm:15s} n {len(R):3d}  avg {R.mean():+.2f} R  win {(R > 0).mean():.0%}  total {R.sum():+6.1f} R")
        R = np.array([r[0] for e in ev if e[0] >= pd.Timestamp("2026-01-01") for r in [S.resell(e, w, s, t, False)] if r])
        boots = np.array([rng.choice(R, len(R)).mean() for _ in range(10000)])
        print(f"   TEST avg {R.mean():+.2f} R, 95% range {np.percentile(boots, 2.5):+.2f} .. {np.percentile(boots, 97.5):+.2f} R; "
              f"chance the true average is <= 0: {(boots <= 0).mean():.0%}\n")


if __name__ == "__main__":
    main()
