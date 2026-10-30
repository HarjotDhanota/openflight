"""Shared contact timing must survive the arc adapter to the speed consumer."""

from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import club_arc, full_speed
from openflight.acoustic_timing import sound_timing_inputs
from openflight.clubs import ClubType
from tests.test_camera_head_track import BALL_LUF, CAMERA_HEIGHT_M, CONTACT_NS, Swing
from tests.test_camera_head_track_chain import _context, _run, no_stable_clubhead  # noqa: F401
from tests.test_full_speed_routes import CLUB_RIG, _club_track, _head_record


def _through_arc(head):
    return club_arc.club_delivery_payload(
        {"status": "camera_head_track", "head_track": head},
        club=ClubType.IRON_7,
        handedness="RH",
    )["head_track"]


def _speed(head, track):
    return full_speed.evaluate_full_club_speed_head_track(
        head_track=head, ops_track=track, rig=CLUB_RIG
    )


@pytest.mark.parametrize("delay_ms", [3.0, 7.0])
def test_shared_model_delay_is_applied_once_after_arc_handoff(delay_ms):
    swing = Swing()
    head = _head_record(swing)
    head["contact_timing"] = {
        "status": "model_interval",
        "gate_to_contact_delay_ns": [(delay_ms - 1) * 1e6, (delay_ms + 1) * 1e6],
        "physical_timing_qualified": False,
    }
    track = _club_track(swing, 8, impact_source="sound_trigger")
    track["impact_ms"] += delay_ms
    direct = _speed(head, track)
    adapted = _speed(_through_arc(head), track)

    assert direct["status"] == "experimental", direct["reason"]
    assert adapted == direct
    assert adapted["reading"]["impact_walk_back_ms"] == pytest.approx(delay_ms)
    assert adapted["reading"]["time_before_contact_ms"] == pytest.approx(8)


@pytest.mark.parametrize(
    "timing",
    [
        False,
        {},
        {"status": "unavailable"},
        {"status": "model_interval", "gate_to_contact_delay_ns": [8, 6]},
    ],
)
def test_explicit_timing_refusal_cannot_turn_into_legacy_delay_after_arc(timing):
    head = _head_record(Swing())
    head["contact_timing"] = timing
    track = _club_track(Swing(), 8, impact_source="sound_trigger")
    direct = _speed(head, track)

    assert direct["status"] == "not_measured"
    assert _speed(_through_arc(head), track) == direct


def test_missing_legacy_timing_stays_missing_and_does_not_change_the_old_route():
    head = _head_record(Swing())
    track = _club_track(Swing(), 8, impact_source="sound_trigger")
    assert _through_arc(head).get("contact_timing") is None
    assert _speed(_through_arc(head), track) == _speed(head, track)


@pytest.mark.usefixtures("no_stable_clubhead")
def test_actual_camera_contact_record_survives_the_adapter():
    swing = Swing()
    calibration = {
        "detector_delay_bounds_ns": [200_000, 200_000],
        "sound_speed_bounds_m_s": [343, 343],
        "path_error_bound_m": 0.0,
        "provenance": "synthetic swing truth only",
    }
    ball = BALL_LUF - np.array([0.0, CAMERA_HEIGHT_M, 0.0])
    delay_ns = np.linalg.norm(ball - np.array([-0.08, 0.0, 0.0])) / 343 * 1e9 + 200_000
    context = _context(
        swing,
        sound_timing=sound_timing_inputs(
            SimpleNamespace(mic_offset_mm=[-80, 0, 0]), calibration=calibration
        ),
        gate_interval_ns=(CONTACT_NS + delay_ns - 100_000, CONTACT_NS + delay_ns + 100_000),
    )
    delivery = _run(swing, head_track=context)
    assert delivery.status == "camera_head_track", delivery.notes
    payload = club_arc.club_delivery_payload(
        asdict(delivery), club=ClubType.IRON_7, handedness="RH"
    )
    assert payload["head_track"].get("contact_timing") == delivery.head_track["contact_timing"]
    track = _club_track(swing, 8, impact_source="sound_trigger")
    track["impact_ms"] += delay_ns / 1e6
    assert _speed(payload["head_track"], track) == _speed(delivery.head_track, track)
