"""The on-radar vs host comparison's pure helpers (the replay itself needs the external tree)."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "iwr_cormac_replay_compare.py"
)
SPEC = importlib.util.spec_from_file_location("iwr_cormac_replay_compare", SCRIPT)
CMP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CMP
SPEC.loader.exec_module(CMP)


def test_availability_names_the_side_that_produced_a_value():
    assert CMP.availability(1.0, 2.0) == "both"
    assert CMP.availability(1.0, None) == "ours"
    assert CMP.availability(None, 0.0) == "his"
    assert CMP.availability(None, None) == "neither"


def test_abs_diff_stats_median_p90_max_and_signed_median():
    stats = CMP.abs_diff_stats([(1.0, 0.0), (0.0, 2.0), (5.0, 1.0)])
    assert stats["n"] == 3
    assert stats["median_abs"] == 2.0
    assert stats["max_abs"] == 4.0
    assert stats["p90_abs"] == pytest.approx(3.6)
    assert stats["median_signed_ours_minus_his"] == 1.0
    assert CMP.abs_diff_stats([])["median_abs"] is None


def test_ours_values_keeps_only_accepted_launches_and_measured_ranges():
    original = {
        "launch_accepted": True,
        "launch_deg": 20.0,
        "horizontal_deg": 3.0,
        "horizontal_status": "hlcmf_v2_accepted",
        "range_status": "measured",
        "range_m": 2.0,
        "path_accepted": False,
        "path_deg": 4.0,
        "attack_status": "candidate_out_of_bounds",
        "attack_deg": -70.0,
    }
    values = CMP.ours_values(original, 0.066)
    assert values["vla"] == 20.0 and values["hla"] == 3.0
    assert values["launch_range_bin"] == pytest.approx((2.0 + 0.066) / (6.0 / 128.0))
    assert values["path"] is None and values["attack"] is None
    low = CMP.ours_values({**original, "horizontal_status": "hlcmf_v2_low_coherence"}, 0.0)
    assert low["hla"] is None
    rejected = CMP.ours_values({**original, "launch_accepted": False}, 0.0)
    assert rejected["ball"] is None and rejected["vla"] is None


def _metric(value: float, flags: int) -> dict:
    return {"value": value, "confidence": 0.5, "flags": flags}


def test_his_values_reads_only_valid_plausible_packet_metrics_in_degrees():
    his = {
        "result": {
            "metrics": {
                "ball_speed": _metric(60.0, 1 | 2),
                "vertical_launch": _metric(math.radians(15.0), 1 | 2),
                "horizontal_launch": _metric(math.radians(50.0), 1 | 2 | 8),  # implausible
                "club_path": _metric(0.0, 0),  # not valid
                "angle_of_attack": _metric(math.radians(-4.0), 1 | 2),
            }
        },
        "ball_first_point": {"frame": 9, "range_bin": 50.0, "range_m": 2.28},
    }
    values = CMP.his_values(his)
    assert values["ball"] == 1.0
    assert values["vla"] == pytest.approx(15.0)
    assert values["hla"] is None
    assert values["path"] is None
    assert values["attack"] == pytest.approx(-4.0)
    assert values["launch_range_bin"] == 50.0


def test_label_first_ball_needs_a_reviewed_sidecar():
    labels = {
        "reviewed": True,
        "ball": {"points": [{"frame": 9, "range_bin": 50}, {"frame": 8, "range_bin": 47}]},
    }
    assert CMP.label_first_ball(labels) == {"frame": 8, "range_bin": 47}
    assert CMP.label_first_ball({**labels, "reviewed": False}) is None
    assert CMP.label_first_ball(None) is None
    assert CMP.launch_range_label_error(45.0, 47.0) == -2.0
    assert CMP.launch_range_label_error(None, 47.0) is None
