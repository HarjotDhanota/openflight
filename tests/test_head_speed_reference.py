"""Full club speed cannot use head geometry scaled by an unbound OPS scalar."""

from copy import deepcopy
from dataclasses import replace

import pytest

from openflight import full_speed
from openflight.camera import head_track as ht
from tests.test_camera_head_track import CONTACT_NS, Swing, _inputs
from tests.test_full_speed_routes import CLUB_RIG, _club_track, _head_record


@pytest.mark.parametrize("reference", [None, {}, {"status": "unbound"}])
def test_unbound_head_geometry_cannot_supply_full_club_speed(reference):
    head = _head_record(Swing())
    head["speed_reference"] = reference

    result = full_speed.evaluate_full_club_speed_head_track(
        ops_track=_club_track(Swing(), 8), head_track=head, rig=CLUB_RIG
    )

    assert result["status"] == "not_measured"
    assert result["value_mph"] is None
    assert "head geometry's OPS speed reference" in result["reason"]


@pytest.mark.parametrize(
    "change",
    [
        {"timestamp_ns": CONTACT_NS - 8_000_000},
        {"clock": "host_timestamp_ns"},
        {"timestamp_ns": float("nan")},
        {"referred_to": "reading"},
        {"method": "unknown_projection"},
    ],
)
def test_a_reference_label_cannot_hide_the_wrong_time_clock_or_geometry(change):
    head = _head_record(Swing())
    head["speed_reference"] = {
        "status": "explicit_contact_model",
        "clock": "sensor_timestamp_ns",
        "timestamp_ns": CONTACT_NS,
        "referred_to": "contact",
        "method": "camera_to_ball_radial",
        **change,
    }

    result = full_speed.evaluate_full_club_speed_head_track(
        ops_track=_club_track(Swing(), 8), head_track=head, rig=CLUB_RIG
    )

    assert result["status"] == "not_measured"
    assert "head geometry's OPS speed reference" in result["reason"]


def test_an_explicit_contact_reference_keeps_the_existing_experimental_speed():
    head = _head_record(Swing())
    head["speed_reference"] = {
        "status": "explicit_contact_model",
        "clock": "sensor_timestamp_ns",
        "timestamp_ns": CONTACT_NS,
        "referred_to": "contact",
        "method": "camera_to_ball_radial",
    }
    original = deepcopy(head)

    result = full_speed.evaluate_full_club_speed_head_track(
        ops_track=_club_track(Swing(), 8), head_track=head, rig=CLUB_RIG
    )

    assert result["status"] == "experimental"
    assert result["value_mph"] > 0
    assert head == original
    assert result["referred_to"] == "the reading's time"


def test_head_producer_retains_the_actual_speed_reference_without_inventing_one():
    inputs = _inputs(Swing())
    inputs = replace(inputs, ops_speed_time_ns=None, ops_speed_clock=None)
    result = ht.estimate_head_track(inputs)

    reference = result.to_dict()["speed_reference"]
    assert reference["status"] == "unbound"
    assert reference["timestamp_ns"] is None
    assert reference["clock"] is None
    assert reference["referred_to"] is None
    assert any("untimed" in note for note in result.notes)


@pytest.mark.parametrize(
    "stamp,clock,stored",
    [
        (CONTACT_NS - 8_000_000, "sensor_timestamp_ns", CONTACT_NS - 8_000_000),
        (CONTACT_NS, "host_timestamp_ns", CONTACT_NS),
        (float("nan"), "sensor_timestamp_ns", None),
    ],
)
def test_head_producer_keeps_a_wrong_reference_unbound(stamp, clock, stored):
    inputs = replace(_inputs(Swing()), ops_speed_time_ns=stamp, ops_speed_clock=clock)
    reference = ht.estimate_head_track(inputs).to_dict()["speed_reference"]
    assert reference["status"] == "unbound"
    assert reference["timestamp_ns"] == stored
    assert reference["clock"] == clock
    assert reference["referred_to"] is None


def test_independent_contact_radial_generator_has_an_explicit_model_reference():
    reference = ht.estimate_head_track(_inputs(Swing())).to_dict()["speed_reference"]
    assert reference["status"] == "explicit_contact_model"
    assert reference["timestamp_ns"] == CONTACT_NS
    assert reference["clock"] == "sensor_timestamp_ns"
    assert reference["referred_to"] == "contact"
    assert reference["physical_timing_qualified"] is False


@pytest.mark.parametrize("offset_ns", [-250_000, 250_000])
def test_speed_timestamp_within_contact_interval_is_retained(offset_ns):
    inputs = replace(_inputs(Swing()), ops_speed_time_ns=CONTACT_NS + offset_ns)
    reference = ht.estimate_head_track(inputs).to_dict()["speed_reference"]
    assert reference["status"] == "explicit_contact_model"
    assert reference["timestamp_ns"] == CONTACT_NS + offset_ns
    assert reference["contact_interval_ns"] == [CONTACT_NS - 1e6, CONTACT_NS + 1e6]


@pytest.mark.parametrize("offset_ns", [-250_000, 250_000])
def test_full_speed_keeps_a_reference_within_contact_interval(offset_ns):
    head = _head_record(Swing())
    head["speed_reference"].update(
        timestamp_ns=CONTACT_NS + offset_ns,
        contact_interval_ns=[CONTACT_NS - 1e6, CONTACT_NS + 1e6],
    )
    result = full_speed.evaluate_full_club_speed_head_track(
        ops_track=_club_track(Swing(), 8), head_track=head, rig=CLUB_RIG
    )
    assert result["status"] == "experimental", result["reason"]


def test_timestamp_without_a_contact_declaration_cannot_bind_the_model():
    inputs = replace(_inputs(Swing()), ops_speed_referred_to=None)
    reference = ht.estimate_head_track(inputs).to_dict()["speed_reference"]
    assert reference["status"] == "unbound"
    assert reference["referred_to"] is None
