"""A change to the experiment's IDENTITY must trigger a deploy.

The deploy filter is an allow-list of "things in the image", which is the right
rule for code. It is the wrong rule for `infra/terraform/variables.tf`: that
file holds the universe, the variant and the risk knobs, and the experiment's
config_hash is computed from exactly those. Change one and Terraform replaces
the host with a new identity -- but if no deploy runs, no successor experiment
is registered, and the new host meets a database holding a RUNNING experiment
whose hash no longer matches. bind_experiment refuses it and the bot is live
and unbound, with nothing red anywhere to say so.

On 2026-09-04 SOLUSD was merged and then stranded: the infrastructure run that
would have applied it failed, and the follow-up merge touched no path either
workflow watched, so nothing carried it. This test exists so the coupling is
asserted rather than remembered.
"""
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEPLOY = ROOT / ".github/workflows/deploy.yml"
VARIABLES = "infra/terraform/variables.tf"


def _paths() -> list[str]:
    wf = yaml.safe_load(DEPLOY.read_text())
    # `on` is parsed as the boolean True by YAML 1.1, which is a trap worth
    # naming: `wf["on"]` is a KeyError and `wf[True]` is the block.
    trigger = wf.get("on", wf.get(True))
    return trigger["push"]["paths"]


def test_the_identity_file_triggers_a_deploy():
    assert VARIABLES in _paths(), (
        f"{VARIABLES} must trigger a deploy: it holds the universe, variant "
        f"and risk settings the experiment's config_hash is computed from, so "
        f"changing it without registering a successor leaves the bot unable "
        f"to bind")


def test_the_identity_file_really_holds_the_identity():
    """If these move elsewhere, the path above has to move with them."""
    text = (ROOT / VARIABLES).read_text()
    for knob in ("bot_symbols", "stacks", "minimum_rr", "max_open_positions"):
        assert f'variable "{knob}"' in text, knob


def test_the_image_paths_are_still_an_allow_list():
    """The fix must not turn the filter back into a deny-list."""
    paths = _paths()
    assert "app/**" in paths and "deltabt/**" in paths
    assert not any(p.startswith("!") and "variables" in p for p in paths)
    # Nothing broad enough to roll the bot for a workflow or a test edit.
    for p in paths:
        assert not p.startswith("tests/"), p
        assert not p.startswith(".github/"), p
