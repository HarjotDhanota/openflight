"""E1 in the kiosk: every shot's club delivery rides on Shot and the payload."""

from __future__ import annotations

import math
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server as server_module
from openflight.club_arc import MEASURED, MODEL_GEOMETRY, NOT_MEASURED
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from openflight.server import shot_to_dict


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


def _shot(**kwargs):
    return Shot(ball_speed_mph=100.0, timestamp=datetime.now(), shot_number=4, **kwargs)


def test_a_new_shot_has_no_club_delivery_until_the_camera_runs():
    shot = _shot()
    assert shot.club_delivery is None
    assert shot.to_dict()["club_delivery"] is None


def test_attach_reads_the_fused_delivery_with_the_shot_s_club(monkeypatch):
    monkeypatch.setattr(server_module.sim_player_state, "handed", "RH")
    shot = _shot(club=ClubType.IRON_7)
    fused = {
        "status": "approach_path_only",
        "club_path_deg": 2.0,
        "attack_angle_deg": None,
        "path_confidence_tier": "medium",
        "attack_confidence_tier": "withheld",
        "club_path_frame": "unit_boresight",
        "head_positions": _arc_positions(),
        "contact_t_s": 0.0,
        "horizontal_offset_deg": 0.0,
    }

    server_module._attach_club_arc(shot, fused)

    metrics = shot.club_delivery["metrics"]
    assert metrics["club_path_deg"]["status"] == MEASURED
    assert metrics["club_path_deg"]["value"] == pytest.approx(2.0)
    assert metrics["attack_angle_deg"]["status"] == MODEL_GEOMETRY
    assert metrics["attack_angle_deg"]["value"] < 0.0
    assert metrics["swing_plane_deg"]["value"] == pytest.approx(57.0, abs=1.0)
    assert shot.club_delivery["arc"]["prior"]["club"] == "7-iron"
    payload = shot_to_dict(shot)["club_delivery"]
    assert payload["metrics"]["swing_plane_deg"]["status"] == MODEL_GEOMETRY
    assert payload["handedness"] == "RH"


def test_attach_takes_the_golfer_s_handedness(monkeypatch):
    monkeypatch.setattr(server_module.sim_player_state, "handed", "LH")
    shot = _shot()

    server_module._attach_club_arc(shot, None)

    assert shot.club_delivery["handedness"] == "LH"


def test_the_fallback_club_delivery_attaches_a_not_measured_arc(monkeypatch):
    monkeypatch.setattr(server_module.sim_player_state, "handed", "RH")
    shot = _shot()

    server_module._fuse_camera_club_delivery(shot, None)

    assert shot.experimental_fused_status == "rejected_no_camera_capture"
    plane = shot.club_delivery["metrics"]["swing_plane_deg"]
    assert plane["status"] == NOT_MEASURED
    assert "rejected_no_camera_capture" in plane["reason"]


def test_withheld_camera_metrics_withhold_the_arc_too(monkeypatch):
    shot = _shot()
    shot.club_delivery = {"stale": True}

    server_module._withhold_camera_metrics(shot, "rejected_strip_offset_not_modelled", "strip")

    for metric in shot.club_delivery["metrics"].values():
        assert metric["status"] == NOT_MEASURED
        assert "rejected_strip_offset_not_modelled" in metric["reason"]


def test_live_camera_fusion_attaches_the_arc_from_the_shared_core(monkeypatch, tmp_path):
    np.savez(
        tmp_path / "frames.npz",
        frames=np.zeros((20, 6, 8), dtype=np.uint8),
        host_timestamp_ns=np.arange(20, dtype=np.int64),
        trigger_host_timestamp_ns=np.int64(10),
        pre_trigger_count=np.int32(10),
    )
    capture = SimpleNamespace(valid=True, path=tmp_path, metadata={})
    calibration = SimpleNamespace(tee_range_m=1.5, radar_height_m=0.051, tee_ball_height_m=0.021)
    monkeypatch.setattr(
        server_module, "camera_capture_runtime", SimpleNamespace(camera_analysis_eligible=True)
    )
    monkeypatch.setattr(server_module, "iwr6843_runtime", SimpleNamespace(calibration=calibration))
    monkeypatch.setattr(
        server_module,
        "camera_capture_config",
        {"mount_height_m": 0.095, "width": 8, "height": 6, "forward_offset_m": 0.03},
    )
    calls = []
    monkeypatch.setattr(
        server_module,
        "_fuse_camera_club_delivery",
        lambda *_args, **_kwargs: calls.append("fallback"),
    )
    shot = _shot()
    shot.camera_fusion_session_uuid = "session-a"

    server_module._fuse_camera_measurements(shot, capture)

    status = shot.camera_fusion_processing["club_delivery"]["status"]
    assert calls == []
    assert shot.club_delivery["cross_check"]["chained"]["status"] == status
    assert shot.club_delivery["metrics"]["club_path_deg"]["status"] == NOT_MEASURED
