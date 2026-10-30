"""A model estimate cannot erase the displaced spin observation's origin."""

import json
from copy import deepcopy

import pytest

from openflight import server, shot_models
from openflight.session_logger import SessionLogger
from tests.test_collision_wiring import _delivered_shot


def attach(shot):
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access


def revoke_speed(shot):
    shot.club_speed_full = {"status": "not_measured", "reason": "late full-speed refusal"}
    attach(shot)


def observed(shot):
    return dict(
        value=shot.spin_rpm,
        confidence=shot.spin_confidence,
        source=shot.spin_source,
        method=shot.spin_method,
    )


@pytest.mark.parametrize("confidence", [0.0, 0.2, 0.59, None])
@pytest.mark.parametrize("source", [None, "measured", "experimental"])
def test_late_refusal_restores_the_original_confidence_and_source(confidence, source):
    shot = _delivered_shot(
        spin_rpm=3210.5,
        spin_confidence=confidence,
        spin_source=source,
        spin_method="original OPS candidate",
    )
    original = observed(shot)
    attach(shot)
    assert shot.spin_source == "calculated"
    assert shot.spin_confidence is None
    revoke_speed(shot)
    assert observed(shot) == original


def test_repeated_model_runs_preserve_the_same_serialized_original_observation():
    shot = _delivered_shot(
        spin_rpm=3210.5, spin_confidence=0.53, spin_source=None, spin_method="multitaper_ungated"
    )
    original = observed(shot)
    for _ in range(3):
        attach(shot)
        assert shot.input_sources["spin_observation"] == original
        assert shot.spin_rpm_measured == original["value"]
        assert shot.spin_method_measured == original["method"]
        assert shot.spin_confidence is None
    payload = shot.to_dict()
    assert payload["input_sources"]["spin_observation"] == original
    payload["input_sources"]["spin_observation"]["confidence"] = 1.0
    assert shot.input_sources["spin_observation"] == original


@pytest.mark.parametrize("source", [None, "measured"])
def test_an_accepted_measurement_keeps_its_origin_and_needs_no_displaced_snapshot(source):
    shot = _delivered_shot(
        spin_rpm=6800.0, spin_confidence=0.9, spin_source=source, spin_method="accepted measurement"
    )
    original = observed(shot)
    attach(shot)
    revoke_speed(shot)
    assert observed(shot) == original
    assert "spin_observation" not in shot.input_sources


@pytest.mark.parametrize("model_confidence", [None, 0.9])
def test_a_legacy_model_record_cannot_invent_the_lost_confidence(model_confidence):
    shot = _delivered_shot(
        spin_rpm=7000.0,
        spin_source="calculated",
        spin_confidence=model_confidence,
        spin_rpm_measured=900.0,
        spin_method_measured="legacy OPS",
    )
    revoke_speed(shot)
    assert observed(shot) == dict(
        value=900.0, confidence=None, source="measured", method="legacy OPS"
    )
    assert "spin_observation" not in shot.input_sources


def test_a_new_observation_replaces_the_old_displaced_snapshot():
    shot = _delivered_shot(spin_rpm=1200.0, spin_confidence=0.2, spin_method="first OPS")
    attach(shot)
    shot.spin_rpm, shot.spin_confidence = 2200.0, 0.4
    shot.spin_source, shot.spin_method = "experimental", "second OPS"
    second = observed(shot)
    attach(shot)
    revoke_speed(shot)
    assert observed(shot) == second
    assert shot.input_sources["spin_observation"] == second


def test_restored_below_floor_confidence_is_explained_without_promoting_the_spin():
    shot = _delivered_shot(spin_rpm=3210.5, spin_confidence=0.53, spin_method="OPS")
    attach(shot)
    revoke_speed(shot)
    server._resolve_spin(shot)  # pylint: disable=protected-access
    resolution = deepcopy(shot.input_sources["spin"])
    assert resolution["route"] == "ops_below_floor"
    assert resolution["label"] == "experimental"
    assert "0.53" in resolution["tried"][0]["reason"]


def test_the_session_log_retains_the_original_confidence_for_later_restoration(tmp_path):
    shot = _delivered_shot(
        spin_rpm=3210.5,
        spin_confidence=0.53,
        spin_source="experimental",
        spin_method="original OPS",
    )
    original = observed(shot)
    attach(shot)
    logger = SessionLogger(log_dir=tmp_path, enabled=True)
    logger.start_session(mode="rolling-buffer", trigger_type="sound")
    assert logger.log_shot(shot)
    entry = json.loads(logger.session_path.read_text().splitlines()[-1])
    restored = _delivered_shot(
        spin_rpm=entry["spin_rpm"],
        spin_confidence=entry["spin_confidence"],
        spin_source=entry["spin_source"],
        spin_method=entry["spin_method"],
        spin_rpm_measured=entry["spin_rpm_measured"],
        spin_method_measured=entry["spin_method_measured"],
        input_sources=entry["input_sources"],
    )
    revoke_speed(restored)
    assert observed(restored) == original
