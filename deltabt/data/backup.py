"""Verification and a backup interface for the forward-recorded partitions.

WHAT THIS DOES AND DELIBERATELY DOES NOT DO
    It VERIFIES: every partition is re-hashed and compared against the SHA-256
    its manifest recorded, so silent corruption or an out-of-band edit is
    visible rather than assumed away.

    It does NOT upload anything. `scripts/backup.sh` is this repository's only
    existing backup mechanism and it dumps the paper-trading Postgres
    database; it has no bearing on Parquet partitions. The only S3 bucket in
    `infra/terraform` is the OpenTofu state bucket, which is not a market-data
    target -- writing gigabytes of history into a state bucket would be an
    abuse of it. So no durable target exists yet, and inventing one here would
    mean inventing credentials and a bucket policy nobody reviewed.

    What exists instead is the interface a target plugs into: a copy that is
    verified after writing, a dry run that reports exactly what would move,
    and a status call the daily durability check reads.

THE PATTERN IS BORROWED FROM scripts/backup.sh, WHICH GETS IT RIGHT
    Write, then read back and confirm, then report. Its comment is the rule:
    "An unverified backup is a guess."

WHAT MUST BE COPIED, AND IN WHAT ORDER
    Raw partitions first, then their manifests, then checkpoints. Manifests
    after partitions so a torn backup never claims to describe data it does
    not contain. Derived research output is NOT a substitute for any of it and
    is never included.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from deltabt.config import DATA_DIR
from deltabt.data import archive

#: Datasets that carry irreplaceable forward-only observations.
DATASETS = ("options", "perp_quotes", "perp_candles")

_PREFIX = {"options": ("quotes", "quotes_"),
           "perp_quotes": ("perp", "perp_quotes_"),
           "perp_candles": ("perp", "perp_candles_1m_")}


def partitions(dataset: str, root: Path | None = None) -> list[Path]:
    sub, prefix = _PREFIX[dataset]
    d = Path(root or DATA_DIR) / sub
    return sorted(d.glob(f"{prefix}*.parquet")) if d.exists() else []


@dataclass
class VerifyResult:
    checked: int = 0
    ok: int = 0
    open_partitions: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)
    unmanifested: list[str] = field(default_factory=list)
    orphan_manifests: list[str] = field(default_factory=list)
    #: Partitions whose manifest MATCHES but was RECONSTRUCTED after a
    #: mismatch was found, not written during collection. They are healthy
    #: from now on and must never be reported as originally verified: the
    #: checksum proves nothing about the state the partition sealed in.
    rebuilt: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not (self.mismatched or self.unmanifested or self.orphan_manifests)

    @property
    def originally_verified(self) -> int:
        """`ok` minus the days whose agreement was established, not observed."""
        return self.ok - len(self.rebuilt)


def verify(root: Path | None = None,
           manifest_root: Path | None = None,
           *, today: str | None = None) -> VerifyResult:
    """Re-hash every SEALED partition and compare against its manifest.

    SEALED VERSUS OPEN, WHICH IS THE WHOLE SUBTLETY
        Today's partition is being appended to every 60 seconds. Its checksum
        is stale the instant after it is written, so verifying it strictly
        would report corruption every single day and train whoever reads this
        to ignore it. Only past-day partitions are sealed, and only those are
        held to their manifest. Today's are listed as `open` and counted, so
        they are visible without being alarming.

    A mismatch on a sealed partition is not repaired and not re-manifested
    HERE. Regenerating the manifest would make the discrepancy disappear,
    which is the opposite of what a checksum is for. Reconstruction is a
    separate, deliberate act -- `archive.rebuild_manifest` -- and what it
    produces is counted in `rebuilt`, never in `originally_verified`.
    """
    res = VerifyResult()
    man_root = Path(manifest_root or archive.MANIFEST_ROOT)
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for ds in DATASETS:
        seen_days = set()
        for p in partitions(ds, root):
            day = archive.utc_day(_first_ts(ds, p))
            seen_days.add(day)
            if day >= today:
                res.open_partitions.append(str(p))
                continue
            res.checked += 1
            mpath = man_root / ds / f"{day}.json"
            if not mpath.exists():
                res.unmanifested.append(str(p))
                continue
            m = json.loads(mpath.read_text())
            recorded = (m.get("checksums") or {}).get(p.name)
            if recorded is None:
                res.unmanifested.append(str(p))
            elif recorded != archive.sha256_file(p):
                res.mismatched.append(str(p))
            else:
                res.ok += 1
                if m.get("provenance") == archive.PROVENANCE_REBUILT:
                    res.rebuilt.append(str(p))
        d = man_root / ds
        if d.exists():
            for m in d.glob("*.json"):
                if m.stem not in seen_days:
                    res.orphan_manifests.append(str(m))
    return res


def _first_ts(dataset: str, path: Path) -> int:
    import pandas as pd
    col = archive.TIME_COLUMN[dataset]
    return int(pd.read_parquet(path, columns=[col])[col].min())


def backup(destination: Path | None, *, dry_run: bool = True,
           root: Path | None = None,
           manifest_root: Path | None = None) -> dict:
    """Copy partitions, manifests and checkpoints to `destination`, verified.

    `dry_run` defaults to True. A backup command whose default is to act is a
    backup command that will one day act somewhere unintended.

    `destination` is a filesystem path -- a mounted volume, an external disk, a
    synced directory. There is deliberately no network client here: adding one
    would mean choosing a provider and credentials that no one has reviewed.
    """
    started = datetime.now(timezone.utc)
    plan = {"dry_run": dry_run, "destination": str(destination) if destination else None,
            "started_at": started.isoformat(), "files": [], "bytes": 0,
            "copied": 0, "verified": 0, "failed": []}

    pre = verify(root, manifest_root)
    plan["source_verification"] = {
        "sealed_checked": pre.checked, "ok": pre.ok,
        "originally_verified": pre.originally_verified,
        "rebuilt": pre.rebuilt,
        "open_partitions": pre.open_partitions,
        "mismatched": pre.mismatched, "unmanifested": pre.unmanifested,
        "healthy": pre.healthy}
    if not pre.healthy:
        plan["aborted"] = ("source failed verification; refusing to propagate "
                           "a partition that does not match its manifest")
        return plan

    man_root = Path(manifest_root or archive.MANIFEST_ROOT)
    items: list[tuple[Path, str]] = []
    for ds in DATASETS:                       # raw observations first
        for p in partitions(ds, root):
            items.append((p, f"data/{p.parent.name}/{p.name}"))
    for ds in DATASETS:                       # then what describes them
        d = man_root / ds
        if d.exists():
            items += [(m, f"manifests/{ds}/{m.name}") for m in sorted(d.glob("*.json"))]
    cp_dir = Path(root or DATA_DIR) / "checkpoints"
    if cp_dir.exists():
        items += [(c, f"checkpoints/{c.name}") for c in sorted(cp_dir.glob("*.json"))]

    for src, rel in items:
        size = src.stat().st_size
        plan["files"].append({"source": str(src), "relative": rel, "bytes": size})
        plan["bytes"] += size
        if dry_run or destination is None:
            continue
        dst = Path(destination) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
            plan["copied"] += 1
            # Verify the COPY, not the source. Confirming the source again
            # would prove nothing about what actually landed.
            if src.suffix == ".parquet":
                if archive.sha256_file(dst) != archive.sha256_file(src):
                    plan["failed"].append(rel)
                else:
                    plan["verified"] += 1
        except OSError as exc:
            plan["failed"].append(f"{rel}: {exc}")

    plan["finished_at"] = datetime.now(timezone.utc).isoformat()
    plan["status"] = ("DRY RUN" if dry_run or destination is None
                      else ("OK" if not plan["failed"] else "FAILED"))
    return plan


def status(root: Path | None = None,
           manifest_root: Path | None = None) -> dict:
    """What the daily durability check reads. No target configured is not an
    error here -- it is the honest current state, reported as such."""
    v = verify(root, manifest_root)
    return {
        "sealed_partitions_checked": v.checked,
        "open_partitions": v.open_partitions,
        "checksum_ok": v.ok,
        # `ok` counts agreement; it does NOT mean the day was verified as
        # recorded. A rebuilt manifest agrees with its partition because the
        # agreement was ESTABLISHED after a mismatch was found. Reporting the
        # two as one number would relabel a reconciled day as an observed one.
        "originally_verified": v.originally_verified,
        "reconstructed_manifests": v.rebuilt,
        "checksum_mismatched": v.mismatched,
        "unmanifested_partitions": v.unmanifested,
        "orphan_manifests": v.orphan_manifests,
        "integrity": "OK" if v.healthy else "FAILED",
        "backup_target_configured": False,
        "backup_target_note": (
            "No durable target exists. scripts/backup.sh covers the paper "
            "Postgres database only, and the sole S3 bucket in infra/terraform "
            "is the OpenTofu state bucket, which is not a market-data target. "
            "Run `python -m deltabt.data.backup --destination <path>` against a "
            "mounted volume to create one."),
        "last_backup": None,
    }


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Verify and back up recorded partitions.")
    ap.add_argument("--destination", default=None,
                    help="filesystem path to copy into; omit for a dry run")
    ap.add_argument("--execute", action="store_true",
                    help="actually copy (default is a dry run)")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()
    if args.verify_only:
        print(json.dumps(status(), indent=2))
        return
    plan = backup(Path(args.destination) if args.destination else None,
                  dry_run=not args.execute)
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
