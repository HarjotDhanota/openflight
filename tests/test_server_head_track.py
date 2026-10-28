"""The kiosk hands the club-delivery chain what its camera head-track fallback needs."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

import openflight.camera.club_delivery as club_delivery_module
from openflight import server
from openflight.camera.club_delivery import ChainedDelivery
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from tests.test_camera_trigger_timing import FRAME_NS, _fusion_archive, _live_server


def _capture(tmp_path, latency_ns=(700_000, 900_000)):
    metadata = {"trigger_clocks": {"gate_to_camera_callback_ns": list(latency_ns)}}
    return SimpleNamespace(valid=True, path=tmp_path, metadata=metadata)


def _archive():
    return _fusion_archive(
        sensor_timestamp_ns=np.arange(24, dtype=np.int64) * FRAME_NS,
        trigger_boottime_ns=np.asarray(160_000_000, dtype=np.int64),
        exposure_us=np.full(24, 68, dtype=np.int32),
    )


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    def club_estimator(*_args, **kwargs):
        seen.update(kwargs)
        return ChainedDelivery(status="rejected_no_stable_clubhead")

    monkeypatch.setattr(club_delivery_module, "estimate_chained_delivery", club_estimator)
    return seen


def test_the_live_path_hands_the_head_track_its_context(monkeypatch, tmp_path, captured):
    _live_server(monkeypatch, tmp_path)
    monkeypatch.setitem(
        server.setup_handoff_config, "target_line", {"status": "set", "total_uncertainty_deg": 1.7}
    )
    shot = Shot(ball_speed_mph=100.0, club_speed_mph=80.0, timestamp=datetime.now())
    shot.handed = "LH"
    shot.club = ClubType.IRON_7

    server._fuse_camera_club_delivery(shot, _capture(tmp_path), camera_archive=_archive())

    context = captured["head_track"]
    assert context.handedness == "LH"
    assert context.club == ClubType.IRON_7
    assert context.yaw_sigma_deg == pytest.approx(1.7)
    assert context.contact_window_ns is None
    assert context.sound_timing["calibration"] is None
    assert context.legacy_sound_model is False
    assert context.exposure_us.tolist() == [68] * 24


@pytest.mark.parametrize("source", ["sound_gate", "ops_speed_trigger"])
def test_live_head_context_keeps_recorded_trigger_source(monkeypatch, tmp_path, captured, source):
    _live_server(monkeypatch, tmp_path)
    shot = Shot(ball_speed_mph=100.0, club_speed_mph=80.0, timestamp=datetime.now())
    archive = {**_archive(), "trigger_source": np.str_(source)}
    server._fuse_camera_club_delivery(shot, _capture(tmp_path), camera_archive=archive)
    context = captured["head_track"]
    assert context.trigger_source == source
    assert context.contact_window_ns is None
    assert context.legacy_sound_model is False


def test_without_an_iwr_the_shots_own_ball_range_still_runs_the_chain(
    monkeypatch, tmp_path, captured
):
    _live_server(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "iwr6843_runtime", None)
    shot = Shot(ball_speed_mph=100.0, club_speed_mph=80.0, timestamp=datetime.now())
    shot.ball_range_m = 1.9

    server._fuse_camera_club_delivery(shot, _capture(tmp_path), camera_archive=_archive())

    assert captured["geometry"].tee_range_m == pytest.approx(1.9)
    assert captured["range_evidence"] is None
    assert shot.experimental_fused_status == "rejected_no_stable_clubhead"


def test_without_an_iwr_or_a_ball_range_the_rejection_stands(monkeypatch, tmp_path, captured):
    _live_server(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "iwr6843_runtime", None)
    shot = Shot(ball_speed_mph=100.0, club_speed_mph=80.0, timestamp=datetime.now())

    server._fuse_camera_club_delivery(shot, _capture(tmp_path), camera_archive=_archive())

    assert captured == {}
    assert shot.experimental_fused_status == "rejected_no_ball_range"


def test_live_sound_contact_has_no_bounded_window_without_detector_delay_calibration(
    monkeypatch, tmp_path, captured
):
    _live_server(monkeypatch, tmp_path)
    shot = Shot(ball_speed_mph=100.0, club_speed_mph=80.0, timestamp=datetime.now())
    archive = _archive()
    archive["gate_boottime_interval_ns"] = np.array([159_000_000, 159_400_000])

    server._fuse_camera_club_delivery(shot, _capture(tmp_path), camera_archive=archive)

    # The old 0.5-4 ms total delay does not even cover sound travel at a 2 m tee.
    assert captured["head_track"].contact_window_ns is None
    assert captured["head_track"].gate_interval_ns == (159_000_000, 159_400_000)
