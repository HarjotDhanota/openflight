import json

import numpy as np
import pytest

from openflight.camera import fusion_processing as fp
from openflight.camera.ball_flight import CameraBallEstimate, HorizontalFusionDecision
from openflight.camera.club_delivery import ChainedDelivery
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
        ball_diameter_m=0.0427,
    )


def _context_kwargs() -> dict:
    return {
        "geometry": _geometry(),
        "lighting_eligible": True,
        "ball_range_evidence": None,
        "club_range_evidence": None,
        "ops_ball_speed_mph": 100.0,
        "ops_club_speed_mph": 75.0,
        "iwr_vertical_deg": 18.0,
        "iwr_horizontal_deg": 1.0,
        "iwr_horizontal_confidence": 0.8,
        "club": ClubType.IRON_7,
        "capture_npz_sha256": "capture-hash",
        "session_uuid": "session-a",
        "shot_number": 3,
    }


def _context():
    return fp.build_context(**_context_kwargs())


def _archive():
    return {
        "frames": np.zeros((20, 6, 8), np.uint8),
        "host_timestamp_ns": np.arange(20, dtype=np.int64),
        "trigger_host_timestamp_ns": np.asarray(10, dtype=np.int64),
        "pre_trigger_count": np.asarray(10, dtype=np.int64),
        "_capture_npz_sha256": "capture-hash",
    }


def _record_range_evidence(monkeypatch):
    seen = {}

    def ball_estimator(*_args, range_evidence, **_kwargs):
        seen["ball"] = range_evidence
        return CameraBallEstimate(status="ok")

    def club_estimator(*_args, range_evidence, **_kwargs):
        seen["club"] = range_evidence
        return ChainedDelivery(status="ok")

    monkeypatch.setattr(fp, "estimate_camera_ball_flight", ball_estimator)
    monkeypatch.setattr(fp, "estimate_chained_delivery", club_estimator)
    return seen


def test_context_is_hashed_serializable_and_tamper_evident():
    context = _context()
    result = fp.process_camera_fusion(context, _archive())
    assert result["context_sha256"] == context["sha256"]
    assert result["notes"] == []  # eligible lighting carries no notes
    changed = {**context, "ops_ball_speed_mph": 101.0}
    with pytest.raises(ValueError, match="fingerprint"):
        fp.process_camera_fusion(changed, _archive())
    with pytest.raises(ValueError, match="capture archive"):
        fp.process_camera_fusion(context, {**_archive(), "_capture_npz_sha256": "other"})


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
    numpy_scalars = {
        name: np.float64(_context_kwargs()[name])
        for name in (
            "ops_ball_speed_mph",
            "ops_club_speed_mph",
            "iwr_vertical_deg",
            "iwr_horizontal_deg",
            "iwr_horizontal_confidence",
        )
    }
    context = fp.build_context(
        **{
            **_context_kwargs(),
            **numpy_scalars,
            "ball_range_evidence": BallRangeEvidence(track, geometry, np.float64(0.02)),
            "club_range_evidence": ClubRangeEvidence(track, geometry, np.float64(0.02)),
        }
    )
    json.dumps(context, allow_nan=False)
    seen = _record_range_evidence(monkeypatch)
    result = fp.process_camera_fusion(context, _archive())
    json.dumps(result, allow_nan=False)
    assert seen["ball"].track.speed_ms == 45.0
    assert seen["club"].geometry.n_samples == 64


@pytest.mark.parametrize(
    ("pre_trigger_count", "why"),
    [("bad", "ValueError"), (np.asarray(0, dtype=np.int64), "outside")],
)
def test_a_bad_club_trigger_is_isolated_to_the_club_stage(monkeypatch, pre_trigger_count, why):
    monkeypatch.setattr(
        fp,
        "estimate_camera_ball_flight",
        lambda *_a, **_k: CameraBallEstimate(status="ok", horizontal_deg=2.5),
    )
    archive = {**_archive(), "pre_trigger_count": pre_trigger_count}
    result = fp.process_camera_fusion(_context(), archive)
    assert result["ball_estimate"]["status"] == "ok"
    assert result["club_delivery"]["status"] == "error"
    assert why in result["errors"]["club"]


def test_ball_detector_anchor_is_shared_with_club_stage(monkeypatch):
    diagnostics = {
        "selected_candidate": {"x": 3.0, "y": 4.0, "diameter_px": 12.0, "area_px": 100},
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


def test_range_evidence_status_rides_in_the_frozen_context(monkeypatch):
    track = BallTrack(45.0, -2.0, 40.0, 0.2, 8, 0.01, 0.04, False)
    geometry = Geometry(4, 8, 2, 4, 64, 0.01, 1)
    context = fp.build_context(
        **{
            **_context_kwargs(),
            "ball_range_evidence": BallRangeEvidence(
                track, geometry, 0.02, status="rejected_track_quality"
            ),
            "club_range_evidence": ClubRangeEvidence(
                track, geometry, 0.02, status="rejected_club_speed_mismatch"
            ),
        }
    )
    seen = _record_range_evidence(monkeypatch)
    fp.process_camera_fusion(context, _archive())

    assert context["ball_range_evidence"]["status"] == "rejected_track_quality"
    assert seen["ball"].status == "rejected_track_quality"
    assert seen["club"].status == "rejected_club_speed_mismatch"


def test_context_recorded_before_range_status_replays_as_accepted(monkeypatch):
    track = BallTrack(45.0, -2.0, 40.0, 0.2, 8, 0.01, 0.04, False)
    geometry = Geometry(4, 8, 2, 4, 64, 0.01, 1)
    snapshot = fp._range_snapshot(BallRangeEvidence(track, geometry, 0.02), "ball")
    del snapshot["status"]

    assert fp._restore_range(snapshot, "ball").status == "accepted"


def test_ineligible_lighting_is_a_note_and_both_stages_still_run(monkeypatch):
    ran = []
    monkeypatch.setattr(
        fp,
        "estimate_camera_ball_flight",
        lambda *_a, **_k: ran.append("ball") or CameraBallEstimate(status="accepted"),
    )
    monkeypatch.setattr(
        fp,
        "estimate_chained_delivery",
        lambda *_a, **_k: ran.append("club") or ChainedDelivery(status="chained_experimental"),
    )
    context = fp.build_context(
        **{
            **_context_kwargs(),
            "lighting_eligible": False,
            "notes": ["lighting: too bright for the ball: 83% of it is clipped"],
        }
    )
    result = fp.process_camera_fusion(context, _archive())

    assert ran == ["ball", "club"]
    assert result["ball_estimate"]["status"] == "accepted"
    assert result["club_delivery"]["status"] == "chained_experimental"
    assert result["notes"] == ["lighting: too bright for the ball: 83% of it is clipped"]


def test_a_context_recorded_before_notes_names_its_ineligible_lighting(monkeypatch):
    monkeypatch.setattr(
        fp, "estimate_camera_ball_flight", lambda *_a, **_k: CameraBallEstimate(status="x")
    )
    monkeypatch.setattr(
        fp, "estimate_chained_delivery", lambda *_a, **_k: ChainedDelivery(status="y")
    )
    context = fp.build_context(**{**_context_kwargs(), "lighting_eligible": False})
    context.pop("notes")
    context.pop("sha256")
    context["sha256"] = fp.geometry_fingerprint(context)

    result = fp.process_camera_fusion(context, _archive())

    assert result["notes"] == ["lighting: capture-time lighting was not analysis eligible"]


def test_the_setup_ball_rides_in_the_context_to_the_ball_gate(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fp,
        "estimate_camera_ball_flight",
        lambda *_a, **kwargs: seen.update(kwargs) or CameraBallEstimate(status="x"),
    )
    ball = {"x": 4.0, "y": 3.0, "diameter_px": 2.0}
    context = fp.build_context(**{**_context_kwargs(), "setup_ball": ball})
    fp.process_camera_fusion(context, _archive())
    assert context["setup_ball"] == ball
    assert seen["setup_ball"] == ball
