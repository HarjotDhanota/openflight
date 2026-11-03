"""Full club speed from the club stage's measured head direction at contact (C11a).

The OPS reads the club's speed along its line of sight. With the camera club stage's head
position and direction at the ball-defined contact (C10), full speed = radial / (LOS . dir),
LOS from the OPS (rig offsets on the lens) to the head; the range is the root-sum-square of
one-at-a-time changes of the path and attack (their own 1 sigma), the head's depth and the
OPS position; the label is the weakest input's.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight import full_speed

POSITIONS = [
    {"t_s": 0.0, "lateral_m": -0.20, "up_m": 0.10, "forward_m": 1.60},
    {"t_s": 0.004, "lateral_m": -0.18, "up_m": 0.07, "forward_m": 1.72},
    {"t_s": 0.008, "lateral_m": -0.16, "up_m": 0.05, "forward_m": 1.84},
]
OPS = np.array([0.02, 0.0, 0.08])  # lateral, forward, up


def _evaluate(**overrides):
    kwargs = {
        "radial_mph": 72.0,
        "head_positions": POSITIONS,
        "contact_t_s": 0.006,
        "path_deg": 9.0,
        "attack_deg": -10.0,
        "ops_position_lfu_m": OPS,
        "path_sigma_deg": 4.7,
        "attack_sigma_deg": 4.6,
        "depth_sigma_m": 0.029,
        "input_labels": {
            "club_path": "measured",
            "attack_angle": "measured",
            "head_depth": "estimated",
        },
        **overrides,
    }
    return full_speed.evaluate_full_club_speed_at_contact(**kwargs)


def test_full_club_speed_is_the_radial_over_the_los_projection_at_contact():
    result = _evaluate()

    head = np.array([-0.17, 1.78, 0.06])  # (lateral, forward, up) halfway f1-f2
    los = (head - OPS) / np.linalg.norm(head - OPS)
    a, p = math.radians(-10.0), math.radians(9.0)
    direction = np.array([math.cos(a) * math.sin(p), math.cos(a) * math.cos(p), math.sin(a)])
    assert result["value_mph"] == pytest.approx(72.0 / float(los @ direction), rel=1e-9)
    assert result["status"] == "estimated"  # the head's depth is the located tee's
    assert result["referred_to"] == "impact" and result["reference_point"] == "clubhead"
    low, high = result["range_mph"]
    assert low < result["value_mph"] < high
    assert set(result["uncertainty_mph"]) >= {"club_path", "attack_angle", "head_depth"}


def test_measured_inputs_give_a_calculated_full_speed():
    result = _evaluate(input_labels={"club_path": "measured", "attack_angle": "measured"})
    assert result["status"] == "calculated"


def test_a_missing_angle_sigma_leaves_the_range_unbounded():
    result = _evaluate(path_sigma_deg=None)
    assert result["value_mph"] is not None and result["range_mph"] is None


@pytest.mark.parametrize(
    ("overrides", "words"),
    [
        ({"head_positions": POSITIONS[:1]}, "bracket"),
        ({"contact_t_s": None}, "contact"),
        ({"attack_deg": None}, "direction"),
        ({"radial_mph": None}, "OPS club"),
        ({"path_deg": 80.0}, "ill-conditioned"),
    ],
)
def test_refusals_name_why(overrides, words):
    result = _evaluate(**overrides)
    assert result["status"] == "not_measured" and result["value_mph"] is None
    assert words in result["reason"]


def test_the_full_smash_takes_it():
    club = _evaluate()
    ball = {"status": "estimated", "value_mph": 84.65, "referred_to": "launch"}
    smash = full_speed.evaluate_full_smash(ball, club)
    assert smash["status"] == "estimated"
    assert smash["value"] == pytest.approx(84.65 / club["value_mph"], rel=1e-6)


def test_the_kiosk_reads_oct27_shot1_s_club_stage_at_contact(monkeypatch):
    """Shot 1's own record (6dc0018e): club stage head f40-f44, contact between f43/f44,
    attack -10.29 at contact, path +10.97 on the target line (+2.07 offset)."""
    from types import SimpleNamespace  # noqa: PLC0415

    from openflight import server, shot_models  # noqa: PLC0415
    from tests.test_club_arc_no_prior_values import SHOT1  # noqa: PLC0415

    monkeypatch.setattr(server, "rig_geometry", SimpleNamespace(ops_offset_mm=(-40.0, 15.0, 0.0)))
    shot = SimpleNamespace(
        club_speed_mph=72.05,
        ball_range_label="estimated",
        ball_range_uncertainty_m=0.0287,
        camera_fusion_processing={"club_delivery": SHOT1},
        camera_fusion_context={
            "geometry": {
                "parameters": {
                    "calibrated_model_snapshot": {"origins": {"camera_origin_lfu": [0, 0, 0.093]}}
                }
            }
        },
        club_delivery={
            "metrics": {
                "club_path_deg": {"status": "measured", "source": "camera_iwr_chained"},
                "attack_angle_deg": {"status": "measured", "source": "camera_iwr_chained"},
            },
            "arc": {
                "conditioning": {
                    "data_only_sigma": {"club_path_deg": 4.71, "attack_angle_deg": 4.64}
                }
            },
        },
    )

    result = shot_models._club_full_at_contact(shot)  # pylint: disable=protected-access

    assert result["status"] == "estimated"
    assert 72.05 < result["value_mph"] < 80.0
    direction = result["geometry_m"]["direction_at_contact"]
    path = math.degrees(math.atan2(direction[0], direction[1]))
    assert path == pytest.approx(10.97 - 2.0666, abs=1e-6)  # the measurement frame
    assert result["range_mph"] is not None


# --- C15b: the OPS reading's own scatter and the angle-noise bias in the sigma ------------


def test_the_sigma_carries_the_ops_reading_scatter_and_the_angle_noise_bias():
    positions = [
        {"t_s": 0.0, "lateral_m": 0.0, "up_m": 0.0, "forward_m": 1.0},
        {"t_s": 0.01, "lateral_m": 0.0, "up_m": 0.0, "forward_m": 2.0},
    ]
    path = math.degrees(math.acos(0.9691))
    result = full_speed.evaluate_full_club_speed_at_contact(
        radial_mph=72.052,
        head_positions=positions,
        contact_t_s=0.005,
        path_deg=path,
        attack_deg=0.0,
        ops_position_lfu_m=(0.0, 0.0, 0.0),
        path_sigma_deg=5.15,
        attack_sigma_deg=5.09,
        depth_sigma_m=0.029,
        input_labels={"club_path": "measured", "attack_angle": "measured"},
    )

    assert result["value_mph"] == pytest.approx(74.35, abs=0.005)  # 72.052 / 0.9691
    assert result["correction_factor"] == pytest.approx(1 / 0.9691, rel=1e-6)
    budget = result["uncertainty_mph"]
    assert budget["ops_reading_scatter"] == full_speed.OPS_CLUB_READING_SD_MPH == 2.2
    assert budget["angle_noise_bias"] == full_speed.ANGLE_NOISE_BIAS_MPH == 0.6
    half = (result["range_mph"][1] - result["range_mph"][0]) / 2
    assert half == pytest.approx(math.sqrt(sum(v * v for v in budget.values())), rel=1e-9)
    assert "OPS reading time unrecorded; treated as contact" in result["note"]
    assert "radial x 1.0319" in result["note"]
