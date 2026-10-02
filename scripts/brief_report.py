"""The dry run's daily report: the attention list, then the trade journal.

    python3 scripts/brief_report.py --instance-id i-... --stack dryrun \
        --document deltabt-paper-dryrun-monitor --log-group /deltabt/paper/dryrun/bot \
        --facts-json facts.json

Two parts and nothing else (owner, 2026-10-02: "a journal only with error
report like we have right now"):

1. Does anything need you -- the bot unhealthy or restarted, errors in its
   log, the 20% stop fired, data missing, the shadow self-check (prereg D7),
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


def journal(rows: list[dict], live: dict, exits: dict | None = None,
            omitted: int = 0) -> tuple[list[str], dict]:
    """Open positions, then every closed trade of the run under all three exits."""
    exits = exits or {}
    out: list[str] = []
    side = {1: "long", -1: "short"}
    # Numbered in order of opening, counting any oldest trades the probe left
    # out for space, so a trade keeps its number from one report to the next.
    num = {r["uid"]: omitted + i + 1 for i, r in enumerate(rows)}
    lev = lambda r: "—" if r.get("lev") is None else f"{r['lev']:.0f}x"
    mark = lambda r: f"{num[r['uid']]}{'*' if r.get('carried') else ''}"

    open_ = [r for r in rows if str(r.get("status")).upper() != "CLOSED"]
    closed = [r for r in rows if str(r.get("status")).upper() == "CLOSED"]

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
    partial = False
    if closed:
        body = []
        for r in closed:
            rr, pnl = r.get("r"), r.get("pnl")
            # dollars per R for this trade, from the real position, so the
            # other exits' R can be priced the same way
            per_r = (pnl / rr) if (pnl is not None and rr) else None
            lines = [("hold to 3R", r.get("closed"), r.get("exit"), r.get("why"), rr, pnl, True)]
            for rule in ("ladder", "trail"):
                e = exits.get((r["uid"], rule))
                if e is None:
                    lines.append((rule, None, None, "not recorded", None, None, True))
                else:
                    lines.append((rule, e.get("closed"), e.get("exit"), e.get("why"), e.get("r"),
                                  None if (per_r is None or e.get("r") is None) else e["r"] * per_r,
                                  bool(e.get("seen", True))))
            for k, (rule, c, px, why, er, epnl, seen) in enumerate(lines):
                first = k == 0
                held = (c - r["opened"]) if c and r.get("opened") else None
                label = rule + ("" if seen else " ~")
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
                key = "baseline" if first else rule
                if er is not None:
                    tot = totals[key]
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
    out.append("Hold to 3R is the real (simulated) trade; ladder and trail are where the other "
               "two exits would have closed it. R and P&L are after fees and funding. Lev is "
               "the leverage a real bot would have set on Delta.")
    real = totals["baseline"]
    return out, {"n": real["n"], "r": real["r"], "pnl": real["pnl"],
                 "by_exit": {k: round(v["r"], 2) for k, v in totals.items()}}


def build(sec: dict, db: dict, now: dt.datetime, *, stack: str,
          errors_24h: int | None, probe_problems: list[str]) -> tuple[str, dict, list[str]]:
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
    if errors_24h:
        problems.append(f"{plural(errors_24h, 'error')} in the bot's log in the last 24 hours")
    dd = dr.num(risk_api.get("drawdown_pct")) if risk_api else None
    dd_limit = dr.num(snap.get("max_drawdown_pct"))
    if dd is not None and dd_limit and dd >= dd_limit:
        problems.append("the 20% drawdown stop has fired: the run is over (no resume, by design)")

    rows = journal_rows(db)
    n_closed = sum(1 for r in rows if str(r.get("status")).upper() == "CLOSED") \
        + int(db.get("journal_omitted") or 0)

    # the self-check (prereg D7): one baseline shadow per closed position
    counts = db.get("shadow_counts")
    if isinstance(counts, dict) and n_closed and counts.get("baseline", 0) != n_closed:
        problems.append(f"self-check: {n_closed} closed trades but {counts.get('baseline', 0)} "
                        f"baseline shadow records -- the exit comparison is incomplete")

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
    out.append("")
    fields = db.get("exits_fields") or []
    exits = {}
    for row in db.get("exits") or []:
        if isinstance(row, list) and len(row) == len(fields):
            e = dict(zip(fields, row))
            if isinstance(e.get("i"), int) and 0 <= e["i"] < len(rows):
                exits[(rows[e["i"]]["uid"], e["rule"])] = e
    omitted = int(db.get("journal_omitted") or 0)
    lines, totals = journal(rows, live, exits, omitted)
    out += lines

    facts = {"stack": stack, "day": now.strftime("%Y-%m-%d"),
             "verdict": "clear" if not problems else "attention", "problems": problems,
             "closed": n_closed, "total_r": round(totals["r"], 2),
             "total_pnl": round(totals["pnl"], 2), "exits_r": totals["by_exit"],
             "experiment": exp_id,
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
