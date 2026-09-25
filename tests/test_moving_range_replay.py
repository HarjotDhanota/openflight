"""Offline moving-range replay contracts."""

from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera.moving_iwr_anchor import MovingIwrAnchorResult
from openflight.iwr6843.range_evidence import MovingRangeTrackResult
from openflight.iwr6843.tracking import BallTrack, Geometry
from openflight.moving_range_replay import (
    replay_moving_camera_iwr_anchor,
    replay_moving_iwr_range,
)
from openflight.tee_range import TeeRangeCandidate

HASH = "a" * 64


def _track_result():
    geometry = Geometry(
        n_frames=4,
        chirps_per_frame=4,
        n_tx=2,
        n_rx=4,
        n_samples=128,
        frame_period_s=0.01,
        trigger_frame=0,
        loop_period_s=0.001,
    )
    track = BallTrack(40.0, 100.0, 30.0, 0.2, 40, 0.01, 0.035, False)
    return MovingRangeTrackResult("selected", track, geometry, "burst", HASH, {})


def _runtime():
    return {
        "net_range_m": 4.0,
        "calibration": {
            "source_sha256": HASH,
            "effective": {"range_bias_m": 0.05},
        },
    }


def _evidence():
    return {
        "range_calibration": {
            "source_sha256": HASH,
            "bias_m": 0.05,
            "uncertainty_m": 0.01,
            "qualified": True,
            "source": "independent reflector survey",
        },
        "impact_time": {
            "time_s": 0.005,
            "uncertainty_s": 0.0002,
            "source": "contact switch bench",
            "qualified": True,
            "independent_of_iwr_range": True,
            "provenance": {
                "independence_basis": "contact edge observed on an independent clock bench",
                "dependencies": ["contact_edge", "qualified_clock_mapping"],
            },
        },
        "camera_iwr_clock_mapping": {
            "offset_s": 0.0,
            "uncertainty_s": 0.0002,
            "qualified": True,
            "source": "shared edge timing bench",
            "source_sha256": "b" * 64,
            "provenance": {"bench": "led-contact-1"},
        },
    }


def test_moving_iwr_replay_retains_full_series_and_diagnostic_candidate(monkeypatch):
    monkeypatch.setattr(
        "openflight.moving_range_replay.extract_moving_ball_range_track",
        lambda *_args, **_kwargs: _track_result(),
    )
    candidate = TeeRangeCandidate(
        candidate_id="moving-1",
        source="iwr_moving_track_independent_impact",
        source_group="iwr",
        radar_slant_range_m=1.42,
        uncertainty_m=0.03,
        evidence={"impact_time": {"independent_of_iwr_range": True}},
        selectable=False,
    )
    monkeypatch.setattr(
        "openflight.moving_range_replay.build_moving_track_candidate",
        lambda *_args, **_kwargs: candidate,
    )

    stage, series = replay_moving_iwr_range(
        b"raw",
        capture_event={"shot_number": 1, "capture_bytes": 3},
        runtime_config=_runtime(),
        shot_event={"moving_range_evidence": _evidence()},
        club="7-iron",
    )

    assert stage["status"] == "candidate"
    assert stage["promotion_allowed"] is False
    assert stage["independent_support_eligible"] is False
    assert stage["tee_range_candidate"] == candidate.to_dict()
    assert len(stage["track"]["series"]["times_s"]) > 2
    assert stage["track"]["series"]["range_reference"] == "bias_corrected"
    assert series is not None and series.qualified
    repeated, repeated_series = replay_moving_iwr_range(
        b"raw",
        capture_event={"shot_number": 1, "capture_bytes": 3},
        runtime_config=_runtime(),
        shot_event={"moving_range_evidence": _evidence()},
        club="7-iron",
    )
    assert repeated == stage
    assert repeated_series == series


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"impact_time": None}, "no independent impact-time"),
        ({"impact_time": {**_evidence()["impact_time"], "qualified": False}}, "not qualified"),
        (
            {
                "impact_time": {
                    **_evidence()["impact_time"],
                    "provenance": {"independence_basis": "circular", "dependencies": ["iwr_range"]},
                }
            },
            "does not prove independence",
        ),
        (
            {"range_calibration": {**_evidence()["range_calibration"], "qualified": False}},
            "qualified range-bias",
        ),
    ],
)
def test_moving_candidate_is_withheld_for_every_unqualified_input(monkeypatch, change, reason):
    monkeypatch.setattr(
        "openflight.moving_range_replay.extract_moving_ball_range_track",
        lambda *_args, **_kwargs: _track_result(),
    )
    evidence = _evidence()
    evidence.update(change)

    stage, _series = replay_moving_iwr_range(
        b"raw",
        capture_event={},
        runtime_config=_runtime(),
        shot_event={"moving_range_evidence": evidence},
        club=None,
    )

    assert stage["status"] == "withheld"
    assert stage["tee_range_candidate"] is None
    assert reason in stage["reason"]


def test_missing_iwr_dump_is_structured_and_does_not_fabricate_track():
    stage, series = replay_moving_iwr_range(
        None,
        capture_event={},
        runtime_config={},
        shot_event={},
        club=None,
    )
    assert stage["status"] == "withheld_missing_iwr_capture"
    assert stage["track"] is None
    assert series is None


def _archive():
    return {
        "frames": np.zeros((6, 20, 30), dtype=np.uint8),
        "host_timestamp_ns": np.arange(6, dtype=np.int64) * 1_000_000,
        "trigger_host_timestamp_ns": np.int64(0),
    }


def _series():
    from openflight.camera.moving_iwr_anchor import TimedIwrRangeSeries

    return TimedIwrRangeSeries((0.0, 0.01), (1.5, 1.9), 0.02, "qualified track", True)


@pytest.mark.parametrize(
    "missing,expected",
    [
        ("archive", "saved_camera_frames"),
        ("camera", "qualified_camera_model"),
        ("clock", "qualified_camera_iwr_clock"),
        ("ops", "ops_ball_speed"),
        ("iwr", "qualified_iwr_range_series"),
    ],
)
def test_camera_iwr_adapter_withholds_each_missing_prerequisite(monkeypatch, missing, expected):
    camera = SimpleNamespace(accuracy_qualified=True)
    monkeypatch.setattr(
        "openflight.moving_range_replay._qualified_camera",
        lambda _context: (None, "unqualified") if missing == "camera" else (camera, "qualified"),
    )
    evidence = _evidence()
    if missing == "clock":
        evidence.pop("camera_iwr_clock_mapping")
    called = []
    monkeypatch.setattr(
        "openflight.moving_range_replay.estimate_moving_ball_impact_anchor",
        lambda *_args, **_kwargs: called.append(True),
    )

    stage = replay_moving_camera_iwr_anchor(
        context={},
        archive=None if missing == "archive" else _archive(),
        shot_event={"moving_range_evidence": evidence},
        iwr_ranges=None if missing == "iwr" else _series(),
        ops_ball_speed_mph=None if missing == "ops" else 100.0,
    )

    assert stage["status"] == "withheld_prerequisites"
    assert (
        next(item for item in stage["prerequisites"] if item["id"] == expected)["status"]
        == "withheld"
    )
    assert called == []


def test_selected_camera_iwr_diagnostic_retains_alternatives_but_cannot_promote(monkeypatch):
    monkeypatch.setattr(
        "openflight.moving_range_replay._qualified_camera",
        lambda _context: (SimpleNamespace(accuracy_qualified=True), "qualified"),
    )
    result = MovingIwrAnchorResult(
        "selected",
        "diagnostic",
        (12.0, 8.0),
        1.5,
        "path-1",
        (),
        {"distinct_candidate_count": 2},
    )
    monkeypatch.setattr(
        "openflight.moving_range_replay.estimate_moving_ball_impact_anchor",
        lambda *_args, **_kwargs: result,
    )

    stage = replay_moving_camera_iwr_anchor(
        context={},
        archive=_archive(),
        shot_event={"moving_range_evidence": _evidence()},
        iwr_ranges=_series(),
        ops_ball_speed_mph=100.0,
    )

    assert stage["status"] == "selected"
    assert stage["result"]["selected_path_id"] == "path-1"
    assert stage["promotion_allowed"] is False
    assert stage["independent_camera_support"] is False
    assert "same moving IWR range" in stage["dependency_reason"]
