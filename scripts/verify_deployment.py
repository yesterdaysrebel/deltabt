#!/usr/bin/env python3
"""Verify a deployment actually landed. Fails closed.

Runs after the image is rolled. Everything here is READ-ONLY -- it inspects,
it does not fix, and it deliberately cannot start anything.

THE DISTINCTION THIS SCRIPT EXISTS TO PRESERVE:

    A successful deployment is not successful trading.

    All thirteen checks passing means the right code is running, connected,
    and being watched. It says nothing about whether the strategy works, and
    it does not create an experiment or start paper trading. Starting the
    forward test remains a separate, deliberate human action.

Usage:
    python scripts/verify_deployment.py --instance-id i-... --expected-sha <git sha>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

FROZEN_STRATEGY_HASH = "5a5412369f3823f3"

PASS, FAIL = "PASS", "FAIL"


def aws(*args: str, region: str) -> tuple[bool, dict]:
    try:
        proc = subprocess.run(["aws", *args, "--region", region, "--output", "json"],
                              capture_output=True, text=True, timeout=120)
    except Exception as exc:                                  # noqa: BLE001
        return False, {"error": str(exc)}
    if proc.returncode != 0:
        return False, {"error": (proc.stderr or "failed").strip().splitlines()[-1]}
    try:
        return True, json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        return False, {"error": str(exc)}


def on_host(instance_id: str, region: str, commands: list[str]) -> tuple[bool, str]:
    """Run commands on the instance via SSM. There is no SSH path, by design."""
    ok, sent = aws("ssm", "send-command",
                   "--document-name", "AWS-RunShellScript",
                   "--instance-ids", instance_id,
                   "--parameters", json.dumps({"commands": commands}),
                   "--comment", "deployment verification (read-only)",
                   region=region)
    if not ok:
        return False, f"could not send the command: {sent.get('error')}"
    command_id = sent["Command"]["CommandId"]

    for _ in range(30):
        time.sleep(5)
        ok, inv = aws("ssm", "get-command-invocation", "--command-id", command_id,
                      "--instance-id", instance_id, region=region)
        if not ok:
            continue
        if inv.get("Status") in ("Success", "Failed", "TimedOut", "Cancelled"):
            body = inv.get("StandardOutputContent", "")
            if inv["Status"] != "Success":
                return False, (inv.get("StandardErrorContent") or body or
                               inv["Status"])
            return True, body
    return False, "timed out waiting for the SSM invocation"


#: One round trip collects everything the host can answer, because thirteen
#: separate SSM invocations would take minutes and could observe thirteen
#: different moments.
HOST_PROBE = [
    "set +e",
    "echo '===CONTAINERS==='",
    "docker ps --filter name=deltabot --format '{{.Names}}\\t{{.Image}}\\t{{.Status}}'",
    "echo '===ALLCONTAINERS==='",
    "docker ps -q | wc -l",
    "echo '===HEALTHZ==='",
    "curl -fsS --max-time 10 http://127.0.0.1:8000/healthz",
    "echo",
    "echo '===READYZ==='",
    "curl -fsS --max-time 10 http://127.0.0.1:8000/readyz",
    "echo",
    "echo '===STATUS==='",
    "curl -fsS --max-time 10 http://127.0.0.1:8000/api/status",
    "echo",
    "echo '===EXPERIMENT==='",
    # Read-only. Reports whether an experiment is bound; creates nothing.
    "docker exec deltabot python -m app forward-test status 2>&1 | head -40",
    "echo '===END==='",
]


def parse_sections(output: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("===") and stripped.endswith("==="):
            if current:
                sections[current] = "\n".join(buf).strip()
            current, buf = stripped.strip("="), []
            continue
        if current:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf).strip()
    return sections


def as_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:                                          # noqa: BLE001
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-id", required=True)
    ap.add_argument("--region", default="ap-south-1")
    ap.add_argument("--environment", default="paper")
    ap.add_argument("--expected-sha", required=True,
                    help="the git SHA this deploy intended to run")
    ap.add_argument("--log-group", default=None)
    args = ap.parse_args()

    name = f"deltabt-{args.environment}"
    log_group = args.log_group or f"/deltabt/{args.environment}/bot"
    results: list[tuple[str, str, str]] = []

    def record(check: str, ok: bool, detail: str) -> None:
        results.append((check, PASS if ok else FAIL, detail))

    # --- 1. exactly one instance -------------------------------------------
    ok, data = aws("ec2", "describe-instances", "--filters",
                   "Name=tag:Project,Values=deltabt",
                   f"Name=tag:Environment,Values={args.environment}",
                   "Name=instance-state-name,Values=pending,running",
                   region=args.region)
    if not ok:
        record("exactly_one_instance", False, f"could not enumerate: {data.get('error')}")
    else:
        ids = [i["InstanceId"] for r in data.get("Reservations", [])
               for i in r.get("Instances", [])]
        record("exactly_one_instance", len(ids) == 1,
               f"{len(ids)} running: {', '.join(ids) or 'none'}"
               + ("" if len(ids) == 1 else
                  ". Exactly one bot may run; the advisory lock refuses a second, "
                  "so a duplicate shows up as a crash loop rather than as bad data."))

    # --- host probe --------------------------------------------------------
    ok, output = on_host(args.instance_id, args.region, HOST_PROBE)
    sections = parse_sections(output) if ok else {}
    if not ok:
        record("host_reachable_via_ssm", False, output)
    else:
        record("host_reachable_via_ssm", True, "SSM Run Command succeeded")

    # --- 2. container running ----------------------------------------------
    containers = sections.get("CONTAINERS", "")
    total = sections.get("ALLCONTAINERS", "").strip()
    running_image = ""
    if "deltabot" in containers and "Up" in containers:
        parts = containers.split("\t")
        running_image = parts[1] if len(parts) > 1 else ""
        record("container_running", total == "1",
               f"{containers.strip()}"
               + ("" if total == "1" else f" -- but {total} containers are running"))
    else:
        record("container_running", False, containers or "no deltabot container")

    healthz = as_json(sections.get("HEALTHZ", ""))
    readyz = as_json(sections.get("READYZ", ""))
    status = as_json(sections.get("STATUS", ""))
    checks = {c["name"]: c for c in healthz.get("checks", [])}

    # --- 3. readiness ------------------------------------------------------
    ready_ok = bool(readyz) and readyz.get("status") == "healthy"
    record("readyz", ready_ok,
           "ready" if ready_ok else
           f"not ready: {readyz.get('checks') or 'no response'}")

    # --- 4. health ---------------------------------------------------------
    health_ok = bool(healthz) and healthz.get("status") == "healthy"
    record("healthz", health_ok,
           "all data-freshness conditions hold" if health_ok else
           f"failing: {[c['name'] for c in healthz.get('checks', []) if not c['ok']]}")

    # --- 5..8. the individual conditions, named ----------------------------
    for check_name, label in (
            ("database_writable", "database_connectivity"),
            ("websocket_fresh", "websocket_market_data"),
            ("candles_fresh", "candle_freshness"),
            ("evaluation_loop_alive", "evaluation_loop_running"),
    ):
        entry = checks.get(check_name)
        if entry is None:
            record(label, False, f"/healthz did not report {check_name}")
        else:
            record(label, bool(entry["ok"]), entry.get("detail", ""))

    # --- 9. the silence alarm ----------------------------------------------
    ok, data = aws("cloudwatch", "describe-alarms",
                   "--alarm-names", f"{name}-bot-silent", region=args.region)
    alarms = data.get("MetricAlarms", []) if ok else []
    if not alarms:
        record("bot_silent_alarm", False,
               "the bot-silent alarm does not exist. It is the only alarm that "
               "catches a dead evaluation loop -- a dead loop logs no errors, so "
               "error-count alarms stay green through exactly that failure.")
    else:
        alarm = alarms[0]
        configured = alarm.get("TreatMissingData") == "breaching"
        record("bot_silent_alarm", configured,
               f"state={alarm.get('StateValue')} treat_missing="
               f"{alarm.get('TreatMissingData')}"
               + ("" if configured else
                  " -- must be 'breaching', or a bot that never logs stays green"))

    # --- 10. logs arriving --------------------------------------------------
    since = int((time.time() - 600) * 1000)
    ok, data = aws("logs", "filter-log-events", "--log-group-name", log_group,
                   "--start-time", str(since), "--limit", "5", region=args.region)
    if not ok:
        record("logs_arriving", False, f"{log_group}: {data.get('error')}")
    else:
        events = data.get("events", [])
        record("logs_arriving", bool(events),
               f"{len(events)} event(s) in the last 10 minutes"
               + ("" if events else
                  f" in {log_group}. The bot evaluates every symbol every bar; "
                  f"silence means it is not running."))

    # --- 11. the running image is the intended commit -----------------------
    tag = running_image.rsplit(":", 1)[-1] if ":" in running_image else ""
    record("image_matches_git_sha", tag == args.expected_sha,
           f"running '{tag or '?'}', expected '{args.expected_sha}'"
           + ("" if tag == args.expected_sha else
              ". The image tag is the only durable link between a database row "
              "and the code that produced it."))

    # --- 12. the frozen strategy hash ---------------------------------------
    actual_hash = status.get("strategy_config_hash", "")
    record("frozen_strategy_hash", actual_hash == FROZEN_STRATEGY_HASH,
           f"{actual_hash or 'unknown'}"
           + ("" if actual_hash == FROZEN_STRATEGY_HASH else
              f" != {FROZEN_STRATEGY_HASH}. A changed hash is a different "
              f"experiment wearing the same name."))

    # --- 13. no experiment exists -------------------------------------------
    experiment = sections.get("EXPERIMENT", "")
    has_experiment = bool(experiment) and not any(
        phrase in experiment.lower()
        for phrase in ("no active", "none", "not bound", "no experiment"))
    record("no_experiment_created", not has_experiment,
           "no experiment is bound -- correct after a deployment"
           if not has_experiment else
           f"AN EXPERIMENT IS ACTIVE:\n{experiment}\nDeployment must not start "
           f"one. If this is a pre-existing run, the code underneath it just "
           f"changed, which makes the results two experiments wearing one name.")

    # --- report -------------------------------------------------------------
    print("Deployment verification")
    print("=" * 72)
    for check, verdict, detail in results:
        print(f"  [{verdict}] {check:<28} {detail}")
    print("=" * 72)

    failures = [c for c, v, _ in results if v == FAIL]
    if failures:
        print(f"VERIFICATION FAILED -- {len(failures)}: {', '.join(failures)}")
        return 1

    print(f"All {len(results)} checks passed.")
    print()
    print("The deployment is live and being watched. That is ALL this means.")
    print("No experiment has been created and no paper trading has started;")
    print("starting the forward test is a separate deliberate action, and the")
    print("preflight gate has to pass first. See docs/aws_deployment.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
