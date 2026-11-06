"""A scalar range/source is not accepted own-shot target evidence."""

import hashlib
from datetime import datetime
from types import SimpleNamespace

import pytest
from impact_range_fixtures import range_result

from openflight.camera import impact_range_evidence as evidence, impact_session as session
from openflight.rig_geometry import geometry_fingerprint


@pytest.mark.parametrize("source", ["", "estimated", "stale_other_shot", "iwr", "iwr_post_impact"])
def test_scalar_range_cannot_claim_own_shot_iwr_evidence(source):
    run = session.RunRecord({}, {}, {}, {}, None)
    assert (
        session.shot_range_m({"ball_range_m": 2.0, "ball_range_source": source}, run, None)[0]
        is None
    )


@pytest.mark.parametrize(
    "start,selected",
    [
        ({}, {"selected_range_m": 2.0, "selected_uncertainty_m": 0.05}),
        ({"config": {"tee_range_handoff": {"range_m": 2.0, "source": "iwr"}}}, None),
    ],
)
def test_implicit_run_or_session_range_is_not_borrowed_into_a_shot(start, selected):
    run = session.RunRecord(start, {}, {}, {}, selected)
    assert session.shot_range_m({}, run, None)[0] is None


def subject():
    return SimpleNamespace(
        camera_fusion_session_uuid="session-a", shot_number=1, impact_timestamp=100.0
    )


def bound_shot():
    result = range_result()
    record = evidence.from_iwr_result(subject(), result, match_tolerance_s=0.75)
    assert record["status"] == evidence.ACCEPTED, record
    record = evidence.bind_camera(record, "a" * 64, session_uuid="session-a", shot_number=1)
    return {
        "shot_number": 1,
        "impact_timestamp": 100.0,
        **result.ball_range.to_dict(),
        "impact_range_evidence": record,
    }


def read(shot):
    return evidence.read_shot_range(shot, session_uuid="session-a", capture_sha256="a" * 64)


def reseal(record):
    record.pop("sha256", None)
    record["sha256"] = geometry_fingerprint(record)


def test_actual_raw_bytes_are_hashed_and_fit_is_not_qualified_as_head_range():
    shot = bound_shot()
    value, sigma, record = read(shot)
    assert (value, sigma) == (2, 0.03)
    assert record["radar"]["capture_sha256"] == hashlib.sha256(b"synthetic-iwr").hexdigest()
    assert record["target"] == "ball" and record["physical_qualified"] is False
    assert record["camera_radar_clock_mapping"] == "unqualified"
    run = session.RunRecord({"session_uuid": "session-a"}, {}, {}, {}, None)
    decision = {}
    assert session.shot_range_m(
        shot, run, None, capture_npz_sha256="a" * 64, decision=decision
    ) == (2, 0.03, "shot_iwr_post_impact")
    assert decision["accepted"] and decision["physical_qualified"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("shot_number", 2),
        ("impact_timestamp", 101.0),
        ("ball_range_m", 2.3),
        ("ball_range_uncertainty_m", 0.04),
        ("ball_range_source", "camera_prior"),
        ("ball_range_label", "estimated"),
    ],
)
def test_scalar_or_shot_update_cannot_reuse_previous_range_record(field, value):
    shot = bound_shot()
    shot[field] = value
    with pytest.raises(ValueError):
        read(shot)


@pytest.mark.parametrize(
    "field,value",
    [
        ("target", "club"),
        ("quantity", "camera_forward_depth"),
        ("value_kind", "extrapolated"),
        ("physical_qualified", True),
        ("camera_radar_clock_mapping", "calibrated"),
        ("session_uuid", "previous-session"),
        ("camera_capture_sha256", "b" * 64),
    ],
)
def test_resealed_wrong_target_or_capture_stays_ineligible(field, value):
    shot = bound_shot()
    shot["impact_range_evidence"][field] = value
    reseal(shot["impact_range_evidence"])
    with pytest.raises(ValueError):
        read(shot)


@pytest.mark.parametrize(
    "field,value",
    [
        ("impact_t_s", -0.001),
        ("impact_t_s", 0.030),
        ("uncertainty_m", 0),
        ("range_m", float("nan")),
        ("label", "estimated"),
        ("partial", {}),
        ("competing", {"verdict": "ambiguous"}),
    ],
)
def test_no_range_prediction_or_partial_ambiguous_fit_is_promoted(field, value):
    shot = bound_shot()
    shot["impact_range_evidence"]["estimate"][field] = value
    with pytest.raises(ValueError):
        reseal(shot["impact_range_evidence"])
        read(shot)


def test_boolean_shot_identity_does_not_alias_shot_one():
    shot = bound_shot()
    shot["shot_number"] = True
    with pytest.raises(ValueError):
        read(shot)


def test_bound_range_cannot_be_rebound_to_another_camera_capture():
    record = bound_shot()["impact_range_evidence"]
    result = evidence.bind_camera(record, "b" * 64, session_uuid="session-a", shot_number=1)
    assert result["status"] == "unavailable"


def test_hash_edit_and_missing_camera_binding_are_refused():
    shot = bound_shot()
    shot["impact_range_evidence"]["radar"]["capture_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        read(shot)
    shot = bound_shot()
    shot["impact_range_evidence"]["camera_capture_sha256"] = None
    reseal(shot["impact_range_evidence"])
    with pytest.raises(ValueError, match="capture"):
        read(shot)


@pytest.mark.parametrize("change", ["no_capture", "no_fit", "wrong_trigger", "missing_tolerance"])
def test_producer_failure_is_explicit_without_fabricating_range(change):
    from dataclasses import replace

    result = range_result()
    tolerance = 0.75
    if change == "no_capture":
        result.capture = None
    elif change == "no_fit":
        result.launch_range = None
    elif change == "wrong_trigger":
        result.capture = replace(result.capture, trigger_timestamp=90)
    else:
        tolerance = None
    record = evidence.from_iwr_result(subject(), result, match_tolerance_s=tolerance)
    assert record["status"] == "unavailable" and record["reason"]
    assert "selected" not in record


def test_shot_serializer_and_server_producer_preserve_range_record(monkeypatch):
    from openflight import server
    from openflight.clubs import ClubType
    from openflight.launch_monitor import Shot

    shot = Shot(
        ball_speed_mph=100,
        timestamp=datetime(2026, 10, 29),
        club=ClubType.IRON_7,
        shot_number=1,
        impact_timestamp=100,
        camera_fusion_session_uuid="session-a",
    )
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(capture_monitor=SimpleNamespace(match_tolerance_s=0.75)),
    )
    server._attach_ball_range(shot, range_result())
    assert shot.impact_range_evidence["status"] == evidence.ACCEPTED
    shot.impact_range_evidence = evidence.bind_camera(
        shot.impact_range_evidence, "a" * 64, session_uuid="session-a", shot_number=1
    )
    serialized = shot.to_dict()
    assert read(serialized)[:2] == (2, 0.03)
    serialized["impact_range_evidence"]["target"] = "club"
    assert shot.impact_range_evidence["target"] == "ball"


def test_server_binds_camera_archive_even_when_other_camera_analysis_is_unavailable(monkeypatch):
    from openflight import server
    from openflight.launch_monitor import Shot

    shot = Shot(
        ball_speed_mph=100,
        timestamp=datetime(2026, 10, 29),
        shot_number=1,
        impact_timestamp=100,
        camera_fusion_session_uuid="session-a",
    )
    result = range_result()
    for key, value in result.ball_range.to_dict().items():
        setattr(shot, key, value)
    shot.impact_range_evidence = evidence.from_iwr_result(shot, result, match_tolerance_s=0.75)
    monkeypatch.setattr(server, "camera_capture_runtime", None)
    monkeypatch.setattr(server, "_camera_lens_intrinsics", lambda: "nominal")
    monkeypatch.setattr(server, "_strip_offset_refusal", lambda *_: None)
    monkeypatch.setattr(server, "_resolve_shot_ball_range", lambda *_: None)
    monkeypatch.setattr(server, "shot_iwr_calibration", lambda *_: None)
    for name in (
        "_fuse_camera_ball_flight",
        "_fuse_camera_club_delivery",
        "_calibrate_camera_tilt_from_swing",
        "_apply_camera_fallback_launch",
    ):
        monkeypatch.setattr(server, name, lambda *_: None)
    server._fuse_camera_measurements(
        shot, SimpleNamespace(metadata={}), {"_capture_npz_sha256": "a" * 64}
    )
    assert read(shot.to_dict())[:2] == (2, 0.03)
    assert shot.camera_fusion_processing["status"] == "error"


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf")])
def test_invalid_explicit_override_is_never_a_numeric_range(value):
    run = session.RunRecord({}, {}, {}, {}, None)
    assert session.shot_range_m({}, run, value)[0] is None
