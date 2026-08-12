"""Append-only experiment registry.

Every hypothesis test writes one record. Records are never overwritten or
deleted -- failed experiments are the majority of the value here, and a
registry that silently drops them reintroduces exactly the selection bias the
research protocol exists to control.

The file is JSONL so that a partially-written run still leaves every prior
record readable.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deltabt.config import OUT_DIR

REGISTRY_PATH = Path(os.environ.get("DELTABT_REGISTRY", OUT_DIR / "experiments.jsonl"))


@dataclass
class Experiment:
    """One pre-registered hypothesis test.

    Fields mirror the research protocol's required record. `classification`
    must be one of the six permitted verdicts; anything else is a bug.
    """

    experiment_id: str
    hypothesis: str
    strategy_family: str
    symbols: list[str]
    timeframe: str
    data_start: str
    data_end: str
    entry_rules: str
    exit_rules: str
    position_sizing: str
    cost_assumptions: str
    parameters: dict[str, Any] = field(default_factory=dict)

    # results
    trades: int | None = None
    effective_n: float | None = None
    gross_expectancy_r: float | None = None
    net_expectancy_r: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    t_stat: float | None = None
    sharpe: float | None = None
    max_drawdown: float | None = None
    baseline_result: dict[str, Any] = field(default_factory=dict)
    out_of_sample: dict[str, Any] = field(default_factory=dict)
    robustness: dict[str, Any] = field(default_factory=dict)

    classification: str | None = None
    reason: str | None = None
    notes: str = ""
    recorded_at: str = ""

    #: The union of the verdict vocabularies used by the successive
    #: pre-registrations in this program. Kept as one list so a verdict can
    #: never be invented at write time -- an unknown string is a bug, and the
    #: registry refuses it rather than recording an unclassifiable result.
    VALID = (
        # strongest
        "ROBUST",
        "VALIDATED EDGE",
        "PROMISING",
        "PROMISING BUT UNPROVEN",
        # qualified
        "FRAGILE",
        "EXECUTION-DEPENDENT",
        # positive gross expectancy that costs consume -- distinct from
        # NO SIGNAL, because the mechanism exists but is uneconomic here
        "REAL SIGNAL / NO ECONOMIC EDGE",
        "NO ECONOMIC EDGE",
        # negative
        "DATA-MINED",
        "NO EDGE",
        "NO SIGNAL",
        "INSUFFICIENT DATA",
    )

    def validate(self) -> None:
        if self.classification is not None and self.classification not in self.VALID:
            raise ValueError(
                f"classification must be one of {self.VALID}, got {self.classification!r}"
            )


def record(exp: Experiment, path: Path = REGISTRY_PATH) -> Path:
    """Append one experiment. Never truncates."""
    exp.validate()
    exp.recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(asdict(exp), default=str) + "\n")
    return path


def load_all(path: Path = REGISTRY_PATH) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def summary(path: Path = REGISTRY_PATH) -> str:
    rows = load_all(path)
    if not rows:
        return "(registry empty)"
    lines = [f"{len(rows)} experiment(s) recorded:"]
    for r in rows:
        lines.append(
            f"  {r['experiment_id']:<16} {str(r.get('classification')):<24}"
            f" n={r.get('trades')} netR={r.get('net_expectancy_r')}"
        )
    return "\n".join(lines)
