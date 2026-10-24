"""The camera never gives the ball's distance (D17, rule 3).

The ball's distance is the IWR's. Without one the camera measures nothing that needs
depth: no ball-size range stands in, in setup or in a shot.
"""

import numpy as np

from openflight.camera import ball_flight
from openflight.camera.ball_flight import CameraBallGeometry, estimate_camera_ball_flight
from openflight.camera.club_motion import ReferenceBall


def test_ball_flight_has_no_ball_size_depth():
    assert not hasattr(ball_flight, "_project_from_ball_size")


def test_a_shot_without_an_iwr_range_measures_no_flight(monkeypatch):
    ball = ReferenceBall(160.0, 100.0, 14.0, 154)
    monkeypatch.setattr(
        ball_flight, "_select_reference_ball", lambda *_a, **_k: (ball, {"selected_source": "x"})
    )

    estimate = estimate_camera_ball_flight(
        np.zeros((30, 200, 320), dtype=np.uint8),
        np.arange(30, dtype=np.int64) * 3_500_000,
        trigger_ns=10 * 3_500_000,
        range_evidence=None,
        geometry=CameraBallGeometry(
            camera_height_m=0.095,
            radar_height_m=0.051,
            tee_range_m=1.5,
            ball_height_m=0.02135,
            image_width_px=320,
            image_height_px=200,
        ),
        ops_ball_speed_mph=100.0,
    )

    assert estimate.status == "rejected_no_iwr_range"
    assert estimate.depth_source is None
    assert estimate.reference_ball_diagnostics == {"selected_source": "x"}


def test_a_setup_saved_before_d17_with_a_camera_size_range_hands_swings_nothing():
    from openflight import tee_range
    from openflight.camera import tester_server as ts

    legacy = tee_range.TeeRangeCandidate(
        candidate_id="patch-ball-epoch-1",
        source="patch_ball_camera_size_range",
        source_group="camera",
        radar_slant_range_m=1.3,
        uncertainty_m=0.28,
        selectable=False,
        evidence={
            "patch_ball": {
                "schema": "openflight.tester_patch_ball.v1",
                "status": "camera_only",
                "source": "camera_size_range",
                "range_m": 1.3,
                "uncertainty_m": 0.28,
            }
        },
    )
    solution = tee_range.TeeRangeSolution.unresolved([legacy], reason=ts.PATCH_BALL_SAVED_REASON)

    candidate, decision = ts.experimental_tee_range_choice(solution)

    assert candidate is None
    assert "ball-size" in decision["withheld"]
    assert ts.tee_range_handoff(solution)[1]["tee_range_status"] == "pending"


def test_the_pipeline_check_names_the_missing_iwr_range_as_the_cause():
    from openflight import pipeline_check as check

    upstream = {
        sid: check.stage(sid, check.PASS)
        for sid in ("lighting", "geometry", "context", "camera_ball", "camera_club", "tee_range")
    }
    upstream["lcmf"] = check.stage("lcmf", check.FAIL, cause="DATA")
    result = {"ball_estimate": {"status": "rejected_no_iwr_range"}}

    row = check.camera_ball(result, upstream, setup_ball=None)

    assert row["status"] == check.NOT_REACHED
    assert row["blocked_by"] == "lcmf"
