"""The report answers "does this need me" before it shows its evidence.

WHY THIS EXISTS

    The daily report grew a section every time something went wrong and
    nobody could tell from the report -- which is the right instinct, and it
    produced 130 lines for a day with ONE closed trade. The verdict sat at the
    bottom, so the only question a 02:00 reader has could only be answered by
    scrolling past everything.

    Worse, most of those lines were CONSTANT: the same explanation of why
    breakers are disabled, the same paragraph on cost, the same note that a
    small sample proves nothing, every night. Two arms now run, so two of these
    arrive nightly, and a summary nobody finishes is a summary nobody reads.

    Nothing was deleted. The evidence still prints; it prints BELOW the answer,
    and the paragraphs that never change are now one clause each or are
    conditional on there being something to explain.

    These tests pin the ordering and the suppression. They do not pin the
    wording of any section: that is allowed to change, the shape is not.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
_REPORT = ROOT / "scripts/daily_report.py"
_DIGEST = ROOT / "scripts/arms_digest.py"


@pytest.fixture(scope="module")
def dr():
    spec = importlib.util.spec_from_file_location("daily_report", _REPORT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def source() -> str:
    return _REPORT.read_text()


class _Args:
    stack = "atr"
    environment = "paper"


FULL_FACTS = {
    "strategy": "manual_scalp_t4@5m@68a126f7",
    "experiment": "MANUAL_SCALP_T4-5-20260904-7e1fdb0",
    "day_of": "day 1/30",
    "closed_line": "2 · 1 won · +0.40R",
    "open_line": "1 · AKEUSD SHORT +0.32R",
    "equity_line": "10000.00 · 0.00% from peak",
    "health_line": "readyz healthy · healthz ok",
    "sample_line": "2/30 closed — INSUFFICIENT",
}


def _head(dr, facts, problems):
    import datetime
    return dr.headline("2026-09-04",
                       datetime.datetime(2026, 9, 4, tzinfo=datetime.timezone.utc),
                       _Args(), facts, problems, [])


# --- the answer comes first -------------------------------------------------

def test_the_verdict_is_in_the_first_lines(dr):
    text = _head(dr, FULL_FACTS, [])
    assert "ALL CLEAR" in text.splitlines()[3]


def test_a_problem_is_named_in_the_headline_not_only_at_the_bottom(dr):
    text = _head(dr, FULL_FACTS, ["readyz not ready: ['database_writable']"])
    assert "NEEDS ATTENTION" in text
    assert "database_writable" in text, (
        "the headline says something is wrong without saying what; the reader "
        "still has to scroll, which is the thing this replaced")


def test_the_headline_carries_what_moved(dr):
    text = _head(dr, FULL_FACTS, [])
    for expected in ("closed today", "open now", "equity", "health", "sample"):
        assert expected in text


def test_a_quiet_day_shows_no_empty_rows(dr):
    """A row with nothing in it is noise, and most nights are quiet."""
    text = _head(dr, {"health_line": "readyz healthy · healthz ok"}, [])
    assert "closed today" not in text
    assert "open now" not in text
    assert "health" in text


def test_the_body_is_captured_so_the_verdict_can_precede_it(source):
    assert "contextlib.redirect_stdout" in source, (
        "the body prints directly again, so the verdict can only be computed "
        "after everything has already been printed")
    assert source.index("def report_body") < source.index("def headline")


def test_the_body_does_not_decide_the_exit_code(source):
    """It appends to `problems`; `main` turns that into the code."""
    body = source[source.index("def report_body"):source.index("def headline")]
    assert "return 1" not in body and "return 0" not in body


# --- nothing that has nothing to say -------------------------------------

def test_the_app_report_block_needs_more_than_its_own_title(source):
    assert "len(report.splitlines()) > 1" in source, (
        "`forward-test report` prints its title on a day with no trades; the "
        "section printed a fenced block holding one line, every night")


def test_the_breaker_table_is_conditional_on_a_breaker_biting(source):
    assert 'if not bit:' in source and "no profile would have refused" in source, (
        "the breaker replay prints three rows of identical numbers and two "
        "paragraphs on every quiet day")


def test_the_gap_explanation_only_prints_when_something_is_unrepaired(source):
    i = source.index("Candle gaps in 24h by symbol")
    block = source[i:i + 900]
    assert "if unrepaired:" in block


# --- and no sentence asserts what it has not read --------------------------

@pytest.mark.parametrize("symbol", ["XRPUSD", "SOLUSD", "BTCUSD", "ETHUSD"])
def test_no_departed_symbol_is_hard_coded_in_a_nightly_sentence(source, symbol):
    """The healthz note named XRPUSD and SOLUSD long after both left the
    universe -- the same fault the per-symbol preamble was fixed for."""
    body = "\n".join(ln for ln in source.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert symbol not in body, (
        f"{symbol} is hard-coded into a line the report prints every run")


# --- the facts file, which is what makes a cross-arm digest possible -------

def test_the_report_can_write_its_facts(source):
    assert "--facts-json" in source and "json.dump(facts" in source


def test_the_facts_file_carries_the_verdict_and_the_stack(source):
    tail = source[source.index("if args.facts_json:"):]
    for key in ("stack=args.stack", "problems=problems", "verdict="):
        assert key in tail


# --- the digest -------------------------------------------------------------

def _digest(tmp_path, arms) -> subprocess.CompletedProcess:
    paths = []
    for facts in arms:
        d = tmp_path / f"daily-report-{facts['stack']}"
        d.mkdir(exist_ok=True)
        (d / "facts.json").write_text(json.dumps(facts))
        paths.append(str(d / "facts.json"))
    return subprocess.run([sys.executable, str(_DIGEST), *paths],
                          capture_output=True, text=True)


CLEAR = {"stack": "atr", "day": "2026-09-04", "verdict": "clear",
         "strategy": "manual_scalp_t4@5m", "day_of": "day 1/30",
         "closed_today": 2, "r_today": -1.4, "closed_line": "2 · 0 won · -1.40R"}
BROKEN = {"stack": "hours", "day": "2026-09-04", "verdict": "attention",
          "problems": ["readyz not ready"], "strategy": "…h18_24@5m",
          "closed_today": 1, "r_today": 0.98, "closed_line": "1 · 1 won · +0.98R"}


def test_the_digest_lists_every_arm(tmp_path):
    r = _digest(tmp_path, [CLEAR, BROKEN])
    assert "`atr`" in r.stdout and "`hours`" in r.stdout


def test_the_digest_fails_when_any_arm_needs_attention(tmp_path):
    assert _digest(tmp_path, [CLEAR, BROKEN]).returncode == 1
    assert _digest(tmp_path, [CLEAR]).returncode == 0


def test_the_digest_names_the_problem_not_just_the_arm(tmp_path):
    assert "readyz not ready" in _digest(tmp_path, [CLEAR, BROKEN]).stdout


def test_the_digest_refuses_to_call_a_winner(tmp_path):
    """One night separates nothing, and a digest that ranks invites acting."""
    out = _digest(tmp_path, [CLEAR, BROKEN]).stdout
    assert "One day separates nothing" in out
    for word in ("better", "winner", "outperform", "beats"):
        assert word not in out.lower()


def test_an_unreadable_facts_file_is_reported_not_swallowed(tmp_path):
    d = tmp_path / "daily-report-broken"
    d.mkdir()
    (d / "facts.json").write_text("{not json")
    r = subprocess.run([sys.executable, str(_DIGEST), str(d / "facts.json")],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "unreadable" in r.stdout


def test_the_digest_says_so_when_nothing_traded(tmp_path):
    quiet = dict(CLEAR, closed_today=0, r_today=0.0, closed_line=None)
    assert "Neither arm closed a trade" in _digest(tmp_path, [quiet]).stdout


def test_the_workflow_feeds_the_digest_from_the_reports():
    wf = (ROOT / ".github/workflows/monitor.yml").read_text()
    assert "--facts-json facts.json" in wf
    assert "facts.json" in wf.split("keep the report")[1][:400], (
        "the facts file is written but never uploaded, so the digest job "
        "downloads nothing")
    digest = wf[wf.index("\n  digest:"):]
    assert "if: always()" in digest, (
        "the digest skips itself when an arm's report failed -- which is "
        "exactly the night it matters")
    assert "needs: report" in digest
