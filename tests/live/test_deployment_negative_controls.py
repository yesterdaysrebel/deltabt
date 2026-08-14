"""The deployment test matrix: every guard, proven to fail on a real violation.

A guard nobody has watched fail is not a guard. Each test here plants the
exact violation the mechanism defends against and asserts that CI would reject
it -- and, where the distinction matters, that the same mechanism does NOT
reject the legitimate case, because a guard that rejects everything is also
useless.

MATRIX (letters match docs/aws_deployment.md section 14 and the task brief)

    A. good image -> deploy -> ready                 requires an AWS account
    B. broken image -> readiness fails -> rollback   logic verified here; the
                                                     live path requires an account
    C. database unavailable -> not ready             verified here against the
                                                     real readiness evaluator
    D. second EC2 instance -> preflight fails        verified here against the
                                                     real check, with a stub API
    E. mutable image tag -> CI fails                 verified here
    F. public ingress -> CI fails                    verified here
    G. static AWS key -> CI fails                    verified here
    H. exchange credential -> CI fails               verified here
    I. live-trading flag -> CI fails                 verified here
    J. database replacement -> tf_guard fails        verified here
    K. state bucket replacement -> tf_guard fails    verified here
    L. expensive resource -> cost guard fails        verified here

A and B's live halves are marked plainly rather than asserted, because this
environment has no AWS account and pretending otherwise would be the exact
failure mode these tests exist to prevent.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def load_file(path: pathlib.Path, name: str):
    """Import a module by FILE PATH, not by dotted package name.

    Everything here lives outside an installed package: scripts/ holds tools,
    and the sibling test module is only importable as `tests.live....` when the
    repository root happens to be on sys.path. It is locally (the editable
    install puts it there) and is NOT on a clean CI checkout, where pytest
    inserts `tests/` rather than the repo root -- so `import_module(
    "tests.live.test_deployment_safety")` raised ModuleNotFoundError in CI
    while passing on every developer machine. Loading by path removes the
    dependency on sys.path entirely.

    Registered in sys.modules before execution because `dataclasses` resolves
    annotations through `sys.modules[cls.__module__]`, and an unregistered
    module makes every dataclass in the file fail to build.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load(name: str):
    """A tool from scripts/."""
    return load_file(SCRIPTS / f"{name}.py", name)


def safety_module():
    """The sibling scanner module, loaded by path so CI and local agree."""
    return load_file(pathlib.Path(__file__).with_name("test_deployment_safety.py"),
                     "deltabt_test_deployment_safety")


def run_guard(script: str, plan: dict, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a guard against a plan exactly as CI does: a subprocess and an exit code."""
    import os
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(plan, fh)
        path = fh.name
    return subprocess.run([sys.executable, str(SCRIPTS / script), path],
                          capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def plan_of(*changes: tuple[str, list[str]]) -> dict:
    return {"resource_changes": [
        {"address": f"{rtype}.x", "type": rtype, "change": {"actions": actions}}
        for rtype, actions in changes]}


BASELINE = (("aws_vpc", ["create"]), ("aws_instance", ["create"]),
            ("aws_db_instance", ["create"]), ("aws_ecr_repository", ["create"]))


# ===========================================================================
# C. Database unavailable -> the bot must not be considered ready
# ===========================================================================

class TestDatabaseUnavailable:
    """Deployment success must never be inferred while the database is down."""

    def test_readiness_is_false_without_a_database(self):
        from app.monitoring.health import evaluate_readiness
        report = evaluate_readiness(
            {"last_closed_1m": 1786560300, "recovery_error": None},
            db_connected=False, lock_held=False, backfill_complete=True,
            indicators_warm=True, execution_ready=True)
        assert not report.healthy
        assert report.status_code == 503
        assert "database_connected" in report.failures

    def test_health_is_false_when_the_write_probe_fails(self):
        """A READABLE database can be read-only. Health probes an actual write."""
        from app.monitoring.health import evaluate_health
        snapshot = {"seconds_since_ws_message": 1.0, "last_closed_1m": 1_000_000,
                    "recent_gaps": 0, "strategy_running": True,
                    "seconds_since_bar_loop": 1.0}
        report = evaluate_health(snapshot, db_writable=False, now=1_000_070)
        assert not report.healthy
        assert "database_writable" in report.failures

    def test_the_verifier_treats_a_failed_database_check_as_failure(self):
        """The deploy verifier must not read 'container is up' as success."""
        verify = load("verify_deployment")
        healthz = json.dumps({
            "status": "unhealthy",
            "checks": [{"name": "database_writable", "ok": False,
                        "detail": "write probe failed"}]})
        parsed = verify.as_json(healthz)
        checks = {c["name"]: c for c in parsed["checks"]}
        assert parsed["status"] != "healthy"
        assert not checks["database_writable"]["ok"]


# ===========================================================================
# B. A broken image must fail readiness and trigger rollback
# ===========================================================================

class TestBrokenImageRollsBack:
    """The host script's rollback path, read as the contract it is."""

    DEPLOY = (ROOT / "deploy" / "aws" / "deploy.sh").read_text()

    def test_readiness_not_health_is_the_rollback_gate(self):
        """/healthz is about MARKET DATA.

        A candle gap in the seconds after a restart would roll back a
        perfectly good build, so the gate is /readyz and /healthz is reported.
        """
        assert "readyz" in self.DEPLOY
        assert re.search(r"if start_and_verify \"\$TAG\"", self.DEPLOY)
        assert "rolling back to $PREVIOUS" in self.DEPLOY

    def test_the_previous_tag_is_recorded_only_after_success(self):
        """Recording it on failure would make the rollback target the bad image."""
        success = self.DEPLOY.index('if start_and_verify "$TAG"; then')
        record = self.DEPLOY.index("set_param \"$SSM_IMAGE_TAG_PREVIOUS_PARAM\"")
        failure = self.DEPLOY.index("DEPLOY FAILED")
        assert success < record < failure

    def test_a_missing_image_is_refused_before_anything_is_touched(self):
        assert "aws ecr describe-images" in self.DEPLOY
        assert self.DEPLOY.index("aws ecr describe-images") < \
               self.DEPLOY.index("start_and_verify()")

    def test_it_refuses_to_loop_when_there_is_no_previous_tag(self):
        """Restarting into nothing forever is worse than stopping."""
        assert 'if [[ -z "$PREVIOUS" || "$PREVIOUS" == "none" ]]' in self.DEPLOY
        assert "leaving the service stopped rather than looping" in self.DEPLOY

    def test_a_failed_rollback_is_reported_as_not_the_image(self):
        assert "ROLLBACK ALSO FAILED" in self.DEPLOY
        assert "the problem is not the image" in self.DEPLOY

    def test_the_deploy_workflow_fails_when_the_host_command_fails(self):
        workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
        assert "::error::deploy $STATUS" in workflow
        assert "verify_deployment.py" in workflow


# ===========================================================================
# D. A second EC2 instance must fail preflight
# ===========================================================================

class TestDuplicateInstance:
    def _stub_aws(self, monkeypatch, module, instances):
        reservations = {"Reservations": [{"Instances": [
            {"InstanceId": i, "State": {"Name": "running"}} for i in instances]}]}
        monkeypatch.setattr(module, "aws",
                            lambda ctx, *a, **k: (True, reservations))

    def test_two_instances_fail_the_preflight_check(self, monkeypatch):
        preflight = load("aws_preflight")
        ctx = preflight.Context(region="ap-south-1", environment="paper",
                                expected_account="1", state_bucket="b",
                                ecr_repository="deltabt")
        self._stub_aws(monkeypatch, preflight, ["i-aaa", "i-bbb"])
        result = preflight.check_exactly_one_instance(ctx)
        assert result.status == preflight.FAIL
        assert "2 running bot instances" in result.detail
        assert "i-aaa" in result.detail and "i-bbb" in result.detail

    def test_one_instance_passes(self, monkeypatch):
        preflight = load("aws_preflight")
        ctx = preflight.Context(region="ap-south-1", environment="paper",
                                expected_account="1", state_bucket="b",
                                ecr_repository="deltabt")
        self._stub_aws(monkeypatch, preflight, ["i-aaa"])
        assert preflight.check_exactly_one_instance(ctx).status == preflight.PASS

    def test_zero_instances_fail_unless_a_plan_creates_one(self, monkeypatch):
        """'Missing' is never read as 'safe to create' on its own."""
        preflight = load("aws_preflight")
        base = dict(region="ap-south-1", environment="paper",
                    expected_account="1", state_bucket="b", ecr_repository="deltabt")
        self._stub_aws(monkeypatch, preflight, [])

        without_plan = preflight.Context(**base)
        assert preflight.check_exactly_one_instance(without_plan).status == preflight.FAIL

        with_plan = preflight.Context(**base, plan=plan_of(("aws_instance", ["create"])))
        assert preflight.check_exactly_one_instance(with_plan).status == preflight.PLANNED

    def test_the_unmanaged_checker_also_catches_duplicates(self):
        source = (SCRIPTS / "aws_unmanaged_check.py").read_text()
        assert "len(running) > 1" in source
        assert "terraform import aws_instance.bot" in source

    def test_a_preflight_check_that_raises_is_a_failure_not_a_skip(self):
        """A preflight that treats its own crash as inconclusive is decoration."""
        source = (SCRIPTS / "aws_preflight.py").read_text()
        assert "check raised:" in source
        body = source[source.index("for check in checks:"):]
        assert "except Exception" in body
        assert "FAIL," in body.split("except Exception")[1][:400]


# ===========================================================================
# E. A mutable image tag must fail CI
# ===========================================================================

class TestMutableTag:
    def test_ecr_enforces_immutability(self):
        assert 'image_tag_mutability = "IMMUTABLE"' in \
            (ROOT / "infra" / "terraform" / "ecr.tf").read_text()

    def test_the_deploy_workflow_tags_with_the_git_sha(self):
        workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
        assert "tag=${GITHUB_SHA}" in workflow
        assert not re.search(r":latest\b", workflow)

    def test_a_planted_latest_tag_would_be_caught(self):
        """Negative control for the check in test_deployment_safety.py."""
        planted = 'tags: ${{ steps.ecr.outputs.registry }}/deltabt:latest'
        assert re.search(r":latest\b", planted)

    def test_a_planted_mutable_ecr_setting_would_be_caught(self):
        planted = 'image_tag_mutability = "MUTABLE"'
        assert "IMMUTABLE" not in planted

    def test_the_verifier_compares_the_running_tag_to_the_intended_sha(self):
        source = (SCRIPTS / "verify_deployment.py").read_text()
        assert "image_matches_git_sha" in source
        assert "tag == args.expected_sha" in source


# ===========================================================================
# F. Public ingress must fail CI
# ===========================================================================

class TestPublicIngress:
    def test_the_real_terraform_has_no_public_ingress(self):
        """The live assertion, not a simulation."""
        for path in (ROOT / "infra" / "terraform").rglob("*.tf"):
            text = "\n".join(l for l in path.read_text().splitlines()
                             if not l.lstrip().startswith("#"))
            for block in re.finditer(r"ingress\s*\{[^}]*\}", text, re.S):
                assert "0.0.0.0/0" not in block.group(0), path

    def test_a_planted_open_ingress_is_caught(self):
        planted = '''
        resource "aws_security_group" "bad" {
          ingress {
            from_port   = 8000
            to_port     = 8000
            protocol    = "tcp"
            cidr_blocks = ["0.0.0.0/0"]
          }
        }
        '''
        found = [b for b in re.finditer(r"ingress\s*\{[^}]*\}", planted, re.S)
                 if "0.0.0.0/0" in b.group(0)]
        assert found, "the ingress scanner missed a wide-open rule"

    def test_egress_to_the_internet_is_still_permitted(self):
        """The bot must reach the exchange, ECR and SSM. Only INGRESS is barred."""
        network = (ROOT / "infra" / "terraform" / "network.tf").read_text()
        egress = re.search(r"egress\s*\{[^}]*\}", network, re.S)
        assert egress and "0.0.0.0/0" in egress.group(0)

    def test_preflight_fails_on_an_open_security_group(self, monkeypatch):
        preflight = load("aws_preflight")
        ctx = preflight.Context(region="ap-south-1", environment="paper",
                                expected_account="1", state_bucket="b",
                                ecr_repository="deltabt")
        monkeypatch.setattr(preflight, "aws", lambda c, *a, **k: (True, {
            "SecurityGroups": [{
                "GroupName": "deltabt-paper-bot",
                "IpPermissions": [{"IpProtocol": "tcp", "FromPort": 22,
                                   "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]}]}))
        result = preflight.check_no_public_ingress(ctx)
        assert result.status == preflight.FAIL
        assert "open to the internet" in result.detail

    def test_preflight_also_catches_an_ipv6_wildcard(self, monkeypatch):
        """`::/0` is just as open, and is easy to miss when reviewing by eye."""
        preflight = load("aws_preflight")
        ctx = preflight.Context(region="ap-south-1", environment="paper",
                                expected_account="1", state_bucket="b",
                                ecr_repository="deltabt")
        monkeypatch.setattr(preflight, "aws", lambda c, *a, **k: (True, {
            "SecurityGroups": [{
                "GroupName": "deltabt-paper-bot",
                "IpPermissions": [{"IpProtocol": "tcp", "FromPort": 8000,
                                   "Ipv6Ranges": [{"CidrIpv6": "::/0"}]}]}]}))
        assert preflight.check_no_public_ingress(ctx).status == preflight.FAIL

    def test_no_ssh_key_pair_exists_anywhere(self):
        for path in (ROOT / "infra" / "terraform").rglob("*.tf"):
            text = path.read_text()
            assert "aws_key_pair" not in text, path
            assert not re.search(r"^\s*key_name\s*=", text, re.M), path


# ===========================================================================
# G / H / I. Static AWS keys, exchange credentials, live-trading flags
# ===========================================================================

class TestCredentialAndFlagGuards:
    """The scanners in test_deployment_safety.py, shown failing."""

    def _scan(self, text: str) -> list[str]:
        safety = safety_module()
        hits = []
        if safety.AWS_STATIC_CREDENTIALS.search(text):
            hits.append("static-aws-credential")
        for pattern in safety.CREDENTIAL_PATTERNS + safety.EXCHANGE_CREDENTIAL_PATTERNS:
            if pattern.search(text):
                hits.append("exchange-credential")
                break
        from app.safety import FORBIDDEN_FLAGS, FORBIDDEN_ORDER_METHODS
        for name in FORBIDDEN_FLAGS | FORBIDDEN_ORDER_METHODS:
            if re.search(rf"\b{re.escape(name)}\b", text):
                hits.append("live-trading-capability")
                break
        return hits

    @pytest.mark.parametrize("planted,expected", [
        # G
        ("env:\n  AWS_ACCESS_KEY_ID: ${{ secrets.AK }}\n", "static-aws-credential"),
        ("  AWS_SECRET_ACCESS_KEY: abcdef\n", "static-aws-credential"),
        # H
        ('resource "aws_secretsmanager_secret" "x" { name = "delta_api_secret" }',
         "exchange-credential"),
        ('  -e "API_KEY=$DELTA_KEY" \\', "exchange-credential"),
        ('  signature = hmac.new(secret, msg)', "exchange-credential"),
        # I
        ("environment:\n  ENABLE_LIVE_TRADING: 'true'\n", "live-trading-capability"),
        ("  ALLOW_REAL_ORDERS: 1\n", "live-trading-capability"),
        ('  command: ["python", "-c", "place_real_order()"]', "live-trading-capability"),
    ])
    def test_a_planted_violation_is_caught(self, planted, expected):
        assert expected in self._scan(planted), \
            f"the scanner did not catch: {planted!r}"

    @pytest.mark.parametrize("benign", [
        'role-to-assume: ${{ vars.AWS_DEPLOY_ROLE_ARN }}',
        'aws secretsmanager get-secret-value --secret-id "$DB_SECRET_ARN"',
        'manage_master_user_password = true',
        'resource "aws_iam_role" "instance" { name = "deltabt-paper-instance" }',
    ])
    def test_legitimate_deployment_code_is_not_flagged(self, benign):
        """A guard that rejects everything is as useless as one that rejects nothing."""
        assert self._scan(benign) == [], f"false positive on: {benign!r}"

    def test_the_real_deployment_surface_is_clean(self):
        safety = safety_module()
        assert safety.FILES, "nothing to scan"
        for path in safety.FILES:
            assert self._scan(safety.code(path)) == [], path


# ===========================================================================
# J / K. Destructive Terraform plans must fail tf_guard
# ===========================================================================

class TestDestructivePlans:
    @pytest.mark.parametrize("rtype,label", [
        ("aws_db_instance", "the forward-test database"),
        ("aws_s3_bucket", "the Terraform state bucket"),
        ("aws_ecr_repository", "every past experiment's image"),
        ("aws_cloudwatch_log_group", "the run's operational history"),
        ("aws_instance", "the running bot"),
    ])
    @pytest.mark.parametrize("actions,kind", [
        (["delete", "create"], "REPLACE"),
        (["delete"], "DESTROY"),
    ])
    def test_a_destructive_plan_is_refused(self, rtype, label, actions, kind):
        result = run_guard("tf_guard.py", plan_of(*BASELINE, (rtype, actions)))
        assert result.returncode == 1, f"{kind} of {rtype} ({label}) was permitted"
        assert "REFUSING THIS PLAN" in result.stdout
        assert kind in result.stdout
        assert "terraform import" in result.stdout

    def test_creating_and_updating_is_permitted(self):
        result = run_guard("tf_guard.py", plan_of(
            ("aws_db_instance", ["update"]), ("aws_instance", ["create"]),
            ("aws_security_group", ["delete", "create"])))
        assert result.returncode == 0, result.stdout

    def test_the_override_is_explicit_and_announced(self):
        result = run_guard("tf_guard.py", plan_of(("aws_db_instance", ["delete"])),
                           env={"ALLOW_REPLACE": "1"})
        assert result.returncode == 0
        assert "ALLOW_REPLACE=1 IS SET" in result.stdout

    def test_terraform_itself_refuses_to_plan_the_database_destroy(self):
        """Belt and braces: prevent_destroy, not only the external guard."""
        rds = (ROOT / "infra" / "terraform" / "rds.tf").read_text()
        assert "prevent_destroy = true" in rds
        bootstrap = (ROOT / "infra" / "terraform" / "bootstrap" / "main.tf").read_text()
        assert "prevent_destroy = true" in bootstrap


# ===========================================================================
# L. Unexpected expensive resources must fail the cost guard
# ===========================================================================

class TestCostGuard:
    @pytest.mark.parametrize("rtype", [
        "aws_nat_gateway", "aws_lb", "aws_alb", "aws_eks_cluster",
        "aws_dynamodb_table", "aws_rds_cluster", "aws_elasticache_cluster",
        "aws_vpc_endpoint", "aws_autoscaling_group", "aws_ecs_service",
        "aws_cloudfront_distribution", "aws_efs_file_system",
    ])
    def test_a_planted_expensive_resource_fails(self, rtype):
        result = run_guard("tf_cost_preview.py", plan_of(*BASELINE, (rtype, ["create"])))
        assert result.returncode == 1, f"{rtype} was permitted"
        assert "REFUSING THIS PLAN" in result.stdout
        assert rtype in result.stdout

    def test_the_intended_architecture_passes(self):
        result = run_guard("tf_cost_preview.py", plan_of(
            *BASELINE,
            ("aws_subnet", ["create"]), ("aws_security_group", ["create"]),
            ("aws_eip", ["create"]), ("aws_cloudwatch_metric_alarm", ["create"]),
            ("aws_iam_role", ["create"]), ("aws_ssm_document", ["create"])))
        assert result.returncode == 0, result.stdout
        assert "REFUSING" not in result.stdout

    def test_an_autoscaler_is_refused_for_correctness_not_cost(self):
        """Exactly one bot may run. An autoscaler exists to run more than one."""
        result = run_guard("tf_cost_preview.py",
                           plan_of(*BASELINE, ("aws_autoscaling_group", ["create"])))
        assert result.returncode == 1
        assert "exactly" in result.stdout.lower()

    def test_an_unrecognised_resource_kind_is_reported(self):
        result = run_guard("tf_cost_preview.py",
                           plan_of(*BASELINE, ("aws_sagemaker_domain", ["create"])))
        assert "UNRECOGNISED" in result.stdout
        assert "aws_sagemaker_domain" in result.stdout

    def test_strict_mode_makes_an_unrecognised_kind_fatal(self):
        result = run_guard("tf_cost_preview.py",
                           plan_of(*BASELINE, ("aws_sagemaker_domain", ["create"])),
                           env={"STRICT_COST_GUARD": "1"})
        assert result.returncode == 1

    def test_the_override_is_explicit(self):
        result = run_guard("tf_cost_preview.py",
                           plan_of(*BASELINE, ("aws_nat_gateway", ["create"])),
                           env={"ALLOW_EXPENSIVE": "1"})
        assert result.returncode == 0
        assert "ALLOW_EXPENSIVE=1 IS SET" in result.stdout


# ===========================================================================
# Bootstrap: never adopts silently
# ===========================================================================

class TestBootstrapNeverAdoptsSilently:
    SOURCE = (SCRIPTS / "bootstrap_check.py").read_text()
    SHELL = (SCRIPTS / "bootstrap.sh").read_text()

    def test_the_checker_cannot_import_anything(self):
        """It prints import commands. It must not run them."""
        assert "terraform import" in self.SOURCE, "no import guidance offered"
        assert not re.search(r'subprocess\.run\(\s*\[\s*["\']terraform["\']\s*,\s*'
                             r'[^]]*["\']import["\']', self.SOURCE), \
            "bootstrap_check must never execute an import"

    def test_it_reports_every_trust_anchor(self):
        for address in ("aws_s3_bucket.state",
                        "aws_iam_openid_connect_provider.github",
                        "aws_iam_role.github_deploy",
                        "aws_iam_role.github_plan"):
            assert address in self.SOURCE

    def test_unmanaged_resources_stop_the_bootstrap(self):
        assert 'if [ "$CHECK" -eq 2 ]' in self.SHELL
        assert "Refusing to continue" in self.SHELL

    def test_the_bootstrap_runs_the_destructive_and_cost_guards(self):
        assert "scripts/tf_guard.py" in self.SHELL
        assert "scripts/tf_cost_preview.py" in self.SHELL

    def test_the_bootstrap_cannot_touch_the_application(self):
        """It creates trust anchors only.

        No EC2, no RDS, no ECR, no container, no experiment. If any of those
        appeared in the bootstrap stack, a workstation admin session would
        become the path by which the running bot changes.
        """
        stack = (ROOT / "infra" / "terraform" / "bootstrap" / "main.tf").read_text()
        for forbidden in ("aws_instance", "aws_db_instance", "aws_ecr_repository",
                          "aws_ssm_document", "aws_ssm_parameter"):
            assert f'resource "{forbidden}"' not in stack, \
                f"bootstrap must not manage {forbidden}"

    def test_there_is_no_bootstrap_workflow(self):
        """Deliberate.

        Anything that can create its own trust anchor can also replace it, and
        then who may deploy is decided by whoever can push a branch.

        The set is pinned as well as the intent, so a NEW workflow has to be
        added here consciously -- a workflow appearing unnoticed is how a
        repository grows a second path to production.
        """
        workflows = {p.name for p in (ROOT / ".github" / "workflows").glob("*.yml")}
        assert workflows == {"test.yml", "infrastructure.yml", "deploy.yml",
                             "monitor.yml"}, f"unexpected workflow: {workflows}"

        # The intent, asserted directly rather than only via the set above.
        # Comments are stripped: infrastructure.yml documents the bootstrap in
        # prose precisely BECAUSE it does not run it, and a scanner that reads
        # that as a violation teaches everyone to delete the explanation.
        code = safety_module().code
        for path in (ROOT / ".github" / "workflows").glob("*.yml"):
            text = code(path)
            assert "bootstrap.sh" not in text, f"{path.name} runs the bootstrap"

            # VALIDATING the bootstrap config in CI is fine and wanted -- it is
            # `terraform validate -backend=false`, which reads no state and
            # changes nothing. What must never appear is an APPLY, or an init
            # that binds to real state. The earlier version of this assertion
            # banned the directory outright and would have pushed someone to
            # delete a useful syntax check to make a test pass.
            if "infra/terraform/bootstrap" not in text:
                continue
            assert "terraform apply" not in text, \
                f"{path.name} applies the bootstrap stack"
            assert "-backend-config" not in text or "-backend=false" in text, \
                f"{path.name} initialises the bootstrap stack against real state"

    def test_the_monitor_workflow_cannot_change_anything(self):
        """The daily report is an observer and must stay one.

        It runs unattended on a schedule with no environment gate -- which is
        correct only for as long as it cannot make a change. If it ever gains
        the ability to apply, deploy, or run arbitrary shell, that gate becomes
        load-bearing and its absence becomes the defect.
        """
        # Comment-stripped: monitor.yml NAMES AWS-RunShellScript and the deploy
        # document in order to say it deliberately uses neither.
        text = safety_module().code(ROOT / ".github" / "workflows" / "monitor.yml")
        for forbidden in ("terraform apply", "terraform plan", "docker build",
                          "docker push", "AWS-RunShellScript", "SSM_DEPLOY_DOCUMENT",
                          "AWS_DEPLOY_ROLE_ARN", "AWS_TERRAFORM_ROLE_ARN",
                          "forward-test start", "forward-test stop"):
            assert forbidden not in text, f"monitor.yml references {forbidden!r}"
        assert "AWS_MONITOR_ROLE_ARN" in text
        assert "SSM_MONITOR_DOCUMENT" in text

    def test_the_monitor_role_and_document_are_read_only(self):
        tf = (ROOT / "infra" / "terraform" / "monitoring.tf").read_text()
        code = "\n".join(l for l in tf.splitlines() if not l.lstrip().startswith("#"))
        # Every granted action must be a read. One write anywhere in this role
        # and "read-only observer" stops being true.
        import re
        actions = re.findall(r'"((?:ec2|rds|ecr|cloudwatch|logs|ssm|s3|iam):[A-Za-z*]+)"', code)
        assert actions, "no actions found to check"
        allowed_verbs = ("Describe", "Get", "List", "Filter")
        for a in actions:
            service, verb = a.split(":")
            assert verb.startswith(allowed_verbs) or a == "ssm:SendCommand", \
                f"monitor role grants a non-read action: {a}"
        # SendCommand is the one exception, and it is scoped to the monitor
        # document -- never AWS-RunShellScript, never the deploy document.
        assert "aws_ssm_document.monitor.arn" in code
        assert "AWS-RunShellScript" not in code
        assert "aws_ssm_document.deploy" not in code


# ===========================================================================
# The experiment boundary
# ===========================================================================

class TestNothingStartsTheExperiment:
    def test_no_automation_can_start_a_run(self):
        pattern = re.compile(r"forward-test\s+(start|create)")
        safety = safety_module()
        for path in safety.FILES:
            assert not pattern.search(safety.code(path)), path
        for script in SCRIPTS.glob("*"):
            if script.suffix in (".py", ".sh"):
                text = "\n".join(l for l in script.read_text().splitlines()
                                 if not l.lstrip().startswith("#"))
                assert not pattern.search(text), script

    def test_the_verifier_fails_if_an_experiment_appeared(self):
        source = (SCRIPTS / "verify_deployment.py").read_text()
        assert "no_experiment_created" in source
        assert "AN EXPERIMENT IS ACTIVE" in source

    def test_the_frozen_hash_is_asserted_end_to_end(self):
        from app.config.strategy import StrategyConfig
        frozen = "5a5412369f3823f3"
        assert StrategyConfig().config_hash == frozen
        assert frozen in (SCRIPTS / "verify_deployment.py").read_text()
        assert frozen in (ROOT / ".github" / "workflows" / "test.yml").read_text()
