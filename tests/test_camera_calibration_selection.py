"""The kiosk picks the calibrated camera per saved-image mode from a calibration directory.

A mode with a pair measures on its calibrated model (principal point, distortion and the
placement's camera-vs-LIS3DH pose); a mode without one stays on the nominal camera and
says why; the session record names the pairs in use.
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server
from openflight.camera.calibrated_projection import load_calibration_dir
from openflight.launch_monitor import Shot
from openflight.rig_geometry import RigGeometry

REPO = Path(__file__).resolve().parents[1]
SET_DIR = REPO / "config" / "camera-calibration" / "6mm"
RIG_PATH = REPO / "config" / "enclosure_v3_rig_geometry.json"


def _rig():
    rig = RigGeometry.from_json(RIG_PATH)
    return rig, rig.snapshot()["sha256"]


def _load(directory=SET_DIR):
    rig, sha = _rig()
    return load_calibration_dir(directory, rig_params_sha256=sha, iwr_offset_mm=rig.iwr_offset_mm)


def _only(tmp_path, *modes):
    for mode in modes:
        for kind in ("optical", "placement"):
            shutil.copy(SET_DIR / f"{mode}.{kind}.json", tmp_path / f"{mode}.{kind}.json")
    return tmp_path


def test_the_shipped_directory_gives_one_pair_per_mode():
    loaded = _load()
    assert loaded.status == "loaded"
    assert {pair.mode for pair in loaded.pairs} == {(640, 400), (1280, 800)}
    pair = loaded.pair_for(640, 400)
    assert pair.optical["camera_matrix"][0][2] == pytest.approx(330.67, abs=0.01)
    assert loaded.pair_for(320, 200) is None
    assert "320x200" in loaded.fallback_reason(320, 200)


def test_an_absent_directory_is_nominal_not_an_error(tmp_path):
    loaded = _load(tmp_path / "missing")
    assert loaded.status == "absent"
    assert loaded.pairs == ()
    assert "absent" in loaded.fallback_reason(640, 400)


def test_a_placement_for_another_rig_leaves_only_its_mode_nominal(tmp_path):
    directory = _only(tmp_path, "640x400", "1280x800")
    placement = json.loads((directory / "1280x800.placement.json").read_text(encoding="utf-8"))
    placement["rig_geometry_sha256"] = "another-rig"
    (directory / "1280x800.placement.json").write_text(json.dumps(placement), encoding="utf-8")
    loaded = _load(directory)
    assert loaded.pair_for(640, 400) is not None
    assert loaded.pair_for(1280, 800) is None
    assert "rig_geometry_sha256" in loaded.fallback_reason(1280, 800)


def test_a_pair_named_for_another_mode_is_refused(tmp_path):
    shutil.copy(SET_DIR / "640x400.optical.json", tmp_path / "1280x800.optical.json")
    shutil.copy(SET_DIR / "640x400.placement.json", tmp_path / "1280x800.placement.json")
    loaded = _load(tmp_path)
    assert loaded.pairs == ()
    assert "calibrated for 640x400" in loaded.rejected["1280x800"]


@pytest.fixture(name="kiosk")
def _kiosk(monkeypatch):
    """The kiosk with the live rig file, a stable LIS3DH at the 28 Oct reference pose."""
    from openflight.camera.capture_runtime import CameraCaptureSettings  # noqa: PLC0415
    from openflight.inclinometer.models import (  # noqa: PLC0415
        OrientationSnapshot,
        SnapshotSelection,
    )

    for name in (
        "rig_geometry",
        "rig_geometry_config",
        "camera_lens",
        "camera_calibration_set",
        "camera_capture_config",
    ):
        monkeypatch.setattr(server, name, getattr(server, name))
    monkeypatch.setattr(server, "setup_handoff_config", {"tee_range": None, "scene": None})
    monkeypatch.setattr(server, "camera_tilt_calibration_path", None)
    reading = OrientationSnapshot(
        timestamp=time.time(),
        x_g=0.0,
        y_g=0.0,
        z_g=1.0,
        gravity_g=1.0,
        raw_pitch_deg=1.574,
        calibrated_pitch_deg=1.574,
        pitch_std_deg=0.05,
        sample_count=20,
    )
    monkeypatch.setattr(
        server,
        "inclinometer_service",
        SimpleNamespace(snapshot_for_impact=lambda _t: SnapshotSelection(reading, "stable")),
    )

    def start(width, height, directory=SET_DIR):
        monkeypatch.setattr(
            server,
            "camera_capture_runtime",
            SimpleNamespace(settings=CameraCaptureSettings(width=width, height=height)),
        )
        server.camera_capture_config = {"enabled": False, "width": width, "height": height}
        server.init_rig_geometry(RIG_PATH)
        server.init_camera_calibrated_fusion(None, None, calibration_dir=directory)
        return server

    return start


@pytest.mark.parametrize(("width", "height"), [(640, 400), (1280, 800)])
def test_the_setup_measures_on_the_mode_s_own_calibrated_camera(kiosk, width, height):
    kiosk(width, height)
    view = server.setup_camera_view()
    assert view.camera.source.startswith("calibrated")
    assert (view.camera.image_width_px, view.camera.image_height_px) == (width, height)
    pair = server.camera_calibration_set.pair_for(width, height)
    matrix = np.asarray(pair.optical["camera_matrix"])
    # the calibrated principal point, not the image centre, is the optical axis
    axis = view.camera.ray_model.rays(np.asarray([matrix[0, 2], matrix[1, 2]]))
    boresight = view.camera.ray_model.optical_to_world_lfu @ [0.0, 0.0, 1.0]
    np.testing.assert_allclose(axis, boresight, atol=1e-9)
    centre = view.camera.ray_model.rays(np.asarray([width / 2.0, height / 2.0]))
    assert np.linalg.norm(centre - boresight) > 1e-3
    # at the reference LIS3DH pitch the axis points as the 28 Oct boards measured it
    assert np.degrees(np.arcsin(boresight[2])) == pytest.approx(
        {640: 2.77, 1280: 2.58}[width], abs=0.01
    )


def test_a_mode_without_a_pair_measures_on_the_nominal_camera(kiosk, tmp_path):
    kiosk(1280, 800, directory=_only(tmp_path, "640x400"))
    view = server.setup_camera_view()
    assert view.camera.source == "nominal_uncalibrated"
    record = server._session_start_config()["camera_calibrated_fusion"]
    assert record["active_mode"] == "1280x800"
    assert record["in_use"] is None
    assert "no camera calibration for 1280x800" in record["fallback_reason"]


def test_the_session_record_names_the_pair_in_use(kiosk):
    kiosk(640, 400)
    record = server._session_start_config()["camera_calibrated_fusion"]
    pair = server.camera_calibration_set.pair_for(640, 400)
    assert record["enabled"] is True
    assert record["source"] == "directory"
    assert record["directory"] == str(SET_DIR)
    assert set(record["pairs"]) == {"640x400", "1280x800"}
    assert record["active_mode"] == "640x400"
    assert record["in_use"]["optical_file_sha256"].startswith("6bc10e1a")
    assert record["in_use"]["placement_file_sha256"] == pair.placement_file_sha256
    assert record["fallback_reason"] is None
    # the keys capture_facts reads stay the active mode's
    assert record["optical_calibration_sha256"] == record["in_use"]["optical_calibration_sha256"]
    assert record["placement_sha256"] == record["in_use"]["placement_sha256"]


def test_an_absent_directory_records_nominal(kiosk, tmp_path):
    kiosk(640, 400, directory=tmp_path / "missing")
    record = server._session_start_config()["camera_calibrated_fusion"]
    assert record["enabled"] is False
    assert record["status"] == "absent"
    assert "absent" in record["fallback_reason"]
    assert server.setup_camera_view().camera.source == "nominal_uncalibrated"


def test_an_explicit_pair_overrides_the_directory(kiosk, tmp_path):
    kiosk(640, 400)
    server.init_camera_calibrated_fusion(
        str(SET_DIR / "1280x800.optical.json"),
        str(SET_DIR / "1280x800.placement.json"),
        calibration_dir=SET_DIR,
    )
    assert server.camera_calibration_set.source == "explicit_pair"
    assert [pair.mode for pair in server.camera_calibration_set.pairs] == [(1280, 800)]
    # 640x400 has no pair in the override: nominal, labelled
    assert server.setup_camera_view().camera.source == "nominal_uncalibrated"


def test_an_explicit_pair_still_fails_loudly(kiosk, tmp_path):
    kiosk(640, 400)
    with pytest.raises(ValueError, match="both calibration and placement"):
        server.init_camera_calibrated_fusion(str(SET_DIR / "640x400.optical.json"), None)


def test_a_selected_lens_the_set_was_not_calibrated_with_is_nominal(kiosk):
    kiosk(640, 400)
    server.init_camera_lens("8mm")
    assert server.setup_camera_view().camera.source == "nominal_uncalibrated"
    record = server._session_start_config()["camera_calibrated_fusion"]
    assert "8mm" in record["fallback_reason"]


def test_the_fusion_takes_the_mode_from_the_capture_mode_evidence():
    capture = SimpleNamespace(
        metadata={"capture_mode": {"frames": {"saved_width": [640] * 3, "saved_height": [400] * 3}}}
    )
    assert server._capture_saved_mode(capture, None) == (640, 400)
    archive = {"frames": np.zeros((2, 800, 1280), np.uint8)}
    assert server._capture_saved_mode(SimpleNamespace(metadata={}), archive) == (1280, 800)
    assert server._capture_saved_mode(None, None) is None


def test_a_shot_in_a_mode_without_a_pair_fuses_on_the_nominal_camera_labelled(
    monkeypatch, tmp_path
):
    loaded = _load(_only(tmp_path, "640x400"))
    monkeypatch.setattr(server, "camera_calibration_set", loaded)
    monkeypatch.setattr(server, "camera_lens", None)
    monkeypatch.setattr(
        server, "camera_capture_runtime", SimpleNamespace(camera_analysis_eligible=True)
    )
    monkeypatch.setattr(server, "iwr6843_runtime", None)
    monkeypatch.setattr(
        server, "camera_capture_config", {"mount_height_m": 0.093, "width": 8, "height": 6}
    )
    monkeypatch.setattr(
        server,
        "_load_camera_capture_archive",
        lambda _capture: {
            "frames": np.zeros((4, 6, 8), np.uint8),
            "host_timestamp_ns": np.arange(4, dtype=np.int64),
            "trigger_host_timestamp_ns": np.int64(2),
            "pre_trigger_count": np.int32(2),
            "_capture_npz_sha256": "hash",
        },
    )
    monkeypatch.setattr(server, "_fuse_camera_ball_flight", lambda *_args: None)
    monkeypatch.setattr(server, "_fuse_camera_club_delivery", lambda *_args: None)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), shot_number=1)
    shot.camera_fusion_session_uuid = "session"
    server._fuse_camera_measurements(shot, SimpleNamespace(valid=True, metadata={}))
    assert shot.calibrated_camera_status == "legacy_inferred_ball_geometry"
    assert "no camera calibration for 8x6" in shot.calibrated_camera_reason
