"""Temporal phase change from motion is distinct from measurement scatter."""

from types import SimpleNamespace

import numpy as np
import pytest

from openflight.iwr6843 import lcmf
from openflight.iwr6843.runtime import camera_horizontal_input


@pytest.mark.parametrize("horizontal", [0, 10, 60, -60])
@pytest.mark.parametrize("roll_deg", [-10, 0, 10])
def test_noise_free_motion_has_high_residual_coherence_without_qualifying_wide_model(
    monkeypatch, horizontal, roll_deg
):
    times = np.linspace(0.006, 0.06, 12)
    vertical = np.radians(10)
    heading = np.radians(horizontal)
    tee = np.array([1.6, 0, -0.06])
    points = tee + times[:, None] * 40 * np.array(
        [np.cos(vertical) * np.cos(heading), np.cos(vertical) * np.sin(heading), np.sin(vertical)]
    )
    ranges = np.linalg.norm(points, axis=1)
    roll = np.radians(roll_deg)
    reference = 0.4
    phases = (
        -np.pi * (points[:, 1] * np.cos(roll) + points[:, 2] * np.sin(roll)) / ranges + reference
    )
    snapshots = [(i, t, phase, phase, 1.0) for i, (t, phase) in enumerate(zip(times, phases))]
    monkeypatch.setattr(
        lcmf,
        "_tx2_phase_snapshots",
        lambda *args, **kwargs: (SimpleNamespace(range_res_m=0.046875), snapshots),
    )
    shot = SimpleNamespace(
        track=SimpleNamespace(range_at=lambda t, res: np.interp(t, times, ranges))
    )

    angle, quality, status = lcmf._tx2_horizontal_motion(
        b"",
        shot,
        SimpleNamespace(range_bias_m=0, roll_rad=roll),
        tdm_sign=1,
        vertical_launch_deg=10,
        tee_position_m=tuple(tee),
        phase_reference_rad=reference,
    )

    assert angle == pytest.approx(horizontal, abs=1e-7)
    assert quality > 0.99999
    assert status == (
        "hlcmf_v3_wide_model_unqualified" if abs(horizontal) > 20 else "hlcmf_v3_accepted"
    )
    if abs(horizontal) > 20:
        if roll_deg == 0:
            assert abs(np.mean(np.exp(1j * phases))) < 0.9
        measurement = SimpleNamespace(
            accepted=True,
            horizontal_deg=angle,
            horizontal_confidence=quality,
            horizontal_status=status,
        )
        assert camera_horizontal_input(measurement) == (None, None)


@pytest.mark.parametrize(
    "status",
    [None, "future_unknown", "hlcmf_v3_wide_model_unqualified", "hlcmf_v3_low_motion_coherence"],
)
def test_unqualified_status_is_not_promoted_by_a_high_score(status):
    measurement = SimpleNamespace(
        accepted=True, horizontal_deg=43, horizontal_confidence=0.99, horizontal_status=status
    )
    assert camera_horizontal_input(measurement) == (None, None)


def test_inconsistent_motion_is_not_rescued_by_removing_expected_phase_change(monkeypatch):
    times = np.linspace(0.02, 0.08, 12)
    points = np.array([1.6, 0, -0.06]) + times[:, None] * np.array([40, 0, 5])
    ranges = np.linalg.norm(points, axis=1)
    phases = np.array([0.7, -0.7] * 6)
    snapshots = [(i, t, phase, phase, 1.0) for i, (t, phase) in enumerate(zip(times, phases))]
    monkeypatch.setattr(
        lcmf,
        "_tx2_phase_snapshots",
        lambda *args, **kwargs: (SimpleNamespace(range_res_m=0.046875), snapshots),
    )
    shot = SimpleNamespace(
        track=SimpleNamespace(range_at=lambda t, res: np.interp(t, times, ranges))
    )

    angle, quality, status = lcmf._tx2_horizontal_motion(
        b"",
        shot,
        SimpleNamespace(range_bias_m=0, roll_rad=0),
        tdm_sign=1,
        vertical_launch_deg=np.degrees(np.arctan2(5, 40)),
        tee_position_m=(1.6, 0, -0.06),
    )

    assert angle is not None
    assert quality < lcmf.HORIZONTAL_COHERENCE_MIN
    assert status == "hlcmf_v3_low_motion_coherence"


def test_wide_model_source_remains_experimental_through_collision_inputs():
    from openflight import shot_models, source_labels
    from openflight.launch_monitor import Shot

    shot = Shot(
        timestamp=0,
        ball_speed_mph=90,
        club_speed_mph=70,
        launch_angle_horizontal=43,
        launch_angle_horizontal_source="radar_model_unqualified",
    )
    assert source_labels.horizontal_launch_label("radar_model_unqualified") == "experimental"
    assert shot_models._collision_inputs(shot)["launch_horizontal"].experimental
