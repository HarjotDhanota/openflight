"""E1 wiring: the chained delivery hands its head positions to the arc fit.

A synthetic arc is projected through a level rear camera; the IWR club range is
the head's own distance from the radar. The chained estimator's per-frame head
positions must land on that arc, on the camera clock, with the contact time.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pytest

import openflight.camera.club_delivery as club_delivery_module
from openflight.camera.club_delivery import CameraDeliveryGeometry, estimate_chained_delivery
from openflight.camera.club_motion import ReferenceBall
from openflight.club_arc import MEASURED, MODEL_GEOMETRY, club_delivery_payload
from openflight.clubs import ClubType
from openflight.iwr6843.club import ClubRangeEvidence

CAMERA_HEIGHT_M, RADAR_HEIGHT_M, BALL_HEIGHT_M = 0.095, 0.051, 0.021335
FOCAL_PX, WIDTH, HEIGHT = 933.33, 640, 400
BALL = np.array([0.0, 1.30, BALL_HEIGHT_M])  # lateral, forward, up
IMPACT_FRAME = 40
FRAME_NS = 2_000_000
CONTACT_S = (IMPACT_FRAME + 0.5) * FRAME_NS / 1e9
PLANE_DEG, DIRECTION_DEG, RADIUS_M, SPEED_MS, T_LOW_S = 57.0, 1.5, 1.0, 38.0, 0.002


def _head(rel_s: float) -> np.ndarray:
    """The synthetic head (lateral, forward, up) ``rel_s`` after contact (right-handed)."""
    psi, theta = math.radians(DIRECTION_DEG), math.radians(PLANE_DEG)
    e1 = np.array([math.sin(psi), math.cos(psi), 0.0])
    toward_golfer = np.array([-math.cos(psi), math.sin(psi), 0.0])
    e2 = math.cos(theta) * toward_golfer + np.array([0.0, 0.0, math.sin(theta)])

    def on_arc(t_s):
        phi = SPEED_MS / RADIUS_M * (t_s - T_LOW_S)
        return RADIUS_M * math.sin(phi) * e1 + RADIUS_M * (1.0 - math.cos(phi)) * e2

    impact = BALL - np.array([0.0, 0.04, 0.0])
    return impact + on_arc(rel_s) - on_arc(0.0)


def _project(point: np.ndarray) -> np.ndarray:
    camera = np.array([0.0, 0.0, CAMERA_HEIGHT_M])
    delta = point - camera
    return np.array(
        [WIDTH / 2 + FOCAL_PX * delta[0] / delta[1], HEIGHT / 2 - FOCAL_PX * delta[2] / delta[1]]
    )


def _frame_rel_s(index: int) -> float:
    return index * FRAME_NS / 1e9 - CONTACT_S


def _scene(monkeypatch):
    camera = np.array([0.0, 0.0, CAMERA_HEIGHT_M])
    ball_px = _project(BALL)
    ball = ReferenceBall(
        x=float(ball_px[0]),
        y=float(ball_px[1]),
        diameter_px=FOCAL_PX * 0.04267 / float(np.linalg.norm(BALL - camera)),
        area_px=400,
    )
    offsets = [np.array([dx, 0.0, dz]) for dx in (-0.01, 0.0, 0.01) for dz in (-0.008, 0.0, 0.008)]

    def pair_tracks(_frames, _background, _ball, *, first_idx, second_idx, **_kwargs):
        tracks = [
            [_project(_head(_frame_rel_s(index)) + offset) for index in (first_idx, second_idx)]
            for offset in offsets
        ]
        return np.asarray(tracks), 6.0

    monkeypatch.setattr(club_delivery_module, "_clubhead_pair_tracks", pair_tracks)
    monkeypatch.setattr(
        club_delivery_module,
        "_detect_impact_index",
        lambda _frames, _ball, trigger_index: IMPACT_FRAME,
    )
    radar = np.array([0.0, 0.0, RADAR_HEIGHT_M])
    impact_t_s = 0.02
    evidence = ClubRangeEvidence(
        track=SimpleNamespace(
            range_at=lambda t_s, _res: float(np.linalg.norm(_head(t_s - impact_t_s) - radar))
        ),
        geometry=SimpleNamespace(range_res_m=0.046875),
        impact_t_s=impact_t_s,
        status="accepted",
    )
    geometry = CameraDeliveryGeometry(
        camera_height_m=CAMERA_HEIGHT_M,
        radar_height_m=RADAR_HEIGHT_M,
        tee_range_m=float(np.linalg.norm(BALL - radar)),
        ball_height_m=BALL_HEIGHT_M,
        image_width_px=WIDTH,
        image_height_px=HEIGHT,
    )
    return ball, evidence, geometry


def _run(monkeypatch, **geometry_overrides):
    ball, evidence, geometry = _scene(monkeypatch)
    from dataclasses import replace  # noqa: PLC0415

    return estimate_chained_delivery(
        np.full((60, HEIGHT, WIDTH), 150, dtype=np.uint8),
        np.arange(60, dtype=np.int64) * FRAME_NS,
        trigger_index=IMPACT_FRAME,
        range_evidence=evidence,
        geometry=replace(geometry, **geometry_overrides),
        ops_club_speed_mph=SPEED_MS * 2.23694,
        reference_ball=ball,
    )


def test_chained_delivery_records_head_positions_on_the_camera_clock(monkeypatch):
    result = _run(monkeypatch)

    assert result.contact_t_s == pytest.approx(CONTACT_S)
    assert result.clock == "host_timestamp_ns"
    frames = sorted(round(p.t_s * 1e9 / FRAME_NS) for p in result.head_positions)
    assert frames == [36, 37, 38, 39, 40, 41]
    for position in result.head_positions:
        truth = _head(position.t_s - CONTACT_S)
        measured = np.array([position.lateral_m, position.forward_m, position.up_m])
        assert np.linalg.norm(measured - truth) < 0.003
    assert result.ball_position["forward_m"] == pytest.approx(BALL[1], abs=1e-6)


def test_the_arc_reads_the_shot_s_own_positions_and_keeps_the_chained_value(monkeypatch):
    result = _run(monkeypatch)
    dt = 1e-6
    velocity = _head(dt) - _head(-dt)
    truth_path = math.degrees(math.atan2(velocity[0], velocity[1]))
    truth_attack = math.degrees(math.atan2(velocity[2], math.hypot(velocity[0], velocity[1])))

    payload = club_delivery_payload(asdict(result), club=ClubType.IRON_7, handedness="RH")

    arc = payload["cross_check"]["arc"]
    assert arc["club_path_deg"] == pytest.approx(truth_path, abs=0.5)
    assert arc["attack_angle_deg"] == pytest.approx(truth_attack, abs=0.5)
    # 5 pre-impact frames at the 5 mm noise floor do not fix the plane alone
    assert payload["metrics"]["swing_plane_deg"]["status"] == MODEL_GEOMETRY
    assert payload["metrics"]["club_path_deg"]["status"] == MEASURED
    assert payload["metrics"]["club_path_deg"]["value"] == result.club_path_deg
    assert payload["arc"]["n_positions"] == 5  # the post-impact frame is left out
    assert payload["arc"]["low_point_from_ball_mm"] is not None


def test_the_horizontal_offset_reaches_the_arc(monkeypatch):
    boresight = club_delivery_payload(
        asdict(_run(monkeypatch)), club=ClubType.IRON_7, handedness="RH"
    )
    turned = club_delivery_payload(
        asdict(_run(monkeypatch, horizontal_offset_deg=2.0)), club=ClubType.IRON_7, handedness="RH"
    )

    assert turned["cross_check"]["arc"]["club_path_deg"] == pytest.approx(
        boresight["cross_check"]["arc"]["club_path_deg"] + 2.0, abs=0.05
    )
    assert turned["metrics"]["swing_direction_deg"]["frame"] == "target_line"
    assert turned["metrics"]["club_path_deg"]["frame"] == "target_line"


def test_camera_ops_fallback_has_no_head_positions(monkeypatch):
    ball, _evidence, geometry = _scene(monkeypatch)
    result = estimate_chained_delivery(
        np.full((60, HEIGHT, WIDTH), 150, dtype=np.uint8),
        np.arange(60, dtype=np.int64) * FRAME_NS,
        trigger_index=IMPACT_FRAME,
        range_evidence=None,
        geometry=geometry,
        ops_club_speed_mph=SPEED_MS * 2.23694,
        reference_ball=ball,
    )

    assert result.status == "camera_ops_fallback"
    assert result.head_positions == ()
    payload = club_delivery_payload(asdict(result), club=ClubType.IRON_7, handedness="RH")
    assert payload["metrics"]["swing_plane_deg"]["status"] == "not_measured"
    assert "camera_ops_fallback" in payload["metrics"]["swing_plane_deg"]["reason"]
