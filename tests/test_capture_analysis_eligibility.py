"""Capture-time analysis eligibility is judged on the setup's ball (P7-8).

Outdoors-test-7 (30 Sept, full sun): every clip was 19-23 % clipped over the
whole hitting-zone box, so the kiosk's zone rule (8 %) withheld the camera from
all 14, while the ladder calls a clipped background amber only. At the setup's
own 10 us lock the same zone rule said too dark, though the ball sat at 58-75 DN
above black. With the setup's ball position the clip is judged as the ladder
judges it: the ball's core clipped 5 % or less, and 20 DN or more above black.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from openflight.camera import study_ladder as sl
from openflight.camera.auto_exposure import measure_exposure
from openflight.camera.capture_runtime import (
    CameraCaptureRuntime,
    CameraCaptureSettings,
    parse_setup_ball,
)
from openflight.camera.triggered_buffer import CameraFrame, TriggeredCapture

SUN_FIXTURE = Path(__file__).parent / "fixtures" / "exposure" / "outdoors-test-7-sun.npz"
BLACK = 16.0


def _sun(kind):
    """The fixture's crop pasted back where it was, the rest of the frame flat."""
    with np.load(SUN_FIXTURE) as data:
        height, width = (int(v) for v in data["frame_shape"])
        crop = data[f"{kind}_crop"]
        x0, y0 = (int(v) for v in data[f"{kind}_origin_xy"])
        x, y, diameter = (float(v) for v in data["setup_ball_xyd"])
        fill = float(data["lock_zone_median_dn"]) if kind == "lock" else 128.0
    crop = crop if crop.ndim == 3 else np.repeat(crop[None], 10, axis=0)
    frames = np.full((len(crop), height, width), fill, np.uint8)
    frames[:, y0 : y0 + crop.shape[1], x0 : x0 + crop.shape[2]] = crop
    return frames, {"x": x, "y": y, "diameter_px": diameter}


def test_the_setups_own_lock_is_eligible_where_the_zone_rule_said_too_dark():
    frames, setup = _sun("lock")
    assert measure_exposure(frames[0]).acceptable is False  # the old rule

    judged = sl.capture_analysis_eligibility(frames, BLACK, setup)

    assert judged["eligible"] is True, judged["reason"]
    assert judged["rule"] == "setup_ball"
    assert judged["ball"]["found_by"] == "detector"
    assert judged["ball"]["signal_dn"] >= sl.BALL_MIN_SIGNAL_DN


def test_a_sunlit_clip_is_ineligible_because_the_ball_clips_not_the_background():
    frames, setup = _sun("clip")

    judged = sl.capture_analysis_eligibility(frames, BLACK, setup)

    assert judged["eligible"] is False
    assert judged["light_cause"] == "ball_clipped"
    assert "too bright for the ball" in judged["reason"]


def test_a_clipped_background_behind_a_well_exposed_ball_is_eligible():
    rng = np.random.default_rng(5)
    frames = np.clip(60 + rng.normal(0, 1.0, (10, 800, 1280)), 0, 255)
    frames[:, 360:480] = 255  # a sunlit strip beyond the mat: 33 % of the zone
    yy, xx = np.indices((800, 1280))
    frames[:, np.hypot(xx - 640, yy - 520) <= 10] = 180
    frames = frames.astype(np.uint8)
    assert measure_exposure(frames[0]).status == "too_bright"

    judged = sl.capture_analysis_eligibility(
        frames, BLACK, {"x": 640.0, "y": 520.0, "diameter_px": 20.0}
    )

    assert judged["eligible"] is True
    assert judged["light_cause"] == "background_clipped"


def test_no_ball_where_the_setup_saw_it_is_not_eligible():
    frames = np.full((10, 800, 1280), 90, np.uint8)

    judged = sl.capture_analysis_eligibility(
        frames, BLACK, {"x": 640.0, "y": 520.0, "diameter_px": 20.0}
    )

    assert judged["eligible"] is False
    assert "not found" in judged["reason"]


def test_the_setup_ball_argument_is_three_positive_numbers():
    assert parse_setup_ball("778.4,464.6,30.9") == {"x": 778.4, "y": 464.6, "diameter_px": 30.9}
    assert parse_setup_ball(None) is None
    for bad in ("1,2", "1,2,0", "a,b,c", "1,2,nan", "-1,2,3"):
        with pytest.raises(ValueError):
            parse_setup_ball(bad)


def _capture(frames):
    return TriggeredCapture(
        frames=tuple(
            CameraFrame(
                image=image,
                sensor_timestamp_ns=1_000_000_000 + index * 8_333_333,
                host_timestamp_ns=1_000_000_000 + index * 8_333_333 + 1_000,
                exposure_us=10,
                analogue_gain=1.0,
            )
            for index, image in enumerate(frames)
        ),
        pre_trigger_count=len(frames) - 2,
        trigger_host_timestamp_ns=1_000_000_000,
    )


def _zone_rule_payload(image):
    observation = measure_exposure(image)
    return {
        "status": "manual",
        "analysis_eligible": observation.acceptable,
        "message": observation.message,
        "observation": observation.to_dict(),
        "exposure_us": 10,
        "gain": 1.0,
        "controls_purpose": "capture",
    }


def test_a_manual_clip_is_judged_on_the_setup_ball_when_the_kiosk_has_one(tmp_path):
    frames, setup = _sun("lock")
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(
            width=1280, height=800, fps=120.0, auto_exposure=False, setup_ball=setup
        ),
    )

    saved = runtime._save_capture(  # pylint: disable=protected-access
        1, 123.0, _capture(frames), auto_exposure=_zone_rule_payload(frames[-1])
    )

    written = json.loads((saved.path / "metadata.json").read_text(encoding="utf-8"))
    assert saved.metadata["auto_exposure"]["analysis_eligible"] is True
    assert written["auto_exposure"]["analysis_eligible"] is True
    judged = written["auto_exposure"]["analysis_eligibility"]
    assert judged["rule"] == "setup_ball"
    assert judged["setup_ball"] == setup
    assert judged["zone_rule"] == {"analysis_eligible": False, "status": "too_dark"}


def test_without_a_setup_ball_the_zone_rule_stands(tmp_path):
    frames, _setup = _sun("lock")
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(width=1280, height=800, fps=120.0, auto_exposure=False),
    )

    saved = runtime._save_capture(  # pylint: disable=protected-access
        1, 123.0, _capture(frames), auto_exposure=_zone_rule_payload(frames[-1])
    )

    assert saved.metadata["auto_exposure"]["analysis_eligible"] is False
    assert "analysis_eligibility" not in saved.metadata["auto_exposure"]


def _run_kiosk_until_camera_init(monkeypatch, tmp_path, *extra):
    from openflight import server as server_module  # noqa: PLC0415

    received = {}

    class StopAfterCameraInit(Exception):
        pass

    def fake_init_camera_capture(**kwargs):
        received.update(kwargs)
        raise StopAfterCameraInit

    monkeypatch.setattr(server_module, "init_camera_capture", fake_init_camera_capture)
    monkeypatch.setattr(server_module, "init_session_logger", lambda **kwargs: None)
    monkeypatch.setattr(server_module, "profile_store", None)
    monkeypatch.setattr(
        "sys.argv",
        [
            "openflight-server",
            "--camera-capture",
            "--camera-capture-manual-exposure",
            "--rig-geometry",
            "config/enclosure_v3_rig_geometry.json",
            "--study-mode",
            "--no-logging",
            "--profiles-path",
            str(tmp_path / "profiles.json"),
            *extra,
        ],
    )
    with pytest.raises((StopAfterCameraInit, SystemExit)) as raised:
        server_module.main()
    return received, raised.value


def test_the_kiosk_hands_the_setup_ball_to_the_camera(monkeypatch, tmp_path):
    received, _stop = _run_kiosk_until_camera_init(
        monkeypatch, tmp_path, "--camera-setup-ball", "778.4,464.6,30.9"
    )

    assert received["setup_ball"] == {"x": 778.4, "y": 464.6, "diameter_px": 30.9}


def test_a_kiosk_without_a_setup_ball_keeps_the_zone_rule(monkeypatch, tmp_path):
    received, _stop = _run_kiosk_until_camera_init(monkeypatch, tmp_path)

    assert received["setup_ball"] is None


def test_a_malformed_setup_ball_stops_the_kiosk(monkeypatch, tmp_path):
    received, stop = _run_kiosk_until_camera_init(
        monkeypatch, tmp_path, "--camera-setup-ball", "778,464"
    )

    assert isinstance(stop, SystemExit) and stop.code == 2
    assert received == {}
