"""The deploy document's image prune (infra/terraform/ec2.tf, 2026-11-11), EXECUTED against stub docker/aws.

Text-grepping infra tests have shipped broken links before (CLAUDE.md), so this one runs the script.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = "123.dkr.ecr.ap-south-1.amazonaws.com/deltabt-paper-live"


def _run_command_items() -> tuple[str, str]:
    tf = (ROOT / "infra/terraform/ec2.tf").read_text()
    doc = tf[tf.index('resource "aws_ssm_document" "deploy"'):]
    doc = doc[:doc.index("\n}\n")]
    first = re.search(r'"(/opt/deltabt/deploy\.sh[^"]*)"', doc).group(1)
    body = doc[doc.index("<<-SH") + len("<<-SH"):doc.index("\n          SH\n")]
    lines = body.split("\n")[1:]
    indent = min(len(x) - len(x.lstrip()) for x in lines if x.strip())
    script = "\n".join(x[indent:] for x in lines).replace("$${", "${")      # Terraform's escape
    return first, script


def _stubs(tmp: Path, images: list[str], running: str, deploy_rc: int) -> dict:
    b = tmp / "bin"
    b.mkdir()
    log = tmp / "rmi.log"
    (tmp / "env").write_text(f"AWS_REGION=ap-south-1\nSSM_IMAGE_TAG_PARAM=/x/image_tag\n"
                             f"SSM_IMAGE_TAG_PREVIOUS_PARAM=/x/image_tag_previous\nECR_REPOSITORY_URL={REPO}\n")
    table = "REPOSITORY TAG IMAGE_ID CREATED SIZE\n" + "".join(f"{REPO} {t} abc 1d 956MB\n" for t in images)
    files = {
        "docker": f"""#!/bin/sh
case "$1" in
  images) printf '%s' '{table}' ;;
  inspect) printf '[{{"Config": {{"Image": "{REPO}:{running}"}}}}]' ;;
  rmi) echo "$2" >> {log} ;;
esac
exit 0
""",
        "aws": """#!/bin/sh
for a in "$@"; do case "$a" in /x/image_tag) echo cur111; exit 0;; /x/image_tag_previous) echo prev222; exit 0;; esac; done
exit 0
""",
        "deploy.sh": f"#!/bin/sh\nexit {deploy_rc}\n",
        "df": "#!/bin/sh\necho 'Filesystem Size Used Avail Use% Mounted'; echo '/dev/x 20G 5G 15G 25% /'\n",
    }
    for name, body in files.items():
        f = b / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return dict(os.environ, PATH=f"{b}:{os.environ['PATH']}"), log


def _script(tmp: Path, first: str, script: str) -> Path:
    s = tmp / "doc.sh"
    s.write_text(first.replace("/opt/deltabt/deploy.sh '{{ ImageTag }}'", "deploy.sh") + "\n"
                 + script.replace("/opt/deltabt/env", str(tmp / "env")))
    return s


def test_after_a_successful_deploy_it_keeps_current_previous_and_running_and_removes_the_rest(tmp_path):
    first, script = _run_command_items()
    assert "{{" not in script, "SSM reads double braces as its own parameters"
    env, log = _stubs(tmp_path, ["cur111", "prev222", "run333", "old444", "old555", "<none>"], "run333", 0)
    r = subprocess.run(["bash", str(_script(tmp_path, first, script))], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    removed = sorted(log.read_text().split())
    assert removed == [f"{REPO}:old444", f"{REPO}:old555"]
    assert "kept current=cur111 previous=prev222" in r.stdout and "25%" in r.stdout


def test_a_failed_deploy_exits_with_its_code_and_prunes_nothing(tmp_path):
    first, script = _run_command_items()
    env, log = _stubs(tmp_path, ["cur111", "old444"], "cur111", 1)
    r = subprocess.run(["bash", str(_script(tmp_path, first, script))], env=env, capture_output=True, text=True)
    assert r.returncode == 1
    assert not log.exists()


def test_the_document_stays_one_step():
    """_roll.yml reads get-command-invocation without --plugin-name: a second step would hang every deploy."""
    tf = (ROOT / "infra/terraform/ec2.tf").read_text()
    doc = tf[tf.index('resource "aws_ssm_document" "deploy"'):]
    doc = doc[:doc.index("\n}\n")]
    assert doc.count('action = "aws:runShellScript"') == 1
    roll = (ROOT / ".github/workflows/_roll.yml").read_text()
    assert "--plugin-name" not in roll
