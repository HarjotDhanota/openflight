"""Tests for the shared spin-experiment loaders."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "analysis"))

from spin_experiment_lib import (  # noqa: E402
    capture_from_entry,
    club_enum,
    load_session_entries,
    load_trackman_by_shot,
    to_float,
    to_int,
)

from openflight.launch_monitor import ClubType  # noqa: E402

SESSION = PROJECT_ROOT / "session_logs" / "session_20260605_132943_trackman.jsonl"
COMPARISON = PROJECT_ROOT / "session_logs" / "comparison_20260605_132943_trackman.csv"


def test_load_session_entries_finds_shots_and_captures():
    shots, captures = load_session_entries(SESSION)
    assert len(shots) > 0
    assert len(captures) > 0
    assert all(entry["type"] == "shot_detected" for entry in shots)
    assert all(entry["type"] == "rolling_buffer_capture" for entry in captures)


def test_capture_from_entry_builds_full_capture():
    _, captures = load_session_entries(SESSION)
    capture = capture_from_entry(captures[0])
    assert capture.num_samples == 4096
    assert len(capture.q_samples) == 4096


def test_load_trackman_by_shot_parses_types():
    by_shot = load_trackman_by_shot(COMPARISON)
    assert len(by_shot) > 0
    assert all(isinstance(shot_number, int) for shot_number in by_shot)
    spins = [row["spin_tm"] for row in by_shot.values() if row["spin_tm"] is not None]
    assert len(spins) > 0
    assert all(isinstance(spin, float) for spin in spins)


def test_club_enum_known_and_unknown():
    assert club_enum("driver") == ClubType.DRIVER
    assert club_enum("7-iron") == ClubType.IRON_7
    assert club_enum("frying-pan") == ClubType.UNKNOWN


def test_numeric_coercion():
    assert to_float("") is None
    assert to_float("3.5") == 3.5
    assert to_int("7") == 7
    assert to_int(None) is None
