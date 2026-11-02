"""The camera locates the tee (C8a, 3 Nov 2026): the shot's ball range is the setup box's
resting ball through the mode's camera model (its angular size gives the distance from
the lens, the rig origins turn it into the IWR phase centre's slant range); the IWR's
launch point only confirms it. TrackMan-shaped: position from the camera, speed from
the OPS, the radar walk a cross-check.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server
from openflight.camera import fallback_chain, resting_ball_range as rbr
from openflight.camera.calibrated_projection import build_calibrated_camera_model
from openflight.camera.reference_ball_range import BallPlaneCamera, project_to_pixel
from openflight.constants import BALL_DIAMETER_M
from openflight.launch_monitor import Shot

SET_DIR = Path(__file__).resolve().parents[1] / "config" / "camera-calibration" / "6mm"
LENS = np.array([0.0, 0.0, 0.093])
RADAR = LENS + np.array([-0.0019, -0.0237, -0.0428])
RADIUS = BALL_DIAMETER_M / 2.0


def _camera(pitch=-0.054, roll=-0.4):
    optical = json.loads((SET_DIR / "640x400.optical.json").read_text(encoding="utf-8"))
    placement = json.loads((SET_DIR / "640x400.placement.json").read_text(encoding="utf-8"))
    model = build_calibrated_camera_model(
        optical, placement, observed_pitch_deg=pitch, observed_roll_deg=roll
    )
    return replace(
        BallPlaneCamera.calibrated(model),
        camera_origin_lfu=tuple(LENS),
        radar_origin_lfu=tuple(RADAR),
    )


def _seen(camera, ball):
    """The ball's centre pixel and its pixel diameter across the image row."""
    centre = np.asarray(project_to_pixel(camera, ball))
    sight = (ball - LENS) / np.linalg.norm(ball - LENS)
    across = np.cross(sight, [0.0, 0.0, 1.0])
    across /= np.linalg.norm(across)
    # the edges seen tangentially: offset by the radius along the perpendicular
    distance = float(np.linalg.norm(ball - LENS))
    half = math.asin(RADIUS / distance)
    edge = [
        np.asarray(
            project_to_pixel(
                camera, LENS + distance * (math.cos(half) * sight + s * math.sin(half) * across)
            )
        )
        for s in (-1.0, 1.0)
    ]
    return centre, float(np.linalg.norm(edge[1] - edge[0]))


def test_the_resting_ball_s_size_through_the_calibrated_model_gives_its_radar_range():
    camera = _camera()
    ball = np.array([0.05, 1.78, 0.0254 + RADIUS])
    centre, diameter = _seen(camera, ball)

    record = rbr.located_tee_range(
        camera, centre, diameter, focal_rel_sigma=0.01, diameter_sigma_px=0.3
    )

    assert record["status"] == "measured_from_size"
    assert record["source"] == "camera_resting_ball"
    assert record["lens_distance_m"] == pytest.approx(np.linalg.norm(ball - LENS), rel=0.003)
    assert record["range_m"] == pytest.approx(np.linalg.norm(ball - RADAR), rel=0.003)
    expected_sigma = math.hypot(0.01, 0.3 / diameter) * record["lens_distance_m"]
    assert record["uncertainty_m"] == pytest.approx(expected_sigma, rel=1e-6)
    assert record["camera_source"].startswith("calibrated")
    assert record["surface_crosscheck"] is None


def test_a_surface_in_force_adds_the_ray_meets_surface_range_as_a_cross_check():
    camera = _camera()
    ball = np.array([0.05, 1.78, 0.0254 + RADIUS])
    centre, diameter = _seen(camera, ball)

    record = rbr.located_tee_range(
        camera,
        centre,
        diameter,
        focal_rel_sigma=0.01,
        diameter_sigma_px=0.3,
        surface_ball_height_m=0.0254 + RADIUS,
    )

    check = record["surface_crosscheck"]
    assert check["range_m"] == pytest.approx(np.linalg.norm(ball - RADAR), abs=0.002)
    assert check["delta_m"] == pytest.approx(record["range_m"] - check["range_m"])
    # the value stays the size range: the surface solve consumes it (no circle)
    assert record["range_m"] != check["range_m"]


CAMERA = {
    "status": "measured_from_size",
    "source": "camera_resting_ball",
    "range_m": 1.81,
    "uncertainty_m": 0.03,
    "camera_source": "calibrated_candidate_unqualified",
}


def _resolve(iwr_launch, camera=CAMERA, shot_range=None):
    return fallback_chain.ball_range_resolution(
        shot_range=shot_range or {},
        camera_located=camera,
        iwr_launch=iwr_launch,
    )


@pytest.mark.parametrize(
    ("iwr_launch", "status", "label"),
    [
        (
            {"label": "measured", "range_m": 1.84, "uncertainty_m": 0.03},
            "iwr_confirmed",
            "measured",
        ),
        (
            {"label": "measured", "range_m": 2.12, "uncertainty_m": 0.03},
            "iwr_disagrees",
            "estimated",
        ),
        (
            {"label": "estimated", "range_m": 2.12, "uncertainty_m": 0.34},
            "iwr_not_measured",
            "estimated",
        ),
        (
            {"label": "not_measured", "range_m": None, "reason": "club walk only"},
            "iwr_not_measured",
            "estimated",
        ),
        (None, "iwr_not_measured", "estimated"),
    ],
)
def test_the_camera_range_stands_and_the_iwr_only_confirms(iwr_launch, status, label):
    resolution = _resolve(iwr_launch)

    assert resolution.route == "camera_resting_ball"
    assert resolution.value == pytest.approx(1.81)
    assert resolution.uncertainty == pytest.approx(0.03)
    assert resolution.label == label
    check = resolution.details["iwr_cross_check"]
    assert check["status"] == status
    if status == "iwr_disagrees":
        assert "iwr_disagrees (0.31 m)" in resolution.note


def test_a_nominal_camera_s_located_tee_is_experimental():
    nominal = {**CAMERA, "camera_source": "nominal_uncalibrated", "uncertainty_m": 0.15}
    resolution = _resolve({"label": "measured", "range_m": 1.84, "uncertainty_m": 0.03}, nominal)

    assert resolution.route == "camera_resting_ball"
    assert resolution.label == "experimental"


def test_without_a_located_tee_the_iwr_launch_point_still_stands():
    resolution = _resolve(
        {"label": "measured", "range_m": 1.84, "uncertainty_m": 0.03},
        camera={"status": "not_measured", "reason": "no resting ball in the setup box"},
        shot_range={
            "ball_range_m": 1.84,
            "ball_range_source": "iwr_post_impact",
            "ball_range_uncertainty_m": 0.03,
            "ball_range_label": "measured",
        },
    )

    assert resolution.route == "iwr_post_impact"
    assert ("camera_resting_ball", "no resting ball in the setup box") in resolution.tried


def test_the_kiosk_puts_the_located_tee_on_the_shot_after_the_iwr(monkeypatch):
    shot = Shot(ball_speed_mph=82.0, timestamp=datetime.now(), shot_number=1)
    shot.input_sources = {"camera_launch_range": dict(CAMERA)}
    launch = SimpleNamespace(
        to_dict=lambda: {
            "status": "not_measured",
            "label": "not_measured",
            "range_m": None,
            "reason": "ball walk not found (hidden by clutter); club walk only",
        }
    )
    result = SimpleNamespace(launch_range=launch, ball_range=None, capture=None)
    monkeypatch.setattr(server, "iwr6843_runtime", None)

    server._attach_ball_range(shot, result)  # pylint: disable=protected-access

    assert shot.ball_range_source == "camera_resting_ball"
    assert shot.ball_range_m == pytest.approx(1.81)
    assert shot.ball_range_label == "estimated"
    assert "club walk only" in shot.ball_range_reason
    record = shot.input_sources["ball_range"]
    assert record["details"]["iwr_cross_check"]["status"] == "iwr_not_measured"


def test_the_located_tee_is_the_iwr_s_per_shot_prior(monkeypatch):
    shot = Shot(ball_speed_mph=82.0, timestamp=datetime.now(), shot_number=1)
    shot.input_sources = {"camera_launch_range": dict(CAMERA)}

    prior = server._shot_look_prior(shot)  # pylint: disable=protected-access

    assert prior.source == "camera_resting_ball"
    assert (prior.range_m, prior.uncertainty_m) == (1.81, 0.03)


# --- what the consumers of shot.ball_range_* read from a located tee -----------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [("measured", None), ("estimated", "estimated"), ("experimental", "experimental")],
)
def test_every_range_consumer_takes_the_located_tee_s_own_label(label, expected):
    from openflight import source_labels  # noqa: PLC0415

    shot = {"ball_range_source": "camera_resting_ball", "ball_range_label": label}
    record_label, note = source_labels.range_source_label(shot)

    assert record_label == expected
    if expected is not None:
        assert "tee the camera located" in note
        # the IWR may not have measured the shot: the fixed text never says it confirmed
        assert "confirms" not in note


def test_the_published_ball_range_names_the_located_tee():
    from openflight import shot_metrics  # noqa: PLC0415

    record = shot_metrics._ball_range(  # pylint: disable=protected-access
        {
            "ball_range_source": "camera_resting_ball",
            "ball_range_m": 1.81,
            "ball_range_uncertainty_m": 0.03,
            "ball_range_label": "estimated",
            "ball_range_reason": "the camera located the tee; iwr_not_measured (club walk only)",
        }
    )

    assert "camera" in record["source"] and "tee" in record["source"]
    assert record["value"] == pytest.approx(1.81)
    # the IWR did not measure this shot: the fixed source text never says it confirmed
    assert "confirms" not in record["source"]


def test_a_look_s_rejection_never_takes_the_located_tee_from_the_iwr(monkeypatch):
    """The address look's refusal (its seed is not the clip's ball) is about the look; the
    located tee is the clip's own box ball, so it stays the IWR's prior."""
    seen = {}

    def process_shot(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            capture=None, measurement=None, club_path=None, launch_range=None, ball_range=None
        )

    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(process_shot=process_shot, replay_config_snapshot=None, range_prior=None),
    )
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    shot = Shot(ball_speed_mph=82.0, timestamp=datetime.now(), shot_number=1, impact_timestamp=1.0)
    shot.input_sources = {
        "camera_launch_range": dict(CAMERA),
        "ball_reference": {"range_prior_rejection": "the look's ball is not the clip's"},
    }

    server._process_iwr6843_angle(shot)  # pylint: disable=protected-access

    assert seen["range_prior"].source == "camera_resting_ball"
    assert "range_prior_rejection" not in seen


def test_the_camera_fallback_takes_the_located_tee_s_ball_and_distance():
    from openflight.camera import fusion_processing as fp  # noqa: PLC0415
    from tests.camera_fallback_scene import render_scene  # noqa: PLC0415
    from tests.test_fallback_chain import _scene_context  # noqa: PLC0415

    scene = render_scene(vertical_deg=17.0, horizontal_deg=-2.0, speed_mph=85.0, pitch_deg=-0.8)
    x, y, diameter = scene.rest_px()
    distance = float(np.linalg.norm(scene.ball_lfu - np.asarray(scene.camera.origin_lfu)))
    located = {
        **CAMERA,
        "ball_px": [x, y],
        "diameter_px": diameter,
        "lens_distance_m": distance,
        "uncertainty_m": 0.02,
    }
    context, archive = _scene_context(
        scene,
        range_record={
            "source": "camera_resting_ball",
            "label": "estimated",
            "uncertainty_m": 0.02,
            "details": located,
        },
    )

    fallback = fp.process_camera_fusion(context, archive)["ball_fallback"]

    assert fallback["status"] == "accepted", fallback["reason"]
    assert fallback["inputs"]["distance_source"] == "camera_resting_ball"
    assert fallback["vertical_deg"] == pytest.approx(17.0, abs=0.6)
