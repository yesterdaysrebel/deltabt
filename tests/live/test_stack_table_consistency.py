"""One stack, three hand-written tables, and they must agree.

WHAT THIS PINS
    deploy.yml says it out loud: "adding or removing a stack is THREE edits --
    variables.tf, here, and monitor.yml". Nothing checked that the three agreed,
    and each disagreement fails differently:

    * variables.tf is the ONLY one that decides what runs. DELTABOT_VARIANT is
      written from it into user_data. The other two are labels and pins.
    * deploy.yml's `variant` is a job title. Wrong, it announces a strategy the
      host is not running, which is how "stack v3 runs variant V4" survived long
      enough to be written into three files.
    * monitor.yml's `strategy_hash` is a DRIFT PIN. Wrong, the daily report
      declares a correct deployment drifted, every day, until someone stops
      reading it -- and a report that is always red is worse than no report.

    On 2026-08-27 the stack moved from the ATR arm to `SPEC:atr_banded@5` and
    all three had to change together, including a hash that goes from 16 chars
    to 64 because a spec arm reports `spec.config_hash` in full while
    AtrArmConfig truncated its own.

WHAT IS NOT ASSERTED
    That the variant is the RIGHT one. Only that the three files name the same
    one, and that a pinned hash is the hash that variant actually produces.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
VARIABLES = ROOT / "infra/terraform/variables.tf"
DEPLOY = ROOT / ".github/workflows/deploy.yml"
MONITOR = ROOT / ".github/workflows/monitor.yml"


def _terraform_stacks() -> dict[str, dict]:
    """The stacks map default -- the only place that decides what runs."""
    text = VARIABLES.read_text()
    start = text.index('variable "stacks"')
    block = text[start: text.index("\nvariable ", start + 10)]
    out = {}
    for name, body in re.findall(r"^\s{4}(\w+)\s*=\s*\{([^}]*)\}", block, re.M):
        fields = dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', body))
        out[name] = fields
    return out


def _deploy_table() -> dict[str, dict]:
    m = re.search(r"all='(\[.*?\])'", DEPLOY.read_text(), re.S)
    assert m, "deploy.yml no longer has an `all=' stack table"
    return {r["stack"]: r for r in json.loads(m.group(1))}


def _monitor_matrix() -> dict[str, dict]:
    text = MONITOR.read_text()
    out = {}
    for chunk in re.split(r"\n\s+- stack:", text)[1:]:
        name = chunk.splitlines()[0].strip()
        fields = dict(re.findall(r"^\s+(\w+):\s*\"?([^\"\n]+)\"?\s*$",
                                 chunk, re.M))
        out[name] = fields
    return out


def test_the_three_tables_name_the_same_stacks():
    tf, dep, mon = _terraform_stacks(), _deploy_table(), _monitor_matrix()
    assert set(tf) == set(dep), (
        f"variables.tf has {sorted(tf)} but deploy.yml has {sorted(dep)}")
    assert set(tf) == set(mon), (
        f"variables.tf has {sorted(tf)} but monitor.yml has {sorted(mon)}")


@pytest.mark.parametrize("table", ["deploy", "monitor"])
def test_the_variant_label_matches_what_actually_runs(table):
    tf = _terraform_stacks()
    other = _deploy_table() if table == "deploy" else _monitor_matrix()
    for stack, fields in tf.items():
        want = fields["variant"]
        got = other[stack]["variant"].strip().strip('"')
        assert got == want, (
            f"{table}.yml calls stack '{stack}' variant '{got}' but "
            f"variables.tf deploys '{want}'. variables.tf is the one that "
            f"decides; the label is what a human reads.")


def test_a_spec_variant_pins_the_hash_that_variant_produces():
    """The drift pin must be the hash the deployed spec actually reports."""
    from deltabt.catalog import build_spec

    mon = _monitor_matrix()
    checked = 0
    for stack, fields in _terraform_stacks().items():
        variant = fields["variant"]
        if not variant.upper().startswith("SPEC:"):
            continue
        family, _, minutes = variant[5:].partition("@")
        spec = build_spec(family, int(minutes))
        pinned = mon[stack]["strategy_hash"].strip().strip('"')
        assert pinned == spec.config_hash, (
            f"monitor.yml pins strategy_hash={pinned[:20]}... for stack "
            f"'{stack}', but {variant} reports "
            f"{spec.config_hash[:20]}.... The daily report would call every "
            f"correct deployment drifted.")
        assert len(pinned) == 64, (
            f"a spec arm reports the FULL sha256 (app/strategy/spec_arm.py "
            f"sets strategy_config_hash=spec.config_hash); {len(pinned)} "
            f"characters can never match it")
        checked += 1
    assert checked, "no SPEC: stack found; this test would pass vacuously"


def test_every_stack_names_a_database_and_a_log_group():
    tf, mon = _terraform_stacks(), _monitor_matrix()
    for stack, fields in tf.items():
        assert fields.get("db_name"), f"stack '{stack}' has no db_name"
        lg = mon[stack].get("log_group", "")
        assert stack in lg, (
            f"stack '{stack}' reports on log group '{lg}', which is not its "
            f"own -- the report would describe a different bot")


def test_the_catalog_family_a_spec_variant_names_exists():
    """A typo here fails closed at boot, but only after a host replacement."""
    from deltabt.catalog import FAMILIES

    for stack, fields in _terraform_stacks().items():
        variant = fields["variant"]
        if not variant.upper().startswith("SPEC:"):
            continue
        family, sep, minutes = variant[5:].partition("@")
        assert sep and minutes.isdigit(), (
            f"stack '{stack}' variant '{variant}' is not SPEC:<family>@<mins>")
        assert family in FAMILIES, (
            f"stack '{stack}' deploys catalog family '{family}', which is not "
            f"in deltabt/catalog.py. resolve_strategy fails closed, so the "
            f"host would boot and never trade.")
