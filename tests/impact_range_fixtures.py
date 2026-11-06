"""Explicit synthetic radar/camera association for known-answer tests only."""

import hashlib
from types import SimpleNamespace

from openflight.camera.impact_range_evidence import bind_camera, from_iwr_result
from openflight.iwr6843.launch_range import LaunchRangeEstimate, shot_ball_range
from openflight.iwr6843.monitor import IWR6843Capture


def range_result(range_m=2.0, uncertainty_m=0.03):
    launch = LaunchRangeEstimate(
        status="measured",
        reason=None,
        method="ball_track_start",
        range_m=range_m,
        uncertainty_m=uncertainty_m,
        impact_t_s=0.01,
        ball_track={"t_first_s": 0.002, "t_last_s": 0.025, "n_inliers": 24},
    )
    capture = IWR6843Capture(1, 100.001, 101.0, 0.999, b"synthetic-iwr", None)
    return SimpleNamespace(
        capture=capture, launch_range=launch, ball_range=shot_ball_range(launch, None)
    )


def bind_test_range(shot, raw_path, session_uuid):
    if shot["ball_range_source"] != "iwr_post_impact":
        return shot
    shot.update(impact_timestamp=100.0, ball_range_label="measured")
    result = range_result(shot["ball_range_m"], shot["ball_range_uncertainty_m"])
    subject = SimpleNamespace(
        camera_fusion_session_uuid=session_uuid,
        shot_number=shot["shot_number"],
        impact_timestamp=100.0,
    )
    evidence = from_iwr_result(subject, result, match_tolerance_s=0.75)
    shot["impact_range_evidence"] = bind_camera(
        evidence,
        hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        session_uuid=session_uuid,
        shot_number=shot["shot_number"],
    )
    return shot
