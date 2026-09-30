"""Chained club delivery times frames by the sensor clock where the clip has it (P6-8).

Host arrival times are distorted by up to 31 ms around impact when frame
delivery stalls (Outdoors-test-5); sensor timestamps are when each frame was
exposed. Club delivery links camera and radar only through the contact
anchor (radar impact time plus camera time since the camera's contact), so
moving every camera time to one clock needs no conversion to the host clock.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import openflight.camera.club_delivery as club_delivery_module
from openflight import server
from openflight.camera import fusion_processing as fp
from openflight.camera.ball_flight import CameraBallEstimate
from openflight.camera.club_delivery import (
    ApproachPairEstimate,
    CameraDeliveryGeometry,
    ChainedDelivery,
    estimate_chained_delivery,
)
from openflight.camera.club_motion import ReferenceBall
from openflight.launch_monitor import Shot

FRAME_NS = 8_685_000
LATENCY_NS = 9_500_000
FIXTURE = Path(__file__).parent / "fixtures" / "ball_departure" / "outdoors-test-5-dusk.npz"


def _clocks(count=60, *, stall_at=None):
    sensor = 1_000_000_000 + np.arange(count, dtype=np.int64) * FRAME_NS
    host = sensor + LATENCY_NS
    if stall_at is not None:
        # The field shape: the stalled frame waits ~31 ms, the next ones less.
        for offset, delay_ms in enumerate((31.3, 24.5, 17.6, 10.5, 3.9)):
            host[stall_at + offset] += int(delay_ms * 1e6)
    assert np.all(np.diff(host) > 0)
    return sensor, host


def _geometry():
    return CameraDeliveryGeometry(
        camera_height_m=0.2032,
        radar_height_m=0.1524,
        tee_range_m=1.524,
        ball_height_m=0.04,
        image_width_px=20,
        image_height_px=20,
    )


def _patch_scene(monkeypatch, seen):
    ball = ReferenceBall(10.0, 10.0, 12.0, 120)
    monkeypatch.setattr(
        club_delivery_module, "detect_reference_ball", lambda _frames, **_kwargs: ball
    )
    monkeypatch.setattr(
        club_delivery_module, "_detect_impact_index", lambda _frames, _ball, trigger_index: 40
    )
    monkeypatch.setattr(
        club_delivery_module,
        "_clubhead_pair_tracks",
        lambda *_args, **_kwargs: (np.zeros((12, 2, 2)), 5.0),
    )

    def timed_estimate(_tracks, camera_times_s, *rest, **_kwargs):
        ranges = rest[0] if rest and isinstance(rest[0], np.ndarray) else None
        seen.append((tuple(camera_times_s), None if ranges is None else tuple(ranges)))
        elapsed_ms = float(camera_times_s[1] - camera_times_s[0]) * 1000.0
        return ApproachPairEstimate(elapsed_ms / 10.0, -elapsed_ms / 5.0, 1.0, 1.0, 12)

    monkeypatch.setattr(club_delivery_module, "_delivery_from_feature_pair", timed_estimate)
    monkeypatch.setattr(
        club_delivery_module, "camera_ops_delivery_from_feature_pair", timed_estimate
    )


def _radar():
    from openflight.iwr6843.club import ClubRangeEvidence  # noqa: PLC0415

    return ClubRangeEvidence(
        track=SimpleNamespace(range_at=lambda t_s, _res: 1.2 + t_s),
        geometry=SimpleNamespace(range_res_m=0.046875),
        impact_t_s=0.5,
    )


@pytest.mark.parametrize("range_evidence", [None, "radar"], ids=["camera_ops", "camera_iwr"])
def test_an_arrival_stall_leaves_the_delivery_unchanged(monkeypatch, range_evidence):
    seen = []
    _patch_scene(monkeypatch, seen)
    evidence = _radar() if range_evidence else None
    frames = np.full((60, 20, 20), 150, dtype=np.uint8)
    sensor, clean = _clocks()
    _, stalled = _clocks(stall_at=40)
    kwargs = {
        "trigger_index": 40,
        "range_evidence": evidence,
        "geometry": _geometry(),
        "ops_club_speed_mph": 80.0,
    }

    steady = estimate_chained_delivery(frames, clean, sensor_timestamp_ns=sensor, **kwargs)
    steady_times = list(seen)
    seen.clear()
    during_stall = estimate_chained_delivery(frames, stalled, sensor_timestamp_ns=sensor, **kwargs)

    assert during_stall == steady
    assert seen == steady_times
    assert steady_times[0][0] == pytest.approx(tuple(sensor[[38, 41]] / 1e9))
    assert steady.status != "rejected_no_impact"


def test_a_clip_without_sensor_timestamps_still_times_by_arrival(monkeypatch):
    seen = []
    _patch_scene(monkeypatch, seen)
    _, stalled = _clocks(stall_at=40)

    estimate_chained_delivery(
        np.full((60, 20, 20), 150, dtype=np.uint8),
        stalled,
        trigger_index=40,
        range_evidence=None,
        geometry=_geometry(),
        ops_club_speed_mph=80.0,
    )

    assert seen[0][0] == pytest.approx(tuple(stalled[[38, 41]] / 1e9))


def test_the_field_clips_contact_and_trigger_offset_use_sensor_time(monkeypatch):
    """Swing 1 of Outdoors-test-5: frame 18 arrived 31 ms late on the host clock."""
    with np.load(FIXTURE) as archive:
        frames = archive["s1_frames"]
        sensor = archive["s1_sensor_timestamp_ns"].astype(np.int64)
        host = archive["s1_host_timestamp_ns"].astype(np.int64)
        x0, y0 = archive["s1_crop_offset_xy"]
        bx, by = archive["s1_ball_xy"]
    ball = ReferenceBall(float(bx - x0), float(by - y0), 21.0, 346)
    seen = {}
    real_contact = club_delivery_module.camera_contact_time

    def contact_spy(frames_, timestamps_ns, ball_, **kwargs):
        seen["timestamps"] = np.asarray(timestamps_ns)
        seen["source"] = kwargs.get("timestamp_source")
        return real_contact(frames_, timestamps_ns, ball_, **kwargs)

    monkeypatch.setattr(club_delivery_module, "camera_contact_time", contact_spy)
    monkeypatch.setattr(club_delivery_module, "SCENE_P995_MIN", 0.0)  # dusk frames
    monkeypatch.setattr(
        club_delivery_module, "_clubhead_pair_tracks", lambda *_args, **_kwargs: (None, None)
    )

    result = estimate_chained_delivery(
        frames,
        host,
        sensor_timestamp_ns=sensor,
        trigger_index=17,
        range_evidence=None,
        geometry=_geometry(),
        ops_club_speed_mph=60.0,
        reference_ball=ball,
    )

    assert seen["source"] == "sensor_timestamp_ns"
    np.testing.assert_array_equal(seen["timestamps"], sensor)
    assert result.impact_frame == 18
    assert result.impact_vs_trigger_ms == pytest.approx((sensor[18] - sensor[17]) / 1e6, abs=0.01)
    assert (host[18] - host[17]) / 1e6 > 35.0  # what the host clock would have said


def test_fusion_replay_passes_the_clips_sensor_timestamps_to_club_delivery(monkeypatch):
    from tests.test_camera_trigger_timing import (  # noqa: PLC0415
        _fusion_archive,
        _fusion_context,
    )

    seen = {}

    def club_estimator(*_args, **kwargs):
        seen["sensor"] = kwargs.get("sensor_timestamp_ns")
        return ChainedDelivery(status="rejected_test")

    monkeypatch.setattr(
        fp, "estimate_camera_ball_flight", lambda *_a, **_k: CameraBallEstimate("rejected_test")
    )
    monkeypatch.setattr(fp, "estimate_chained_delivery", club_estimator)
    sensor = np.arange(24, dtype=np.int64) * FRAME_NS

    fp.process_camera_fusion(_fusion_context(), _fusion_archive(sensor_timestamp_ns=sensor))
    np.testing.assert_array_equal(seen["sensor"], sensor)

    fp.process_camera_fusion(_fusion_context(), _fusion_archive())
    assert seen["sensor"] is None


def test_the_live_path_passes_the_clips_sensor_timestamps_to_club_delivery(monkeypatch, tmp_path):
    from tests.test_camera_trigger_timing import (  # noqa: PLC0415
        _fusion_archive,
        _live_server,
    )

    _live_server(monkeypatch, tmp_path)
    seen = {}

    def club_estimator(*_args, **kwargs):
        seen["sensor"] = kwargs.get("sensor_timestamp_ns")
        return ChainedDelivery(status="rejected_test")

    monkeypatch.setattr(club_delivery_module, "estimate_chained_delivery", club_estimator)
    sensor = np.arange(24, dtype=np.int64) * FRAME_NS
    shot = Shot(ball_speed_mph=100.0, club_speed_mph=80.0, timestamp=datetime.now())

    server._fuse_camera_club_delivery(
        shot,
        SimpleNamespace(valid=True, path=tmp_path),
        camera_archive=_fusion_archive(sensor_timestamp_ns=sensor),
    )

    np.testing.assert_array_equal(seen["sensor"], sensor)
