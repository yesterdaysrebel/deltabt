"""Rendered user_data must stay well inside EC2's 16 KB cap.

WHAT THIS PINS
    EC2 rejects user_data over 16,384 bytes. The template embeds run.sh,
    deploy.sh and cloudwatch-agent.json, which are most of its weight, and
    nothing measured the total.

    On 2026-08-26 a thirteen-line comment added to run.sh took the rendered
    size from ~16,050 to 17,070 and the apply failed with

        expected length of user_data to be in the range (0 - 16384)

    -- a message that names neither the file that grew nor the reason. It had
    been sitting at 98% of the cap for some time, so the first symptom of any
    documentation edit was an opaque Terraform error at apply time, after the
    plan and the guard had both passed.

    The fix was base64gzip() rather than filebase64(), which is why the budget
    below is generous. The point of this test is that the NEXT approach to the
    limit is a failing test naming the file, not a plan failure naming nothing.

WHY A FRACTION AND NOT THE LIMIT
    Failing at 16,384 would mean the test passes right up until the apply
    breaks. 85% leaves ~2,400 bytes to notice in. Note that comments in the
    TEMPLATE are uncompressed and cost about three times what the same line
    costs inside one of the embedded scripts.
"""

from __future__ import annotations

import base64
import gzip
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "infra/terraform/templates/user_data.sh.tftpl"

EC2_LIMIT = 16_384
#: 85% leaves ~2,400 bytes -- roughly 35 comment lines -- between a
#: failing test and a failing apply. Measured at 78% after base64gzip().
BUDGET = int(0.85 * EC2_LIMIT)

EMBEDDED = {
    "run_sh_b64": ROOT / "deploy/aws/run.sh",
    "deploy_sh_b64": ROOT / "deploy/aws/deploy.sh",
    "cw_agent_b64": ROOT / "deploy/aws/cloudwatch-agent.json",
}

#: Scalars Terraform substitutes. None is near this long; it is a ceiling so
#: the estimate errs high rather than passing a template that would fail.
SCALAR_WIDTH = 120


def _b64gzip(path: pathlib.Path) -> str:
    """What Terraform's base64gzip() produces, near enough for sizing."""
    return base64.b64encode(gzip.compress(path.read_bytes())).decode()


def _rendered() -> str:
    text = TEMPLATE.read_text()
    for name, path in EMBEDDED.items():
        text = text.replace("${%s}" % name, _b64gzip(path))
    return re.sub(r"\$\{[a-z_]+\}", "x" * SCALAR_WIDTH, text)


def test_rendered_user_data_is_within_budget():
    n = len(_rendered())
    assert n <= BUDGET, (
        f"rendered user_data is {n:,} bytes, over the {BUDGET:,} budget "
        f"({EC2_LIMIT:,} is EC2's hard cap). The embedded scripts are the "
        f"weight: " + ", ".join(
            f"{k} {len(_b64gzip(v)):,}" for k, v in EMBEDDED.items()))


def test_it_is_within_the_hard_cap_too():
    """Belt and braces: the budget could be edited, the cap cannot."""
    assert len(_rendered()) <= EC2_LIMIT


@pytest.mark.parametrize("name", sorted(EMBEDDED))
def test_each_embedded_file_is_compressed_not_just_encoded(name):
    """filebase64() is what overflowed; base64gzip() is what fixed it."""
    ec2 = (ROOT / "infra/terraform/ec2.tf").read_text()
    row = [l for l in ec2.splitlines() if l.strip().startswith(name)]
    assert row, f"{name} is not passed to the template at all"
    assert "base64gzip(" in row[0], (
        f"{name} uses {row[0].strip()}; filebase64() is ~3x larger and is what "
        f"took user_data over EC2's cap on 2026-08-26")


def test_the_template_gunzips_what_it_embeds():
    """A base64gzip payload decoded without gunzip writes binary to disk."""
    text = TEMPLATE.read_text()
    for name in EMBEDDED:
        block = text[: text.index("${%s}" % name)]
        last = block.rsplit("\n", 2)[-2] if "\n" in block else block
        assert "gunzip" in last, (
            f"the heredoc feeding {name} does not pipe through gunzip: "
            f"{last!r}. The file on the host would be gzip bytes.")


def test_headroom_is_reported_not_just_asserted():
    """A number a human can act on, printed by -s or on failure."""
    n = len(_rendered())
    print(f"\nrendered user_data {n:,} / {EC2_LIMIT:,} bytes "
          f"({100*n/EC2_LIMIT:.0f}%), budget {BUDGET:,}")
    assert n > 0
