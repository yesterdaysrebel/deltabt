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
    """Extract the `paths:` list from deploy.yml's push trigger.

    Hand-parsed rather than handed to PyYAML, which is not in the test
    environment. Adding it would mean editing pyproject.toml -- and
    pyproject.toml is IN the deploy allow-list, so installing a dependency to
    test the allow-list would trigger a deploy, fail on drift against the
    running experiment and restart the bot. Exactly the failure this file
    exists to prevent, caused by testing for it.

    The parse is deliberately strict: anything unrecognised raises instead of
    returning a short list, because a silently-empty result would make every
    assertion below pass vacuously.
    """
    lines = WORKFLOW.read_text().splitlines()
    try:
        i = next(n for n, l in enumerate(lines) if l.rstrip() == "  push:")
    except StopIteration:                                   # pragma: no cover
        raise AssertionError("deploy.yml has no `push:` trigger")

    key = None
    for n in range(i + 1, len(lines)):
        line = lines[n]
        if line.strip() and not line.startswith("    "):    # left the block
            break
        stripped = line.strip()
        if stripped in ("paths:", "paths-ignore:"):
            key = stripped[:-1]
            i = n
            break
    assert key == "paths", (
        "deploy.yml is back on a deny-list (or has no path filter at all). A "
        "deny-list is unbounded over a repository: every directory nobody "
        "thought of triggers a roll, and a roll against a running experiment "
        "fails on drift and restarts the bot twice.")

    out: list[str] = []
    for line in lines[i + 1:]:
        if not line.strip() or line.strip().startswith("#"):
            continue
        if not line.startswith("      - "):                 # dedented out
            break
        out.append(line.strip()[2:].strip().strip("'\""))
    assert out, "parsed an empty `paths:` list -- every check below would pass vacuously"
    return out


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

    #: The one path outside the image that MUST roll, and why.
    #:
    #: `infra/terraform/variables.tf` holds the universe, the variant and the
    #: risk knobs -- the inputs the experiment's config_hash is computed from.
    #: Changing one replaces the host with a NEW identity, and the other half
    #: of that change (retire the running experiment, register a successor)
    #: lives in the deploy workflow. Without a roll the new host meets a
    #: database holding a RUNNING experiment whose hash no longer matches,
    #: bind_experiment refuses it, and the bot is live and unbound with
    #: nothing red to say so. SOLUSD sat merged-but-unapplied for an hour on
    #: 2026-09-04 for exactly this reason.
    #:
    #: The rest of infra/ stays excluded: a subnet or an alarm changing does
    #: not move the experiment's identity.
    IDENTITY_EXCEPTIONS = {"infra/terraform/variables.tf"}

    @pytest.mark.parametrize("never", ["tests/", "scripts/", "out/", "reports/",
                                       "docs/", "infra/", ".github/"])
    def test_paths_outside_the_image_are_not_triggers(self, never):
        root = never.rstrip("/")
        for p in trigger_paths():
            if p.startswith("!") or p in self.IDENTITY_EXCEPTIONS:
                continue
            assert not (p == root or p.startswith(root + "/")), (
                f"{never} is not copied into the image, so a change there "
                f"cannot alter what the bot runs -- but it would roll a live "
                f"experiment and fail on drift.")

    def test_the_identity_exception_is_exactly_one_file(self):
        """A narrow exception, not a reopened door to all of infra/."""
        infra_triggers = {p for p in trigger_paths()
                          if not p.startswith("!") and p.startswith("infra/")}
        assert infra_triggers == self.IDENTITY_EXCEPTIONS, (
            f"only {self.IDENTITY_EXCEPTIONS} may trigger a roll from infra/; "
            f"found {infra_triggers}")

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
