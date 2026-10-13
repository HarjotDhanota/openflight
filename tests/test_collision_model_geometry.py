"""Part E integration: model-geometry club inputs (D39) reach spin, spin axis and flight.

E1 labels the arc's club path and attack angle "model_geometry" whenever the arc
leans on the published prior (every shot on today's lens). E2's collision model
inherits that label; this file checks the label rides all the way to the shot's
spin, spin axis, flight and carry, that simulators still refuse it (D31), and
that a missing model-geometry input is still "not measured" (no filled-in values).
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pytest

from openflight import collision_model as cm, server as server_module
from openflight.club_arc import ARC_PRIOR_SOURCE, MODEL_GEOMETRY
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from openflight.sim import resolver
from openflight.sim.resolver import resolve_shot
from openflight.sim.types import PlayerState, UnmeasuredShotError


def _arc_positions():
    """Five pre-impact head positions on a 57 deg, 1.0 m right-handed arc (2 ms frames)."""
    theta, radius, omega, t_low = math.radians(57.0), 1.0, 38.0, 0.002
    e1 = np.array([1.0, 0.0, 0.0])  # forward
    e2 = math.cos(theta) * np.array([0.0, -1.0, 0.0]) + math.sin(theta) * np.array([0, 0, 1.0])
    impact = np.array([1.26, 0.0, 0.021])
    positions = []
    for t_s in (-0.0085, -0.0065, -0.0045, -0.0025, -0.0005):

        def at(time_s):
            phi = omega * (time_s - t_low)
            return radius * math.sin(phi) * e1 + radius * (1.0 - math.cos(phi)) * e2

        point = impact + at(t_s) - at(0.0)
        positions.append(
            {"t_s": t_s, "lateral_m": point[1], "up_m": point[2], "forward_m": point[0]}
        )
    return positions


def _arc_entry(value, *, frame=None, status=MODEL_GEOMETRY, source=ARC_PRIOR_SOURCE):
    return {
        "value": value,
        "status": status,
        "source": source,
        "frame": frame,
        "range": None,
        "reason": None,
    }


def _club_delivery(path=-2.0, attack=-4.0, **entries):
    metrics = {
        "club_path_deg": _arc_entry(path, frame="target_line"),
        "attack_angle_deg": _arc_entry(attack),
    }
    metrics.update(entries)
    return {"schema": "openflight.club_delivery", "version": 1, "metrics": metrics}


def _arc_shot(**overrides):
    """A 7-iron whose chained camera delivery was rejected, so the arc prior carries path
    and attack: launch measured, club speed measured, no spin measured."""
    values = {
        "ball_speed_mph": 120.0,
        "club_speed_mph": 85.0,
        "timestamp": datetime(2026, 10, 12, 9, 0, 0),
        "club": ClubType.IRON_7,
        "launch_angle_horizontal": 1.0,
        "launch_angle_horizontal_source": "camera",
        "launch_angle_horizontal_confidence": 0.8,
        "launch_angle_horizontal_frame": "target_line",
        "launch_angle_vertical": 18.0,
        "launch_angle_vertical_source": "radar",
        "experimental_fused_status": "rejected_low_path_consensus",
        "club_delivery": _club_delivery(),
    }
    values.update(overrides)
    return Shot(**values)


def _attach(shot):
    server_module._attach_collision_model(shot)  # pylint: disable=protected-access


# ---- the arc's labelled path and attack reach the collision model --------------------


def test_the_arc_path_and_attack_enter_the_collision_model_as_model_geometry():
    shot = _arc_shot()

    _attach(shot)

    inputs = shot.collision_model["inputs"]
    assert inputs["club_path"]["value"] == -2.0
    assert inputs["club_path"]["label"] == "model_geometry"
    assert inputs["club_path"]["source"] == ARC_PRIOR_SOURCE
    assert inputs["club_path"]["frame"] == "target_line"
    assert inputs["attack_angle"]["value"] == -4.0
    assert inputs["attack_angle"]["label"] == "model_geometry"
    metrics = shot.collision_model["metrics"]
    for name in ("face_angle", "dynamic_loft", "spin_loft", "d_plane_tilt", "spin_rate"):
        assert metrics[name]["status"] == "model_geometry", name
    expected = cm.evaluate(
        club=ClubType.IRON_7,
        club_path=cm.Input(-2.0, cm.LABEL_MODEL_GEOMETRY, frame="target_line"),
        attack_angle=cm.Input(-4.0, cm.LABEL_MODEL_GEOMETRY),
        launch_horizontal=cm.Input(1.0, frame="target_line"),
        launch_vertical=cm.Input(18.0),
        club_speed_mph=cm.Input(85.0),
    ).metrics
    assert shot.face_angle_deg == pytest.approx(expected["face_angle"].value, abs=0.05)
    assert shot.spin_rpm_collision == pytest.approx(expected["spin_rate"].value, abs=0.5)


def test_a_model_geometry_face_names_its_label_on_the_kiosk_status():
    shot = _arc_shot()

    _attach(shot)

    assert shot.experimental_face_angle_status == "d_plane_estimate_model_geometry"
    assert shot.experimental_face_angle_path_source == ARC_PRIOR_SOURCE


def test_a_calculated_arc_value_enters_as_calculated():
    delivery = _club_delivery(
        club_path_deg=_arc_entry(
            -2.0, frame="target_line", status="calculated", source="club_arc:shot_fit"
        ),
        attack_angle_deg=_arc_entry(-4.0, status="calculated", source="club_arc:shot_fit"),
    )
    shot = _arc_shot(club_delivery=delivery)

    _attach(shot)

    assert shot.collision_model["inputs"]["club_path"]["label"] == "calculated"
    assert shot.collision_model["metrics"]["spin_rate"]["status"] == "calculated"


def test_a_measured_path_wins_over_the_arc():
    shot = _arc_shot(
        experimental_fused_status="chained_high",
        experimental_fused_club_path_deg=3.0,
        experimental_fused_club_path_frame="target_line",
        experimental_fused_attack_angle_deg=-5.0,
        experimental_fused_club_path_confidence="medium",
        experimental_fused_attack_angle_confidence="medium",
    )

    _attach(shot)

    inputs = shot.collision_model["inputs"]
    assert inputs["club_path"]["value"] == 3.0 and inputs["club_path"]["label"] == "measured"
    assert inputs["attack_angle"]["value"] == -5.0
    assert inputs["attack_angle"]["label"] == "measured"


@pytest.mark.parametrize(
    "entry",
    [
        _arc_entry(None, frame=None, status="not_measured", source=None),
        _arc_entry(math.nan, frame="target_line"),
        None,
    ],
)
def test_a_missing_arc_path_is_still_not_measured(entry):
    delivery = _club_delivery()
    delivery["metrics"]["club_path_deg"] = entry
    shot = _arc_shot(club_delivery=delivery)

    _attach(shot)

    assert shot.collision_model["inputs"]["club_path"]["value"] is None
    assert shot.face_angle_deg is None
    assert shot.spin_axis_deg is None
    assert shot.collision_model["metrics"]["spin_axis"]["status"] == "not_measured"


def test_the_kiosk_s_own_arc_reaches_the_collision_model(monkeypatch):
    """The real E1 payload: a rejected chained path, the arc on its prior."""
    monkeypatch.setattr(server_module.sim_player_state, "handed", "RH")
    shot = _arc_shot(club_delivery=None)
    server_module._attach_club_arc(  # pylint: disable=protected-access
        shot,
        {
            "status": "rejected_low_path_consensus",
            "club_path_deg": None,
            "attack_angle_deg": None,
            "head_positions": _arc_positions(),
            "contact_t_s": 0.0,
            "horizontal_offset_deg": 0.0,
        },
    )
    path = shot.club_delivery["metrics"]["club_path_deg"]
    assert path["status"] == MODEL_GEOMETRY and path["value"] is not None

    _attach(shot)

    assert shot.collision_model["inputs"]["club_path"]["value"] == pytest.approx(path["value"])
    assert shot.collision_model["inputs"]["club_path"]["label"] == "model_geometry"
    assert shot.collision_model["metrics"]["face_angle"]["status"] == "model_geometry"


# ---- simulators: measured or calculated only (D31); model geometry behind one switch ---

MODEL_GEOMETRY_AXIS_SOURCE = (
    "model geometry (D-plane tilt, Wood et al. 2018 weights; club path from the arc prior)"
)


def _model_geometry_shot(**overrides):
    """A fusion shot whose spin, spin axis and carry are all built on the arc prior."""
    values = {
        "ball_speed_mph": 120.0,
        "club_speed_mph": 85.0,
        "timestamp": datetime(2026, 10, 12, 9, 0, 0),
        "club": ClubType.IRON_7,
        "launch_angle_horizontal": 1.0,
        "launch_angle_horizontal_source": "camera",
        "launch_angle_vertical": 18.0,
        "launch_angle_vertical_source": "radar",
        "spin_rpm": 6400.0,
        "spin_source": "model_geometry",
        "spin_method": "Penner 2001; club inputs model geometry (D39)",
        "spin_axis_deg": 3.0,
        "spin_axis_source": MODEL_GEOMETRY_AXIS_SOURCE,
        "carry_spin_adjusted": 150.0,
        "carry_status": "model_geometry",
        "club_delivery": _club_delivery(),
    }
    values.update(overrides)
    return Shot(**values)


def test_the_simulator_switch_for_model_geometry_defaults_off():
    assert resolver.SIMULATORS_ACCEPT_MODEL_GEOMETRY is False


def test_a_fusion_shot_with_model_geometry_spin_and_axis_is_not_sent_and_says_why():
    with pytest.raises(UnmeasuredShotError) as raised:
        resolve_shot(_model_geometry_shot(), PlayerState(), substitutes=False)

    assert raised.value.missing == ["spin", "spin axis"]
    assert "model geometry" in str(raised.value)
    assert "spin, spin axis" in str(raised.value)


def test_a_model_geometry_axis_alone_still_refuses_the_fusion_shot():
    shot = _model_geometry_shot(spin_rpm=6800.0, spin_confidence=0.9, spin_source="measured")

    with pytest.raises(UnmeasuredShotError) as raised:
        resolve_shot(shot, PlayerState(), substitutes=False)

    assert raised.value.missing == ["spin axis"]
    assert "model geometry" in str(raised.value)


def test_an_ops_only_resolve_never_sends_model_geometry_values():
    resolved = resolve_shot(_model_geometry_shot(), PlayerState(), substitutes=True)

    assert resolved.provenance["total_spin"] == "estimated"
    assert resolved.total_spin_rpm != 6400.0
    assert resolved.provenance["spin_axis"] == "estimated" and resolved.spin_axis_deg == 0.0
    assert resolved.carry_yards is None and "carry" not in resolved.provenance
    assert resolved.club_path_deg == 0.0 and resolved.provenance["club_path"] == "estimated"
    assert "model_geometry" not in resolved.provenance.values()


def test_the_switch_on_sends_every_model_geometry_value_under_its_label(monkeypatch):
    monkeypatch.setattr(resolver, "SIMULATORS_ACCEPT_MODEL_GEOMETRY", True)

    resolved = resolve_shot(_model_geometry_shot(), PlayerState(), substitutes=False)

    assert resolved.total_spin_rpm == 6400.0
    assert resolved.spin_axis_deg == 3.0
    assert resolved.carry_yards == 150.0
    assert resolved.club_path_deg == -2.0
    for name in ("total_spin", "spin_axis", "back_spin", "side_spin", "carry", "club_path"):
        assert resolved.provenance[name] == "model_geometry", name
    assert resolved.provenance["vla"] == "measured"
