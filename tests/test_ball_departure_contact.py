"""The ball-defined contact INSTANT (C13a): the camera fallback's departure fit, extrapolated
back to the resting ball, gives contact on the sensor clock with its own sigma.

Inside the gap between the last frame the ball rests in and the first it has moved in,
the contact instant was a guess (the gap's midpoint). The fallback's launch fit already
solves the time the ball left its resting position (its fourth parameter): it is now
recorded as the shot's contact, with the fit's standard deviation, the frame bracket and
the fraction inside it. A ball the frames after contact show by itself (before the
track's first point) is a second witness; when the head hides it the witness says so.
"""

from __future__ import annotations

import json

import pytest

from tests.camera_fallback_scene import render_scene
from tests.test_ball_launch_fallback import estimate


@pytest.mark.parametrize("before_trigger_ms", [0.4, 1.3, 2.0, 2.9])
def test_a_rendered_departure_gives_back_its_contact_instant(before_trigger_ms):
    scene = render_scene(contact_before_trigger_ms=before_trigger_ms)
    launch = estimate(scene)
    assert launch.status == "accepted", launch.reason

    contact = launch.contact
    assert contact["source"] == "ball_departure_extrapolation"
    assert abs(contact["sensor_ns"] - scene.contact_ns) < 0.2e6  # < 0.2 ms
    assert 0.0 < contact["sigma_ms"] < 0.5
    stamps = scene.sensor_timestamp_ns
    rest, moved = contact["last_rest_frame"], contact["first_moved_frame"]
    assert moved == rest + 1
    assert stamps[rest] <= contact["sensor_ns"] - scene.exposure_us * 500 <= stamps[moved]
    assert 0.0 <= contact["fraction"] <= 1.0
    json.dumps(contact)


def test_the_first_moved_frame_witness_is_recorded_or_says_why_not():
    scene = render_scene(contact_before_trigger_ms=2.0)
    witness = estimate(scene).contact["first_moved_witness"]

    assert witness["status"] in ("measured", "not_measured")
    if witness["status"] == "measured":
        assert abs(witness["sensor_ns"] - scene.contact_ns) < 0.5e6
    else:
        assert witness["reason"]


def test_a_withheld_launch_carries_no_contact():
    scene = render_scene(vertical_deg=16.0, speed_mph=90.0, contact_before_trigger_ms=-200.0)
    launch = estimate(scene)
    assert launch.status != "accepted"
    assert launch.contact is None


def test_the_acoustic_cross_check_is_recorded_beside_the_departure_instant():
    """C14 (C13 gap): the gate's sound edge minus the ball-to-mic flight time, +-1.5 ms, beside
    the departure fit's instant; a disagreement beyond 3 sigma labels the instant, never
    refuses it."""
    from openflight.camera import ball_launch_fallback as fallback  # noqa: PLC0415

    contact = {"status": "measured", "sensor_ns": 1_000_000_000.0, "sigma_ms": 0.14}
    # ball 1.789 m straight ahead of the mic: 5.216 ms of sound at 343 m/s
    check = fallback.acoustic_cross_check(
        contact,
        trigger_boottime_ns=1_000_000_000 + 5_216_000 + 882_295,
        gate_latency_ns=882_295,
        mic_camera_ruf_m=(-0.08, 0.0, 0.0),
        ball_lfu_m=(-0.08, 1.789, 0.068),
        origin_lfu_m=(0.0, 0.0, 0.068),
        speed_of_sound_m_s=343.0,
    )
    assert check["status"] == "measured"
    assert check["sensor_ns"] == pytest.approx(1_000_000_000.0, abs=2_000)
    assert check["sigma_ms"] == 1.5
    assert check["agrees_within_3_sigma"] is True
    late = fallback.acoustic_cross_check(
        contact,
        trigger_boottime_ns=1_000_000_000 + 5_216_000 + 882_295 + 6_000_000,
        gate_latency_ns=882_295,
        mic_camera_ruf_m=(-0.08, 0.0, 0.0),
        ball_lfu_m=(-0.08, 1.789, 0.068),
        origin_lfu_m=(0.0, 0.0, 0.068),
        speed_of_sound_m_s=343.0,
    )
    assert late["agrees_within_3_sigma"] is False
    missing = fallback.acoustic_cross_check(
        contact,
        trigger_boottime_ns=None,
        gate_latency_ns=None,
        mic_camera_ruf_m=None,
        ball_lfu_m=None,
        origin_lfu_m=None,
    )
    assert missing["status"] == "not_measured" and missing["reason"]
