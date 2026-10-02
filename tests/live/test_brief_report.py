"""scripts/brief_report.py: the dry run's one-screen daily report (2026-10-02)."""
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
STARTED = "2026-10-02 10:39:17+00:00"
RISK = {"starting_equity": 250.0, "risk_per_trade": 0.02, "max_drawdown_pct": 0.2,
        "max_daily_loss_pct": 0.1, "max_consecutive_losses": 8}


def a_probe(*, closed=2, healthy=True, restarts=0, shadow=None, dry=None):
    trades = [{"symbol": "AKEUSD", "side": "LONG", "status": "CLOSED", "r": 2.9,
               "pnl": 14.5, "reason": "TAKE_PROFIT", "opened_ist": "2026-10-03 09:00:00 IST",
               "closed_ist": "2026-10-03 21:00:00 IST"},
              {"symbol": "BEATUSD", "side": "SHORT", "status": "CLOSED", "r": -1.02,
               "pnl": -5.1, "reason": "STOP_LOSS", "opened_ist": "2026-10-03 10:00:00 IST",
               "closed_ist": "2026-10-03 12:00:00 IST"}][:closed]
    sec = {
        "HEALTHZ": json.dumps({"status": "healthy" if healthy else "unhealthy", "ready": healthy,
                               "checks": [] if healthy else [{"name": "candles_fresh", "ok": False}],
                               "equity": 259.4, "uptime_seconds": 140000, "ws_connected": True}),
        "RISK": json.dumps({"equity": 259.4, "drawdown_pct": 0.0, "daily_loss_pct": 0.0,
                            "consecutive_losses": 1}),
        "TRADES": json.dumps(trades),
        "POSITIONS": json.dumps([]),
        "CONTAINER": f"img|Up 2 days\nuser=10001 readonly=true restarts={restarts} started=x",
    }
    if shadow is None:
        shadow = [["baseline", "AKEUSD", 1, 2.9, "TAKE_PROFIT", False, None],
                  ["ladder", "AKEUSD", 1, 0.45, "STOP_LOSS", True, None],
                  ["trail", "AKEUSD", 1, 1.6, "STOP_LOSS", True, 0.0006],
                  ["baseline", "BEATUSD", 2, -1.02, "STOP_LOSS", False, None],
                  ["ladder", "BEATUSD", 2, -1.02, "STOP_LOSS", False, None],
                  ["trail", "BEATUSD", 2, -1.02, "STOP_LOSS", False, None]][:3 * closed]
    db = {"experiments": [{"experiment_id": "DRY-MANUAL_SCALP_BOTH_T3-5-20261002-ed31cba",
                           "status": "RUNNING", "started_at": STARTED, "risk": RISK}],
          "shadow_exits": shadow, "evaluations_24h": 288,
          "rejections_24h": {"already holding an open position in AKEUSD": 40},
          "dry_run": dry if dry is not None else {
              "BEATUSD": {"orders": 1, "refused": 2, "reasons": {"spread": 2},
                          "spread_r": [0.12], "deviation_r": [0.05], "leverage": [11]},
              "AKEUSD": {"orders": 1, "refused": 0, "reasons": {},
                         "spread_r": [0.3], "deviation_r": [0.1], "leverage": [6]}}}
    return sec, db


def render(errors=0, **kw):
    sec, db = a_probe(**kw)
    return br.build(sec, db, NOW, stack="dryrun", errors_24h=errors, probe_problems=[])


def test_a_normal_day_says_so_first_and_fits_on_a_screen():
    text, facts, problems = render()
    assert not problems and facts["verdict"] == "clear"
    lines = text.splitlines()
    assert lines[0].startswith("# Dry run — day 2 of 21")
    assert "7:00 AM IST" in lines[0]
    assert "All normal" in lines[2]
    assert len(lines) <= 45, f"{len(lines)} lines: the report must stay one screen"


def test_progress_toward_the_read_is_stated():
    text, _, _ = render()
    assert "**Progress:** 2 of 100 trades closed · day 2 of 21" in text


def test_the_three_exits_are_compared_on_the_same_trades():
    text, facts, _ = render()
    assert facts["exits_r"] == {"baseline": 1.88, "ladder": -0.57, "trail": 0.58}
    assert "hold to 3R" in text and "ladder" in text and "trail" in text
    assert "At $5.00 per R" in text, "dollars at the pilot's 2% of $250"
    assert "not whether any has an edge" in text


def test_what_a_real_bot_would_have_done_is_per_symbol_and_plain():
    text, _, _ = render()
    assert "**BEATUSD**: 1 order would have been sent; 2 refused (2 spread)" in text
    assert "typical spread 0.12R, leverage 11x" in text


def test_the_account_is_measured_against_its_own_limits():
    text, _, _ = render()
    assert "## Simulated $250 account" in text
    assert "Equity $259.40 (+3.8%)" in text
    assert "drawdown 0.0% of the 20% limit" in text and "losing streak 1 of 8" in text


def test_problems_lead_the_report():
    text, facts, problems = render(healthy=False, restarts=2, errors=3)
    assert facts["verdict"] == "attention"
    assert text.splitlines()[2] == "**⚠️ Needs your attention:**"
    joined = " ".join(problems)
    assert "not healthy (candles_fresh)" in joined
    assert "restarted 2 times" in joined and "3 errors" in joined


def test_a_missing_shadow_record_is_flagged_by_the_self_check():
    sec, db = a_probe()
    db["shadow_exits"] = db["shadow_exits"][:3]           # one position unrecorded
    _, _, problems = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert any("self-check" in p for p in problems)


def test_before_the_first_trade_it_says_so_plainly():
    text, _, problems = render(closed=0, shadow=[], dry={})
    assert not problems
    assert "No trade has closed yet" in text
    assert "No entry signal yet" in text


def test_the_thin_symbol_gap_check_alone_does_not_raise_the_alarm():
    """no_recent_gaps is red most of the time on BEAT/BANK (no trade in a
    minute); the first real report (2026-10-02) flagged it, and a report that
    is red every day stops being read. It is shown under Health instead."""
    sec, db = a_probe()
    sec["HEALTHZ"] = json.dumps({
        "status": "unhealthy", "ready": True, "equity": 250.0, "uptime_seconds": 1800,
        "ws_connected": True,
        "checks": [{"name": "no_recent_gaps", "ok": False, "detail": "4 gap(s) in the recent window"},
                   {"name": "candles_fresh", "ok": True}]})
    text, facts, problems = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert not problems and facts["verdict"] == "clear"
    assert "4 gap(s) in the recent window (normal on thin symbols)" in text


def test_a_real_health_failure_still_raises_it_next_to_gaps():
    sec, db = a_probe()
    sec["HEALTHZ"] = json.dumps({
        "status": "unhealthy", "ready": True, "equity": 250.0, "ws_connected": False,
        "checks": [{"name": "no_recent_gaps", "ok": False},
                   {"name": "websocket_fresh", "ok": False}]})
    _, _, problems = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert problems == ["the bot is not healthy (websocket_fresh)"]


def test_progress_and_the_self_check_count_from_the_database_not_the_50_row_api():
    """/api/trades returns at most 50 rows; the read waits for 100."""
    sec, db = a_probe()
    db["closed_trades_total"] = 73
    db["shadow_exits"] = [[rule, "BEATUSD", i, 0.1, "STOP_LOSS", False, None]
                          for i in range(73) for rule in ("baseline", "ladder", "trail")]
    text, _, problems = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert "**Progress:** 73 of 100 trades closed" in text
    assert not problems


def test_entries_the_simulator_did_not_open_are_flagged():
    """The 2026-10-02 defect: three AKEUSD entries passed the live checks and
    the paper broker opened none, so the dry run held nothing a real bot
    would have held. The report must say so, per symbol."""
    sec, db = a_probe()
    db["opened_by_symbol"] = {"BEATUSD": 1}
    db["dry_run"]["AKEUSD"]["orders"] = 3
    text, _, problems = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert any(p.startswith("AKEUSD: 3 entries passed the live checks but the simulator opened 0")
               for p in problems)
    assert "**AKEUSD**: 3 orders would have been sent (simulated: 0 opened)" in text
    assert "**BEATUSD**: 1 order would have been sent (simulated: 1 opened)" in text


def test_trades_open_across_a_restart_are_called_approximate():
    sec, db = a_probe()
    db["shadow_exits"] = [row + [rule_seen] for row, rule_seen in
                          zip(db["shadow_exits"], [False, False, False, True, True, True])]
    text, _, problems = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert "1 trade was open across a bot restart" in text
    assert not problems


def test_a_position_carried_over_from_the_previous_run_counts():
    """Owner, 2026-10-02: a position restored from the retired experiment
    (same strategy, sizing and gates) counts in the new run. Its shadow rows
    land under the new id, so the count and the self-check must include it."""
    sec, db = a_probe()
    t_recent = int(NOW.timestamp()) - 3600
    db["closed_in_run"] = [["AKEUSD", t_recent, 2.9, "TAKE_PROFIT", True],
                           ["BEATUSD", t_recent, -1.02, "STOP_LOSS", False]]
    text, facts, problems = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert "**Progress:** 2 of 100 trades closed" in text
    assert "Includes 1 trade carried over from the previous run." in text
    assert "- 2 trades closed under hold-to-3R (+1.88R in total)." in text
    assert not problems, problems
    assert facts["closed"] == 2


def test_the_self_check_still_fires_when_a_shadow_record_is_missing():
    sec, db = a_probe()
    t_recent = int(NOW.timestamp()) - 3600
    db["closed_in_run"] = [["AKEUSD", t_recent, 2.9, "TAKE_PROFIT", True],
                           ["BEATUSD", t_recent, -1.02, "STOP_LOSS", False]]
    db["shadow_exits"] = db["shadow_exits"][:3]
    _, _, problems = br.build(sec, db, NOW, stack="dryrun", errors_24h=0, probe_problems=[])
    assert any("self-check: 2 closed trades but 1 baseline" in p for p in problems)
