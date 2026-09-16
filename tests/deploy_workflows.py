"""The deploy pipeline's files, found rather than named.

WHY THIS EXISTS. `deploy.yml` was split into deploy-paper.yml,
deploy-testnet.yml, deploy-mainnet.yml and the shared _roll.yml on 2026-09-16.
Every test that read the pipeline read it BY NAME, so the split would have
turned each of them into a FileNotFoundError at best and, had they used a glob,
into a test that passes because it found nothing at worst.

That second failure is the one this module is shaped against. This repository
has already shipped six broken links behind a green suite
(tests/live/test_deployment_safety.py records it), and the common thread is
assertions that can be satisfied by absence. So `paths()` REFUSES to return an
empty set, and refuses a set missing the roll sequence: a rename that loses a
workflow fails here loudly instead of quietly making the pipeline untested.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"

#: The reusable workflow holding the roll sequence. Not a deploy entry point of
#: its own -- it has no triggers -- but it is where the steps actually live, so
#: a test asking "what does a deploy do?" must read it.
ROLL = WORKFLOWS / "_roll.yml"


def paths() -> list[pathlib.Path]:
    """Every file the deploy pipeline is made of, roll sequence included."""
    found = sorted(WORKFLOWS.glob("deploy-*.yml"))
    if not found:
        raise AssertionError(
            f"no deploy-*.yml under {WORKFLOWS}. Either the pipeline was "
            f"renamed and this helper was not, or it is gone. Every test "
            f"using this would otherwise pass by finding nothing.")
    if not ROLL.exists():
        raise AssertionError(
            f"{ROLL} is missing. The callers are thin; the guard, the retire "
            f"and the successor registration all live in it, so a test that "
            f"reads only the callers asserts nothing about what a deploy does.")
    return found + [ROLL]


def text() -> str:
    """Every deploy file concatenated, for substring assertions."""
    return "\n".join(p.read_text() for p in paths())


def entry_points() -> list[pathlib.Path]:
    """Just the callers -- the files with triggers. Excludes _roll.yml."""
    return [p for p in paths() if p != ROLL]


def named(stem: str) -> pathlib.Path:
    """One caller by stem, e.g. 'deploy-paper'. Fails if it is not there."""
    hit = [p for p in paths() if p.stem == stem]
    if not hit:
        raise AssertionError(
            f"no {stem}.yml; have {[p.stem for p in paths()]}")
    return hit[0]
