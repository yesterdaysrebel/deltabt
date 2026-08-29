"""Shared guards for tests that read the LIVE recorder tree.

WHY THIS EXISTS
    `data/` is gitignored (.gitignore line 12), so it does not exist on a
    clean checkout. Several research tests assert against the real recorded
    cache -- that is deliberate and valuable: a fixture-only test cannot catch
    a products.json whose contract values silently changed, or an option
    symbol the parser stopped handling.

    But a test that reads a gitignored directory with no guard does not FAIL
    honestly in CI, it fails with FileNotFoundError before it can assert
    anything. On 2026-08-29 that put thirty such failures onto master and
    blocked the deploy gate, because the branch carrying them had never been
    pushed and CI had never seen them.

WHAT THIS DOES NOT DO
    It does not weaken a single assertion. When the data IS present -- which
    is every local run, and any runner given the cache -- these tests execute
    exactly as before. The guard only distinguishes "cannot run here" from
    "ran and found a problem", which are two different results that were
    previously indistinguishable.
"""

from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def require_live_data(*relative: str) -> None:
    """Skip, with a reason, unless every named path exists under the repo root.

    Call it from a fixture or at the top of a test. The reason names the
    missing path so a skipped run says WHY rather than merely how many.
    """
    missing = [r for r in relative if not (ROOT / r).exists()]
    if missing:
        pytest.skip(
            "needs the live recorder tree, which is gitignored and absent "
            f"here: missing {', '.join(missing)}")


def live_data_present(*relative: str) -> bool:
    return all((ROOT / r).exists() for r in relative)
