"""Optional worker evidence never promotes pose, strike or ball readiness."""

import argparse
import hashlib
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from openflight.camera import club_diagnostics as diagnostic, impact_live


def clip():
    frames = np.full((12, 120, 160), 80, np.uint8)
    for index in range(8, 12):
        x = 65 + index
        cv2.line(frames[index], (20, 5), (x, 80), 150, 3)
        cv2.ellipse(frames[index], (x + 15, 84), (18, 12), 0, 0, 360, 100, -1)
    prepared = SimpleNamespace(
        frames=frames,
        window=(8, 9, 10, 11),
        ball=SimpleNamespace(x=90.0, y=100.0, diameter_px=15.0),
        roi_origin=(40, 60),
        full_size=(240, 200),
        flipped=False,
        masks=SimpleNamespace(ball_vetoes={}),
    )
    return SimpleNamespace(
        prepared=prepared,
        extra={
            "capture_npz_sha256": "a" * 64,
            "capture_frame_timestamps_ns": tuple(1_000_000_000 + i * 4_000_000 for i in range(12)),
            "capture_frame_exposure_us": (298.0,) * 12,
            "template_capture_context": "recorded_context",
        },
    )


def test_optional_diagnostics_keep_current_pixel_and_crop_identity():
    current = clip()
    before = current.prepared.frames.copy()
    result = diagnostic.analyse_clip(current)
    assert result["status"] == "current_image_hypotheses_only" and len(result["frames"]) == 4
    assert result["future_assisted_frame_selection"] and not result["affects_live_readiness"]
    assert result["physical_pose"] is result["strike"] is None
    assert any(frame["parts"] is not None for frame in result["frames"])
    for frame in result["frames"]:
        identity = frame["identity"]
        assert (
            identity["frame_sha256"]
            == hashlib.sha256(before[identity["frame_index"]].tobytes()).hexdigest()
        )
        assert frame["coordinate_mapping"]["roi_origin_px"] == (40, 60)
        assert frame["association"]["physical_pose"] is frame["association"]["strike"] is None
    assert np.array_equal(current.prepared.frames, before)


def test_missing_frame_timing_has_named_refusal_without_inventing_it():
    current = clip()
    current.extra.pop("capture_frame_exposure_us")
    result = diagnostic.analyse_clip(current)
    assert result["status"] == "withheld_missing_per_frame_sensor_time_or_exposure"
    assert result["frames"] == []


def test_uncertain_ball_veto_retains_an_unexcluded_club_acquisition_branch():
    current = clip()
    current.prepared.masks.ball_vetoes = {index: (56, 42, 15) for index in range(8, 12)}
    before = current.prepared.frames.copy()
    result = diagnostic.analyse_clip(current)
    for row in result["frames"]:
        assert row["ball_identity_branch"] == "ball_excluded"
        assert len(row["alternatives"]) == 1
        alternative = row["alternatives"][0]
        assert alternative["ball_identity_branch"] == "ball_identity_uncertain"
        assert alternative["ball_veto_used"] is None
        assert alternative["association"]["physical_pose"] is None
    assert np.array_equal(current.prepared.frames, before)


def test_no_prepared_window_keeps_legacy_refusal_and_has_no_prediction():
    assert diagnostic.analyse_clip(SimpleNamespace(prepared=None))["frames"] == []


def test_worker_diagnostics_are_off_by_default_and_require_boolean():
    assert impact_live.LiveAnalyser().club_part_diagnostics is False
    with pytest.raises(ValueError):
        impact_live.LiveAnalyser(club_part_diagnostics="true")


def test_optional_observer_failure_preserves_legacy_refusal(monkeypatch):
    current = SimpleNamespace(
        club="7-iron",
        handedness="right",
        frame_size="640x400",
        extra={},
        range_source="test",
        error=None,
        prepared=SimpleNamespace(
            contact=SimpleNamespace(status="bad", reason="legacy_contact_refusal")
        ),
    )
    calls = []

    def broken(_):
        calls.append(True)
        raise RuntimeError("test observer failure")

    monkeypatch.setattr(diagnostic, "analyse_clip", broken)
    off = impact_live.LiveAnalyser()
    off._prepare = lambda _: current
    baseline = off.analyse({"session_uuid": "test"})
    assert calls == [] and "club_part_diagnostics" not in baseline
    on = impact_live.LiveAnalyser(club_part_diagnostics=True)
    on._prepare = lambda _: current
    observed = on.analyse({"session_uuid": "test"})
    assert observed["reason_code"] == baseline["reason_code"] == "legacy_contact_refusal"
    assert observed["club_part_diagnostics"]["reason"] == "diagnostic_failed_RuntimeError"


def test_diagnostic_switch_is_explicit_and_default_off():
    extension = impact_live.ImpactLiveExtension(runner_factory=lambda _: None)
    parser = argparse.ArgumentParser()
    extension.add_arguments(parser)
    extension.configure(parser.parse_args([]), parser)
    assert not extension.club_part_diagnostics
    extension.configure(parser.parse_args(["--impact-club-part-diagnostics"]), parser)
    assert extension.club_part_diagnostics


def test_opted_in_analysis_preserves_synthetic_legacy_metrics(tmp_path):
    from test_impact_live import Session

    job = Session(tmp_path).job()
    off = impact_live.LiveAnalyser(swings_needed=1).analyse(job)
    on = impact_live.LiveAnalyser(swings_needed=1, club_part_diagnostics=True).analyse(job)
    for key in (
        "status",
        "impact_offset_mm",
        "impact_offset_sigma_mm",
        "height_status",
        "impact_height_mm",
        "contact_frame",
    ):
        assert off[key] == on[key]
    extra = on["club_part_diagnostics"]
    assert extra["status"] == "current_image_hypotheses_only" and extra["frames"]
    assert extra["physical_pose"] is extra["strike"] is None


def test_missing_metric_range_does_not_prevent_image_only_club_observations(tmp_path):
    from test_impact_live import Session

    job = Session(tmp_path).job(range_source="camera_prior")
    analyser = impact_live.LiveAnalyser(club_part_diagnostics=True)
    observed = analyser.analyse(job)
    baseline = impact_live.LiveAnalyser().analyse(job)
    assert observed["reason_code"] == baseline["reason_code"] == "no_ball_range_for_this_shot"
    extra = observed["club_part_diagnostics"]
    assert extra["frames"] and extra["status"] == "current_image_hypotheses_only"
    assert extra["window_selection"]["source"] == "image_departure_only"
    assert extra["window_selection"]["first_touch_interval_ns"] is None
    assert extra["physical_pose"] is extra["strike"] is None
    assert observed["template"]["swings"] == 0


def test_image_only_selection_does_not_modify_the_metric_preparation(tmp_path):
    from test_impact_live import Session

    job = Session(tmp_path).job(range_source="camera_prior")
    current = impact_live.LiveAnalyser()._prepare(job)
    contact, window = current.prepared.contact, current.prepared.window
    assert not window and contact.status == "rejected"
    result = diagnostic.analyse_clip(current)
    assert result["frames"]
    assert current.prepared.contact is contact and current.prepared.window is window
