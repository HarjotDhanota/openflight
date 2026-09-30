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
