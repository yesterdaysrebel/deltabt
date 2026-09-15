"""The paper bot must not be able to reach `live/`, and must stay paper-only.

`tests/live/test_no_live_trading.py` asserts that `app/` and `deltabt/` contain
no order-placement capability. Adding `live/` to this repository does not
weaken that claim, and these tests are what keeps it true:

  * `app/` and `deltabt/` never import `live`
  * the shipped Docker image does not contain `live/`
  * the installed package set does not contain `live`

If any of those drift, the paper bot's boundary becomes a promise instead of a
property, and the whole point of `app/safety.py` is that it is a property.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAPER_PACKAGES = [ROOT / "app", ROOT / "deltabt"]


def _py_files(roots):
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def test_paper_packages_never_import_live():
    """The single most important assertion in this file.

    If `app/` imports `live`, the paper process gains order-placement
    capability and every guarantee in app/safety.py becomes false.
    """
    offenders = []
    for path in _py_files(PAPER_PACKAGES):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name == "live" or name.startswith("live."):
                    rel = path.relative_to(ROOT)
                    offenders.append(f"{rel}:{node.lineno} imports {name}")
    assert not offenders, (
        "the paper bot must never import the live execution package:\n  "
        + "\n  ".join(offenders))


def test_the_paper_image_does_not_contain_live():
    """The image is the artifact that actually runs. It must not carry `live/`."""
    dockerfile = (ROOT / "deploy" / "docker" / "Dockerfile").read_text()
    copied = [ln.strip() for ln in dockerfile.splitlines()
              if ln.strip().startswith("COPY")]
    bad = [ln for ln in copied if " live" in f" {ln} " or "/live" in ln]
    assert not bad, f"Dockerfile copies the live package into the bot image: {bad}"
    assert 'ENTRYPOINT ["python", "-m", "app"]' in dockerfile


def test_the_live_image_is_a_SEPARATE_one_that_does_contain_live():
    """Two images, not one image with a flag.

    Adding `live` to the paper image would make app/safety.py's boundary a
    promise again instead of a property. This asserts the separation is real
    and deliberate, so 'simplifying' the two Dockerfiles into one fails here
    rather than silently arming the paper bot.
    """
    live_df = ROOT / "deploy" / "docker" / "Dockerfile.live"
    assert live_df.exists(), "the live image has no Dockerfile"
    text = live_df.read_text()
    assert "COPY live ./live" in text, "the live image does not carry live/"
    assert 'ENTRYPOINT ["python", "-m", "live"]' in text
    # And it must not be the same file as the paper one.
    assert text != (ROOT / "deploy" / "docker" / "Dockerfile").read_text()


def test_credentials_are_not_baked_into_the_live_image():
    """They arrive at run time, like the database DSN, and are never layered."""
    text = (ROOT / "deploy" / "docker" / "Dockerfile.live").read_text()
    for name in ("DELTA_API_KEY", "DELTA_API_SECRET"):
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("ENV", "ARG")) and name in stripped:
                raise AssertionError(
                    f"{name} is baked into the live image at {stripped!r}")


def test_packaging_does_not_install_live():
    """`pip install -e .` must not put `live` on the bot's import path."""
    pyproject = (ROOT / "pyproject.toml").read_text()
    include_line = next(
        (ln for ln in pyproject.splitlines() if ln.strip().startswith("include")),
        "")
    assert "live" not in include_line, (
        f"packages.find includes the live package: {include_line!r}")


def test_live_package_is_where_we_think_it_is():
    """A negative control: the tests above would pass vacuously if `live/`
    were not there at all, or had been moved under `app/`."""
    assert (ROOT / "live" / "client.py").exists(), "live/ package is missing"
    assert not (ROOT / "app" / "live").exists(), (
        "live execution code has been moved INSIDE app/, which puts it in the "
        "paper bot's image and inside the safety scan")


def test_existing_paper_boundary_still_holds():
    """Re-run the shipped boundary check so this branch cannot regress it."""
    from app.safety import (FORBIDDEN_CREDENTIAL_NAMES, FORBIDDEN_IMPORTS,
                            FORBIDDEN_ORDER_METHODS)
    hits = []
    for path in _py_files(PAPER_PACKAGES):
        if path.name == "safety.py":
            continue  # the module that DEFINES the names, skipped by name
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in FORBIDDEN_ORDER_METHODS:
                    hits.append(f"{path.name}:{node.lineno} def {node.name}")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                        else [node.module or ""])
                if any((m or "").split(".")[0] in FORBIDDEN_IMPORTS for m in mods):
                    hits.append(f"{path.name}:{node.lineno} signing import")
    assert not hits, f"paper-only boundary broken in app/ or deltabt/: {hits}"


@pytest.mark.parametrize("name", ["hmac", "requests"])
def test_live_package_does_import_what_it_needs(name):
    """The mirror image: `live/` is EXPECTED to sign and to POST. This test
    exists so nobody 'fixes' a boundary scan by widening it to cover live/."""
    source = (ROOT / "live" / "auth.py").read_text() + \
             (ROOT / "live" / "client.py").read_text()
    assert f"import {name}" in source
