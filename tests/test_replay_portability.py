"""Replay must run from a copied session and carry the identity the session recorded."""

from __future__ import annotations

import json
import math
import shutil
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import rig_geometry
from openflight.raw_radar_replay import (
    benchmark_candidate_from_raw_replays,
    locate_recorded_capture,
)
from openflight.runtime_provenance import (
    capture_runtime_provenance,
    source_content_manifest_sha256,
)
from scripts.analysis import replay_raw_fusion as cli

PI_RUN = "/home/pi/openflight_sessions/tester_pilot/t1/arm5/paired/run-01"
SETUP_HASH = "c" * 64


def _ops_capture(shot_number, *, processor_config=True):
    event = {
        "type": "rolling_buffer_capture",
        "shot_number": shot_number,
        "sample_time": 10.0,
        "trigger_time": 10.1,
        "i_samples": [
            2048 + int(500 * math.cos(2 * math.pi * 4000.0 * index / 30_000))
            for index in range(4096)
        ],
        "q_samples": [
            2048 + int(500 * math.sin(2 * math.pi * 4000.0 * index / 30_000))
            for index in range(4096)
        ],
    }
    if processor_config:
        event["processor_config"] = {"sample_rate_hz": 30_000, "club_type": "7-iron"}
    return event


def _write_frames(folder: Path, exposure_us: int, gain: float) -> None:
    folder.mkdir(parents=True)
    np.savez(
        folder / "frames.npz",
        frames=np.zeros((4, 8, 8), dtype=np.uint8),
        sensor_timestamp_ns=np.arange(4, dtype=np.int64),
        host_timestamp_ns=np.arange(4, dtype=np.int64),
        exposure_us=np.full(4, exposure_us, dtype=np.int32),
        analogue_gain=np.full(4, gain, dtype=np.float32),
    )


def _copied_session(tmp_path: Path) -> Path:
    """A run folder as it looks after copying it off the Pi: paths still say /home/pi."""
    run = tmp_path / "elsewhere" / "run-01"
    (run / "iwr6843").mkdir(parents=True)
    (run / "iwr6843" / "iwr_001.l3dump").write_bytes(b"raw")
    _write_frames(run / "arm5" / "camera" / "camera_001", 300, 4.0)
    _write_frames(run / "arm5" / "camera" / "camera_002", 150, 8.0)
    parameters = {"camera_mount_height_m": 0.095}
    start = {
        "type": "session_start",
        "session_uuid": "session-a",
        "config": {
            "camera_capture": {"output_dir": f"{PI_RUN}/arm5/camera"},
            "rig_geometry": {
                "snapshot": {
                    "parameters": parameters,
                    "sha256": rig_geometry.geometry_fingerprint(parameters),
                }
            },
        },
    }
    events = [start]
    for shot, capture in ((1, "camera_001"), (2, "camera_002")):
        events.append(_ops_capture(shot))
        events.append(
            {
                "type": "camera_capture",
                "shot_number": shot,
                "capture_path": f"{PI_RUN}/arm5/camera/{capture}",
                "metadata": {
                    "tester_setup": {
                        "config_hash": SETUP_HASH,
                        "observations": {"lis3dh": {"placement_guard": {"warned": shot == 2}}},
                    }
                },
            }
        )
    events.append(
        {
            "type": "iwr6843_capture",
            "shot_number": 1,
            "capture_path": f"{PI_RUN}/iwr6843/iwr_001.l3dump",
            "capture_bytes": 3,
            "runtime_config": {
                "net_range_m": 4.0,
                "tdm_sign_policy": "positive",
                "azimuth_offset_deg": 0.0,
                "horizontal_phase_reference_rad": None,
                "club_window_policy": {},
                "club_impact_correction_s": 0.0,
                "recovery_observations": [],
            },
        }
    )
    session = run / "session_20260924_185002_arm5.jsonl"
    session.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return session


def _args(session: Path, shot: int = 1, **changes) -> Namespace:
    values = dict(
        session=session,
        shot=shot,
        ops_sample_rate_hz=None,
        club=None,
        projection_manifest=None,
        iwr=False,
        iwr_calibration=None,
        iwr_config=None,
        iwr_runtime_config=None,
        iwr_tee_m=None,
        iwr_tilt_deg=None,
        iwr_radar_height_m=None,
        ball_height_m=0.04,
        camera=False,
        camera_capture=None,
        output=None,
    )
    values.update(changes)
    return Namespace(**values)


def test_recorded_pi_path_is_relocated_inside_the_copied_session(tmp_path):
    session = _copied_session(tmp_path)
    found, resolution = locate_recorded_capture(f"{PI_RUN}/iwr6843/iwr_001.l3dump", session.parent)
    assert found == (session.parent / "iwr6843" / "iwr_001.l3dump").resolve()
    assert resolution == "relocated_under_session_directory"
    found, resolution = locate_recorded_capture("iwr6843/iwr_001.l3dump", session.parent)
    assert resolution == "recorded"


def test_relocation_never_reads_outside_the_session_folder(tmp_path):
    session = _copied_session(tmp_path)
    outside = tmp_path / "secret.l3dump"
    outside.write_bytes(b"x")
    with pytest.raises(FileNotFoundError, match="not in the session folder"):
        locate_recorded_capture(str(outside), session.parent)
    with pytest.raises(FileNotFoundError):
        locate_recorded_capture(f"{PI_RUN}/iwr6843/missing.l3dump", session.parent)


def test_iwr_stage_replays_a_copied_session_with_pi_paths(tmp_path, monkeypatch):
    session = _copied_session(tmp_path)
    seen = {}
    monkeypatch.setattr(cli, "build_replay_calibration", lambda *_a, **_k: object())
    monkeypatch.setattr(cli, "tx_order_from_config", lambda _path: "normal")
    measurement = SimpleNamespace(range_evidence=None, to_dict=lambda: {"status": "rejected"})
    monkeypatch.setattr(
        cli,
        "replay_iwr_capture_bytes",
        lambda raw, *_a, **kwargs: seen.update(raw=raw, club=kwargs["club"]) or (measurement, None),
    )
    calibration = tmp_path / "cal.json"
    calibration.write_text("{}", encoding="utf-8")
    config = tmp_path / "radar.cfg"
    config.write_text("cfg", encoding="utf-8")
    report = cli.replay(
        _args(
            session,
            iwr_calibration=calibration,
            iwr_config=config,
            iwr_tee_m=1.5,
            path_root=session.parent,
        )
    )
    stage = report["stages"]["iwr6843"]
    assert stage.get("error") is None, stage
    assert seen == {"raw": b"raw", "club": "7-iron"}
    assert stage["capture_path"] == "iwr6843/iwr_001.l3dump"
    assert stage["capture_path_resolution"] == "relocated_under_session_directory"
    assert report["session_file"] == session.name
    assert "iwr6843/iwr_001.l3dump" in report["input_paths"]
    assert all(not Path(path).is_absolute() for path in report["input_paths"])


def test_ops_sample_rate_and_club_come_from_the_recorded_processor_config(tmp_path):
    report = cli.replay(_args(_copied_session(tmp_path)))
    assert report["stages"]["ops"]["status"] in {"ok", "no_detection"}
    assert report["replay_inputs"] == {
        "ops_sample_rate_hz": 30_000,
        "club": "7-iron",
        "sources": {
            "ops_sample_rate_hz": "rolling_buffer_capture.processor_config.sample_rate_hz",
            "club": "rolling_buffer_capture.processor_config.club_type",
        },
    }


def test_missing_recorded_sample_rate_is_a_stage_error_not_a_guess(tmp_path):
    session = tmp_path / "session.jsonl"
    events = [
        {"type": "session_start", "session_uuid": "session-a"},
        _ops_capture(1, processor_config=False),
    ]
    session.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    report = cli.replay(_args(session))
    assert report["stages"]["ops"]["status"] == "error"
    assert "records no OPS sample rate" in report["stages"]["ops"]["error"]


def test_source_identity_carries_every_recorded_field_per_shot(tmp_path):
    session = _copied_session(tmp_path)
    first = cli.replay(_args(session, shot=1))
    second = cli.replay(_args(session, shot=2))
    identity = first["source_identity"]
    assert identity["arm_id"] == "arm5"
    assert identity["rig_geometry_sha256"] == rig_geometry.geometry_fingerprint(
        {"camera_mount_height_m": 0.095}
    )
    assert identity["setup_config_hash"] == SETUP_HASH
    assert (identity["capture_exposure_us"], identity["capture_gain"]) == (300.0, 4.0)
    assert identity["placement_warned"] is False
    assert second["source_identity"]["capture_exposure_us"] == 150.0
    assert second["source_identity"]["placement_warned"] is True
    evidence = first["source_identity_evidence"]
    assert evidence["capture_exposure_us"].startswith("frames.npz")
    assert evidence["rig_geometry_sha256"] == "session_start.config.rig_geometry.snapshot"

    candidate = benchmark_candidate_from_raw_replays([first, second])
    groups = {attempt["attempt_id"]: attempt["group"] for attempt in candidate["attempts"]}
    assert groups["session-a:1"]["capture_exposure_us"] == 300.0
    assert groups["session-a:2"]["capture_exposure_us"] == 150.0
    assert groups["session-a:2"]["placement_warned"] is True


def test_absent_identity_evidence_stays_null_with_a_reason(tmp_path):
    session = tmp_path / "session.jsonl"
    events = [{"type": "session_start", "session_uuid": "session-a"}, _ops_capture(1)]
    session.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    report = cli.replay(_args(session))
    identity = report["source_identity"]
    for key in ("arm_id", "rig_geometry_sha256", "capture_exposure_us", "setup_config_hash"):
        assert identity[key] is None
        assert report["source_identity_evidence"][key]


def test_tampered_rig_snapshot_is_not_reported_as_the_rig_identity(tmp_path):
    session = _copied_session(tmp_path)
    lines = session.read_text(encoding="utf-8").splitlines()
    start = json.loads(lines[0])
    start["config"]["rig_geometry"]["snapshot"]["parameters"]["camera_mount_height_m"] = 0.2
    session.write_text("\n".join([json.dumps(start), *lines[1:]]) + "\n", encoding="utf-8")
    report = cli.replay(_args(session))
    assert report["source_identity"]["rig_geometry_sha256"] is None
    assert "does not match" in report["source_identity_evidence"]["rig_geometry_sha256"]


def test_aggregation_still_rejects_reports_from_different_rigs(tmp_path):
    session = _copied_session(tmp_path)
    first = cli.replay(_args(session, shot=1))
    second = cli.replay(_args(session, shot=2))
    second["source_identity"] = {**second["source_identity"], "rig_geometry_sha256": "d" * 64}
    with pytest.raises(ValueError, match="disagree on source identity"):
        benchmark_candidate_from_raw_replays([first, second])


def _fake_repository(root: Path) -> Path:
    (root / "src" / "openflight").mkdir(parents=True)
    (root / "src" / "openflight" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    return root


def test_software_hash_ignores_where_the_checkout_lives(tmp_path):
    first = _fake_repository(tmp_path / "pi" / "openflight")
    second = tmp_path / "laptop" / "somewhere-else"
    shutil.copytree(first, second)
    assert source_content_manifest_sha256(first) == source_content_manifest_sha256(second)
    (second / "src" / "openflight" / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert source_content_manifest_sha256(first) != source_content_manifest_sha256(second)


def test_software_hash_equals_the_capture_snapshot_hash_for_the_same_source(tmp_path):
    repository = _fake_repository(tmp_path / "repo")
    recorded = capture_runtime_provenance(tmp_path / "log", "s1", repo_root=repository)
    assert recorded["source_snapshot"]["content_manifest_sha256"] == (
        source_content_manifest_sha256(repository)
    )
