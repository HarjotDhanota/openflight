from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "analysis" / "calibrate_camera_intrinsics.py"
SPEC = importlib.util.spec_from_file_location("calibrate_camera_intrinsics", SCRIPT)
assert SPEC and SPEC.loader
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def profile(width=80, height=60):
    return {
        "version": 1,
        "camera_id": "ov9281-serial-1",
        "lens_id": "lens-1",
        "focus_id": "focus-stop-1",
        "sensor_output": {
            "mode_id": "ov9281-1280x800-y8",
            "width": 1280,
            "height": 800,
            "bit_depth": 8,
            "raw_format": "Y8",
        },
        "saved_image": {
            "width": width,
            "height": height,
            "stream": "raw-gray",
            "rotate_180": False,
            "mirror": False,
        },
        "crop_readout_mapping": {
            "native_sensor_crop": None,
            "scaler_crop": None,
            "driver_vertical_offset_px": None,
            "sensor_output_mapping": None,
            "saved_image_mapping": None,
        },
    }


def write_manifest(path, normalized_profile, views):
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "board": {
                    "inner_corners_columns": 7,
                    "inner_corners_rows": 5,
                    "square_size_mm": 20.0,
                },
                "mode_profile": normalized_profile,
                "views": views,
            }
        ),
        encoding="utf-8",
    )


def save_image(path, image, extension=None):
    suffix = extension or path.suffix
    ok, encoded = cv2.imencode(suffix, image)
    assert ok
    path.write_bytes(encoded.tobytes())


def render_checkerboard_views(directory, normalized_profile):
    columns, rows = 7, 5
    square_mm = 24.0
    pixels_per_square = 48
    texture = np.full(
        ((rows + 1) * pixels_per_square, (columns + 1) * pixels_per_square),
        255,
        np.uint8,
    )
    for row in range(rows + 1):
        for column in range(columns + 1):
            if (row + column) % 2 == 0:
                texture[
                    row * pixels_per_square : (row + 1) * pixels_per_square,
                    column * pixels_per_square : (column + 1) * pixels_per_square,
                ] = 0
    texture_corners = np.float32(
        [
            [0, 0],
            [texture.shape[1] - 1, 0],
            [texture.shape[1] - 1, texture.shape[0] - 1],
            [0, texture.shape[0] - 1],
        ]
    )
    board_corners = np.float32(
        [
            [-square_mm, -square_mm, 0],
            [columns * square_mm, -square_mm, 0],
            [columns * square_mm, rows * square_mm, 0],
            [-square_mm, rows * square_mm, 0],
        ]
    )
    camera_matrix = np.array([[520.0, 0, 320.0], [0, 515.0, 240.0], [0, 0, 1.0]])
    poses = [
        (
            (-0.20 + 0.07 * (index % 5), -0.18 + 0.09 * (index % 4), 0.025 * index),
            (-72.0 + 12.0 * (index % 4), -50.0 + 14.0 * (index % 3), 480.0 + 24.0 * (index % 5)),
        )
        for index in range(13)
    ]
    views = []
    for index, (rotation, translation) in enumerate(poses):
        projected, _ = cv2.projectPoints(
            board_corners,
            np.asarray(rotation, dtype=float),
            np.asarray(translation, dtype=float),
            camera_matrix,
            np.zeros(5),
        )
        transform = cv2.getPerspectiveTransform(
            texture_corners, projected.reshape(4, 2).astype(np.float32)
        )
        image = cv2.warpPerspective(
            texture,
            transform,
            (640, 480),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=180,
        )
        name = f"perspective-{index}.png"
        save_image(directory / name, image)
        views.append(
            {
                "id": f"perspective-{index}",
                "group_id": f"pose-{index}",
                "image": name,
                "split": "fit" if index < 10 else "validation",
                "profile_sha256": normalized_profile["sha256"],
            }
        )
    return views


def test_actual_checkerboard_detector_finds_rendered_board():
    columns, rows, square = 7, 5, 36
    board = np.full(((rows + 1) * square, (columns + 1) * square), 255, np.uint8)
    for row in range(rows + 1):
        for column in range(columns + 1):
            if (row + column) % 2 == 0:
                board[
                    row * square : (row + 1) * square,
                    column * square : (column + 1) * square,
                ] = 0
    image = cv2.copyMakeBorder(board, 45, 45, 45, 45, cv2.BORDER_CONSTANT, value=180)

    corners = cli.detect_checkerboard(image, columns, rows)

    assert corners is not None
    assert len(corners) == columns * rows


def test_end_to_end_perspective_images_produce_held_out_candidate(tmp_path):
    validate, _ = cli._core()
    normalized = validate(profile(640, 480))
    views = render_checkerboard_views(tmp_path, normalized)
    manifest = tmp_path / "manifest.json"
    write_manifest(manifest, normalized, views)
    output = tmp_path / "results"

    assert cli.main(["calibrate", str(manifest), "--output-dir", str(output)]) == 0

    report = json.loads((output / "camera_intrinsics_report.json").read_text(encoding="utf-8"))
    candidate = report["candidate"]
    assert candidate["status"] == "candidate"
    assert candidate["mode_binding"]["status"] == "unverified"
    assert len(report["views"]) == 13
    assert report["counts"]["fit_detected_groups"] == 10
    assert report["counts"]["validation_detected_groups"] == 3
    assert np.isfinite(candidate["fit"]["rms_px"])
    assert np.isfinite(candidate["validation"]["rms_px"])
    assert candidate["fit"]["rms_px"] < 1.0
    assert candidate["validation"]["rms_px"] < 1.0
    summary = (output / "camera_intrinsics_summary.md").read_text(encoding="utf-8")
    assert '"fit"' in summary
    assert '"validation"' in summary
    assert "Camera-mode binding is unverified" in summary


def test_solver_error_writes_null_candidate_and_complete_report(tmp_path, monkeypatch):
    validate, _ = cli._core()
    normalized = validate(profile(640, 480))
    views = render_checkerboard_views(tmp_path, normalized)
    manifest = tmp_path / "manifest.json"
    write_manifest(manifest, normalized, views)

    def failed_solver(**_kwargs):
        raise cv2.error("synthetic calibration failure")

    monkeypatch.setattr(cli, "_core", lambda: (validate, failed_solver))
    output = tmp_path / "failed-results"

    assert cli.main(["calibrate", str(manifest), "--output-dir", str(output)]) == 1
    report = json.loads((output / "camera_intrinsics_report.json").read_text(encoding="utf-8"))
    candidate = json.loads(
        (output / "camera_intrinsics_candidate.json").read_text(encoding="utf-8")
    )
    assert report["candidate"] is None
    assert candidate["candidate"] is None
    assert "synthetic calibration failure" in report["candidate_reason"]
    assert len(report["views"]) == 13
    assert all(view["status"] == "detected" for view in report["views"])


def test_failures_dimension_profile_and_duplicate_content_are_retained(tmp_path):
    validate, _ = cli._core()
    normalized = validate(profile())
    base = np.arange(80 * 60, dtype=np.uint8).reshape(60, 80)
    save_image(tmp_path / "good.png", base)
    save_image(tmp_path / "duplicate.bmp", base, ".bmp")
    save_image(tmp_path / "wrong.png", np.zeros((30, 40), np.uint8))
    (tmp_path / "bad.png").write_bytes(b"not an image")
    views = [
        {
            "id": "good",
            "group_id": "g1",
            "image": "good.png",
            "split": "fit",
            "profile_sha256": normalized["sha256"],
        },
        {
            "id": "duplicate",
            "group_id": "g2",
            "image": "duplicate.bmp",
            "split": "validation",
            "profile_sha256": normalized["sha256"],
        },
        {
            "id": "wrong-size",
            "group_id": "g3",
            "image": "wrong.png",
            "split": "fit",
            "profile_sha256": normalized["sha256"],
        },
        {
            "id": "bad",
            "group_id": "g4",
            "image": "bad.png",
            "split": "validation",
            "profile_sha256": normalized["sha256"],
        },
        {
            "id": "missing",
            "group_id": "g5",
            "image": "missing.png",
            "split": "fit",
            "profile_sha256": normalized["sha256"],
        },
        {
            "id": "mixed",
            "group_id": "g6",
            "image": "good.png",
            "split": "validation",
            "profile_sha256": "0" * 64,
        },
    ]
    manifest = tmp_path / "manifest.json"
    write_manifest(manifest, normalized, views)
    output = tmp_path / "out"

    result = cli.main(["calibrate", str(manifest), "--output-dir", str(output)])

    assert result == 1
    report = json.loads((output / "camera_intrinsics_report.json").read_text(encoding="utf-8"))
    by_id = {view["id"]: view for view in report["views"]}
    assert by_id["good"]["source_sha256"]
    assert by_id["good"]["image_sha256"]
    assert by_id["duplicate"]["source_sha256"] != by_id["good"]["source_sha256"]
    assert by_id["duplicate"]["image_sha256"] == by_id["good"]["image_sha256"]
    assert "duplicate decoded pixels" in by_id["duplicate"]["reason"]
    assert "80x60" in by_id["wrong-size"]["reason"]
    assert by_id["bad"]["reason"] == "image decoding failed"
    assert by_id["missing"]["reason"] == "image file does not exist"
    assert "profile_sha256" in by_id["mixed"]["reason"]
    assert report["candidate"] is None
    assert report["counts"]["fit_total"] == 3
    assert report["counts"]["validation_total"] == 3


def test_explicit_splits_reach_core_and_outputs_are_protected(tmp_path, monkeypatch):
    validate, _ = cli._core()
    normalized = validate(profile())
    observations = []
    views = []
    for index in range(13):
        image = np.full((60, 80), index, np.uint8)
        name = f"view-{index}.png"
        save_image(tmp_path / name, image)
        split = "fit" if index < 10 else "validation"
        views.append(
            {
                "id": f"v{index}",
                "group_id": f"pose-{index}",
                "image": name,
                "split": split,
                "profile_sha256": normalized["sha256"],
            }
        )
    manifest = tmp_path / "manifest.json"
    write_manifest(manifest, normalized, views)

    corners = [[float(x), float(y)] for y in range(5) for x in range(7)]
    monkeypatch.setattr(cli, "detect_checkerboard", lambda *_args: corners)

    def fake_fit(**kwargs):
        observations.extend(kwargs["observations"])
        return {
            "statistics": {
                "fit": {"rms_px": 0.2},
                "validation": {"rms_px": 0.3, "pose_fitted_per_view": True},
            }
        }

    monkeypatch.setattr(cli, "_core", lambda: (validate, fake_fit))
    output = tmp_path / "results"

    assert cli.main(["calibrate", str(manifest), "--output-dir", str(output)]) == 0
    assert [item["split"] for item in observations].count("fit") == 10
    assert [item["split"] for item in observations].count("validation") == 3
    assert (output / "camera_intrinsics_candidate.json").is_file()
    assert (output / "camera_intrinsics_report.json").is_file()
    summary = (output / "camera_intrinsics_summary.md").read_text(encoding="utf-8")
    assert "held out from intrinsic fitting" in summary
    assert "binding is unverified" in summary
    assert cli.main(["calibrate", str(manifest), "--output-dir", str(output)]) == 2
    assert cli.main(["calibrate", str(manifest), "--output-dir", str(output), "--overwrite"]) == 0


def test_profile_hash_command_and_helpful_manifest_error(tmp_path, capsys):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile()), encoding="utf-8")
    validate, _ = cli._core()

    assert cli.main(["profile-hash", str(profile_path)]) == 0
    assert capsys.readouterr().out.strip() == validate(profile())["sha256"]

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{}", encoding="utf-8")
    assert cli.main(["calibrate", str(malformed), "--output-dir", str(tmp_path / "out")]) == 2
    assert "manifest.version must be 1" in capsys.readouterr().err


def test_overwrite_cannot_replace_a_source_image(tmp_path):
    validate, _ = cli._core()
    normalized = validate(profile())
    source = tmp_path / "camera_intrinsics_report.json"
    save_image(source, np.zeros((60, 80), np.uint8), ".png")
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        normalized,
        [
            {
                "id": "v",
                "group_id": "g",
                "image": source.name,
                "split": "fit",
                "profile_sha256": normalized["sha256"],
            }
        ],
    )

    before = source.read_bytes()
    with pytest.raises(cli.CliError, match="overlaps"):
        cli.calibrate(manifest, tmp_path, True)
    assert source.read_bytes() == before


@pytest.mark.parametrize("path", ["/absolute.png", "C:\\absolute.png"])
def test_manifest_rejects_absolute_image_paths(tmp_path, path):
    validate, _ = cli._core()
    normalized = validate(profile())
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        normalized,
        [
            {
                "id": "v",
                "group_id": "g",
                "image": path,
                "split": "fit",
                "profile_sha256": normalized["sha256"],
            }
        ],
    )

    with pytest.raises(cli.CliError, match="relative"):
        cli.calibrate(manifest, tmp_path / "out", False)
