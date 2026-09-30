"""The review shows every value the kiosk shows, with its label (P8-7, D15)."""

from __future__ import annotations

import pytest

from openflight.review_metrics import review_replay


def _report(delivery: dict, ball: dict | None = None, notes: list | None = None) -> dict:
    result = {
        "ball_estimate": ball or {"status": "accepted", "confidence_tier": "high"},
        "club_delivery": delivery,
        "horizontal_decision": {},
    }
    if notes is not None:
        result["notes"] = notes
    return {
        "stages": {"camera": {"recomputed_radar_context": {"status": "replayed", "result": result}}}
    }


def _metrics(report: dict) -> dict[str, dict]:
    return {m["key"]: m for m in review_replay(report, {})["metrics"]}


@pytest.mark.parametrize(
    "status, path, attack",
    [
        ("chained_experimental", 2.5, -3.0),
        ("approach_mixed", 1.5, -4.0),
        ("approach_path_only", 1.5, None),
        ("approach_aoa_only", None, -4.0),
    ],
)
def test_a_club_delivery_the_kiosk_shows_is_shown_with_its_label(status, path, attack):
    delivery = {
        "status": status,
        "club_path_deg": path,
        "attack_angle_deg": attack,
        "path_confidence_tier": "experimental" if path is not None else "withheld",
        "attack_confidence_tier": "experimental" if attack is not None else "withheld",
    }
    metrics = _metrics(_report(delivery))
    for key, value in (("camera_club_path_deg", path), ("camera_attack_angle_deg", attack)):
        metric = metrics[key]
        if value is None:
            assert metric["status"] == "rejected"
            continue
        assert metric["status"] == "experimental", metric
        assert metric["value"] == value
        assert status in metric["reason"]


def test_a_high_tier_chained_delivery_stays_accepted():
    delivery = {
        "status": "chained_high",
        "club_path_deg": 1.0,
        "attack_angle_deg": -2.0,
        "path_confidence_tier": "high",
        "attack_confidence_tier": "high",
    }
    metrics = _metrics(_report(delivery))
    assert metrics["camera_club_path_deg"]["status"] == "accepted"
    assert metrics["camera_club_path_deg"]["reason"] is None


def test_a_refused_delivery_stays_rejected():
    metrics = _metrics(_report({"status": "rejected_no_impact"}))
    assert metrics["camera_club_path_deg"]["status"] == "rejected"
    assert metrics["camera_club_path_deg"]["value"] is None


def test_camera_notes_label_every_camera_metric():
    """D15 (P8-7): a lighting note rides on the values it qualifies, never blanks them."""
    delivery = {
        "status": "chained_high",
        "club_path_deg": 1.0,
        "attack_angle_deg": -2.0,
        "path_confidence_tier": "high",
        "attack_confidence_tier": "high",
    }
    ball = {
        "status": "accepted",
        "confidence_tier": "high",
        "horizontal_deg": 2.0,
        "vertical_deg": 17.0,
    }
    note = "lighting: too bright for the ball: 83% of it is clipped"
    metrics = _metrics(_report(delivery, ball, notes=[note]))
    for key in (
        "camera_launch_horizontal_deg",
        "camera_launch_vertical_deg",
        "camera_club_path_deg",
        "camera_attack_angle_deg",
    ):
        metric = metrics[key]
        assert metric["value"] is not None, key
        assert metric["status"] == "experimental", key
        assert note in metric["reason"], key
        assert metric["details"]["notes"] == [note]


def test_the_overlay_shows_the_gate_the_ball_stage_used():
    gate = {
        "source": "setup_ball",
        "setup_ball": {"x": 778.0, "y": 463.0, "diameter_px": 31.0},
        "region_px": [685.0, 370.0, 871.0, 556.0],
        "diameter_px": [18.6, 49.6],
    }
    ball = {
        "status": "rejected_reference_ball_not_found",
        "reference_ball_diagnostics": {"gate": gate},
    }
    reviewed = review_replay(_report({"status": "rejected_no_ball"}, ball), {})
    region = reviewed["overlay"]["expected_region"]
    assert region["source"] == "setup_ball"
    assert region["region_px"] == gate["region_px"]


def test_a_low_consensus_ball_is_shown_as_experimental_with_its_label():
    ball = {
        "status": "low_consensus",
        "confidence_tier": "low",
        "horizontal_deg": 4.7,
        "vertical_deg": 15.8,
    }
    metrics = _metrics(_report({"status": "rejected_no_impact"}, ball))
    for key, value in (("camera_launch_horizontal_deg", 4.7), ("camera_launch_vertical_deg", 15.8)):
        assert metrics[key]["status"] == "experimental"
        assert metrics[key]["value"] == value
        assert "low consensus" in metrics[key]["reason"]


def test_club_stage_notes_label_the_club_metrics_only():
    delivery = {
        "status": "approach_mixed",
        "club_path_deg": 1.5,
        "attack_angle_deg": -4.0,
        "path_confidence_tier": "low",
        "attack_confidence_tier": "low",
        "notes": ["contact from the trigger frame: no ball departure was seen"],
    }
    ball = {
        "status": "accepted",
        "confidence_tier": "high",
        "horizontal_deg": 2.0,
        "vertical_deg": 17.0,
    }
    metrics = _metrics(_report(delivery, ball))
    for key in ("camera_club_path_deg", "camera_attack_angle_deg"):
        assert "contact from the trigger frame" in metrics[key]["reason"]
        assert metrics[key]["details"]["notes"] == delivery["notes"]
    assert metrics["camera_launch_horizontal_deg"]["reason"] is None


def test_a_low_coherence_iwr_horizontal_is_shown_experimental():
    report = {
        "stages": {
            "iwr6843": {
                "status": "accepted",
                "launch_angle_deg": 18.0,
                "horizontal_deg": 2.5,
                "horizontal_confidence": 0.2,
                "horizontal_status": "hlcmf_v1_low_coherence",
            }
        }
    }
    tee = {"tee_slant_range_m": 1.5, "source": "qualified_static_iwr"}
    metrics = {m["key"]: m for m in review_replay(report, {}, tee_range=tee)["metrics"]}
    horizontal = metrics["iwr_launch_horizontal_deg"]
    assert horizontal["value"] == 2.5
    assert horizontal["status"] == "experimental"
    assert "hlcmf_v1_low_coherence" in horizontal["reason"]


def test_a_reject_quality_track_launch_is_shown_experimental():
    report = {
        "stages": {
            "iwr6843": {
                "status": "accepted_warning_track_quality",
                "launch_angle_deg": 18.0,
                "tracker_quality": "reject",
            }
        }
    }
    tee = {"tee_slant_range_m": 1.5, "source": "qualified_static_iwr"}
    metrics = {m["key"]: m for m in review_replay(report, {}, tee_range=tee)["metrics"]}
    vertical = metrics["iwr_launch_vertical_deg"]
    assert vertical["value"] == 18.0
    assert vertical["status"] == "experimental"
    assert "track quality reject" in vertical["reason"]


def test_camera_metrics_from_a_reconstructed_context_say_so():
    report = _report(
        {
            "status": "chained_high",
            "club_path_deg": 1.0,
            "attack_angle_deg": -2.0,
            "path_confidence_tier": "high",
            "attack_confidence_tier": "high",
        },
        {
            "status": "accepted",
            "confidence_tier": "high",
            "horizontal_deg": 2.0,
            "vertical_deg": 17.0,
        },
    )
    report["stages"]["camera"]["recorded_context"] = {"context_source": "reconstructed"}
    metrics = _metrics(report)
    for key in ("camera_launch_horizontal_deg", "camera_club_path_deg"):
        assert metrics[key]["status"] == "experimental"
        assert "context reconstructed" in metrics[key]["reason"]


def test_a_missing_tee_range_is_named_on_every_metric_it_prevents():
    """P8-7 gate 2: the tee contract stands; its absence reads plainly on each metric."""
    report = {
        "stages": {
            "iwr6843": {"status": "withheld", "reason": "tee_range_unresolved"},
            "camera": {
                "status": "error",
                "error": "ValueError: cannot reconstruct the camera context: the session "
                "recorded no tee range (tee_range_handoff status 'pending', source 'pending')",
            },
        }
    }
    metrics = _metrics(report)
    for key in (
        "iwr_launch_vertical_deg",
        "iwr_launch_horizontal_deg",
        "iwr_club_path_deg",
        "iwr_attack_angle_deg",
        "camera_launch_horizontal_deg",
        "camera_club_path_deg",
    ):
        assert metrics[key]["status"] == "unavailable", key
        assert "no tee range" in metrics[key]["reason"], key
