"""Known-answer shots through the kiosk's own shot assembly.

A known club delivery becomes a known launch (forward collision model, iron axis fit, Penner
2001 ball speed), arc positions and OPS readings; through the server's own functions every output
must come back with the delivery's value and label, RH and LH, at target yaw 0 and +-3 deg.
The positive harness supplies an ideal full impact-speed input separately from OPS;
the live reading-time route is tested without that supplied input too.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import openflight.server as server_module
from openflight import collision_forward, full_speed, shot_models
from openflight.club_arc import LOW_POINT_OFFSETS_MM, prior_for_club
from openflight.clubs import ClubType
from openflight.flight import AIR_DENSITY_NORMALIZED, FLIGHT_METRICS
from openflight.flight_sim import AIR_DENSITY_STD, LaunchConditions, simulate
from openflight.horizontal_frame import offset_from_target_yaw
from openflight.launch_monitor import SMASH_RADIAL_RATIO, Shot
from openflight.rig_geometry import RigGeometry

REPO = Path(__file__).resolve().parents[1]
MPH = 0.44704

# Penner 2001 Sec. II A: ball 45.9 g, 4.27 cm; head 200 g; I = 2/5 m r^2
PENNER_M, PENNER_D, PENNER_HEAD = 0.0459, 0.0427, 0.200


def _unit(elevation_deg, azimuth_deg):
    e, a = math.radians(elevation_deg), math.radians(azimuth_deg)
    return np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])


def spin_loft_deg(loft, face, attack, path):
    d, n = _unit(attack, path), _unit(loft, face)
    return math.degrees(math.acos(max(-1.0, min(1.0, float(d @ n)))))


def d_plane_tilt_deg(loft, face, attack, path):
    """The D plane's angle to the plumb line, + when the face points right of the path.

    Built differently from the module: the face normal's part perpendicular to the
    head's direction spans the plane with it. The angle between a plane and the
    plumb line is asin |z . m| for the plane's unit normal m; its sign is the side
    that part of the normal leans to, seen along the head's direction.
    """
    d, n = _unit(attack, path), _unit(loft, face)
    p = n - (n @ d) * d
    p /= np.linalg.norm(p)
    m = np.cross(d, p)
    m /= np.linalg.norm(m)
    right_of_travel = np.array([-math.sin(math.radians(path)), math.cos(math.radians(path)), 0.0])
    lean = 1.0 if p @ right_of_travel > 0 else -1.0
    return lean * math.degrees(math.asin(abs(m[2])))


def penner(club_speed_mph, theta_deg):
    v = club_speed_mph * MPH
    t = math.radians(theta_deg)
    r = PENNER_D / 2
    e = 0.86 - 0.0029 * v * math.cos(t)
    vn = (1 + e) * v * math.cos(t) / (1 + PENNER_M / PENNER_HEAD)
    vp = v * math.sin(t) / (1 + PENNER_M / PENNER_HEAD + 2.5)
    omega = PENNER_M * vp * r / (0.4 * PENNER_M * r * r)
    return math.hypot(vn, vp) / MPH, omega * 60 / (2 * math.pi)


def truth_launch(club, speed, path, attack, face, loft):
    impact = collision_forward.forward(
        club=club,
        club_speed_mph=speed,
        attack_angle_deg=attack,
        club_path_deg=path,
        face_angle_deg=face,
        dynamic_loft_deg=loft,
    )
    launch_h, launch_v, spin = (
        impact.launch_horizontal_deg,
        impact.launch_vertical_deg,
        impact.spin_rpm,
    )
    theta = spin_loft_deg(loft, face, attack, path)
    ball_speed, _ = penner(speed, theta)
    tilt = d_plane_tilt_deg(loft, face, attack, path)
    # irons: the clean-room TM4 iron fit on the D-plane tilt
    iron_axis = math.degrees(math.atan(math.tan(math.radians(tilt)) * (0.81385 + 0.00342 * loft)))
    return {
        "launch_h": launch_h,
        "launch_v": launch_v,
        "spin_loft": theta,
        "tilt": tilt,
        "axis": iron_axis if club is ClubType.IRON_7 else impact.centre_spin_axis_deg,
        "ball_speed": ball_speed,
        "spin": spin,
        "face_to_path": face - path,
    }


def _rotate(xyz, degrees):
    """Turn (forward, lateral, up) points about the vertical by ``degrees`` (+ toward right)."""
    a = math.radians(degrees)
    out = np.array(xyz, dtype=float)
    fwd, lat = out[..., 0].copy(), out[..., 1].copy()
    out[..., 0] = fwd * math.cos(a) - lat * math.sin(a)
    out[..., 1] = fwd * math.sin(a) + lat * math.cos(a)
    return out


def arc_truth(*, path, attack, plane_deg, radius, speed_mph, handed, impact_target):
    """Head positions on a circle with this path and attack at impact (target frame)."""
    side = -1.0 if handed == "LH" else 1.0
    theta = math.radians(plane_deg)
    phi_i = math.asin(math.sin(math.radians(attack)) / math.sin(theta))
    psi = math.radians(path) + side * math.atan(math.tan(phi_i) * math.cos(theta))
    e1 = np.array([math.cos(psi), math.sin(psi), 0.0])
    toward = side * np.array([math.sin(psi), -math.cos(psi), 0.0])
    e2 = math.cos(theta) * toward + np.array([0, 0, math.sin(theta)])
    omega = speed_mph * MPH / radius
    low = impact_target - radius * math.sin(phi_i) * e1 - radius * (1 - math.cos(phi_i)) * e2

    def at(t_s):
        phi = phi_i + omega * t_s
        return low + radius * math.sin(phi) * e1 + radius * (1 - math.cos(phi)) * e2

    times = [-0.0085, -0.0065, -0.0045, -0.0025, -0.0005]
    low_rel = low - impact_target
    return {
        "times": times,
        "points": np.array([at(t) for t in times]),
        "swing_direction": math.degrees(psi),
        "swing_plane": plane_deg,
        "low_point_mm": (low_rel[0] * 1000, low_rel[2] * 1000, low_rel[1] * 1000),
    }


def _ops_and_ball(rig, tee_range, radar_h, ball_h, side):
    ops = np.array(rig.ops_offset_mm, float) / 1000.0  # camera right, down, forward
    org = np.array(rig.iwr_origin_mm, float) / 1000.0
    ops_m = np.array([ops[2] - org[2], ops[0] - org[0], radar_h - ops[1] + org[1]])
    dz = ball_h - radar_h
    ball = np.array([math.sqrt(tee_range**2 - dz**2 - side**2), side, ball_h])
    return ops_m, ball


def ops_track(full_mph, launch_v, launch_h_boresight, ops_m, ball, impact_ms=40.0):
    """Radial readings of a ball leaving ``ball`` on a straight line, at window centres."""
    window = 128 / 30.0
    d = _unit(launch_v, launch_h_boresight)
    v0 = full_mph * MPH
    readings = []
    for i in range(6):
        start = impact_ms + 0.5 + 1.0667 * i
        t = (start + 0.5 * window - impact_ms) / 1000.0
        decel = 0.5 * 1.225 * 0.28 * math.pi * 0.02135**2 / 0.04593 * v0**2
        decel += 9.81 * math.sin(math.radians(launch_v))
        pos = ball + d * (v0 * t - 0.5 * decel * t * t)
        los = (pos - ops_m) / np.linalg.norm(pos - ops_m)
        readings.append([start, (v0 - decel * t) * float(los @ d) / MPH])
    return {
        "clock": "ops_capture",
        "impact_ms": impact_ms,
        "impact_source": "ops_transition",
        "impact_uncertainty_ms": 0.6,
        "window_ms": window,
        "radial_resolution_mph": 0.05,
        "canonical_radial_mph": max(speed for _, speed in readings),
        "outbound": readings,
    }


TEE_RANGE, BALL_H, SIDE = 2.10, 0.02135, 0.03


@pytest.fixture
def kiosk(monkeypatch):
    reading_speed = full_speed.evaluate_full_club_speed_head_track
    rig = RigGeometry.from_json(str(REPO / "config" / "enclosure_v3_rig_geometry.json"))
    radar_h = rig.enclosure_setup().radar_height_m
    calibration = SimpleNamespace(
        tee_range_m=TEE_RANGE, tee_ball_height_m=BALL_H, radar_height_m=radar_h
    )
    monkeypatch.setattr(server_module, "rig_geometry", rig)
    monkeypatch.setattr(server_module, "iwr6843_runtime", SimpleNamespace(calibration=calibration))
    scene = dict(server_module.setup_handoff_config.get("scene") or {})
    scene["tee_side_offset_m"] = SIDE
    monkeypatch.setitem(server_module.setup_handoff_config, "scene", scene)
    monkeypatch.setattr(server_module, "ball_speed_correction_enabled", False)
    monkeypatch.setattr(server_module, "ballistics_enabled", True)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

    def set_up(*, yaw, handed, impact_speed_mph):
        offset = offset_from_target_yaw(yaw) if yaw else None
        monkeypatch.setattr(server_module, "horizontal_offset_deg", offset)
        monkeypatch.setattr(server_module, "golfer_handed", handed)
        producer = reading_speed
        if impact_speed_mph is not None:

            def producer(**_kwargs):
                return {
                    "status": "calculated",
                    "value_mph": impact_speed_mph,
                    "referred_to": "impact",
                    "reference_point": "clubhead",
                    "source": "synthetic constant-angular-speed impact reference",
                }

        monkeypatch.setattr(full_speed, "evaluate_full_club_speed_head_track", producer)
        return rig, radar_h, offset

    return set_up


DELIVERIES = {
    # club, club speed, path, attack, face, dynamic loft
    "rh_7iron_draw": (ClubType.IRON_7, 87.0, 3.0, -4.5, 1.0, 21.0),
    "rh_driver_fade": (ClubType.DRIVER, 113.0, -2.0, 3.0, 1.0, 13.0),
}


def run_shot(kiosk, *, delivery, handed, yaw, measured_club=False, impact_reference=True):
    club, speed, path, attack, face, loft = delivery
    if handed == "LH":
        # a left-hander's in-to-out path is left of the target line: mirror the delivery
        path, face = -path, -face
    truth = truth_launch(club, speed, path, attack, face, loft)
    rig, radar_h, offset = kiosk(
        yaw=yaw, handed=handed, impact_speed_mph=speed if impact_reference else None
    )

    ops_m, ball_bore = _ops_and_ball(rig, TEE_RANGE, radar_h, BALL_H, SIDE)
    # the published prior's plane and radius: the arc fit recovers them exactly
    plane, radius = prior_for_club(club).plane_deg, prior_for_club(club).radius_m
    impact_target = _rotate(ball_bore, -yaw)
    arc = arc_truth(
        path=path,
        attack=attack,
        plane_deg=plane,
        radius=radius,
        speed_mph=speed,
        handed=handed,
        impact_target=impact_target,
    )
    bore_points = _rotate(arc["points"], yaw)
    delivery_dict = {
        "status": "chained_high" if measured_club else "rejected_low_path_consensus",
        "club_path_deg": round(path, 2) if measured_club else None,
        "attack_angle_deg": round(attack, 2) if measured_club else None,
        "club_path_frame": server_module._horizontal_frame(),
        "head_positions": [
            {"t_s": t, "forward_m": p[0], "lateral_m": p[1], "up_m": p[2]}
            for t, p in zip(arc["times"], bore_points)
        ],
        "contact_t_s": 0.0,
        "horizontal_offset_deg": offset,
    }
    launch_h_bore = truth["launch_h"] + yaw
    shot = Shot(
        ball_speed_mph=0.0,
        club_speed_mph=speed,
        timestamp=datetime(2026, 10, 13, 9, 0, 0),
        club=club,
        launch_angle_vertical=truth["launch_v"],
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=server_module._reported_horizontal_deg(launch_h_bore),
        launch_angle_horizontal_source="radar",
        launch_angle_horizontal_confidence=0.9,
        launch_angle_horizontal_frame=server_module._horizontal_frame(),
        mode="rolling-buffer",
        camera_lens_intrinsics="calibrated",
        inclinometer={"applied_to": ["camera", "iwr6843"]},
    )
    track = ops_track(truth["ball_speed"], truth["launch_v"], launch_h_bore, ops_m, ball_bore)
    shot.ball_speed_mph = track["canonical_radial_mph"]
    shot.ops_speed_track = track
    if measured_club:
        shot.experimental_fused_status = "chained_high"
        shot.experimental_fused_club_path_deg = delivery_dict["club_path_deg"]
        shot.experimental_fused_club_path_frame = delivery_dict["club_path_frame"]
        shot.experimental_fused_attack_angle_deg = delivery_dict["attack_angle_deg"]
        shot.experimental_fused_club_path_confidence = "high"
        shot.experimental_fused_attack_angle_confidence = "high"
    else:
        shot.experimental_fused_status = "rejected_low_path_consensus"
    server_module._open_smash_factor_status(shot)
    shot_models._attach_club_arc(shot, delivery_dict)
    shot_models._attach_collision_model(shot)
    server_module._finalize_shot_detected(shot, emit_event="shot")
    truth.update(arc=arc, path=path, attack=attack, face=face, loft=loft, speed=speed)
    return shot, truth


CASES = [
    pytest.param("rh_7iron_draw", "RH", 0.0, id="rh-7iron-yaw0"),
    pytest.param("rh_7iron_draw", "RH", 3.0, id="rh-7iron-yaw+3"),
    pytest.param("rh_7iron_draw", "RH", -3.0, id="rh-7iron-yaw-3"),
    pytest.param("rh_driver_fade", "RH", 3.0, id="rh-driver-yaw+3"),
    pytest.param("rh_driver_fade", "LH", -3.0, id="lh-driver-yaw-3"),
    pytest.param("rh_7iron_draw", "LH", 3.0, id="lh-7iron-yaw+3"),
]


@pytest.mark.parametrize("name, handed, yaw", CASES)
@pytest.mark.parametrize("measured_club", [False, True], ids=["arc", "chained"])
def test_a_known_delivery_comes_back_with_every_metric_and_label(
    kiosk, name, handed, yaw, measured_club
):
    shot, truth = run_shot(
        kiosk, delivery=DELIVERIES[name], handed=handed, yaw=yaw, measured_club=measured_club
    )
    frame = "target_line" if yaw else "unit_boresight"

    # club delivery: path, attack, swing plane and direction, low point
    arc = shot.club_delivery["metrics"]
    arc_label = arc["swing_plane_deg"]["status"]
    assert arc_label in ("calculated", "model_geometry")
    club_label = "measured" if measured_club else arc_label
    assert arc["club_path_deg"]["value"] == pytest.approx(truth["path"], abs=0.02)
    assert arc["club_path_deg"]["status"] == club_label
    assert arc["club_path_deg"]["frame"] == frame
    assert arc["attack_angle_deg"]["value"] == pytest.approx(truth["attack"], abs=0.02)
    assert arc["attack_angle_deg"]["status"] == club_label
    assert arc["swing_plane_deg"]["value"] == pytest.approx(truth["arc"]["swing_plane"], abs=0.02)
    assert arc["swing_direction_deg"]["value"] == pytest.approx(
        truth["arc"]["swing_direction"], abs=0.02
    )
    assert arc["swing_direction_deg"]["frame"] == frame
    distance, height, side = truth["arc"]["low_point_mm"]
    offset = LOW_POINT_OFFSETS_MM[DELIVERIES[name][0]]
    assert arc["low_point_distance_mm"]["geometry_value"] == pytest.approx(distance, abs=0.5)
    assert arc["low_point_distance_mm"]["value"] == pytest.approx(distance + offset, abs=0.5)
    assert arc["low_point_height_mm"]["value"] == pytest.approx(height, abs=0.5)
    assert arc["low_point_side_mm"]["value"] == pytest.approx(side, abs=0.5)
    assert distance > 0 if truth["attack"] < 0 else distance < 0  # TrackMan: down = after

    # face and collision, labelled by their weakest input
    collision_label = "calculated" if club_label in ("measured", "calculated") else "model_geometry"
    metrics = shot.collision_model["metrics"]
    for metric in ("face_angle", "dynamic_loft", "spin_loft", "face_to_path", "d_plane_tilt"):
        assert metrics[metric]["status"] == collision_label, metric
    assert shot.face_angle_deg == pytest.approx(truth["face"], abs=0.06)
    assert metrics["face_angle"]["frame"] == frame
    assert shot.dynamic_loft_deg == pytest.approx(truth["loft"], abs=0.06)
    assert shot.spin_loft_deg == pytest.approx(truth["spin_loft"], abs=0.06)
    assert shot.face_to_path_deg == pytest.approx(truth["face_to_path"], abs=0.06)
    assert math.copysign(1, shot.face_to_path_deg) == math.copysign(
        1, truth["face"] - truth["path"]
    )
    assert shot.d_plane_tilt_deg == pytest.approx(truth["tilt"], abs=0.06)
    assert math.copysign(1, shot.d_plane_tilt_deg) == math.copysign(1, truth["face_to_path"])
    assert shot.spin_rpm == pytest.approx(truth["spin"], abs=1.0)
    assert shot.spin_source == collision_label
    assert metrics["spin_rate"]["status"] == collision_label
    assert shot.spin_axis_deg == pytest.approx(truth["axis"], abs=0.06)
    assert metrics["spin_axis"]["status"] == collision_label
    assert metrics["gyro"]["status"] == "not_measured"

    # The ideal impact-speed input supports full smash; the radial ratio stays separate.
    full = shot.ball_speed_full
    assert full["status"] == "calculated", full.get("reason")
    assert full["value_mph"] == pytest.approx(truth["ball_speed"], abs=0.15)
    assert full["range_mph"][0] <= truth["ball_speed"] <= full["range_mph"][1]
    assert shot.ball_speed_full_mph == full["value_mph"]
    assert shot.smash_factor_full["status"] == "calculated"
    assert shot.smash_factor_full["value"] == pytest.approx(
        truth["ball_speed"] / truth["speed"], abs=0.002
    )
    assert shot.smash_factor_status == SMASH_RADIAL_RATIO

    # flight: both airs, every output, the flight's label is the weakest input's
    flight_label = collision_label
    expected = {}
    for block, density in (
        ("as_measured", AIR_DENSITY_STD),
        ("normalized", AIR_DENSITY_NORMALIZED),
    ):
        traj = simulate(
            LaunchConditions(
                truth["ball_speed"],
                truth["launch_v"],
                truth["launch_h"],
                truth["spin"],
                truth["axis"],
                "calculated",
            ),
            air_density=density,
        )
        expected[block] = traj
        got = shot.flight[block]["metrics"]
        assert shot.flight[block]["launch"]["launch_horizontal_deg"] == pytest.approx(
            truth["launch_h"], abs=1e-6
        )
        assert set(got) == set(FLIGHT_METRICS)
        assert got["carry"]["value"] == pytest.approx(traj.carry_yards, abs=0.6)
        assert got["carry_side"]["value"] == pytest.approx(traj.carry_side_yards, abs=0.3)
        assert got["apex"]["value"] == pytest.approx(traj.apex_yards, abs=0.2)
        assert got["landing_angle"]["value"] == pytest.approx(traj.landing_angle_deg, abs=0.2)
        assert got["hang_time"]["value"] == pytest.approx(traj.flight_time_s, abs=0.03)
        assert got["curve"]["value"] == pytest.approx(
            traj.carry_side_yards * math.cos(math.radians(truth["launch_h"]))
            - math.sqrt(traj.carry_yards**2 - traj.carry_side_yards**2)
            * math.sin(math.radians(truth["launch_h"])),
            abs=0.3,
        )
        # the ball curves the way the face points relative to the path
        assert math.copysign(1, got["curve"]["value"]) == math.copysign(1, truth["face_to_path"])
        for metric in FLIGHT_METRICS:
            if got[metric]["value"] is not None:
                assert got[metric]["status"] == flight_label, (block, metric)
    assert shot.flight["as_measured"]["launch"] == shot.flight["normalized"]["launch"]
    assert shot.carry_spin_adjusted == pytest.approx(
        shot.flight["as_measured"]["metrics"]["carry"]["value"]
    )
    assert shot.carry_status == flight_label


@pytest.mark.parametrize("measured_club", [False, True])
def test_reading_time_route_does_not_supply_the_ideal_impact_input(kiosk, measured_club):
    shot, _truth = run_shot(
        kiosk,
        delivery=DELIVERIES["rh_7iron_draw"],
        handed="RH",
        yaw=0,
        measured_club=measured_club,
        impact_reference=False,
    )
    assert shot.face_angle_deg is not None and shot.dynamic_loft_deg is not None
    assert shot.collision_model["inputs"]["club_speed_mph"]["value"] is None
    assert shot.spin_rpm is None and shot.spin_rpm_collision is None
    assert shot.smash_factor_full["status"] == "not_measured"


@pytest.mark.parametrize("handed", ["RH", "LH"])
def test_the_target_yaw_moves_no_target_line_output(kiosk, handed):
    results = [
        run_shot(kiosk, delivery=DELIVERIES["rh_7iron_draw"], handed=handed, yaw=yaw)[0]
        for yaw in (-3.0, 0.0, 3.0)
    ]
    fields = (
        "face_angle_deg",
        "dynamic_loft_deg",
        "spin_loft_deg",
        "face_to_path_deg",
        "d_plane_tilt_deg",
        "spin_rpm",
        "spin_axis_deg",
    )
    for name in fields:
        values = [getattr(shot, name) for shot in results]
        assert max(values) - min(values) <= 0.11, (name, values)
    for name in ("club_path_deg", "attack_angle_deg", "swing_direction_deg", "low_point_side_mm"):
        values = [shot.club_delivery["metrics"][name]["value"] for shot in results]
        assert max(values) - min(values) <= 0.05, (name, values)
    carries = [shot.flight["normalized"]["metrics"]["carry"]["value"] for shot in results]
    sides = [shot.flight["normalized"]["metrics"]["carry_side"]["value"] for shot in results]
    assert max(carries) - min(carries) <= 0.6
    assert max(sides) - min(sides) <= 0.3
