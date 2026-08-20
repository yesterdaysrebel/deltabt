"""The report must survive either side of the compression change alone.

The monitor SSM document and this repository's scripts deploy through
different pipelines -- Terraform apply and a git push -- so there is always a
window where one has the compression change and the other does not. A report
that failed during that window would be a second outage caused by fixing the
first, which is the shape the truncation bug already had once.
"""

from __future__ import annotations

import base64
import gzip
import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "daily_report",
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "daily_report.py")
dr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dr)


def _gz(text: str) -> str:
    return base64.b64encode(gzip.compress(text.encode())).decode()


class TestGunzipSection:
    def test_reads_a_compressed_section(self):
        payload = '{"evaluations_24h": 406}'
        got = dr.gunzip_section({"PERSISTENCE_GZ": _gz(payload)}, "PERSISTENCE")
        assert got == payload

    def test_falls_back_to_the_plain_section(self):
        # An old document against a new report: no _GZ key exists at all.
        payload = '{"evaluations_24h": 406}'
        assert dr.gunzip_section({"PERSISTENCE": payload}, "PERSISTENCE") == payload

    def test_prefers_the_compressed_form_when_both_are_present(self):
        got = dr.gunzip_section(
            {"PERSISTENCE": "stale", "PERSISTENCE_GZ": _gz("fresh")}, "PERSISTENCE")
        assert got == "fresh"

    def test_tolerates_whitespace_and_line_wrapping(self):
        # base64 -w0 emits one line, but nothing downstream guarantees it stays
        # one line once it has been through SSM and a YAML document.
        payload = '{"a": 1}'
        blob = _gz(payload)
        wrapped = "\n".join(blob[i:i + 40] for i in range(0, len(blob), 40))
        assert dr.gunzip_section({"X_GZ": wrapped}, "X") == payload

    @pytest.mark.parametrize("blob", ["not-base64!!", "", "   ",
                                      base64.b64encode(b"not gzipped").decode()])
    def test_a_broken_blob_returns_empty_rather_than_raising(self, blob):
        # Truncation is exactly how this section fails, and losing one section
        # must not lose the whole report -- the caller already distinguishes
        # "unavailable" from "zero", which is the distinction that matters.
        assert dr.gunzip_section({"Y_GZ": blob}, "Y") == ""

    def test_a_truncated_gzip_blob_does_not_raise(self):
        blob = _gz('{"evaluations_24h": 406, "padding": "' + "x" * 500 + '"}')
        assert dr.gunzip_section({"Z_GZ": blob[:len(blob) // 2]}, "Z") == ""
