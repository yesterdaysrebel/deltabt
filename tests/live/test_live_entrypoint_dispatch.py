"""The live entry point must hand subcommands to the CLI, and must trade the
venue's universe rather than whatever DELTABOT_SYMBOLS happens to say.

Both failures were invisible to every existing check. `python -m live
forward-test stop` did not error -- it started a full trading bot and ran
forever -- and the tnet host reported healthy for six hours while warming the
paper universe against testnet product ids it had no entry for.

These tests RUN the entry point. Asserting that the dispatch block appears in
the file would have passed against the broken version too, because the bug was
never a missing string; it was a missing call.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def _run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "live", *args],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout,
        # No DATABASE_URL and no venue credentials: a dispatched CLI parses
        # argv and exits long before it needs either. A bot does not.
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT),
             "HOME": "/tmp", "TZ": "UTC"},
    )


def test_bare_subcommand_reaches_argparse_not_the_bot():
    """`forward-test` with no action is an argparse error, exit 2.

    The broken entry point ignored argv and booted a bot, which never exits.
    A timeout here IS the regression.
    """
    try:
        proc = _run(["forward-test"])
    except subprocess.TimeoutExpired:
        pytest.fail("`python -m live forward-test` did not exit: argv was "
                    "discarded and the bot started instead of the CLI")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    combined = proc.stdout + proc.stderr
    assert "forward-test" in combined


def test_help_lists_the_forward_test_command():
    try:
        proc = _run(["--help"])
    except subprocess.TimeoutExpired:
        pytest.fail("`python -m live --help` did not exit")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "forward-test" in proc.stdout


@pytest.mark.asyncio
async def test_bot_is_built_with_the_venue_universe_not_the_env(monkeypatch):
    """Drive main() far enough to see what universe the bot is handed.

    DELTABOT_SYMBOLS is set to the paper universe, exactly as the tnet host
    has it. The bot must still be constructed with the venue's symbols.
    """
    import live.__main__ as entry
    from live.config import VENUE_SYMBOLS

    monkeypatch.setenv("DELTABOT_SYMBOLS", "BEATUSD,AKEUSD,BANKUSD,WIFUSD")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/x")

    venue = "testnet"
    expected = tuple(VENUE_SYMBOLS[venue])

    class _Client:
        is_mainnet = False

    captured = {}

    class _Stop(Exception):
        pass

    def _fake_bot(settings, *a, **kw):
        captured["symbols"] = settings.symbols
        raise _Stop

    monkeypatch.setattr(entry, "venue_name", lambda *a, **k: venue)
    monkeypatch.setattr(entry, "client_from_env", lambda *a, **k: _Client())
    monkeypatch.setattr(entry, "resolve_products", lambda c, s: list(s))
    monkeypatch.setattr(entry, "product_ids",
                        lambda p: {s: i for i, s in enumerate(p, 1)})
    monkeypatch.setattr(entry, "tick_sizes", lambda p: {s: 0.1 for s in p})
    monkeypatch.setattr(entry, "load_costs", lambda s, b: {})
    monkeypatch.setattr(entry, "PostgresRepository", lambda *a, **k: object())
    monkeypatch.setattr(entry, "SingleInstanceLock", lambda *a, **k: object())
    monkeypatch.setattr(entry, "Backfiller", lambda *a, **k: object())
    monkeypatch.setattr(entry, "LiveTradingBot", _fake_bot)

    with pytest.raises(_Stop):
        await entry.main()

    assert captured["symbols"] == expected, (
        f"bot was built with {captured['symbols']}, but venue {venue} trades "
        f"{expected}: DELTABOT_SYMBOLS won over the venue universe")


def test_source_applies_the_override_before_constructing_the_bot():
    """Order matters: replace() must precede LiveTradingBot(settings, ...)."""
    src = (REPO_ROOT / "live" / "__main__.py").read_text()
    override = src.index("settings = replace(settings")
    construct = src.index("bot = LiveTradingBot(")
    assert override < construct, (
        "settings.symbols is overridden after the bot is built, so the bot "
        "still holds the un-overridden universe")
