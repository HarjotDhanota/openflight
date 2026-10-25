"""Capture-time analysis eligibility on a real sunlit setup (fork only: it loads
field frames). The setup's own lock passes where the zone rule called it too dark;
the sunlit clip fails because the ball clips, not the background.
"""

import json
from pathlib import Path

import numpy as np

from openflight.camera import light_judging as lj
from openflight.camera.auto_exposure import measure_exposure
from openflight.camera.capture_runtime import CameraCaptureRuntime, CameraCaptureSettings
from openflight.camera.triggered_buffer import CameraFrame, TriggeredCapture
from tests.test_capture_analysis_eligibility import BLACK

SUN_FIXTURE = Path(__file__).parent / "fixtures" / "exposure" / "outdoors-test-7-sun.npz"


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
    assert measure_exposure(frames[0]).acceptable is False  # the zone rule

    judged = lj.capture_analysis_eligibility(frames, BLACK, setup)

    assert judged["eligible"] is True, judged["reason"]
    assert judged["rule"] == "setup_ball"
    assert judged["ball"]["found_by"] == "detector"
    assert judged["ball"]["signal_dn"] >= lj.BALL_MIN_SIGNAL_DN


def test_a_sunlit_clip_is_ineligible_because_the_ball_clips_not_the_background():
    frames, setup = _sun("clip")

    judged = lj.capture_analysis_eligibility(frames, BLACK, setup)

    assert judged["eligible"] is False
    assert judged["light_cause"] == "ball_clipped"
    assert "too bright for the ball" in judged["reason"]


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
    assert judged["zone_rule"] == {
        "analysis_eligible": False,
        "status": "too_dark",
        "zone_source": "fixed",
    }


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
