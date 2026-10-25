"""The camera head track feeds the collision model, and every output inherits experimental.

Owner decision 5 (25 Oct 2026). The head track's club path and attack angle enter the
collision model as "estimated" with the reason "camera head track (experimental)" (the
model has no experimental label, and would treat one as missing).
``shot_metrics.with_input_labels`` then lifts every output built on them to
experimental, with a note: face angle, dynamic loft, spin loft, face to path, D-plane
tilt, spin axis, the collision spin, and through them carry and the flight metrics.
Simulators never receive them. The E1 arc fits the head track's hosel positions under
their label: experimental, or model geometry for an output the published prior sets
(the swing plane from two or three points), with the reason.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from openflight import collision_model, server as server_module, shot_metrics, shot_models
from openflight.clubs import ClubType
from openflight.flight import FLIGHT_METRICS, compute_flight
from openflight.launch_monitor import CARRY_ESTIMATED, Shot
from openflight.sim import resolver
from openflight.sim.resolver import resolve_shot
from openflight.sim.types import PlayerState, UnmeasuredShotError
from tests.every_metric import full_ball_speed
from tests.test_club_arc import HEAD_TRACK_SIGMA_M, HEAD_TRACK_TIMES, _synthetic_swing

HEAD_TRACK = "camera_head_track"
EXPERIMENTAL_OUTPUTS = (
    "face_angle",
    "dynamic_loft",
    "spin_loft",
    "face_to_path",
    "d_plane_tilt",
    "spin_axis",
    "spin_rate",
)


def _head_track_positions() -> list[dict]:
    positions, _truth = _synthetic_swing(times=HEAD_TRACK_TIMES, noise_m=0.0)
    return [
        {"t_s": p.t_s, "lateral_m": p.lateral_m, "up_m": p.up_m, "forward_m": p.forward_m}
        for p in positions
    ]


def _head_track_shot(head_positions=(), **overrides) -> Shot:
    """A 7-iron with a measured radar launch, a weak OPS spin and only the head track's
    club data, built as the kiosk builds it."""
    values = {
        "ball_speed_mph": 120.0,
        "club_speed_mph": 85.0,
        "timestamp": datetime(2026, 10, 25, 9, 0, 0),
        "club": ClubType.IRON_7,
        "launch_angle_vertical": 18.0,
        "launch_angle_vertical_source": "radar",
        "inclinometer": {"applied_to": ["camera", "iwr6843"]},
        "launch_angle_vertical_confidence": 0.9,
        "launch_angle_horizontal": 1.0,
        "launch_angle_horizontal_source": "radar",
        "launch_angle_horizontal_confidence": 0.9,
        "launch_angle_horizontal_frame": "target_line",
        "spin_rpm": 6800.0,
        "spin_confidence": 0.3,
        "mode": "rolling-buffer",
    }
    values.update(overrides)
    shot = Shot(**values)
    shot_models._attach_club_arc(  # pylint: disable=protected-access
        shot,
        {
            "status": HEAD_TRACK,
            "club_path_deg": 3.1,
            "attack_angle_deg": -4.2,
            "path_confidence_tier": "experimental",
            "attack_confidence_tier": "experimental",
            "club_path_frame": "target_line",
            "head_positions": list(head_positions),
            "contact_t_s": 0.0,
            "horizontal_offset_deg": 0.0,
            "head_track": {"label": "experimental", "position_sigma_m": HEAD_TRACK_SIGMA_M},
        },
    )
    shot.experimental_fused_status = HEAD_TRACK
    shot.experimental_fused_club_path_deg = 3.1
    shot.experimental_fused_club_path_frame = "target_line"
    shot.experimental_fused_attack_angle_deg = -4.2
    shot.experimental_fused_club_path_confidence = "experimental"
    shot.experimental_fused_attack_angle_confidence = "experimental"
    shot.ball_speed_full = full_ball_speed()
    shot.ball_speed_full_mph = shot.ball_speed_full["value_mph"]
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access
    return shot


def _fly(shot: Shot) -> Shot:
    flight = compute_flight(shot)
    shot.flight = flight.to_dict()
    if flight.as_measured is not None:
        shot.carry_spin_adjusted = flight.as_measured.carry_yards
        shot.carry_status = CARRY_ESTIMATED
    return shot


def _records(shot: Shot) -> dict:
    return shot_metrics.records(shot.to_dict())


@pytest.mark.parametrize("name", ["club_path", "attack_angle"])
def test_the_head_track_enters_the_collision_model_estimated_with_its_reason(name):
    inputs = _head_track_shot().collision_model["inputs"]

    assert inputs[name]["label"] == collision_model.LABEL_ESTIMATED
    assert inputs[name]["source"] == HEAD_TRACK
    assert inputs[name]["reason"] == shot_models.HEAD_TRACK_REASON
    assert shot_models.HEAD_TRACK_REASON == "camera head track (experimental)"


def test_the_collision_model_builds_face_loft_and_axis_on_it():
    shot = _head_track_shot()

    assert shot.face_angle_deg is not None
    assert shot.dynamic_loft_deg is not None
    assert shot.spin_loft_deg is not None
    assert shot.spin_axis_deg is not None
    assert shot.experimental_face_angle_path_source == HEAD_TRACK
    assert shot.experimental_face_angle_status.endswith("_estimated")


@pytest.mark.parametrize("name", ["club_path", "attack_angle"])
def test_the_head_track_path_and_attack_are_experimental_records(name):
    record = _records(_head_track_shot())[name]

    assert record["status"] == "experimental"
    assert record["source"] == shot_metrics.HEAD_TRACK_SOURCE_LABEL
    assert "camera head track" in record["note"]


@pytest.mark.parametrize("name", EXPERIMENTAL_OUTPUTS)
def test_every_output_built_on_the_head_track_is_experimental_with_a_note(name):
    record = _records(_fly(_head_track_shot()))[name]

    assert record["value"] is not None, name
    assert record["status"] == "experimental", name
    assert "experimental" in record["note"], name


@pytest.mark.parametrize("name", FLIGHT_METRICS)
def test_carry_and_the_flight_inherit_experimental(name):
    shot = _fly(_head_track_shot())
    record = _records(shot)[name]

    if record["value"] is None:  # total needs a landing the turf model covers
        assert record["status"] == "not_measured" and record["reason"]
        return
    assert record["status"] == "experimental", name
    assert "experimental" in record["note"], name


def test_the_collision_spin_is_the_shots_spin_and_experimental():
    shot = _head_track_shot()

    assert shot.spin_source == "estimated"
    assert shot.spin_rpm == shot.spin_rpm_collision
    assert _records(shot)["spin_rate"]["status"] == "experimental"


@pytest.mark.parametrize("accept_estimated", [False, True], ids=["default", "switch-on"])
@pytest.mark.parametrize("accept_model_geometry", [False, True], ids=["mg-off", "mg-on"])
def test_simulators_never_receive_values_built_on_the_head_track(
    monkeypatch, accept_estimated, accept_model_geometry
):
    monkeypatch.setattr(resolver, "SIMULATORS_ACCEPT_ESTIMATED", accept_estimated)
    monkeypatch.setattr(resolver, "SIMULATORS_ACCEPT_MODEL_GEOMETRY", accept_model_geometry)
    shot = _fly(_head_track_shot())

    with pytest.raises(UnmeasuredShotError) as refused:
        resolve_shot(shot, PlayerState())

    assert refused.value.missing == ["spin (experimental)", "spin axis (experimental)"]


def test_a_head_track_shot_with_a_measured_spin_still_withholds_its_axis_and_carry(monkeypatch):
    monkeypatch.setattr(resolver, "SIMULATORS_ACCEPT_ESTIMATED", True)
    shot = _fly(_head_track_shot(spin_confidence=0.9))
    assert shot.spin_source != "estimated"  # the OPS's own spin, measured

    with pytest.raises(UnmeasuredShotError) as refused:
        resolve_shot(shot, PlayerState())

    assert refused.value.missing == ["spin axis (experimental)"]


def test_a_measured_route_outranks_the_head_track():
    shot = _head_track_shot(
        experimental_club_path_deg=6.0, experimental_club_path_status="accepted"
    )
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    path = shot.collision_model["inputs"]["club_path"]
    assert path["source"] == "iwr" and path["label"] == collision_model.LABEL_MEASURED


@pytest.mark.parametrize(
    "name", ["swing_plane", "swing_direction", "low_point_distance", "low_point_height"]
)
def test_without_head_track_positions_the_arc_is_not_measured(name):
    record = _records(_head_track_shot())[name]

    assert record["value"] is None and record["status"] == "not_measured"
    assert "no club-head positions" in record["reason"]


def test_the_arc_on_head_track_positions_is_the_prior_s_plane_and_experimental_otherwise():
    records = _records(_head_track_shot(head_positions=_head_track_positions()))

    plane = records["swing_plane"]
    assert plane["value"] is not None and plane["status"] == "model_geometry"
    assert "prior sets it" in plane["note"]
    for name in ("swing_direction", "low_point_distance", "low_point_height", "low_point_side"):
        record = records[name]
        assert record["value"] is not None, name
        assert record["status"] == "experimental", name
        assert "head track" in record["note"], name


def test_the_head_track_low_point_reaches_the_contact_note_estimated_with_its_reason():
    shot = _head_track_shot(head_positions=_head_track_positions())

    low_point = shot_models._low_point_input(shot)  # pylint: disable=protected-access

    assert low_point.present
    assert low_point.label == collision_model.LABEL_ESTIMATED
    assert low_point.reason == shot_models.HEAD_TRACK_REASON
    assert low_point.experimental


def test_the_spin_chain_takes_the_collision_spin_labelled_experimental():
    """No formula spin route any more (25 Oct 2026): the head-track collision spin stands."""
    shot = _head_track_shot()

    server_module._resolve_spin(shot)  # pylint: disable=protected-access

    spin = shot.input_sources["spin"]
    assert spin["route"] == "collision"
    assert spin["label"] == "experimental"
    assert "attack_angle" in spin["note"] and "club_path" in spin["note"]
    assert [entry["route"] for entry in spin["tried"]] == ["ops"]
    assert shot.spin_rpm == shot.spin_rpm_collision


def test_an_output_already_experimental_still_names_the_head_track():
    """On a camera-fallback launch the face is experimental already; it still says it
    rests on the head track's path and attack."""
    shot = _head_track_shot(
        launch_angle_vertical_source="camera_fallback",
        launch_angle_horizontal_source="camera_fallback",
    )

    face = _records(shot)["face_angle"]

    assert face["status"] == "experimental"
    assert "rests on the experimental club path" in face["note"]
    assert "rests on the experimental attack angle" in face["note"]
