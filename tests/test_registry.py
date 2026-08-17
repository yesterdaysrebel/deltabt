"""The H-Structure-1 record, and the verdict vocabulary it had to map onto."""

from __future__ import annotations

import pytest

from deltabt.research.record_hstructure1 import EXPERIMENT_ID, build, main
from deltabt.research.registry import Experiment, load_all, record


def test_record_validates():
    build().validate()


def test_classification_is_a_permitted_verdict():
    assert build().classification == "NO SIGNAL"
    assert "NO SIGNAL" in Experiment.VALID


def test_dead_is_not_a_registry_verdict():
    """The report's word is DEAD; the registry has no such verdict."""
    assert "DEAD" not in Experiment.VALID
    exp = build()
    exp.classification = "DEAD"
    with pytest.raises(ValueError, match="classification must be one of"):
        exp.validate()


def test_recorded_once_in_the_live_registry():
    ids = [r["experiment_id"] for r in load_all()]
    assert ids.count(EXPERIMENT_ID) == 1


def test_rerunning_refuses_to_duplicate(tmp_path):
    """Never against the real registry: on a checkout missing the record this
    would append to a tracked, append-only file as a test side effect."""
    path = tmp_path / "experiments.jsonl"
    assert main(path) == 0
    assert main(path) == 1
    assert len(load_all(path)) == 1


def test_record_appends_and_reloads(tmp_path):
    path = tmp_path / "experiments.jsonl"
    record(build(), path=path)
    rows = load_all(path)
    assert len(rows) == 1
    assert rows[0]["experiment_id"] == EXPERIMENT_ID
    assert rows[0]["out_of_sample"]["test"] == "LOCKED - not computed"
    assert rows[0]["recorded_at"]
