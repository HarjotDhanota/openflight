#!/usr/bin/env python3
"""Build an offline checkerboard intrinsic-calibration candidate from saved images."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

import numpy as np

# OpenCV exposes extension members that Pylint cannot introspect.
# pylint: disable=no-member

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


class CliError(Exception):
    """An operator-actionable input or output error."""


def _core():
    try:
        from openflight.camera.optical_calibration import (  # pylint: disable=import-outside-toplevel
            calibrate_checkerboard,
            validate_mode_profile,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        raise CliError(
            "camera calibration support is unavailable; install the camera dependencies "
            "and use a checkout containing optical_calibration.py"
        ) from exc
    return validate_mode_profile, calibrate_checkerboard


def _cv2():
    try:
        import cv2  # pylint: disable=import-outside-toplevel
    except (ImportError, ModuleNotFoundError) as exc:
        raise CliError(
            "OpenCV is required; install the project camera extra with `uv sync --extra camera`"
        ) from exc
    return cv2


def _object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise CliError(f"{label} must be a JSON object")
    return value


def _load_json(path: Path, label: str) -> dict:
    try:
        return _object(json.loads(path.read_text(encoding="utf-8")), label)
    except FileNotFoundError as exc:
        raise CliError(f"{label} does not exist: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CliError(f"cannot read {label} {path}: {exc}") from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pixel_sha256(image) -> str:
    descriptor = f"{image.dtype.str}:{','.join(map(str, image.shape))}:".encode("ascii")
    return _sha256(descriptor + image.tobytes(order="C"))


def detect_checkerboard(image, columns: int, rows: int):
    """Return subpixel corner coordinates or ``None`` for an undetected board."""
    cv2 = _cv2()
    pattern = (columns, rows)
    corners = None
    if hasattr(cv2, "findChessboardCornersSB"):
        flags = cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE
        found, corners = cv2.findChessboardCornersSB(image, pattern, flags)
    else:
        found = False
    if not found:
        flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
        found, corners = cv2.findChessboardCorners(image, pattern, flags)
        if found:
            criteria = (
                cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER,
                40,
                0.001,
            )
            corners = cv2.cornerSubPix(image, corners, (5, 5), (-1, -1), criteria)
    if not found or corners is None:
        return None
    return corners.reshape(-1, 2).astype(float).tolist()


def _positive_number(value: Any, label: str, *, integer: bool = False):
    if isinstance(value, bool):
        raise CliError(f"{label} must be positive")
    try:
        converted = int(value) if integer else float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CliError(f"{label} must be positive") from exc
    if not math.isfinite(converted) or converted <= 0 or (integer and converted != value):
        raise CliError(f"{label} must be a positive {'integer' if integer else 'number'}")
    return converted


def _board(manifest: Mapping[str, Any]) -> dict:
    board = _object(manifest.get("board"), "manifest.board")
    required = {"inner_corners_columns", "inner_corners_rows", "square_size_mm"}
    missing = sorted(required - board.keys())
    if missing:
        raise CliError(f"manifest.board is missing: {', '.join(missing)}")
    normalized = {
        "inner_corners_columns": _positive_number(
            board["inner_corners_columns"], "board.inner_corners_columns", integer=True
        ),
        "inner_corners_rows": _positive_number(
            board["inner_corners_rows"], "board.inner_corners_rows", integer=True
        ),
        "square_size_mm": _positive_number(board["square_size_mm"], "board.square_size_mm"),
    }
    if board.get("square_size_uncertainty_mm") is not None:
        normalized["square_size_uncertainty_mm"] = _positive_number(
            board["square_size_uncertainty_mm"], "board.square_size_uncertainty_mm"
        )
    if board.get("board_id") is not None:
        if not isinstance(board["board_id"], str) or not board["board_id"].strip():
            raise CliError("board.board_id must be a non-empty string")
        normalized["board_id"] = board["board_id"].strip()
    return normalized


def _expected_size(profile: Mapping[str, Any]) -> tuple[int, int]:
    saved = _object(profile.get("saved_image"), "mode_profile.saved_image")
    try:
        return int(saved["width"]), int(saved["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CliError("mode_profile.saved_image requires integer width and height") from exc


def _base_view(raw: Any, index: int) -> dict:
    view = _object(raw, f"manifest.views[{index}]")
    view_id = view.get("id")
    split = view.get("split")
    group_id = view.get("group_id")
    path = view.get("image")
    profile_hash = view.get("profile_sha256")
    if not isinstance(view_id, str) or not view_id.strip():
        raise CliError(f"manifest.views[{index}].id must be a non-empty string")
    if split not in {"fit", "validation"}:
        raise CliError(f"view {view_id!r} split must be 'fit' or 'validation'")
    if not isinstance(group_id, str) or not group_id.strip():
        raise CliError(f"view {view_id!r} group_id must be a non-empty string")
    if not isinstance(path, str) or not path:
        raise CliError(f"view {view_id!r} image must be a relative path")
    if PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute():
        raise CliError(f"view {view_id!r} image must be relative to the manifest")
    if not isinstance(profile_hash, str) or not profile_hash:
        raise CliError(f"view {view_id!r} requires profile_sha256")
    return {
        "id": view_id,
        "split": split,
        "group_id": group_id.strip(),
        "image": path,
        "profile_sha256": profile_hash,
        "status": "failed",
        "reason": None,
        "source_sha256": None,
        "image_sha256": None,
        "image_size": None,
        "corners": None,
    }


def _extract_views(  # pylint: disable=too-many-branches
    manifest_path: Path, manifest: dict, profile: dict, board: dict
) -> list[dict]:
    raw_views = manifest.get("views")
    if not isinstance(raw_views, list) or not raw_views:
        raise CliError("manifest.views must be a non-empty array")
    expected = _expected_size(profile)
    cv2 = _cv2()
    seen_ids: set[str] = set()
    group_splits: dict[str, str] = {}
    seen_pixels: dict[str, str] = {}
    results = []
    for index, raw in enumerate(raw_views):
        result = _base_view(raw, index)
        view_id = result["id"]
        if view_id in seen_ids:
            raise CliError(f"duplicate view id: {view_id}")
        seen_ids.add(view_id)
        prior_split = group_splits.setdefault(result["group_id"], result["split"])
        if prior_split != result["split"]:
            raise CliError(f"group_id {result['group_id']!r} spans fit and validation splits")
        if result["profile_sha256"] != profile["sha256"]:
            result["reason"] = "profile_sha256 does not match manifest.mode_profile"
            results.append(result)
            continue
        path = manifest_path.parent / result["image"]
        try:
            source = path.read_bytes()
        except FileNotFoundError:
            result["reason"] = "image file does not exist"
            results.append(result)
            continue
        except OSError as exc:
            result["reason"] = f"image file cannot be read: {exc}"
            results.append(result)
            continue
        result["source_sha256"] = _sha256(source)
        try:
            image = cv2.imdecode(np.frombuffer(source, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        except Exception as exc:
            result["reason"] = f"image decoding failed: {exc}"
            results.append(result)
            continue
        if image is None:
            result["reason"] = "image decoding failed"
            results.append(result)
            continue
        height, width = image.shape[:2]
        result["image_size"] = [width, height]
        result["image_sha256"] = _pixel_sha256(image)
        if (width, height) != expected:
            result["reason"] = (
                f"decoded image is {width}x{height}; profile saved image is "
                f"{expected[0]}x{expected[1]}"
            )
            results.append(result)
            continue
        duplicate = seen_pixels.get(result["image_sha256"])
        if duplicate is not None:
            result["reason"] = f"duplicate decoded pixels of view {duplicate!r}"
            results.append(result)
            continue
        seen_pixels[result["image_sha256"]] = view_id
        try:
            corners = detect_checkerboard(
                image, board["inner_corners_columns"], board["inner_corners_rows"]
            )
        except Exception as exc:
            result["reason"] = f"checkerboard detection failed: {exc}"
            results.append(result)
            continue
        if corners is None:
            result["reason"] = "checkerboard corners were not detected"
            results.append(result)
            continue
        result.update(status="detected", reason=None, corners=corners)
        results.append(result)
    return results


def _observation(view: Mapping[str, Any]) -> dict:
    keys = (
        "id",
        "split",
        "group_id",
        "profile_sha256",
        "image_sha256",
        "image_size",
        "corners",
        "status",
        "reason",
    )
    return {key: view[key] for key in keys if view.get(key) is not None}


def _summary(report: Mapping[str, Any]) -> str:
    counts = report["counts"]
    candidate = report.get("candidate")
    candidate_ok = candidate is not None and candidate.get("status", "ok") != "failed"
    lines = [
        "# Camera intrinsic calibration candidate",
        "",
        f"- Fit: {counts['fit_detected']} detected of {counts['fit_total']} declared views.",
        (
            f"- Validation: {counts['validation_detected']} detected of "
            f"{counts['validation_total']} declared views."
        ),
        f"- Failed or excluded: {counts['failed']} views.",
        f"- Candidate: {'produced' if candidate_ok else 'not produced'}.",
    ]
    if report.get("candidate_reason"):
        lines.append(f"- Reason: {report['candidate_reason']}")
    if candidate_ok:
        metrics = candidate.get("statistics", candidate.get("metrics", {}))
        if not metrics:
            metrics = {key: candidate[key] for key in ("fit", "validation") if key in candidate}
        if metrics:
            lines.extend(["", "## Fit and held-out validation statistics", "", "```json"])
            lines.append(json.dumps(metrics, indent=2, sort_keys=True))
            lines.append("```")
    lines.extend(
        [
            "",
            (
                "The validation views are held out from intrinsic fitting; their board poses are "
                "fitted only to evaluate the candidate."
            ),
            "",
            "Camera-mode binding is unverified because saved images cannot prove sensor crop, "
            "readout, preview mirroring, or driver transforms. This candidate is not applied to "
            "live or rig defaults.",
            "",
            "This offline result does not qualify optical or physical accuracy.",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_write(path: Path, data: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def calibrate(manifest_path: Path, output_dir: Path, overwrite: bool) -> dict:
    """Run extraction and calibration, then write all three output artifacts."""
    manifest = _load_json(manifest_path, "manifest")
    if manifest.get("version") != 1:
        raise CliError("manifest.version must be 1")
    board = _board(manifest)
    validate_profile, fit = _core()
    try:
        profile = validate_profile(_object(manifest.get("mode_profile"), "manifest.mode_profile"))
    except (TypeError, ValueError) as exc:
        raise CliError(f"invalid mode_profile: {exc}") from exc
    views = _extract_views(manifest_path, manifest, profile, board)
    paths = [
        output_dir / "camera_intrinsics_candidate.json",
        output_dir / "camera_intrinsics_report.json",
        output_dir / "camera_intrinsics_summary.md",
    ]
    protected = {manifest_path.resolve()}
    protected.update((manifest_path.parent / view["image"]).resolve() for view in views)
    collisions = [path for path in paths if path.resolve() in protected]
    if collisions:
        raise CliError(
            "output artifact path overlaps the manifest or a source image: "
            + ", ".join(str(path) for path in collisions)
        )
    existing = [path.name for path in paths if path.exists()]
    if existing and not overwrite:
        raise CliError(
            f"output artifacts already exist in {output_dir}: {', '.join(existing)}; "
            "pass --overwrite to replace them"
        )
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CliError(f"cannot create output directory {output_dir}: {exc}") from exc
    fit_views = [view for view in views if view["split"] == "fit"]
    validation_views = [view for view in views if view["split"] == "validation"]
    fit_valid = [view for view in fit_views if view["status"] == "detected"]
    validation_valid = [view for view in validation_views if view["status"] == "detected"]
    fit_groups = {view["group_id"] for view in fit_valid}
    validation_groups = {view["group_id"] for view in validation_valid}
    candidate = None
    reason = None
    if len(fit_groups) < 10 or len(validation_groups) < 3:
        reason = (
            "insufficient independent detected pose groups: requires at least 10 fit and 3 "
            f"validation; found {len(fit_groups)} fit and {len(validation_groups)} validation"
        )
    else:
        try:
            candidate = fit(
                board=board,
                mode_profile=profile,
                observations=[_observation(view) for view in views],
            )
        except Exception as exc:  # OpenCV exposes numerical failures as cv2.error.
            reason = f"core calibration rejected the observations: {exc}"
        if candidate is not None and candidate.get("status", "ok") == "failed":
            reason = candidate.get("reason", "core calibration did not produce a candidate")
    counts = {
        "fit_total": len(fit_views),
        "fit_detected": len(fit_valid),
        "fit_detected_groups": len(fit_groups),
        "validation_total": len(validation_views),
        "validation_detected": len(validation_valid),
        "validation_detected_groups": len(validation_groups),
        "failed": sum(view["status"] != "detected" for view in views),
    }
    report = {
        "version": 1,
        "board": board,
        "mode_profile": profile,
        "binding_status": "unverified",
        "accuracy_qualification": "not_qualified",
        "counts": counts,
        "candidate": candidate,
        "candidate_reason": reason,
        "views": views,
    }
    candidate_document = {
        "version": 1,
        "candidate": candidate,
        "reason": reason,
        "mode_profile_sha256": profile["sha256"],
    }
    try:
        _atomic_write(
            paths[0],
            json.dumps(candidate_document, indent=2, sort_keys=True, allow_nan=False) + "\n",
        )
        _atomic_write(
            paths[1], json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )
        _atomic_write(paths[2], _summary(report))
    except OSError as exc:
        raise CliError(f"cannot write output artifacts in {output_dir}: {exc}") from exc
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    hash_parser = commands.add_parser("profile-hash", help="print a validated profile hash")
    hash_parser.add_argument("profile", type=Path, help="mode-profile JSON file")
    run = commands.add_parser("calibrate", help="extract corners and fit a candidate")
    run.add_argument("manifest", type=Path, help="calibration manifest JSON")
    run.add_argument("--output-dir", required=True, type=Path, help="artifact directory")
    run.add_argument("--overwrite", action="store_true", help="replace named artifacts")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the selected command and return a process exit status."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "profile-hash":
            validate_profile, _ = _core()
            try:
                profile = validate_profile(_load_json(args.profile, "mode profile"))
            except (TypeError, ValueError) as exc:
                raise CliError(f"invalid mode profile: {exc}") from exc
            print(profile["sha256"])
            return 0
        report = calibrate(args.manifest, args.output_dir, args.overwrite)
        if report["candidate"] is None or report["candidate"].get("status", "ok") == "failed":
            print(f"No candidate: {report['candidate_reason']}", file=sys.stderr)
            return 1
        print(f"Wrote calibration candidate and report to {args.output_dir}")
        return 0
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
