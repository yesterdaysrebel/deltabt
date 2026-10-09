"""Can the candle ML lab see a weak signal at all? A planted-signal check (2026-10-09).

    PYTHONPATH=. <python with lightgbm> scripts/entry_filter_ml_planted.py

WHY. scripts/entry_filter_ml_lab.py found AUC 0.490. A null from a pipeline
that cannot see anything is not evidence. This runs the lab's own feature code
and walk-forward unchanged (exec of the lab's source up to its evaluation
section), adds one feature = strength * label + N(0,1) (rng seed 0), and reports
the walk-forward AUC (no fee inputs, seeds 11/22/33) next to the feature's own.

RESULT 2026-10-09 (out/sweep/five_min_arm_lab/symbol_screen/entry_filter_ml_planted_2026-10-09.txt):
strength 0 -> 0.502 alone / 0.494 model; 0.25 -> 0.576 / 0.534; 0.5 -> 0.614 /
0.591. The pipeline sees a weak signal; the 0.490 null is not a blind lab.
"""
import pathlib, sys
import numpy as np
from sklearn.metrics import roc_auc_score

src = pathlib.Path("scripts/entry_filter_ml_lab.py").read_text()
g = {"__name__": "lab"}; sys.argv = ["lab", "out/sweep/five_min_arm_lab/symbol_screen"]
exec(compile(src[:src.index("\ntest = np.isin")], "candle_lab_prefix", "exec"), g)
X, y, T, MONTHS, month, mstart, NOFEE, fit_score = (g[k] for k in ("X", "y", "T", "MONTHS", "month", "mstart", "NOFEE", "fit_score"))
rng = np.random.default_rng(0)
for strength in (0.0, 0.25, 0.5):
    X["plant"] = strength * y + rng.normal(size=len(y))
    cols = NOFEE + ["plant"]; alone, model = [], []
    for m in MONTHS:
        tr = (T.exit_baseline < mstart[m]).to_numpy(); te = month == m
        s = np.mean([fit_score(cols, tr, te, sd)[0] for sd in (11, 22, 33)], axis=0)
        model.append(roc_auc_score(y[te], s)); alone.append(roc_auc_score(y[te], X.plant[te]))
    print(f"plant strength {strength}: planted feature alone AUC {np.mean(alone):.3f}; walk-forward model AUC {np.mean(model):.3f}")
