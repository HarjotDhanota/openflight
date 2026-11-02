#!/usr/bin/env python3
"""Regenerate a lens's per-mode camera placements for the loaded rig file.

A calibration set directory (``config/camera-calibration/<lens>/``) holds, per saved-image
mode, the optical artifact ``<W>x<H>.optical.json`` (never rewritten here) and the
placement ``<W>x<H>.placement.json`` this script writes, from the recorded inputs in
``placement-inputs.json`` and the rig file:

- orientation: the normalised mean of the mode's recorded camera-frame gravity-up vectors
  (the 28 Oct board poses), heading along the boresight's horizontal projection;
- reference pose: the LIS3DH reading paired with those boards; a later reading moves the
  camera by the difference (``calibrated_projection.build_calibrated_camera_model``);
- camera origin: straight above the world origin at the rig file's lens height;
- radar origin: the rig file's IWR RX-row offset turned by that orientation (what
  ``check_placement_against_rig`` checks).

Run from the repository root::

    python scripts/camera/build_camera_placements.py config/camera-calibration/6mm \
        --rig-geometry config/enclosure_v3_rig_geometry.json

``--from-research DIR`` first rebuilds ``placement-inputs.json`` from the 28 Oct
calibration folder (``research/lens-calibration/2026-10-27-01``), checking its hashes.
The output is deterministic: the same inputs and rig file give the same bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from openflight.camera.calibrated_projection import (
    build_calibrated_camera_model,
    calibrated_camera_model_from_snapshot,
    check_placement_against_rig,
)
from openflight.rig_geometry import RigGeometry

INPUTS_NAME = "placement-inputs.json"
INPUTS_SCHEMA = "openflight.camera.placement_inputs"
OPTICAL_SUFFIX = ".optical.json"
PLACEMENT_SUFFIX = ".placement.json"


def rotation_from_up(up: Any) -> np.ndarray:
    """Optical RDF to world LFU: world up is ``up``, forward the boresight's horizontal."""
    up = np.asarray(up, dtype=float)
    if up.shape != (3,) or not np.isfinite(up).all() or np.linalg.norm(up) < 1e-9:
        raise ValueError("camera up must be a finite nonzero three-vector")
    up = up / np.linalg.norm(up)
    forward = np.array([0.0, 0.0, 1.0]) - up[2] * up
    if np.linalg.norm(forward) < 1e-9:
        raise ValueError("a vertical boresight has no horizontal heading")
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, up)
    return np.array([right, forward, up])


def _text(payload: Mapping) -> str:
    return json.dumps(payload, indent=2, allow_nan=False) + "\n"


def _lf_sha256(path: Path) -> str:
    """SHA-256 of a text file's bytes with CRLF read as LF (the same on every checkout)."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _rig(rig_path: Path) -> RigGeometry:
    rig = RigGeometry.from_json(rig_path)
    if rig.iwr_offset_mm is None:
        raise ValueError("the rig file lacks iwr_offset_mm (the IWR RX-row offset)")
    if rig.lens_height_above_floor_mm is None:
        raise ValueError("the rig file lacks lens_height_above_floor_mm")
    return rig


def build_placements(set_dir: Path, rig_path: Path) -> dict[str, str]:
    """Every mode's placement text for ``set_dir``'s inputs and the rig file, checked."""
    set_dir, rig_path = Path(set_dir), Path(rig_path)
    rig = _rig(rig_path)
    rig_sha = rig.snapshot()["sha256"]
    inputs_path = set_dir / INPUTS_NAME
    inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
    if inputs.get("schema") != INPUTS_SCHEMA or inputs.get("version") != 1:
        raise ValueError(f"{inputs_path} is not {INPUTS_SCHEMA} v1")
    lens_height_mm = float(rig.lens_height_above_floor_mm)
    reference = dict(inputs["reference_pose_deg"])
    provenance = inputs["provenance"]
    built: dict[str, str] = {}
    for mode, recorded in sorted(inputs["modes"].items()):
        optical_path = set_dir / f"{mode}{OPTICAL_SUFFIX}"
        optical = json.loads(optical_path.read_text(encoding="utf-8"))
        saved = optical.get("candidate", optical)["mode_profile"]["saved_image"]
        if f"{saved['width']}x{saved['height']}" != mode:
            raise ValueError(f"{optical_path.name} is calibrated for another mode")
        mount = rotation_from_up(np.mean(np.asarray(recorded["gravity_up_in_camera_rdf"]), axis=0))
        camera = np.array([0.0, 0.0, lens_height_mm / 1000.0])
        radar = camera + mount @ (np.asarray(rig.iwr_offset_mm, dtype=float) / 1000.0)
        placement = {
            "schema": "openflight.camera.placement",
            "version": 1,
            "world_frame": "target_lfu",
            "rig_geometry_sha256": rig_sha,
            "optical_to_enclosure_lfu": mount.tolist(),
            "enclosure_to_target_lfu": np.eye(3).tolist(),
            "camera_origin_lfu": camera.tolist(),
            "radar_origin_lfu": radar.tolist(),
            "enclosure_pivot_lfu": [0.0, 0.0, 0.0],
            "reference_pose_deg": reference,
            "rig_offset_consistency_tolerance_m": inputs["rig_offset_consistency_tolerance_m"],
            "origin_provenance": {
                "camera_origin_lfu": provenance["camera_origin"].format(
                    lens_height_mm=lens_height_mm
                ),
                "radar_origin_lfu": provenance["radar_origin"].format(
                    iwr_offset_mm=list(rig.iwr_offset_mm)
                ),
                "enclosure_pivot_lfu": provenance["pivot"],
            },
            "accuracy_qualified": False,
            "placement_evidence": {
                **inputs["source_sha256"],
                "placement_inputs_sha256": _lf_sha256(inputs_path),
                "optical_artifact_sha256": _lf_sha256(optical_path),
                "rig_file_sha256": _lf_sha256(rig_path),
                "rig_file_sha256_basis": "lf_normalized_bytes",
                "orientation_capture_ids": recorded["orientation_capture_ids"],
                "heading": provenance["heading"],
                "reference_pose_basis": provenance["reference_pose_basis"],
                "consumer_frame": provenance["consumer_frame"],
                "limitations": provenance["limitations"],
                "generator": "scripts/camera/build_camera_placements.py",
            },
        }
        check_placement_against_rig(
            placement, rig_params_sha256=rig_sha, iwr_offset_mm=rig.iwr_offset_mm
        )
        model = build_calibrated_camera_model(
            optical,
            placement,
            observed_pitch_deg=reference["pitch"],
            observed_roll_deg=reference["roll"],
        )
        np.testing.assert_allclose(model.optical_to_world_lfu, mount, atol=1e-12)
        restored = calibrated_camera_model_from_snapshot(model.snapshot)
        np.testing.assert_allclose(restored.optical_to_world_lfu, mount, atol=1e-12)
        built[f"{mode}{PLACEMENT_SUFFIX}"] = _text(placement)
    return built


def inputs_from_research(research_dir: Path) -> dict:
    """``placement-inputs.json`` from the 28 Oct calibration folder, hash-checked."""
    root = Path(research_dir)
    candidate_path = root / "distance-anchored-candidate-001.json"
    freeze = json.loads((root / "distance-anchor-freeze-001.json").read_text(encoding="utf-8"))
    if (
        hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        != freeze["scratch_candidate_sha256"]
    ):
        raise ValueError("the distance-anchored candidate does not match its freeze record")
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    method_path = root / "integration-001" / "placement-method-001.json"
    method = json.loads(method_path.read_text(encoding="utf-8"))
    tilt_path = root / "integration-001" / "placement-tilt-reference.json"
    tilt = json.loads(tilt_path.read_text(encoding="utf-8"))
    if not tilt["owner_confirms_unchanged_since_board_capture_and_aimed"]:
        raise ValueError("the tilt reference was not paired with the board capture")
    if tilt["control_registers_before"] != tilt["control_registers_after"]:
        raise ValueError("the tilt reference left the LIS3DH registers changed")
    modes = {
        result["mode"]: {
            "gravity_up_in_camera_rdf": [
                pose["gravity_up_in_camera_rdf"]
                for pose in result["physical_calibration_input_poses"]
            ],
            "orientation_capture_ids": method["orientation_inputs"],
        }
        for result in candidate["results"]
    }
    return {
        "schema": INPUTS_SCHEMA,
        "version": 1,
        "lens_id": "6mm",
        "date": method["date"],
        "note": (
            "Recorded inputs of the 28 Oct 2026 placements (research/lens-calibration/"
            "2026-10-27-01/integration-001). Orientation, reference pose and tolerance are "
            "unchanged; the lens height and the IWR RX-row offset come from the rig file each "
            "time the placements are built (lens 93 mm and RX row 22.3 mm behind the lens "
            "since 2 Nov 2026; the 28 Oct files used 95 mm and 30 mm)."
        ),
        "modes": modes,
        "reference_pose_deg": tilt["reference_pose_deg"],
        "rig_offset_consistency_tolerance_m": method["consistency_tolerance_m"]["value"],
        "provenance": {
            "camera_origin": (
                "World horizontal origin directly below the lens; lens height "
                "{lens_height_mm:g} mm above the ground the enclosure stands on, from the rig "
                "file."
            ),
            "radar_origin": (
                "The rig file's IWR RX-row offset {iwr_offset_mm} mm (camera image axes) turned "
                "by the reference optical rotation, as the placement consistency check requires. "
                "Not an independent measurement."
            ),
            "pivot": method["pivot"],
            "heading": method["heading"],
            "reference_pose_basis": method["reference_pose"],
            "consumer_frame": (
                "Reference optical rotation in the owner-declared LFU frame; subsequent LIS3DH "
                "changes use the existing reference-pose delta."
            ),
            "limitations": [
                "Fixed-setup candidate; physical mechanical pivot unmeasured",
                "Optical yaw relative to the housing and the shot line unmeasured",
                "Phone-level, print/floor and optical-yaw uncertainties unqualified",
            ],
        },
        "source_sha256": {
            "method_sha256": hashlib.sha256(method_path.read_bytes()).hexdigest(),
            "optical_candidate_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
            "tilt_reference_sha256": hashlib.sha256(tilt_path.read_bytes()).hexdigest(),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("set_dir", type=Path, help="the lens's calibration set directory")
    parser.add_argument("--rig-geometry", type=Path, required=True, help="the rig file")
    parser.add_argument(
        "--from-research",
        type=Path,
        default=None,
        help="rebuild placement-inputs.json from this 28 Oct calibration folder first",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="only compare with the files on disk; exit 1 on a difference",
    )
    args = parser.parse_args(argv)
    if args.from_research is not None:
        text = _text(inputs_from_research(args.from_research))
        (args.set_dir / INPUTS_NAME).write_text(text, encoding="utf-8", newline="\n")
    built = build_placements(args.set_dir, args.rig_geometry)
    differs = []
    for name, text in built.items():
        path = args.set_dir / name
        current = path.read_bytes().replace(b"\r\n", b"\n") if path.exists() else None
        if current == text.encode("utf-8"):
            print(f"{name}: unchanged")
            continue
        differs.append(name)
        if not args.check:
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"{name}: written")
        else:
            print(f"{name}: differs")
    return 1 if args.check and differs else 0


if __name__ == "__main__":
    sys.exit(main())
