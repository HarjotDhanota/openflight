"""Collision angles use one zero from the shot's saved target-line record."""

from copy import deepcopy

import pytest

from openflight import server, shot_models
from tests.test_collision_wiring import _delivered_shot


def attach(shot):
    shot_models._attach_collision_model(shot)
    return shot


@pytest.mark.parametrize("offset", [-6.0, 0.0, 7.0])
@pytest.mark.parametrize("boresight_input", ["path", "launch"])
def test_saved_offset_normalizes_both_mixed_orders(offset, boresight_input, monkeypatch):
    monkeypatch.setattr(server, "horizontal_offset_deg", 39.0)
    snapshot = {"horizontal_offset_deg": offset, "frame": "target_line", "revision": 17}
    mixed = _delivered_shot(
        experimental_fused_club_path_deg=-2.0 if boresight_input == "path" else -2.0 + offset,
        experimental_fused_club_path_frame="unit_boresight"
        if boresight_input == "path"
        else "target_line",
        launch_angle_horizontal=1.0 if boresight_input == "launch" else 1.0 + offset,
        launch_angle_horizontal_frame="unit_boresight"
        if boresight_input == "launch"
        else "target_line",
        target_line=snapshot,
    )
    original = (
        mixed.experimental_fused_club_path_deg,
        mixed.launch_angle_horizontal,
        deepcopy(snapshot),
    )
    expected = attach(
        _delivered_shot(
            experimental_fused_club_path_deg=-2.0 + offset, launch_angle_horizontal=1.0 + offset
        )
    )
    actual = attach(mixed)

    for name, metric in expected.collision_model["metrics"].items():
        assert actual.collision_model["metrics"][name] == metric
    for name in ("club_path", "launch_horizontal"):
        assert actual.collision_model["inputs"][name]["frame"] == "target_line"
    conversion = actual.collision_model["horizontal_frame_conversion"]
    assert conversion["offset_deg"] == offset
    assert conversion["target_line_revision"] == 17
    assert conversion["original_inputs"]["club_path"]["value"] == original[0]
    assert conversion["original_inputs"]["launch_horizontal"]["value"] == original[1]
    assert actual.experimental_fused_club_path_deg == original[0]
    assert actual.launch_angle_horizontal == original[1]
    assert actual.target_line == original[2]


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        {},
        {"frame": "unit_boresight", "horizontal_offset_deg": -3.0},
        {"frame": "target_line", "horizontal_offset_deg": None},
        {"frame": "target_line", "horizontal_offset_deg": float("nan")},
    ],
)
def test_unbound_mixed_frames_retain_inputs_without_publishing_a_model_angle(snapshot, monkeypatch):
    monkeypatch.setattr(server, "horizontal_offset_deg", -3.0)
    current = attach(
        _delivered_shot(experimental_fused_club_path_frame="unit_boresight", target_line=snapshot)
    )
    assert current.spin_axis_deg is None
    assert current.collision_model["inputs"]["club_path"]["frame"] == "unit_boresight"
    assert current.collision_model["inputs"]["launch_horizontal"]["frame"] == "target_line"
    assert current.experimental_face_angle_status == "mixed_horizontal_frames"
    assert "horizontal_frame_conversion" not in current.collision_model


def test_same_frame_inputs_are_not_rotated_twice():
    baseline = attach(_delivered_shot())
    current = attach(
        _delivered_shot(target_line={"frame": "target_line", "horizontal_offset_deg": -3.0})
    )
    assert current.collision_model == baseline.collision_model


def test_conversion_preserves_an_experimental_input_label():
    current = attach(
        _delivered_shot(
            experimental_fused_club_path_frame="unit_boresight",
            experimental_fused_club_path_confidence="low",
            target_line={"frame": "target_line", "horizontal_offset_deg": -3.0},
        )
    )
    given = current.collision_model["inputs"]["club_path"]
    assert given["value"] == -5.0
    assert given["label"] == "estimated" and given["experimental"]
    assert current.collision_model["metrics"]["face_angle"]["status"] == "estimated"


def test_a_late_run_does_not_reuse_an_old_frame_conversion():
    current = attach(
        _delivered_shot(
            experimental_fused_club_path_frame="unit_boresight",
            target_line={"frame": "target_line", "horizontal_offset_deg": -3.0},
        )
    )
    assert current.spin_axis_frame == "target_line"
    assert "horizontal_frame_conversion" in current.collision_model
    current.target_line = None
    attach(current)
    assert current.spin_axis_deg is None
    assert current.experimental_face_angle_status == "mixed_horizontal_frames"
    assert "horizontal_frame_conversion" not in current.collision_model
