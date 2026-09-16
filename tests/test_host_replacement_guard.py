"""A merge must not replace a bot host silently, or at all while it runs an
experiment.

WHAT HAPPENED. 2026-09-16: #76 and #77 each replaced tnet. Termination
protection was on and did not stop it; tf_guard.py allows aws_instance
replacement on purpose. Both plans said `must be replaced` on their pull
requests, in text nobody read to the end, and the PR description claimed the
opposite. See scripts/host_replacements.py.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/host_replacements.py"
WORKFLOW = (ROOT / ".github/workflows/infrastructure.yml").read_text()
CLI = (ROOT / "app/cli.py").read_text()

sys.path.insert(0, str(ROOT / "scripts"))
import host_replacements as hr  # noqa: E402


def rc(address, *actions):
    return {"address": address, "change": {"actions": list(actions)}}


PLAN_76 = {"resource_changes": [       # what #76's plan actually contained
    rc('aws_instance.bot["tnet"]', "delete", "create"),
    rc('aws_eip.bot["tnet"]', "update"),
    rc('aws_iam_role.github_app_deploy', "update"),
    rc('aws_db_instance.main', "update"),
]}


# --- reading the plan ------------------------------------------------------

def test_a_replaced_bot_host_is_found():
    assert hr.replaced_stacks(PLAN_76) == ["tnet"]


@pytest.mark.parametrize("change", [
    rc('aws_instance.bot["atr"]', "update"),         # in place: harmless
    rc('aws_instance.bot["atr"]', "create"),         # a new stack
    rc('aws_instance.bot["atr"]', "no-op"),
    rc('aws_instance.other["atr"]', "delete", "create"),   # not a bot
    rc('aws_eip.bot["atr"]', "delete", "create"),
])
def test_other_changes_are_not_host_replacements(change):
    assert hr.replaced_stacks({"resource_changes": [change]}) == []


def test_create_before_destroy_ordering_is_still_a_replacement():
    plan = {"resource_changes": [rc('aws_instance.bot["ltp"]', "create", "delete")]}
    assert hr.replaced_stacks(plan) == ["ltp"]


def test_several_hosts_are_all_reported():
    plan = {"resource_changes": [
        rc('aws_instance.bot["atr"]', "delete", "create"),
        rc('aws_instance.bot["ladder"]', "delete", "create")]}
    assert hr.replaced_stacks(plan) == ["atr", "ladder"]


# --- the pull request must name each host ----------------------------------

@pytest.mark.parametrize("body,expected", [
    ("Replaces-Host: tnet", {"tnet"}),
    ("text\nreplaces-host:  TNET \nmore", {"tnet"}),
    ("Replaces-Host: tnet, atr", {"tnet", "atr"}),
    ("Replaces-Host: tnet\nReplaces-Host: ladder", {"tnet", "ladder"}),
    ("this PR replaces the tnet host", set()),          # prose is not a claim
    ("", set()),
])
def test_announcements_are_parsed(body, expected):
    assert hr.announced_stacks(body) == expected


def _run(plan, body, tmp_path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(p), "--check-announced", "BODY",
         "--report", str(tmp_path / "r.md")],
        capture_output=True, text=True, env={"BODY": body, "PATH": "/usr/bin:/bin"})


def test_an_unannounced_replacement_fails(tmp_path):
    r = _run(PLAN_76, "Rename mainnet to prod.", tmp_path)
    assert r.returncode == 1, r.stdout
    assert "tnet" in (tmp_path / "r.md").read_text()


def test_an_announced_replacement_passes_and_is_still_reported(tmp_path):
    r = _run(PLAN_76, "...\nReplaces-Host: tnet\n", tmp_path)
    assert r.returncode == 0, r.stdout
    assert "REPLACES OR REMOVES BOT HOSTS" in (tmp_path / "r.md").read_text()


def test_announcing_one_host_does_not_cover_another(tmp_path):
    """The reason it is names and not a switch."""
    plan = {"resource_changes": [
        rc('aws_instance.bot["tnet"]', "delete", "create"),
        rc('aws_instance.bot["atr"]', "delete", "create")]}
    r = _run(plan, "Replaces-Host: tnet", tmp_path)
    assert r.returncode == 1
    assert "atr" in r.stdout


def test_a_plan_with_no_replacement_passes_silently(tmp_path):
    r = _run({"resource_changes": [rc('aws_db_instance.main', "update")]}, "", tmp_path)
    assert r.returncode == 0
    assert (tmp_path / "r.md").read_text() == ""


# --- the workflow wiring ---------------------------------------------------

def _step(name: str) -> str:
    i = WORKFLOW.index(f"- name: {name}")
    j = WORKFLOW.find("\n      - name:", i + 1)
    return WORKFLOW[i:j if j != -1 else len(WORKFLOW)]


def test_the_pr_body_is_never_interpolated_into_a_shell_command():
    """A PR body is attacker-controlled. It must reach the script through the
    environment; `${{ ... body }}` inside `run:` is a command injection."""
    for m in re.finditer(r"\$\{\{\s*github\.event\.pull_request\.body\s*\}\}", WORKFLOW):
        line = WORKFLOW[WORKFLOW.rfind("\n", 0, m.start()) + 1:WORKFLOW.find("\n", m.end())]
        assert re.match(r"\s+[A-Z_]+:\s", line), (
            f"the PR body is used outside an env: mapping: {line.strip()}")


def test_on_a_pr_the_replacement_is_reported_before_the_plan_is_posted():
    detect = WORKFLOW.index("- name: find bot hosts this plan replaces")
    post = WORKFLOW.index("- name: post the plan on the pull request")
    fail = WORKFLOW.index("- name: a pull request that replaces a host must name it")
    assert detect < post < fail, (
        "the check must detect before posting, and fail only AFTER the plan is "
        "posted -- failing first would hide the plan the reviewer needs")
    assert "head +" in _step("post the plan on the pull request"), (
        "the replacement is not placed at the top of the posted plan")


def test_the_apply_asks_the_host_before_applying():
    guard = WORKFLOW.index("- name: refuse to replace a host that is running an experiment")
    apply = WORKFLOW.index("- name: apply the reviewed plan, not a fresh one")
    assert guard < apply, "the experiment check runs after the apply it guards"
    step = _step("refuse to replace a host that is running an experiment")
    assert "if: needs.plan.outputs.changes == '2'" in step
    assert "deltabt-paper-$stack-experiment" in step
    assert '"Action=status"' in step


def test_the_apply_check_matches_the_clis_own_words():
    """Matched exactly as the deploy guard matches them, and pinned to the CLI,
    so a reworded message cannot turn every host into 'running'."""
    step = _step("refuse to replace a host that is running an experiment")
    assert '*"no experiment is RUNNING"*' in step
    assert "no experiment is RUNNING" in CLI


def test_the_apply_check_fails_closed():
    step = _step("refuse to replace a host that is running an experiment")
    assert "could not say whether it is running an experiment" in step
    assert "exit 1" in step
    # A missing host is the one "cannot ask" that is safe.
    assert "no host exists; nothing to protect" in step


def test_replace_hosts_names_stacks_and_only_a_dispatch_sets_it():
    dispatch = WORKFLOW[WORKFLOW.index("workflow_dispatch:"):WORKFLOW.index("\npermissions:")]
    assert "replace_hosts:" in dispatch
    assert "type: string" in dispatch[dispatch.index("replace_hosts:"):]
    step = _step("refuse to replace a host that is running an experiment")
    assert "github.event.inputs.replace_hosts" in step


def test_removing_a_host_is_reported_like_replacing_it():
    """Taking a stack out of the map destroys its host; the experiment ends
    either way."""
    plan = {"resource_changes": [rc('aws_instance.bot["ladder"]', "delete")]}
    assert hr.replaced_stacks(plan) == ["ladder"]


def test_mentioning_the_host_in_prose_is_not_announcing_it(tmp_path):
    """#76's description talked about tnet at length and still did not say
    its host would be replaced. Only an explicit line counts."""
    r = _run(PLAN_76, "This renames the venue on the tnet host and nothing is replaced.",
             tmp_path)
    assert r.returncode == 1, r.stdout
