"""Tests for the replay-only IWR6843 club-path experiment."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from openflight.iwr6843.club import ClubPathResult

_SCRIPT = Path(__file__).parent.parent / "scripts" / "analysis" / "iwr6843_club_path_replay.py"
_SPEC = importlib.util.spec_from_file_location("iwr6843_club_path_replay", _SCRIPT)
replay = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = replay
_SPEC.loader.exec_module(replay)


def test_control_comparison_uses_csv_printed_precision():
    result = ClubPathResult(
        status="rejected_phase_span",
        candidate_path_deg=12.3456784,
        n_frames=4,
        phase_span_rad=2.5,
    )
    row = {
        "iwr_club_path_status": "rejected_phase_span",
        "iwr_club_path_path_deg": "",
        "iwr_club_path_candidate_path_deg": "12.345678",
        "iwr_club_path_n_frames": "4",
        "iwr_club_path_phase_span_rad": "2.500",
    }

    assert replay.control_mismatches(result, row) == []

    row["iwr_club_path_candidate_path_deg"] = "12.345677"
    mismatches = replay.control_mismatches(result, row)

    assert mismatches == [
        {
            "field": "candidate_path_deg",
            "expected": "12.345677",
            "actual": 12.3456784,
        }
    ]


def test_window_static_removal_uses_only_selected_frames():
    cube = replay.np.asarray(
        [
            [[[[1.0 + 0.0j]]], [[[3.0 + 0.0j]]]],
            [[[[5.0 + 0.0j]]], [[[7.0 + 0.0j]]]],
            [[[[101.0 + 0.0j]]], [[[103.0 + 0.0j]]]],
        ]
    )

    filtered = replay.window_static_remove(cube, selected_frames={0, 1})

    assert filtered[0, :, 0, 0, 0].tolist() == [-3.0 + 0.0j, -1.0 + 0.0j]
    assert filtered[1, :, 0, 0, 0].tolist() == [1.0 + 0.0j, 3.0 + 0.0j]
    assert filtered[2, :, 0, 0, 0].tolist() == [97.0 + 0.0j, 99.0 + 0.0j]


def test_velocity_sources_are_explicit_and_signed():
    assert replay.derotation_velocity("quadratic", -31.0, -33.0, 80.0, 0.9) == -31.0
    assert replay.derotation_velocity("linear", -31.0, -33.0, 80.0, 0.9) == -33.0
    assert replay.derotation_velocity("ops", -31.0, -33.0, 80.0, 0.9) < 0.0
