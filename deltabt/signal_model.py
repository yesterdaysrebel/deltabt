"""The signal model and its grade -- shared by the signal lab and the weekly grader.

Fixed 2026-10-13 in scripts/signal_features_lab.py before any result was
read; moved here unchanged so `python -m live grade-model` grades exactly the
model the lab tested. GRADING ONLY: nothing in the trading path imports this
module (tests/live_exec/test_model_never_trades.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

#: Columns of a signals frame that are labels or keys, not model inputs.
NON_INPUTS = ("symbol", "entry_time", "R", "win", "exit_reason")


def features(df: pd.DataFrame) -> pd.DataFrame:
    """The model's inputs: everything but the labels and keys (side included)."""
    return df.drop(columns=[c for c in NON_INPUTS if c in df.columns])


def model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                          min_samples_leaf=50, l2_regularization=1.0,
                                          random_state=0)


def top_vs_random(R, score, frac=0.3, n=2000, seed=0):
    """(mean R of the top `frac` by score, share of random subsets of the same
    size whose mean R is at least that, subset size)."""
    k = max(1, int(round(frac * len(R))))
    top = R[np.argsort(-score)[:k]].mean()
    rng = np.random.default_rng(seed)
    rand = np.array([R[rng.choice(len(R), k, replace=False)].mean() for _ in range(n)])
    return top, float((rand >= top).mean()), k
