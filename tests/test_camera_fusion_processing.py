import json

import numpy as np
import pytest

from openflight.camera import fusion_processing as fp
from openflight.camera.ball_flight import CameraBallEstimate, HorizontalFusionDecision
from openflight.camera.club_delivery import ChainedDelivery, ReferenceBallTracker
from openflight.camera.club_motion import ReferenceBall
from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs
from openflight.clubs import ClubType
from openflight.iwr6843.club import ClubRangeEvidence
from openflight.iwr6843.lcmf import BallRangeEvidence
from openflight.iwr6843.tracking import BallTrack, Geometry


def _geometry():
    return EffectiveCameraGeometryInputs(
        camera_height_m=0.095,
        radar_height_m=0.051,
        tee_slant_range_m=1.5,
        ball_height_m=0.021,
        camera_lateral_offset_m=0.0,
        camera_forward_offset_m=0.03,
        image_width_px=8,
        image_height_px=6,
        horizontal_pixel_sign=1.0,
        roll_correction_deg=0.0,
        ball_horizontal_output_offset_deg=0.0,
        ball_diameter_m=0.04267,
    )


def _context(ball_tracker=None, club_tracker=None):
    return fp.build_context(
        geometry=_geometry(),
        lighting_eligible=True,
        ball_tracker=ball_tracker or ReferenceBallTracker(),
        club_tracker=club_tracker or ReferenceBallTracker(),
        ball_range_evidence=None,
        club_range_evidence=None,
        ops_ball_speed_mph=100.0,
        ops_club_speed_mph=75.0,
        iwr_vertical_deg=18.0,
        iwr_horizontal_deg=1.0,
        iwr_horizontal_confidence=0.8,
        club=ClubType.IRON_7,
        capture_npz_sha256="capture-hash",
        session_uuid="session-a",
        shot_number=3,
    )


def _archive():
    return {
        "frames": np.zeros((20, 6, 8), np.uint8),
        "host_timestamp_ns": np.arange(20, dtype=np.int64),
        "trigger_host_timestamp_ns": np.asarray(10, dtype=np.int64),
        "pre_trigger_count": np.asarray(10, dtype=np.int64),
        "_capture_npz_sha256": "capture-hash",
    }


def test_context_is_hashed_serializable_and_tamper_evident():
    context = _context()
    result = fp.process_camera_fusion(context, _archive())
    assert result["context_sha256"] == context["sha256"]
    changed = {**context, "ops_ball_speed_mph": 101.0}
    with pytest.raises(ValueError, match="fingerprint"):
        fp.process_camera_fusion(changed, _archive())
    with pytest.raises(ValueError, match="capture archive"):
        fp.process_camera_fusion(context, {**_archive(), "_capture_npz_sha256": "other"})


def test_both_tracker_states_evolve_and_replay_exactly(monkeypatch):
    ball_tracker = ReferenceBallTracker(max_samples=5, min_fallback_samples=2)
    club_tracker = ReferenceBallTracker(max_samples=7, min_fallback_samples=3)
    ball_tracker.resolve(ReferenceBall(3.0, 4.0, 12.0, 100))
    club_tracker.resolve(ReferenceBall(2.0, 4.0, 13.0, 110))
    context = _context(ball_tracker, club_tracker)

    def ball_estimator(*_args, ball_tracker, **_kwargs):
        ball_tracker.resolve(ReferenceBall(3.2, 4.1, 12.1, 101))
        return CameraBallEstimate(status="ok", horizontal_deg=2.0, confidence_tier="high")

    def club_estimator(*_args, ball_tracker, **_kwargs):
        ball_tracker.resolve(ReferenceBall(2.2, 4.1, 13.1, 111))
        return ChainedDelivery(status="chained_high", attack_angle_deg=-4.0, club_path_deg=1.0)

    monkeypatch.setattr(fp, "estimate_camera_ball_flight", ball_estimator)
    monkeypatch.setattr(fp, "estimate_chained_delivery", club_estimator)
    first = fp.process_camera_fusion(context, _archive())
    replay = fp.process_camera_fusion(context, _archive())
    assert first == replay
    assert len(first["next_ball_tracker"]["samples"]) == 2
    assert len(first["next_club_tracker"]["samples"]) == 2
    assert first["next_ball_tracker"]["max_samples"] == 5
    assert first["next_club_tracker"]["min_fallback_samples"] == 3


def test_stage_exception_does_not_prevent_other_stage(monkeypatch):
    monkeypatch.setattr(fp, "estimate_camera_ball_flight", lambda *_a, **_k: 1 / 0)
    monkeypatch.setattr(
        fp,
        "estimate_chained_delivery",
        lambda *_a, **_k: ChainedDelivery(status="rejected_no_impact"),
    )
    monkeypatch.setattr(
        fp,
        "select_camera_assisted_horizontal",
        lambda *_a, **_k: HorizontalFusionDecision(
            None, None, None, "iwr_fallback", 1.0, None, None
        ),
    )
    result = fp.process_camera_fusion(_context(), _archive())
    assert result["ball_estimate"]["status"] == "error"
    assert result["club_delivery"]["status"] == "rejected_no_impact"
    assert "ZeroDivisionError" in result["errors"]["ball"]


def test_missing_tracker_context_is_not_reconstructed_as_empty():
    context = _context()
    del context["ball_tracker"]
    with pytest.raises(ValueError, match="fingerprint"):
        fp.process_camera_fusion(context, _archive())


@pytest.mark.parametrize("limit", [0, -1, 1.5, True])
def test_tracker_snapshot_rejects_invalid_limits(limit):
    snapshot = ReferenceBallTracker().snapshot()
    snapshot["max_samples"] = limit
    with pytest.raises(ValueError, match="positive integers"):
        ReferenceBallTracker.from_snapshot(snapshot)


def test_tracker_snapshot_rejects_nonfinite_sample():
    snapshot = ReferenceBallTracker().snapshot()
    snapshot["samples"] = [{"x": float("nan"), "y": 1.0, "diameter_px": 12.0, "area_px": 10}]
    with pytest.raises(ValueError, match="finite"):
        ReferenceBallTracker.from_snapshot(snapshot)


def test_real_range_evidence_is_strict_json_and_round_trips(monkeypatch):
    track = BallTrack(
        speed_ms=np.float64(45.0),
        slope_bins=-2.0,
        intercept_bins=40.0,
        rms_bins=0.2,
        n_inliers=np.int64(8),
        t_first=0.01,
        t_last=0.04,
        low_confidence=False,
    )
    geometry = Geometry(
        n_frames=np.int64(4),
        chirps_per_frame=8,
        n_tx=2,
        n_rx=4,
        n_samples=64,
        frame_period_s=np.float64(0.01),
        trigger_frame=1,
    )
    evidence = BallRangeEvidence(track, geometry, np.float64(0.02))
    context = fp.build_context(
        geometry=_geometry(),
        lighting_eligible=True,
        ball_tracker=ReferenceBallTracker(),
        club_tracker=ReferenceBallTracker(),
        ball_range_evidence=evidence,
        club_range_evidence=ClubRangeEvidence(track, geometry, np.float64(0.02)),
        ops_ball_speed_mph=np.float64(100),
        ops_club_speed_mph=np.float64(75),
        iwr_vertical_deg=np.float64(18),
        iwr_horizontal_deg=np.float64(1),
        iwr_horizontal_confidence=np.float64(0.8),
        club=ClubType.IRON_7,
        capture_npz_sha256="capture-hash",
        session_uuid="session-a",
        shot_number=3,
    )
    json.dumps(context, allow_nan=False)
    seen = {}

    def ball_estimator(*_args, range_evidence, **_kwargs):
        seen["ball"] = range_evidence
        return CameraBallEstimate(status="ok")

    def club_estimator(*_args, range_evidence, **_kwargs):
        seen["club"] = range_evidence
        return ChainedDelivery(status="ok")

    monkeypatch.setattr(fp, "estimate_camera_ball_flight", ball_estimator)
    monkeypatch.setattr(fp, "estimate_chained_delivery", club_estimator)
    result = fp.process_camera_fusion(context, _archive())
    json.dumps(result, allow_nan=False)
    assert seen["ball"].track.speed_ms == 45.0
    assert seen["club"].geometry.n_samples == 64


def test_malformed_club_trigger_does_not_discard_ball_result(monkeypatch):
    monkeypatch.setattr(
        fp,
        "estimate_camera_ball_flight",
        lambda *_a, **_k: CameraBallEstimate(status="ok", horizontal_deg=2.5),
    )
    archive = {**_archive(), "pre_trigger_count": "bad"}
    result = fp.process_camera_fusion(_context(), archive)
    assert result["ball_estimate"]["status"] == "ok"
    assert result["club_delivery"]["status"] == "error"
    assert "ValueError" in result["errors"]["club"]


def test_ball_detector_anchor_is_shared_with_club_stage(monkeypatch):
    diagnostics = {
        "selected_candidate": {
            "x": 3.0,
            "y": 4.0,
            "diameter_px": 12.0,
            "area_px": 100,
        },
        "selected_source": "detector_agreement:detected",
    }
    monkeypatch.setattr(
        fp,
        "estimate_camera_ball_flight",
        lambda *_a, **_k: CameraBallEstimate(status="ok", reference_ball_diagnostics=diagnostics),
    )
    seen = {}

    def club_estimator(*_args, reference_ball, reference_ball_selected, **_kwargs):
        seen["ball"] = reference_ball
        seen["selected"] = reference_ball_selected
        return ChainedDelivery(status="rejected_no_impact")

    monkeypatch.setattr(fp, "estimate_chained_delivery", club_estimator)
    result = fp.process_camera_fusion(_context(), _archive())

    assert seen["ball"] == ReferenceBall(3.0, 4.0, 12.0, 100)
    assert seen["selected"] is True
    assert result["ball_estimate"]["reference_ball_diagnostics"] == diagnostics


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"frames": np.zeros((0, 6, 8), np.uint8)}, "nonempty uint8 3D"),
        ({"frames": np.zeros((20, 6, 8), np.float32)}, "nonempty uint8 3D"),
        ({"host_timestamp_ns": np.arange(19, dtype=np.int64)}, "aligned"),
        (
            {"host_timestamp_ns": np.asarray([0] + list(range(19)), dtype=np.int64)},
            "strictly increasing",
        ),
        ({"trigger_host_timestamp_ns": np.asarray([10])}, "integer scalar"),
    ],
)
def test_archive_contract_rejects_malformed_inputs(change, message):
    with pytest.raises(ValueError, match=message):
        fp.process_camera_fusion(_context(), {**_archive(), **change})


def test_out_of_range_pre_trigger_count_is_isolated_to_club_stage(monkeypatch):
    monkeypatch.setattr(
        fp,
        "estimate_camera_ball_flight",
        lambda *_a, **_k: CameraBallEstimate(status="ok"),
    )
    result = fp.process_camera_fusion(
        _context(), {**_archive(), "pre_trigger_count": np.asarray(0, dtype=np.int64)}
    )
    assert result["ball_estimate"]["status"] == "ok"
    assert result["club_delivery"]["status"] == "error"
    assert "outside" in result["errors"]["club"]
