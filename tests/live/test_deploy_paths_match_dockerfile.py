"""The deploy trigger must list exactly what the image contains.

deploy.yml decides whether to roll the bot from a `paths:` allow-list. It used
to be a deny-list, and a deny-list over a repository has no edge: every
directory nobody thought of defaulted to triggering a roll, and a roll against
a RUNNING experiment fails on drift, restarts the container twice and goes
red. That happened three times -- a research CSV on 2026-08-18, then scripts/
and tests/ on 2026-08-20.

An allow-list has the opposite failure: a path that IS in the image but is
missing from the list means the host quietly keeps running stale code, which
surfaces weeks later as a git_sha in a report that nobody expected. That is
worse than a red build, so it gets a test.

The Dockerfile is the authority. Every COPY source in it must be covered by an
entry here, or be named as a deliberate exception with a reason.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "deploy" / "docker" / "Dockerfile"
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"

#: Copied into the image, deliberately NOT a deploy trigger. Changing it cannot
#: change what the container does, and rolling a live experiment to ship a
#: paragraph is exactly what the filter exists to prevent.
DELIBERATE_EXCEPTIONS = {"README.md"}


def copy_sources() -> set[str]:
    """Every local path the Dockerfile copies into the image."""
    out: set[str] = set()
    for line in DOCKERFILE.read_text().splitlines():
        line = line.strip()
        if not line.upper().startswith("COPY "):
            continue
        # `COPY --from=<stage>` moves build artefacts between stages; its
        # source is inside the builder, not in this repository.
        if "--from=" in line:
            continue
        parts = re.sub(r"^COPY\s+", "", line, flags=re.I).split()
        out.update(parts[:-1])          # the last token is the destination
    return out


def trigger_paths() -> list[str]:
    doc = yaml.safe_load(WORKFLOW.read_text())
    push = doc[True]["push"]            # PyYAML parses the key `on` as True
    assert "paths-ignore" not in push, (
        "deploy.yml is back on a deny-list. It is unbounded over a repository: "
        "every directory nobody thought of triggers a roll, and a roll against "
        "a running experiment fails on drift and restarts the bot twice.")
    return push["paths"]


class TestDeployTriggerCoversTheImage:
    def test_every_copied_path_is_a_trigger(self):
        patterns = [p for p in trigger_paths() if not p.startswith("!")]
        missing = []
        for src in sorted(copy_sources()):
            if src in DELIBERATE_EXCEPTIONS:
                continue
            root = src.rstrip("/").split("/")[0]
            if not any(p == src or p.startswith(root + "/") or p == root
                       for p in patterns):
                missing.append(src)
        assert not missing, (
            f"the Dockerfile copies {missing} into the image, but deploy.yml "
            f"would not roll on a change to it -- the host would keep running "
            f"stale code and only a git_sha in some later report would say so. "
            f"Add it to `paths:`, or to DELIBERATE_EXCEPTIONS with a reason.")

    def test_the_dockerfile_itself_triggers(self):
        # Changing HOW the image is built changes the image as surely as
        # changing what goes into it.
        assert any(p.startswith("deploy/docker") for p in trigger_paths())

    @pytest.mark.parametrize("never", ["tests/", "scripts/", "out/", "reports/",
                                       "docs/", "infra/", ".github/"])
    def test_paths_outside_the_image_are_not_triggers(self, never):
        root = never.rstrip("/")
        for p in trigger_paths():
            if p.startswith("!"):
                continue
            assert not (p == root or p.startswith(root + "/")), (
                f"{never} is not copied into the image, so a change there "
                f"cannot alter what the bot runs -- but it would roll a live "
                f"experiment and fail on drift.")

    def test_research_runners_are_excluded(self):
        # They ship, since deltabt/ is copied whole, but nothing under app/
        # imports a run_*.py. Five arrived in one day of research.
        assert any(p == "!deltabt/research/run_*.py" for p in trigger_paths())

    def test_the_modules_app_actually_imports_are_not_excluded(self):
        # app/strategy/rules.py imports _leg_extreme from research.hwpr and
        # frozen_hwpr calls build_conditions and arm_signals directly, so a
        # change there changes what the bot computes and MUST roll.
        negations = [p[1:] for p in trigger_paths() if p.startswith("!")]
        for pattern in negations:
            assert not pattern.endswith("hwpr.py"), (
                f"{pattern} excludes a module app/ imports at runtime")
            assert pattern != "deltabt/research/**", (
                "excluding all of deltabt/research would stop hwpr.py changes "
                "from ever reaching the host")
