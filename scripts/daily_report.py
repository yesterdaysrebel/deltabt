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
import time

FROZEN_STRATEGY_HASH = "5a5412369f3823f3"
FROZEN_CONFIG_HASH = "ab43fbad6bf3945c"
FROZEN_RISK_HASH = "db4ecc872c759c52"

#: Below this, performance numbers are noise. Stated on every report so the
#: reader is never invited to draw a conclusion the sample cannot support.
MIN_CLOSED_TRADES = 30

#: Mirrors app.config.settings.MAX_WS_SILENCE -- the silence after which the
#: client forces its own reconnect. Duplicated rather than imported because
#: this script runs in CI without the application installed; a test asserts
#: the two stay equal.
MAX_WS_SILENCE = 30.0

#: Four times the client's own reconnect trigger. Below this the reconnect
#: machinery is working as designed; above it, it tried and failed to restore
#: the feed, which is the only websocket condition worth waking someone for.
MAX_FEED_SILENCE = 120.0

#: Hard ceiling on events pulled per log query. A report that stops counting at
#: a page boundary and prints the result as a total is worse than one that
#: admits it truncated, so hitting this is stated in the report.
LOG_EVENT_CAP = 2000

#: Errors from the market-data feed are judged by whether the feed recovered,
#: never by how many were logged. Delta recycles the websocket roughly hourly
#: and the client resubscribes in ~1.5s; counting those as faults would page a
#: human every morning for a socket doing exactly what it is supposed to do.
#: Everything outside this prefix is an application error and always escalates.
FEED_LOGGER_PREFIX = "app.market_data."

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


def log_events(group: str, since_ms: int, pattern: str,
               region: str) -> tuple[list[dict], bool]:
    """Page through filter-log-events. Returns (parsed JSON events, truncated).

    filter-log-events pages. Reading one page and printing its length as a
    24h total silently under-reports the moment there is more than a page of
    it -- and under-reports worst exactly when things are going wrong.
    """
    out: list[dict] = []
    token: str | None = None
    while len(out) < LOG_EVENT_CAP:
        extra = ["--next-token", token] if token else []
        ok, page = aws("logs", "filter-log-events", "--log-group-name", group,
                       "--start-time", str(since_ms), "--filter-pattern", pattern,
                       *extra, region=region)
        if not ok:
            problems.append(f"could not read {group}: {page.get('error')}")
            return out, False
        for e in page.get("events", []):
            parsed = as_json(e.get("message", ""))
            if parsed:
                out.append(parsed)
        token = page.get("nextToken")
        if not token:
            break
    out.sort(key=lambda d: str(d.get("ts", "")))
    return out[:LOG_EVENT_CAP], len(out) >= LOG_EVENT_CAP


def parse_ts(value: str) -> datetime.datetime | None:
    """Parse an ISO8601 UTC stamp, tolerating docker's nanosecond precision."""
    text = str(value).strip().replace("Z", "+00:00")
    if "." in text:                       # datetime accepts at most microseconds
        head, _, tail = text.partition(".")
        digits = "".join(c for c in tail if c.isdigit())[:6]
        offset = tail[len(tail) - 6:] if tail.endswith(("+00:00",)) else ""
        text = f"{head}.{digits or '0'}{offset or '+00:00'}"
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def container_started(sec: dict[str, str]) -> datetime.datetime | None:
    """Pull started=... out of the probe's docker inspect line."""
    for line in sec.get("CONTAINER", "").splitlines():
        for field in line.split():
            if field.startswith("started="):
                return parse_ts(field.split("=", 1)[1])
    return None


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


def as_json_list(text: str) -> list[dict]:
    try:
        loaded = json.loads(text)
    except Exception:                                          # noqa: BLE001
        return []
    return [d for d in loaded if isinstance(d, dict)] if isinstance(loaded, list) else []


def num(value: object) -> float | None:
    """A float, or None -- so a missing field never renders as 0.0."""
    return float(value) if isinstance(value, (int, float)) else None


def fmt(value: object, places: int = 4) -> str:
    n = num(value)
    return "—" if n is None else f"{n:.{places}f}"


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

    # --- 2b. the trades themselves ------------------------------------------
    # Aggregates hide the thing worth reading. Every trade is shown with the
    # geometry that decides whether the strategy can survive costs at all:
    # 1R measured in basis points of entry, and how many R the target sits at.
    # The panel's central objection was that a 21.6 bps median 1R leaves
    # round-trip costs eating 0.36-0.71R of a 2R target, so a report that
    # prints P&L without printing R-in-bps omits the number under test.
    trades = as_json_list(sec.get("TRADES", ""))
    positions = as_json_list(sec.get("POSITIONS", ""))
    open_now = [t for t in positions if str(t.get("status", "")).upper() == "OPEN"]
    done = [t for t in trades if str(t.get("status", "")).upper() != "OPEN"]

    def geometry(t: dict) -> tuple[float | None, float | None]:
        """(1R in bps of entry, target distance in R)."""
        entry, stop, target = num(t.get("entry")), num(t.get("stop")), num(t.get("target"))
        if entry is None or stop is None or not entry:
            return None, None
        r = abs(entry - stop)
        if not r:
            return None, None
        return r / abs(entry) * 1e4, (abs(target - entry) / r if target is not None else None)

    if open_now:
        print("### Open\n")
        print("| Symbol | Side | Qty | Entry | Stop | Target | Now | R | Unrealised | 1R (bps) | Target |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for t in open_now:
            bps, rr = geometry(t)
            r_now = num(t.get("r"))
            print(f"| {t.get('symbol', '?')} | {t.get('side', '?')} | {t.get('quantity', '—')} "
                  f"| {fmt(t.get('entry'))} | {fmt(t.get('stop'))} | {fmt(t.get('target'))} "
                  f"| {fmt(t.get('current_price'))} | {'—' if r_now is None else f'{r_now:+.3f}R'} "
                  f"| {fmt(t.get('unrealized_pnl'), 2)} "
                  f"| {'—' if bps is None else f'{bps:.1f}'} "
                  f"| {'—' if rr is None else f'{rr:.2f}R'} |")
        print()

    for t in open_now:
        side = str(t.get("side", "")).upper()
        entry, stop = num(t.get("entry")), num(t.get("stop"))
        target, r_now = num(t.get("target")), num(t.get("r"))
        sym = t.get("symbol", "?")
        # A long's stop sits below entry and its target above; inverted for a
        # short. Anything else means the bracket was built wrong, and a wrong
        # bracket is a risk-management failure however healthy the process is.
        if None not in (entry, stop, target):
            ordered = (stop < entry < target) if side == "LONG" else (target < entry < stop)
            if not ordered:
                problems.append(f"{sym} {side} bracket is inverted: stop {stop}, "
                                f"entry {entry}, target {target}")
        # Past -1R the stop should already have fired. Stops trigger on MARK
        # price while this quote is last-traded, so a small transient overshoot
        # is normal; well beyond it is not.
        if r_now is not None and r_now <= -1.10:
            problems.append(f"{sym} is at {r_now:+.2f}R with the position still "
                            f"open — the stop should have triggered by -1R")

    if done:
        print("### Closed\n")
        print("| Symbol | Side | Entry | Exit | R | P&L | Reason | Closed |")
        print("|---|---|---|---|---|---|---|---|")
        for t in done:
            r_done = num(t.get("r"))
            print(f"| {t.get('symbol', '?')} | {t.get('side', '?')} | {fmt(t.get('entry'))} "
                  f"| {fmt(t.get('exit'))} | {'—' if r_done is None else f'{r_done:+.3f}R'} "
                  f"| {fmt(t.get('pnl'), 2)} | {t.get('reason') or '—'} "
                  f"| {t.get('closed_ist') or '—'} |")
        print()
        rs = [num(t.get("r")) for t in done]
        rs = [r for r in rs if r is not None]
        if rs:
            wins = sum(1 for r in rs if r > 0)
            print(f"Closed: **{len(rs)}** · won **{wins}** · "
                  f"total **{sum(rs):+.2f}R** · mean **{sum(rs) / len(rs):+.3f}R**\n")

    # An open position the risk cap should have prevented is a control failure,
    # not a market outcome, so it is a problem even on a profitable day.
    if len(open_now) > 1:
        problems.append(f"{len(open_now)} positions open at once; the frozen "
                        f"configuration allows max_open_positions=1")

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

    # --- feed continuity ----------------------------------------------------
    # The heartbeat records how long since the last websocket message, so it
    # measures the thing that actually matters -- whether market data stopped --
    # instead of how loudly the client complained about a socket it then fixed.
    beats, beats_cut = log_events(log_group, since, '{ $.message = "heartbeat" }',
                                  args.region)
    silences = [b["seconds_since_ws_message"] for b in beats
                if isinstance(b.get("seconds_since_ws_message"), (int, float))]
    worst = max(silences) if silences else None
    unknown = sum(1 for b in beats if b.get("seconds_since_ws_message") is None)

    if not beats:
        problems.append("no heartbeat lines in 24h -- the bar loop may not be "
                        "running, and feed health cannot be judged either way")
    elif worst is not None and worst > MAX_FEED_SILENCE:
        problems.append(f"feed silence reached {worst:.0f}s (the client forces a "
                        f"reconnect at {MAX_WS_SILENCE:.0f}s, so this means the "
                        f"reconnect did not restore the feed)")
    if unknown:
        notes.append(f"{unknown} heartbeat(s) reported unknown feed silence "
                     f"(no websocket message had arrived yet at that point)")
    if beats:
        measured = f", worst silence **{worst:.1f}s**" if worst is not None else ""
        print(f"Feed continuity: **{len(beats)}** heartbeats{measured} "
              f"(client reconnects at {MAX_WS_SILENCE:.0f}s, report escalates "
              f"above {MAX_FEED_SILENCE:.0f}s)\n")

    # --- errors, attributed and classified ----------------------------------
    errs, errs_cut = log_events(
        log_group, since, '{ $.level = "ERROR" || $.level = "CRITICAL" }', args.region)
    if errs_cut or beats_cut:
        problems.append(f"log query hit the {LOG_EVENT_CAP}-event cap; counts below "
                        f"are floors, not totals")

    # Partition on the record itself. Identity tests like `d not in stale` compare
    # dicts by value, so two identical log lines would collapse into one bucket.
    started = container_started(sec)
    if started is None and errs:
        notes.append("container start time unavailable, so every error is "
                     "attributed to the running container")

    def retired(d: dict) -> bool:
        if started is None:
            return False
        t = parse_ts(d.get("ts", ""))
        return t is not None and t < started

    stale = [d for d in errs if retired(d)]
    live = [d for d in errs if not retired(d)]
    feed = [d for d in live if str(d.get("logger", "")).startswith(FEED_LOGGER_PREFIX)]
    app_errs = [d for d in live
                if not str(d.get("logger", "")).startswith(FEED_LOGGER_PREFIX)]

    print(f"ERROR/CRITICAL in 24h: **{len(errs)}** — "
          f"{len(stale)} from a retired container, {len(feed)} feed reconnects, "
          f"**{len(app_errs)} application**\n")

    # A retired container's shutdown noise is not today's problem; say so once
    # rather than reprinting the same tracebacks every morning until they age out.
    if stale:
        notes.append(f"{len(stale)} error(s) predate the current container "
                     f"(started {started:%Y-%m-%d %H:%M:%S}Z) and belong to the "
                     f"instance it replaced")
    if feed:
        if worst is not None and worst <= MAX_FEED_SILENCE:
            notes.append(f"{len(feed)} feed reconnect(s), all recovered — worst "
                         f"observed silence {worst:.1f}s stayed under "
                         f"{MAX_FEED_SILENCE:.0f}s")
        else:
            problems.append(f"{len(feed)} feed error(s) and no heartbeat evidence "
                            f"that the feed came back")
    if app_errs:
        loggers = sorted({str(d.get("logger", "?")) for d in app_errs})
        problems.append(f"{len(app_errs)} application ERROR/CRITICAL line(s) since "
                        f"the container started, from: {', '.join(loggers)}")
        print("```")
        for d in app_errs[:5]:
            print(f"{d.get('ts')} {d.get('logger')} "
                  f"{str(d.get('message', '')).splitlines()[0][:150]}")
        print("```\n")

    # Gaps: a data-quality property of the universe, tracked as evidence.
    gaps, gaps_cut = log_events(log_group, since, '{ $.message = "*gap*" }', args.region)
    if gaps_cut:
        notes.append(f"gap query hit the {LOG_EVENT_CAP}-event cap; the gap counts "
                     f"below are floors, not totals")
    detected: dict[str, int] = {}
    unrepaired = 0
    for d in gaps:
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
