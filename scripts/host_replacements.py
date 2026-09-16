"""Which bot hosts a Terraform plan REPLACES, and whether a PR said so.

WHY THIS EXISTS. On 2026-09-16 two merges, #76 and #77, each replaced the tnet
host -- twice in eight minutes. Both plans said `aws_instance.bot["tnet"] must
be replaced`, and both plans were posted on their pull requests. The line was
buried in a wall of plan text, and the PR description said the opposite.

Nothing else stood in the way:

  * tf_guard.py deliberately ALLOWS replacing aws_instance, because replacing a
    host is usually expected and a blanket approval flag had been approved
    reflexively (see ALLOW_REPLACE_TYPES in infrastructure.yml);
  * termination protection was on, and did not stop it -- the provider clears
    it before destroying, whatever infra/terraform/ec2.tf used to say.

This script is the data half of two checks in infrastructure.yml:

  * on a PULL REQUEST, a replacement goes to the top of the posted plan and
    fails the check unless the PR body names each host on a
    `Replaces-Host: <stack>` line. Naming the host, not flipping a switch, is
    the point: a PR announcing tnet that also replaces atr still fails;
  * before APPLY, each replaced host is asked whether it is running an
    experiment, and a running one is refused.

Usage:
  host_replacements.py PLAN_JSON --list
      print replaced stacks, one per line
  host_replacements.py PLAN_JSON --check-announced BODY_ENV_VAR [--report FILE]
      exit 1 if a replaced stack is not announced in $BODY_ENV_VAR
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

#: The Terraform resource that is a bot host. Its index key is the stack name.
BOT_ADDRESS = re.compile(r'^aws_instance\.bot\["([^"]+)"\]$')

#: `Replaces-Host: tnet` or `Replaces-Host: tnet, atr`, anywhere in the body.
ANNOUNCEMENT = re.compile(r"^\s*Replaces-Host:\s*(.+?)\s*$", re.I | re.M)


def replaced_stacks(plan: dict) -> list[str]:
    """Stacks whose bot host this plan DESTROYS -- replaced or removed.

    Both end the experiment running on it, which is the only thing this guard
    is about. A bare delete (a stack taken out of the map) is included even
    though tf_guard.py refuses to destroy an aws_instance outright today: that
    refusal is one list entry away from being widened, and this check should
    not depend on it. Mutation testing is what raised it -- the first version
    reported only delete-and-create, and nothing said whether that was a
    choice or an omission.
    """
    out = []
    for rc in plan.get("resource_changes") or []:
        m = BOT_ADDRESS.match(rc.get("address", ""))
        if not m:
            continue
        actions = set((rc.get("change") or {}).get("actions") or [])
        if "delete" in actions:
            out.append(m.group(1))
    return sorted(set(out))


def announced_stacks(body: str) -> set[str]:
    names: set[str] = set()
    for line in ANNOUNCEMENT.findall(body or ""):
        names.update(n.strip().lower() for n in line.split(",") if n.strip())
    return names


def report(replaced: list[str], unannounced: list[str]) -> str:
    if not replaced:
        return ""
    lines = [
        "### :warning: THIS PLAN REPLACES OR REMOVES BOT HOSTS",
        "",
        "| stack | announced in the PR body |",
        "|---|---|",
    ]
    for s in replaced:
        lines.append(f"| `{s}` | {'no' if s in unannounced else 'yes'} |")
    lines += [
        "",
        "Replacing a host destroys it and boots a new one. A running experiment "
        "on it ends, and termination protection does not prevent this.",
        "",
        "The apply will refuse any host that is running an experiment when it "
        "gets there. If a replacement is intended, add a line per host to the "
        "PR description:",
        "",
        "```",
        *[f"Replaces-Host: {s}" for s in replaced],
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan_json")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--check-announced", metavar="BODY_ENV_VAR")
    ap.add_argument("--report", metavar="FILE")
    args = ap.parse_args(argv)

    plan = json.loads(Path(args.plan_json).read_text())
    replaced = replaced_stacks(plan)

    if args.list:
        for s in replaced:
            print(s)
        return 0

    # Read from the ENVIRONMENT, never interpolated into the shell: a PR body
    # is attacker-controlled text.
    body = os.environ.get(args.check_announced, "")
    unannounced = [s for s in replaced if s.lower() not in announced_stacks(body)]
    text = report(replaced, unannounced)
    if args.report:
        Path(args.report).write_text(text)
    if text:
        print(text)
    if unannounced:
        print(f"::error::this plan replaces {', '.join(unannounced)} and the "
              f"pull request does not say so. Add a 'Replaces-Host: <stack>' "
              f"line for each to the PR description if it is intended.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
