"""The dry run's daily report: one screen, plain English, IST times.

    python3 scripts/brief_report.py --instance-id i-... --stack dryrun \
        --document deltabt-paper-dryrun-monitor --log-group /deltabt/paper/dryrun/bot \
        --facts-json facts.json

WHY A NEW REPORT (owner, 2026-10-02: "previous ones were very long and made no
sense to me ... make them informative and human readable"). scripts/
daily_report.py grew to ~1,500 lines for the paper arms and still reads that
way. This one answers, in order: does anything need me; how far along is it;
what happened in the last 24 hours; how the three exits compare on the same
trades; what a real bot would have done; where the simulated $250 account
stands against its limits; is the bot healthy. It reuses daily_report.py's
plumbing (the read-only SSM probe, section parsing, log search) and nothing
else.

What it does NOT claim is stated once, in a line: the comparison is read at
100 trades (docs/prod_dry_run_prereg.md) and measures shape, not edge.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import pathlib
import statistics as st
import sys

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("daily_report", HERE / "daily_report.py")
dr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dr)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
READ_AT_TRADES = 100            # docs/prod_dry_run_prereg.md
READ_AT_DAYS = 21
#: The health check left out of the verdict (see build()).
GAPS_CHECK = "no_recent_gaps"
RULES = ("baseline", "ladder", "trail")
RULE_LABEL = {"baseline": "hold to 3R", "ladder": "ladder", "trail": "trail"}


# -- small helpers ----------------------------------------------------------

def ist(ts: float | int | dt.datetime | None, *, with_day: bool = False) -> str:
    if ts is None:
        return "—"
    t = ts if isinstance(ts, dt.datetime) else dt.datetime.fromtimestamp(float(ts), dt.timezone.utc)
    t = t.astimezone(IST)
    out = t.strftime("%-I:%M %p")
    return (t.strftime("%a %d %b, ") + out) if with_day else out


def parse_time(value) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, dt.timezone.utc)
    s = str(value).replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S%z"):
        try:
            return dt.datetime.fromisoformat(s) if fmt is None else dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def duration(seconds: float | None) -> str:
    if not seconds:
        return "—"
    m = int(seconds // 60)
    d, h, m = m // 1440, (m % 1440) // 60, m % 60
    return f"{d}d {h}h" if d else (f"{h}h {m}m" if h else f"{m}m")


def max_dip(rs: list[float]) -> float:
    eq = peak = dip = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dip = max(dip, peak - eq)
    return dip


# -- the report -------------------------------------------------------------

def exit_table(shadow: list[list], risk_per_r: float | None) -> tuple[list[str], dict]:
    """How the three exits did on the same closed positions."""
    by_rule: dict[str, list] = {r: [] for r in RULES}
    for row in shadow:
        rule, _sym, t, r, reason = row[0], row[1], row[2], row[3], row[4]
        if rule in by_rule:
            by_rule[rule].append((t, float(r), str(reason)))
    n = min((len(v) for v in by_rule.values()), default=0)
    if n == 0:
        return ["No trade has closed yet, so there is nothing to compare."], {}
    lines = ["```",
             f"{'exit':12s}{'trades':>7s}{'total R':>9s}{'avg R':>8s}{'won':>6s}"
             f"{'hit 3R':>8s}{'worst dip':>11s}"]
    summary = {}
    for rule in RULES:
        rows = sorted(by_rule[rule])
        rs = [r for _, r, _ in rows]
        won = sum(1 for r in rs if r > 0)
        tp = sum(1 for _, _, why in rows if why.endswith("TAKE_PROFIT"))
        total = sum(rs)
        summary[rule] = round(total, 2)
        lines.append(f"{RULE_LABEL[rule]:12s}{len(rs):7d}{total:+9.2f}"
                     f"{total / len(rs):+8.2f}{100 * won / len(rs):5.0f}%"
                     f"{tp:8d}{-max_dip(rs):+10.1f}R")
    lines.append("```")
    if risk_per_r:
        money = " / ".join(f"{RULE_LABEL[r]} {summary[r] * risk_per_r:+,.2f}" for r in RULES)
        lines.append(f"At ${risk_per_r:,.2f} per R that is {money} dollars.")
    return lines, summary


def would_have(dry: dict, opened: dict | None = None) -> list[str]:
    """What the live checks would have sent or refused, per symbol, and how
    many of the sent ones the simulator actually opened."""
    if not dry:
        return ["No entry signal yet, so the live checks have not run."]
    out = []
    for sym in sorted(dry):
        s = dry[sym]
        sent = f"{plural(s['orders'], 'order')} would have been sent"
        if opened is not None and s["orders"]:
            sent += f" (simulated: {opened.get(sym, 0)} opened)"
        bits = [sent]
        if s["refused"]:
            why = ", ".join(f"{n} {k}" for k, n in
                            sorted(s["reasons"].items(), key=lambda kv: -kv[1]))
            bits.append(f"{s['refused']} refused ({why})")
        detail = []
        if s.get("spread_r"):
            detail.append(f"typical spread {st.median(s['spread_r']):.2f}R")
        if s.get("leverage"):
            detail.append(f"leverage {st.median(s['leverage']):.0f}x")
        line = f"- **{sym}**: " + "; ".join(bits)
        out.append(line + (f" — {', '.join(detail)}" if detail else "") + ".")
    return out


def build(sec: dict, db: dict, now: dt.datetime, *, stack: str,
          errors_24h: int | None, probe_problems: list[str]) -> tuple[str, dict, list[str]]:
    problems = list(probe_problems)
    healthz = dr.as_json(sec.get("HEALTHZ", ""))
    risk_api = dr.as_json(sec.get("RISK", ""))
    trades = dr.as_json_list(dr.gunzip_section(sec, "TRADES"))
    open_pos = dr.as_json_list(sec.get("POSITIONS", ""))

    experiments = db.get("experiments") or []
    running = next((e for e in experiments if str(e.get("status")).upper() == "RUNNING"), None)
    snap = running.get("risk") if running else None
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except ValueError:
            snap = None
    snap = snap or {}
    started = parse_time(running.get("started_at")) if running else None
    exp_id = running.get("experiment_id") if running else None
    if not db:
        problems.append("the database figures did not arrive (probe output missing or cut off)")
    if not running:
        problems.append("no experiment is RUNNING on this host")

    closed = [t for t in trades if str(t.get("status")).upper() == "CLOSED"]
    # The run's closed count comes from the database: /api/trades returns at
    # most 50 rows, so counting its list would stall progress (and break the
    # self-check) at 50 of the 100 trades the read waits for.
    n_closed = db.get("closed_trades_total")
    n_closed = len(closed) if n_closed is None else int(n_closed)
    since = now - dt.timedelta(hours=24)
    day_closed = [t for t in closed if (parse_time(t.get("closed_utc") or t.get("closed_at"))
                                         or dt.datetime.min.replace(tzinfo=dt.timezone.utc)) >= since]
    # /api/trades gives IST strings; fall back on them when no UTC field exists.
    if not day_closed and closed and "closed_ist" in closed[0]:
        day_closed = [t for t in closed if _ist_string_after(t.get("closed_ist"), since)]

    equity = dr.num(risk_api.get("equity") if risk_api else None) or dr.num(healthz.get("equity"))
    start_eq = dr.num(snap.get("starting_equity")) or 10_000.0
    rpt = dr.num(snap.get("risk_per_trade"))
    per_r = start_eq * rpt if rpt else None
    dd = dr.num(risk_api.get("drawdown_pct")) if risk_api else None
    dd_limit = dr.num(snap.get("max_drawdown_pct"))
    day_loss = dr.num(risk_api.get("daily_loss_pct")) if risk_api else None
    day_limit = dr.num(snap.get("max_daily_loss_pct"))
    streak = risk_api.get("consecutive_losses") if risk_api else None
    streak_limit = snap.get("max_consecutive_losses")

    # health
    # `no_recent_gaps` is left out of the verdict. It counts minutes with no
    # trade, and on BEAT/BANK that is most minutes (CONTEXT.md: health is
    # permanently "unhealthy" from it on thin symbols), so it would mark every
    # day as needing attention. A dead feed still shows: candles_fresh and
    # websocket_fresh go red with it. The gap count is still shown under Health.
    failing = [c for c in healthz.get("checks", []) if not c.get("ok")]
    gaps = next((c for c in failing if c.get("name") == GAPS_CHECK), None)
    bad = [c.get("name") for c in failing if c.get("name") != GAPS_CHECK]
    healthy = bool(healthz) and healthz.get("ready") is True and not bad and (
        str(healthz.get("status")) == "healthy" or gaps is not None)
    if healthz and not healthy:
        problems.append(f"the bot is not healthy ({', '.join(bad) or 'not ready'})")
    elif not healthz:
        problems.append("the bot did not answer its health check")
    restarts = None
    for line in (sec.get("CONTAINER") or "").splitlines():
        if "restarts=" in line:
            try:
                restarts = int(line.split("restarts=")[1].split()[0])
            except (ValueError, IndexError):
                pass
    if restarts:
        problems.append(f"the bot has restarted {plural(restarts, 'time')}")
    if errors_24h:
        problems.append(f"{plural(errors_24h, 'error')} in the bot's log in the last 24 hours")
    if dd is not None and dd_limit and dd >= dd_limit:
        problems.append("the 20% drawdown stop has fired: the run is over (no resume, by design)")

    # the self-check (prereg D7): one baseline shadow per closed position
    shadow = db.get("shadow_exits") or []
    base_rows = sum(1 for r in shadow if r[0] == "baseline")
    if n_closed and base_rows != n_closed:
        problems.append(f"self-check: {n_closed} closed trades but {base_rows} baseline "
                        f"shadow records -- the comparison is incomplete")

    # every entry the live checks passed should become a simulated position
    opened = db.get("opened_by_symbol")
    if isinstance(opened, dict):
        for sym, s in sorted((db.get("dry_run") or {}).items()):
            if s.get("orders", 0) > opened.get(sym, 0):
                problems.append(
                    f"{sym}: {plural(s['orders'], 'entry', 'entries')} passed the live checks but "
                    f"the simulator opened {opened.get(sym, 0)} -- the dry run is not holding "
                    f"what a real bot would")

    day_n = (now - started).days + 1 if started else None
    title_day = f"day {day_n} of {READ_AT_DAYS}" if day_n else "day ?"
    out = [f"# Dry run — {title_day} · {ist(now, with_day=True)} IST", ""]
    if problems:
        out.append("**⚠️ Needs your attention:**")
        out += [f"- {p}" for p in problems]
    else:
        out.append("**✅ All normal — nothing needs you.**")
    out.append("")
    read_by = (started + dt.timedelta(days=READ_AT_DAYS)) if started else None
    out.append(f"**Progress:** {n_closed} of {READ_AT_TRADES} trades closed · {title_day}"
               + (f" · read due by {ist(read_by, with_day=True).split(',')[0]}" if read_by else "")
               + " (whichever comes first).")
    out.append("")

    # last 24 hours
    out.append("## Last 24 hours")
    if day_closed:
        rsum = sum(dr.num(t.get("r")) or 0 for t in day_closed)
        out.append(f"- {plural(len(day_closed), 'trade')} closed under hold-to-3R "
                   f"({rsum:+.2f}R in total).")
    else:
        out.append("- No trade closed.")
    if open_pos:
        bits = [f"{p.get('symbol')} {str(p.get('side')).lower()} since {p.get('opened_ist', '?')}"
                for p in open_pos]
        out.append(f"- Open now: {', '.join(bits)}.")
    else:
        out.append("- Nothing open now.")
    ev = db.get("evaluations_24h")
    rej = db.get("rejections_24h") or {}
    if isinstance(rej, dict) and rej:
        top = sorted(rej.items(), key=lambda kv: -kv[1])[:2]
        out.append(f"- Signals checked: {ev if ev is not None else '?'}; most common reasons "
                   f"for no trade: " + "; ".join(f"{k} ({v})" for k, v in top) + ".")
    elif ev is not None:
        out.append(f"- Signals checked: {ev}.")
    out.append("")

    # the three exits
    out.append("## The three exits on the same trades (since the start)")
    lines, summary = exit_table(shadow, per_r)
    out += lines
    out.append("Too early to tell them apart; the planned read is at "
               f"{READ_AT_TRADES} trades, and even then it compares how they behave, not "
               "whether any has an edge.")
    out.append("")

    # what a real bot would have done
    out.append("## What a real bot would have done (since the start)")
    out += would_have(db.get("dry_run") or {},
                      opened if isinstance(opened, dict) else None)
    out.append("")

    # the account
    out.append(f"## Simulated ${start_eq:,.0f} account")
    if equity is not None:
        pct = 100 * (equity - start_eq) / start_eq
        parts = [f"Equity ${equity:,.2f} ({pct:+.1f}%)"]
        if dd is not None:
            parts.append(f"drawdown {100 * dd:.1f}%" + (f" of the {100 * dd_limit:.0f}% limit" if dd_limit else ""))
        if day_loss is not None:
            parts.append(f"today's loss {100 * day_loss:.1f}%" + (f" of {100 * day_limit:.0f}%" if day_limit else ""))
        if streak is not None:
            parts.append(f"losing streak {streak}" + (f" of {streak_limit}" if streak_limit else ""))
        out.append(" · ".join(parts) + ".")
    else:
        out.append("Equity unavailable.")
    out.append("")

    # health
    out.append("## Health")
    up = healthz.get("uptime_seconds")
    out.append(f"Bot up {duration(up)}, {plural(restarts or 0, 'restart')}, "
               f"{'?' if errors_24h is None else plural(errors_24h, 'error')} in the log "
               f"(24h), prices {'live' if healthz.get('ws_connected') else 'NOT live'}"
               + (f"; {gaps.get('detail') or 'price gaps'} (normal on thin symbols)"
                  if gaps else "") + ". "
               f"Experiment `{exp_id or '?'}`.")

    facts = {"stack": stack, "day": now.strftime("%Y-%m-%d"),
             "verdict": "clear" if not problems else "attention", "problems": problems,
             "day_closed": len(day_closed), "closed": n_closed, "exits_r": summary,
             "experiment": exp_id, "equity_line": None if equity is None else f"{equity:,.2f}",
             "health_line": "healthy" if healthy else "not healthy",
             "day_of": title_day}
    return "\n".join(out) + "\n", facts, problems


def _ist_string_after(value, since: dt.datetime) -> bool:
    """`/api/trades` dates read like '2026-10-02 22:50:24 IST'."""
    try:
        t = dt.datetime.strptime(str(value).replace(" IST", ""), "%Y-%m-%d %H:%M:%S")
        return t.replace(tzinfo=IST) >= since
    except ValueError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-id", required=True)
    ap.add_argument("--document", required=True)
    ap.add_argument("--stack", default="dryrun")
    ap.add_argument("--log-group", default="")
    ap.add_argument("--region", default="ap-south-1")
    ap.add_argument("--facts-json")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    day = (now - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    raw = dr.probe(args.instance_id, args.document, day, args.region)
    sec = dr.sections(raw)
    persist = dr.gunzip_section(sec, "PERSISTENCE")
    db = dr.as_json(persist.splitlines()[-1]) if persist.strip() else {}
    errors = None
    if args.log_group:
        since_ms = int((now - dt.timedelta(hours=24)).timestamp() * 1000)
        evs, _trunc = dr.log_events(args.log_group, since_ms,
                                    '{ ($.level = "ERROR") || ($.level = "CRITICAL") }',
                                    region=args.region)
        errors = len(evs) if evs is not None else None
    text, facts, problems = build(sec, db, now, stack=args.stack, errors_24h=errors,
                                  probe_problems=list(dr.problems))
    print(text)
    if args.facts_json:
        pathlib.Path(args.facts_json).write_text(json.dumps(facts, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
