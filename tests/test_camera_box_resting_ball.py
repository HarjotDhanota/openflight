"""One resting ball per shot (C7b, 2 Nov): the ball the setup box finds in the clip's own
pre-impact frames (``swing_tilt.resting_ball_px``, the surface solve's and the swing-tilt
pair's observation) is the camera ball estimate's reference ball; the scene-wide detector
looks only when the box holds no ball, and the record names which found it.

Oct 27 shot 1: the box ball is at (283.2, 202.8), 24.1 px; the scene detector picked a
29 px object at (350.6, 232.8) and the estimate stopped at reference_ball_not_found.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server
from openflight.camera import ball_flight, fusion_processing as fp
from openflight.camera.club_motion import ReferenceBall
from openflight.launch_monitor import Shot
from openflight.rig_geometry import geometry_fingerprint

BOX_BALL = {"x_px": 283.2, "y_px": 202.8, "diameter_px": 24.1, "frames_used": 10}
SETUP_BALL = {"x": 283.07, "y": 203.0, "diameter_px": 24.13}
WRONG = ReferenceBall(350.6, 232.8, 29.0, 660)


def _geometry():
    return SimpleNamespace(image_width_px=640, image_height_px=400, calibrated_model=None)


def _frames():
    return np.full((24, 400, 640), 60, dtype=np.uint8)


def _scene_detector_finds(monkeypatch, found, calls):
    def scene(*_a, **_k):
        calls.append("scene")
        return found

    def impact(*_a, **_k):
        raise ValueError("no persistent tee-ball departure found")

    monkeypatch.setattr(ball_flight, "detect_reference_ball", scene)
    monkeypatch.setattr(ball_flight, "detect_impact_reference_ball", impact)
    monkeypatch.setattr(ball_flight, "measure_resting_ball", lambda _f, found, *_a, **_k: found)


def test_the_box_ball_is_the_reference_ball_and_the_scene_detector_never_looks(monkeypatch):
    calls = []
    _scene_detector_finds(monkeypatch, WRONG, calls)

    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        _frames(), 18, _geometry(), setup_ball=SETUP_BALL, box_resting_ball=BOX_BALL
    )

    assert (selected.x, selected.y, selected.diameter_px) == (283.2, 202.8, 24.1)
    assert diagnostics["reference_source"] == "resting_ball_in_box"
    assert diagnostics["selected_source"] == "resting_ball_in_box"
    assert diagnostics["box_resting_ball"] == BOX_BALL
    assert calls == []


def test_an_empty_box_leaves_the_scene_detector_to_find_it_labelled(monkeypatch):
    calls = []
    found = ReferenceBall(284.0, 203.0, 24.0, 452)
    _scene_detector_finds(monkeypatch, found, calls)

    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        _frames(), 18, _geometry(), setup_ball=SETUP_BALL, box_resting_ball=None
    )

    assert calls == ["scene"]
    assert selected is not None
    assert diagnostics["reference_source"] == "scene_detector"


def test_the_fusion_context_carries_the_box_ball_to_the_estimate(monkeypatch):
    from tests.camera_fallback_scene import render_scene  # noqa: PLC0415
    from tests.test_fallback_chain import _scene_context  # noqa: PLC0415

    context, archive = _scene_context(render_scene())
    context = {key: value for key, value in context.items() if key != "sha256"}
    context["box_resting_ball"] = dict(BOX_BALL)
    context["sha256"] = geometry_fingerprint(context)
    seen = {}
    real = fp.estimate_camera_ball_flight

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(fp, "estimate_camera_ball_flight", spy)
    fp.process_camera_fusion(context, archive)

    assert seen["box_resting_ball"] == BOX_BALL


def test_build_context_records_the_box_ball_only_when_there_is_one():
    from tests.camera_fallback_scene import render_scene  # noqa: PLC0415
    from tests.test_fallback_chain import _scene_context  # noqa: PLC0415

    context, _archive = _scene_context(render_scene())
    assert "box_resting_ball" not in context


def test_the_kiosk_hands_the_fusion_the_box_ball_of_the_clip(monkeypatch):
    seen = {}

    def stop(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop after the context")

    monkeypatch.setattr(fp, "build_context", stop)
    monkeypatch.setattr(server, "camera_calibration_set", None)
    monkeypatch.setattr(
        server, "camera_capture_runtime", SimpleNamespace(camera_analysis_eligible=True)
    )
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(
            calibration=SimpleNamespace(
                tee_range_m=1.81, radar_height_m=0.05, tee_ball_height_m=0.021
            )
        ),
    )
    monkeypatch.setattr(
        server, "camera_capture_config", {"mount_height_m": 0.093, "width": 640, "height": 400}
    )
    archive = {
        "frames": np.zeros((4, 400, 640), np.uint8),
        "host_timestamp_ns": np.arange(4, dtype=np.int64),
        "trigger_host_timestamp_ns": np.int64(2),
        "pre_trigger_count": np.int32(2),
        "_capture_npz_sha256": "hash",
    }
    monkeypatch.setattr(server, "_load_camera_capture_archive", lambda _capture: archive)
    monkeypatch.setattr(server, "_shot_resting_ball", lambda _archive: dict(BOX_BALL))
    monkeypatch.setattr(server, "_fuse_camera_ball_flight", lambda *_a: None)
    monkeypatch.setattr(server, "_fuse_camera_club_delivery", lambda *_a: None)
    shot = Shot(ball_speed_mph=82.0, timestamp=datetime.now(), shot_number=1)
    shot.camera_fusion_session_uuid = "session"

    server._fuse_camera_measurements(shot, SimpleNamespace(valid=True, metadata={}))

    assert seen["box_resting_ball"] == BOX_BALL
    assert pytest.approx(BOX_BALL["x_px"]) == seen["box_resting_ball"]["x_px"]
