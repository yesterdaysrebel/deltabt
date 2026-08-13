"""Experiment identity: what, exactly, produced this dataset.

AUDIT FINDING F5. Nothing recorded a commit SHA and nothing prevented
configuration drift. Two specific holes:

* **Risk configuration was not part of any hash.** ``strategy_config_hash``
  covers strategy parameters only, so ``DELTABOT_RISK_PER_TRADE``,
  ``DELTABOT_MIN_RR``, ``DELTABOT_MAX_TRADES_PER_DAY`` and the rest could
  change between restarts with nothing in the data reflecting it. Halving the
  risk fraction mid-run would have been invisible.
* **Nothing refused to start on a change.** A 30-day experiment whose
  configuration can move underneath it is not one experiment.

So the experiment is identified by a COMPOSITE hash over strategy AND risk AND
execution parameters, plus the code that ran. ``strategy_config_hash`` is left
exactly as it was -- it still means "the strategy rules" and is still
5a5412369f3823f3 -- and the composite sits alongside it.

FAIL CLOSED. If the running configuration does not match the experiment that is
already in the database, the bot refuses to trade. It does not adopt the new
configuration, and it does not quietly continue on the old one.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field, is_dataclass

APP_VERSION = "1.0.0-paper"
UNKNOWN_SHA = "unknown"


def _canonical(obj) -> str:
    """Stable text for hashing. Key order must not change the hash."""
    if is_dataclass(obj) and not isinstance(obj, type):
        obj = asdict(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _hash(obj) -> str:
    return hashlib.sha256(_canonical(obj).encode()).hexdigest()[:16]


def git_sha() -> tuple[str, bool]:
    """(sha, dirty). Env first, so a container without git still reports it.

    Returns ``("unknown", True)`` rather than guessing. Preflight treats an
    unknown SHA as a failure: a result that cannot be tied to code is not
    reproducible, and pretending otherwise is worse than admitting it.
    """
    env = os.environ.get("DELTABOT_GIT_SHA")
    if env:
        return env, os.environ.get("DELTABOT_GIT_DIRTY", "") not in ("", "0", "false")
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=5, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"],
                                    capture_output=True, text=True, timeout=5,
                                    check=True).stdout.strip())
        return sha, dirty
    except Exception:                                   # noqa: BLE001
        return UNKNOWN_SHA, True


#: Execution parameters are part of the experiment too. They are not strategy
#: rules, but they change which fills happen, so a run with a different entry
#: TTL is a different experiment.
EXECUTION_FIELDS = ("entry_ttl_seconds", "max_entry_deviation", "min_fill_rr",
                    "slippage_bps")


@dataclass(frozen=True)
class ExperimentIdentity:
    experiment_id: str
    strategy_hash: str
    risk_hash: str
    execution_hash: str
    config_hash: str
    git_sha: str
    git_dirty: bool
    app_version: str
    strategy_version: str
    symbols: tuple[str, ...]
    snapshot: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["symbols"] = list(self.symbols)
        return d

    def differences(self, other: "ExperimentIdentity") -> list[str]:
        """Which components moved. Named, so an operator can see what changed."""
        out = []
        for f in ("strategy_hash", "risk_hash", "execution_hash", "git_sha",
                  "app_version"):
            a, b = getattr(self, f), getattr(other, f)
            if a != b:
                out.append(f"{f}: {b} -> {a}")
        if set(self.symbols) != set(other.symbols):
            out.append(f"symbols: {sorted(other.symbols)} -> {sorted(self.symbols)}")
        return out


def build_identity(experiment_id: str, strategy, risk, execution: dict,
                   symbols) -> ExperimentIdentity:
    """Compute the composite identity of a configuration.

    The composite hash is over the three COMPONENT HASHES rather than over one
    flattened blob, so a change is attributable: the operator is told which of
    strategy, risk or execution moved.
    """
    strategy_hash = strategy.config_hash
    risk_hash = _hash(risk)
    exec_hash = _hash({k: execution[k] for k in sorted(execution)})
    sha, dirty = git_sha()
    composite = _hash({
        "strategy": strategy_hash, "risk": risk_hash, "execution": exec_hash,
        "symbols": sorted(symbols), "app_version": APP_VERSION,
    })
    return ExperimentIdentity(
        experiment_id=experiment_id, strategy_hash=strategy_hash,
        risk_hash=risk_hash, execution_hash=exec_hash, config_hash=composite,
        git_sha=sha, git_dirty=dirty, app_version=APP_VERSION,
        strategy_version=strategy.version, symbols=tuple(symbols),
        snapshot={"strategy": strategy.to_dict(),
                  "risk": asdict(risk) if is_dataclass(risk) else dict(risk),
                  "execution": dict(execution),
                  "symbols": sorted(symbols)})


class ConfigurationDrift(Exception):
    """The running configuration differs from the recorded experiment.

    Raised, never absorbed. Adopting the new configuration would silently make
    the second half of a 30-day run a different experiment from the first.
    """

    def __init__(self, experiment_id: str, differences: list[str]) -> None:
        super().__init__(
            f"configuration drift in experiment {experiment_id}: "
            + "; ".join(differences)
            + ". Start a NEW experiment rather than changing this one.")
        self.experiment_id = experiment_id
        self.differences = differences
