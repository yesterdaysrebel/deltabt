"""scripts/brief_report.py: the dry run's daily report -- the attention list,
then the trade journal, and nothing else (owner, 2026-10-02)."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("brief_report", ROOT / "scripts/brief_report.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

NOW = dt.datetime(2026, 10, 4, 1, 30, tzinfo=dt.timezone.utc)      # 7:00 AM IST
STARTED = "2026-10-02 11:52:38+00:00"
RISK = {"starting_equity": 250.0, "risk_per_trade": 0.02, "max_drawdown_pct": 0.2,
        "max_daily_loss_pct": 0.1, "max_consecutive_losses": 8}
FIELDS = ["uid", "symbol", "side", "status", "qty", "entry", "sl", "tp", "notional",
          "opened", "closed", "exit", "why", "r", "pnl", "costs", "carried", "lev"]
T = lambda s: int(dt.datetime.fromisoformat(s).timestamp())          # noqa: E731


def trade(uid, symbol, side, status, *, opened, closed=None, exit=None, why=None,
          r=None, pnl=None, carried=False, lev=6.0, qty=1, entry=0.0323638,
          sl=0.0334511, tp=0.0290679, notional=323.6, costs=0.33):
    return [uid, symbol, side, status, qty, entry, sl, tp, notional,
            T(opened), None if closed is None else T(closed), exit, why, r, pnl,
            costs, carried, lev]


EXITS_FIELDS = ["i", "rule", "closed", "exit", "why", "r", "seen"]
DEFAULT_EXITS = [
    [0, "ladder", T("2026-10-02T19:40:00+00:00"), 0.0318201, "STOP_LOSS", 0.45, True],
    [0, "trail", T("2026-10-02T21:10:00+00:00"), 0.0306100, "STOP_LOSS", 1.61, True],
    [1, "ladder", T("2026-10-03T05:10:00+00:00"), 0.8812, "STOP_LOSS", -1.03, True],
    [1, "trail", T("2026-10-03T05:10:00+00:00"), 0.8812, "STOP_LOSS", -1.03, True],
]


BASELINE_EXITS = [
    [0, "baseline", T("2026-10-03T03:42:00+00:00"), 0.0290679, "TAKE_PROFIT", 2.93, True],
    [1, "baseline", T("2026-10-03T05:10:00+00:00"), 0.8812, "STOP_LOSS", -1.03, True],
]
VERSION = {"baseline": "manual_scalp_both_t3@5m@41e764beceaf",
           "trail": "manual_scalp_both_t3_trail@5m@cf9917a73c61",
           "ladder": "manual_scalp_both_t3_ladder@5m@2e8bd2529685"}


def a_probe(*, healthy=True, restarts=0, journal=None, shadow=None, dry=None, opened=None,
            exits=None, real="baseline", risk=None):
    if journal is None:
        journal = [
            trade("p1", "AKEUSD", -1, "CLOSED", opened="2026-10-02T11:30:06+00:00",
                  closed="2026-10-03T03:42:00+00:00", exit=0.0290679, why="TAKE_PROFIT",
                  r=2.93, pnl=31.84, carried=True),
            trade("p2", "BEATUSD", 1, "CLOSED", opened="2026-10-03T04:00:00+00:00",
                  closed="2026-10-03T05:10:00+00:00", exit=0.8812, why="STOP_LOSS",
                  r=-1.03, pnl=-5.15, qty=277, entry=0.9101, sl=0.8920, tp=0.9644,
                  notional=252.1, lev=12.0),
            trade("p3", "BANKUSD", 1, "OPEN", opened="2026-10-04T00:15:00+00:00",
                  qty=125, entry=0.03061, sl=0.02981, tp=0.03301, notional=382.6, lev=None),
        ]
    sec = {
        "HEALTHZ": json.dumps({"status": "healthy" if healthy else "unhealthy", "ready": healthy,
                               "checks": [] if healthy else [{"name": "candles_fresh", "ok": False}],
                               "equity": 276.7, "uptime_seconds": 140000, "ws_connected": True}),
        "RISK": json.dumps({"equity": 276.7, "drawdown_pct": 0.0, "daily_loss_pct": 0.0,
                            "consecutive_losses": 1}),
        "POSITIONS": json.dumps([{"position_uid": "p3", "current_price": 0.03102,
                                  "r": 0.51, "unrealized_pnl": 2.55}]),
        "CONTAINER": f"img|Up 2 days\nuser=10001 readonly=true restarts={restarts} started=x",
    }
    n_closed = sum(1 for j in journal if j[3] == "CLOSED")
    db = {"experiments": [{"experiment_id": "DRY-MANUAL_SCALP_BOTH_T3-5-20261002-c4b719a",
                           "status": "RUNNING", "started_at": STARTED,
                           "strategy_version": VERSION[real], "risk": risk or RISK}],
          "journal_fields": FIELDS, "journal": journal,
          "exits_fields": EXITS_FIELDS,
          "exits": BASELINE_EXITS + DEFAULT_EXITS if exits is None else exits,
          "shadow_counts": shadow if shadow is not None else
          {"baseline": n_closed, "ladder": n_closed, "trail": n_closed},
          "opened_by_symbol": opened if opened is not None else {"BEATUSD": 1, "BANKUSD": 1},
          "dry_run": dry if dry is not None else {
              "BEATUSD": {"orders": 1, "refused": 0, "reasons": {}},
              "BANKUSD": {"orders": 1, "refused": 2, "reasons": {"spread": 2}}}}
    return sec, db


def render(errors=0, **kw):
    sec, db = a_probe(**kw)
    return br.build(sec, db, NOW, stack="dryrun", errors_24h=errors, probe_problems=[])


def test_the_report_is_the_attention_list_then_the_journal_and_nothing_else():
    text, facts, problems = render()
    assert not problems and facts["verdict"] == "clear"
    lines = text.splitlines()
    assert lines[0] == "# Dry run journal — day 2 of 21 · Sun 04 Oct, 7:00 AM IST"
    assert lines[2] == "**✅ All normal — nothing needs you.**"
    headers = [ln for ln in lines if ln.startswith("## ")]
    assert headers == ["## Open now (1)",
                       "## Closed so far (2 of 100 for the read) — the three exits on each trade"]


def _block(text, mark):
    """The three lines of one closed trade: hold to 3R, ladder, trail."""
    lines = text.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.lstrip().startswith(mark + " ")
             and "hold to 3R" in ln)
    return lines[i:i + 3]


def test_each_closed_trade_shows_its_facts_and_all_three_exits():
    text, _, _ = render()
    real, ladder, trail = _block(text, "2")
    for cell in ("BEATUSD", "long", "277", "12x", "03 Oct 9:30 AM", "0.9101", "0.892",
                 "0.9644", "hold to 3R", "03 Oct 10:40 AM", "0.8812", "stop", "1h 10m",
                 "-1.03", "-5.15"):
        assert cell in real, f"{cell!r} missing from: {real}"
    assert ladder.split()[0] == "ladder" and trail.split()[0] == "trail", \
        "the facts are written once; the exit lines below carry only the exit"


def test_the_other_exits_are_priced_in_dollars_from_the_real_trade():
    """$ per R comes from the real position (31.84 / 2.93), so a ladder exit
    at +0.45R is +4.89 and a trail exit at +1.61R is +17.50."""
    text, _, _ = render()
    real, ladder, trail = _block(text, "1*")
    assert "target" in real and "+2.93" in real and "+31.84" in real
    for cell in ("03 Oct 1:10 AM", "0.0318201", "stop", "8h 9m", "+0.45", "+4.89"):
        assert cell in ladder, f"{cell!r} missing from: {ladder}"
    for cell in ("0.03061", "+1.61", "+17.50"):
        assert cell in trail, f"{cell!r} missing from: {trail}"


def test_totals_compare_the_three_exits_on_the_same_trades():
    text, facts, _ = render()
    lines = text.splitlines()
    i = lines.index("Totals on the same trades:")
    rows = {ln.split("  ")[0].strip(): ln.split() for ln in lines[i + 3:i + 6]}
    assert rows["hold to 3R"][-6:] == ["2", "1", "1", "+1.90", "+0.95", "+26.69"]
    assert rows["ladder"][-6:] == ["2", "1", "0", "-0.58", "-0.29", "-0.26"]
    assert rows["trail"][-6:] == ["2", "1", "0", "+0.58", "+0.29", "+12.35"]
    assert facts["exits_r"] == {"baseline": 1.9, "ladder": -0.58, "trail": 0.58}
    assert "not whether any has an edge" in text


def test_an_exit_still_running_after_the_real_trade_says_still_open():
    """The other rules keep running after the real position's own stop or
    target; their rows arrive when each closes (2026-10-06: the report said
    "not recorded", which read as data lost)."""
    text, _, problems = render(exits=BASELINE_EXITS, shadow={"baseline": 2, "ladder": 0, "trail": 0})
    _, ladder, trail = _block(text, "2")
    assert "still open" in ladder and "still open" in trail
    assert "still open: the real trade has closed" in text and "lost: restart:" not in text
    assert not problems, "an exit still running is not a fault"


def test_an_exit_lost_in_a_restart_after_the_real_close_says_so():
    """The running legs live in memory; a restart after the real close loses them."""
    sec, db = a_probe(exits=BASELINE_EXITS, shadow={"baseline": 2, "ladder": 0, "trail": 0})
    sec["HEALTHZ"] = sec["HEALTHZ"].replace('"uptime_seconds": 140000', '"uptime_seconds": 3600')
    text, _, _ = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    _, ladder, trail = _block(text, "2")
    assert "lost: restart" in ladder and "lost: restart" in trail
    assert "lost: restart: the bot restarted after the real trade closed" in text


def test_an_exit_older_than_the_time_stop_is_not_still_open():
    late = dt.datetime(2026, 10, 7, 1, 30, tzinfo=dt.timezone.utc)
    sec, db = a_probe(exits=BASELINE_EXITS, shadow={"baseline": 2, "ladder": 0, "trail": 0})
    sec["HEALTHZ"] = sec["HEALTHZ"].replace('"uptime_seconds": 140000', '"uptime_seconds": 400000')
    text, _, _ = br.build(sec, db, late, stack="dryrun", errors_24h=0, probe_problems=[])
    assert "lost: restart" in _block(text, "2")[1]


def test_trades_are_listed_newest_first_and_keep_their_numbers():
    journal = [trade(f"p{i}", "AKEUSD", -1, "CLOSED", opened=f"2026-10-03T{10 + i:02d}:00:00+00:00",
                     closed=f"2026-10-03T{11 + i:02d}:00:00+00:00", exit=0.0334, why="STOP_LOSS",
                     r=-1.0, pnl=-10.0) for i in range(3)]
    journal += [trade(f"o{i}", "BEATUSD", 1, "OPEN", opened=f"2026-10-04T0{i}:10:00+00:00",
                      qty=258, entry=0.0859, sl=0.0839, tp=0.0918) for i in range(2)]
    text, _, _ = render(journal=journal, exits=[])
    lines = text.splitlines()
    closed = [ln.split()[0] for ln in lines if "AKEUSD" in ln and "hold to 3R" in ln]
    assert closed == ["3", "2", "1"]
    open_ = [ln.split()[0] for ln in lines if "BEATUSD" in ln]
    assert open_ == ["5", "4"]


def test_an_exit_open_across_a_restart_is_marked_approximate():
    exits = [row[:6] + [False] if row[0] == 0 else row for row in DEFAULT_EXITS]
    text, _, _ = render(exits=exits)
    _, ladder, _ = _block(text, "1*")
    assert ladder.split()[0:2] == ["ladder", "~"]
    assert "~ the bot restarted while this trade was open" in text


def test_an_open_trade_shows_where_it_stands_now():
    text, _, _ = render()
    row = next(ln for ln in text.splitlines() if "BANKUSD" in ln)
    for cell in ("long", "125", "04 Oct 5:45 AM", "0.03061", "0.02981", "0.03301",
                 "0.03102", "+0.51", "+2.55"):
        assert cell in row, f"{cell!r} missing from: {row}"
    assert "  —  " in row, "unknown leverage is a dash, not a guess"


def test_a_carried_over_trade_is_marked_and_counted():
    text, _, _ = render()
    row = next(ln for ln in text.splitlines() if "AKEUSD" in ln and "hold to 3R" in ln)
    assert row.lstrip().startswith("1*")
    assert "Trades marked * opened under the previous run and count in this one" in text


def test_every_closed_trade_is_listed_past_the_apis_50_row_limit():
    journal = [trade(f"p{i}", "BEATUSD", 1, "CLOSED", opened="2026-10-03T04:00:00+00:00",
                     closed="2026-10-03T05:00:00+00:00", exit=0.88, why="STOP_LOSS",
                     r=-1.0, pnl=-5.0) for i in range(73)]
    exits = [[i, "baseline", T("2026-10-03T05:00:00+00:00"), 0.88, "STOP_LOSS", -1.0, True]
             for i in range(73)]
    text, _, problems = render(journal=journal, exits=exits)
    assert "## Closed so far (73 of 100 for the read)" in text
    assert sum(1 for ln in text.splitlines() if "BEATUSD" in ln and "hold to 3R" in ln) == 73
    assert not problems


def test_before_any_trade_it_says_so_plainly():
    text, _, problems = render(journal=[], dry={}, opened={})
    assert not problems
    assert "Nothing open." in text and "No trade has closed yet." in text


def test_problems_lead_the_report():
    text, facts, problems = render(healthy=False, restarts=2, errors=3)
    assert facts["verdict"] == "attention"
    assert text.splitlines()[2] == "**⚠️ Needs your attention:**"
    joined = " ".join(problems)
    assert "not healthy (candles_fresh)" in joined
    assert "restarted 2 times" in joined and "3 errors" in joined


def test_the_thin_symbol_gap_check_alone_does_not_raise_the_alarm():
    sec, db = a_probe()
    sec["HEALTHZ"] = json.dumps({
        "status": "unhealthy", "ready": True, "ws_connected": True,
        "checks": [{"name": "no_recent_gaps", "ok": False, "detail": "4 gap(s)"},
                   {"name": "candles_fresh", "ok": True}]})
    _, facts, problems = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert not problems and facts["verdict"] == "clear"


def test_a_real_health_failure_still_raises_it_next_to_gaps():
    sec, db = a_probe()
    sec["HEALTHZ"] = json.dumps({
        "status": "unhealthy", "ready": True, "ws_connected": False,
        "checks": [{"name": "no_recent_gaps", "ok": False},
                   {"name": "websocket_fresh", "ok": False}]})
    _, _, problems = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert problems == ["the bot is not healthy (websocket_fresh)"]


def test_a_missing_shadow_record_is_flagged_by_the_self_check():
    _, _, problems = render(exits=BASELINE_EXITS[:1] + DEFAULT_EXITS)
    assert any("self-check: 1 closed trade (#2) has no hold to 3R shadow record" in p
               for p in problems)


def test_the_self_check_counts_a_carried_trades_row_from_the_previous_run():
    """2026-10-06: 16 closed, 15 trail rows tagged with this run. Trade 1's row
    was written by the previous run (one row per position and rule, first
    wins), so counting rows by run ID raised a false alarm. The check goes
    trade by trade."""
    _, _, problems = render(shadow={"baseline": 1, "ladder": 1, "trail": 1})
    assert not any("self-check" in p for p in problems)


def test_entries_the_simulator_did_not_open_are_flagged():
    """The 2026-10-02 defect: AKEUSD entries passed the live checks and the
    paper broker opened none."""
    dry = {"AKEUSD": {"orders": 3, "refused": 0, "reasons": {}}}
    _, _, problems = render(dry=dry, opened={})
    assert any(p.startswith("AKEUSD: 3 entries passed the live checks but the simulator opened 0")
               for p in problems)


def test_a_missing_probe_is_a_problem_not_an_empty_journal():
    sec, _ = a_probe()
    _, _, problems = br.build(sec, {}, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert any("database figures did not arrive" in p for p in problems)


def test_trades_left_out_for_space_are_counted_and_said():
    sec, db = a_probe()
    db["journal_omitted"] = 40
    db["shadow_counts"] = {"baseline": 42, "ladder": 42, "trail": 42}
    text, facts, problems = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert "## Closed so far (42 of 100 for the read)" in text
    assert "The oldest 40 closed trades are left out to fit the report's size limit" in text
    assert _block(text, "41*")[0].split()[1] == "AKEUSD", "numbers continue past the omitted"
    assert not problems and facts["closed"] == 42


# -- the account runs the TRAIL (amendment 2026-10-03) ----------------------

def test_when_the_account_runs_the_trail_its_line_is_the_real_one():
    """The trail's line comes from the position; hold-to-3R and the ladder
    come from their shadow rows."""
    sec, db = a_probe(real="trail", exits=BASELINE_EXITS + DEFAULT_EXITS)
    text, facts, problems = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert not problems and facts["real_exit"] == "trail"
    blk = _block_by_first(text, "1*")
    assert blk[0].split()[0] == "1*" and "trail" in blk[0] and "+2.93" in blk[0]
    assert blk[1].split()[0:3] == ["hold", "to", "3R"] and "+2.93" in blk[1]
    assert blk[2].split()[0] == "ladder" and "+0.45" in blk[2]
    assert "Trail is the real (simulated) trade; hold to 3R and ladder are where" in text


def test_the_self_check_follows_the_real_rule():
    no_trail_2 = [e for e in BASELINE_EXITS + DEFAULT_EXITS if (e[0], e[1]) != (1, "trail")]
    _, _, problems = a_probe_problems(real="trail", exits=no_trail_2)
    assert any("self-check: 1 closed trade (#2) has no trail shadow" in p for p in problems)
    no_base_2 = [e for e in BASELINE_EXITS + DEFAULT_EXITS if (e[0], e[1]) != (1, "baseline")]
    _, _, problems = a_probe_problems(real="trail", exits=no_base_2)
    assert not any("self-check" in p for p in problems), "a missing hold-to-3R row is not a D7 fault"


def a_probe_problems(**kw):
    sec, db = a_probe(**kw)
    return br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])


def _block_by_first(text, mark):
    lines = text.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.lstrip().startswith(mark + " "))
    return lines[i:i + 3]


def _losing_journal(n):
    return [trade(f"L{i}", "AKEUSD", -1, "CLOSED", opened=f"2026-10-03T{10 + i:02d}:00:00+00:00",
                  closed=f"2026-10-03T{11 + i:02d}:00:00+00:00", exit=0.0334, why="STOP_LOSS",
                  r=-1.05, pnl=-11.5) for i in range(n)]


def test_with_the_stop_lifted_the_report_states_when_20_percent_would_have_fired():
    """Amendment 2026-10-03: the dry run runs under a 50% stop; the plan's 20%
    is recorded, not enforced. Five $11.50 losses on $250 = 23%."""
    lifted = dict(RISK, max_drawdown_pct=0.5)
    sec, db = a_probe(risk=lifted, journal=_losing_journal(5), exits=[],
                      shadow={"baseline": 5, "ladder": 5, "trail": 5})
    sec["RISK"] = json.dumps({"equity": 192.5, "drawdown_pct": 23.0, "daily_loss_pct": 23.0,
                              "consecutive_losses": 5})
    text, facts, problems = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert not any("drawdown stop has fired" in p for p in problems), "50% has not fired"
    assert "**The plan's 20% drawdown stop would have fired on Sat 03 Oct, 8:30 PM IST.**" in text
    assert "continues under the 50% stop" in text
    assert facts["prereg_latch_at"] == T("2026-10-03T15:00:00+00:00")


def test_under_the_lifted_stop_four_losses_do_not_trigger_the_note():
    lifted = dict(RISK, max_drawdown_pct=0.5)
    sec, db = a_probe(risk=lifted, journal=_losing_journal(4), exits=[],
                      shadow={"baseline": 4, "ladder": 4, "trail": 4})
    text, facts, _ = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert "would have fired" not in text and facts["prereg_latch_at"] is None


def test_the_configured_stop_is_named_when_it_fires():
    lifted = dict(RISK, max_drawdown_pct=0.5)
    sec, db = a_probe(risk=lifted)
    sec["RISK"] = json.dumps({"equity": 120.0, "drawdown_pct": 52.0, "daily_loss_pct": 0.0,
                              "consecutive_losses": 9})
    _, _, problems = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert any("the 50% drawdown stop has fired" in p for p in problems)


def _with_drawdown(pct):
    sec, db = a_probe()
    sec["RISK"] = json.dumps({"equity": 229.38, "drawdown_pct": pct, "daily_loss_pct": 4.6,
                              "consecutive_losses": 2})
    return br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])


def test_a_9_percent_drawdown_does_not_read_as_the_20_percent_stop():
    """/api/risk gives PERCENT; the limit is a fraction. The 2026-10-03 report
    compared them raw and said the run was over at 9.27%."""
    _, _, problems = _with_drawdown(9.27)
    assert not any("drawdown stop" in p for p in problems), problems


def test_the_20_percent_stop_is_still_reported_when_it_fires():
    _, _, problems = _with_drawdown(20.4)
    assert any("20% drawdown stop has fired" in p for p in problems)


# -- the log: show each error; a Delta feed drop that recovered is a note -----

def _ev(ts, level, message, logger="app.market_data.delta_ws"):
    return {"ts": ts, "level": level, "logger": logger, "message": message}


#: The two real ERROR lines of 2026-10-04/05, each followed by a reconnect.
TODAYS_LOG = [
    _ev("2026-10-03T17:58:59.830Z", "ERROR", "feed error: no close frame received or sent"),
    _ev("2026-10-03T17:59:01.120Z", "INFO", "subscribed"),
    _ev("2026-10-04T01:04:47.660Z", "ERROR", "feed went silent with the socket still open; forcing a reconnect"),
    _ev("2026-10-04T01:04:50.300Z", "INFO", "subscribed"),
]


def _with_log(events):
    sec, db = a_probe()
    return br.build(sec, db, NOW, stack="dryrun", errors_24h=None, probe_problems=[], log_events=events)


def test_feed_drops_the_bot_recovered_from_are_a_note_not_an_alarm():
    """2026-10-05: both 'errors' were Delta's feed stalling; the bot was back in
    2-3 seconds. The report called that 'needs your attention' every day."""
    text, facts, problems = _with_log(TODAYS_LOG)
    assert not problems and facts["verdict"] == "clear"
    assert ("*Note:* Delta's price feed dropped 2 times and the bot reconnected within seconds each time "
            "(11:28 PM, 6:34 AM IST). Nothing needs you.") in text


def test_a_feed_drop_with_no_reconnect_is_an_alarm():
    _, _, problems = _with_log(TODAYS_LOG[:1] + TODAYS_LOG[2:])
    assert problems == ["Delta's price feed dropped at 11:28 PM IST and no reconnect followed within 60 s"]


def test_many_recovered_drops_in_a_day_are_worth_a_look():
    events = []
    for h in range(7):
        events += [_ev(f"2026-10-04T{h:02d}:10:00.000Z", "ERROR", "feed went silent with the socket still open"),
                   _ev(f"2026-10-04T{h:02d}:10:03.000Z", "INFO", "subscribed")]
    _, _, problems = _with_log(events)
    assert problems == ["Delta's price feed dropped 7 times in 24 hours (each recovered) -- more than the usual "
                        "few; worth a look"]


def test_any_other_error_is_an_alarm_with_its_time_and_message():
    events = TODAYS_LOG + [_ev("2026-10-04T00:15:00.000Z", "ERROR", "shadow exits failed on a tick; continuing\nTraceback ...",
                               logger="app.runtime.bot")]
    text, _, problems = _with_log(events)
    assert problems == ["1 error in the bot's log in the last 24 hours: "
                        "5:45 AM IST ERROR (bot): shadow exits failed on a tick; continuing"]
    assert "Delta's price feed dropped 2 times" in text, "the feed note still appears"


def test_a_drop_moments_before_the_report_is_not_called_unrecovered():
    events = [_ev("2026-10-04T01:29:50.000Z", "ERROR", "feed went silent with the socket still open")]
    text, _, problems = _with_log(events)
    assert not problems and "moments before this report" in text


def test_a_quiet_log_says_nothing():
    text, facts, problems = _with_log([])
    assert not problems and "price feed" not in text



# -- the host Postgres volume's daily snapshot (infra/terraform/db_host.tf) ----

def _with_snaps(snaps):
    sec, db = a_probe()
    return br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[], snapshots=snaps)


def test_a_recent_snapshot_is_a_note():
    text, _, problems = _with_snaps([{"State": "completed", "StartTime": "2026-10-03T18:30:12.000Z"},
                                     {"State": "completed", "StartTime": "2026-10-02T18:30:09.000Z"}])
    assert not problems
    assert "*Note:* Database backed up 7 hours ago (2 daily snapshots kept)." in text


def test_a_stale_or_missing_snapshot_is_an_alarm():
    _, _, problems = _with_snaps([{"State": "completed", "StartTime": "2026-10-02T12:00:00.000Z"}])
    assert problems == ["the newest database snapshot is 38 hours old: the daily backup has stopped"]
    _, _, problems = _with_snaps([{"State": "pending", "StartTime": "2026-10-04T01:00:00.000Z"}])
    assert problems == ["no completed database snapshot exists: the daily backup has not run"]
    _, _, problems = _with_snaps(None)
    assert problems == ["could not read the database snapshots (backup state unknown)"]


def test_snapshots_are_not_checked_unless_asked():
    sec, db = a_probe()
    text, _, problems = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert not problems and "backed up" not in text
