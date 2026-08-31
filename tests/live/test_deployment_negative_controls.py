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
import datetime
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
        """Two images failing to start usually means the environment.

        The assertion below used to be the whole story. 2026-08-31 falsified
        its absolutism: the rollback failed BECAUSE of the image. The stack
        moved to SPEC:manual_scalp@5 and the previous tag predated that catalog
        family, so the rolled-back container died on 'names no catalog family'
        every 20 seconds. `usually` is now load-bearing, and the counter-case
        is named so nobody re-derives it at 3am.
        """
        assert "ROLLBACK ALSO FAILED" in self.DEPLOY
        assert "the problem is not the image" in self.DEPLOY
        assert "BUT NOT ALWAYS" in self.DEPLOY
        assert "DELTABOT_VARIANT" in self.DEPLOY

    def test_a_failed_rollback_stops_rather_than_loops(self):
        """start_and_verify uses `systemctl restart` and the unit is
        Restart=always, so logging and exiting leaves an unattended crash loop.
        It burned CPU and filled the journal on 2026-08-31 until somebody
        stopped it by hand. A stopped host is honest: /readyz is unreachable,
        the report says so, the alarms fire."""
        tail = self.DEPLOY[self.DEPLOY.index('if start_and_verify "$PREVIOUS"'):]
        assert "systemctl stop deltabt.service" in tail

    def test_the_deploy_workflow_fails_when_the_host_command_fails(self):
        workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
        assert "::error::deploy $STATUS" in workflow
        assert "verify_deployment.py" in workflow


# ===========================================================================
# D. A second EC2 instance must fail preflight
# ===========================================================================

class TestDuplicateInstance:
    """Two bots per DATABASE is the fatal case, not two bots in total.

    The check was "exactly one instance anywhere" until two experiments began
    running side by side on separate databases. The advisory lock, the
    single-RUNNING-experiment index and the one-open-position-per-symbol index
    are all per-database, so the collision the old rule prevented does not
    exist between stacks -- but it is completely unchanged WITHIN one.
    """

    def _stub_aws(self, monkeypatch, module, instances):
        """instances: list of (id, stack) or bare ids meaning untagged."""
        def inst(spec):
            if isinstance(spec, tuple):
                iid, stack = spec
                tags = [{"Key": "Stack", "Value": stack}]
            else:
                iid, tags = spec, []
            return {"InstanceId": iid, "State": {"Name": "running"}, "Tags": tags}

        reservations = {"Reservations": [{"Instances": [inst(i) for i in instances]}]}
        monkeypatch.setattr(module, "aws",
                            lambda ctx, *a, **k: (True, reservations))

    def _ctx(self, preflight, **kw):
        return preflight.Context(region="ap-south-1", environment="paper",
                                 expected_account="1", state_bucket="b",
                                 ecr_repository="deltabt", **kw)

    def test_two_instances_in_the_same_stack_fail(self, monkeypatch):
        preflight = load("aws_preflight")
        self._stub_aws(monkeypatch, preflight, [("i-aaa", "v1"), ("i-bbb", "v1")])
        result = preflight.check_one_instance_per_stack(self._ctx(preflight))
        assert result.status == preflight.FAIL
        assert "i-aaa" in result.detail and "i-bbb" in result.detail

    def test_two_untagged_instances_fail(self, monkeypatch):
        """A host that lost its tags must not read as 'a different stack'."""
        preflight = load("aws_preflight")
        self._stub_aws(monkeypatch, preflight, ["i-aaa", "i-bbb"])
        result = preflight.check_one_instance_per_stack(self._ctx(preflight))
        assert result.status == preflight.FAIL

    def test_one_instance_per_stack_passes(self, monkeypatch):
        preflight = load("aws_preflight")
        self._stub_aws(monkeypatch, preflight, [("i-aaa", "v1"), ("i-bbb", "v2")])
        result = preflight.check_one_instance_per_stack(self._ctx(preflight))
        assert result.status == preflight.PASS
        assert "v1=i-aaa" in result.detail and "v2=i-bbb" in result.detail

    def test_one_instance_passes(self, monkeypatch):
        preflight = load("aws_preflight")
        self._stub_aws(monkeypatch, preflight, [("i-aaa", "v1")])
        assert preflight.check_one_instance_per_stack(
            self._ctx(preflight)).status == preflight.PASS

    def test_zero_instances_fail_unless_a_plan_creates_one(self, monkeypatch):
        """'Missing' is never read as 'safe to create' on its own."""
        preflight = load("aws_preflight")
        self._stub_aws(monkeypatch, preflight, [])

        assert preflight.check_one_instance_per_stack(
            self._ctx(preflight)).status == preflight.FAIL

        with_plan = self._ctx(preflight,
                              plan=plan_of(("aws_instance", ["create"])))
        assert preflight.check_one_instance_per_stack(
            with_plan).status == preflight.PLANNED

    def test_the_unmanaged_checker_also_catches_duplicates(self):
        """Behaviour, not a grep for a source string.

        This asserted `"len(running) > 1" in source`, which stayed true while
        the meaning changed underneath it -- and the checker went on refusing
        every plan with two legitimate hosts running until a real apply failed.
        """
        check = load("aws_unmanaged_check")

        def inst(iid, stack):
            return {"InstanceId": iid,
                    "Tags": ([{"Key": "Stack", "Value": stack}] if stack else [])}

        one_each = check.group_by_stack([inst("i-a", "v1"), inst("i-b", "v2")])
        assert {k: len(v) for k, v in one_each.items()} == {"v1": 1, "v2": 1}

        dupes = check.group_by_stack([inst("i-a", "v1"), inst("i-b", "v1")])
        assert dupes["v1"] == ["i-a", "i-b"], "two in one stack must group together"

        untagged = check.group_by_stack([inst("i-a", None), inst("i-b", None)])
        assert untagged["<untagged>"] == ["i-a", "i-b"], (
            "a host that lost its tags must not pass as a different stack")

        source = (SCRIPTS / "aws_unmanaged_check.py").read_text()
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
        #
        # Matched on the reference rather than one exact expression: the
        # documents became per-stack (`for d in aws_ssm_document.monitor`)
        # when a second experiment started running alongside the first, and
        # pinning the old `.arn` spelling would have failed on a change that
        # does not weaken anything.
        assert "aws_ssm_document.monitor" in code
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
        from app.config.strategy import FROZEN
        frozen = "d7837e445bc74781"
        assert FROZEN.config_hash == frozen, (
            "assert against what SHIPS. StrategyConfig() is the dataclass "
            "default, which is V2; FROZEN is set explicitly so a variant "
            "switch is one visible line -- see app/config/variants.py")
        assert frozen in (SCRIPTS / "verify_deployment.py").read_text()
        assert frozen in (ROOT / ".github" / "workflows" / "test.yml").read_text()


# ===========================================================================
# The daily report's verdict
#
# The first production report printed eighteen ERROR lines and then concluded
# "Nothing in this report needs a human". The count was computed and thrown
# away: it never reached `problems`, and `problems` alone sets the exit code
# that the scheduled workflow turns into an email. So no quantity of
# application errors could ever raise an alarm.
#
# The fix cannot simply be "any ERROR fails", because Delta recycles the
# market-data websocket about once an hour and the client resubscribes in
# ~1.5s. Escalating those would page a human every morning for a socket
# working exactly as designed -- and an alarm that cries wolf daily is the
# same as no alarm. So errors are attributed (retired container vs running
# one) and then judged: application errors always escalate, feed errors only
# when the heartbeat shows the data actually stopped.
#
# Every test below pairs the failure it detects with the benign case it must
# stay quiet on.
# ===========================================================================

def _report():
    return load("daily_report")


def _probe(started="2026-08-13T19:42:44.492000000Z", **db):
    """A probe payload in the ===SECTION=== format the report parses."""
    body = {"experiments": [{"experiment_id": "H-WPR-1-PAPER-AWS-20260813",
                             "status": "RUNNING",
                             "started_at": "2026-08-13T19:41:15",
                             "planned_days": 30}],
            "evaluations_24h": 412, "outcomes_24h": {"NO_SETUP": 400},
            "paper_orders": 1, "paper_fills": 1, "closed_trades_total": 0}
    body.update(db)
    return "\n".join([
        "===CONTAINER===",
        "deltabt:abc|Up 8 hours (healthy)",
        f"user=10001:10001 readonly=true restarts=0 started={started}",
        "systemd_restarts=0",
        "===HEALTHZ===", json.dumps({"status": "healthy"}),
        "===READYZ===", json.dumps({"status": "healthy"}),
        "===STATUS===", json.dumps({"strategy_config_hash": "d7837e445bc74781"}),
        "===PERSISTENCE===", json.dumps(body),
        "===END===",
    ])


def _run_report(monkeypatch, capsys, probe_text, errors=(), beats=(), gaps=(),
                extra_argv=(), instances=(("i-1", None),), alarms=()):
    """Drive main() against canned AWS responses; return (exit code, markdown)."""
    mod = _report()
    mod.problems.clear()
    mod.notes.clear()

    def fake_aws(*args, region):
        head = tuple(args[:2])
        if head == ("ssm", "send-command"):
            return True, {"Command": {"CommandId": "c1"}}
        if head == ("ssm", "get-command-invocation"):
            return True, {"Status": "Success", "StandardOutputContent": probe_text}
        if head == ("ec2", "describe-instances"):
            # (id, stack); stack None means the host carries no Stack tag.
            return True, {"Reservations": [{"Instances": [
                {"InstanceId": i,
                 "Tags": ([{"Key": "Stack", "Value": st}] if st else [])}
                for i, st in instances]}]}
        if head == ("cloudwatch", "describe-alarms"):
            # (name, state)
            return True, {"MetricAlarms": [
                {"AlarmName": n, "StateValue": st} for n, st in alarms]}
        if head == ("logs", "filter-log-events"):
            pattern = args[args.index("--filter-pattern") + 1]
            picked = (beats if "heartbeat" in pattern
                      else errors if "$.level" in pattern else gaps)
            return True, {"events": [{"message": json.dumps(d)} for d in picked]}
        raise AssertionError(f"unexpected AWS call: {args}")

    monkeypatch.setattr(mod, "aws", fake_aws)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None, raising=False)
    monkeypatch.setattr(sys, "argv", ["daily_report.py", "--instance-id", "i-1",
                                      "--document", "d", "--day", "2026-08-13",
                                      *extra_argv])
    code = mod.main()
    return code, capsys.readouterr().out


def _err(ts, logger, message="boom"):
    return {"ts": ts, "level": "ERROR", "logger": logger, "message": message}


def _beat(ts, silence):
    return {"ts": ts, "level": "INFO", "logger": "app.runtime.bot",
            "message": "heartbeat", "seconds_since_ws_message": silence}


HEALTHY_BEATS = [_beat(f"2026-08-13T2{h}:00:00.000Z", 0.3) for h in range(4)]


class TestZeroEvaluationsMeansDifferentThingsAtDifferentAges:
    """Scoping evaluations_24h to the experiment broke this check.

    It used to count the whole database, so zero meant the loop was dead.
    Now a run that started twenty minutes ago legitimately has zero, and
    escalating that would fire on EVERY experiment start -- the same cry-wolf
    failure this report already had with hourly feed reconnects.
    """

    @staticmethod
    def _aged(minutes):
        """A probe whose RUNNING experiment started `minutes` ago."""
        began = (datetime.datetime.now(datetime.timezone.utc)
                 - datetime.timedelta(minutes=minutes))
        return _probe(evaluations_24h=0, outcomes_24h={},
                      # No orders either -- the "why no trades" section this
                      # check lives in only renders when nothing was placed.
                      orders_run=0, fills_run=0, paper_orders=0, paper_fills=0,
                      experiments=[{"experiment_id": "E", "status": "RUNNING",
                                    "started_at": began.strftime("%Y-%m-%dT%H:%M:%S"),
                                    "planned_days": 30}])

    def test_a_young_run_with_no_evaluations_is_a_note(self, monkeypatch, capsys):
        code, out = _run_report(monkeypatch, capsys, self._aged(5),
                                beats=HEALTHY_BEATS)
        assert code == 0, "a one-minute-old experiment must not page anyone"
        assert "All clear" in out
        assert "no evaluations yet" in out

    def test_an_old_run_with_no_evaluations_needs_a_human(self, monkeypatch, capsys):
        """The case the check exists for: the loop really has stopped."""
        code, out = _run_report(monkeypatch, capsys, self._aged(600),
                                beats=HEALTHY_BEATS)
        assert code == 1
        assert "the loop may not be running" in out

    def test_the_boundary_is_the_stated_one(self, monkeypatch, capsys):
        mod = _report()
        assert mod.MIN_RUN_AGE_FOR_SILENCE == 3600.0
        code, _ = _run_report(monkeypatch, capsys, self._aged(61),
                              beats=HEALTHY_BEATS)
        assert code == 1, "just past the threshold must escalate"

    def test_a_run_that_is_evaluating_is_unaffected(self, monkeypatch, capsys):
        """The negative control: normal activity at any age stays quiet."""
        code, out = _run_report(monkeypatch, capsys, _probe(), beats=HEALTHY_BEATS)
        assert code == 0
        assert "no evaluations" not in out


class TestTheRejectionSectionAnswersItsOwnHeading:
    """A heading with nothing under it is worse than no heading at all.

    The first run of H-WPR-1-PAPER-AWS-V1-20260814-2 printed
    "## Why there were no trades" followed by a blank line: the young-run
    explanation had been routed into notes/problems, which render at the FOOT
    of the report. The reader saw a section that declined to answer itself and
    the answer twenty lines away. An empty section reads as "we don't know",
    and this report exists precisely so that a quiet day is never unexplained.
    """

    @staticmethod
    def _section(out):
        """The body between the heading and the next one, whitespace stripped."""
        head = "## Why setups did not become trades"
        assert head in out, "the section must always render"
        rest = out.split(head, 1)[1]
        return rest.split("\n## ", 1)[0].strip()

    def test_a_young_run_explains_itself_in_the_section(self, monkeypatch, capsys):
        began = (datetime.datetime.now(datetime.timezone.utc)
                 - datetime.timedelta(minutes=6))
        probe = _probe(evaluations_24h=0, outcomes_24h={},
                       orders_run=0, fills_run=0, paper_orders=0, paper_fills=0,
                       experiments=[{"experiment_id": "E", "status": "RUNNING",
                                     "started_at": began.strftime("%Y-%m-%dT%H:%M:%S"),
                                     "planned_days": 30}])
        code, out = _run_report(monkeypatch, capsys, probe, beats=HEALTHY_BEATS)
        assert code == 0
        body = self._section(out)
        assert body, "THE BUG: the heading printed with an empty body"
        assert "has not evaluated a bar yet" in body
        # The note still fires -- explanation and escalation are separate jobs.
        assert "no evaluations yet" in out

    def test_a_dead_loop_says_so_in_the_section_too(self, monkeypatch, capsys):
        began = (datetime.datetime.now(datetime.timezone.utc)
                 - datetime.timedelta(minutes=600))
        probe = _probe(evaluations_24h=0, outcomes_24h={},
                       orders_run=0, fills_run=0, paper_orders=0, paper_fills=0,
                       experiments=[{"experiment_id": "E", "status": "RUNNING",
                                     "started_at": began.strftime("%Y-%m-%dT%H:%M:%S"),
                                     "planned_days": 30}])
        code, out = _run_report(monkeypatch, capsys, probe, beats=HEALTHY_BEATS)
        assert code == 1
        assert "No evaluations at all in 24h" in self._section(out)

    def test_evaluations_with_no_gate_hit_name_the_outcome_mix(self, monkeypatch, capsys):
        """The old fallback said 'no rejection reasons recorded' and stopped."""
        probe = _probe(evaluations_24h=412,
                       outcomes_24h={"NO_SETUP": 400, "SETUP_INCOMPLETE": 12},
                       rejections_24h={},
                       orders_run=0, fills_run=0, paper_orders=0, paper_fills=0)
        _, out = _run_report(monkeypatch, capsys, probe, beats=HEALTHY_BEATS)
        body = self._section(out)
        assert "412 evaluation(s)" in body
        assert "`NO_SETUP` 400" in body
        assert "`SETUP_INCOMPLETE` 12" in body

    def test_the_section_is_never_empty_on_any_quiet_path(self, monkeypatch, capsys):
        """The general guard, so a future branch cannot reintroduce the blank."""
        quiet = dict(orders_run=0, fills_run=0, paper_orders=0, paper_fills=0)
        cases = [
            dict(evaluations_24h=0, outcomes_24h={}),
            dict(evaluations_24h=90, outcomes_24h={"NO_SETUP": 90}),
            dict(evaluations_24h=90, outcomes_24h={"SETUP": 90},
                 rejections_24h={"COOLDOWN": 90}),
            dict(evaluations_24h=5, outcomes_24h={}, rejections_24h={}),
        ]
        for case in cases:
            probe = _probe(**{**quiet, **case})
            _, out = _run_report(monkeypatch, capsys, probe, beats=HEALTHY_BEATS)
            assert self._section(out), f"empty section for {case!r}"


class TestTheReportChecksTheRunItWasPointedAt:
    """With two experiments live, "an experiment is running" stopped being
    enough to identify which one this is.

    The strategy hash was a module constant pinned to V1. Run against the V2
    host it would have declared a hash change on every single report, and the
    only way to silence it would have been to remove the check.
    """

    V2_STRATEGY = "632efcaff62c4d7c"
    LOOSE_RISK = "0000feed0000beef"

    def _v2_probe(self, **kw):
        text = _probe(experiments=[{
            "experiment_id": "H-WPR-1-PAPER-AWS-V2", "status": "RUNNING",
            "started_at": "2026-08-13T19:41:15", "planned_days": 30,
            "strategy_hash": self.V2_STRATEGY, "risk_hash": self.LOOSE_RISK}],
            **kw)
        return text.replace('"strategy_config_hash": "d7837e445bc74781"',
                            f'"strategy_config_hash": "{self.V2_STRATEGY}"')

    def test_the_fixture_really_switches_hashes(self):
        """Without this, a silently failing .replace() would make every test
        below pass by testing V1 twice."""
        assert '"strategy_config_hash": "d7837e445bc74781"' in _probe()
        assert self.V2_STRATEGY in self._v2_probe()
        assert "d7837e445bc74781" not in self._v2_probe()

    def test_pointing_the_report_at_v2_accepts_v2s_hashes(self, monkeypatch, capsys):
        code, out = _run_report(
            monkeypatch, capsys, self._v2_probe(), beats=HEALTHY_BEATS,
            extra_argv=("--expect-strategy-hash", self.V2_STRATEGY,
                        "--expect-risk-hash", self.LOOSE_RISK))
        assert code == 0, out
        assert "STRATEGY HASH CHANGED" not in out
        assert "RISK HASH" not in out

    def test_the_v1_default_would_have_failed_on_the_v2_host(self, monkeypatch, capsys):
        """Why the constant had to become an argument."""
        code, out = _run_report(monkeypatch, capsys, self._v2_probe(),
                                beats=HEALTHY_BEATS)
        assert code == 1
        assert "STRATEGY HASH CHANGED" in out

    def test_a_wrong_strategy_hash_still_needs_a_human(self, monkeypatch, capsys):
        """The check must not have been weakened into a no-op."""
        code, out = _run_report(
            monkeypatch, capsys, self._v2_probe(), beats=HEALTHY_BEATS,
            extra_argv=("--expect-strategy-hash", "deadbeefdeadbeef",
                        "--expect-risk-hash", self.LOOSE_RISK))
        assert code == 1
        assert "STRATEGY HASH CHANGED" in out
        assert self.V2_STRATEGY in out and "deadbeefdeadbeef" in out

    def test_a_wrong_risk_hash_is_caught(self, monkeypatch, capsys):
        """The original audit finding: risk could change with nothing showing."""
        code, out = _run_report(
            monkeypatch, capsys, self._v2_probe(), beats=HEALTHY_BEATS,
            extra_argv=("--expect-strategy-hash", self.V2_STRATEGY,
                        "--expect-risk-hash", "1111222233334444"))
        assert code == 1
        assert "RISK HASH IS NOT THE EXPECTED ONE" in out
        assert "H-WPR-1-PAPER-AWS-V2" in out

    def test_an_experiment_with_no_risk_hash_is_not_flagged(self, monkeypatch, capsys):
        """Older rows predate the probe returning it; absence is not a mismatch."""
        code, out = _run_report(monkeypatch, capsys, _probe(),
                                beats=HEALTHY_BEATS)
        assert code == 0
        assert "RISK HASH" not in out


class TestTheReportCountsInstancesPerStack:
    """It asked for exactly one instance ANYWHERE, which was right while there
    was one experiment.

    With two running side by side it fired on BOTH daily reports, each naming
    the other stack's host as the fault:

        expected exactly 1 running bot instance, found 2:
        ['i-04005b0d4b20e198c', 'i-00a4acce037259971']

    The same correction had already been made in aws_preflight.py and
    verify_deployment.py; this file was missed because it enumerates instances
    itself rather than sharing their helper.
    """

    def _run(self, monkeypatch, capsys, instances, stack="v1"):
        argv = ("--stack", stack) if stack else ()
        return _run_report(monkeypatch, capsys, _probe(), beats=HEALTHY_BEATS,
                           extra_argv=argv, instances=instances)

    def test_two_stacks_are_not_a_problem_for_either_report(self, monkeypatch, capsys):
        for stack in ("v1", "v2"):
            code, out = self._run(monkeypatch, capsys,
                                  [("i-aaa", "v1"), ("i-bbb", "v2")], stack)
            assert code == 0, out
            assert "expected exactly 1 running" not in out
            assert "no running instance tagged" not in out
            assert "Instances **1**" in out, "it must count ITS OWN stack"

    def test_two_instances_in_ONE_stack_still_needs_a_human(self, monkeypatch, capsys):
        code, out = self._run(monkeypatch, capsys,
                              [("i-aaa", "v1"), ("i-bbb", "v1")])
        assert code == 1
        assert "expected exactly 1 running instance in stack v1" in out

    def test_a_missing_stack_needs_a_human(self, monkeypatch, capsys):
        """The host this report is about is not running at all."""
        code, out = self._run(monkeypatch, capsys, [("i-bbb", "v2")])
        assert code == 1
        assert "no running instance tagged Stack=v1" in out

    def test_no_instances_at_all_needs_a_human(self, monkeypatch, capsys):
        code, out = self._run(monkeypatch, capsys, [])
        assert code == 1
        assert "no running bot instance found" in out

    def test_each_stack_only_escalates_on_its_own_alarms(self, monkeypatch, capsys):
        """Both stacks share the deltabt-paper- prefix, so an unfiltered read
        made ONE host's alarm fail BOTH reports.

        v1 kept the unsuffixed names, so it cannot be selected by prefix -- it
        is "everything that is not another stack's", which this pins down.
        """
        alarms = [("deltabt-paper-v2-restart-loop", "ALARM"),
                  ("deltabt-paper-bot-silent", "OK")]
        both = [("i-aaa", "v1"), ("i-bbb", "v2")]

        code, out = _run_report(monkeypatch, capsys, _probe(),
                                beats=HEALTHY_BEATS, extra_argv=("--stack", "v2"),
                                instances=both, alarms=alarms)
        assert code == 1 and "deltabt-paper-v2-restart-loop" in out

        code, out = _run_report(monkeypatch, capsys, _probe(),
                                beats=HEALTHY_BEATS, extra_argv=("--stack", "v1"),
                                instances=both, alarms=alarms)
        assert code == 0, "v1 must not fail on v2's alarm"
        assert "deltabt-paper-v2-restart-loop" not in out

    def test_v1_still_sees_its_own_unsuffixed_alarm(self, monkeypatch, capsys):
        """The negative control for the 'not another stack's' rule."""
        code, out = _run_report(
            monkeypatch, capsys, _probe(), beats=HEALTHY_BEATS,
            extra_argv=("--stack", "v1"),
            instances=[("i-aaa", "v1"), ("i-bbb", "v2")],
            alarms=[("deltabt-paper-bot-silent", "ALARM")])
        assert code == 1
        assert "deltabt-paper-bot-silent" in out

    def test_without_a_stack_the_old_single_host_rule_holds(self, monkeypatch, capsys):
        """Unstacked deployments must not silently lose the check."""
        code, out = self._run(monkeypatch, capsys,
                              [("i-aaa", None), ("i-bbb", None)], stack=None)
        assert code == 1
        assert "expected exactly 1 running bot instance" in out


class TestTheOpenPositionCapComesFromTheExperiment:
    """It was hardcoded to 1.

    The first day the cap was raised to six, the report called two open
    positions a control failure:

        2 positions open at once; the frozen configuration allows
        max_open_positions=1

    That is the configuration under test being reported as the fault -- the
    same shape as "four symbols configured" blocking a six-symbol universe.
    The experiment records its own risk snapshot, and anything else is a second
    copy of the configuration that can disagree with the one actually running.
    """

    @staticmethod
    def _make(max_open, n_open):
        exp = {"experiment_id": "E", "status": "RUNNING",
               "started_at": "2026-08-13T19:41:15", "planned_days": 30}
        if max_open is not None:
            exp["risk"] = {"max_open_positions": max_open}
        positions = [{"symbol": f"SYM{i}", "side": "LONG", "status": "OPEN",
                      "quantity": 1,
                      "opened_ist": "2026-08-14 10:00:00", "entry": 100.0,
                      "stop": 99.0, "target": 102.0, "current_price": 100.5,
                      "r": 0.5, "unrealized_pnl": 1.0}
                     for i in range(n_open)]
        return _probe_with(positions, experiments=[exp])

    def test_six_open_under_a_cap_of_six_is_not_a_problem(self, monkeypatch, capsys):
        code, out = _run_report(monkeypatch, capsys, self._make(6, 6),
                                beats=HEALTHY_BEATS)
        assert "positions open at once" not in out
        assert code == 0, out

    def test_seven_open_under_a_cap_of_six_still_needs_a_human(self, monkeypatch, capsys):
        """The check must not have been weakened into a no-op."""
        code, out = _run_report(monkeypatch, capsys, self._make(6, 7),
                                beats=HEALTHY_BEATS)
        assert code == 1
        assert "7 positions open at once" in out
        assert "max_open_positions=6" in out

    def test_two_open_under_a_cap_of_one_still_needs_a_human(self, monkeypatch, capsys):
        """The original case, which must survive the generalisation."""
        code, out = _run_report(monkeypatch, capsys, self._make(1, 2),
                                beats=HEALTHY_BEATS)
        assert code == 1
        assert "2 positions open at once" in out
        assert "max_open_positions=1" in out

    def test_a_missing_snapshot_is_a_note_not_a_guess(self, monkeypatch, capsys):
        """Older rows predate the column. Unknown must not become assumed."""
        code, out = _run_report(monkeypatch, capsys, self._make(None, 3),
                                beats=HEALTHY_BEATS)
        assert code == 0
        assert "not checked" in out
        assert "positions open at once" not in out


class TestABrandNewInstanceIsNotABlindAlarm:
    """The check failed the very apply that created the instance.

    EC2 does not publish StatusCheckFailed for the first few minutes of an
    instance's life, so `alarms_watch_real_metrics` reported the new host's
    alarm as pointed at a dimension with no data -- three times on 2026-08-15,
    once per stack. The apply had already succeeded each time, so the red run
    reported a misconfiguration that did not exist while saying nothing about
    the one the check is built to catch.

    "No datapoints yet" and "no datapoints ever" are different claims.
    """

    @staticmethod
    def _ctx(preflight):
        return preflight.Context(region="ap-south-1", environment="paper",
                                 expected_account="1", state_bucket="b",
                                 ecr_repository="deltabt")

    def _stub(self, monkeypatch, preflight, age_seconds, datapoints):
        import datetime
        launched = (datetime.datetime.now(datetime.timezone.utc)
                    - datetime.timedelta(seconds=age_seconds))

        def fake(ctx, *args, global_service=False):
            head = tuple(args[:2])
            if head == ("cloudwatch", "describe-alarms"):
                return True, {"MetricAlarms": [{
                    "AlarmName": "deltabt-paper-v3-instance-status",
                    "Namespace": "AWS/EC2", "MetricName": "StatusCheckFailed",
                    "Dimensions": [{"Name": "InstanceId", "Value": "i-new"}]}]}
            if head == ("ec2", "describe-instances"):
                return True, {"Reservations": [{"Instances": [
                    {"InstanceId": "i-new", "LaunchTime": launched.isoformat()}]}]}
            if head == ("cloudwatch", "get-metric-statistics"):
                return True, {"Datapoints": datapoints}
            raise AssertionError(args)

        monkeypatch.setattr(preflight, "aws", fake)

    def test_a_young_instance_with_no_datapoints_passes(self, monkeypatch):
        preflight = load("aws_preflight")
        self._stub(monkeypatch, preflight, age_seconds=90, datapoints=[])
        r = preflight.check_alarms_watch_real_metrics(self._ctx(preflight))
        assert r.status == preflight.PASS, r.detail
        assert "warming" in r.detail, "the fact must still be reported"

    def test_an_old_instance_with_no_datapoints_still_fails(self, monkeypatch):
        """THE NEGATIVE CONTROL. The blind-alarm bug this check exists for --
        three RDS alarms built from the wrong id -- must still be caught."""
        preflight = load("aws_preflight")
        self._stub(monkeypatch, preflight, age_seconds=6 * 3600, datapoints=[])
        r = preflight.check_alarms_watch_real_metrics(self._ctx(preflight))
        assert r.status == preflight.FAIL
        assert "NO datapoints" in r.detail

    def test_a_young_instance_with_data_is_simply_fine(self, monkeypatch):
        preflight = load("aws_preflight")
        self._stub(monkeypatch, preflight, age_seconds=90,
                   datapoints=[{"Maximum": 0.0}])
        r = preflight.check_alarms_watch_real_metrics(self._ctx(preflight))
        assert r.status == preflight.PASS
        assert "warming" not in r.detail

    def test_the_threshold_is_the_stated_one(self):
        preflight = load("aws_preflight")
        assert preflight.MIN_METRIC_AGE == 900.0


class TestTheReportPrintsWhatTheRunIsMeasuring:
    """Every column below was recorded from the first run and none of it was
    reported, so the questions the forward test exists to answer had to be
    settled by querying the database by hand.
    """

    @staticmethod
    def _econ(**kw):
        base = {"symbol": "BANKUSD", "side": 1, "r_multiple": 1.872,
                "planned_r": 2.0, "fill_rr": 1.81, "notional": 4000.0,
                "entry_fee": 2.36, "exit_fee": 2.44, "funding": 0.5,
                "entry_slippage": 0.8, "exit_slippage": 0.9,
                "realized_pnl": 93.49, "exit_reason": "TAKE_PROFIT",
                "hold_seconds": 11431}
        base.update(kw)
        return base

    def test_cost_per_r_is_reported(self, monkeypatch, capsys):
        """The panel's binding constraint, which the report never printed."""
        code, out = _run_report(monkeypatch, capsys,
                                _probe(economics=[self._econ()]),
                                beats=HEALTHY_BEATS)
        assert "## Cost, and what it leaves" in out
        assert "cost/R" in out
        # 1R = |93.49| / 1.872 = 49.94; cost = 2.36+2.44+0.5+0.8+0.9 = 7.00
        assert "0.140R" in out, out

    def test_planned_versus_filled_degradation_is_reported(self, monkeypatch, capsys):
        """schema.sql: 'reporting only one hides the degradation the forward
        test exists to measure' -- and the report showed neither."""
        code, out = _run_report(monkeypatch, capsys,
                                _probe(economics=[self._econ()]),
                                beats=HEALTHY_BEATS)
        assert "Approved-to-filled reward/risk moved **-0.190**" in out

    def test_drawdown_is_reported_and_escalates_with_the_gate_off(
            self, monkeypatch, capsys):
        """max_drawdown_pct is disabled for these runs, so nothing enforces it."""
        code, out = _run_report(
            monkeypatch, capsys,
            _probe(risk_state={"equity": 8500.0, "peak_equity": 10000.0,
                               "wins": 1, "losses": 4, "consecutive_losses": 2}),
            beats=HEALTHY_BEATS)
        assert "## Equity" in out and "15.00%" in out
        assert code == 1
        assert "DISABLED for this run" in out

    def test_a_modest_drawdown_is_reported_without_escalating(
            self, monkeypatch, capsys):
        code, out = _run_report(
            monkeypatch, capsys,
            _probe(risk_state={"equity": 9800.0, "peak_equity": 10000.0,
                               "wins": 1, "losses": 1, "consecutive_losses": 0}),
            beats=HEALTHY_BEATS)
        assert "2.00%" in out
        assert code == 0

    def test_a_symbol_that_never_trades_is_named(self, monkeypatch, capsys):
        """THE CASE THAT PROMPTED THIS. AKEUSD and BEATUSD had every setup
        refused for stop width and the aggregates showed nothing."""
        code, out = _run_report(
            monkeypatch, capsys,
            _probe(by_symbol=[
                {"symbol": "AKEUSD", "setups": 8, "approved": 0, "rejected": 8,
                 "min_stop_pct": 5.06, "max_stop_pct": 11.44},
                {"symbol": "BTCUSD", "setups": 4, "approved": 2, "rejected": 2,
                 "min_stop_pct": 0.18, "max_stop_pct": 0.38}],
                bar_quality=[{"symbol": "AKEUSD", "bars": 1440, "synthetic": 0},
                             {"symbol": "BTCUSD", "bars": 1440, "synthetic": 14}]),
            beats=HEALTHY_BEATS)
        assert "## Per symbol" in out
        assert "5.06–11.44" in out
        assert "no setup has ever been approved for AKEUSD" in out
        assert "BTCUSD" in out
        assert code == 0, "a symbol not trading is a finding, not a fault"

    def test_a_stale_open_position_escalates(self, monkeypatch, capsys):
        """There is no time stop: only STOP_LOSS and TAKE_PROFIT close."""
        code, out = _run_report(monkeypatch, capsys,
                                _probe(oldest_open_seconds=5 * 86400),
                                beats=HEALTHY_BEATS)
        assert "Oldest open position" in out
        assert code == 1
        assert "no time stop" in out

    def test_a_normal_open_position_does_not_escalate(self, monkeypatch, capsys):
        code, out = _run_report(monkeypatch, capsys,
                                _probe(oldest_open_seconds=3600),
                                beats=HEALTHY_BEATS)
        assert "Oldest open position: **1.0h**" in out
        assert code == 0

    def test_rejections_are_shown_even_when_orders_exist(self, monkeypatch, capsys):
        """The section was gated on 'no orders', which is how a symbol refused
        on every single setup stayed invisible."""
        code, out = _run_report(
            monkeypatch, capsys,
            _probe(orders_run=4, fills_run=4, paper_orders=4, paper_fills=4,
                   rejections_24h={"stop 20.95% exceeds max_stop_pct 5.00%": 7}),
            beats=HEALTHY_BEATS)
        assert "## Why setups did not become trades" in out
        assert "exceeds max_stop_pct" in out


class TestPostgresNumericsSurviveTheProbe:
    """db_probe serialises with json.dumps(default=str), so every NUMERIC
    column arrives as a string.

    num() accepted only int/float, so the entire cost table rendered as "—"
    and 0.00 against data that was fully populated -- entry_fee 2.21993942,
    r_multiple 1.872476. A zero there reads as measured-and-negligible rather
    than not-read, which is worse than omitting the column.
    """

    def test_numeric_strings_parse(self):
        mod = _report()
        assert mod.num("2.21993942") == 2.21993942
        assert mod.num("1.872476") == 1.872476
        assert mod.num("-0.5") == -0.5

    def test_absent_stays_absent(self):
        """The reason num() exists: a missing field must not render as 0.0."""
        mod = _report()
        for v in (None, "", "TAKE_PROFIT", [], {}):
            assert mod.num(v) is None, v
        assert mod.fmt(None) == "—"

    def test_the_cost_table_reads_stringified_numerics(self, monkeypatch, capsys):
        """End to end, in the shape the probe actually emits."""
        econ = [{"symbol": "BANKUSD", "side": 1, "r_multiple": "1.872476",
                 "planned_r": None, "fill_rr": None, "notional": "2810.04989760",
                 "entry_fee": "2.21993942", "exit_fee": "0.68634086",
                 "funding": "1.77526566", "entry_slippage": "0.00000000",
                 "exit_slippage": "0.00000000", "realized_pnl": "93.49255646",
                 "exit_reason": "TAKE_PROFIT", "hold_seconds": None}]
        code, out = _run_report(monkeypatch, capsys, _probe(economics=econ),
                                beats=HEALTHY_BEATS)
        assert "+1.872" in out, "r_multiple must render"
        assert "4.68" in out, "fees must sum, not read as zero"
        assert "| 0.00 | 0.00 | 0.00 |" not in out

    def test_the_per_symbol_stop_range_reads_them_too(self, monkeypatch, capsys):
        code, out = _run_report(
            monkeypatch, capsys,
            _probe(by_symbol=[{"symbol": "AKEUSD", "setups": 15, "approved": 0,
                               "rejected": 15, "min_stop_pct": "5.06",
                               "max_stop_pct": "11.44"}]),
            beats=HEALTHY_BEATS)
        assert "5.06–11.44" in out


class TestTheReportVerdictReactsToErrors:
    def test_an_application_error_needs_a_human(self, monkeypatch, capsys):
        code, out = _run_report(
            monkeypatch, capsys, _probe(),
            errors=[_err("2026-08-13T21:00:00.000Z", "app.runtime.bot",
                         "bar loop error (consecutive=3)")],
            beats=HEALTHY_BEATS)
        assert code == 1, "an application error must set the exit code"
        assert "NEEDS ATTENTION" in out
        assert "app.runtime.bot" in out

    def test_but_hourly_feed_reconnects_do_not(self, monkeypatch, capsys):
        """The negative control. These are the errors production actually emits."""
        code, out = _run_report(
            monkeypatch, capsys, _probe(),
            errors=[_err(f"2026-08-13T2{h}:20:00.000Z", "app.market_data.delta_ws",
                         "feed error: no close frame received or sent")
                    for h in range(3)],
            beats=HEALTHY_BEATS)
        assert code == 0, "a recovered reconnect must not page anyone"
        assert "All clear" in out
        assert "3 feed reconnect" in out

    def test_a_feed_that_never_came_back_does_need_a_human(self, monkeypatch, capsys):
        """Same feed errors, but the heartbeat proves data actually stopped."""
        mod = _report()
        code, out = _run_report(
            monkeypatch, capsys, _probe(),
            errors=[_err("2026-08-13T21:20:00.000Z", "app.market_data.delta_ws",
                         "feed error")],
            beats=HEALTHY_BEATS + [_beat("2026-08-13T23:00:00.000Z",
                                         mod.MAX_FEED_SILENCE + 1)])
        assert code == 1
        assert "feed silence reached" in out

    def test_errors_from_a_retired_container_are_not_todays_problem(
            self, monkeypatch, capsys):
        """Shutdown noise from the container a restart replaced must not alarm."""
        code, out = _run_report(
            monkeypatch, capsys, _probe(started="2026-08-13T19:42:44.492000000Z"),
            errors=[_err("2026-08-13T19:42:38.119Z", "uvicorn.error", "Traceback"),
                    _err("2026-08-13T19:05:00.622Z", "app.runtime.bot", "bar loop")],
            beats=HEALTHY_BEATS)
        assert code == 0, "a dead container's last words are not a live fault"
        assert "retired container" in out

    def test_and_the_same_errors_after_the_restart_do_alarm(self, monkeypatch, capsys):
        """Identical lines, moved past the start time: the attribution is real."""
        code, _ = _run_report(
            monkeypatch, capsys, _probe(started="2026-08-13T19:00:00.000000000Z"),
            errors=[_err("2026-08-13T19:42:38.119Z", "uvicorn.error", "Traceback"),
                    _err("2026-08-13T19:05:00.622Z", "app.runtime.bot", "bar loop")],
            beats=HEALTHY_BEATS)
        assert code == 1

    def test_a_silent_bar_loop_is_a_problem(self, monkeypatch, capsys):
        """No heartbeats at all means feed health cannot be judged -- not that it passed."""
        code, out = _run_report(monkeypatch, capsys, _probe(), errors=[], beats=[])
        assert code == 1
        assert "no heartbeat lines" in out

    def test_a_clean_day_still_passes(self, monkeypatch, capsys):
        code, out = _run_report(monkeypatch, capsys, _probe(),
                                errors=[], beats=HEALTHY_BEATS)
        assert code == 0
        assert "All clear" in out


class TestTheReportNeverSilentlyTruncates:
    def test_log_queries_follow_the_next_token(self):
        """One page read as a total under-reports worst when things go wrong."""
        mod = _report()
        mod.problems.clear()
        pages = [{"events": [{"message": json.dumps({"ts": "1", "message": "a"})}],
                  "nextToken": "t2"},
                 {"events": [{"message": json.dumps({"ts": "2", "message": "b"})}]}]
        calls: list[tuple] = []

        def fake_aws(*args, region):
            calls.append(args)
            return True, pages[len(calls) - 1]

        mod.aws = fake_aws
        try:
            events, truncated = mod.log_events("g", 0, "p", "ap-south-1")
        finally:
            mod.aws = mod.__dict__["aws"]
        assert len(events) == 2, "the second page was dropped"
        assert truncated is False
        assert "--next-token" in calls[1]

    def test_hitting_the_cap_is_reported_rather_than_hidden(self):
        mod = _report()
        mod.problems.clear()
        page = {"events": [{"message": json.dumps({"ts": "1", "message": "x"})}] * 50,
                "nextToken": "more"}
        mod.aws = lambda *a, region: (True, page)
        events, truncated = mod.log_events("g", 0, "p", "ap-south-1")
        assert truncated is True
        assert len(events) == mod.LOG_EVENT_CAP

    def test_no_log_query_passes_a_bare_limit(self):
        """--limit caps the page; the count then reads as a total. Use the pager."""
        source = _report_source()
        assert '"--limit"' not in source, (
            "a --limit on filter-log-events silently truncates the count")


def _report_source():
    return "\n".join(l for l in (SCRIPTS / "daily_report.py").read_text().splitlines()
                     if not l.lstrip().startswith("#"))


class TestTheReportStaysInSyncWithTheBot:
    def test_the_silence_threshold_matches_the_client(self):
        """Duplicated because CI runs the script without the app installed."""
        from app.config.settings import MAX_WS_SILENCE
        assert _report().MAX_WS_SILENCE == MAX_WS_SILENCE

    def test_escalation_is_above_the_clients_own_reconnect_trigger(self):
        mod = _report()
        assert mod.MAX_FEED_SILENCE > mod.MAX_WS_SILENCE, (
            "escalating at or below the reconnect trigger fires on every reconnect")

    def test_the_probe_reports_container_start_time(self):
        """Without it the report cannot tell shutdown noise from a live fault."""
        tf = (ROOT / "infra" / "terraform" / "monitoring.tf").read_text()
        assert "State.StartedAt" in tf

    def test_docker_nanosecond_precision_parses(self):
        """docker emits 9 fractional digits; datetime accepts at most 6."""
        mod = _report()
        parsed = mod.parse_ts("2026-08-13T19:42:44.492000000Z")
        assert parsed is not None and parsed.year == 2026
        assert mod.parse_ts("not a time") is None

    def test_the_start_time_is_read_out_of_the_probe(self):
        mod = _report()
        sec = mod.sections(_probe(started="2026-08-13T19:42:44.492000000Z"))
        assert mod.container_started(sec) is not None
        assert mod.container_started({"CONTAINER": "user=x readonly=true"}) is None


# ===========================================================================
# Trade detail in the report
#
# Aggregates hide the thing worth reading. The panel's objection to the whole
# strategy was cost-per-R: a 21.6 bps median 1R leaves round-trip costs eating
# 0.36-0.71R of a 2R target. A report that prints P&L but not 1R-in-bps omits
# the number actually under test, so the per-trade table carries it.
#
# The bracket and stop checks are risk-control assertions, not commentary: a
# long whose stop sits above entry, or a position still open well past -1R,
# is a control failure however healthy the process looks.
# ===========================================================================

def _pos(symbol="SOLUSD", side="LONG", entry=76.3432656, stop=75.586,
         target=77.812, current=75.85, r=-0.651, status="OPEN", qty=65):
    return {"symbol": symbol, "side": side, "status": status, "quantity": qty,
            "entry": entry, "stop": stop, "target": target,
            "current_price": current, "unrealized_pnl": -32.06, "r": r,
            "opened_ist": "2026-08-14 03:40:07 IST"}


def _probe_with(positions=(), trades=(), **kw):
    text = _probe(**kw)
    return text.replace(
        "===HEALTHZ===",
        "===POSITIONS===\n" + json.dumps(list(positions)) +
        "\n===TRADES===\n" + json.dumps(list(trades)) + "\n===HEALTHZ===")


class TestTheReportShowsTheTrades:
    def test_an_open_position_is_shown_with_its_geometry(self, monkeypatch, capsys):
        code, out = _run_report(monkeypatch, capsys,
                                _probe_with(positions=[_pos()]), beats=HEALTHY_BEATS)
        assert code == 0
        assert "SOLUSD" in out and "-0.651R" in out
        # 1R = 0.7573 on a 76.343 entry = 99.2 bps, and the target sits at 1.94R.
        assert "99.2" in out, "1R in basis points is the number under test"
        assert "1.94R" in out

    def test_closed_trades_are_summarised_in_r(self, monkeypatch, capsys):
        closed = [dict(_pos(status="CLOSED"), exit=77.812, pnl=95.5, r=1.94,
                       reason="target", closed_ist="2026-08-14 05:00:00 IST"),
                  dict(_pos(status="CLOSED", symbol="BTCUSD"), exit=75.586,
                       pnl=-49.2, r=-1.0, reason="stop",
                       closed_ist="2026-08-14 06:00:00 IST")]
        code, out = _run_report(monkeypatch, capsys,
                                _probe_with(trades=closed), beats=HEALTHY_BEATS)
        assert code == 0
        assert "won **1**" in out
        assert "+0.94R" in out, "total R across the closed trades"

    def test_a_report_with_no_trades_at_all_still_renders(self, monkeypatch, capsys):
        code, out = _run_report(monkeypatch, capsys, _probe_with(),
                                beats=HEALTHY_BEATS)
        assert code == 0
        assert "### Open" not in out and "### Closed" not in out


class TestTheTradeTableIsARiskCheck:
    def test_an_inverted_long_bracket_needs_a_human(self, monkeypatch, capsys):
        """Stop above entry on a long is a broken bracket, not a bad trade."""
        code, out = _run_report(
            monkeypatch, capsys,
            _probe_with(positions=[_pos(entry=76.0, stop=77.0, target=78.0, r=0.1)]),
            beats=HEALTHY_BEATS)
        assert code == 1
        assert "bracket is inverted" in out

    def test_a_correct_short_bracket_stays_quiet(self, monkeypatch, capsys):
        """The negative control: a short's stop is ABOVE entry by design."""
        code, _ = _run_report(
            monkeypatch, capsys,
            _probe_with(positions=[_pos(side="SHORT", entry=76.0, stop=77.0,
                                        target=74.0, r=-0.2)]),
            beats=HEALTHY_BEATS)
        assert code == 0, "a short is not an inverted long"

    def test_a_position_past_minus_one_r_needs_a_human(self, monkeypatch, capsys):
        """Past -1R with the position open means the stop did not fire."""
        code, out = _run_report(monkeypatch, capsys,
                                _probe_with(positions=[_pos(r=-1.4)]),
                                beats=HEALTHY_BEATS)
        assert code == 1
        assert "stop should have triggered" in out

    def test_a_small_overshoot_is_tolerated(self, monkeypatch, capsys):
        """Stops fire on MARK price; this quote is last-traded. -1.02R is noise."""
        code, _ = _run_report(monkeypatch, capsys,
                              _probe_with(positions=[_pos(r=-1.02)]),
                              beats=HEALTHY_BEATS)
        assert code == 0

    #: The cap now comes from the experiment's own risk snapshot rather than a
    #: constant in the report, so a test about breaching it has to say what the
    #: experiment allowed. See TestTheOpenPositionCapComesFromTheExperiment.
    CAPPED_AT_ONE = [{"experiment_id": "H-WPR-1-PAPER-AWS-20260813",
                      "status": "RUNNING", "started_at": "2026-08-13T19:41:15",
                      "planned_days": 30,
                      "risk": {"max_open_positions": 1}}]

    def test_two_open_positions_break_the_frozen_risk_cap(self, monkeypatch, capsys):
        code, out = _run_report(
            monkeypatch, capsys,
            _probe_with(positions=[_pos(), _pos(symbol="BTCUSD")],
                        experiments=self.CAPPED_AT_ONE),
            beats=HEALTHY_BEATS)
        assert code == 1
        assert "max_open_positions=1" in out

    def test_one_open_position_does_not(self, monkeypatch, capsys):
        code, _ = _run_report(monkeypatch, capsys,
                              _probe_with(positions=[_pos()]), beats=HEALTHY_BEATS)
        assert code == 0


class TestTheTestWorkflowActuallyRuns:
    def test_the_reusable_call_does_not_cancel_the_standalone_run(self):
        """Keyed on the ref alone, deploy's nested copy killed the push run.

        github.workflow is the CALLER's name, so it separates the two.
        """
        text = (ROOT / ".github" / "workflows" / "test.yml").read_text()
        assert "group: test-${{ github.workflow }}-${{ github.ref }}" in text, (
            "test.yml's concurrency group must distinguish the reusable call "
            "from the push-triggered run, or one silently cancels the other")


class TestTheReportShowsEntryTimeInIST:
    def test_an_open_position_shows_when_it_was_entered(self, monkeypatch, capsys):
        code, out = _run_report(monkeypatch, capsys,
                                _probe_with(positions=[_pos()]), beats=HEALTHY_BEATS)
        assert code == 0
        assert "Entered (IST)" in out
        assert "2026-08-14 03:40:07" in out, "the entry timestamp must be shown"

    def test_a_closed_trade_shows_entry_exit_and_duration(self, monkeypatch, capsys):
        closed = [dict(_pos(status="CLOSED"), exit=77.812, pnl=95.5, r=1.94,
                       reason="target", closed_ist="2026-08-14 05:10:07 IST")]
        code, out = _run_report(monkeypatch, capsys,
                                _probe_with(trades=closed), beats=HEALTHY_BEATS)
        assert code == 0
        assert "2026-08-14 03:40:07" in out and "2026-08-14 05:10:07" in out
        assert "1h30" in out, "held duration derived from the two IST stamps"

    def test_a_missing_timestamp_renders_as_a_dash_not_a_crash(self, monkeypatch, capsys):
        p = _pos(); p.pop("opened_ist")
        code, out = _run_report(monkeypatch, capsys, _probe_with(positions=[p]),
                                beats=HEALTHY_BEATS)
        assert code == 0 and "Entered (IST)" in out

    def test_the_duration_helper_rejects_unparseable_input(self):
        mod = _report()
        assert mod.held("nonsense", "also nonsense") == "—"
        assert mod.held("2026-08-14 03:00:00 IST", "2026-08-14 03:45:00 IST") == "45m"
        assert mod.held("2026-08-14 03:00:00 IST", "2026-08-14 09:30:00 IST") == "6h30"
