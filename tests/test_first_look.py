"""Does the first-look script answer "is this session usable" from a session on disk?

The heavy replays (registration, track metrics) are monkeypatched here: they
are pinned by their own regression tests against the real export, and running
them would make this file a minutes-long integration test. What IS exercised
against real code is everything the first look owns -- the facts, the geometry
tell (measured versus ASSUMED), the per-shot setup solve on synthetic frames
through the real `detect_reference_ball` and `solve_setup`, the saturation
check, and the verdict lines.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from openflight.camera.club_motion import ReferenceBall

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "first_look.py"
_SPEC = importlib.util.spec_from_file_location("first_look", _SCRIPT)
first_look = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = first_look
_SPEC.loader.exec_module(first_look)


FRAME_WIDTH = 64
FRAME_HEIGHT = 40
BALL_XY = (32.0, 26.0)
BALL_DIAMETER = 12.0
# `detect_reference_ball` refuses anything under 210 on the bright path.
BALL_LEVEL = 220


def _frames(*, ball_level: int = BALL_LEVEL, n: int = 8) -> np.ndarray:
    """A tiny capture with one stationary bright ball on a dark ground."""
    frames = np.full((n, FRAME_HEIGHT, FRAME_WIDTH), 30, dtype=np.uint8)
    ys, xs = np.mgrid[0:FRAME_HEIGHT, 0:FRAME_WIDTH]
    # The rig saves the capture MIRRORED for the operator preview and the
    # script un-mirrors before detecting, so place the ball mirrored here.
    mirrored_x = FRAME_WIDTH - 1 - BALL_XY[0]
    disc = (xs - mirrored_x) ** 2 + (ys - BALL_XY[1]) ** 2 <= (BALL_DIAMETER / 2.0) ** 2
    frames[:, disc] = ball_level
    return frames


def _empty_frames(n: int = 8) -> np.ndarray:
    """A capture with no ball in it at all."""
    return np.full((n, FRAME_HEIGHT, FRAME_WIDTH), 30, dtype=np.uint8)


def _write_shot(shot_dir: Path, *, frames: np.ndarray | None, with_dump: bool = True) -> None:
    shot_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "frame_count": 0 if frames is None else int(frames.shape[0]),
        "delivered_fps": 467.5,
        "pre_trigger_frames": 4,
        "trigger_host_timestamp_ns": 1_000_000_000,
        "settings": {
            "width": FRAME_WIDTH,
            "height": FRAME_HEIGHT,
            "fps": 488.0,
            "exposure_us": 500,
            "gain": 15.0,
            "stream": "raw",
            "rotate_180": False,
            "mirror_horizontal": True,
            "roll_correction_deg": 0.0,
        },
    }
    (shot_dir / "camera_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    if frames is not None:
        np.savez(
            shot_dir / "frames.npz",
            frames=frames,
            host_timestamp_ns=np.arange(frames.shape[0], dtype=np.int64) * 2_138_000,
            trigger_host_timestamp_ns=np.int64(1_000_000_000),
            exposure_us=np.full(frames.shape[0], 500, dtype=np.int64),
            analogue_gain=np.full(frames.shape[0], 15.0),
        )
    if with_dump:
        (shot_dir / "iwr6843_000.l3dump").write_bytes(b"\x00" * 16)


def _config(rig_geometry: dict | None) -> dict:
    return {
        "camera_capture": {
            "width": FRAME_WIDTH,
            "height": FRAME_HEIGHT,
            "fps": 488.0,
            "exposure_us": 500,
            "gain": 15.0,
            "mount_height_m": 0.2032,
            "lateral_offset_m": -0.060325,
            "horizontal_offset_deg": 0.0,
            "roll_correction_deg": 0.0,
            "mirror_horizontal": True,
        },
        "iwr6843": {
            "tee_slant_range_m": 1.524,
            "net_range_m": 5.131,
            "tilt_deg": 10.5,
            "radar_height_m": 0.1651,
            "ball_height_m": 0.04,
        },
        "rig_geometry": rig_geometry if rig_geometry is not None else {"enabled": False},
    }


def build_session(
    root: Path,
    *,
    rig_geometry: dict | None = None,
    with_config: bool = True,
    frames_by_shot: dict[int, np.ndarray | None] | None = None,
) -> Path:
    """A two-shot session export, small enough to build inside a test."""
    session = root / "openflight_session_20260101_000000"
    (session / "shots").mkdir(parents=True)
    (session / "source_logs").mkdir(parents=True)
    rows = [
        "shot_number,club,ball_speed_mph,launch_angle_vertical,launch_angle_horizontal",
        "1,7-iron,110.0,18.0,1.0",
        "2,9-iron,95.0,24.0,-2.0",
    ]
    (session / "shots.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    overrides = frames_by_shot or {}
    for number, club in ((1, "7-iron"), (2, "9-iron")):
        frames = overrides[number] if number in overrides else _frames()
        _write_shot(session / "shots" / f"shot_{number:03d}_{club}", frames=frames)
    entries: list[dict] = [{"type": "reading_accepted", "speed": 1.0}]
    if with_config:
        entries.append({"type": "session_start", "config": _config(rig_geometry)})
    (session / "source_logs" / "session_20260101.jsonl").write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8"
    )
    return session


@pytest.fixture(name="no_replays")
def _no_replays(monkeypatch):
    """Both heavy replays return nothing; their sections then skip by name."""
    monkeypatch.setattr(first_look.rr, "replay_session", lambda *a, **k: [])
    monkeypatch.setattr(first_look.rtm, "replay_session", lambda *a, **k: [])


# --- section 1: session facts -------------------------------------------------


def test_facts_report_shots_clubs_fps_and_capture_mode(tmp_path, no_replays):
    payload = first_look.first_look(build_session(tmp_path))
    facts = payload["facts"]
    assert facts["n_shots"] == 2
    assert facts["clubs"] == {"7-iron": 1, "9-iron": 1}
    assert facts["delivered_fps"]["median"] == pytest.approx(467.5)
    assert facts["capture_mode"] == f"{FRAME_WIDTH}x{FRAME_HEIGHT}"
    assert facts["settings"]["exposure_us"] == 500
    assert facts["settings"]["gain"] == 15.0
    assert facts["per_frame_exposure_arrays"] == 2
    assert facts["per_frame_gain_arrays"] == 2
    assert facts["n_l3dump"] == 2
    assert facts["ball_to_unit_m"] == pytest.approx(1.524)


def test_facts_render_in_the_report(tmp_path, no_replays):
    text = first_look.report(first_look.first_look(build_session(tmp_path)))
    assert "1. session facts" in text
    assert f"capture mode: {FRAME_WIDTH}x{FRAME_HEIGHT}" in text
    assert "7-iron x1" in text
    assert "exposure: 500 us" in text


def test_missing_per_frame_arrays_are_counted_not_assumed(tmp_path, no_replays, monkeypatch):
    session = build_session(tmp_path)
    shot = session / "shots" / "shot_001_7-iron"
    np.savez(shot / "frames.npz", frames=_frames(), host_timestamp_ns=np.zeros(8))
    facts = first_look.first_look(session)["facts"]
    assert facts["per_frame_exposure_arrays"] == 1
    assert facts["per_frame_gain_arrays"] == 1
    assert facts["n_frames_npz"] == 2


# --- section 1: the geometry tell, both ways ----------------------------------


def test_geometry_is_assumed_without_a_rig_geometry_block(tmp_path, no_replays):
    payload = first_look.first_look(build_session(tmp_path))
    geometry = payload["facts"]["geometry"]
    assert geometry["status"] == "assumed"
    assert geometry["assumed_values"]["camera_mount_height_m"] == pytest.approx(0.2032)
    assert geometry["assumed_values"]["radar_height_m"] == pytest.approx(0.1651)
    assert any(line.startswith("geometry: ASSUMED") for line in payload["verdict"])
    assert any("cannot be attributed to sensors" in line for line in payload["verdict"])
    assert "test_rig" in payload["setup"]["rig_source"]


def test_geometry_is_measured_with_a_rig_geometry_block(tmp_path, no_replays):
    rig_path = tmp_path / "enclosure_v42_rig_geometry.json"
    first_look.RigGeometry(
        focal_px=466.6667,
        image_width=FRAME_WIDTH,
        image_height=FRAME_HEIGHT,
        iwr_offset_mm=(75.05, 165.06, 16.72),
        mic_offset_mm=(-30.0, 3.0, -9.0),
        lens_height_above_floor_mm=220.65,
        iwr_boresight_pitch_deg=10.0,
        provenance="Fusion design v42",
    ).to_json(rig_path)
    block = {
        "enabled": True,
        "path": str(rig_path),
        "derived": {
            "camera_mount_height_m": 0.22065,
            "camera_lateral_offset_m": -0.07505,
            "radar_height_m": 0.05559,
            "iwr_tilt_deg": 10.0,
            "missing": [],
            "provenance": "Fusion design v42",
        },
    }
    payload = first_look.first_look(build_session(tmp_path, rig_geometry=block))
    geometry = payload["facts"]["geometry"]
    assert geometry["status"] == "measured"
    assert geometry["derived"]["camera_mount_height_m"] == pytest.approx(0.22065)
    assert "Fusion design v42" in geometry["provenance"]
    assert payload["setup"]["rig_source"].startswith("rig_geometry file")
    assert any(
        line == "geometry: measured (rig_geometry enclosure_v42_rig_geometry.json)"
        for line in payload["verdict"]
    )
    text = first_look.report(payload)
    assert "geometry: MEASURED" in text
    assert "camera_mount_height_m = 0.22065" in text


def test_measured_block_whose_file_is_absent_falls_back_by_name(tmp_path, no_replays):
    block = {
        "enabled": True,
        "path": "/home/pacinoj/openflight/config/not_on_this_machine.json",
        "derived": {"camera_mount_height_m": 0.22065, "missing": [], "provenance": "v42"},
    }
    payload = first_look.first_look(build_session(tmp_path, rig_geometry=block))
    assert payload["facts"]["geometry"]["status"] == "measured"
    assert "test_rig" in payload["setup"]["rig_source"]
    assert "not_on_this_machine.json" in payload["setup"]["rig_source"]


def test_no_config_skips_geometry_and_registration_by_name(tmp_path, no_replays):
    payload = first_look.first_look(build_session(tmp_path, with_config=False))
    assert payload["facts"]["geometry"]["status"] == "skipped_no_session_start_config"
    assert payload["registration"]["status"] == "skipped_no_session_start_config"
    assert any(line.startswith("geometry: unknown") for line in payload["verdict"])
    assert "skipped_no_session_start_config" in first_look.report(payload)


# --- section 2: the setup solve -----------------------------------------------


def test_setup_solve_reports_range_lateral_height_and_mic(tmp_path, no_replays):
    setup = first_look.first_look(build_session(tmp_path))["setup"]
    assert setup["status"] == "ok"
    assert setup["n_solved"] == 2
    # f * 42.67 / ~12 px, the slant along the off-axis ball's own pixel ray.
    assert setup["range_to_ball_mm"]["median"] == pytest.approx(1660.0, abs=40.0)
    for key in ("lateral_offset_mm", "height_above_ball_mm", "mic_to_ball_m", "ball_diameter_px"):
        assert setup[key]["n"] == 2
    assert setup["warnings"] == {}
    assert setup["n_diameter_out_of_band"] == 0


def test_the_rig_image_size_is_replaced_by_the_capture_and_said_so(tmp_path, no_replays):
    setup = first_look.first_look(build_session(tmp_path))["setup"]
    assert setup["shots"][0]["rig_image_size_replaced"] == "320x200"


def test_ball_diameter_outside_the_band_is_flagged(tmp_path, no_replays, monkeypatch):
    monkeypatch.setattr(
        first_look,
        "detect_reference_ball",
        lambda frames: ReferenceBall(x=32.0, y=26.0, diameter_px=6.0, area_px=28),
    )
    payload = first_look.first_look(build_session(tmp_path))
    setup = payload["setup"]
    assert setup["n_diameter_out_of_band"] == 2
    assert setup["diameter_out_of_band_shots"] == [1, 2]
    assert any("diameter outside 9-30 px in 2/2 shots" in line for line in payload["verdict"])
    # solve_setup's own warning is reported by its own name, not folded in.
    assert "ball_only_6.0_px_range_solve_is_soft" in setup["warnings"]
    assert any(
        line.startswith("setup warning: ball_only_6.0_px_range_solve_is_soft")
        for line in payload["verdict"]
    )


def test_a_saturated_ball_is_named_in_the_verdict(tmp_path, no_replays):
    session = build_session(
        tmp_path, frames_by_shot={1: _frames(ball_level=255), 2: _frames(ball_level=255)}
    )
    payload = first_look.first_look(session)
    assert payload["setup"]["n_saturated"] == 2
    assert payload["setup"]["shots"][0]["core_fraction_at_or_above_250"] == pytest.approx(1.0)
    assert payload["setup"]["shots"][0]["anchor_peak"] == pytest.approx(255.0)
    assert any(line.startswith("ball: saturated in 2/2 shots") for line in payload["verdict"])


def test_an_unsaturated_ball_is_not_called_saturated(tmp_path, no_replays):
    payload = first_look.first_look(build_session(tmp_path))
    assert payload["setup"]["n_saturated"] == 0
    assert payload["setup"]["shots"][0]["core_fraction_at_or_above_250"] == pytest.approx(0.0)
    assert not any(line.startswith("ball: saturated") for line in payload["verdict"])


def test_a_shot_with_no_frames_skips_by_name(tmp_path, no_replays):
    payload = first_look.first_look(build_session(tmp_path, frames_by_shot={1: None}))
    setup = payload["setup"]
    assert setup["n_solved"] == 1
    statuses = {record["shot_number"]: record["status"] for record in setup["shots"]}
    assert statuses == {1: "missing_frames_npz", 2: "ok"}
    assert "missing_frames_npz" in first_look.report(payload)


def test_a_shot_with_no_ball_skips_by_name(tmp_path, no_replays):
    payload = first_look.first_look(build_session(tmp_path, frames_by_shot={1: _empty_frames()}))
    statuses = {record["shot_number"]: record["status"] for record in payload["setup"]["shots"]}
    assert statuses == {1: "no_reference_ball", 2: "ok"}
    assert payload["setup"]["status_counts"]["no_reference_ball"] == 1


def test_a_session_with_no_frames_at_all_skips_the_whole_section(tmp_path, no_replays):
    payload = first_look.first_look(build_session(tmp_path, frames_by_shot={1: None, 2: None}))
    assert payload["setup"]["status"] == "skipped_no_frames"
    assert payload["setup"]["n_solved"] == 0
    assert any(line == "setup solve: skipped_no_frames" for line in payload["verdict"])
    assert any(line == "usable shots: 0/2" for line in payload["verdict"])


# --- sections 3 and 4: the replays, mocked ------------------------------------


def _registration_summary():
    return {
        "n_shots": 2,
        "elevation": {
            "status": "registered",
            "n": 2,
            "offset_deg": 6.18,
            "spread_deg": 1.15,
            "standard_error_deg": 0.33,
            "reason": "",
        },
        "azimuth": {
            "status": "registered",
            "n": 2,
            "offset_deg": 5.19,
            "spread_deg": 0.92,
            "standard_error_deg": 0.29,
            "reason": "",
        },
        "ball_clock_vs_acoustic_tee": {
            "n": 2,
            "median_frames": 1.89,
            "mad_frames": 0.8,
            "min_frames": 1.1,
            "max_frames": 2.7,
        },
        "ball_clock_vs_acoustic_taped": {"n": 0},
    }


def _track_records():
    return [
        {
            "shot_number": 1,
            "club": "7-iron",
            "track_status": "ok",
            "swing_plane_deg": 58.7,
            "swing_direction_deg": 1.24,
            "track_club_path_deg": 0.4,
            "track_attack_angle_deg": -3.1,
            "fused_attack_angle_deg": -4.1,
            "arc_radius_m": 1.6,
            "low_point_mm": 20.0,
            "lcmf_vertical_deg": 18.0,
            "camera_vertical_launch_deg": 24.0,
            "dynamic_loft_deg": 30.0,
            "dynamic_loft_status": "computed",
            "face_angle_status": "withheld_no_common_horizontal_reference",
        },
        {
            "shot_number": 2,
            "club": "9-iron",
            "track_status": "rejected_no_stable_head",
            "swing_plane_deg": None,
            "swing_direction_deg": None,
            "track_club_path_deg": None,
            "track_attack_angle_deg": None,
            "fused_attack_angle_deg": None,
            "arc_radius_m": None,
            "low_point_mm": None,
            "lcmf_vertical_deg": 24.0,
            "camera_vertical_launch_deg": None,
            "dynamic_loft_deg": None,
            "dynamic_loft_status": "withheld_no_vertical_launch",
            "face_angle_status": "withheld_no_common_horizontal_reference",
        },
    ]


@pytest.fixture(name="both_replays")
def _both_replays(monkeypatch):
    monkeypatch.setattr(
        first_look.rr, "replay_session", lambda *a, **k: [{"shot_number": 1, "status": "ok"}]
    )
    monkeypatch.setattr(first_look.rr, "summarise", lambda records: _registration_summary())
    monkeypatch.setattr(first_look.rtm, "replay_session", lambda *a, **k: _track_records())


def test_registration_and_timing_reach_the_verdict(tmp_path, both_replays):
    payload = first_look.first_look(build_session(tmp_path))
    assert payload["registration"]["elevation"]["offset_deg"] == pytest.approx(6.18)
    verdict = payload["verdict"]
    assert any(
        line.startswith("registration: elevation +6.18 deg") and "registered" in line
        for line in verdict
    )
    assert any(line.startswith("registration: azimuth +5.19 deg") for line in verdict)
    assert any(
        line.startswith("timing: ball clock 1.89 frames from acoustic (MAD 0.80)")
        for line in verdict
    )
    assert "3. registration + ball clock" in first_look.report(payload)


def test_club_metrics_report_statuses_and_per_club_loft(tmp_path, both_replays):
    payload = first_look.first_look(build_session(tmp_path))
    club = payload["club_metrics"]
    assert club["n_reconstructed"] == 1
    assert club["swing_plane_median_deg"] == pytest.approx(58.7)
    assert club["track_status_counts"]["rejected_no_stable_head"] == 1
    assert club["dynamic_loft_status_counts"]["withheld_no_vertical_launch"] == 1
    assert club["face_angle_status_counts"]["withheld_no_common_horizontal_reference"] == 2
    assert club["per_club"]["7-iron"]["dynamic_loft_median_deg"] == pytest.approx(30.0)
    assert any(line.startswith("club metrics: 1/2 reconstructed") for line in payload["verdict"])
    assert any(
        line == "dynamic loft: withheld_no_vertical_launch on 1 shots"
        for line in payload["verdict"]
    )


def test_skip_club_metrics_skips_that_section_by_name(tmp_path, both_replays):
    payload = first_look.first_look(build_session(tmp_path), skip_club_metrics=True)
    assert payload["club_metrics"]["status"] == "skipped_by_request"
    assert "skipped_by_request" in first_look.report(payload)
    assert any(line == "club metrics: skipped_by_request" for line in payload["verdict"])
    # Without the club track, "usable" falls back to the setup gate alone.
    assert any(line == "usable shots: 2/2" for line in payload["verdict"])


def test_usable_shots_counts_the_shots_that_survived_every_gate(tmp_path, both_replays):
    payload = first_look.first_look(build_session(tmp_path))
    assert any(line == "usable shots: 1/2" for line in payload["verdict"])
    assert any(line.startswith("usable means: ") for line in payload["verdict"])


# --- the command line ---------------------------------------------------------


def test_main_writes_json_and_text(tmp_path, both_replays, capsys):
    session = build_session(tmp_path)
    out = tmp_path / "out"
    assert first_look.main(["--session", str(session), "--out", str(out)]) == 0
    payload = json.loads((out / "first_look.json").read_text(encoding="utf-8"))
    assert payload["facts"]["n_shots"] == 2
    assert payload["setup"]["range_to_ball_mm"]["median"] is not None
    assert payload["verdict"]
    text = (out / "first_look.txt").read_text(encoding="utf-8")
    assert "5. verdict" in text
    assert text in capsys.readouterr().out


def test_main_reads_the_session_from_the_environment(tmp_path, both_replays, monkeypatch):
    monkeypatch.setenv("OPENFLIGHT_SESSION", str(build_session(tmp_path)))
    assert first_look.main([]) == 0


def test_main_excludes_the_shots_it_is_told_to(tmp_path, both_replays):
    session = build_session(tmp_path)
    payload = first_look.first_look(session, excluded=(1,))
    assert payload["setup"]["n_shots"] == 1
    assert payload["setup"]["shots"][0]["shot_number"] == 2


def test_main_refuses_a_directory_that_is_not_an_export(tmp_path):
    with pytest.raises(SystemExit):
        first_look.main(["--session", str(tmp_path)])
