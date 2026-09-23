"""The ball distance check recovers a rig's tilt, lens height and roll from taped placements."""

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "ball_range_check.py"
spec = importlib.util.spec_from_file_location("ball_range_check", SCRIPT)
check = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = check
spec.loader.exec_module(check)

RIG = check.RigGeometry.from_json(
    Path(__file__).resolve().parents[1] / "config" / "enclosure_v3_rig_geometry.json"
)


SPOTS = [(800, 560), (800, 760), (1200, 600), (1200, 700), (1800, 620), (1800, 680)]


def _placements(pitch_deg, drop_mm, roll_deg, size_scale=1.0, measured_pitch=None, spots=SPOTS):
    """Where a 1280x800 camera would see the ball at taped spots on a flat floor."""
    focal, cx, cy = check.FOCAL_PX_1X, 640.0, 400.0
    rows = []
    for number, (tape, x) in enumerate(spots, start=1):
        aside = tape * (x - cx) / focal
        along = math.sqrt(tape**2 - drop_mm**2 - aside**2)
        # a camera pitched up sees the floor further below its axis
        below = math.radians(pitch_deg) + math.atan(drop_mm / along)
        y = cy + focal * math.tan(below) + (x - cx) * math.tan(math.radians(roll_deg))
        diameter = size_scale * focal * check.BALL_DIAMETER_MM / tape
        rows.append(
            {
                "placement": number,
                "arm": {"width": 1280, "height": 800},
                # the tape runs from the radar window, 30 mm behind the lens
                "tee_mm": tape + 30.0,
                "ball": {"found": True, "x": x, "y": y, "diameter_px": diameter},
                "inclinometer": (
                    {"camera_pitch_deg": measured_pitch} if measured_pitch is not None else {}
                ),
            }
        )
    return rows


def test_it_recovers_a_pitched_rolled_camera_at_a_different_height(tmp_path):
    log = tmp_path / "placements.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in _placements(3.5, 80.0, 1.2)) + "\n")

    placements = check.load(log, RIG)
    floor = check.fit_floor(placements, 73.665)

    assert floor["pitch_up_deg"] == pytest.approx(3.5, abs=0.05)
    assert floor["drop_mm"] == pytest.approx(80.0, abs=1.0)
    assert floor["roll_deg"] == pytest.approx(1.2, abs=0.05)
    assert max(abs(r) for r in floor["residual_px"]) < 0.2


def test_the_report_names_a_ball_read_small(tmp_path):
    log = tmp_path / "placements.json"
    log.write_text(json.dumps({"placements": _placements(0.0, 73.665, 0.0, size_scale=0.88)}))

    lines = check.report(check.load(log, RIG), RIG)

    assert any("reads the ball at 88%" in line for line in lines)
    assert any("camera points 0.00 deg up" in line for line in lines)


def test_what_the_inclinometer_leaves_is_named(tmp_path):
    log = tmp_path / "placements.jsonl"
    rows = _placements(3.4, 73.665, 0.0, measured_pitch=3.3)
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    lines = check.report(check.load(log, RIG), RIG)

    assert any("inclinometer says 3.30 deg up, leaving 0.10 deg" in line for line in lines)


def test_a_centreline_walk_fits_pitch_and_height_and_leaves_roll(tmp_path):
    # front and back only: six distances, every ball within a few pixels of the middle
    spots = [(600, 636), (800, 640), (1000, 644), (1300, 638), (1600, 642), (2000, 640)]
    log = tmp_path / "placements.jsonl"
    log.write_text(
        "\n".join(json.dumps(r) for r in _placements(3.4, 80.0, 2.0, spots=spots)) + "\n"
    )

    placements = check.load(log, RIG)
    floor = check.fit_floor(placements, 73.665)
    lines = check.report(placements, RIG)

    assert "roll" not in floor["fitted"]
    assert floor["pitch_up_deg"] == pytest.approx(3.4, abs=0.05)
    assert floor["drop_mm"] == pytest.approx(80.0, abs=1.0)
    assert any("roll not fitted" in line for line in lines)
