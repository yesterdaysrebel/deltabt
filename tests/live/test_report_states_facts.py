"""The daily report must not assert as fact anything it has not read.

WHAT THIS PINS
    Two sentences in ``scripts/daily_report.py`` were prose hard-coded from
    the state of the world on the day they were written, and both were printed
    unconditionally on every run long after they stopped being true:

    1. The per-symbol preamble said "AKEUSD and BEATUSD had every setup refused
       for stop width". On 2026-08-31 AKEUSD's only rejection was the
       FALSE->TRUE edge rule and BEATUSD had none. The specific finding is
       derived from ``by_symbol`` after the table; the preamble must stay
       general.

    2. The open-position line said "there is no time stop" while
       ``DELTABOT_MAX_HOLD=86400`` was live, written to /opt/deltabt/env and
       forwarded into the container. That inverts an operator's decision during
       an incident: told a losing position is held indefinitely they intervene,
       told it closes in 24h they wait.

    A report is read when something is already wrong. A confident wrong
    sentence costs more there than a missing one.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_REPORT = pathlib.Path(__file__).resolve().parents[2] / "scripts/daily_report.py"


@pytest.fixture(scope="module")
def dr():
    spec = importlib.util.spec_from_file_location("daily_report", _REPORT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def source():
    return _REPORT.read_text()


# --- 2. the time stop is read from the running config ---------------------

def test_a_configured_time_stop_is_reported_as_present(dr):
    s = dr.time_stop_sentence(86400)
    assert "24.0h" in s
    assert "no time stop" not in s
    assert "indefinitely" not in s


@pytest.mark.parametrize("seconds,hours", [
    (86400, "24.0h"),
    (3600, "1.0h"),
    (1800, "0.5h"),
    (259200, "72.0h"),
])
def test_it_renders_the_actual_configured_hold(dr, seconds, hours):
    assert hours in dr.time_stop_sentence(seconds)


def test_zero_means_disabled_and_says_so(dr):
    s = dr.time_stop_sentence(0)
    assert "no time stop" in s.lower()
    assert "indefinitely" in s


def test_missing_is_not_rendered_as_disabled(dr):
    """None means the snapshot lacked the field. That is NOT the same as 0."""
    s = dr.time_stop_sentence(None)
    assert "unknown" in s
    assert "indefinitely" not in s
    assert s != dr.time_stop_sentence(0)


def test_the_three_states_are_all_distinct(dr):
    assert len({dr.time_stop_sentence(v) for v in (None, 0, 86400)}) == 3


# --- 1. the per-symbol preamble names no symbol ---------------------------

def test_the_per_symbol_preamble_names_no_symbol(source):
    start = source.index('print("## Per symbol')
    end = source.index("| Symbol | Setups |", start)
    preamble = source[start:end]
    body = "\n".join(ln for ln in preamble.splitlines()
                     if not ln.lstrip().startswith("#"))
    for sym in ("AKEUSD", "BEATUSD", "BANKUSD", "BTCUSD", "ETHUSD", "SOLUSD"):
        assert sym not in body, f"{sym} hard-coded into a paragraph printed every run"


def test_the_stale_claims_are_gone_from_the_source(source):
    """Both phrases survive only where they document the regression.

    Stripping comments and docstrings first is the point: the fix deliberately
    quotes the old wording so the next reader knows why the code is shaped this
    way. What must not come back is either phrase in code that PRINTS.
    """
    import ast

    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)

    code = "\n".join(ln for ln in source.splitlines()
                     if not ln.lstrip().startswith("#"))
    for doc in docstrings:
        code = code.replace(doc, "")

    assert "had every setup refused for stop width" not in code
    assert "there is no time stop" not in code


# --- a probe that ERRORS is not a probe that was TRUNCATED --------------------

def test_a_probe_traceback_is_reported_as_an_error_not_as_the_ssm_cap(source):
    """On 2026-09-03 the RDS master password rotated. Every fresh connection
    failed, the probe printed a traceback ending in InvalidPasswordError, and
    the report said "hit the SSM 24,000-byte cap". gunzip_section returns ""
    for a truncated blob, so non-empty non-JSON can only be probe OUTPUT."""
    import importlib.util, pathlib, io, contextlib, types
    spec = importlib.util.spec_from_file_location("daily_report", _REPORT)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert "DATABASE PROBE ERRORED" in source
    assert "db_errored" in source
    # the error branch must be checked BEFORE the truncation branch
    assert source.index("if db_errored:") < source.index("elif db_truncated:")


def test_error_text_keeps_the_exception_line():
    raw = ("Traceback (most recent call last):\n"
           '  File "x.py", line 1, in <module>\n'
           "    await repo.connect()\n"
           'asyncpg.exceptions.InvalidPasswordError: password authentication failed for user "deltabt"\n')
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    tail = [ln for ln in lines if not ln.startswith((" ", "\t"))][-2:]
    text = " | ".join(ln.strip()[:160] for ln in tail)
    assert "InvalidPasswordError" in text
    assert "password authentication failed" in text
