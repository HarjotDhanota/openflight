"""Grading each swing's IWR launch-point range against a taped ball (13 Oct, B5).

The tape is bench truth, supplied as a file (never a UI input). The grade says how
far each measured launch point is from it, whether that is within its own stated
uncertainty, and whether the session passes the provisional gate. Until a session
passes, the per-swing range is NOT validated against a tape.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843 import launch_range_grade as grade
from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ16, pack_dump
from openflight.iwr6843.tracking import MPH_PER_MS
from tests.test_iwr6843_launch_range import _capture

REPO = Path(__file__).resolve().parents[1]


def _session(tmp_path: Path, name: str, launches: list[float | None]) -> Path:
    """A kiosk run folder: its session log and one IWR dump per shot."""
    run = tmp_path / name / "arm5" / "paired" / "run-01"
    (run / "iwr6843").mkdir(parents=True)
    lines = [{"type": "session_start", "iwr6843": {"range_prior": None}}]
    for number, launch in enumerate(launches, start=1):
        dump = run / "iwr6843" / f"iwr6843_{number:03d}.l3dump"
        if launch is not None:
            cube = _capture(launch_m=launch, seed=number).cube * 100.0
            dump.write_bytes(
                pack_dump(
                    cube, n_tx=2, version=3, frame_period_us=3000, sample_fmt=SAMPLE_RANGE_FFT_IQ16
                )
            )
        lines.append(
            {
                "type": "iwr6843_capture",
                "shot_number": number,
                "capture_path": f"/home/pi/sessions/{name}/iwr6843/{dump.name}",
                "ball_speed_mph": 45.0 * MPH_PER_MS,
            }
        )
        lines.append(
            {"type": "shot_detected", "shot_number": number, "club_speed_mph": 37.6 * MPH_PER_MS}
        )
    (run / "session_20261014_arm5.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )
    return run


def _truth(tmp_path: Path, sessions: dict) -> Path:
    path = tmp_path / "tape.json"
    path.write_text(
        json.dumps(
            {
                "schema": grade.TRUTH_SCHEMA,
                "tape_reference": "radar window face to the ball's near side",
                "sessions": sessions,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_each_shot_is_graded_against_the_tape(tmp_path):
    _session(tmp_path, "indoor-swings", [2.10, 2.10])
    truth = grade.load_truth(_truth(tmp_path, {"indoor-swings": {"tape_m": 2.10}}))

    report = grade.grade_sessions([tmp_path / "indoor-swings"], truth)

    shots = report["sessions"][0]["shots"]
    assert len(shots) == 2
    assert all(shot["status"] == "measured" for shot in shots)
    assert all(abs(shot["error_m"]) < 0.06 for shot in shots)
    assert report["sessions"][0]["tape_m"] == 2.10
    assert report["validated_against_tape"] is False
    assert "NOT validated" in report["statement"]


def test_a_shot_without_its_dump_is_not_measured_and_says_why(tmp_path):
    _session(tmp_path, "missing", [2.10, None])
    truth = grade.load_truth(_truth(tmp_path, {"missing": {"tape_m": 2.10}}))

    shots = grade.grade_sessions([tmp_path / "missing"], truth)["sessions"][0]["shots"]

    assert shots[1]["status"] == "not_measured"
    assert "dump" in shots[1]["reason"]


def test_a_session_without_a_tape_is_reported_ungraded():
    summary = grade.summarize(
        [{"status": "measured", "range_m": 2.1, "uncertainty_m": 0.03}], tape_m=None
    )

    assert summary["gate"] == "no_tape"
    assert summary["measured"] == 1


def test_the_gate_needs_most_shots_within_their_own_uncertainty():
    good = [
        {"status": "measured", "range_m": 2.12, "uncertainty_m": 0.03},
        {"status": "measured", "range_m": 2.09, "uncertainty_m": 0.03},
        {"status": "measured", "range_m": 2.11, "uncertainty_m": 0.03},
    ]
    bad = [
        {"status": "measured", "range_m": 2.40, "uncertainty_m": 0.03},
        {"status": "measured", "range_m": 1.80, "uncertainty_m": 0.03},
        {"status": "not_measured", "reason": "no ball track"},
    ]

    assert grade.summarize(good, tape_m=2.10)["gate"] == "pass"
    failed = grade.summarize(bad, tape_m=2.10)
    assert failed["gate"] == "fail"
    assert failed["measured_fraction"] == pytest.approx(2 / 3)


def test_the_truth_file_must_name_its_schema(tmp_path):
    path = tmp_path / "tape.json"
    path.write_text(json.dumps({"sessions": {}}), encoding="utf-8")

    with pytest.raises(ValueError, match="schema"):
        grade.load_truth(path)


def test_the_script_runs_and_prints_the_statement(tmp_path):
    _session(tmp_path, "script-swings", [2.10])
    truth = _truth(tmp_path, {"script-swings": {"tape_m": 2.10}})
    out = tmp_path / "grade.json"

    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "analysis" / "grade_launch_range.py"),
            str(tmp_path / "script-swings"),
            "--truth",
            str(truth),
            "--json",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
    )

    assert result.returncode == 0, result.stderr
    assert "NOT validated against a tape" in result.stdout
    assert json.loads(out.read_text(encoding="utf-8"))["sessions"][0]["name"] == "script-swings"


def test_grading_without_truth_still_lists_each_shot(tmp_path):
    _session(tmp_path, "untaped", [2.10])

    report = grade.grade_sessions([tmp_path / "untaped"], grade.Truth(sessions={}, shots={}))

    session = report["sessions"][0]
    assert session["tape_m"] is None
    assert session["summary"]["gate"] == "no_tape"
    assert np.isfinite(session["shots"][0]["range_m"])
