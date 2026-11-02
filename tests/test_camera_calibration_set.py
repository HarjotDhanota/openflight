"""The 6 mm lens's per-mode calibration set shipped in config/camera-calibration/6mm.

Two optical artifacts (the 28 Oct distance-anchored exports, unchanged) and two placements
regenerated for the live rig file by scripts/camera/build_camera_placements.py.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pytest

from openflight.camera.calibrated_projection import (
    build_calibrated_camera_model,
    check_placement_against_rig,
    load_calibration_candidate,
)
from openflight.rig_geometry import RigGeometry

REPO = Path(__file__).resolve().parents[1]
SET_DIR = REPO / "config" / "camera-calibration" / "6mm"
RIG_PATH = REPO / "config" / "enclosure_v3_rig_geometry.json"
SCRIPT = REPO / "scripts" / "camera" / "build_camera_placements.py"
MODES = ("640x400", "1280x800")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _script():
    spec = importlib.util.spec_from_file_location("build_camera_placements", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _model(mode: str, *, pitch: float | None = None, roll: float | None = None):
    optical = _load(SET_DIR / f"{mode}.optical.json")
    placement = _load(SET_DIR / f"{mode}.placement.json")
    reference = placement["reference_pose_deg"]
    return build_calibrated_camera_model(
        optical,
        placement,
        observed_pitch_deg=reference["pitch"] if pitch is None else pitch,
        observed_roll_deg=reference["roll"] if roll is None else roll,
    )


@pytest.mark.parametrize("mode", MODES)
def test_each_placement_passes_the_live_rig_file(mode):
    rig = RigGeometry.from_json(RIG_PATH)
    placement = _load(SET_DIR / f"{mode}.placement.json")
    check_placement_against_rig(
        placement,
        rig_params_sha256=rig.snapshot()["sha256"],
        iwr_offset_mm=rig.iwr_offset_mm,
    )
    assert rig.snapshot()["sha256"].startswith("7151cc5d")
    # the lens stands where the rig file puts it (93 mm), not the 28 Oct 95 mm
    assert placement["camera_origin_lfu"] == pytest.approx([0.0, 0.0, 0.093], abs=1e-12)


@pytest.mark.parametrize("mode", MODES)
def test_each_optical_artifact_is_bound_to_its_saved_image_mode(mode):
    optical = _load(SET_DIR / f"{mode}.optical.json")
    profile = optical.get("candidate", optical)["mode_profile"]
    width, height = (int(part) for part in mode.split("x"))
    assert (profile["saved_image"]["width"], profile["saved_image"]["height"]) == (width, height)
    assert profile["lens_id"] == "6mm"


@pytest.mark.parametrize(
    ("mode", "sha256_prefix"), [("640x400", "6bc10e1acfac"), ("1280x800", "f4fc1e2bd18c")]
)
def test_the_optical_artifacts_are_the_decided_exports_byte_for_byte(mode, sha256_prefix):
    """STATUS A locks these two distance-anchored exports; they are copied, never rewritten."""
    digest = hashlib.sha256((SET_DIR / f"{mode}.optical.json").read_bytes()).hexdigest()
    assert digest.startswith(sha256_prefix)


def test_the_640x400_principal_point_is_the_calibrated_one():
    optical = _load(SET_DIR / "640x400.optical.json")
    projection = load_calibration_candidate(
        optical, mode_profile=optical.get("candidate", optical)["mode_profile"]
    )
    assert projection.camera_matrix[0, 2] == pytest.approx(330.67, abs=0.005)
    assert projection.camera_matrix[1, 2] == pytest.approx(155.85, abs=0.005)


@pytest.mark.parametrize(("mode", "elevation_deg"), [("640x400", 2.77), ("1280x800", 2.58)])
def test_the_reference_pose_composes_the_measured_boresight_elevation(mode, elevation_deg):
    """At the LIS3DH reading taken with the 28 Oct boards the optical axis points as the
    boards measured it (2.77 deg up at 640x400): the camera-vs-LIS3DH offset lives here."""
    model = _model(mode)
    boresight = model.optical_to_world_lfu @ np.array([0.0, 0.0, 1.0])
    elevation = math.degrees(math.asin(boresight[2]))
    assert elevation == pytest.approx(elevation_deg, abs=0.01)
    reference = _load(SET_DIR / f"{mode}.placement.json")["reference_pose_deg"]["pitch"]
    assert reference == pytest.approx(1.574, abs=0.001)


def test_the_lis3dh_pitch_moves_the_boresight_one_for_one():
    level = _model("640x400")
    tilted = _model("640x400", pitch=1.574 + 2.0)
    before = math.degrees(math.asin((level.optical_to_world_lfu @ [0, 0, 1])[2]))
    after = math.degrees(math.asin((tilted.optical_to_world_lfu @ [0, 0, 1])[2]))
    assert after - before == pytest.approx(2.0, abs=0.01)


def test_the_script_reproduces_the_committed_placements_byte_for_byte():
    built = _script().build_placements(SET_DIR, RIG_PATH)
    assert set(built) == {f"{mode}.placement.json" for mode in MODES}
    for name, text in built.items():
        committed = (SET_DIR / name).read_bytes().replace(b"\r\n", b"\n")
        assert text.encode("utf-8") == committed, name


def test_the_script_refuses_a_rig_without_the_rx_row_offset(tmp_path):
    rig = _load(RIG_PATH)
    rig["iwr_offset_mm"] = None
    path = tmp_path / "rig.json"
    path.write_text(json.dumps(rig), encoding="utf-8")
    with pytest.raises(ValueError, match="iwr_offset_mm"):
        _script().build_placements(SET_DIR, path)
