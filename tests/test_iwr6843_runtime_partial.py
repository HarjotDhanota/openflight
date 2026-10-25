"""The IWR runtime decodes a failed transfer's bytes and labels what it measures "iwr_partial".

A capture whose transfer lost bytes is never a valid capture; the runtime runs the
estimators on the frames ``partial_dump`` recovers, and every result says it came
from a partial dump and what was missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openflight.iwr6843 import launch_range as lr
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.club import ClubPathResult
from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.lcmf import LCMFResult
from openflight.iwr6843.partial_dump import PARTIAL_LABEL
from openflight.iwr6843.runtime import IWR6843Runtime
from tests.iwr6843_partial_scene import FRAME_NBYTES, lose, pack, scene


@dataclass
class FakeCapture:
    raw: bytes | None
    error: str | None
    partial_raw: bytes | None = field(default=None, repr=False)
    path = None
    trigger_timestamp = 1.0
    dump_duration_s = 5.3
    sequence = 1

    @property
    def valid(self) -> bool:
        return self.raw is not None and self.error is None


class FakeMonitor:
    def __init__(self, capture):
        self.capture = capture

    def capture_for_shot(self, _ts, timeout_s):
        del timeout_s
        return self.capture

    def stop(self):
        pass


def _runtime(capture):
    return IWR6843Runtime(
        capture_monitor=FakeMonitor(capture),
        calibration=Calibration.identity(),
        net_range_m=4.6,
    )


def _measured():
    return lr.LaunchRangeEstimate(
        status="measured", reason=None, method="ball_track_start", range_m=1.91, uncertainty_m=0.1
    )


@pytest.fixture(scope="module", name="lossy")
def fixture_lossy():
    cube = scene(seed=21)
    raw = pack(cube)
    return cube, raw, lose(raw, [(6 * FRAME_NBYTES + 5_000, 3 * 256)])


@pytest.fixture(name="seen")
def fixture_seen():
    calls = {}

    def fake_prepare(raw):
        calls["prepared_raw"] = raw
        return SimpleNamespace(vertical="vertical")

    def fake_process(raw, calibration, **kwargs):
        calls["raw"] = raw
        calls["calibration"] = calibration
        return LCMFResult(status="accepted", angle_deg=13.5), ClubPathResult(
            status="accepted", path_deg=2.0
        )

    with (
        patch("openflight.iwr6843.runtime.prepare_lcmf_capture", side_effect=fake_prepare),
        patch("openflight.iwr6843.runtime.process_raw_capture", side_effect=fake_process),
        patch("openflight.iwr6843.runtime.estimate_launch_range", return_value=_measured()),
    ):
        yield calls


def test_a_failed_transfer_is_decoded_and_every_result_is_labelled_partial(lossy, seen):
    cube, _raw, partial = lossy
    capture = FakeCapture(raw=None, error="IWR6843 dump ended early", partial_raw=partial)

    result = _runtime(capture).process_shot(
        impact_timestamp=1.0, ball_speed_mph=100.0, club="7i", club_speed_mph=74.0
    )

    meta, recovered = parse_dump(seen["raw"])
    assert seen["prepared_raw"] == seen["raw"]
    assert meta["n_frames"] == 11
    assert recovered.shape[0] == 11 and (recovered[6] == cube[7]).all()
    assert result.partial_recovery["label"] == PARTIAL_LABEL
    assert result.partial_recovery["frames_dropped"] == [6]
    assert result.measurement.status == f"accepted_{PARTIAL_LABEL}"
    assert result.measurement.accepted
    assert result.club_path.status == f"accepted_{PARTIAL_LABEL}"
    assert result.launch_range.label == PARTIAL_LABEL
    assert result.launch_range.to_dict()["partial"]["frames_dropped"] == [6]
    assert result.ball_range.label == PARTIAL_LABEL
    assert result.ball_range.source == "iwr_post_impact"
    assert result.ball_range.range_m == 1.91
    assert "768 of" in result.ball_range.reason and "dropped frames 6" in result.ball_range.reason
    assert seen["calibration"].tee_range_m == 1.91


def test_bytes_that_cannot_be_decoded_measure_nothing_but_say_why(seen):
    capture = FakeCapture(raw=None, error="ended early", partial_raw=b"ILD1\x07\x00 too short")

    result = _runtime(capture).process_shot(
        impact_timestamp=1.0, ball_speed_mph=100.0, club="7i", club_speed_mph=74.0
    )

    assert result.measurement is None
    assert "raw" not in seen
    assert result.partial_recovery["status"] == "unrecoverable"
    assert "header" in result.partial_recovery["reason"]


def test_a_failure_without_bytes_has_no_partial_recovery(seen):
    capture = FakeCapture(raw=None, error="ended early")

    result = _runtime(capture).process_shot(
        impact_timestamp=1.0, ball_speed_mph=100.0, club="7i", club_speed_mph=74.0
    )

    assert result.measurement is None
    assert result.partial_recovery is None


def test_a_whole_capture_is_measured_and_labelled_as_before(lossy, seen):
    _cube, raw, _partial = lossy
    capture = FakeCapture(raw=raw, error=None)

    result = _runtime(capture).process_shot(
        impact_timestamp=1.0, ball_speed_mph=100.0, club="7i", club_speed_mph=74.0
    )

    assert seen["raw"] == raw
    assert result.partial_recovery is None
    assert result.measurement.status == "accepted"
    assert result.ball_range.label == "measured"
    assert result.launch_range.to_dict()["partial"] is None
