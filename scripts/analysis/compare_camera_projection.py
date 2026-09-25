"""Compare an intrinsic-calibration candidate with the saved legacy camera model.

This is an offline diagnostic. Its output measures model disagreement on supplied
saved-image pixels; it does not qualify either model's accuracy or promote a
calibration for live use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from openflight.camera.geometry import intersect_radar_range_sphere, unit_world_rays


class CliError(ValueError):
    """A diagnostic input or output error suitable for command-line display."""


def _core():
    from openflight.camera.calibrated_projection import (  # pylint: disable=import-outside-toplevel
        inspect_capture_compatibility,
        load_calibration_candidate,
    )

    return load_calibration_candidate, inspect_capture_compatibility


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CliError(f"cannot read {label} {path}: {exc}") from exc
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliError(f"invalid JSON in {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CliError(f"{label} must be a JSON object")
    return value, raw


def _fingerprint(path: Path, raw: bytes) -> dict[str, Any]:
    return {"path": str(path), "byte_count": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _finite_array(value: Any, shape: tuple[int | None, ...], label: str) -> np.ndarray:
    try:
        array = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as exc:
        raise CliError(f"{label} must contain numbers") from exc
    if array.ndim != len(shape) or any(
        want is not None and got != want for got, want in zip(array.shape, shape)
    ):
        raise CliError(f"{label} has invalid shape")
    if not np.all(np.isfinite(array)):
        raise CliError(f"{label} must contain only finite numbers")
    return array


def _ranges(setup: dict[str, Any], count: int) -> np.ndarray | None:
    range_fields = ("radar_ranges_m", "camera_origin_lfu", "radar_origin_lfu")
    present = [field in setup for field in range_fields]
    if any(present) and not all(present):
        raise CliError(
            "setup radar_ranges_m, camera_origin_lfu, and radar_origin_lfu "
            "must be supplied together"
        )
    if "radar_ranges_m" not in setup:
        return None
    try:
        values = np.asarray(setup["radar_ranges_m"], dtype=float)
    except (TypeError, ValueError) as exc:
        raise CliError("setup.radar_ranges_m must contain numbers") from exc
    if values.ndim == 0:
        values = np.full(count, float(values))
    if values.shape != (count,) or not np.all(np.isfinite(values)) or np.any(values <= 0):
        raise CliError(
            "setup.radar_ranges_m must be a positive finite scalar or one value per pixel"
        )
    return values


def _validate_output(output: Path, sources: list[Path], overwrite: bool) -> None:
    resolved = output.resolve()
    if any(resolved == source.resolve() for source in sources):
        raise CliError("output path overlaps an input file")
    if output.exists() and not overwrite:
        raise CliError(f"output already exists: {output}; pass --overwrite to replace it")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    text = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise CliError(f"cannot write output {path}: {exc}") from exc


def compare(candidate_path: Path, profile_path: Path, setup_path: Path) -> dict[str, Any]:
    """Build an offline per-pixel disagreement report from frozen JSON inputs."""
    candidate_doc, candidate_raw = _read_json(candidate_path, "candidate")
    profile, profile_raw = _read_json(profile_path, "mode profile")
    setup, setup_raw = _read_json(setup_path, "setup")
    if setup.get("version") != 1:
        raise CliError("setup.version must be 1")
    pixels = _finite_array(setup.get("pixel_points"), (None, 2), "setup.pixel_points")
    if len(pixels) == 0:
        raise CliError("setup.pixel_points must not be empty")
    rotation = _finite_array(
        setup.get("optical_rdf_to_world_lfu"), (3, 3), "setup.optical_rdf_to_world_lfu"
    )
    legacy = setup.get("legacy")
    if not isinstance(legacy, dict):
        raise CliError("setup.legacy must be an object")
    saved = profile.get("saved_image")
    if not isinstance(saved, dict):
        raise CliError("mode profile.saved_image must be an object")
    load_candidate, _inspect = _core()
    try:
        calibrated = load_candidate(candidate_doc, mode_profile=profile)
        candidate_rays = calibrated.pixel_rays_lfu(pixels, optical_to_world_lfu=rotation)
        legacy_rays = unit_world_rays(
            pixels,
            focal_px=float(legacy["focal_px"]),
            pitch_rad=float(legacy["pitch_rad"]),
            image_width_px=int(saved["width"]),
            image_height_px=int(saved["height"]),
            horizontal_pixel_sign=float(legacy["horizontal_pixel_sign"]),
            roll_correction_deg=float(legacy["roll_correction_deg"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CliError(f"projection input rejected: {exc}") from exc
    dot = np.sum(candidate_rays * legacy_rays, axis=1)
    cross_norm = np.linalg.norm(np.cross(candidate_rays, legacy_rays), axis=1)
    angles = np.degrees(np.arctan2(cross_norm, dot))
    ranges = _ranges(setup, len(pixels))
    candidate_positions = legacy_positions = None
    if ranges is not None:
        camera = _finite_array(setup.get("camera_origin_lfu"), (3,), "setup.camera_origin_lfu")
        radar = _finite_array(setup.get("radar_origin_lfu"), (3,), "setup.radar_origin_lfu")
        candidate_positions = []
        legacy_positions = []
        try:
            for index, radar_range in enumerate(ranges):
                candidate_positions.append(
                    calibrated.reconstruct_at_radar_range(
                        pixels[index],
                        float(radar_range),
                        optical_to_world_lfu=rotation,
                        camera_origin_lfu=camera,
                        radar_origin_lfu=radar,
                    ).tolist()
                )
                legacy_positions.append(
                    intersect_radar_range_sphere(
                        legacy_rays[index],
                        float(radar_range),
                        camera_origin_lfu=camera,
                        radar_origin_lfu=radar,
                    ).tolist()
                )
        except ValueError as exc:
            raise CliError(f"radar-range reconstruction rejected: {exc}") from exc
    points = []
    for index, pixel in enumerate(pixels):
        item = {
            "pixel_px": pixel.tolist(),
            "candidate_ray_lfu": candidate_rays[index].tolist(),
            "legacy_ray_lfu": legacy_rays[index].tolist(),
            "angular_difference_deg": float(angles[index]),
        }
        if candidate_positions is not None and legacy_positions is not None:
            item.update(
                {
                    "radar_range_m": float(ranges[index]),
                    "candidate_position_lfu_m": candidate_positions[index],
                    "legacy_position_lfu_m": legacy_positions[index],
                    "position_difference_m": float(
                        np.linalg.norm(
                            np.asarray(candidate_positions[index])
                            - np.asarray(legacy_positions[index])
                        )
                    ),
                }
            )
        points.append(item)
    return {
        "version": 1,
        "diagnostic": "candidate_vs_legacy_projection",
        "interpretation": "model_disagreement_not_accuracy",
        "accuracy_qualified": False,
        "mode_binding": "unverified",
        "live_use_authorized": False,
        "coordinates": {
            "rotation_input": "physical_optical_rdf_to_world_lfu",
            "ray_output": "world_lfu_unit_vector",
            "position_output": "world_lfu_meters",
            "pixels": "supplied_saved_image_pixels",
        },
        "inputs": {
            "candidate": _fingerprint(candidate_path, candidate_raw),
            "mode_profile": _fingerprint(profile_path, profile_raw),
            "setup": _fingerprint(setup_path, setup_raw),
        },
        "point_count": len(points),
        "points": points,
    }


def inspect_capture(candidate_path: Path, profile_path: Path, capture_path: Path) -> dict[str, Any]:
    """Report conservative capture compatibility without verifying a projection."""
    candidate_doc, candidate_raw = _read_json(candidate_path, "candidate")
    profile, profile_raw = _read_json(profile_path, "mode profile")
    capture, capture_raw = _read_json(capture_path, "capture metadata")
    load_candidate, inspector = _core()
    try:
        load_candidate(candidate_doc, mode_profile=profile)
        compatibility = inspector(capture, mode_profile=profile)
    except (TypeError, ValueError) as exc:
        raise CliError(f"compatibility input rejected: {exc}") from exc
    return {
        "version": 1,
        "diagnostic": "capture_projection_compatibility",
        "compatibility": {"status": compatibility.status, "reasons": list(compatibility.reasons)},
        "accuracy_qualified": False,
        "mode_binding": "unverified",
        "verified_projection_authorized": False,
        "live_use_authorized": False,
        "inputs": {
            "candidate": _fingerprint(candidate_path, candidate_raw),
            "mode_profile": _fingerprint(profile_path, profile_raw),
            "capture_metadata": _fingerprint(capture_path, capture_raw),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compare_parser = commands.add_parser("compare", help="compare rays on supplied saved pixels")
    inspect_parser = commands.add_parser("inspect-capture", help="inspect capture compatibility")
    for command in (compare_parser, inspect_parser):
        command.add_argument("candidate", type=Path)
        command.add_argument("profile", type=Path)
        command.add_argument("input", type=Path, help="setup or capture-metadata JSON")
        command.add_argument("--output", required=True, type=Path)
        command.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one diagnostic command and return its documented process status."""
    args = _parser().parse_args(argv)
    try:
        _validate_output(args.output, [args.candidate, args.profile, args.input], args.overwrite)
        if args.command == "compare":
            report = compare(args.candidate, args.profile, args.input)
            status = 0
        else:
            report = inspect_capture(args.candidate, args.profile, args.input)
            compatibility = report["compatibility"]["status"]
            status = 1 if compatibility == "incompatible" else 3
        _atomic_json(args.output, report)
        print(f"Wrote offline diagnostic report to {args.output}")
        return status
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
