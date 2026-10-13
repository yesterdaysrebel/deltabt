"""The dry run's daily report: the attention list, then the trade journal.

    python3 scripts/brief_report.py --instance-id i-... --stack dryrun \
        --document deltabt-paper-dryrun-monitor --log-group /deltabt/paper/dryrun/bot \
        --facts-json facts.json

Two parts and nothing else (owner, 2026-10-02: "a journal only with error
report like we have right now"):

1. Does anything need you -- the bot unhealthy or restarted, errors in its
   log (each shown; a Delta feed drop the bot recovered from is a note), the 20% stop fired, data missing, the shadow self-check (prereg D7),
   or an entry the live checks passed that the simulator did not open.
2. The journal -- every trade of the run: what is open now and everything
   closed so far, with times in IST, side, size, the leverage a real bot
   would have set on Delta, entry, stop, target, exit, R and dollars.

It reuses daily_report.py's plumbing (the read-only SSM probe, section
parsing, log search) and nothing else.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import pathlib
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
EXITS = ("baseline", "ladder", "trail")
EXIT_LABEL = {"baseline": "hold to 3R", "ladder": "ladder", "trail": "trail"}
#: The drawdown stop the plan pre-registered. The dry run may run with a looser
#: configured stop (amendment 2026-10-03); the report then states the day this
#: one WOULD have fired.
PREREG_LATCH = 0.20
#: The time stop (variables.tf max_hold_seconds, 72 h). A shadow exit that
#: outlives the real position closes by then at the latest.
MAX_HOLD_SECONDS = 259_200
#: The real position's own exits; after these the other rules keep running
#: (app/execution/shadow_exits.py). Any other real exit closes them with it.
OWN_EXITS = ("STOP_LOSS", "TAKE_PROFIT")


#: A Delta price-feed drop counts as RECOVERED if the bot logged "subscribed"
#: within this many seconds after it. Drops happen a few times a day on the
#: venue's side; the bot reconnects in 2-3 seconds (2026-10-04/05 log).
FEED_RECOVERY_SECONDS = 60
#: More recovered drops than this in 24 hours is itself worth a look.
FEED_DROPS_ALARM = 6
#: The pattern main() reads: every ERROR/CRITICAL line, plus the feed's
#: "subscribed" lines that show a reconnect completed.
LOG_PATTERN = '{ ($.level = "ERROR") || ($.level = "CRITICAL") || ($.message = "subscribed") }'


def _log_time(event: dict) -> float | None:
    t = parse_time(str(event.get("ts", "")).replace("Z", "+00:00"))
    return t.timestamp() if t else None


def _is_feed_drop(event: dict) -> bool:
    msg = str(event.get("message", ""))
    return (str(event.get("logger", "")).endswith("delta_ws")
            and (msg.startswith("feed went silent") or msg.startswith("feed error")))


def classify_log(events: list[dict], now: dt.datetime) -> tuple[list[str], list[str]]:
    """(notes, problems) from the last 24 hours of the bot's log.

    A Delta feed drop followed by "subscribed" within FEED_RECOVERY_SECONDS is
    a note: the venue's socket stalled and the bot reconnected, which is the
    safety check working. Every other ERROR/CRITICAL line is a problem, shown
    with its time and message, as is a drop with no reconnect after it and a
    day with more than FEED_DROPS_ALARM drops.
    """
    errors = [e for e in events if str(e.get("level")) in ("ERROR", "CRITICAL")]
    subs = sorted(t for t in (_log_time(e) for e in events
                              if str(e.get("message")) == "subscribed") if t is not None)
    recovered, unrecovered, pending, other = [], [], [], []
    for e in errors:
        t = _log_time(e)
        if _is_feed_drop(e) and t is not None:
            if any(t < s <= t + FEED_RECOVERY_SECONDS for s in subs):
                recovered.append(t)
            elif now.timestamp() - t < FEED_RECOVERY_SECONDS + 30:
                pending.append(t)
            else:
                unrecovered.append(e)
        else:
            other.append(e)
    notes, problems = [], []
    if recovered:
        when = ", ".join(ist(t) for t in recovered[:6]) + (" ..." if len(recovered) > 6 else "")
        notes.append(f"Delta's price feed dropped {plural(len(recovered), 'time')} and the bot "
                     f"reconnected within seconds each time ({when} IST). Nothing needs you.")
        if len(recovered) > FEED_DROPS_ALARM:
            problems.append(f"Delta's price feed dropped {len(recovered)} times in 24 hours (each "
                            f"recovered) -- more than the usual few; worth a look")
    if pending:
        notes.append(f"Delta's price feed dropped at {ist(pending[-1])} IST, moments before this "
                     f"report; the reconnect was not yet logged.")
    for e in unrecovered:
        problems.append(f"Delta's price feed dropped at {ist(_log_time(e))} IST and no reconnect "
                        f"followed within {FEED_RECOVERY_SECONDS} s")

    def line(e):
        msg = str(e.get("message", "")).splitlines()[0][:140]
        t = _log_time(e)
        return f"{ist(t) if t else '?'} IST {e.get('level')} ({str(e.get('logger', '?')).split('.')[-1]}): {msg}"
    if other:
        shown = "; ".join(line(e) for e in other[:3])
        more = f"; and {len(other) - 3} more" if len(other) > 3 else ""
        problems.append(f"{plural(len(other), 'error')} in the bot's log in the last 24 hours: {shown}{more}")
    return notes, problems


#: The host Postgres volume is snapshotted daily (infra/terraform/db_host.tf);
#: a newest snapshot older than this means the backup has stopped.
SNAPSHOT_MAX_AGE_HOURS = 30


def snapshot_problems(snapshots: list[dict] | None, now: dt.datetime) -> tuple[list[str], list[str]]:
    """(notes, problems) for the stack's daily database snapshots. None = could not read."""
    if snapshots is None:
        return [], ["could not read the database snapshots (backup state unknown)"]
    done = [s for s in snapshots if str(s.get("State")) == "completed"]
    if not done:
        return [], ["no completed database snapshot exists: the daily backup has not run"]
    newest = max(parse_time(str(s.get("StartTime")).replace("Z", "+00:00")) for s in done)
    age = (now - newest).total_seconds() / 3600
    if age > SNAPSHOT_MAX_AGE_HOURS:
        return [], [f"the newest database snapshot is {age:.0f} hours old: the daily backup has stopped"]
    return [f"Database backed up {age:.0f} hours ago ({len(done)} daily snapshots kept)."], []


def real_rule_of(strategy_version: str | None) -> str:
    """Which exit the simulated account runs, from forward_test.strategy_version
    (e.g. 'manual_scalp_both_t3_trail@5m@cf9917a73c61')."""
    name = str(strategy_version or "").split("@")[0]
    if name.endswith("_trail"):
        return "trail"
    if name.endswith("_ladder"):
        return "ladder"
    return "baseline"


def would_have_latched(closed: list[dict], start_eq: float,
                       limit: float = PREREG_LATCH) -> int | None:
    """Close time of the trade at which equity first fell `limit` below its
    peak, from the journal's closed trades in close order; None if never."""
    eq = peak = start_eq
    for r in sorted((r for r in closed if r.get("closed")), key=lambda r: r["closed"]):
        eq += r.get("pnl") or 0.0
        peak = max(peak, eq)
        if peak > 0 and (peak - eq) / peak >= limit:
            return int(r["closed"])
    return None
WHY = {"TAKE_PROFIT": "target", "STOP_LOSS": "stop", "TIME_EXIT": "time",
       "TIME_STOP": "time", "MAX_HOLD": "time", "MANUAL_CLOSE": "manual",
       "LIQUIDATION": "LIQUIDATED", "HALT": "halt"}


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


def when(ts) -> str:
    """'02 Oct 5:00 PM' in IST, or a dash."""
    if ts is None:
        return "—"
    t = dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).astimezone(IST)
    return t.strftime("%d %b %-I:%M %p")


def price(x) -> str:
    if x is None:
        return "—"
    s = f"{float(x):.6g}"
    return f"{float(x):.8f}".rstrip("0") if "e" in s else s


def money(x) -> str:
    return "—" if x is None else f"{float(x):+,.2f}"


def table(header: list[str], rows: list[list[str]], right: set[int]) -> list[str]:
    """A fixed-width table in a code block: reads the same in mail and on GitHub."""
    widths = [max(len(header[i]), *(len(r[i]) for r in rows)) for i in range(len(header))]

    def line(cells):
        return "  ".join(c.rjust(w) if i in right else c.ljust(w)
                         for i, (c, w) in enumerate(zip(cells, widths))).rstrip()
    return ["```", line(header), *(line(r) for r in rows), "```"]


def journal_rows(db: dict) -> list[dict]:
    fields = db.get("journal_fields") or []
    return [dict(zip(fields, row)) for row in (db.get("journal") or [])
            if isinstance(row, list) and len(row) == len(fields)]


def missing_exit(r: dict, now_ts: int, proc_start: int | None) -> str:
    """Why a closed trade has no row yet for one of the other exits.

    The other rules keep running after the real position closes by its own
    stop or target, and a row is written only when each closes. Those legs
    live in the bot's memory alone: a restart after the real close loses
    them (app/execution/shadow_exits.py).
    """
    c = r.get("closed")
    if (str(r.get("why")) in OWN_EXITS and c is not None
            and (proc_start is None or proc_start <= c)
            and r.get("opened") is not None and now_ts - r["opened"] < MAX_HOLD_SECONDS):
        return "still open"
    return "lost: restart"


def journal(rows: list[dict], live: dict, exits: dict | None = None,
            omitted: int = 0, real: str = "baseline", *, now_ts: int | None = None,
            proc_start: int | None = None) -> tuple[list[str], dict]:
    """Open positions, then every closed trade of the run under all three exits.

    `real` is the rule the simulated account runs: its line comes from the
    position itself; the other two rules' lines come from their shadow rows.
    Newest first in both tables (owner, 2026-10-06); numbers stay in order of
    opening.
    """
    exits = exits or {}
    now_ts = int(now_ts if now_ts is not None else dt.datetime.now(dt.timezone.utc).timestamp())
    others = [rule for rule in EXITS if rule != real]
    out: list[str] = []
    side = {1: "long", -1: "short"}
    # Numbered in order of opening, counting any oldest trades the probe left
    # out for space, so a trade keeps its number from one report to the next.
    num = {r["uid"]: omitted + i + 1 for i, r in enumerate(rows)}
    lev = lambda r: "—" if r.get("lev") is None else f"{r['lev']:.0f}x"
    mark = lambda r: f"{num[r['uid']]}{'*' if r.get('carried') else ''}"

    open_ = [r for r in rows if str(r.get("status")).upper() != "CLOSED"][::-1]
    closed = [r for r in rows if str(r.get("status")).upper() == "CLOSED"][::-1]

    out.append(f"## Open now ({len(open_)})")
    if open_:
        body = []
        for r in open_:
            now = live.get(r["uid"], {})
            r_now = now.get("r")
            body.append([mark(r), r["symbol"], side.get(r["side"], "?"), str(r["qty"]),
                         f"{r['notional']:,.0f}" if r.get("notional") is not None else "—",
                         lev(r), when(r["opened"]), price(r["entry"]), price(r["sl"]),
                         price(r["tp"]), price(now.get("current_price")),
                         "—" if r_now is None else f"{r_now:+.2f}",
                         money(now.get("unrealized_pnl"))])
        out += table(["#", "symbol", "side", "qty", "size $", "lev", "opened (IST)",
                      "entry", "stop", "target", "now", "R now", "P&L now $"],
                     body, right={0, 3, 4, 5, 7, 8, 9, 10, 11, 12})
    else:
        out.append("Nothing open.")
    out.append("")

    out.append(f"## Closed so far ({len(closed) + omitted} of {READ_AT_TRADES} for the read) — "
               f"the three exits on each trade")
    if omitted:
        out.append(f"The oldest {plural(omitted, 'closed trade')} are left out to fit the "
                   f"report's size limit; the database has every trade, and the totals "
                   f"below cover only the trades shown.")
    totals = {rule: {"n": 0, "won": 0, "tp": 0, "r": 0.0, "pnl": 0.0} for rule in EXITS}
    partial = waiting = lost = False
    if closed:
        body = []
        for r in closed:
            rr, pnl = r.get("r"), r.get("pnl")
            # dollars per R for this trade, from the real position, so the
            # other exits' R can be priced the same way
            per_r = (pnl / rr) if (pnl is not None and rr) else None
            lines = [(real, r.get("closed"), r.get("exit"), r.get("why"), rr, pnl, True)]
            for rule in others:
                e = exits.get((r["uid"], rule))
                if e is None:
                    why_missing = missing_exit(r, now_ts, proc_start)
                    waiting = waiting or why_missing == "still open"
                    lost = lost or why_missing != "still open"
                    lines.append((rule, None, None, why_missing, None, None, True))
                else:
                    lines.append((rule, e.get("closed"), e.get("exit"), e.get("why"), e.get("r"),
                                  None if (per_r is None or e.get("r") is None) else e["r"] * per_r,
                                  bool(e.get("seen", True))))
            for k, (rule, c, px, why, er, epnl, seen) in enumerate(lines):
                first = k == 0
                held = (c - r["opened"]) if c and r.get("opened") else None
                label = EXIT_LABEL[rule] + ("" if seen else " ~")
                partial = partial or not seen
                body.append([
                    mark(r) if first else "", r["symbol"] if first else "",
                    side.get(r["side"], "?") if first else "", str(r["qty"]) if first else "",
                    lev(r) if first else "", when(r["opened"]) if first else "",
                    price(r["entry"]) if first else "", price(r["sl"]) if first else "",
                    price(r["tp"]) if first else "",
                    label, when(c), price(px),
                    WHY.get(str(why), str(why or "—").lower()), duration(held),
                    "—" if er is None else f"{er:+.2f}", money(epnl)])
                if er is not None:
                    tot = totals[rule]
                    tot["n"] += 1
                    tot["won"] += 1 if er > 0 else 0
                    tot["tp"] += 1 if str(why).endswith("TAKE_PROFIT") else 0
                    tot["r"] += er
                    tot["pnl"] += epnl or 0.0
            body.append([""] * 16)
        body.pop()
        out += table(["#", "symbol", "side", "qty", "lev", "opened (IST)", "entry", "stop",
                      "target", "exit rule", "closed (IST)", "exit", "how", "held", "R",
                      "P&L $"], body, right={0, 3, 4, 6, 7, 8, 11, 13, 14, 15})
        out.append("")
        out.append("Totals on the same trades:")
        rows_t = []
        for rule in EXITS:
            tot = totals[rule]
            n = tot["n"]
            rows_t.append([EXIT_LABEL[rule], str(n), str(tot["won"]), str(tot["tp"]),
                           f"{tot['r']:+.2f}", f"{tot['r'] / n:+.2f}" if n else "—",
                           money(tot["pnl"])])
        out += table(["exit rule", "trades", "won", "hit 3R", "total R", "avg R", "P&L $"],
                     rows_t, right={1, 2, 3, 4, 5, 6})
        out.append(f"Too early to tell the exits apart; the planned read is at {READ_AT_TRADES} "
                   f"trades, and even then it compares how they behave, not whether any has "
                   f"an edge.")
    else:
        out.append("No trade has closed yet.")
    if any(r.get("carried") for r in rows):
        out.append("Trades marked * opened under the previous run and count in this one (same setup).")
    if partial:
        out.append("~ the bot restarted while this trade was open, so this exit is approximate.")
    if waiting:
        out.append("still open: the real trade has closed, but this exit has not reached its "
                   "own stop or target yet (72 h at most); it fills in when it does.")
    if lost:
        out.append("lost: restart: the bot restarted after the real trade closed, and the exits "
                   "still running were lost with it; they cannot be recovered.")
    out.append(f"{EXIT_LABEL[real][0].upper()}{EXIT_LABEL[real][1:]} is the real (simulated) "
               f"trade; {EXIT_LABEL[others[0]]} and {EXIT_LABEL[others[1]]} are where the other "
               "two exits would have closed it. R and P&L are after fees and funding. Lev is "
               "the leverage a real bot would have set on Delta.")
    tot = totals[real]
    return out, {"n": tot["n"], "r": tot["r"], "pnl": tot["pnl"],
                 "by_exit": {k: round(v["r"], 2) for k, v in totals.items()}}


def build(sec: dict, db: dict, now: dt.datetime, *, stack: str,
          errors_24h: int | None, probe_problems: list[str],
          log_events: list[dict] | None = None,
          snapshots: list[dict] | None | bool = False) -> tuple[str, dict, list[str]]:
    problems = list(probe_problems)
    healthz = dr.as_json(sec.get("HEALTHZ", ""))
    risk_api = dr.as_json(sec.get("RISK", ""))
    live = {p.get("position_uid"): p for p in dr.as_json_list(sec.get("POSITIONS", ""))}

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
    real = real_rule_of(running.get("strategy_version")) if running else "baseline"
    start_eq = dr.num(snap.get("starting_equity")) or 250.0
    if not db:
        problems.append("the database figures did not arrive (probe output missing or cut off)")
    if not running:
        problems.append("no experiment is RUNNING on this host")

    # health. `no_recent_gaps` is left out: it counts minutes with no trade,
    # and on BEAT/BANK that is most minutes (CONTEXT.md), so it would mark
    # every day as needing attention. A dead feed still shows: candles_fresh
    # and websocket_fresh go red with it.
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
    log_notes = []
    if log_events is not None:
        # The log lines themselves (main() fetches them): classify, show what each was.
        log_notes, log_problems = classify_log(log_events, now)
        problems += log_problems
    elif errors_24h:
        problems.append(f"{plural(errors_24h, 'error')} in the bot's log in the last 24 hours")
    if snapshots is not False:
        # False = not checked (RDS); None = could not read; a list = the stack's snapshots.
        s_notes, s_problems = snapshot_problems(snapshots, now)
        log_notes += s_notes
        problems += s_problems
    # /api/risk reports drawdown in PERCENT (round(100 * fraction, 3), see
    # app/api/app.py); the experiment's limit is a FRACTION (0.20). Comparing
    # the two unconverted declared the 20% stop fired at a 0.2% drawdown --
    # the 2026-10-03 report said "the run is over" at 9.3%.
    dd_pct = dr.num(risk_api.get("drawdown_pct")) if risk_api else None
    dd = None if dd_pct is None else dd_pct / 100.0
    dd_limit = dr.num(snap.get("max_drawdown_pct"))
    if dd is not None and dd_limit and dd >= dd_limit:
        problems.append(f"the {100 * dd_limit:.0f}% drawdown stop has fired: the run is over "
                        f"(no resume, by design)")

    rows = journal_rows(db)
    closed_rows = [r for r in rows if str(r.get("status")).upper() == "CLOSED"]
    n_closed = len(closed_rows) + int(db.get("journal_omitted") or 0)

    # The plan's 20% stop, when the configured stop is looser (dry run,
    # amendment 2026-10-03): say when it would have fired, outside the
    # attention list -- it is a recorded fact, not a fault.
    notes = []
    if dd_limit and dd_limit > PREREG_LATCH + 1e-9 and not db.get("journal_omitted"):
        at = would_have_latched(closed_rows, start_eq)
        if at is not None:
            notes.append(f"**The plan's {100 * PREREG_LATCH:.0f}% drawdown stop would have fired on "
                         f"{ist(at, with_day=True)} IST.** The dry run continues under the "
                         f"{100 * dd_limit:.0f}% stop, by amendment; real money keeps "
                         f"{100 * PREREG_LATCH:.0f}%.")

    fields = db.get("exits_fields") or []
    exits = {}
    for row in db.get("exits") or []:
        if isinstance(row, list) and len(row) == len(fields):
            e = dict(zip(fields, row))
            if isinstance(e.get("i"), int) and 0 <= e["i"] < len(rows):
                exits[(rows[e["i"]]["uid"], e["rule"])] = e

    # the self-check (prereg D7): one shadow row under the REAL rule per
    # closed position (the simulated account runs that rule). Checked trade by
    # trade, not by counting rows tagged with this run: a carried trade's row
    # may have been written by the previous run, and the table keeps the
    # first row per (position, rule) -- the 2026-10-06 report flagged trade 1
    # for exactly that.
    if "exits" in db and isinstance(db.get("shadow_counts"), dict):
        omitted_n = int(db.get("journal_omitted") or 0)
        number = {r["uid"]: omitted_n + i + 1 for i, r in enumerate(rows)}
        lacking = [number[r["uid"]] for r in closed_rows if (r["uid"], real) not in exits]
        if lacking:
            problems.append(f"self-check: {plural(len(lacking), 'closed trade')} "
                            f"(#{', #'.join(map(str, lacking))}) "
                            f"{'has' if len(lacking) == 1 else 'have'} no {EXIT_LABEL[real]} "
                            f"shadow record -- the exit comparison is incomplete")
    else:
        counts = db.get("shadow_counts")
        if isinstance(counts, dict) and n_closed and counts.get(real, 0) != n_closed:
            problems.append(f"self-check: {n_closed} closed trades but {counts.get(real, 0)} "
                            f"{EXIT_LABEL[real]} shadow records -- the exit comparison is "
                            f"incomplete")

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
    out = [f"# Dry run journal — {title_day} · {ist(now, with_day=True)} IST", ""]
    if problems:
        out.append("**⚠️ Needs your attention:**")
        out += [f"- {p}" for p in problems]
    else:
        out.append("**✅ All normal — nothing needs you.**")
    for n in notes:
        out.append(n)
    if log_notes:
        out.append("")
        out += [f"*Note:* {n}" for n in log_notes]
    out.append("")
    omitted = int(db.get("journal_omitted") or 0)
    uptime = dr.num(healthz.get("uptime_seconds")) if healthz else None
    proc_start = None if uptime is None else int(now.timestamp() - uptime)
    lines, totals = journal(rows, live, exits, omitted, real,
                            now_ts=int(now.timestamp()), proc_start=proc_start)
    out += lines

    facts = {"stack": stack, "day": now.strftime("%Y-%m-%d"),
             "verdict": "clear" if not problems else "attention", "problems": problems,
             "closed": n_closed, "total_r": round(totals["r"], 2),
             "total_pnl": round(totals["pnl"], 2), "exits_r": totals["by_exit"],
             "experiment": exp_id, "real_exit": real,
             "prereg_latch_at": None if not notes else at,
             "health_line": "healthy" if healthy else "not healthy", "day_of": title_day}
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
    ap.add_argument("--expect-db-snapshots", action="store_true",
                    help="the stack's Postgres runs on its host and is snapshotted daily; check it")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    day = (now - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    raw = dr.probe(args.instance_id, args.document, day, args.region)
    sec = dr.sections(raw)
    persist = dr.gunzip_section(sec, "PERSISTENCE")
    db = dr.as_json(persist.splitlines()[-1]) if persist.strip() else {}
    events = None
    if args.log_group:
        since_ms = int((now - dt.timedelta(hours=24)).timestamp() * 1000)
        events, _trunc = dr.log_events(args.log_group, since_ms, LOG_PATTERN, region=args.region)
    snaps: list[dict] | None | bool = False
    if args.expect_db_snapshots:
        ok, out = dr.aws("ec2", "describe-snapshots", "--owner-ids", "self", "--filters",
                         f"Name=tag:Stack,Values={args.stack}", "Name=tag:Role,Values=pgdata-backup",
                         region=args.region)
        snaps = out.get("Snapshots", []) if ok else None
    text, facts, problems = build(sec, db, now, stack=args.stack, errors_24h=None,
                                  probe_problems=list(dr.problems), log_events=events,
                                  snapshots=snaps)
    print(text)
    if args.facts_json:
        pathlib.Path(args.facts_json).write_text(json.dumps(facts, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
