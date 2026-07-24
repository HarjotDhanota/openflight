#!/usr/bin/env python3
"""Shared loaders for offline spin experiments.

Used by experiment_spin_windows.py and experiment_spin_ripple.py: session
JSONL parsing, TrackMan comparison-CSV matching, club normalization, and
IQCapture reconstruction from logged capture entries.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from openflight.launch_monitor import ClubType  # noqa: E402
from openflight.rolling_buffer.types import IQCapture  # noqa: E402


def club_enum(normalized_club: str) -> ClubType:
    aliases = {
        "driver": ClubType.DRIVER,
        "3-wood": ClubType.WOOD_3,
        "5-wood": ClubType.WOOD_5,
        "7-wood": ClubType.WOOD_7,
        "3-hybrid": ClubType.HYBRID_3,
        "5-hybrid": ClubType.HYBRID_5,
        "7-hybrid": ClubType.HYBRID_7,
        "9-hybrid": ClubType.HYBRID_9,
        "2-iron": ClubType.IRON_2,
        "3-iron": ClubType.IRON_3,
        "4-iron": ClubType.IRON_4,
        "5-iron": ClubType.IRON_5,
        "6-iron": ClubType.IRON_6,
        "7-iron": ClubType.IRON_7,
        "8-iron": ClubType.IRON_8,
        "9-iron": ClubType.IRON_9,
        "pw": ClubType.PW,
        "gw": ClubType.GW,
        "sw": ClubType.SW,
        "lw": ClubType.LW,
    }
    return aliases.get(normalized_club, ClubType.UNKNOWN)


def to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> Optional[int]:
    number = to_float(value)
    return int(number) if number is not None else None


def load_session_entries(path: Path) -> tuple[list[dict], list[dict]]:
    shots = []
    captures = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("type") == "shot_detected":
                shots.append(entry)
            elif entry.get("type") == "rolling_buffer_capture":
                captures.append(entry)
    return shots, captures


def load_trackman_by_shot(comparison_path: Path) -> dict[int, dict[str, Any]]:
    by_shot = {}
    with comparison_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            shot_number = to_int(row.get("shot_number_of"))
            if shot_number is None:
                continue
            by_shot[shot_number] = {
                "match_quality": row.get("match_quality"),
                "spin_tm": to_float(row.get("spin_tm")),
                "ball_speed_tm": to_float(row.get("ball_speed_tm")),
            }
    return by_shot


def capture_from_entry(capture_entry: dict) -> IQCapture:
    """Rebuild an IQCapture from a logged rolling_buffer_capture entry."""
    return IQCapture(
        sample_time=capture_entry.get("sample_time", 0),
        trigger_time=capture_entry.get("trigger_time", 0),
        i_samples=capture_entry["i_samples"],
        q_samples=capture_entry["q_samples"],
    )
