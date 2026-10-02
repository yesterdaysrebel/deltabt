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


def a_probe(*, healthy=True, restarts=0, journal=None, shadow=None, dry=None, opened=None):
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
                           "status": "RUNNING", "started_at": STARTED, "risk": RISK}],
          "journal_fields": FIELDS, "journal": journal,
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
    assert headers == ["## Open now (1)", "## Closed so far (2 of 100 for the read)"]


def test_a_closed_trade_shows_times_prices_size_leverage_and_result():
    text, _, _ = render()
    row = next(ln for ln in text.splitlines() if ln.lstrip().startswith("2 ") and "BEATUSD" in ln)
    for cell in ("BEATUSD", "long", "277", "252", "12x", "03 Oct 9:30 AM", "0.9101",
                 "0.892", "0.9644", "03 Oct 10:40 AM", "0.8812", "stop", "1h 10m",
                 "-1.03", "-5.15"):
        assert cell in row, f"{cell!r} missing from: {row}"


def test_an_open_trade_shows_where_it_stands_now():
    text, _, _ = render()
    row = next(ln for ln in text.splitlines() if "BANKUSD" in ln)
    for cell in ("long", "125", "04 Oct 5:45 AM", "0.03061", "0.02981", "0.03301",
                 "0.03102", "+0.51", "+2.55"):
        assert cell in row, f"{cell!r} missing from: {row}"
    assert "  —  " in row, "unknown leverage is a dash, not a guess"


def test_totals_are_after_fees_and_funding():
    text, facts, _ = render()
    assert "Total: 1 won, 1 lost · +1.90R · $+26.69 after $0.66 of fees and funding." in text
    assert facts["closed"] == 2 and facts["total_r"] == 1.9


def test_a_carried_over_trade_is_marked_and_counted():
    text, _, _ = render()
    row = next(ln for ln in text.splitlines() if "AKEUSD" in ln)
    assert row.lstrip().startswith("1*")
    assert "Trades marked * opened under the previous run and count in this one" in text


def test_every_closed_trade_is_listed_past_the_apis_50_row_limit():
    journal = [trade(f"p{i}", "BEATUSD", 1, "CLOSED", opened="2026-10-03T04:00:00+00:00",
                     closed="2026-10-03T05:00:00+00:00", exit=0.88, why="STOP_LOSS",
                     r=-1.0, pnl=-5.0) for i in range(73)]
    text, _, problems = render(journal=journal)
    assert "## Closed so far (73 of 100 for the read)" in text
    assert sum(1 for ln in text.splitlines() if "BEATUSD" in ln) == 73
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
    _, _, problems = render(shadow={"baseline": 1, "ladder": 1, "trail": 1})
    assert any("self-check: 2 closed trades but 1 baseline" in p for p in problems)


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
