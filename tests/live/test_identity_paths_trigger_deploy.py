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

NO PyYAML HERE, DELIBERATELY. The parser is borrowed from the sibling module
for the reason documented there: PyYAML is not in the test environment, and
adding it means editing pyproject.toml -- which is itself in the deploy
allow-list, so installing a dependency in order to test the allow-list would
trigger a deploy and restart the running experiment. The first version of this
file imported yaml and failed in CI for exactly that reason.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
VARIABLES = "infra/terraform/variables.tf"


def _paths() -> list[str]:
    """`trigger_paths()` from the sibling module, loaded by path."""
    src = pathlib.Path(__file__).with_name("test_deploy_paths_match_dockerfile.py")
    spec = importlib.util.spec_from_file_location("_deploy_paths", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    paths = mod.trigger_paths()
    assert paths, "the borrowed parser returned nothing; every assertion would pass vacuously"
    return paths


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


def test_the_committed_universe_is_what_the_bot_should_run():
    """A universe on master that no host runs is a landmine.

    SOLUSD sat here unapplied for hours: the next infrastructure apply for any
    reason would have replaced the host and restarted the experiment as a side
    effect of an unrelated change. This does not check the live host -- a test
    cannot reach it -- but it does pin the committed value, so changing the
    universe is a visible edit here rather than a silent drift.
    """
    text = (ROOT / VARIABLES).read_text()
    i = text.index('variable "bot_symbols"')
    block = text[i:text.index("\n}", i)]
    line = next(l for l in block.splitlines() if l.strip().startswith("default"))
    universe = line.split("=", 1)[1].strip().strip('"')
    assert universe == "BEATUSD,AKEUSD,BANKUSD,WIFUSD", (
        f"the committed universe is {universe!r}. If that is intended, update "
        f"this test in the same commit -- and remember it ends the running "
        f"experiment and starts a successor.")
