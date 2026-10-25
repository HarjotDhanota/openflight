"""The camera fallback on real frames (fork only: it loads field fixtures).

Harjot-outdoor-test-1 (24 Oct 2026, dusk, OV9281 + 6 mm lens, 1280x800 at 120 fps,
7-iron), where the IWR lost 32 of 33 shots and the setup saved no ball range:

* shot 28: a clean six-point ball track. The values are pinned as the kiosk's chain
  gives them today; they are not truth (no TrackMan on this session).
* shot 14: the one shot the IWR ranged, 1.908 +- 0.115 m. The resting ball's own size at
  the lens's listed focal length must agree within the two uncertainties.
"""

from pathlib import Path

import numpy as np
import pytest

from openflight.camera import ball_launch_fallback as fb, resting_ball_range as rbr
from tests.test_reference_ball_expected_size_field import shot_14_frames

FIXTURE = (
    Path(__file__).parent / "fixtures" / "camera_fallback" / "outdoor-test-1-shot-28-launch.npz"
)
CAMERA_ORIGIN = (0.0018575, 0.031383845739872185, 0.095)
RADAR_ORIGIN = np.array([0.0, 0.0, 0.05224817918569254])
OPS_LFU = (CAMERA_ORIGIN[0] - 0.085, CAMERA_ORIGIN[1] - 0.020, CAMERA_ORIGIN[2] - 0.047)
SIX_MM_FOCAL_PX = 2000.0001
BALL_HEIGHT_M = 0.02135
SHOT_14_IWR_RANGE = (1.908, 0.115)


def shot_28():
    data = np.load(FIXTURE)
    frames = data["frames"]
    x0, y0 = (int(value) for value in data["origin_px"])
    width, height = (int(value) for value in data["image_size_px"])
    pad = (
        (0, 0),
        (y0, height - y0 - frames.shape[1]),
        (x0, width - x0 - frames.shape[2]),
    )
    return (
        np.pad(frames, pad, mode="edge"),
        data["sensor_timestamp_ns"],
        data["exposure_us"],
        int(data["first_post_index"]),
    )


def shot_28_geometry() -> fb.FallbackGeometry:
    """The kiosk's inputs for shot 28: its resting ball's size range and swing roll."""
    return fb.FallbackGeometry(
        camera=fb.PinholeCamera(
            focal_px=SIX_MM_FOCAL_PX,
            width=1280,
            height=800,
            pitch_deg=0.0,
            roll_correction_deg=-0.6096246350458501,
            origin_lfu=CAMERA_ORIGIN,
        ),
        rest_px=(782.4398039013633, 421.50342885529295),
        rest_diameter_px=45.29346013178725,
        ball_distance_m=1.8854820104606165,
        ball_distance_sd_m=0.04258673156098796,
        distance_source="camera_ball_size",
        focal_rel_sd=0.08,
        pitch_source="resting_ball",
        pitch_sd_deg=1.6167689521111144,
        ball_height_m=BALL_HEIGHT_M,
        ops_lfu=OPS_LFU,
        horizontal_offset_deg=-2.1951,
        horizontal_offset_sd_deg=2.6412,
    )


def test_shot_28_s_ball_is_tracked_in_every_post_impact_frame_and_fitted():
    frames, stamps, exposure, first_post = shot_28()

    result = fb.estimate_camera_ball_launch(
        frames,
        stamps,
        exposure_us=exposure,
        first_post_index=first_post,
        geometry=shot_28_geometry(),
        ops_radial_mph=87.56997514160157,
    )

    assert result.status == "accepted", result.reason
    assert result.n_points == 6
    assert result.residual_px < 1.5
    # pinned (today's values, not truth)
    assert result.vertical_deg == pytest.approx(16.98, abs=0.05)
    assert result.horizontal_deg == pytest.approx(0.92, abs=0.05)
    assert result.horizontal_frame == "target_line"
    # the uncertainty is dominated by the inferred pitch and the aim
    assert 1.5 < result.vertical_sd_deg < 2.5
    assert 2.6 < result.horizontal_sd_deg < 3.5
    # the camera's own speed runs low against the OPS on fast shots (5-12 %) and is
    # only a check: consistent within its band, never the ball speed
    assert result.speed_check["status"] == "consistent"
    assert result.camera_speed_mph < result.speed_mph


def test_shot_14_s_resting_ball_size_agrees_with_its_iwr_launch_point():
    frames = shot_14_frames()

    record = rbr.resting_ball_range(
        frames,
        first_post_index=19,
        seed=(793.3, 418.2, 38.43),
        lens_focal_px=SIX_MM_FOCAL_PX,
        calibrated_focal_px=None,
        camera_origin_lfu=np.asarray(CAMERA_ORIGIN),
        radar_origin_lfu=RADAR_ORIGIN,
        ball_height_m=BALL_HEIGHT_M,
        image_width_px=1280,
        image_height_px=800,
        roll_correction_deg=-0.355,
    )

    assert record["status"] == "measured_from_size"
    assert record["diameter_px"] == pytest.approx(42.2, abs=1.0)
    iwr, iwr_sd = SHOT_14_IWR_RANGE
    gap = abs(record["slant_range_m"] - iwr)
    assert gap <= 2.0 * float(np.hypot(record["uncertainty_m"], iwr_sd))
    # the listed focal length reads it long: the nominal 2000 px is likely high for
    # this unit (a calibrated focal length would close the gap)
    assert record["slant_range_m"] > iwr
