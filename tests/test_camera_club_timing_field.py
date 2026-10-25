"""Chained club delivery on a real dusk clip times frames by the sensor clock
(fork only: it loads field frames)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import openflight.camera.club_delivery as club_delivery_module
from openflight.camera.club_delivery import estimate_chained_delivery
from openflight.camera.club_motion import ReferenceBall
from tests.test_camera_club_timing import _geometry

FIXTURE = Path(__file__).parent / "fixtures" / "ball_departure" / "outdoors-test-5-dusk.npz"


def test_the_field_clips_contact_and_trigger_offset_use_sensor_time(monkeypatch):
    """Frame 18 arrives 31 ms late on the host clock."""
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
