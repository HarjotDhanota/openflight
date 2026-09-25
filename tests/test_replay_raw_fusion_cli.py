from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

from openflight.accuracy_benchmark import build_accuracy_report
from scripts.analysis import benchmark_accuracy as benchmark_cli, replay_raw_fusion as cli


def test_standalone_cli_help_does_not_require_repository_pythonpath():
    repository = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(repository / "scripts/analysis/replay_raw_fusion.py"), "--help"],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--projection-manifest" in result.stdout


def _args(tmp_path, session, **changes):
    values = dict(
        session=session,
        shot=1,
        ops_sample_rate_hz=30_000,
        club="7-iron",
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


def _session(tmp_path):
    path = tmp_path / "session.jsonl"
    events = [
        {"type": "session_start", "session_uuid": "session-a"},
        {"type": "rolling_buffer_capture", "shot_number": 1},
    ]
    path.write_text("".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")
    return path


def _ops_capture(shot_number):
    frequency_hz = 4_000.0
    samples = range(4096)
    return {
        "type": "rolling_buffer_capture",
        "shot_number": shot_number,
        "sample_time": 10.0,
        "trigger_time": 10.1,
        "i_samples": [
            2048 + int(500 * math.cos(2 * math.pi * frequency_hz * index / 30_000))
            for index in samples
        ],
        "q_samples": [
            2048 + int(500 * math.sin(2 * math.pi * frequency_hz * index / 30_000))
            for index in samples
        ],
    }


def test_cli_writes_session_candidate_with_replayed_and_no_read_attempts(tmp_path):
    session = tmp_path / "session.jsonl"
    events = [
        {
            "type": "session_start",
            "session_uuid": "session-a",
            "started_at_utc": "2026-09-24T18:00:00+00:00",
            "runtime_provenance": {
                "source_snapshot": {
                    "status": "preserved",
                    "sha256": "a" * 64,
                    "content_manifest_sha256": "b" * 64,
                }
            },
        },
        _ops_capture(1),
        {"type": "shot_detected", "shot_number": 2},
    ]
    session.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    candidate_path = tmp_path / "candidate.json"
    replay_path = tmp_path / "replay.json"

    assert (
        cli.main(
            [
                str(session),
                "1",
                "--ops-sample-rate-hz",
                "30000",
                "--club",
                "7-iron",
                "--output",
                str(replay_path),
                "--benchmark-candidate-output",
                str(candidate_path),
            ]
        )
        == 0
    )

    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert candidate["identity"]["session_uuid"] == "session-a"
    assert len(candidate["identity"]["replay_software_content_sha256"]) == 64
    assert candidate["identity"]["captured_software_content_sha256"] == "b" * 64
    assert [attempt["attempt_id"] for attempt in candidate["attempts"]] == [
        "session-a:1",
        "session-a:2",
    ]
    assert [attempt["status"] for attempt in candidate["attempts"]] == ["read", "no_read"]
    assert candidate["attempts"][1]["metrics"]["ball_speed_radial"]["status"] == "withheld"

    report = build_accuracy_report(
        candidate=candidate,
        references=[
            {
                "reference_id": "reference-1",
                "metrics": {
                    "ball_speed": {
                        "status": "available",
                        "value": 100.0,
                        "unit": "mph",
                        "contract_id": "ball.speed.radial.mph.v1",
                    }
                },
            }
        ],
        match_manifest={
            "schema_version": 1,
            "candidate_sha256": "c" * 64,
            "reference_sha256": "r" * 64,
            "matches": [{"candidate_attempt_id": "session-a:1", "reference_id": "reference-1"}],
            "required_group_fields": ["candidate_kind", "software_content_sha256"],
            "metric_contracts": {
                "ball_speed": {"unit": "mph", "contract_id": "ball.speed.radial.mph.v1"}
            },
        },
        candidate_sha256="c" * 64,
        reference_sha256="r" * 64,
    )
    assert report["counts"]["attempts"] == 2
    assert (
        report["attempts"][1]["comparisons"]["ball_speed"]["reason"]
        == "candidate attempt is not a read"
    )


def test_session_candidate_refuses_other_shot_capture_outputs_before_writing(tmp_path):
    session = tmp_path / "session.jsonl"
    dump = tmp_path / "shot-2.l3dump"
    dump.write_bytes(b"preserve this raw dump")
    capture = tmp_path / "camera-2"
    capture.mkdir()
    metadata = capture / "metadata.json"
    metadata.write_text("preserve this metadata", encoding="utf-8")
    events = [
        {"type": "session_start", "session_uuid": "session-a"},
        _ops_capture(1),
        {"type": "iwr6843_capture", "shot_number": 2, "capture_path": str(dump)},
        {"type": "camera_capture", "shot_number": 2, "capture_path": str(capture)},
    ]
    session.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    candidate = tmp_path / "candidate.json"

    assert (
        cli.main(
            [
                str(session),
                "1",
                "--ops-sample-rate-hz",
                "30000",
                "--club",
                "7-iron",
                "--output",
                str(dump),
                "--benchmark-candidate-output",
                str(candidate),
            ]
        )
        == 2
    )
    assert dump.read_bytes() == b"preserve this raw dump"
    assert metadata.read_text(encoding="utf-8") == "preserve this metadata"
    report = tmp_path / "report.json"
    assert (
        cli.main(
            [
                str(session),
                "1",
                "--ops-sample-rate-hz",
                "30000",
                "--club",
                "7-iron",
                "--output",
                str(report),
                "--benchmark-candidate-output",
                str(metadata),
            ]
        )
        == 2
    )
    assert not report.exists()
    assert metadata.read_text(encoding="utf-8") == "preserve this metadata"


def test_benchmark_cli_consumes_session_candidate_with_reviewed_six_metric_contracts(tmp_path):
    session = tmp_path / "session.jsonl"
    session.write_text(
        "".join(
            json.dumps(event) + "\n"
            for event in [
                {"type": "session_start", "session_uuid": "session-a"},
                _ops_capture(1),
                {"type": "shot_detected", "shot_number": 2},
            ]
        ),
        encoding="utf-8",
    )
    candidate_path = tmp_path / "candidate.json"
    assert (
        cli.main(
            [
                str(session),
                "1",
                "--ops-sample-rate-hz",
                "30000",
                "--club",
                "7-iron",
                "--benchmark-candidate-output",
                str(candidate_path),
            ]
        )
        == 0
    )
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    metrics = candidate["attempts"][0]["metrics"]
    metrics["ball_speed_total"] = {
        "status": "available",
        "value": 100.0,
        "unit": "mph",
        "contract_id": "ball.speed.total.mph.v1",
        "source": "typed_fixture",
    }
    for key, value, contract in (
        ("launch_angle_vertical", 12.0, "ball.launch.vertical.deg.v1"),
        ("launch_angle_horizontal", 1.0, "ball.launch.horizontal.deg.v1"),
        ("club_speed", 90.0, "club.speed.ops_radial_vs_trackman.conditional.mph.v1"),
        ("club_path", -2.0, "club.path.optical_feature_vs_face_center.conditional.deg.v1"),
        ("attack_angle", 3.0, "club.attack.optical_feature_vs_face_center.conditional.deg.v1"),
    ):
        metrics[key] = {
            "status": "available",
            "value": value,
            "unit": "deg" if "deg" in contract else "mph",
            "contract_id": contract,
            "source": "typed_fixture",
        }
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    reference = tmp_path / "reference.csv"
    reference.write_text(
        "Ball Speed (mph),Launch Angle V,Launch Direction,Club Speed (mph),Club Path,Attack Angle\n100,12,1,90,-2,3\n",
        encoding="utf-8",
    )
    matches = tmp_path / "matches.json"
    output = tmp_path / "benchmark.json"
    assert (
        benchmark_cli.main(
            [
                "--candidate",
                str(candidate_path),
                "--reference",
                str(reference),
                "--write-match-template",
                str(matches),
            ]
        )
        == 0
    )
    reviewed = json.loads(matches.read_text(encoding="utf-8"))
    reviewed["metric_contracts"] = {
        "ball_speed_total": {
            "candidate_field": "experimental_ball_speed_total.value_mph",
            "reference_field": "ball_speed_mph",
            "unit": "mph",
            "contract_id": "ball.speed.total.mph.v1",
            "compatibility_basis": "reviewed",
        },
        "launch_angle_vertical": {
            "candidate_field": "launch_angle_vertical",
            "reference_field": "launch_angle_vertical",
            "unit": "deg",
            "contract_id": "ball.launch.vertical.deg.v1",
            "compatibility_basis": "reviewed",
        },
        "launch_angle_horizontal": {
            "candidate_field": "launch_angle_horizontal",
            "reference_field": "launch_angle_horizontal",
            "unit": "deg",
            "contract_id": "ball.launch.horizontal.deg.v1",
            "compatibility_basis": "reviewed",
        },
        "club_speed": {
            "candidate_field": "club_speed_mph",
            "reference_field": "club_speed_mph",
            "unit": "mph",
            "contract_id": "club.speed.ops_radial_vs_trackman.conditional.mph.v1",
            "compatibility_basis": "reviewed",
        },
        "club_path": {
            "candidate_field": "experimental_fused_club_path_deg",
            "reference_field": "club_path_deg",
            "unit": "deg",
            "contract_id": "club.path.optical_feature_vs_face_center.conditional.deg.v1",
            "compatibility_basis": "reviewed",
        },
        "attack_angle": {
            "candidate_field": "experimental_fused_attack_angle_deg",
            "reference_field": "attack_angle_deg",
            "unit": "deg",
            "contract_id": "club.attack.optical_feature_vs_face_center.conditional.deg.v1",
            "compatibility_basis": "reviewed",
        },
    }
    reviewed["matches"] = [
        {"candidate_attempt_id": "session-a:1", "reference_id": "trackman-row-1"}
    ]
    matches.write_text(json.dumps(reviewed), encoding="utf-8")
    assert (
        benchmark_cli.main(
            [
                "--candidate",
                str(candidate_path),
                "--reference",
                str(reference),
                "--matches",
                str(matches),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["counts"]["attempts"] == 2
    assert result["attempts"][1]["status"] == "no_read"


def test_projection_manifest_is_bound_to_session_shot_capture_and_derived_window(
    tmp_path, monkeypatch
):
    session = _session(tmp_path)
    ops_hash = "a" * 64
    monkeypatch.setattr(
        cli,
        "replay_ops_capture",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "canonical_capture_payload_sha256": ops_hash,
            "result": {"ball_speed_mph": 100.0},
            "overlapping_readings": [{"speed_mph": 90.0, "magnitude": 5.0, "timestamp_ms": 4.0}],
        },
    )
    manifest = {
        "reading_index": 0,
        "expected_speed_mph": 90.0,
        "expected_timestamp_ms": 4.0,
        "session_uuid": "session-a",
        "shot_number": 1,
        "ops_capture_payload_sha256": ops_hash,
        "ops_capture_origin_ns": 1_000_000_000,
        "ops_origin_m": [0, 0, 0],
        "ball_position_m": [1, 0, 0],
        "trajectory_direction": [1, 0, 0],
        "radial_clock_domain_id": "boot-a",
        "direction_clock_domain_id": "boot-a",
        "radial_target_frame_id": "rig-v3",
        "direction_target_frame_id": "rig-v3",
        "direction_dependencies": ["camera_pixels"],
        "minimum_projection": 0.2,
        "direction_observed_at_ns": 1_006_133_333,
        "association_tolerance_ns": 1,
        "ops_geometry_source": "operator-attested survey",
        "direction_source": "operator-attested camera fit",
    }
    manifest_path = tmp_path / "projection.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = cli.replay(_args(tmp_path, session, projection_manifest=manifest_path))
    stage = result["stages"]["measured_total_speed_candidate"]
    assert "candidate" in stage, stage
    assert stage["candidate"]["status"] == "available", stage["candidate"]
    assert stage["time_mapping"]["derived_center_ns"] == 1_006_133_333

    manifest["shot_number"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    failed = cli.replay(_args(tmp_path, session, projection_manifest=manifest_path))
    assert failed["stages"]["measured_total_speed_candidate"]["status"] == "error"


def test_unified_camera_recomputation_receives_replayed_radar_context(tmp_path, monkeypatch):
    session = _session(tmp_path)
    raw = tmp_path / "iwr.bin"
    raw.write_bytes(b"raw")
    with session.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "iwr6843_capture",
                    "shot_number": 1,
                    "capture_path": str(raw),
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
            + "\n"
        )
    monkeypatch.setattr(
        cli,
        "replay_ops_capture",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "canonical_capture_payload_sha256": "b" * 64,
            "result": {"ball_speed_mph": 101.0, "club_speed_mph": 77.0},
            "overlapping_readings": [],
        },
    )
    context = {
        "sha256": "old",
        "ops_ball_speed_mph": 1.0,
        "ops_club_speed_mph": 2.0,
        "ball_range_evidence": {"stale": True},
        "club_range_evidence": {"stale": True},
    }
    monkeypatch.setattr(
        cli,
        "replay_frozen_shot",
        lambda *_args, **_kwargs: {
            "capture_path": str(tmp_path / "frames.npz"),
            "_context": context,
            "_archive": {"frozen": True},
        },
    )
    seen = {}
    monkeypatch.setattr(cli, "geometry_fingerprint", lambda _context: "new-hash")
    monkeypatch.setattr(
        cli,
        "process_camera_fusion",
        lambda replay_context, archive: (
            seen.update(context=replay_context, archive=archive) or {"status": "ok"}
        ),
    )
    measurement = SimpleNamespace(
        accepted=True,
        angle_deg=18.0,
        horizontal_deg=1.0,
        horizontal_coherence=0.81234,
        range_evidence=None,
        to_dict=lambda: {
            "status": "accepted",
            "angle_deg": 18.0,
            "horizontal_deg": 1.0,
            "horizontal_confidence": 0.8,
        },
    )
    monkeypatch.setattr(cli, "build_replay_calibration", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "tx_order_from_config", lambda _path: "normal")
    monkeypatch.setattr(
        cli, "replay_iwr_capture_bytes", lambda *_args, **_kwargs: (measurement, None)
    )
    calibration = tmp_path / "cal.json"
    config = tmp_path / "radar.cfg"
    calibration.write_text("{}", encoding="utf-8")
    config.write_text("cfg", encoding="utf-8")
    result = cli.replay(
        _args(
            tmp_path,
            session,
            camera=True,
            iwr_calibration=calibration,
            iwr_config=config,
            iwr_tee_m=1.5,
        )
    )
    assert result["stages"]["camera"]["recomputed_radar_context"]["status"] == "replayed"
    assert seen["context"]["ops_ball_speed_mph"] == 101.0
    assert seen["context"]["ball_range_evidence"] is None
    assert seen["context"]["club_range_evidence"] is None
    assert seen["context"]["iwr_vertical_deg"] == 18.0
    assert seen["context"]["iwr_horizontal_confidence"] == 0.812
    assert seen["archive"] == {"frozen": True}


def test_recorded_iwr_snapshot_restores_base_tilt_and_base64_config(tmp_path, monkeypatch):
    session = _session(tmp_path)
    raw = tmp_path / "iwr.bin"
    raw.write_bytes(b"raw")
    radar_bytes = b"binary\xffcfg"
    runtime = {
        "net_range_m": 4.0,
        "tdm_sign_policy": "positive",
        "azimuth_offset_deg": 0.0,
        "horizontal_phase_reference_rad": None,
        "club_window_policy": {},
        "club_impact_correction_s": 0.0,
        "recovery_observations": [],
        "calibration": {
            "source_payload": {"elem_phase_rad": [0] * 8, "elem_gain": [1] * 8},
            "source_sha256": "source-file-hash",
            "effective": {
                "tilt_deg": 12.5,
                "tee_slant_range_m": 1.5,
                "radar_height_m": 0.05,
                "ball_height_m": 0.04,
            },
        },
        "radar_config": {
            "source_bytes_base64": base64.b64encode(radar_bytes).decode("ascii"),
            "source_sha256": hashlib.sha256(radar_bytes).hexdigest(),
        },
        "per_shot_inputs": {
            "ball_speed_mph": 101.0,
            "club": "7-iron",
            "club_speed_mph": 77.0,
            "effective_tilt_deg": None,
        },
    }
    runtime_hash = hashlib.sha256(
        json.dumps(runtime, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    runtime["sha256"] = runtime_hash
    with session.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "iwr6843_capture",
                    "shot_number": 1,
                    "capture_path": str(raw),
                    "capture_bytes": 3,
                    "runtime_config": runtime,
                    "runtime_config_sha256": runtime_hash,
                }
            )
            + "\n"
        )
    monkeypatch.setattr(
        cli,
        "replay_ops_capture",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "canonical_capture_payload_sha256": "c" * 64,
            "result": {"ball_speed_mph": 101.0, "club_speed_mph": 77.0},
            "overlapping_readings": [],
        },
    )
    seen = {}
    monkeypatch.setattr(
        cli,
        "build_replay_calibration",
        lambda _path, **kwargs: seen.update(kwargs) or object(),
    )
    monkeypatch.setattr(cli, "tx_order_from_config", lambda _path: "normal")
    measurement = SimpleNamespace(range_evidence=None, to_dict=lambda: {"status": "rejected"})
    monkeypatch.setattr(
        cli, "replay_iwr_capture_bytes", lambda *_args, **_kwargs: (measurement, None)
    )
    result = cli.replay(_args(tmp_path, session, iwr=True))
    assert result["stages"]["iwr6843"]["runtime_config_source"] == "recorded_per_shot_snapshot"
    assert seen["tilt_deg"] == 12.5
    assert result["stages"]["iwr6843"]["config_sha256"] == hashlib.sha256(radar_bytes).hexdigest()
