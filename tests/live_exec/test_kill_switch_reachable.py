"""The bot must be able to SEE the kill switch, not just be stopped by it.

WHAT HAPPENED. On 2026-09-16 tnet refused every entry with

    could not read the kill switch at /run/deltabt/HALT: [Errno 13]
    Permission denied -- treating as ENGAGED

and there was no HALT file. The switch lived in /run/deltabt, the directory
that holds the venue credentials and is 0700 root, while the bot runs as uid
10001. kill_switch_engaged() did exactly the right thing -- an unreadable
switch is treated as engaged -- so the defect was not in the check, it was that
the check could never succeed. The bot was permanently halted by a directory
mode, reporting healthy.

Each piece was correct on its own: the credentials directory SHOULD be 0700,
the bot SHOULD NOT run as root, the guard SHOULD fail closed. Nothing asserted
that the three were compatible. These tests do.
"""
from __future__ import annotations

import os
import pathlib
import re
import stat

import pytest

from live import guards

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN_LIVE = (ROOT / "deploy/aws/run_live.sh").read_text()
DOCKERFILE = (ROOT / "deploy/docker/Dockerfile.live").read_text()


def _passed_switch_path() -> str:
    m = re.search(r'-e "DELTA_KILL_SWITCH=([^"]+)"', RUN_LIVE)
    assert m, "run_live.sh does not pass DELTA_KILL_SWITCH to the container"
    return m.group(1)


def _mounts() -> list[tuple[str, str, str]]:
    return re.findall(r"-v (\S+?):(\S+?):(\w+)", RUN_LIVE)


# --- the three facts, and that they agree ----------------------------------

def test_the_bot_does_not_run_as_root():
    """If this ever changes, the directory mode below stops mattering -- and so
    does a real layer of protection. Asserted so the test that depends on it
    cannot pass for the wrong reason."""
    users = re.findall(r"^USER\s+(\S+)", DOCKERFILE, re.M)
    assert users, "Dockerfile.live sets no USER, so the bot runs as root"
    assert users[-1] not in ("root", "0"), users[-1]


def test_the_switch_path_is_inside_a_mount_the_container_has():
    path = _passed_switch_path()
    parent = str(pathlib.PurePosixPath(path).parent)
    mounted = {dest for _, dest, _ in _mounts()}
    assert parent in mounted, (
        f"the bot checks {path}, but {parent} is not mounted into the "
        f"container, so the operator's HALT on the host is invisible to it")


def test_the_switch_mount_is_read_only():
    """The bot must be able to see HALT, never to create or remove it."""
    path = _passed_switch_path()
    parent = str(pathlib.PurePosixPath(path).parent)
    modes = {dest: mode for _, dest, mode in _mounts()}
    assert modes.get(parent) == "ro", modes


def test_the_code_default_matches_what_the_host_passes():
    """Two statements of one path. If only the env var carried the fix, any
    process that started without it would check the old, unreachable path."""
    # Read the DEFAULT from source, not guards.KILL_SWITCH_PATH: that one is
    # resolved from the environment at import, so a DELTA_KILL_SWITCH set in
    # the test environment would make this pass without checking the default.
    src = (ROOT / "live/guards.py").read_text()
    m = re.search(r'os\.environ\.get\("DELTA_KILL_SWITCH",\s*"([^"]+)"\)', src)
    assert m, "could not find the kill switch default in live/guards.py"
    assert m.group(1) == _passed_switch_path(), (
        f"live/guards.py defaults to {m.group(1)} but run_live.sh passes "
        f"{_passed_switch_path()}")


def test_the_switch_directory_can_be_traversed_by_the_bot():
    """THE defect. The bot is not root, so it needs the OTHER execute bit."""
    parent = str(pathlib.PurePosixPath(_passed_switch_path()).parent)
    m = re.search(rf"install -d -m (0?[0-7]{{3,4}}) {re.escape(parent)}\b",
                  RUN_LIVE)
    assert m, f"run_live.sh does not create {parent} with an explicit mode"
    mode = int(m.group(1), 8)
    assert mode & stat.S_IXOTH, (
        f"{parent} is created {m.group(1)}; the bot (uid 10001, not the owner) "
        f"cannot traverse it, so every kill-switch check raises PermissionError "
        f"and is treated as ENGAGED -- the 2026-09-16 tnet halt")
    assert not mode & stat.S_IWOTH, (
        f"{parent} is world-writable at {m.group(1)}: anyone could create or "
        f"delete HALT")


def test_the_credentials_directory_is_not_mounted_into_the_container():
    """It was mounted only so the switch could be reached. The credentials are
    read by the docker daemon via --env-file, on the host; the container never
    needed the directory, and now does not get it."""
    for src, _dest, _mode in _mounts():
        assert src != "/run/deltabt", (
            "the credentials directory is bind-mounted into the container")


def test_the_credentials_directory_stays_root_only():
    """The fix must not be made by loosening this instead."""
    assert "install -d -m 0700 /run/deltabt\n" in RUN_LIVE


# --- the behaviour, on a real filesystem -----------------------------------

@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root bypasses directory permissions, so the "
                           "unreadable case cannot be reproduced")
def test_a_directory_the_bot_cannot_traverse_reads_as_engaged(tmp_path):
    """The 2026-09-16 failure, reproduced: no HALT, yet ENGAGED."""
    d = tmp_path / "control"
    d.mkdir()
    d.chmod(0o600)                  # no execute: cannot stat anything inside
    try:
        assert guards.kill_switch_engaged(str(d / "HALT")) is True
    finally:
        d.chmod(0o755)


def test_a_traversable_directory_reports_the_switch_truthfully(tmp_path):
    d = tmp_path / "control"
    d.mkdir()
    d.chmod(0o755)
    halt = d / "HALT"
    assert guards.kill_switch_engaged(str(halt)) is False, (
        "no HALT file, readable directory, and the switch still reads engaged")
    halt.write_text("stop")
    assert guards.kill_switch_engaged(str(halt)) is True
