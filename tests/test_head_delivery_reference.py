"""Unbound conditional head models stay diagnostic, without public delivery values."""

from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import club_arc, full_speed
from openflight.camera.head_track import HeadTrackContext
from openflight.clubs import ClubType
from tests.test_camera_head_track import CONTACT_NS, Swing
from tests.test_camera_head_track_chain import _context, _run, no_stable_clubhead  # noqa: F401
from tests.test_full_speed_routes import CLUB_RIG, _club_track

pytestmark = pytest.mark.usefixtures("no_stable_clubhead")


@pytest.mark.parametrize(
    "stamp,clock",
    [
        (None, None),
        (CONTACT_NS - 8_000_000, "sensor_timestamp_ns"),
        (CONTACT_NS, "host_timestamp_ns"),
    ],
)
def test_unbound_head_geometry_does_not_publish_delivery_or_arc_points(stamp, clock):
    swing = Swing()
    values = asdict(
        HeadTrackContext(
            contact_window_ns=(CONTACT_NS - 1e6, CONTACT_NS + 1e6),
            exposure_us=np.full(24, swing.exposure_us),
            handedness=swing.handed,
            club=ClubType.IRON_7,
        )
    )
    values.update(ops_speed_time_ns=stamp, ops_speed_clock=clock)
    delivery = _run(swing, head_track=SimpleNamespace(**values))

    assert delivery.status == "rejected_no_ops_reference"
    assert delivery.club_path_deg is None
    assert delivery.attack_angle_deg is None
    assert delivery.speed_mph is None
    assert delivery.head_positions == ()
    assert len(delivery.head_track["track"]) >= 2
    assert delivery.head_track["speed_reference"]["status"] == "unbound"
    assert any("OPS speed reference" in note for note in delivery.notes)


def test_explicit_reference_survives_the_arc_handoff_to_full_speed():
    swing = Swing()
    delivery = _run(swing, head_track=_context(swing))
    payload = club_arc.club_delivery_payload(
        asdict(delivery), club=ClubType.IRON_7, handedness="RH"
    )
    assert payload["head_track"]["speed_reference"] == delivery.head_track["speed_reference"]
    result = full_speed.evaluate_full_club_speed_head_track(
        ops_track=_club_track(swing, 8), head_track=payload["head_track"], rig=CLUB_RIG
    )
    assert result["status"] == "experimental", result["reason"]
