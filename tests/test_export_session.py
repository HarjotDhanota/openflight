"""Can someone export their own session and replay it?

The camera-vision scripts read a session export. A running launch monitor
writes a session JSONL plus per-shot captures in their own directories, which
is a different shape. These tests build a miniature live session in the layout
the server actually produces and check the exporter turns it into something
those scripts accept -- including the failure paths, because a contributor
whose export comes out empty needs to know which shot was missing what.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

EXPORTER = Path(__file__).parent.parent / "scripts" / "analysis" / "export_session.py"


def _write_frames(path: Path) -> None:
    """A frames.npz carrying the arrays capture_runtime writes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = np.zeros((8, 200, 320), dtype=np.uint8)
    np.savez(
        path,
        frames=frames,
        sensor_timestamp_ns=np.arange(8, dtype=np.int64) * 2_138_000,
        host_timestamp_ns=np.arange(8, dtype=np.int64) * 2_138_000 + 5,
        exposure_us=np.full(8, 280, dtype=np.int32),
        analogue_gain=np.full(8, 8.0, dtype=np.float32),
        pre_trigger_count=np.int32(6),
        trigger_host_timestamp_ns=np.int64(6 * 2_138_000),
        trigger_epoch_timestamp=np.float64(1_787_000_000.0),
    )


def _live_session(root: Path, shots: int = 2, *, drop_camera_on: int | None = None) -> Path:
    """A session JSONL plus captures, in the layout the server writes."""
    camera_root = root / "openflight_sessions" / "camera"
    radar_root = root / "openflight_sessions" / "iwr6843"
    log = root / "openflight_sessions" / "session_20260825_181734.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = [{"ts": "2026-08-25T18:17:34", "type": "session_start"}]
    for n in range(1, shots + 1):
        camera_dir = camera_root / f"camera_2026_{n:03d}"
        dump = radar_root / f"iwr6843_2026_{n:03d}.l3dump"
        dump.parent.mkdir(parents=True, exist_ok=True)
        dump.write_bytes(b"ILD1" + bytes(64))
        if n != drop_camera_on:
            _write_frames(camera_dir / "frames.npz")
            (camera_dir / "camera_metadata.json").write_text(
                json.dumps({"sequence": n, "pre_trigger_frames": 6}), encoding="utf-8"
            )
            entries.append(
                {
                    "ts": f"2026-08-25T18:18:{n:02d}",
                    "type": "camera_capture",
                    "shot_number": n,
                    "capture_path": str(camera_dir),
                    "metadata": {"delivered_fps": 467.6, "pre_trigger_frames": 6},
                }
            )
        entries.append(
            {
                "ts": f"2026-08-25T18:18:{n:02d}",
                "type": "iwr6843_capture",
                "shot_number": n,
                "capture_path": str(dump),
                "capture_bytes": 68,
            }
        )
        entries.append(
            {
                "ts": f"2026-08-25T18:18:{n:02d}",
                "type": "shot_detected",
                "shot_number": n,
                "club": "7-iron",
                "ball_speed_mph": 115.0 + n,
                "club_speed_mph": 82.0,
                "launch_angle_vertical": 20.0,
                "launch_angle_horizontal": -2.0,
                "spin_candidates": [{"rank": 1, "rpm": 5273}],
            }
        )
    entries.append({"ts": "2026-08-25T18:30:00", "type": "session_end"})

    with open(log, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
    return log


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(EXPORTER), *args], capture_output=True, text=True, check=False
    )


class TestExport:
    def test_produces_a_layout_the_scripts_recognise(self, tmp_path):
        """`shots.csv` plus a `shots/` directory is exactly what
        session_path._looks_like_session requires."""
        log = _live_session(tmp_path, shots=2)
        out = tmp_path / "export"
        result = _run(str(log), "--output", str(out))
        assert result.returncode == 0, result.stdout + result.stderr
        assert (out / "shots.csv").is_file()
        assert (out / "shots").is_dir()

    def test_each_shot_carries_its_frames_and_radar_dump(self, tmp_path):
        log = _live_session(tmp_path, shots=2)
        out = tmp_path / "export"
        assert _run(str(log), "--output", str(out)).returncode == 0
        with open(out / "shots.csv", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 2
        for row in rows:
            assert (out / row["archive_frames_npz"]).is_file()
            assert (out / row["archive_iwr_file"]).is_file()

    def test_frames_survive_the_copy(self, tmp_path):
        """The arrays the scripts read must come back intact, not just the file."""
        log = _live_session(tmp_path, shots=1)
        out = tmp_path / "export"
        assert _run(str(log), "--output", str(out)).returncode == 0
        with open(out / "shots.csv", newline="", encoding="utf-8") as handle:
            row = next(iter(csv.DictReader(handle)))
        data = np.load(out / row["archive_frames_npz"])
        assert data["frames"].shape == (8, 200, 320)
        assert "sensor_timestamp_ns" in data

    def test_capture_metadata_is_flattened_with_its_prefix(self, tmp_path):
        log = _live_session(tmp_path, shots=1)
        out = tmp_path / "export"
        assert _run(str(log), "--output", str(out)).returncode == 0
        with open(out / "shots.csv", newline="", encoding="utf-8") as handle:
            row = next(iter(csv.DictReader(handle)))
        assert row["shot_number"] == "1"
        assert row["club"] == "7-iron"
        assert row["iwr_capture_bytes"] == "68"
        assert row["camera_metadata_delivered_fps"] == "467.6"

    def test_a_shot_missing_its_camera_capture_is_excluded_and_explained(self, tmp_path):
        """Half a shot fails deep inside a fit later; drop it here with a reason."""
        log = _live_session(tmp_path, shots=2, drop_camera_on=2)
        out = tmp_path / "export"
        assert _run(str(log), "--output", str(out)).returncode == 0
        with open(out / "shots.csv", newline="", encoding="utf-8") as handle:
            assert len(list(csv.DictReader(handle))) == 1
        with open(out / "excluded_shots.csv", newline="", encoding="utf-8") as handle:
            excluded = list(csv.DictReader(handle))
        assert len(excluded) == 1
        assert excluded[0]["shot_number"] == "2"
        assert "camera" in excluded[0]["reason"].lower()

    def test_optional_launch_filter_drops_and_records(self, tmp_path):
        log = _live_session(tmp_path, shots=2)
        out = tmp_path / "export"
        result = _run(str(log), "--output", str(out), "--min-launch-deg", "30")
        assert result.returncode == 1, "no shot passes a 30 deg floor here"
        with open(out / "excluded_shots.csv", newline="", encoding="utf-8") as handle:
            assert all("vertical launch" in r["reason"] for r in csv.DictReader(handle))

    def test_the_original_log_is_kept(self, tmp_path):
        log = _live_session(tmp_path, shots=1)
        out = tmp_path / "export"
        assert _run(str(log), "--output", str(out)).returncode == 0
        assert (out / "source_logs" / log.name).is_file()

    def test_readme_tells_the_reader_what_to_do_next(self, tmp_path):
        log = _live_session(tmp_path, shots=1)
        out = tmp_path / "export"
        assert _run(str(log), "--output", str(out)).returncode == 0
        readme = (out / "README.md").read_text(encoding="utf-8")
        assert "OPENFLIGHT_SESSION" in readme


class TestFailsClearly:
    def test_a_missing_log_is_named(self, tmp_path):
        result = _run(str(tmp_path / "nope.jsonl"), "--output", str(tmp_path / "out"))
        assert result.returncode == 2
        assert "no session log" in result.stdout.lower()

    def test_a_log_with_no_shots_says_so(self, tmp_path):
        log = tmp_path / "empty.jsonl"
        log.write_text(json.dumps({"ts": "x", "type": "session_start"}) + "\n", encoding="utf-8")
        result = _run(str(log), "--output", str(tmp_path / "out"))
        assert result.returncode == 2
        assert "shot_detected" in result.stdout

    def test_captures_that_moved_are_still_found(self, tmp_path):
        """Logs record absolute paths from the Pi. Exporting from a copied
        session on another machine must still locate the files."""
        log = _live_session(tmp_path, shots=1)
        entries = [
            json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line
        ]
        for entry in entries:
            if entry.get("type") in {"camera_capture", "iwr6843_capture"}:
                entry["capture_path"] = str(
                    Path("/somewhere/else") / Path(entry["capture_path"]).name
                )
        log.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
        out = tmp_path / "export"
        assert _run(str(log), "--output", str(out)).returncode == 0, "should fall back to a search"


@pytest.mark.parametrize("shots", [1, 3])
def test_export_is_readable_by_the_session_finder(tmp_path, shots):
    """End to end: the finder the research scripts use must accept the result."""
    sys.path.insert(
        0, str(Path(__file__).parent.parent / "research" / "silhouette_poc" / "falsification")
    )
    from session_path import find_session  # noqa: PLC0415

    log = _live_session(tmp_path, shots=shots)
    out = tmp_path / "export"
    assert _run(str(log), "--output", str(out)).returncode == 0
    assert find_session(str(out)) == out
