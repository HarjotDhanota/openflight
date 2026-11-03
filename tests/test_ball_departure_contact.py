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
