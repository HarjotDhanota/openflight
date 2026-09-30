"""The end-to-end pipeline check (P8-6): a whole tester session, stage by stage."""

from __future__ import annotations

import hashlib

import pytest

from openflight import pipeline_check as check
from scripts.analysis import check_pipeline as runner
from tests.session_fixtures import FLIGHT_HORIZONTAL_DEG, LAUNCH_DEG, TESTER, capture_tree

# Stages the complete synthetic session must pass: everything its data supports.
SUPPORTED = (
    "setup_admitted",
    "box_and_setup_ball",
    "tee_range",
    "ops_shot",
    "trigger_evidence",
    "clip_matched",
    "lighting",
    "geometry",
    "context",
    "camera_ball",
    "iwr_capture",
    "lcmf",
    "metric:ball_speed_mph",
    "metric:spin_rpm",
    "metric:iwr_launch_vertical_deg",
    "metric:camera_launch_horizontal_deg",
    "metric:camera_launch_vertical_deg",
)


@pytest.fixture(name="synthetic", scope="module")
def _synthetic(tmp_path_factory):
    root = capture_tree(tmp_path_factory.mktemp("pi"), shots=(1,), tester_setup=True)
    session = next((root / TESTER).rglob("session_*.jsonl"))
    before = hashlib.sha256(session.read_bytes()).hexdigest()
    report = runner.check_tester(root / TESTER)
    assert hashlib.sha256(session.read_bytes()).hexdigest() == before
    return report


def _rows(report) -> dict[str, dict]:
    (run,) = report["runs"]
    (shot,) = run["shots"]
    return {row["id"]: row for row in shot["stages"]}


def test_every_stage_the_synthetic_session_supports_passes(synthetic):
    rows = _rows(synthetic)
    failed = {sid: rows[sid] for sid in SUPPORTED if not check.passed(rows[sid])}
    assert not failed, failed
    assert synthetic["injected"] is None


def test_the_synthetic_flight_comes_back_through_the_camera(synthetic):
    rows = _rows(synthetic)
    ball = rows["camera_ball"]["value"]
    assert ball.startswith("accepted"), ball
    horizontal = float(rows["metric:camera_launch_horizontal_deg"]["value"].split()[0])
    vertical = float(rows["metric:camera_launch_vertical_deg"]["value"].split()[0])
    assert abs(horizontal - FLIGHT_HORIZONTAL_DEG) < 0.5
    assert abs(vertical - LAUNCH_DEG) < 1.5


def test_the_experimental_tee_labels_the_iwr_launch_experimental(synthetic):
    rows = _rows(synthetic)
    assert "experimental" in rows["tee_range"]["value"]
    assert rows["metric:iwr_launch_vertical_deg"]["value"].endswith("[experimental]")


def test_what_the_synthetic_data_cannot_support_is_named_not_hidden(synthetic):
    rows = _rows(synthetic)
    offset = rows["camera_vertical_offset"]
    assert (offset["status"], offset["cause"]) == (check.FAIL, "PENDING")
    assert "P8-3" in offset["evidence"]
    # the synthetic OPS I/Q carries only the ball, so there is no club speed
    club = rows["camera_club"]
    assert (club["status"], club["cause"]) == (check.FAIL, "DATA")
    for row in rows.values():
        if row["status"] in (check.FAIL, check.NOT_REACHED):
            assert row["cause"] in check.CAUSES, row


def test_no_code_break_in_the_synthetic_session(synthetic):
    assert synthetic["code_breaks"] == []


def test_the_review_replay_hands_the_camera_what_the_kiosk_does(synthetic):
    assert _rows(synthetic)["replay_agrees"]["status"] == check.PASS


def test_injected_setup_values_are_labelled_loudly(tmp_path):
    root = capture_tree(tmp_path / "pi", shots=(1,))
    injection = runner.Injection(
        tee_range_m=1.6, setup_ball={"x": 160.0, "y": 140.0, "diameter_px": 12.0}
    )
    report = runner.check_tester(root / TESTER, injection)
    assert report["injected"]["tee_range_m"] == 1.6
    rows = _rows(report)
    assert rows["tee_range"]["injected"] is True
    assert runner.INJECTED_SOURCE in rows["tee_range"]["value"]
    printed = runner.format_report(report)
    assert "INJECTED SETUP VALUES, NOT RECORDED BY THE SESSION" in printed
    assert "(INJECTED)" in printed


def test_the_virtual_session_rehashes_the_iwr_snapshot_it_changes(tmp_path):
    from openflight.raw_radar_replay import load_session_events

    root = capture_tree(tmp_path / "pi", shots=(1,))
    session = next((root / TESTER).rglob("session_*.jsonl"))
    frozen = load_session_events(session)
    _hash, start, events = runner.virtual_session(frozen, runner.Injection(tee_range_m=1.7))
    (iwr,) = [e for e in events if e.get("type") == "iwr6843_capture"]
    runtime = dict(iwr["runtime_config"])
    assert runtime["calibration"]["effective"]["tee_slant_range_m"] == 1.7
    embedded = runtime.pop("sha256")
    assert embedded == iwr["runtime_config_sha256"] == runner._canonical_sha256(runtime)
    assert start["config"]["tee_range_handoff"]["source"] == runner.INJECTED_SOURCE
    # the recorded events are untouched
    assert frozen[2][0]["config"]["tee_range_handoff"]["source"] == "qualified_static_iwr"


# ---------------------------------------------------------------- verdicts


def test_a_failure_must_name_its_cause():
    with pytest.raises(ValueError, match="names no cause"):
        check.stage("lcmf", check.FAIL)


def test_a_tee_range_one_holder_did_not_receive_is_a_code_break():
    row = check.tee_range(
        {"tee_slant_range_m": 1.5, "source": "unqualified_static_iwr"},
        configured_tee=1.5,
        runtime_tees=[1.5, None],
        setup_record=None,
        injected=False,
    )
    assert (row["status"], row["cause"]) == (check.FAIL, "CODE")
    assert "IWR runtime snapshot 2=None" in row["evidence"]


def test_a_holder_the_session_never_recorded_is_not_a_hand_off_failure():
    row = check.tee_range(
        {"tee_slant_range_m": 1.5, "source": "qualified_static_iwr"},
        configured_tee=check._ABSENT,
        runtime_tees=[1.5],
        setup_record=None,
        injected=False,
    )
    assert row["status"] == check.PASS and "qualified" in row["value"]


def test_a_missing_tee_is_data_and_names_the_saved_candidates():
    row = check.tee_range(
        {"tee_slant_range_m": None, "status": "pending", "source": "pending"},
        configured_tee=None,
        runtime_tees=[None],
        setup_record={
            "status": "unresolved",
            "reason": "camera_arm6_ball_not_identified_raw_evidence_only",
            "candidates": [
                {
                    "source": "iwr_static_profile_difference",
                    "radar_slant_range_m": 1.5277,
                    "selectable": False,
                }
            ],
        },
        injected=False,
    )
    assert (row["status"], row["cause"]) == (check.FAIL, "DATA")
    assert "1.528 m (not selectable)" in row["evidence"]


def test_a_session_with_a_setup_ball_judged_on_the_zone_is_a_code_break():
    row = check.lighting(
        {"eligible": False, "rule": "zone", "reason": "22% clipped", "manual_exposure": True},
        config_has_setup_ball=True,
    )
    assert (row["status"], row["cause"]) == (check.FAIL, "CODE")


def test_a_clipped_setup_ball_is_a_label():
    row = check.lighting(
        {
            "eligible": False,
            "rule": "setup_ball",
            "reason": "83% of it is clipped",
            "manual_exposure": True,
        },
        config_has_setup_ball=True,
    )
    assert row["status"] == check.LABELLED and "83% of it is clipped" in row["evidence"]


def _upstream(**overrides):
    rows = {
        sid: check.stage(sid, check.PASS)
        for sid in (
            "lighting",
            "geometry",
            "context",
            "camera_ball",
            "camera_club",
            "lcmf",
            "ops_shot",
            "iwr_capture",
            "tee_range",
        )
    }
    rows.update(overrides)
    return rows


def _not_found(candidate, gate_source="fixed_fractions"):
    return {
        "ball_estimate": {
            "status": "rejected_reference_ball_not_found",
            "reference_ball_diagnostics": {
                "scene": {
                    "status": "available",
                    "reason": "candidate failed size or hitting-zone geometry",
                    "candidate": candidate,
                },
                "impact": {"status": "rejected", "reason": "ValueError: none", "candidate": None},
                "gate": {"source": gate_source, "region_px": [685.0, 370.0, 871.0, 556.0]},
            },
        }
    }


SETUP_BALL = {"x": 778.0, "y": 463.0, "diameter_px": 31.0}


def test_a_setup_ball_the_ball_gate_never_received_is_a_code_break():
    row = check.camera_ball(_not_found(None, "fixed_fractions"), _upstream(), setup_ball=SETUP_BALL)
    assert (row["status"], row["cause"]) == (check.FAIL, "CODE")
    assert "gate used fixed_fractions" in row["evidence"]


def test_a_ball_not_found_at_the_setup_ball_is_unknown_with_facts():
    candidate = {"x": 659.0, "y": 192.0, "diameter_px": 5.0}
    row = check.camera_ball(_not_found(candidate, "setup_ball"), _upstream(), setup_ball=SETUP_BALL)
    assert (row["status"], row["cause"]) == (check.FAIL, "UNKNOWN")
    assert "(659, 192)" in row["evidence"]
    assert "gate: the setup ball" in row["evidence"]


def test_a_shown_club_delivery_the_review_calls_rejected_is_a_code_break():
    delivery = {"status": "chained_experimental", "club_path_deg": 2.1, "attack_angle_deg": -3.0}
    metric = {
        "key": "camera_club_path_deg",
        "label": "Club path (camera + IWR)",
        "status": "rejected",
        "value": 2.1,
        "unit": "deg",
        "reason": "chained_experimental",
        "details": {},
    }
    row = check.metric_stage(
        metric, _upstream(), estimator={"club_delivery": delivery}, azimuth_calibrated=False
    )
    assert (row["status"], row["cause"]) == (check.FAIL, "CODE")
    assert "ACCEPTED_DELIVERY_STATUSES" in row["evidence"]


def test_an_uncalibrated_iwr_horizontal_labelled_accepted_is_a_code_break():
    metric = {
        "key": "iwr_launch_horizontal_deg",
        "label": "Horizontal launch (IWR)",
        "status": "accepted",
        "value": 1.2,
        "unit": "deg",
        "reason": None,
        "details": {"tee_range_qualified": True},
    }
    row = check.metric_stage(metric, _upstream(), estimator={}, azimuth_calibrated=False)
    assert (row["status"], row["cause"]) == (check.FAIL, "CODE")


def test_ineligible_lighting_is_a_label_and_the_context_still_builds():
    judged = check.lighting(
        {
            "eligible": False,
            "rule": "setup_ball",
            "reason": "clipped",
            "manual_exposure": True,
        },
        config_has_setup_ball=True,
    )
    assert judged["status"] == check.LABELLED and check.passed(judged)
    row = check.context(
        {"context": {"available": True, "sha256": "ab" * 32}, "context_source": "rebuilt"},
        _upstream(lighting=judged),
    )
    assert row["status"] == check.PASS


def test_a_blocked_stage_inherits_the_root_cause():
    tee = check.stage("tee_range", check.FAIL, cause="DATA", evidence="none recorded")
    row = check.lcmf(
        {"status": "withheld", "reason": "tee_range_unresolved"}, _upstream(tee_range=tee)
    )
    assert (row["status"], row["cause"], row["blocked_by"]) == (
        check.NOT_REACHED,
        "DATA",
        "tee_range",
    )
