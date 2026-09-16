"""Emit the deploy matrix for LIVE stacks, read from Terraform.

WHY THIS EXISTS AND IS NOT A TABLE IN THE WORKFLOW

deploy-paper.yml's `targets` job carries a hand-maintained JSON table of
stacks. That table has drifted from Terraform before: it once named two hosts
that had been destroyed, and the workflow dispatched rolls at terminated
instances. The file header of tests/live_exec/test_live_deployment.py records
it as one of two failures that motivated "adding a live stack must be ONE
edit".

Reproducing that pattern for live stacks would reintroduce exactly the bug the
live work is meant to avoid, so the list is READ FROM `infra/terraform/live.tf`
-- the same file Terraform reads. There is no second place to update, and a
stack that exists in Terraform cannot be missing here.

WHY A PARSER AND NOT `tofu output`. The deploy workflow authenticates as a role
that can push one image and invoke one SSM document. It has no Terraform state
access, and giving it some so a matrix could be computed would dissolve the
separation between deploying and administering.

The grammar accepted is the one HCL uses for this map and nothing more:

    default = {
      tnet = { variant = "SPEC:...", db_name = "deltabt_tnet" }
    }
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIVE_TF = ROOT / "infra/terraform/live.tf"

#: `key = { ... }` one per line, inside the default block.
ENTRY = re.compile(
    r'^\s*(?P<stack>[A-Za-z_][A-Za-z0-9_-]*)\s*=\s*\{(?P<body>[^}]*)\}\s*$')
FIELD = re.compile(r'(?P<name>[a-z_]+)\s*=\s*"(?P<value>[^"]*)"')


def default_block(text: str) -> str:
    """The body of `default = { ... }` inside `variable "live_stacks"`.

    Brace-matched rather than regex-matched: the block contains nested braces,
    and a non-greedy match to the first `}` would stop inside the first entry
    and silently return an empty stack list -- which reads as "no live stacks"
    and would skip every deploy.
    """
    i = text.index('variable "live_stacks"')
    j = text.index("default", i)
    k = text.index("{", j)
    depth = 0
    for pos in range(k, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return text[k + 1:pos]
    raise ValueError("unbalanced braces in the live_stacks default block")


def matrix(text: str) -> list[dict[str, str]]:
    out = []
    for line in default_block(text).splitlines():
        line = line.split("#", 1)[0]
        m = ENTRY.match(line)
        if not m:
            continue
        fields = {f["name"]: f["value"] for f in FIELD.finditer(m["body"])}
        stack = m["stack"]
        if "variant" not in fields:
            raise ValueError(f"live stack {stack!r} has no variant")
        out.append({
            "stack": stack,
            "variant": fields["variant"],
            # Matching the paper matrix's shape, so the steps that read
            # `matrix.document_var` behave identically. The fallback the
            # workflow applies when the variable is unset is the name
            # Terraform gives the document, so a new stack needs no
            # repository variable at all.
            "document_var": f"SSM_DEPLOY_DOCUMENT_{stack.upper()}",
        })
    return out


def main(argv: list[str]) -> int:
    rows = matrix(LIVE_TF.read_text())
    only = argv[1] if len(argv) > 1 else ""
    if only:
        rows = [r for r in rows if r["stack"] == only]
        if not rows:
            print(f"only_stack={only!r} matches no live stack", file=sys.stderr)
            return 1
    print(json.dumps(rows, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
