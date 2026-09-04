"""The paper-only boundary, enforced across the DEPLOYMENT surface.

`test_no_live_trading.py` scans shipped Python. It cannot see Terraform, shell,
or workflow YAML -- and those are exactly where a credential would be
introduced if one ever were: an IAM policy granting access to a new secret, an
`-e API_KEY=` in a `docker run`, an `aws secretsmanager` call fetching something
the bot has no business having.

The boundary is the ABSENCE of the capability. That claim has to hold for the
whole artifact, not just the part written in Python.

Every check here is negative-controlled: each has a companion asserting that
the scanner actually fires on a planted violation. A green scanner that cannot
fail is not evidence.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.safety import FORBIDDEN_CREDENTIAL_NAMES, FORBIDDEN_FLAGS, FORBIDDEN_ORDER_METHODS

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Everything that describes or performs a deployment.
SURFACE_DIRS = [ROOT / "infra", ROOT / "deploy", ROOT / ".github"]

SURFACE_SUFFIXES = {".tf", ".tftpl", ".sh", ".yml", ".yaml", ".json", ".example"}

#: This file names the forbidden things in order to look for them, and
#: docs/ explains the boundary in prose. Neither is deployed.
EXCLUDED = {"tests", "docs"}


def surface_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for base in SURFACE_DIRS:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in SURFACE_SUFFIXES:
                continue
            if any(part in EXCLUDED or part.startswith(".terraform") for part in path.parts):
                continue
            out.append(path)
    return out


FILES = surface_files()
IDS = [str(p.relative_to(ROOT)) for p in FILES]


def code(path: pathlib.Path) -> str:
    """The file with whole-line comments removed.

    The scanners have to look for the forbidden names, and the files
    themselves explain -- in comments -- why those names are absent. A comment
    cannot grant a capability, open a port, or carry a usable credential, so
    scanning prose about the boundary as if it were a violation would train
    everyone to weaken the scanner. Only whole-line comments are stripped;
    anything with code before the `#` is still scanned.
    """
    return "\n".join(
        line for line in path.read_text().splitlines()
        if not line.lstrip().startswith(("#", "//"))
    )


def test_there_is_a_deployment_surface_to_scan():
    """A scan over zero files passes vacuously and proves nothing."""
    assert len(FILES) >= 15, f"only found {len(FILES)} deployment files: {IDS}"
    kinds = {p.suffix for p in FILES}
    assert {".tf", ".sh", ".yml"} <= kinds, f"missing a whole file kind: {kinds}"


# ---------------------------------------------------------------------------
# Exchange credentials
# ---------------------------------------------------------------------------

#: Word-boundary patterns so `secret_key` does not match inside
#: `secretsmanager`, and `signature` does not match `SignatureVersion`.
CREDENTIAL_PATTERNS = [
    re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
    for name in FORBIDDEN_CREDENTIAL_NAMES
]

#: Delta-specific credential spellings the generic list would miss.
EXCHANGE_CREDENTIAL_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"\bdelta[_-]?api",
        r"\bDELTA_(KEY|SECRET|TOKEN)\b",
        r"\bexchange[_-]?(key|secret|credential)",
        r"\bhmac\b",
    )
]


@pytest.mark.parametrize("path", FILES, ids=IDS)
def test_no_exchange_credential_appears_in_the_deployment_surface(path):
    text = code(path)
    for pattern in CREDENTIAL_PATTERNS + EXCHANGE_CREDENTIAL_PATTERNS:
        match = pattern.search(text)
        assert match is None, (
            f"{path.relative_to(ROOT)} mentions '{match.group(0)}'. V1 has no "
            f"exchange credentials; introducing one here would give the bot a "
            f"capability the safety boundary says it does not have."
        )


def test_the_credential_scan_catches_a_planted_credential(tmp_path):
    """Negative control."""
    planted = tmp_path / "evil.tf"
    planted.write_text('resource "aws_secretsmanager_secret" "x" {\n'
                       '  name = "delta_api_secret"\n}\n')
    text = planted.read_text()
    assert any(p.search(text) for p in CREDENTIAL_PATTERNS + EXCHANGE_CREDENTIAL_PATTERNS)


# ---------------------------------------------------------------------------
# Order placement and live-mode flags
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", FILES, ids=IDS)
def test_no_order_placement_or_live_flag_is_configured(path):
    text = code(path)
    for name in FORBIDDEN_ORDER_METHODS | FORBIDDEN_FLAGS:
        assert not re.search(rf"\b{re.escape(name)}\b", text), (
            f"{path.relative_to(ROOT)} references '{name}'. A flag-gated live "
            f"mode is explicitly forbidden: the boundary is the absence of the "
            f"capability, not a runtime toggle."
        )


def test_the_flag_scan_catches_a_planted_flag(tmp_path):
    planted = tmp_path / "compose.yml"
    planted.write_text("environment:\n  ENABLE_LIVE_TRADING: 'true'\n")
    assert any(re.search(rf"\b{re.escape(n)}\b", planted.read_text())
               for n in FORBIDDEN_FLAGS)


# ---------------------------------------------------------------------------
# Long-lived AWS credentials
# ---------------------------------------------------------------------------

AWS_STATIC_CREDENTIALS = re.compile(
    r"\bAWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)\b")


@pytest.mark.parametrize("path", FILES, ids=IDS)
def test_no_long_lived_aws_credentials(path):
    """OIDC only. A static key is a credential to leak, rotate, and forget."""
    match = AWS_STATIC_CREDENTIALS.search(code(path))
    assert match is None, (
        f"{path.relative_to(ROOT)} uses {match.group(0)}. Authentication is "
        f"GitHub OIDC; there must be no long-lived AWS credential anywhere."
    )


def test_the_aws_credential_scan_catches_a_planted_key(tmp_path):
    planted = tmp_path / "wf.yml"
    planted.write_text("env:\n  AWS_ACCESS_KEY_ID: ${{ secrets.KEY }}\n")
    assert AWS_STATIC_CREDENTIALS.search(planted.read_text())


# ---------------------------------------------------------------------------
# Database credentials
# ---------------------------------------------------------------------------

#: A DSN carrying an inline password, capturing the host it points at.
#: `postgresql://user@host` and `postgresql://${VAR}` do not match.
INLINE_DSN_PASSWORD = re.compile(
    r"postgres(?:ql)?://[^\s\"'$:]+:[^\s\"'$@/]+@([^\s\"'/:]+)")

#: Hosts that provably cannot be a real database: loopback, and the service
#: names Docker Compose and the GitHub Actions service container use. A
#: throwaway credential for a container that exists for the length of a test
#: run is a different thing from a production password, and the difference is
#: checkable rather than a matter of judgement -- so it is checked.
EPHEMERAL_HOSTS = {"localhost", "127.0.0.1", "postgres", "db", "database", "pg"}


@pytest.mark.parametrize("path", FILES, ids=IDS)
def test_no_database_password_is_committed(path):
    text = code(path)
    for match in INLINE_DSN_PASSWORD.finditer(text):
        host = match.group(1)
        assert host in EPHEMERAL_HOSTS, (
            f"{path.relative_to(ROOT)} commits a DSN password for '{host}', "
            f"which is not a throwaway local host. RDS generates the password "
            f"into Secrets Manager; it must never be committed."
        )
    # random_password writes the generated value into Terraform state in
    # plaintext, which defeats the point of using Secrets Manager at all.
    assert "random_password" not in text, (
        f"{path.relative_to(ROOT)} uses random_password, which stores the "
        f"secret in Terraform state. Use manage_master_user_password instead."
    )


def test_the_dsn_scan_catches_a_planted_password():
    """Negative control: a real host with an inline password must be caught."""
    planted = "DATABASE_URL=postgresql://deltabt:hunter2@deltabt-paper.rds.amazonaws.com:5432/deltabt"
    match = INLINE_DSN_PASSWORD.search(planted)
    assert match and match.group(1) not in EPHEMERAL_HOSTS


def test_the_dsn_scan_permits_a_substituted_password():
    """The real scripts build the DSN from Secrets Manager at runtime."""
    for benign in (
        "postgresql://${DB_USER}:${DB_PASS_ENC}@${DB_HOST}:${DB_PORT}/${DB_NAME}",
        "postgresql://deltabt@localhost:5432/deltabt",
    ):
        assert INLINE_DSN_PASSWORD.search(benign) is None, benign


def test_the_comment_stripper_does_not_hide_real_code(tmp_path):
    """A violation with code before the `#` is still scanned."""
    planted = tmp_path / "x.yml"
    planted.write_text("  AWS_ACCESS_KEY_ID: abc   # not a comment line\n")
    assert AWS_STATIC_CREDENTIALS.search(code(planted))


# ---------------------------------------------------------------------------
# Network exposure
# ---------------------------------------------------------------------------

def test_nothing_is_open_to_the_internet():
    """No ingress rule may use 0.0.0.0/0.

    Egress may -- the bot has to reach the exchange, ECR and SSM. The
    distinction is the whole security model: outbound-only means SSM works
    with no inbound port at all.
    """
    for path in FILES:
        if path.suffix != ".tf":
            continue
        text = code(path)
        for block in re.finditer(
                r"(ingress\s*\{[^}]*\}|type\s*=\s*\"ingress\"[^}]*)", text, re.S):
            assert "0.0.0.0/0" not in block.group(0), (
                f"{path.relative_to(ROOT)} has an ingress rule open to the "
                f"internet. Access is via SSM Session Manager, which needs none."
            )


def test_the_database_is_never_publicly_accessible():
    rds = (ROOT / "infra" / "terraform" / "rds.tf").read_text()
    assert re.search(r"publicly_accessible\s*=\s*false", rds), \
        "rds.tf must set publicly_accessible = false explicitly"
    assert re.search(r"storage_encrypted\s*=\s*true", rds)
    assert re.search(r"deletion_protection\s*=\s*var\.db_deletion_protection", rds)


def test_admin_cidrs_defaults_to_empty():
    """SSH stays shut unless somebody deliberately opens it."""
    variables = (ROOT / "infra" / "terraform" / "variables.tf").read_text()
    block = variables[variables.index('variable "admin_cidrs"'):]
    block = block[:block.index("\n}")]
    assert re.search(r"default\s*=\s*\[\]", block), \
        "admin_cidrs must default to empty; access is via SSM"


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------

def test_the_deployed_image_tag_is_never_mutable():
    """`latest` cannot appear as a deploy target.

    A forward test whose results cannot be tied to an exact commit is not a
    forward test. The image tag is the only durable link between a row in the
    database and the code that produced it, so it must be the git SHA.
    """
    deploy = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    assert not re.search(r":latest\b", deploy), \
        "deploy.yml must never push or deploy a mutable tag"
    assert "IMMUTABLE" in (ROOT / "infra" / "terraform" / "ecr.tf").read_text()


def test_the_experiment_is_started_only_through_the_reviewed_path():
    """A run may be started by automation, but only from ONE place.

    THIS RULE CHANGED ON 2026-09-04, deliberately, and what it protects did
    not. It used to read "no automation may start a run at all", on the
    argument that starting one is a human go/no-go after preflight. In
    practice the human step was a merge, a dispatch, four SSM commands and a
    hand-edited repository variable -- and every one of those was a place to
    get it wrong. It went wrong: five service restarts and a stale instance id
    on 2026-09-04 alone. A ritual performed by hand is not a safety property.

    What actually keeps a run honest is unchanged and is asserted elsewhere:
    preflight must PASS before `start` is accepted (app/cli.py), the
    experiment records its git_sha and refuses a container that does not match
    (bind_experiment), and no order-placement code exists to ship
    (test_no_live_trading.py).

    So the rule is now about CONTAINMENT: exactly one automated path may start
    a run -- the experiment SSM document, whose content is reviewed in
    infra/terraform/ec2.tf. Anything else doing it silently is still a bug.
    """
    started = re.compile(r"forward-test\s+(start|create)")
    allowed = {"infra/terraform/ec2.tf"}
    offenders = []
    for path in FILES:
        rel = str(path.relative_to(ROOT))
        if rel in allowed:
            continue
        if started.search(code(path)):
            offenders.append(rel)
    assert not offenders, (
        f"{offenders} start the experiment outside the reviewed path. The only "
        f"automated starter is the experiment SSM document in {sorted(allowed)}."
    )


def test_the_one_allowed_starter_still_gates_on_preflight():
    """The containment above is worth nothing if that path skips preflight."""
    doc = (ROOT / "infra" / "terraform" / "ec2.tf").read_text()
    # Match the INVOCATION, not the prose. The comment above the document
    # names both commands while explaining the ordering, so a plain substring
    # search finds the explanation and reports the opposite of the truth.
    assert "cli forward-test preflight" in doc, (
        "the experiment document must run preflight before starting a run")
    pre = doc.index("cli forward-test preflight")
    start = doc.index("cli forward-test start")
    assert pre < start, "preflight must run BEFORE forward-test start"


def test_the_experiment_scan_catches_a_planted_start(tmp_path):
    planted = tmp_path / "userdata.sh"
    planted.write_text("docker exec deltabot python -m app forward-test start --days 30\n")
    assert re.search(r"forward-test\s+(start|create)", code(planted))


# ---------------------------------------------------------------------------
# Monitoring that is actually pointed at something
# ---------------------------------------------------------------------------

def test_rds_alarms_use_the_identifier_not_the_resource_id():
    """An alarm on the wrong dimension is worse than no alarm.

    `aws_db_instance.main.id` is the DbiResourceId (db-XVL3...); CloudWatch
    publishes AWS/RDS metrics under DBInstanceIdentifier = deltabt-paper. An
    alarm built from `.id` watches a dimension that has never had a datapoint.

    Shipped that way, two of the three RDS alarms used
    `treat_missing_data = notBreaching` and therefore sat in OK permanently --
    a filling disk or a pegged CPU during a 30-day run would have raised
    nothing. They reported green because they were not looking, which is the
    exact failure the bot-silent alarm exists to prevent, reproduced in the
    database alarms.
    """
    text = (ROOT / "infra" / "terraform" / "cloudwatch.tf").read_text()
    for line in text.splitlines():
        if "DBInstanceIdentifier" not in line or line.lstrip().startswith("#"):
            continue
        assert "aws_db_instance.main.id" not in line.replace(
            "aws_db_instance.main.identifier", ""), (
            f"DBInstanceIdentifier must come from .identifier, not .id:\n  {line.strip()}")


def test_ec2_alarms_use_the_instance_id():
    """The mirror case, asserted so the fix above is not over-applied.

    For aws_instance, `.id` IS the i-... instance id, which is what the
    InstanceId dimension wants. Nothing here should be changed to `.identifier`
    -- that attribute does not exist on aws_instance.
    """
    text = (ROOT / "infra" / "terraform" / "cloudwatch.tf").read_text()
    lines = [l for l in text.splitlines()
             if "InstanceId =" in l and not l.lstrip().startswith("#")]
    assert lines, "no InstanceId dimensions found to check"
    for line in lines:
        # Matched on the attribute rather than the whole expression: the
        # instances became keyed by stack (aws_instance.bot["v1"]) when a
        # second experiment started running alongside the first, and pinning
        # the unkeyed spelling would fail on a change that cannot introduce
        # the bug this test exists to catch.
        assert re.search(r"aws_instance\.bot(\[[^\]]+\])?\.id\b", line), line.strip()
        assert ".identifier" not in line, line.strip()
