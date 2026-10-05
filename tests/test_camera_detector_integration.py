"""Reference-ball consensus and trajectory cleanup for shared camera fusion."""

import numpy as np
import pytest

from openflight.camera import ball_flight
from openflight.camera.ball_flight import BallCandidate, CameraBallGeometry
from openflight.camera.club_motion import ReferenceBall, detect_impact_reference_ball


def _geometry():
    return CameraBallGeometry(
        camera_height_m=0.095,
        radar_height_m=0.051,
        tee_range_m=1.5,
        ball_height_m=0.021,
        image_width_px=100,
        image_height_px=100,
    )


def _ball(x=50.0, y=70.0, diameter=14.0):
    return ReferenceBall(x, y, diameter, round(np.pi * (diameter / 2) ** 2))


def _candidate(x, y):
    return BallCandidate(x, y, 100, 11, 11, 0.8, 0.9, 220.0)


def test_agreeing_scene_and_impact_candidates_are_both_preserved(monkeypatch):
    monkeypatch.setattr(ball_flight, "detect_reference_ball", lambda _frames, **_kwargs: _ball())
    monkeypatch.setattr(
        ball_flight,
        "detect_impact_reference_ball",
        lambda _frames, **_kwargs: _ball(52.0, 69.0, 14.5),
    )

    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        np.zeros((30, 100, 100), np.uint8), 15, _geometry()
    )

    assert selected.x == pytest.approx(52.0)
    assert diagnostics["agreement"] is True
    assert diagnostics["scene"]["candidate"]["x"] == 50.0
    assert diagnostics["impact"]["candidate"]["x"] == 52.0
    assert diagnostics["distance_px"] == pytest.approx(np.sqrt(5.0))


def test_implausible_candidate_keeps_its_raw_observation_and_rejection_reason(monkeypatch):
    monkeypatch.setattr(
        ball_flight, "detect_reference_ball", lambda _frames, **_kwargs: _ball(96.0)
    )
    monkeypatch.setattr(
        ball_flight,
        "detect_impact_reference_ball",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("no departure")),
    )

    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        np.zeros((30, 100, 100), np.uint8), 15, _geometry()
    )

    assert selected is None
    assert diagnostics["scene"]["status"] == "rejected"
    assert diagnostics["scene"]["candidate"]["x"] == 96.0
    assert diagnostics["scene"]["reason"] == "candidate failed size or hitting-zone geometry"


def test_static_ball_has_no_impact_departure():
    frames = np.full((30, 100, 100), 80, np.uint8)
    yy, xx = np.mgrid[:100, :100]
    frames[:, (xx - 50) ** 2 + (yy - 72) ** 2 <= 7**2] = 230

    with pytest.raises(ValueError, match="departure"):
        detect_impact_reference_ball(frames, trigger_frame_index=15)


def test_estimator_persists_detector_evidence_on_path_rejection(monkeypatch):
    monkeypatch.setattr(ball_flight, "detect_reference_ball", lambda _frames, **_kwargs: _ball())
    monkeypatch.setattr(
        ball_flight,
        "detect_impact_reference_ball",
        lambda _frames, **_kwargs: _ball(51.0, 70.0),
    )
    monkeypatch.setattr(ball_flight, "_pixel_paths", lambda *_args, **_kwargs: [])
    frames = np.zeros((30, 100, 100), np.uint8)
    timestamps = np.arange(30, dtype=np.int64) * 1_000_000

    estimate = ball_flight.estimate_camera_ball_flight(
        frames,
        timestamps,
        trigger_ns=15_000_000,
        range_evidence=None,
        geometry=_geometry(),
        ops_ball_speed_mph=100.0,
    )

    # no IWR range, so no distance: the detectors' evidence is kept all the same
    assert estimate.status == "rejected_no_iwr_range"
    assert estimate.reference_ball_diagnostics["agreement"] is True
    assert estimate.reference_ball_diagnostics["selected_source"].startswith("detector_agreement")


def test_path_cleanup_removes_outlier_and_fills_one_frame_gap():
    frame_indices = list(range(10, 17))
    path = [
        (0, _candidate(10.0, 60.0)),
        (1, _candidate(12.0, 56.0)),
        (2, _candidate(70.0, 20.0)),
        (3, _candidate(16.0, 48.0)),
        (4, _candidate(18.0, 44.0)),
        (6, _candidate(22.0, 36.0)),
    ]

    cleaned = ball_flight._clean_launch_path(path, frame_indices)  # pylint: disable=protected-access
    values = {frame_indices[relative]: candidate for relative, candidate in cleaned}

    assert values[15].x == pytest.approx(20.0)
    assert values[12].x == pytest.approx(14.0)
    assert values[12].y == pytest.approx(52.0)
    assert list(values) == list(range(10, 17))
