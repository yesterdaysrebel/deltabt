"""A sealed partition with no manifest must be noticed, not merely not-fail.

WHY THIS EXISTS. On 2026-08-31 six sealed partitions -- options, perp_candles
and perp_quotes for the 29th and 30th, 19 MB of options quotes among them --
had no manifest at all. The recorders ran the whole time and logged 72 and 96
successful polls across those days. Not one manifest-write failure was logged,
because none failed.

The gap was never an error path. The manifest write is wrapped in try/except so
it can never kill the poll, which also means it can never complain; and nothing
else ever asked whether the days on disk were described. A missing description
raises nothing, fails no health check, and appears in no log.

So the fix under test is not better error handling. It is asking the question.
"""
from __future__ import annotations

import pandas as pd
import pytest

from deltabt.data import archive


def _partition(root, dataset, day):
    """Write a real partition so the manifest machinery has something to read.

    The time column differs per dataset (archive.TIME_COLUMN), and a manifest
    cannot be built without it -- so the fixture asks rather than assuming.
    """
    sub, prefix = archive._PARTITION_LAYOUT[dataset]
    d = root / sub
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{prefix}{day}.parquet"
    tcol = archive.TIME_COLUMN[dataset]
    # The timestamps must fall on `day`. write_manifest names the manifest from
    # the DATA's date while this check looks it up by the FILENAME's date, so a
    # fixture with epoch-1 timestamps writes 1970-01-01.json and the day looks
    # undescribed. Verified against production: all 16 live partitions agree,
    # so the coupling holds today -- but it is a coupling, and
    # test_the_manifest_is_named_from_the_data_not_the_filename pins it.
    base = int(pd.Timestamp(day, tz="UTC").timestamp())
    pd.DataFrame({tcol: [base, base + 60], "symbol": ["A", "B"]}).to_parquet(p)
    return p


def test_a_sealed_partition_without_a_manifest_is_reported(tmp_path):
    _partition(tmp_path, "options", "2026-01-01")
    assert archive.unmanifested_days(
        "options", root=tmp_path, manifest_root=tmp_path / "m",
        today="2026-06-01") == ["2026-01-01"]


def test_a_described_day_is_not_reported(tmp_path):
    p = _partition(tmp_path, "options", "2026-01-01")
    archive.write_manifest("options", p, root=tmp_path / "m")
    assert archive.unmanifested_days(
        "options", root=tmp_path, manifest_root=tmp_path / "m",
        today="2026-06-01") == []


def test_todays_partition_is_excluded_because_it_is_still_open(tmp_path):
    """Its manifest is rewritten on every append; an open day is not a gap."""
    _partition(tmp_path, "options", "2026-01-01")
    assert archive.unmanifested_days(
        "options", root=tmp_path, manifest_root=tmp_path / "m",
        today="2026-01-01") == []


@pytest.mark.parametrize("dataset", ["options", "perp_quotes", "perp_candles"])
def test_every_recorded_dataset_can_be_checked(dataset, tmp_path):
    """All three were missing on 2026-08-29/30, not just the options one."""
    _partition(tmp_path, dataset, "2026-01-01")
    assert archive.unmanifested_days(
        dataset, root=tmp_path, manifest_root=tmp_path / "m",
        today="2026-06-01") == ["2026-01-01"]


def test_an_unknown_dataset_is_refused_rather_than_silently_empty(tmp_path):
    """Returning [] for a typo would report perfect coverage of nothing."""
    with pytest.raises(ValueError, match="unknown dataset"):
        archive.unmanifested_days("optoins", root=tmp_path)


def test_a_missing_partition_directory_is_not_a_gap(tmp_path):
    """Before the first poll there is nothing to describe, and that is fine."""
    assert archive.unmanifested_days(
        "options", root=tmp_path, manifest_root=tmp_path / "m") == []


def test_the_layout_is_shared_with_refresh_manifests():
    """Two copies of the partition layout would drift; this pins that they are
    one. refresh_manifests and unmanifested_days must agree on where a
    dataset's files live, or the check would look in the wrong directory and
    report perfect coverage."""
    assert set(archive._PARTITION_LAYOUT) == {"options", "perp_quotes",
                                              "perp_candles"}
    for ds, (sub, prefix) in archive._PARTITION_LAYOUT.items():
        got = archive.partition_path(ds, 0, root=archive.Path("/x"))
        assert got.parent.name == sub and got.name.startswith(prefix)


def test_the_manifest_is_named_from_the_data_not_the_filename(tmp_path):
    """A COUPLING WORTH KNOWING ABOUT, found while writing these tests.

    write_manifest derives the manifest's name from the partition's own
    timestamps (build_manifest: `utc_day(df[tcol].min())`), while
    unmanifested_days looks it up by the date in the FILENAME. They agree for
    every one of the 16 live partitions, and the recorders only ever append
    rows belonging to the current day, so this is not a live defect.

    But it is load-bearing: a partition whose earliest row fell on the previous
    UTC day would have its manifest written under that day's name -- silently
    overwriting a real manifest AND leaving its own day undescribed. Pinned so
    that if the naming ever moves, it moves deliberately.
    """
    sub, prefix = archive._PARTITION_LAYOUT["options"]
    d = tmp_path / sub
    d.mkdir(parents=True)
    p = d / f"{prefix}2026-03-02.parquet"          # filename says the 2nd
    stale = int(pd.Timestamp("2026-03-01T23:00:00", tz="UTC").timestamp())
    pd.DataFrame({archive.TIME_COLUMN["options"]: [stale],
                  "symbol": ["A"]}).to_parquet(p)   # data says the 1st
    written = archive.write_manifest("options", p, root=tmp_path / "m")
    assert written.name == "2026-03-01.json", (
        "the manifest follows the data's date, not the filename's")
    assert archive.unmanifested_days(
        "options", root=tmp_path, manifest_root=tmp_path / "m",
        today="2026-06-01") == ["2026-03-02"], (
        "and so the partition's own day is reported as undescribed")


# --- a batch that straddles midnight ----------------------------------------
#
# WHAT HAPPENED, TWICE. archive.append_partition splits a batch spanning
# midnight and returns only the LAST path -- groupby yields days ascending, so
# that is the NEW day. _commit manifested only that one, leaving YESTERDAY's
# partition rewritten with a stale manifest, and verification then refused the
# ENTIRE backup:
#
#     source failed verification; refusing to propagate a partition that does
#     not match its manifest
#
# 2026-09-01: perp_candles parquet rewritten 00:13:29, manifest written
# 00:00:29. 2026-09-02: the same, same dataset. The 00:0x poll carries the
# previous day's final minutes, so it recurs every night.
#
# unmanifested_days() does NOT catch it. It checks a manifest EXISTS, not that
# it AGREES -- it reported clean on both mornings.

import hashlib
import json

import pandas as pd

from deltabt.data import archive as _archive
from deltabt.data.perp_recorder import _commit as _perp_commit

_MIDNIGHT = 1788220800          # 2026-09-01 00:00:00 UTC


def _candles(ts: int, n: int) -> pd.DataFrame:
    return pd.DataFrame({
        "time": [ts + 60 * i for i in range(n)], "fetched_ts": [ts] * n,
        "symbol": ["BTCUSD"] * n, "open": [1.0] * n, "high": [1.0] * n,
        "low": [1.0] * n, "close": [1.0] * n, "volume": [1.0] * n})


def _manifest_matches(root, day: str) -> bool:
    mp = _archive.manifest_path("perp_candles", day, root / "manifests")
    if not mp.exists():
        return False
    m = json.loads(mp.read_text())
    part = root / "perp" / f"perp_candles_1m_{day}.parquet"
    want = list(m["checksums"].values())[0]
    return want == hashlib.sha256(part.read_bytes()).hexdigest()


def test_a_batch_spanning_midnight_manifests_BOTH_days(tmp_path):
    """The regression. Yesterday's partition is rewritten by the batch, so its
    manifest must be rewritten with it."""
    _perp_commit(_candles(_MIDNIGHT + 600, 5), "perp_candles", "time", tmp_path)
    nxt = _MIDNIGHT + 86400
    _perp_commit(_candles(nxt - 180, 6), "perp_candles", "time", tmp_path)

    assert _manifest_matches(tmp_path, "2026-09-01"), \
        "the OLD day was rewritten by the straddling batch and left stale"
    assert _manifest_matches(tmp_path, "2026-09-02")


def test_a_single_day_batch_still_manifests_its_one_day(tmp_path):
    _perp_commit(_candles(_MIDNIGHT + 600, 5), "perp_candles", "time", tmp_path)
    assert _manifest_matches(tmp_path, "2026-09-01")
    assert not _archive.manifest_path(
        "perp_candles", "2026-09-02", tmp_path / "manifests").exists()


def test_three_days_in_one_batch_are_all_manifested(tmp_path):
    """Not a real polling pattern, but the loop must not assume exactly two."""
    df = pd.concat([_candles(_MIDNIGHT + 600, 2),
                    _candles(_MIDNIGHT + 86400 + 600, 2),
                    _candles(_MIDNIGHT + 172800 + 600, 2)], ignore_index=True)
    _perp_commit(df, "perp_candles", "time", tmp_path)
    for day in ("2026-09-01", "2026-09-02", "2026-09-03"):
        assert _manifest_matches(tmp_path, day), day
