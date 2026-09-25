from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from openflight.camera.optical_calibration import calibrate_checkerboard, validate_mode_profile
from tests.test_optical_calibration import (
    _board as calibration_board,
    _profile as calibration_profile,
    _synthetic_observations,
)

SCRIPT = Path(__file__).parents[1] / "scripts" / "analysis" / "compare_camera_projection.py"
SPEC = importlib.util.spec_from_file_location("compare_camera_projection", SCRIPT)
assert SPEC and SPEC.loader
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def _profile() -> dict:
    return validate_mode_profile(
        {
            "version": 1,
            "camera_id": "camera-1",
            "lens_id": "lens-1",
            "focus_id": "focus-1",
            "sensor_output": {
                "mode_id": "mode-640x400",
                "width": 640,
                "height": 400,
                "bit_depth": 8,
                "raw_format": "Y8",
            },
            "saved_image": {
                "width": 640,
                "height": 400,
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
    )


def _candidate(profile: dict, *, distortion=None, cx=320.0) -> dict:
    coefficients = distortion or [0.0] * 5
    return {
        "version": 1,
        "status": "candidate",
        "accuracy_qualified": False,
        "mode_binding": {"status": "unverified", "reason": "bench only"},
        "mode_profile": profile,
        "mode_profile_sha256": profile["sha256"],
        "camera_matrix": [[500.0, 0.0, cx], [0.0, 500.0, 200.0], [0.0, 0.0, 1.0]],
        "distortion_model": "opencv_brown_5",
        "distortion_convention": {
            "coefficient_order": ["k1", "k2", "p1", "p2", "k3"],
            "coordinates": "OpenCV normalized camera coordinates",
        },
        "distortion_coefficients": dict(zip(("k1", "k2", "p1", "p2", "k3"), coefficients)),
    }


def _setup() -> dict:
    return {
        "version": 1,
        "pixel_points": [[320.0, 200.0], [470.0, 120.0]],
        "optical_rdf_to_world_lfu": [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]],
        "legacy": {
            "focal_px": 500.0,
            "pitch_rad": 0.0,
            "horizontal_pixel_sign": 1.0,
            "roll_correction_deg": 0.0,
        },
        "radar_ranges_m": [2.0, 2.2],
        "camera_origin_lfu": [0.02, 0.03, 0.1],
        "radar_origin_lfu": [0.0, 0.0, 0.05],
    }


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _run(tmp_path: Path, candidate: dict, profile: dict, setup: dict, name="report.json"):
    candidate_path = tmp_path / "candidate.json"
    profile_path = tmp_path / "profile.json"
    setup_path = tmp_path / "setup.json"
    output = tmp_path / name
    _write(
        candidate_path,
        {
            "version": 1,
            "candidate": candidate,
            "reason": None,
            "mode_profile_sha256": profile["sha256"],
        },
    )
    _write(profile_path, profile)
    _write(setup_path, setup)
    result = cli.main(
        [
            "compare",
            str(candidate_path),
            str(profile_path),
            str(setup_path),
            "--output",
            str(output),
        ]
    )
    return result, output, (candidate_path, profile_path, setup_path)


def test_same_intrinsics_and_legacy_model_agree_on_rays_and_positions(tmp_path):
    profile = _profile()
    result, output, inputs = _run(tmp_path, _candidate(profile), profile, _setup())

    assert result == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["accuracy_qualified"] is False
    assert report["mode_binding"] == "unverified"
    assert report["interpretation"] == "model_disagreement_not_accuracy"
    assert all(
        point["angular_difference_deg"] == pytest.approx(0.0, abs=1e-6)
        for point in report["points"]
    )
    assert all(
        point["position_difference_m"] == pytest.approx(0.0, abs=1e-6) for point in report["points"]
    )
    assert [report["inputs"][key]["sha256"] for key in ("candidate", "mode_profile", "setup")] == [
        __import__("hashlib").sha256(path.read_bytes()).hexdigest() for path in inputs
    ]


def test_distortion_and_off_center_principal_point_report_model_difference(tmp_path):
    profile = _profile()
    candidate = _candidate(profile, distortion=[0.12, -0.02, 0.003, -0.002, 0.0], cx=304.0)
    result, output, _inputs = _run(tmp_path, candidate, profile, _setup())

    assert result == 0
    points = json.loads(output.read_text(encoding="utf-8"))["points"]
    assert points[0]["angular_difference_deg"] > 1.0
    assert points[1]["angular_difference_deg"] > 0.5
    assert points[1]["position_difference_m"] > 0.01


def test_compare_accepts_serialized_candidate_from_real_calibration_solver(tmp_path):
    profile = validate_mode_profile(calibration_profile())
    candidate = calibrate_checkerboard(
        board=calibration_board(),
        mode_profile=profile,
        observations=_synthetic_observations(),
    )
    setup = {
        "version": 1,
        "pixel_points": [[318.0, 237.0], [500.0, 100.0]],
        "optical_rdf_to_world_lfu": [
            [1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0],
            [0.0, -1.0, 0.0],
        ],
        "legacy": {
            "focal_px": 605.0,
            "pitch_rad": 0.0,
            "horizontal_pixel_sign": 1.0,
            "roll_correction_deg": 0.0,
        },
    }

    result, output, _inputs = _run(tmp_path, candidate, profile, setup)

    assert result == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["point_count"] == 2
    assert report["points"][1]["angular_difference_deg"] > 0.0


def test_profile_mismatch_and_malformed_json_do_not_write_reports(tmp_path, capsys):
    profile = _profile()
    other = _profile()
    other["camera_id"] = "different-camera"
    other = validate_mode_profile({key: value for key, value in other.items() if key != "sha256"})
    result, output, _inputs = _run(tmp_path, _candidate(profile), other, _setup())
    assert result == 2
    assert not output.exists()
    assert "profile" in capsys.readouterr().err.lower()

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    profile_path = tmp_path / "valid-profile.json"
    setup_path = tmp_path / "valid-setup.json"
    _write(profile_path, profile)
    _write(setup_path, _setup())
    target = tmp_path / "malformed-report.json"
    assert (
        cli.main(
            ["compare", str(malformed), str(profile_path), str(setup_path), "--output", str(target)]
        )
        == 2
    )
    assert not target.exists()


def test_output_collision_and_overwrite_are_explicit(tmp_path):
    profile = _profile()
    result, output, inputs = _run(tmp_path, _candidate(profile), profile, _setup())
    assert result == 0
    assert cli.main(["compare", *map(str, inputs), "--output", str(output)]) == 2
    assert cli.main(["compare", *map(str, inputs), "--output", str(output), "--overwrite"]) == 0
    before = inputs[0].read_bytes()
    assert cli.main(["compare", *map(str, inputs), "--output", str(inputs[0]), "--overwrite"]) == 2
    assert inputs[0].read_bytes() == before


@pytest.mark.parametrize("missing", ["radar_ranges_m", "camera_origin_lfu", "radar_origin_lfu"])
def test_range_and_both_origins_are_required_as_one_group(tmp_path, missing):
    profile = _profile()
    setup = _setup()
    setup.pop(missing)

    result, output, _inputs = _run(tmp_path, _candidate(profile), profile, setup)

    assert result == 2
    assert not output.exists()


def test_inspect_capture_retains_unverified_status_and_nonzero_exit(tmp_path):
    profile = _profile()
    startup = {
        "settings": {
            "width": 640,
            "height": 400,
            "stream": "raw-gray",
            "rotate_180": False,
            "mirror_horizontal": False,
        },
        "resolved_config": {"raw": {"size": [640, 400], "format": "Y8", "bit_depth": 8}},
        "driver": {"strip_y_offset": {"value_px": None}},
    }
    canonical = json.dumps(startup, sort_keys=True, separators=(",", ":"))
    context_id = __import__("hashlib").sha256(canonical.encode()).hexdigest()
    candidate_path = tmp_path / "candidate.json"
    profile_path = tmp_path / "profile.json"
    sidecar_path = tmp_path / "sidecar.json"
    output = tmp_path / "inspection.json"
    _write(
        candidate_path,
        {
            "version": 1,
            "candidate": _candidate(profile),
            "reason": None,
            "mode_profile_sha256": profile["sha256"],
        },
    )
    _write(profile_path, profile)
    _write(
        sidecar_path,
        {
            "frame_count": 1,
            "capture_mode": {
                "version": 1,
                "contexts": [{"id": context_id, "startup": startup}],
                "frames": {
                    "context_index": [0],
                    "scaler_crop": [None],
                    "frame_duration_us": [3472],
                    "saved_width": [640],
                    "saved_height": [400],
                },
            },
        },
    )

    result = cli.main(
        [
            "inspect-capture",
            str(candidate_path),
            str(profile_path),
            str(sidecar_path),
            "--output",
            str(output),
        ]
    )

    assert result == 3
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["compatibility"]["status"] == "unverified"
    assert report["accuracy_qualified"] is False
    assert report["verified_projection_authorized"] is False

    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["frame_count"] = 2
    _write(sidecar_path, sidecar)
    mismatch_output = tmp_path / "mismatch-inspection.json"
    mismatch_result = cli.main(
        [
            "inspect-capture",
            str(candidate_path),
            str(profile_path),
            str(sidecar_path),
            "--output",
            str(mismatch_output),
        ]
    )
    assert mismatch_result == 1
    mismatch = json.loads(mismatch_output.read_text(encoding="utf-8"))
    assert mismatch["compatibility"]["status"] == "incompatible"
    assert "misaligned" in " ".join(mismatch["compatibility"]["reasons"])
