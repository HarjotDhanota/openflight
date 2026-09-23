"""The exporter is the join: what it includes, what it excludes and why."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "export_session.py"
spec = importlib.util.spec_from_file_location("export_session", SCRIPT)
export_session = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = export_session  # dataclass resolves the module by name
spec.loader.exec_module(export_session)


def _capture_tree(root: Path, shots: list[dict]) -> Path:
    """A kiosk log dir: JSONL, raw log, camera dirs and dumps, as the server writes them."""
    src = root / "paired"
    camera_root = src / "arm1" / "camera"
    dump_root = src / "iwr6843"
    camera_root.mkdir(parents=True)
    dump_root.mkdir(parents=True)
    lines = [
        json.dumps(
            {
                "type": "session_start",
                "session_id": "20260922_101500",
                "session_uuid": "uuid-1",
                "app_version": "0.2.0",
                "config": {
                    "camera_capture": {
                        "width": 320,
                        "height": 200,
                        "fps": 450.0,
                        "exposure_us": 87,
                        "gain": 6.0,
                        "mount_height_m": 0.095,
                        "lateral_offset_m": 0.0,
                    },
                    "iwr6843": {
                        "tilt_deg": 10.0,
                        "radar_height_m": 0.051,
                        "tee_slant_range_m": 1.524,
                    },
                    "rig_geometry": {
                        "enabled": True,
                        "path": "config/enclosure_v3_rig_geometry.json",
                        "derived": {
                            "camera_mount_height_m": 0.095,
                            "camera_lateral_offset_m": 0.0,
                            "radar_height_m": 0.051,
                            "iwr_tilt_deg": 10.0,
                        },
                    },
                },
            }
        )
    ]
    for s in shots:
        n = s["n"]
        lines.append(
            json.dumps(
                {
                    "type": "shot_detected",
                    "shot_number": n,
                    "club": "7-iron",
                    "ball_speed_mph": 110.0,
                    "experimental_fused_status": s.get("status", "ok"),
                    "readings": [1, 2],
                }
            )
        )
        if s.get("camera", True):
            cam_dir = camera_root / f"camera_20260922_{n:03d}"
            if s.get("camera_files", True):
                cam_dir.mkdir()
                np.savez(
                    cam_dir / "frames.npz",
                    frames=np.zeros((3, 4, 4), np.uint8),
                    sensor_timestamp_ns=np.arange(3),
                )
                (cam_dir / "metadata.json").write_text(
                    json.dumps(
                        {
                            "delivered_fps": 467.6,
                            "frame_count": 3,
                            "settings": {
                                "width": 320,
                                "height": 200,
                                "exposure_us": 87,
                                "gain": 6.0,
                            },
                        }
                    )
                )
                (cam_dir / "first.pgm").write_bytes(b"P5")
            lines.append(
                json.dumps(
                    {
                        "type": "camera_capture",
                        "shot_number": n,
                        "capture_path": f"/home/pi/openflight_sessions/x/camera/{cam_dir.name}",
                        "capture_error": s.get("camera_error"),
                        "metadata": {"delivered_fps": 467.6, "gap_count": 0},
                    }
                )
            )
        if s.get("radar", True):
            dump = dump_root / f"iwr6843_20260922_{n:03d}.l3dump"
            if s.get("radar_files", True):
                dump.write_bytes(b"ILD1dump")
            lines.append(
                json.dumps(
                    {
                        "type": "iwr6843_capture",
                        "shot_number": n,
                        "capture_path": f"/home/pi/openflight_sessions/iwr6843/{dump.name}",
                        "capture_error": s.get("radar_error"),
                    }
                )
            )
    (src / "session_20260922_101500_arm1.jsonl").write_text("\n".join(lines) + "\n")
    (src / "radar_raw_20260922_101500.log").write_text("raw\n")
    return src


class TestTheJoin:
    def test_a_paired_shot_is_exported_in_the_layout_the_tools_read(self, tmp_path):
        src = _capture_tree(tmp_path, [{"n": 1}])
        report = export_session.export_session(src, tmp_path / "out")
        assert report.included == [1] and report.excluded == {}
        shot = tmp_path / "out" / "shots" / "shot_001_7-iron"
        for name in ("camera_metadata.json", "frames.npz", "capture.l3dump", "first.pgm"):
            assert (shot / name).is_file(), name
        assert (tmp_path / "out" / "session.jsonl").is_file()
        assert (tmp_path / "out" / "radar_raw.log").is_file()

    def test_a_shot_without_a_radar_dump_is_excluded_by_name(self, tmp_path):
        src = _capture_tree(tmp_path, [{"n": 1}, {"n": 2, "radar": False}])
        report = export_session.export_session(src, tmp_path / "out")
        assert report.included == [1]
        assert report.excluded == {2: ["no_radar_capture_event"]}
        rows = list(csv.DictReader((tmp_path / "out" / "excluded_shots.csv").open()))
        assert (
            rows[0]["shot_number"] == "2"
            and "no_radar_capture_event" in rows[0]["exclusion_reason"]
        )

    def test_a_capture_error_excludes_even_when_files_exist(self, tmp_path):
        src = _capture_tree(tmp_path, [{"n": 1, "camera_error": "buffer overrun"}])
        report = export_session.export_session(src, tmp_path / "out")
        assert report.included == []
        assert report.excluded[1] == ["camera_capture_error (buffer overrun)"]

    def test_files_missing_on_disk_exclude_even_when_the_log_says_ok(self, tmp_path):
        src = _capture_tree(tmp_path, [{"n": 1, "radar_files": False}])
        report = export_session.export_session(src, tmp_path / "out")
        assert report.excluded[1] == ["radar_dump_missing_on_disk"]

    def test_captures_are_found_by_basename_not_by_the_recorded_path(self, tmp_path):
        # capture_path in the JSONL points at /home/pi/...; the tree lives elsewhere
        src = _capture_tree(tmp_path, [{"n": 1}])
        assert export_session.export_session(src, tmp_path / "out").included == [1]

    def test_two_session_starts_is_refused(self, tmp_path):
        src = _capture_tree(tmp_path, [{"n": 1}])
        path = next(src.glob("session_*.jsonl"))
        path.write_text(
            path.read_text() + json.dumps({"type": "session_start", "session_uuid": "dup"}) + "\n"
        )
        report = export_session.export_session(src, tmp_path / "out")
        assert report.included == [] and "session_start" in report.problems[0]


class TestShotsCsv:
    def test_rows_carry_the_shot_the_capture_and_the_file_metadata(self, tmp_path):
        src = _capture_tree(tmp_path, [{"n": 1, "status": "low_light"}])
        export_session.export_session(src, tmp_path / "out")
        rows = list(csv.DictReader((tmp_path / "out" / "shots.csv").open()))
        row = rows[0]
        assert row["shot_number"] == "1" and row["club"] == "7-iron"
        assert row["dir"] == "shots/shot_001_7-iron"
        assert row["camera_metadata_delivered_fps"] == "467.6"
        assert row["camera_file_settings_exposure_us"] == "87"
        assert row["fused_status"] == "low_light" and row["accepted"] == "False"
        assert row["readings"] == "[1,2]", "lists are serialised, not exploded"


class TestManifest:
    def test_manifest_records_join_provenance_and_the_arm(self, tmp_path):
        src = _capture_tree(tmp_path, [{"n": 1}, {"n": 2, "camera": False}])
        arm = {
            "tester_id": "t1",
            "arm_id": "arm1",
            "label": "320×200 @450",
            "width": 320,
            "height": 200,
            "fps": 450.0,
            "exposure_us": 87,
            "club": "7-iron",
            "environment": "indoors",
            "light_index": 0.2,
            "capture_gain": 6.0,
            "capture_exposure_us": 87,
            "gain_source": "gain screen",
        }
        export_session.export_session(src, tmp_path / "out", arm_state=arm)
        manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
        assert manifest["contract_version"] == 1
        assert manifest["session_uuid"] == "uuid-1"
        assert manifest["tester_id"] == "t1" and manifest["arm"]["arm_id"] == "arm1"
        assert manifest["environment"]["light_index"] == 0.2
        assert manifest["shots"][0]["joined_by"] == "session_jsonl"
        assert manifest["excluded_shots"] == [
            {"shot_number": 2, "reasons": ["no_camera_capture_event"]}
        ]
        assert manifest["capture"]["resolved"] is None  # the fixture metadata has none
        assert manifest["capture"]["resolved_missing_reason"]

    def test_geometry_provenance_says_cad_file_when_the_rig_block_rode_in(self, tmp_path):
        src = _capture_tree(tmp_path, [{"n": 1}])
        export_session.export_session(src, tmp_path / "out")
        prov = json.loads((tmp_path / "out" / "manifest.json").read_text())["provenance"]
        assert prov["radar_height_m"] == {"value": 0.051, "source": "cad_file"}
        assert prov["iwr_tilt_deg"]["source"] == "cad_file"
        assert prov["tee_slant_range_m"]["source"] == "flag"

    def test_geometry_provenance_says_default_without_the_rig_block(self, tmp_path):
        src = _capture_tree(tmp_path, [{"n": 1}])
        path = next(src.glob("session_*.jsonl"))
        lines = path.read_text().splitlines()
        start = json.loads(lines[0])
        del start["config"]["rig_geometry"]
        lines[0] = json.dumps(start)
        path.write_text("\n".join(lines) + "\n")
        export_session.export_session(src, tmp_path / "out")
        prov = json.loads((tmp_path / "out" / "manifest.json").read_text())["provenance"]
        assert prov["radar_height_m"]["source"] == "default"


class TestValidate:
    def test_a_clean_export_passes(self, tmp_path):
        src = _capture_tree(tmp_path, [{"n": 1}])
        report = export_session.export_session(src, tmp_path / "out")
        assert export_session.validate_export(report.out) == []

    def test_a_missing_file_is_named(self, tmp_path):
        src = _capture_tree(tmp_path, [{"n": 1}])
        report = export_session.export_session(src, tmp_path / "out")
        (report.out / "shots" / "shot_001_7-iron" / "capture.l3dump").unlink()
        problems = export_session.validate_export(report.out)
        assert problems == ["shots/shot_001_7-iron missing capture.l3dump"]

    def test_a_uuid_mismatch_is_named(self, tmp_path):
        src = _capture_tree(tmp_path, [{"n": 1}])
        report = export_session.export_session(src, tmp_path / "out")
        manifest_path = report.out / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["session_uuid"] = "other"
        manifest_path.write_text(json.dumps(manifest))
        assert (
            "manifest session_uuid does not match session_start"
            in export_session.validate_export(report.out)
        )


class TestTesterRoot:
    def test_one_export_per_arm_with_its_state(self, tmp_path):
        tester = tmp_path / "t1"
        for arm in ("arm1", "arm2"):
            arm_dir = tester / arm
            _capture_tree(arm_dir, [{"n": 1}])
            (arm_dir / "arm.json").write_text(
                json.dumps({"tester_id": "t1", "arm_id": arm, "club": "7-iron", "light_index": 0.3})
            )
        reports = export_session.export_tester_root(tester, tmp_path / "out")
        assert [r.out.name for r in reports] == ["arm1", "arm2"]
        m = json.loads((tmp_path / "out" / "arm2" / "manifest.json").read_text())
        assert m["arm"]["arm_id"] == "arm2" and m["environment"]["light_index"] == 0.3

    def test_cli_exports_and_reports(self, tmp_path, capsys):
        src = _capture_tree(tmp_path, [{"n": 1}])
        assert export_session.main(["--source", str(src), "--out", str(tmp_path / "out")]) == 0
        assert "1 shots exported, 0 excluded" in capsys.readouterr().out
        # damage the tree afterwards: validation names the missing file
        (tmp_path / "out" / "shots" / "shot_001_7-iron" / "frames.npz").unlink()
        assert export_session.validate_export(tmp_path / "out") == [
            "shots/shot_001_7-iron missing frames.npz"
        ]


@pytest.mark.parametrize(
    "status,accepted", [("ok", True), ("fused", True), ("low_light", False), (None, False)]
)
def test_accepted_follows_the_estimator_status(status, accepted):
    shot = {"experimental_fused_status": status} if status else {}
    assert (export_session.fused_status(shot) in export_session.ACCEPTED_STATUSES) is accepted
