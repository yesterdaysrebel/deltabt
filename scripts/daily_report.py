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
import base64
import datetime
import gzip
import json
import subprocess
import sys
import time

#: DEFAULT ONLY. Both of these are overridable per run, because two experiments
#: now run concurrently from one image -- V1 at d7837e445bc74781 and V2 at
#: 632efcaff62c4d7c -- and the risk hash moves for both whenever the limits are
#: relaxed. Pinning them as constants would mean the report either checked the
#: wrong run or checked nothing. The value of the check is unchanged: within
#: ONE experiment these must never move.
FROZEN_STRATEGY_HASH = "d7837e445bc74781"
#: The COMPOSITE hash also covers risk, execution and the git SHA, so it moves
#: on every deploy and cannot be pinned here. The bot already refuses to trade
#: an experiment whose composite hash it does not match (see
#: app/forwardtest/identity.py), so this report checks the strategy hash --
#: the part that must never move inside one experiment -- and lets the runtime
#: enforce the composite.
FROZEN_RISK_HASH = "db4ecc872c759c52"

#: Below this, performance numbers are noise. Stated on every report so the
#: reader is never invited to draw a conclusion the sample cannot support.
MIN_CLOSED_TRADES = 30

#: How old a RUNNING experiment must be before "zero evaluations" is treated as
#: a dead loop rather than a young run. Twelve 5m bars, plus room for the
#: backfill and indicator warm-up that precede the first evaluation.
MIN_RUN_AGE_FOR_SILENCE = 3600.0

#: Stack names, and which one kept the unsuffixed resource names. Mirrors
#: local.stacks / local.legacy_stack in infra/terraform/ec2.tf; duplicated
#: rather than imported because this script runs on a CI runner with no
#: Terraform state to read.
KNOWN_STACKS = ("v1", "v2", "v3")
LEGACY_STACK = "v1"

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
        out = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    # Storage is UTC throughout, but not every source spells it: docker stamps
    # carry Z while forward_test.started_at arrives bare. Returning a naive
    # datetime for the second kind makes it unsubtractable from the first, so
    # the assumption is made explicit here rather than at each call site.
    return out if out.tzinfo else out.replace(tzinfo=datetime.timezone.utc)


def container_started(sec: dict[str, str]) -> datetime.datetime | None:
    """Pull started=... out of the probe's docker inspect line."""
    for line in sec.get("CONTAINER", "").splitlines():
        for field in line.split():
            if field.startswith("started="):
                return parse_ts(field.split("=", 1)[1])
    return None


def gunzip_section(sec: dict[str, str], name: str) -> str:
    """Return section `name`, transparently un-gzipping a `name_GZ` variant.

    The monitor document compresses the two sections that grow with the trade
    count, because together they crossed SSM's 24,000-byte output cap on
    2026-08-20 and took every database figure in the report with them. The
    plain name is still accepted: the document and this script deploy through
    different pipelines -- Terraform and a git push -- so there is always a
    window where one is new and the other is not, and a report that fails
    during that window would be a second outage caused by fixing the first.
    """
    raw = (sec.get(name + "_GZ", "") or "").strip()
    if raw:
        blob = "".join(raw.split())
        try:
            return gzip.decompress(base64.b64decode(blob)).decode("utf-8", "replace")
        except Exception:
            # Truncated or malformed: fall through and let the caller's own
            # unavailable-vs-zero handling report it, rather than raising here
            # and losing the whole report over one section.
            return ""
    return sec.get(name, "") or ""


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
    """A float, or None -- so a missing field never renders as 0.0.

    STRINGS COUNT, because PostgreSQL NUMERIC arrives as one. db_probe.py
    serialises with json.dumps(default=str), so every NUMERIC column -- fees,
    funding, r_multiple, stop_distance_pct -- reaches this as "2.21993942".
    Rejecting those rendered the entire cost table as "—" and 0.00 on data
    that was fully populated, which is worse than omitting it: a zero reads as
    measured-and-negligible rather than not-read.

    Non-numeric strings still return None. The point of this function is that
    absent stays visibly absent.
    """
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def fmt(value: object, places: int = 4) -> str:
    n = num(value)
    return "—" if n is None else f"{n:.{places}f}"


def ist(stamp: object) -> str:
    """The host already renders IST; keep the clock time, drop the redundancy.

    Every other timestamp in this report is UTC, so an unlabelled local time
    would be ambiguous exactly where it matters -- comparing an entry against a
    log line. The column header carries the zone; this drops the repeated
    " IST" suffix and the date when it is today's report anyway.
    """
    if not stamp:
        return "—"
    text = str(stamp).replace(" IST", "").strip()
    return text or "—"


def held(opened: object, closed: object) -> str:
    """How long the position was open, from the two IST stamps."""
    a, b = parse_ist(opened), parse_ist(closed)
    if a is None or b is None:
        return "—"
    minutes = int((b - a).total_seconds() // 60)
    if minutes < 0:
        return "—"
    return f"{minutes}m" if minutes < 90 else f"{minutes // 60}h{minutes % 60:02d}"


def parse_ist(stamp: object) -> datetime.datetime | None:
    if not stamp:
        return None
    try:
        return datetime.datetime.strptime(
            str(stamp).replace(" IST", "").strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


#: Circuit-breaker profiles for the counterfactual below. NONE of these is what
#: the paper run enforces -- every breaker is disabled there on purpose, so the
#: expectancy estimate is not censored by a gate that stops trading after a bad
#: start and thereby deletes the trades that would have followed it.
#:
#: "production" is the classic profile. Measured against this run's history it
#: fires on ROUTINE behaviour rather than on anomalies: at a 33% win rate a
#: streak of three losses has probability 0.67^3 = 30%, so a limit of 3 is not
#: a circuit breaker, it is a description of an ordinary afternoon. "relaxed"
#: is where the arithmetic puts a breaker that catches something unusual --
#: a streak of eight is expected about once in 72 trades at the same win rate.
GATE_PROFILES = {
    "production": dict(consec=3, daily=0.02, drawdown=0.10),
    "relaxed": dict(consec=8, daily=0.05, drawdown=0.10),
}


def gated_replay(trades: list[dict], day_start: float, profile: dict) -> dict:
    """Which of the day's trades a breaker would have refused, and what was in them.

    FIRST-ORDER, AND THE LIMIT IS THE POINT OF SAYING SO. Refusing an entry
    also frees a position slot and leaves a cooldown unstarted, so the real
    counterfactual would have taken DIFFERENT later signals rather than fewer
    of the same ones. Answering that needs the strategy re-run against tick
    data, not a trade list replayed. What this measures exactly is the question
    worth asking daily: of the trades that did happen, which would a breaker
    have stopped, and what was inside them.
    """
    eq = day_start
    daily = 0.0
    peak = day_start
    consec = 0
    halted = None
    taken = blocked = 0
    taken_pnl = blocked_pnl = 0.0
    for t in trades:
        pnl = num(t.get("pnl"))
        if pnl is None:
            continue
        why = halted
        if not why:
            if profile["daily"] and daily < 0 and day_start > 0 and \
                    (-daily / day_start) >= profile["daily"]:
                why = f"daily loss >= {profile['daily']:.0%}"
            elif profile["drawdown"] and peak > 0 and \
                    (peak - eq) / peak >= profile["drawdown"]:
                why = f"drawdown >= {profile['drawdown']:.0%}"
            elif profile["consec"] and consec >= profile["consec"]:
                why = f"{consec} consecutive losses"
            if why:
                halted = why
        if why:
            blocked += 1
            blocked_pnl += pnl
            continue
        taken += 1
        taken_pnl += pnl
        eq += pnl
        daily += pnl
        peak = max(peak, eq)
        consec = consec + 1 if pnl < 0 else 0
    return dict(taken=taken, blocked=blocked, taken_pnl=taken_pnl,
                blocked_pnl=blocked_pnl, halted=halted,
                halted_after=taken if halted else None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-id", required=True)
    ap.add_argument("--document", required=True)
    ap.add_argument("--region", default="ap-south-1")
    ap.add_argument("--environment", default="paper")
    ap.add_argument("--log-group", default=None)
    ap.add_argument("--stack", default=None,
                    help="which concurrent run this is (v1, v2). Selects the "
                         "log group when --log-group is not given.")
    ap.add_argument("--day", default=None, help="UTC date to report on; default yesterday")
    ap.add_argument("--expect-strategy-hash", default=None,
                    help=f"strategy config hash this run must be on "
                         f"(default {FROZEN_STRATEGY_HASH}, which is V1)")
    ap.add_argument("--expect-risk-hash", default=None,
                    help=f"risk hash the RUNNING experiment must carry "
                         f"(default {FROZEN_RISK_HASH})")
    args = ap.parse_args()

    name = f"deltabt-{args.environment}"
    # The log group gained a stack segment when a second experiment started
    # running alongside the first. Falling back to the old path would make the
    # report silently find no heartbeats and declare the bot silent.
    log_group = args.log_group or (
        f"/deltabt/{args.environment}/{args.stack}/bot" if args.stack
        else f"/deltabt/{args.environment}/bot")
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
    _persist_raw = gunzip_section(sec, "PERSISTENCE")
    _persist_last = _persist_raw.splitlines()[-1] if _persist_raw.strip() else ""
    db = as_json(_persist_last)
    # DID THE PROBE ACTUALLY ARRIVE?
    #
    # SSM caps StandardOutputContent at 24,000 bytes and appends
    # "--output truncated--". The PERSISTENCE JSON is emitted LAST by the
    # monitor document, so it is the first thing lost when the rest of the
    # output grows -- and json.loads then fails, as_json returns {}, and every
    # database figure below reads as though the database said zero.
    #
    # On 2026-08-19 that produced "expected exactly 1 RUNNING experiment,
    # found 0" and "no evaluations at all in 24h" for V3, whose experiment was
    # RUNNING and which had written 1,384 signals in that window. The report
    # was not describing the bot; it was describing its own truncated input.
    #
    # A missing probe is now its own finding. It is NOT rendered as zeros.
    db_ok = bool(db)
    db_truncated = bool(_persist_last) and not db_ok

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

    want_strategy = args.expect_strategy_hash or FROZEN_STRATEGY_HASH
    got_strategy = status.get("strategy_config_hash")
    if got_strategy and got_strategy != want_strategy:
        problems.append(f"STRATEGY HASH CHANGED: {got_strategy} != {want_strategy}")

    experiments = db.get("experiments") or []
    _running = [e for e in experiments
                if str(e.get("status")).upper() == "RUNNING"]
    _risk = (_running[0].get("risk") if _running else None) or {}
    if isinstance(_risk, str):
        try:
            _risk = json.loads(_risk)
        except ValueError:
            _risk = {}
    max_open = _risk.get("max_open_positions")
    # The risk hash was the original audit finding: risk_per_trade could change
    # between restarts with nothing in the data reflecting it. The runtime
    # refuses to CONTINUE an experiment across such a change, but only the
    # report can say the running experiment is the one that was intended.
    want_risk = args.expect_risk_hash or FROZEN_RISK_HASH
    for e in experiments:
        if str(e.get("status")).upper() != "RUNNING":
            continue
        got_risk = e.get("risk_hash")
        if got_risk and got_risk != want_risk:
            problems.append(
                f"RISK HASH IS NOT THE EXPECTED ONE: {got_risk} != {want_risk} "
                f"(experiment {e.get('experiment_id')})")

    running = [e for e in experiments if str(e.get("status")).upper() == "RUNNING"]
    if db_truncated:
        problems.append(
            "DATABASE PROBE UNREADABLE -- the monitor output hit the SSM "
            "24,000-byte cap and the PERSISTENCE JSON was truncated. Every "
            "database figure in this report is UNAVAILABLE, not zero. This "
            "says nothing about whether the bot is healthy.")
    elif not db_ok:
        problems.append(
            "DATABASE PROBE MISSING -- no PERSISTENCE section was returned. "
            "Database figures are unavailable, not zero.")
    elif len(running) != 1:
        problems.append(f"expected exactly 1 RUNNING experiment, found {len(running)}")
    run_age_seconds = None
    if running:
        e = running[0]
        started = str(e.get("started_at", ""))[:19]
        print(f"**Experiment** `{e.get('experiment_id')}` · {e.get('status')} · "
              f"started {started}Z · planned {e.get('planned_days')} days\n")
        began = parse_ts(str(e.get("started_at", "")))
        if began:
            run_age_seconds = (
                datetime.datetime.now(datetime.timezone.utc) - began).total_seconds()
    if db.get("signals_unbound", 0) and running:
        notes.append(f"{db['signals_unbound']} pre-binding signals carry no experiment id "
                     f"(expected: they predate the run and stay out of the dataset)")

    # --- 2. what did it do? -------------------------------------------------
    print("## What it did\n")
    outcomes = db.get("outcomes_24h") or {}
    evals = db.get("evaluations_24h") if db_ok else None
    if evals is None:
        print("Evaluations in the last 24h: **unavailable** — the database probe "
              "did not parse; see NEEDS ATTENTION.\n")
    else:
        print(f"Evaluations in the last 24h: **{evals}**\n")

    # WHY THIS LINE EXISTS. On 2026-08-19 the V3 report said "0 evaluations in
    # 24h" and listed three open positions, one entered 32 minutes earlier. Both
    # numbers came from the same probe and could not both be right, and the
    # report carried nothing to tell an operator which. The probe was already
    # collecting these two counts and simply never printed them.
    #
    # Read them together with `scoped_to`: unbound signals accumulate whenever
    # forward_test has no RUNNING row, which is exactly the state that also
    # makes every experiment-scoped figure above read zero.
    bound, unbound = db.get("signals_bound"), db.get("signals_unbound")
    if bound is not None or unbound is not None:
        scope = db.get("scoped_to") or "NONE -- running unbound"
        print(f"Signals recorded all-run: **{bound or 0}** bound · "
              f"**{unbound or 0}** unbound · scoped to `{scope}`\n")
    if outcomes:
        print("| Outcome | Count |")
        print("|---|---|")
        for k, v in outcomes.items():
            print(f"| {k} | {v} |")
        print()

    # THIS RUN's orders and fills, not the database's. The probe still reports
    # the all-time totals under `paper_orders` / `paper_fills` for the
    # persistence section; using them here printed the previous experiment's
    # execution under this experiment's heading.
    orders = db.get("orders_run", db.get("paper_orders", 0))
    fills = db.get("fills_run", db.get("paper_fills", 0))
    closed = db.get("closed_trades_total", 0)
    print(f"Orders **{orders}** · fills **{fills}** · closed trades **{closed}**\n")

    # --- 2b. the trades themselves ------------------------------------------
    # Aggregates hide the thing worth reading. Every trade is shown with the
    # geometry that decides whether the strategy can survive costs at all:
    # 1R measured in basis points of entry, and how many R the target sits at.
    # The panel's central objection was that a 21.6 bps median 1R leaves
    # round-trip costs eating 0.36-0.71R of a 2R target, so a report that
    # prints P&L without printing R-in-bps omits the number under test.
    trades = as_json_list(gunzip_section(sec, "TRADES"))
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
        print("| Symbol | Side | Qty | Entered (IST) | Entry | Stop | Target | Now | R "
              "| Unrealised | 1R (bps) | Target |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for t in open_now:
            bps, rr = geometry(t)
            r_now = num(t.get("r"))
            print(f"| {t.get('symbol', '?')} | {t.get('side', '?')} | {t.get('quantity', '—')} "
                  f"| {ist(t.get('opened_ist'))} "
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
        # Qty is carried here as well as in the Open table. Without it a
        # closed row cannot be checked against its own P&L: R is normalised by
        # risk and P&L is in dollars, so the only way to tell a large position
        # at a small move from a small one at a large move -- and therefore to
        # spot a sizing fault rather than a market outcome -- is the size
        # itself. /api/trades has always returned it; the table simply dropped
        # it on the floor.
        print("### Closed\n")
        print("| Symbol | Side | Qty | Entered (IST) | Closed (IST) | Held "
              "| Entry | Exit | R | P&L | Reason |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for t in done:
            r_done = num(t.get("r"))
            qty = num(t.get("quantity"))
            print(f"| {t.get('symbol', '?')} | {t.get('side', '?')} "
                  f"| {'—' if qty is None else f'{qty:,.0f}'} "
                  f"| {ist(t.get('opened_ist'))} | {ist(t.get('closed_ist'))} "
                  f"| {held(t.get('opened_ist'), t.get('closed_ist'))} "
                  f"| {fmt(t.get('entry'))} | {fmt(t.get('exit'))} "
                  f"| {'—' if r_done is None else f'{r_done:+.3f}R'} "
                  f"| {fmt(t.get('pnl'), 2)} | {t.get('reason') or '—'} |")
        print()
        rs = [num(t.get("r")) for t in done]
        rs = [r for r in rs if r is not None]
        if rs:
            wins = sum(1 for r in rs if r > 0)
            print(f"Closed: **{len(rs)}** · won **{wins}** · "
                  f"total **{sum(rs):+.2f}R** · mean **{sum(rs) / len(rs):+.3f}R**\n")

    # An open position the risk cap should have prevented is a control failure,
    # not a market outcome, so it is a problem even on a profitable day.
    #
    # THE LIMIT IS READ FROM THE EXPERIMENT, NOT ASSUMED. It was hardcoded to 1,
    # so the first day the cap was raised to six the report called two open
    # positions a control failure -- flagging the configuration under test as
    # the fault, the same shape as "four symbols configured". The experiment
    # records its own risk snapshot; anything else is a second copy of the
    # configuration that can disagree with the one actually running.
    #
    # None means the snapshot predates this column. Unknown is not license to
    # assume: the check is skipped and said to be skipped, rather than run
    # against a guess.
    if max_open is None:
        notes.append("open-position cap not checked: this experiment's record "
                     "carries no risk snapshot")
    elif len(open_now) > max_open:
        problems.append(f"{len(open_now)} positions open at once; this "
                        f"experiment allows max_open_positions={max_open}")

    # --- 3. WHY no trades? --------------------------------------------------
    # THE SECTION MUST ANSWER ITS OWN HEADING.
    #
    # This used to print the heading first and then, on the zero-evaluations
    # path, route the actual explanation into notes/problems -- which render at
    # the FOOT of the report. The reader got a "Why there were no trades"
    # heading with nothing under it and the answer twenty lines away, which is
    # worse than not having the section: an empty section reads as "we don't
    # know", and this report is supposed to be the thing that always knows.
    #
    # So the body is composed FIRST and the heading is printed only with it.
    # notes/problems still get their entry, because escalation is a separate
    # concern from explanation and the daily digest is read on its own.
    # NOT gated on "no orders" any more. It was, and that is how AKEUSD and
    # BEATUSD stayed hidden while every one of their 15 setups was refused:
    # one order anywhere in the universe suppressed the whole breakdown.
    if True:
        body = ""
        rejections = db.get("rejections_24h") or {}
        if rejections:
            rows = "\n".join(f"| {k} | {v} |" for k, v in rejections.items())
            body = f"| Rejection reason | Count |\n|---|---|\n{rows}\n"
        elif outcomes.get("NO_SETUP") and len(outcomes) == 1:
            body = ("Every evaluation returned `NO_SETUP`: the entry conditions were "
                    "never all true at the same bar close. No risk gate was reached, "
                    "so nothing was rejected — the setup simply did not occur.\n")
        elif evals is None:
            body = ("Evaluation count unavailable -- the database probe did not "
                    "parse. Nothing here describes the strategy loop.\n")
        elif evals == 0:
            # "Zero evaluations" stopped meaning "the loop is dead" the moment
            # this count became experiment-scoped. A run that started twenty
            # minutes ago has legitimately evaluated nothing, and escalating
            # that would fire on EVERY experiment start -- the cry-wolf failure
            # this report already had once with feed reconnects.
            #
            # A 5m bar closes every 300s, and warm-up needs the backfill to
            # finish first, so an hour is comfortably long enough that silence
            # is real. Below it the fact is still reported, just not escalated.
            if run_age_seconds is not None and run_age_seconds < MIN_RUN_AGE_FOR_SILENCE:
                mins = int(run_age_seconds // 60)
                body = (f"The experiment has not evaluated a bar yet. It is {mins} "
                        f"minute(s) old, and a 5m bar closes every 300s after the "
                        f"backfill and indicator warm-up finish, so there has not "
                        f"been time for one. Not escalated below "
                        f"{int(MIN_RUN_AGE_FOR_SILENCE // 60)} minutes.\n")
                notes.append(
                    f"no evaluations yet: the experiment is {mins} minutes old "
                    f"(not escalated below "
                    f"{int(MIN_RUN_AGE_FOR_SILENCE // 60)} minutes)")
            else:
                body = ("**No evaluations at all in 24h.** The strategy loop did not "
                        "reach a single bar close, which is not a market outcome — "
                        "at four symbols on a 5m timeframe this should be in the "
                        "hundreds. Escalated.\n")
                problems.append(
                    "no evaluations at all in 24h — the loop may not be running")
        else:
            # Evaluations happened, none reached a risk gate, and the outcome mix
            # was not purely NO_SETUP. Name the mix rather than saying nothing.
            mix = ", ".join(f"`{k}` {v}" for k, v in sorted(outcomes.items()))
            body = (f"{evals} evaluation(s), no order placed and no risk-gate "
                    f"rejection recorded. Outcome mix: {mix or 'none recorded'}.\n")
        print("## Why setups did not become trades\n")
        print(body)

    # --- 3b. what the run is measuring --------------------------------------
    econ = db.get("economics") or []
    if econ:
        print("## Cost, and what it leaves\n")
        print("The binding constraint per the panel review is cost, not "
              "signal: a fixed fraction of notional against a variable R. "
              "Every column here was recorded from the first run and none of "
              "it was reported.\n")
        print("| Symbol | R | planned R | fill R | slip | fees | funding | "
              "cost | cost/R | held |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        tot_cost = tot_r_value = 0.0
        for t in econ:
            r = num(t.get("r_multiple"))
            fees = (num(t.get("entry_fee")) or 0) + (num(t.get("exit_fee")) or 0)
            slip = ((num(t.get("entry_slippage")) or 0)
                    + (num(t.get("exit_slippage")) or 0))
            fund = num(t.get("funding")) or 0
            cost = fees + slip + fund
            # R in currency: |realised| / |r_multiple| recovers one R, which is
            # what cost has to be compared against.
            pnl = num(t.get("realized_pnl"))
            r_value = (abs(pnl) / abs(r)) if (pnl is not None and r) else None
            cpr = (cost / r_value) if r_value else None
            if r_value:
                tot_cost += cost
                tot_r_value += r_value
            hold_s = t.get("hold_seconds")
            print(f"| {t.get('symbol','?')} "
                  f"| {'—' if r is None else f'{r:+.3f}'} "
                  f"| {fmt(t.get('planned_r'), 2)} | {fmt(t.get('fill_rr'), 2)} "
                  f"| {slip:.2f} | {fees:.2f} | {fund:.2f} | {cost:.2f} "
                  f"| {'—' if cpr is None else f'{cpr:.3f}R'} "
                  f"| {'—' if not hold_s else f'{int(hold_s)//3600}h{int(hold_s)%3600//60:02d}'} |")
        print()
        if tot_r_value:
            share = tot_cost / tot_r_value
            print(f"Cost consumed **{share:.3f}R** per trade on average "
                  f"({len(econ)} closed). The research put this at ~0.55R at a "
                  f"median 1R of 21.6 bps, and estimated 0.15R needs a median "
                  f"R near 80 bps.\n")
        # planned_r vs fill_rr is the degradation schema.sql names explicitly.
        deg = [(num(t.get("planned_r")), num(t.get("fill_rr"))) for t in econ]
        deg = [(p, f) for p, f in deg if p and f]
        if deg:
            mean = sum(f - p for p, f in deg) / len(deg)
            print(f"Approved-to-filled reward/risk moved **{mean:+.3f}** on "
                  f"average -- entry slippage between what the risk engine "
                  f"approved and what the fill produced.\n")

    rs = db.get("risk_state") or {}
    if rs:
        eq, peak = num(rs.get("equity")), num(rs.get("peak_equity"))
        dd = ((peak - eq) / peak) if (peak and eq is not None and peak > 0) else None
        print("## Equity\n")
        print(f"Equity **{fmt(eq, 2)}** · peak **{fmt(peak, 2)}** · drawdown "
              f"**{'—' if dd is None else f'{100*dd:.2f}%'}** · "
              f"wins {rs.get('wins')} losses {rs.get('losses')} · "
              f"streak {rs.get('consecutive_losses')}\n")
        # The drawdown halt is disabled for these runs, so nothing enforces
        # this number. That is exactly why it is printed.
        if dd is not None and dd >= 0.10:
            problems.append(
                f"drawdown {100*dd:.2f}% -- the max_drawdown_pct halt is "
                f"DISABLED for this run, so nothing stops it compounding")

    # ---- what the circuit breakers would have done --------------------
    #
    # The paper run has every breaker disabled so the measurement is not
    # censored. That leaves an obvious question unanswered every day -- what
    # would production have done -- and answering it from the same trade
    # stream costs nothing and needs no second bot.
    if done:
        seq = sorted(done, key=lambda t: str(t.get("closed_ist") or ""))
        pnls = [num(t.get("pnl")) for t in seq]
        actual = sum(p for p in pnls if p is not None)
        day_start = None
        if rs:
            e, d = num(rs.get("equity")), num(rs.get("daily_pnl"))
            if e is not None and d is not None:
                day_start = e - d
        if day_start:
            print("## What the circuit breakers would have done\n")
            print("Every breaker is DISABLED in this run, deliberately: one that "
                  "halts after a bad start deletes the trades that would have "
                  "followed it, and the expectancy estimate is then conditional "
                  "on the day not already having gone wrong. This is the same "
                  "trade stream replayed against profiles that are not "
                  "enforced.\n")
            # THE REPLAY STARTS AT THE LEDGER, NOT AT MIDNIGHT, and on a day
            # the experiment was registered those are different instants. The
            # ledger is reset on registration, so losses taken earlier that UTC
            # day under the previous experiment are not in `daily_pnl` and not
            # in this table -- a breaker that would have fired at 02:12Z on the
            # old ledger shows here as never having fired. Say so rather than
            # quietly understating, because a restart day is exactly when
            # someone reads this table to decide whether the gates matter.
            if began is not None and began.date().isoformat() == day:
                print(f"> This experiment was registered at "
                      f"{began.strftime('%H:%M')}Z on the reported day, and the "
                      f"risk ledger was reset with it. The replay below starts "
                      f"there, NOT at 00:00Z -- anything traded earlier in the "
                      f"UTC day belonged to the previous experiment and is "
                      f"outside it.\n")
            print("| Profile | Streak | Daily | Drawdown | Taken | Refused "
                  "| Day P&L | vs actual | First halt |")
            print("|---|---|---|---|---|---|---|---|---|")
            print(f"| _as run_ | off | off | off | {len(seq)} | 0 "
                  f"| {actual:+.2f} | — | — |")
            for name, prof in GATE_PROFILES.items():
                g = gated_replay(seq, day_start, prof)
                delta = g["taken_pnl"] - actual
                halt = (f"{g['halted']} after {g['halted_after']}"
                        if g["halted"] else "never fired")
                print(f"| {name} | {prof['consec']} | {prof['daily']:.0%} "
                      f"| {prof['drawdown']:.0%} | {g['taken']} | {g['blocked']} "
                      f"| {g['taken_pnl']:+.2f} | {delta:+.2f} | {halt} |")
            print()
            print("First-order only: refusing an entry also frees a position "
                  "slot and leaves a cooldown unstarted, so the true "
                  "counterfactual would have taken DIFFERENT later signals, not "
                  "merely fewer of the same. What is exact is which trades that "
                  "did happen a breaker would have refused.\n")

    by_sym = db.get("by_symbol") or []
    if by_sym:
        print("## Per symbol\n")
        print("A symbol that never trades is invisible in a total. AKEUSD and "
              "BEATUSD had every setup refused for stop width and the "
              "aggregates showed nothing.\n")
        print("| Symbol | Setups | Approved | Rejected | Stop % range | Bars | Synthetic |")
        print("|---|---|---|---|---|---|---|")
        quality = {q["symbol"]: q for q in (db.get("bar_quality") or [])}
        for r in by_sym:
            q = quality.get(r["symbol"], {})
            bars, syn = q.get("bars"), q.get("synthetic")
            pct = f"{100*syn/bars:.0f}%" if bars else "—"
            lo, hi = num(r.get("min_stop_pct")), num(r.get("max_stop_pct"))
            rng = "—" if lo is None else f"{lo:.2f}–{hi:.2f}"
            print(f"| {r['symbol']} | {r.get('setups', 0)} | {r.get('approved', 0)} "
                  f"| {r.get('rejected', 0)} | {rng} | {bars or '—'} | {pct} |")
        print()
        dead = [r["symbol"] for r in by_sym
                if r.get("setups", 0) >= 5 and not r.get("approved")]
        if dead:
            notes.append(f"no setup has ever been approved for {', '.join(dead)} "
                         f"-- configured and evaluating, but not trading")

    oldest = db.get("oldest_open_seconds")
    if oldest:
        hours = int(oldest) / 3600.0
        print(f"Oldest open position: **{hours:.1f}h**. Only STOP_LOSS and "
              f"TAKE_PROFIT close a position -- there is no time stop -- so a "
              f"target that cannot be reached is held indefinitely.\n")
        if hours >= 72:
            problems.append(f"a position has been open {hours:.0f}h with no "
                            f"time stop to release it")

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
    # ONE BOT PER STACK, NOT ONE BOT IN TOTAL.
    #
    # This asked for exactly one instance anywhere, which was right while there
    # was one experiment. With two running side by side it fired on BOTH
    # reports every day, naming the other stack's host as the fault -- the same
    # correction already made in aws_preflight.py and verify_deployment.py, and
    # missed here because this file enumerates instances itself.
    #
    # Two bots in ONE stack is still fatal: they share a database, and the
    # advisory lock, the single-RUNNING-experiment index and
    # ux_positions_open_symbol are all per-database.
    ok, data = aws("ec2", "describe-instances", "--filters",
                   "Name=tag:Project,Values=deltabt",
                   f"Name=tag:Environment,Values={args.environment}",
                   "Name=instance-state-name,Values=pending,running", region=args.region)
    by_stack: dict = {}
    if ok:
        for r in data.get("Reservations", []):
            for i in r.get("Instances", []):
                tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
                by_stack.setdefault(tags.get("Stack", "<untagged>"), []).append(
                    i["InstanceId"])
    ids = [i for v in by_stack.values() for i in v]
    mine = by_stack.get(args.stack) if args.stack else None
    if not ids:
        problems.append("no running bot instance found")
    elif args.stack and mine is None:
        problems.append(f"no running instance tagged Stack={args.stack}; "
                        f"found {sorted(by_stack)}")
    elif mine is not None and len(mine) != 1:
        problems.append(f"expected exactly 1 running instance in stack "
                        f"{args.stack}, found {len(mine)}: {mine}. Two bots "
                        f"share one database and will interleave one account.")
    elif args.stack is None and len(ids) != 1:
        problems.append(f"expected exactly 1 running bot instance, found "
                        f"{len(ids)}: {ids}")

    # ALARMS ARE FILTERED TO THIS STACK, for the same reason the instance count
    # is. Both stacks share the prefix, so an unfiltered read made each report
    # escalate on the other host's alarms -- two red reports for one problem,
    # each naming a resource its own experiment does not own.
    #
    # The legacy stack has no suffix, so it cannot be selected by prefix: it is
    # "everything under deltabt-paper- that is not another stack's". That is
    # ugly and it is the price of v1 keeping the names it was created with.
    other_prefixes = tuple(f"{name}-{s}-" for s in KNOWN_STACKS
                           if args.stack and s != args.stack)

    def mine_alarm(alarm_name: str) -> bool:
        if not args.stack:
            return True
        if args.stack == LEGACY_STACK:
            return not alarm_name.startswith(other_prefixes)
        return alarm_name.startswith(f"{name}-{args.stack}-")

    ok, alarms = aws("cloudwatch", "describe-alarms", "--alarm-name-prefix", name,
                     region=args.region)
    firing = [a["AlarmName"] for a in alarms.get("MetricAlarms", [])
              if a["StateValue"] == "ALARM" and mine_alarm(a["AlarmName"])
              ] if ok else ["<could not read alarms>"]
    if firing:
        problems.append(f"alarms in ALARM: {', '.join(firing)}")
    print(f"Instances **{len(mine) if mine is not None else len(ids)}** · alarms in ALARM **{len(firing)}** "
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
