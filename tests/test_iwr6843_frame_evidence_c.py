"""The research frame and sample reports read from C-written dumps (frame_evidence)."""

import sys

import pytest

from openflight.iwr6843 import dump
from openflight.iwr6843.frame_evidence import frame_timing_report, sample_integrity_report
from tests.test_iwr6843_firmware_evidence import FIXTURE_WRITER, _run_c

LITTLE_ENDIAN = pytest.mark.skipif(
    sys.byteorder != "little", reason="the firmware and the parser are little-endian"
)


@LITTLE_ENDIAN
@pytest.mark.parametrize("version", [8, 9])
def test_a_c_written_dump_reads_as_continuous(tmp_path, version):
    out = tmp_path / f"v{version}.l3dump"
    _run_c(FIXTURE_WRITER, tmp_path, str(out), str(version))
    meta, _cube = dump.parse_dump(out.read_bytes())
    assert frame_timing_report(meta)["status"] == "continuous"


@LITTLE_ENDIAN
def test_a_c_written_unassociated_record_leaves_the_request_unobserved(tmp_path):
    out = tmp_path / "unassociated.l3dump"
    _run_c(FIXTURE_WRITER, tmp_path, str(out), "8", "unassociated")
    meta, _cube = dump.parse_dump(out.read_bytes())
    report = frame_timing_report(meta)
    request = report["request"]
    assert [(f["kind"], f["frame"]) for f in report["findings"]] == [("unassociated", 1)]
    assert report["observed_start_offsets_us"] == [0.0, None]
    assert request["status"] == "unobserved"
    assert request["first_post_seq"] is None
    assert request["frames_request_to_first_post"] is None
    assert request["request_to_first_post_start_us"] is None


@LITTLE_ENDIAN
@pytest.mark.parametrize("version", [8, 9])
def test_a_partly_scanned_iq8_dump_is_unobserved_never_clean(tmp_path, version):
    """Frame 0 was not scanned: the whole capture's integrity is unobserved, never clean."""
    out = tmp_path / f"iq8_v{version}.l3dump"
    _run_c(FIXTURE_WRITER, tmp_path, str(out), str(version), "iq8")
    meta, cube = dump.parse_dump(out.read_bytes())
    report = sample_integrity_report(meta, cube)
    assert (report["format"], report["status"]) == ("iq8", "unobserved")
    assert report["reason"] == "frames [0] were not scanned before narrowing"


@pytest.mark.skipif(
    sys.byteorder != "little", reason="the firmware and the parser are little-endian"
)
def test_a_c_written_fully_scanned_iq8_dump_reports_its_overflow(tmp_path):
    """Every frame scanned: the report carries the firmware's counts, frame by frame."""
    out = tmp_path / "iq8_scanned.l3dump"
    _run_c(FIXTURE_WRITER, tmp_path, str(out), "8", "iq8", "all-scanned")

    meta, cube = dump.parse_dump(out.read_bytes())
    report = sample_integrity_report(meta, cube)

    assert report["status"] == "overflowed"
    assert report["frames_scanned"] == 2
    assert report["overflow_components"] == 2
    assert report["frames_with_overflow"] == [1]
    assert report["max_abs"] == 300
