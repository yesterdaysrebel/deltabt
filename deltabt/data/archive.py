"""Durable append-only persistence for forward-recorded market data.

WHY THIS IS SEPARATE FROM THE RECORDERS
    `quote_recorder.py` has been collecting continuously since 2026-08-24 and
    its collection semantics are proven. This module adds the durability the
    options gate found missing -- checkpoints, manifests, checksums, schema
    versions -- WITHOUT touching how a snapshot is taken. A recorder calls
    `append_partition` where it used to call `to_parquet`, and nothing about
    what it collects changes.

THE PROPERTY EVERYTHING RESTS ON
    A partially written snapshot must never be mistaken for a complete one.
    Every write goes to a temp file in the same directory and is moved into
    place with `os.replace`, which is atomic on POSIX within a filesystem. A
    crash mid-write leaves the previous good partition untouched.

WHY READ-MODIFY-WRITE RATHER THAN A ROW-GROUP APPEND
    Parquet cannot be appended to in place. The alternative -- one file per
    snapshot -- produces ~100k files a year and makes every later read a
    directory walk. A day is the unit: at ~10 MB/day for options the rewrite
    is cheap, and a corrupt write can cost at most one day, never the history.

WHAT A MANIFEST IS FOR
    Exactly one question: which observations existed before a future
    experiment was frozen. A manifest records the row count, the timestamp
    bounds, the contract set size and the SHA-256 of the partition as it stood.
    If a file is later altered, the checksum no longer matches and the
    alteration is visible rather than silent.

MISSING MEANS MISSING
    Nothing here forward-fills, interpolates, or substitutes a mark for a
    quote. A gap in the record is a gap in the record, and `health.py` reports
    it rather than repairing it.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from deltabt.config import ROOT as _REPO_ROOT
from deltabt.config import DATA_DIR, OUT_DIR

#: Bump when the MEANING of a column changes or a column is added/removed.
#: Never reinterpret an existing version -- historical rows keep their schema.
SCHEMA_VERSIONS: dict[str, int] = {
    "options": 1,
    "perp_quotes": 1,
    "perp_candles": 1,
}

#: Deterministic uniqueness key per dataset. Chosen after inspecting the data:
#:
#: options / perp_quotes -- `snapshot_ts` is the LOCAL poll instant and is
#:   constant across every contract in one snapshot, so (snapshot_ts, symbol)
#:   identifies one observation exactly. `exchange_ts` cannot be used: it
#:   varies per contract within a single snapshot and repeats across snapshots
#:   when the exchange has not refreshed a quiet contract.
#:
#: perp_candles -- (time, symbol) where `time` is the bar OPEN in unix
#:   seconds. Re-fetching a bar must overwrite, never duplicate.
DEDUP_KEYS: dict[str, tuple[str, ...]] = {
    "options": ("snapshot_ts", "symbol"),
    "perp_quotes": ("snapshot_ts", "symbol"),
    "perp_candles": ("time", "symbol"),
}

#: The column carrying the observation's own time, used for manifest bounds.
TIME_COLUMN: dict[str, str] = {
    "options": "snapshot_ts",
    "perp_quotes": "snapshot_ts",
    "perp_candles": "time",
}

ARCHIVE_ROOT = DATA_DIR

def _default_manifest_root() -> Path:
    """Manifests live under out/, which git TRACKS, while the partitions they
    describe live under data/, which .gitignore excludes wholesale.

    That asymmetry is the point. A manifest exists to answer one question --
    which observations existed before an experiment was frozen -- and a record
    that lives only beside the data it describes dies with it. Manifests are a
    few KB a day, so pushing them to the git remote gives the audit trail an
    independent durable copy for free, even while the bulk data has none.

    BUT ONLY FOR THE PRODUCTION DATASET. If DELTABT_DATA has been pointed
    somewhere else -- a test, a scratch run, a restore rehearsal -- manifests
    follow the data instead. Otherwise an isolated run writes into the tracked
    tree and can overwrite the manifest describing the real partition for the
    same day, which is exactly the silent production mutation the isolation
    rule exists to prevent. Found by the reboot test, which did it.
    """
    if DATA_DIR == _REPO_ROOT / "data":
        return OUT_DIR / "manifests"
    return DATA_DIR / "manifests"


MANIFEST_ROOT = _default_manifest_root()

#: Checkpoints stay under data/: they are mutable process state, not an audit
#: trail, and committing a file that changes every 60 seconds would be noise.
CHECKPOINT_ROOT = DATA_DIR / "checkpoints"

#: Bumped when a recorder's collection behaviour changes, so a manifest says
#: which code produced its rows.
COLLECTOR_VERSION = "2026-08-28.1"


def utc_day(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")


def partition_path(dataset: str, ts: int, root: Path | None = None) -> Path:
    """Deterministic daily partition. One file per dataset per UTC day.

    Daily rather than hourly: at 96 option snapshots a day a partition is
    ~10 MB, while hourly would multiply the file count 24x for no read benefit
    and no extra crash protection.
    """
    base = Path(root or ARCHIVE_ROOT)
    layout = {
        "options": base / "quotes" / f"quotes_{utc_day(ts)}.parquet",
        "perp_quotes": base / "perp" / f"perp_quotes_{utc_day(ts)}.parquet",
        "perp_candles": base / "perp" / f"perp_candles_1m_{utc_day(ts)}.parquet",
    }
    if dataset not in layout:
        raise ValueError(f"unknown dataset {dataset!r}; known: {sorted(layout)}")
    return layout[dataset]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write via a temp file in the SAME directory, then `os.replace`.

    Same directory because `os.replace` is only atomic within a filesystem;
    a temp file in /tmp could land on a different mount and degrade to a
    copy, which is exactly the partial write this prevents.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".parquet.tmp")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        df.to_parquet(tmp_path, index=False, compression="snappy")
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def append_partition(df: pd.DataFrame, dataset: str, *,
                     root: Path | None = None) -> Path:
    """Merge one batch into its daily partition, deduplicated and atomic.

    `keep="last"` so a re-fetch of the same bar replaces the earlier copy
    rather than sitting beside it.
    """
    if df.empty:
        raise ValueError(f"refusing to append an empty {dataset} batch")
    tcol = TIME_COLUMN[dataset]
    if df[tcol].nunique() and utc_day(df[tcol].min()) != utc_day(df[tcol].max()):
        # A batch straddling midnight is split so every row lands in its own day.
        out = None
        for day, part in df.groupby(df[tcol].map(utc_day)):
            out = append_partition(part, dataset, root=root)
        return out
    path = partition_path(dataset, int(df[tcol].iloc[0]), root)
    key = list(DEDUP_KEYS[dataset])
    if path.exists():
        merged = pd.concat([pd.read_parquet(path), df], ignore_index=True)
        merged = merged.drop_duplicates(subset=key, keep="last")
    else:
        merged = df
    merged = merged.sort_values(key).reset_index(drop=True)
    atomic_write_parquet(merged, path)
    return path


# --------------------------------------------------------------- checkpoints

@dataclass
class Checkpoint:
    dataset: str
    last_timestamp: int = 0
    last_partition: str = ""
    rows_written: int = 0
    batches_written: int = 0
    schema_version: int = 0
    collector_version: str = COLLECTOR_VERSION
    updated_at: str = ""
    last_error: str = ""
    checksum: str = ""

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def checkpoint_path(dataset: str, root: Path | None = None) -> Path:
    return Path(root or CHECKPOINT_ROOT) / f"{dataset}.json"


def read_checkpoint(dataset: str, root: Path | None = None) -> Checkpoint:
    p = checkpoint_path(dataset, root)
    if not p.exists():
        return Checkpoint(dataset=dataset,
                          schema_version=SCHEMA_VERSIONS.get(dataset, 0))
    d = json.loads(p.read_text())
    known = {k: v for k, v in d.items() if k in Checkpoint.__dataclass_fields__}
    known["dataset"] = dataset
    return Checkpoint(**known)


def write_checkpoint(cp: Checkpoint, root: Path | None = None) -> Path:
    """Checkpoints are written atomically too. A truncated checkpoint would
    make a clean restart look like a corrupt one."""
    p = checkpoint_path(cp.dataset, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    cp.updated_at = datetime.now(timezone.utc).isoformat()
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".json.tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(cp.to_dict(), fh, indent=2)
    os.replace(tmp, p)
    return p


# ----------------------------------------------------------------- manifests

def manifest_path(dataset: str, day: str, root: Path | None = None) -> Path:
    return Path(root or MANIFEST_ROOT) / dataset / f"{day}.json"


def build_manifest(dataset: str, path: Path, *, venue: str = "delta_india") -> dict:
    """Describe one partition exactly as it stands on disk."""
    df = pd.read_parquet(path)
    tcol = TIME_COLUMN[dataset]
    sym = "symbol" if "symbol" in df.columns else None
    return {
        "dataset": dataset,
        "venue": venue,
        "date": utc_day(int(df[tcol].min())) if len(df) else "",
        "first_timestamp": int(df[tcol].min()) if len(df) else None,
        "last_timestamp": int(df[tcol].max()) if len(df) else None,
        "rows": int(len(df)),
        "unique_contracts": int(df[sym].nunique()) if sym else None,
        "unique_batches": int(df[tcol].nunique()),
        "schema_version": SCHEMA_VERSIONS[dataset],
        "columns": list(df.columns),
        "dedup_key": list(DEDUP_KEYS[dataset]),
        "file_paths": [str(path)],
        "checksums": {path.name: sha256_file(path)},
        "bytes": int(path.stat().st_size),
        "collection_process_version": COLLECTOR_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_manifest(dataset: str, path: Path, *, root: Path | None = None,
                   venue: str = "delta_india") -> Path:
    m = build_manifest(dataset, path, venue=venue)
    out = manifest_path(dataset, m["date"], root)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out.parent), suffix=".json.tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(m, fh, indent=2)
    os.replace(tmp, out)
    return out


def refresh_manifests(dataset: str, *, root: Path | None = None,
                      manifest_root: Path | None = None) -> list[Path]:
    """Regenerate manifests for every partition of a dataset that exists.

    Run over the already-collected partitions so the five days recorded before
    this module existed are described too.
    """
    base = Path(root or ARCHIVE_ROOT)
    globs = {
        "options": base / "quotes",
        "perp_quotes": base / "perp",
        "perp_candles": base / "perp",
    }[dataset]
    prefix = {"options": "quotes_", "perp_quotes": "perp_quotes_",
              "perp_candles": "perp_candles_1m_"}[dataset]
    out = []
    if not globs.exists():
        return out
    for p in sorted(globs.glob(f"{prefix}*.parquet")):
        out.append(write_manifest(dataset, p, root=manifest_root))
    return out


# ------------------------------------------------------- single instance lock

LOCK_ROOT = DATA_DIR / "locks"


class AlreadyRunning(RuntimeError):
    """Another instance of this recorder holds the lock."""


@contextmanager
def single_instance(name: str, root: Path | None = None):
    """Refuse to start a second writer for the same dataset.

    WHY THIS IS A CORRECTNESS FIX AND NOT A CONVENIENCE
        `append_partition` is read-modify-write: it reads the whole daily
        partition, merges the new batch and replaces the file. Two concurrent
        writers therefore race on the WHOLE DAY, not on a row -- A reads, B
        reads, A writes, B writes, and A's snapshot is gone. The loss is
        silent, and for forward-only data it is permanent.

        A systemd unit cannot race itself, but a manual `python -m
        deltabt.data.quote_recorder` alongside a running service can, and that
        is exactly how this repository has been operated.

    `flock` is released automatically when the process dies for any reason,
    including SIGKILL, so a crash never leaves a stale lock behind.
    """
    d = Path(root or LOCK_ROOT)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.lock"
    fh = open(path, "w")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise AlreadyRunning(
                    f"another {name} recorder already holds {path}; refusing to "
                    f"start a second writer on the same partitions") from None
            raise
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        yield path
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()
