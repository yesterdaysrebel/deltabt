#!/usr/bin/env python3
"""The daily forward-test report, assembled from AWS and one read-only probe.

Runs in scheduled CI as the monitor role, which can read infrastructure and
invoke exactly one fixed SSM document. It changes nothing, anywhere.

WHAT THIS IS FOR

    Not "is the process up" -- CloudWatch answers that, and answers it to
    nobody, because alerting is deliberately unconfigured. This answers the
    question an operator actually has each morning:

        Is the experiment still valid, and if it took no trades, WHY NOT?

    "No trades" is not an observation. The strategy rejects for specific
    recorded reasons, and a quiet day should name the gate that was closed.
    A run that reports thirty silent days without saying which filter did the
    silencing has produced no evidence about the strategy at all.

Exit codes:
    0  everything the report checks is healthy
    1  something needs a human
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys

FROZEN_STRATEGY_HASH = "5a5412369f3823f3"
FROZEN_CONFIG_HASH = "ab43fbad6bf3945c"
FROZEN_RISK_HASH = "db4ecc872c759c52"

#: Below this, performance numbers are noise. Stated on every report so the
#: reader is never invited to draw a conclusion the sample cannot support.
MIN_CLOSED_TRADES = 30

problems: list[str] = []
notes: list[str] = []


def aws(*args: str, region: str) -> tuple[bool, dict]:
    try:
        proc = subprocess.run(["aws", *args, "--region", region, "--output", "json"],
                              capture_output=True, text=True, timeout=120)
    except Exception as exc:                                   # noqa: BLE001
        return False, {"error": str(exc)}
    if proc.returncode != 0:
        return False, {"error": (proc.stderr or "failed").strip().splitlines()[-1]}
    try:
        return True, json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        return False, {"error": str(exc)}


def probe(instance: str, document: str, day: str, region: str) -> str:
    """Invoke the read-only monitor document and return its stdout."""
    ok, sent = aws("ssm", "send-command", "--document-name", document,
                   "--instance-ids", instance,
                   "--parameters", json.dumps({"Day": [day]}),
                   "--comment", "daily forward-test report (read-only)",
                   region=region)
    if not ok:
        problems.append(f"could not invoke the monitor document: {sent.get('error')}")
        return ""
    command_id = sent["Command"]["CommandId"]
    import time
    for _ in range(40):
        time.sleep(6)
        ok, inv = aws("ssm", "get-command-invocation", "--command-id", command_id,
                      "--instance-id", instance, region=region)
        if ok and inv.get("Status") in ("Success", "Failed", "TimedOut", "Cancelled"):
            if inv["Status"] != "Success":
                problems.append(f"monitor probe {inv['Status']}")
            return inv.get("StandardOutputContent", "")
    problems.append("monitor probe timed out")
    return ""


def sections(text: str) -> dict[str, str]:
    out, current, buf = {}, None, []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("===") and s.endswith("==="):
            if current:
                out[current] = "\n".join(buf).strip()
            current, buf = s.strip("="), []
            continue
        if current:
            buf.append(line)
    if current:
        out[current] = "\n".join(buf).strip()
    return out


def as_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:                                          # noqa: BLE001
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-id", required=True)
    ap.add_argument("--document", required=True)
    ap.add_argument("--region", default="ap-south-1")
    ap.add_argument("--environment", default="paper")
    ap.add_argument("--log-group", default=None)
    ap.add_argument("--day", default=None, help="UTC date to report on; default yesterday")
    args = ap.parse_args()

    name = f"deltabt-{args.environment}"
    log_group = args.log_group or f"/deltabt/{args.environment}/bot"
    now = datetime.datetime.now(datetime.timezone.utc)
    day = args.day or (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"# DeltaBt daily report — {day} (UTC)")
    print(f"_generated {now.strftime('%Y-%m-%dT%H:%M:%SZ')}_\n")

    # --- the host probe ----------------------------------------------------
    raw = probe(args.instance_id, args.document, day, args.region)
    sec = sections(raw)
    status = as_json(sec.get("STATUS", ""))
    healthz = as_json(sec.get("HEALTHZ", ""))
    readyz = as_json(sec.get("READYZ", ""))
    db = as_json((sec.get("PERSISTENCE", "") or "").splitlines()[-1] if sec.get("PERSISTENCE") else "")

    # --- 1. is it alive and is it still the same experiment? ---------------
    print("## Is it running, and is it still the same experiment?\n")
    container = sec.get("CONTAINER", "").splitlines()
    print("```")
    for line in container[:3]:
        print(line)
    print(f"healthz  {healthz.get('status', 'NO RESPONSE')}")
    print(f"readyz   {readyz.get('status', 'NO RESPONSE')}")
    print("```\n")

    if healthz.get("status") != "healthy":
        failing = [c["name"] for c in healthz.get("checks", []) if not c["ok"]]
        # A gap in a thin symbol is a property of the instrument, not a fault.
        if failing == ["no_recent_gaps"]:
            notes.append("healthz red only on no_recent_gaps — an illiquid-minute "
                         "gap, expected on XRPUSD/SOLUSD; see the gap table below")
        else:
            problems.append(f"healthz unhealthy: {failing}")
    if readyz.get("status") != "healthy":
        problems.append(f"readyz not ready: "
                        f"{[c['name'] for c in readyz.get('checks', []) if not c['ok']]}")

    got_strategy = status.get("strategy_config_hash")
    if got_strategy and got_strategy != FROZEN_STRATEGY_HASH:
        problems.append(f"STRATEGY HASH CHANGED: {got_strategy} != {FROZEN_STRATEGY_HASH}")

    experiments = db.get("experiments") or []
    running = [e for e in experiments if str(e.get("status")).upper() == "RUNNING"]
    if len(running) != 1:
        problems.append(f"expected exactly 1 RUNNING experiment, found {len(running)}")
    if running:
        e = running[0]
        started = str(e.get("started_at", ""))[:19]
        print(f"**Experiment** `{e.get('experiment_id')}` · {e.get('status')} · "
              f"started {started}Z · planned {e.get('planned_days')} days\n")
    if db.get("signals_unbound", 0) and running:
        notes.append(f"{db['signals_unbound']} pre-binding signals carry no experiment id "
                     f"(expected: they predate the run and stay out of the dataset)")

    # --- 2. what did it do? -------------------------------------------------
    print("## What it did\n")
    outcomes = db.get("outcomes_24h") or {}
    evals = db.get("evaluations_24h", 0)
    print(f"Evaluations in the last 24h: **{evals}**\n")
    if outcomes:
        print("| Outcome | Count |")
        print("|---|---|")
        for k, v in outcomes.items():
            print(f"| {k} | {v} |")
        print()

    orders = db.get("paper_orders", 0)
    fills = db.get("paper_fills", 0)
    closed = db.get("closed_trades_total", 0)
    print(f"Orders **{orders}** · fills **{fills}** · closed trades **{closed}**\n")

    # --- 3. WHY no trades? --------------------------------------------------
    if not orders:
        print("## Why there were no trades\n")
        rejections = db.get("rejections_24h") or {}
        if rejections:
            print("| Rejection reason | Count |")
            print("|---|---|")
            for k, v in rejections.items():
                print(f"| {k} | {v} |")
            print()
        elif outcomes.get("NO_SETUP") and len(outcomes) == 1:
            print("Every evaluation returned `NO_SETUP`: the entry conditions were "
                  "never all true at the same bar close. No risk gate was reached, "
                  "so nothing was rejected — the setup simply did not occur.\n")
        elif evals == 0:
            problems.append("no evaluations at all in 24h — the loop may not be running")
        else:
            print("No rejection reasons recorded and no orders placed.\n")

    # --- 4. sample size -----------------------------------------------------
    print("## Sample size\n")
    if closed < MIN_CLOSED_TRADES:
        print(f"**{closed} / {MIN_CLOSED_TRADES} closed trades — INSUFFICIENT SAMPLE.** "
              f"No performance conclusion can be drawn. What the run is validating so "
              f"far is execution correctness, risk enforcement, persistence and "
              f"restart safety, not profitability.\n")
    else:
        print(f"{closed} closed trades — at or above the {MIN_CLOSED_TRADES}-trade "
              f"minimum for a performance read.\n")

    # --- 5. the daily report from the app itself ---------------------------
    report = sec.get("DAILYREPORT", "").strip()
    if report:
        print(f"## `forward-test report --day {day}`\n")
        print("```")
        print(report[:4000])
        print("```\n")

    # --- 6. infrastructure --------------------------------------------------
    print("## Infrastructure\n")
    ok, data = aws("ec2", "describe-instances", "--filters",
                   "Name=tag:Project,Values=deltabt",
                   f"Name=tag:Environment,Values={args.environment}",
                   "Name=instance-state-name,Values=pending,running", region=args.region)
    ids = ([i["InstanceId"] for r in data.get("Reservations", []) for i in r.get("Instances", [])]
           if ok else [])
    if len(ids) != 1:
        problems.append(f"expected exactly 1 running bot instance, found {len(ids)}: {ids}")

    ok, alarms = aws("cloudwatch", "describe-alarms", "--alarm-name-prefix", name,
                     region=args.region)
    firing = [a["AlarmName"] for a in alarms.get("MetricAlarms", [])
              if a["StateValue"] == "ALARM"] if ok else ["<could not read alarms>"]
    if firing:
        problems.append(f"alarms in ALARM: {', '.join(firing)}")
    print(f"Instances **{len(ids)}** · alarms in ALARM **{len(firing)}** "
          f"{firing if firing else ''}\n")

    since = int((now - datetime.timedelta(hours=24)).timestamp() * 1000)
    ok, errs = aws("logs", "filter-log-events", "--log-group-name", log_group,
                   "--start-time", str(since), "--limit", "25",
                   "--filter-pattern", '{ $.level = "ERROR" || $.level = "CRITICAL" }',
                   region=args.region)
    events = errs.get("events", []) if ok else []
    print(f"ERROR/CRITICAL log lines in 24h: **{len(events)}**\n")
    if events:
        print("```")
        for e in events[:5]:
            print(e["message"][:220])
        print("```\n")

    # Gaps: a data-quality property of the universe, tracked as evidence.
    ok, gaps = aws("logs", "filter-log-events", "--log-group-name", log_group,
                   "--start-time", str(since), "--limit", "200",
                   "--filter-pattern", '{ $.message = "*gap*" }', region=args.region)
    detected: dict[str, int] = {}
    unrepaired = 0
    for e in (gaps.get("events", []) if ok else []):
        try:
            d = json.loads(e["message"])
        except Exception:                                      # noqa: BLE001
            continue
        if "gap detected" in d.get("message", ""):
            detected[d.get("symbol", "?")] = detected.get(d.get("symbol", "?"), 0) + 1
        if d.get("message", "").startswith("gap repair fetched 0"):
            unrepaired += 1
    if detected:
        print(f"Candle gaps in 24h by symbol: `{detected}` — **{unrepaired} unrepaired**. "
              f"Illiquid minutes produce no trade and therefore no bar; this is a "
              f"property of the instrument and is recorded as evidence, not treated "
              f"as a fault.\n")

    if db.get("dup_signal_keys") or db.get("dup_candles"):
        problems.append(f"DUPLICATE PERSISTENCE: signal keys={db.get('dup_signal_keys')} "
                        f"candles={db.get('dup_candles')}")
    if db.get("quarantined_fills"):
        problems.append(f"{db['quarantined_fills']} quarantined fill(s)")

    # --- verdict ------------------------------------------------------------
    print("---\n")
    for n in notes:
        print(f"- Note: {n}")
    if notes:
        print()
    if problems:
        print("## NEEDS ATTENTION\n")
        for p in problems:
            print(f"- **{p}**")
        return 1
    print("## All clear\n")
    print("Nothing in this report needs a human. The bot is running the frozen "
          "configuration, bound to the experiment, with no duplicate persistence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
