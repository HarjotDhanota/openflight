"""Independent pinhole positions and LOS Doppler constrain one club velocity."""

import math
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera import club_delivery as delivery


def radial_case(path=25.0, attack=-5.0, *, offset=(0.0, 0.0, 0.0), pitch=0.0):
    focal = 800.0
    camera = np.array([0.0, 0.0, 0.04])
    head = np.array([0.03, 2.0, 0.02135])
    ops = camera + offset
    angle, tilt = math.radians(path), math.radians(attack)
    velocity = 40.0 * np.array(
        [math.cos(tilt) * math.sin(angle), math.cos(tilt) * math.cos(angle), math.sin(tilt)]
    )
    pitch_rad = math.radians(pitch)

    def project(position):
        lateral, forward, up = position - camera
        depth = forward * math.cos(pitch_rad) + up * math.sin(pitch_rad)
        height = -forward * math.sin(pitch_rad) + up * math.cos(pitch_rad)
        return np.array([320.0 + focal * lateral / depth, 200.0 - focal * height / depth])

    times = np.array([1.999, 2.0])
    features = np.asarray(
        [
            [project(head + shift + velocity * (time - times[-1])) for time in times]
            for shift in (np.array([-0.001, 0.0, 0.0]), np.zeros(3), np.array([0.001, 0.0, 0.0]))
        ]
    )
    pixel = project(head)
    geometry = delivery.CameraDeliveryGeometry(
        camera_height_m=camera[2],
        radar_height_m=camera[2],
        tee_range_m=float(np.linalg.norm(head - camera)),
        ball_height_m=head[2],
        ball_lateral_offset_m=head[0],
        setup_pitch_deg=pitch,
        setup_focal_px=focal,
    )
    ball = SimpleNamespace(
        x=pixel[0],
        y=pixel[1],
        diameter_px=focal * geometry.ball_diameter_m / np.linalg.norm(head - camera),
    )
    los = (head - ops) / np.linalg.norm(head - ops)
    radial = float(los @ velocity) * 2.23694
    reference = SimpleNamespace(
        ops_origin_lfu=tuple(ops),
        head_position_lfu=tuple(head),
        timestamp_s=times[-1],
        clock="camera_sensor",
    )
    return features, times, ball, geometry, radial, reference


def solve(case, **overrides):
    features, times, ball, geometry, radial, reference = case
    kwargs = dict(
        ball=ball,
        geometry=geometry,
        ops_club_speed_mph=radial,
        ops_reference=reference,
        timestamp_source="camera_sensor",
    )
    kwargs.update(overrides)
    return delivery.camera_ops_delivery_from_feature_pair(features, times, **kwargs)


@pytest.mark.parametrize("path", [-25.0, 25.0])
@pytest.mark.parametrize("pitch", [-7.0, 0.0, 8.0])
@pytest.mark.parametrize("offset", [(0.0, 0.0, 0.0), (0.12, -0.08, 0.06)])
def test_radial_constraint_recovers_known_velocity(path, pitch, offset):
    estimate = solve(radial_case(path, offset=offset, pitch=pitch))
    assert estimate is not None
    assert estimate.path_deg == pytest.approx(path, abs=0.01)
    assert estimate.attack_angle_deg == pytest.approx(-5.0, abs=0.01)
    assert estimate.speed_ratio_ops is None


@pytest.mark.parametrize(
    "change",
    [
        None,
        {"timestamp_s": 1.99},
        {"clock": "host"},
        {"ops_origin_lfu": (0.0, 0.0, math.nan)},
        {"head_position_lfu": (0.0, 0.0, 0.04)},
    ],
)
def test_missing_or_mismatched_reference_never_supplies_a_velocity(change):
    case = radial_case()
    reference = None if change is None else SimpleNamespace(**{**vars(case[-1]), **change})
    assert solve(case, ops_reference=reference) is None


def test_mirror_preserves_physical_path():
    features, times, ball, geometry, radial, reference = radial_case()
    features = features.copy()
    features[:, :, 0] = 640.0 - features[:, :, 0]
    ball.x = 640.0 - ball.x
    estimate = solve(
        (features, times, ball, replace(geometry, horizontal_pixel_sign=-1.0), radial, reference)
    )
    assert estimate.path_deg == pytest.approx(25.0, abs=0.01)


def test_legacy_scalar_alone_does_not_define_an_ops_reference():
    features, times, ball, geometry, radial, _reference = radial_case()
    assert (
        delivery.camera_ops_delivery_from_feature_pair(
            features, times, ball=ball, geometry=geometry, ops_club_speed_mph=radial
        )
        is None
    )


def test_runtime_keeps_missing_reference_reason_without_using_the_old_closure(monkeypatch):
    features, _times, ball, geometry, radial, _reference = radial_case()
    monkeypatch.setattr(delivery, "_clubhead_pair_tracks", lambda *a, **k: (features, 5.0))

    def unsupported(*args, **kwargs):
        raise AssertionError("the scalar speed alone cannot close this geometry")

    monkeypatch.setattr(delivery, "camera_ops_delivery_from_feature_pair", unsupported)
    result = delivery.estimate_chained_delivery(
        np.full((60, 20, 20), 150, dtype=np.uint8),
        np.arange(60, dtype=np.int64) * 2_000_000,
        trigger_index=40,
        range_evidence=None,
        geometry=replace(geometry, image_width_px=20, image_height_px=20),
        ops_club_speed_mph=radial,
        reference_ball=delivery.ReferenceBall(10.0, 10.0, 12.0, 120),
        reference_ball_selected=True,
    )
    assert result.status == "rejected_no_ops_reference"
    assert result.club_path_deg is None and result.attack_angle_deg is None
    assert any("associated head position and reading time" in note for note in result.notes)
