"""Offline comparison of frozen recorded tracks through two projection models."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from .calibrated_projection import (
    CaptureCompatibility,
    inspect_capture_compatibility,
    load_calibration_candidate,
)
from .geometry import intersect_radar_range_sphere, unit_world_rays

_HASH_CHARS = frozenset("0123456789abcdef")


def _exact_keys(
    value: Mapping[str, Any], required: set[str], optional: set[str], name: str
) -> None:
    keys = set(value)
    missing = required - keys
    extra = keys - required - optional
    if missing or extra:
        raise ValueError(
            f"{name} keys are invalid (missing={sorted(missing)}, extra={sorted(extra)})"
        )


def _sha256(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in _HASH_CHARS for char in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _finite_vector(value: Any, shape: tuple[int, ...], name: str) -> np.ndarray:
    array = np.asarray(value)
    if array.shape != shape or array.dtype.kind not in "iuf" or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be a finite numeric array with shape {shape}")
    return array.astype(float)


def _validate_archive(archive: Mapping[str, Any], profile: Mapping[str, Any]):
    if not isinstance(archive, Mapping):
        raise ValueError("archive must be a mapping of NPZ arrays")
    try:
        frames = np.asarray(archive["frames"])
        sensor = np.asarray(archive["sensor_timestamp_ns"])
        host = np.asarray(archive["host_timestamp_ns"])
    except KeyError as exc:
        raise ValueError(f"archive is missing {exc.args[0]}") from exc
    if frames.ndim != 3 or frames.shape[0] == 0:
        raise ValueError("archive frames must have shape [frame,height,width]")
    count = frames.shape[0]
    expected = (int(profile["saved_image"]["height"]), int(profile["saved_image"]["width"]))
    dimension_reason = (
        "archive frame dimensions differ from the mode profile"
        if frames.shape[1:] != expected
        else None
    )
    for name, timestamps in (("sensor_timestamp_ns", sensor), ("host_timestamp_ns", host)):
        if timestamps.shape != (count,) or timestamps.dtype.kind not in "iu":
            raise ValueError(
                f"archive {name} must be a one-dimensional integer array aligned to frames"
            )
    return count, sensor, host, dimension_reason


def _validate_setup(setup: Mapping[str, Any], has_ranges: bool) -> dict[str, Any]:
    if not isinstance(setup, Mapping):
        raise ValueError("setup must be an object")
    _exact_keys(
        setup,
        {
            "version",
            "projection_assumption",
            "timestamp_source",
            "optical_rdf_to_world_lfu",
            "legacy",
        },
        {"camera_origin_lfu", "radar_origin_lfu"},
        "setup",
    )
    if setup["version"] != 1:
        raise ValueError("setup.version must be 1")
    if setup["projection_assumption"] != "declared_profile_hypothesis":
        raise ValueError("setup.projection_assumption must be declared_profile_hypothesis")
    if setup["timestamp_source"] not in ("sensor_timestamp_ns", "host_timestamp_ns"):
        raise ValueError("setup.timestamp_source is invalid")
    rotation = _finite_vector(setup["optical_rdf_to_world_lfu"], (3, 3), "setup rotation")
    if not np.allclose(rotation.T @ rotation, np.eye(3), rtol=0.0, atol=1e-9) or not math.isclose(
        float(np.linalg.det(rotation)), 1.0, rel_tol=0.0, abs_tol=1e-9
    ):
        raise ValueError("setup rotation must be orthonormal with determinant +1")
    legacy = setup["legacy"]
    if not isinstance(legacy, Mapping):
        raise ValueError("setup.legacy must be an object")
    _exact_keys(
        legacy,
        {"focal_px", "pitch_rad", "horizontal_pixel_sign", "roll_correction_deg"},
        set(),
        "setup.legacy",
    )
    numeric = {name: float(legacy[name]) for name in legacy}
    if not all(math.isfinite(value) for value in numeric.values()) or numeric["focal_px"] <= 0:
        raise ValueError("setup.legacy values must be finite with positive focal_px")
    if numeric["horizontal_pixel_sign"] not in (-1.0, 1.0):
        raise ValueError("setup.legacy.horizontal_pixel_sign must be -1 or 1")
    origins = ("camera_origin_lfu", "radar_origin_lfu")
    present_origins = [name in setup for name in origins]
    if any(present_origins) and not all(present_origins):
        raise ValueError("setup camera and radar origins must be supplied together")
    if has_ranges != all(present_origins):
        raise ValueError("both setup origins are required exactly when tracks contain radar ranges")
    return {
        "rotation": rotation,
        "legacy": numeric,
        "camera": _finite_vector(setup[origins[0]], (3,), origins[0]) if has_ranges else None,
        "radar": _finite_vector(setup[origins[1]], (3,), origins[1]) if has_ranges else None,
    }


def _validate_manifest(manifest: Mapping[str, Any], count: int, npz_hash: str, metadata_hash: str):
    if not isinstance(manifest, Mapping):
        raise ValueError("tracks_manifest must be an object")
    _exact_keys(
        manifest,
        {"version", "capture_npz_sha256", "metadata_sha256", "tracks"},
        set(),
        "tracks_manifest",
    )
    if manifest["version"] != 1:
        raise ValueError("tracks_manifest.version must be 1")
    if _sha256(manifest["capture_npz_sha256"], "capture_npz_sha256") != npz_hash:
        raise ValueError("capture NPZ digest does not match the tracks manifest")
    if _sha256(manifest["metadata_sha256"], "metadata_sha256") != metadata_hash:
        raise ValueError("metadata digest does not match the tracks manifest")
    tracks = manifest["tracks"]
    if not isinstance(tracks, list) or not tracks:
        raise ValueError("tracks_manifest.tracks must be a non-empty list")
    seen_ids: set[str] = set()
    has_ranges = False
    for track_index, track in enumerate(tracks):
        name = f"tracks[{track_index}]"
        if not isinstance(track, Mapping):
            raise ValueError(f"{name} must be an object")
        _exact_keys(
            track,
            {"id", "object", "point_kind", "source", "observations"},
            {"radar_range_source"},
            name,
        )
        if not isinstance(track["id"], str) or not track["id"].strip() or track["id"] in seen_ids:
            raise ValueError("track ids must be unique non-empty strings")
        seen_ids.add(track["id"])
        expected_kind = {"ball": "ball_center", "club": "club_feature"}.get(track["object"])
        if expected_kind is None or track["point_kind"] != expected_kind:
            raise ValueError(f"{name} object and point_kind are inconsistent")
        source = track["source"]
        if not isinstance(source, Mapping):
            raise ValueError(f"{name}.source must be an object")
        _exact_keys(source, {"kind", "description"}, set(), f"{name}.source")
        if (
            source["kind"] not in ("manual_annotation", "tracker_export")
            or not isinstance(source["description"], str)
            or not source["description"].strip()
        ):
            raise ValueError(f"{name}.source is invalid")
        observations = track["observations"]
        if not isinstance(observations, list) or not observations:
            raise ValueError(f"{name}.observations must be a non-empty list")
        previous = -1
        track_has_ranges = False
        for row_index, observation in enumerate(observations):
            row_name = f"{name}.observations[{row_index}]"
            if not isinstance(observation, Mapping):
                raise ValueError(f"{row_name} must be an object")
            _exact_keys(
                observation,
                {"frame_index"},
                {"pixel_px", "radar_range_m"},
                row_name,
            )
            frame = observation["frame_index"]
            if isinstance(frame, bool) or not isinstance(frame, int) or not 0 <= frame < count:
                raise ValueError(f"{row_name}.frame_index is outside the capture")
            if frame <= previous:
                raise ValueError(
                    f"{name} observations must have unique increasing frame_index values"
                )
            previous = frame
            radar_range = observation.get("radar_range_m")
            if radar_range is not None:
                track_has_ranges = True
                has_ranges = True
        range_source = track.get("radar_range_source")
        if track_has_ranges != (isinstance(range_source, str) and bool(range_source.strip())):
            raise ValueError(
                f"{name}.radar_range_source is required exactly when ranges are present"
            )
        if not track_has_ranges and "radar_range_source" in track:
            raise ValueError(f"{name}.radar_range_source must be absent without ranges")
    return tracks, has_ranges


def _direction(vector: np.ndarray) -> tuple[float | None, float | None, list[str]]:
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12:
        return None, None, ["directions are undefined for zero displacement"]
    horizontal_norm = float(np.hypot(vector[0], vector[1]))
    horizontal = (
        math.degrees(math.atan2(float(vector[0]), float(vector[1])))
        if horizontal_norm > 1e-12
        else None
    )
    vertical = math.degrees(math.atan2(float(vector[2]), float(np.hypot(vector[0], vector[1]))))
    reasons = [] if horizontal is not None else ["horizontal direction is undefined"]
    return horizontal, vertical, reasons


def _motion(start: np.ndarray, end: np.ndarray, dt_s: float) -> dict[str, Any]:
    displacement = end - start
    velocity = displacement / dt_s
    horizontal, vertical, reasons = _direction(displacement)
    result = {
        "displacement_lfu_m": displacement.tolist(),
        "velocity_lfu_mps": velocity.tolist(),
        "speed_mps": float(np.linalg.norm(velocity)),
        "horizontal_direction_deg": horizontal,
        "vertical_direction_deg": vertical,
    }
    if reasons:
        result["direction_withheld_reasons"] = reasons
    return result


def _wrapped_difference(candidate: float | None, legacy: float | None) -> float | None:
    if candidate is None or legacy is None:
        return None
    return (candidate - legacy + 180.0) % 360.0 - 180.0


def compare_recorded_tracks(
    *,
    archive: Mapping[str, Any],
    metadata: Mapping[str, Any],
    tracks_manifest: Mapping[str, Any],
    candidate: Mapping[str, Any],
    mode_profile: Mapping[str, Any],
    setup: Mapping[str, Any],
    capture_npz_sha256: str,
    metadata_sha256: str,
) -> dict[str, Any]:
    """Compare capture-bound annotations without selecting or synthesizing track points."""
    npz_hash = _sha256(capture_npz_sha256, "capture_npz_sha256")
    sidecar_hash = _sha256(metadata_sha256, "metadata_sha256")
    calibrated = load_calibration_candidate(candidate, mode_profile=mode_profile)
    count, sensor_ns, host_ns, dimension_reason = _validate_archive(
        archive, calibrated.mode_profile
    )
    tracks, has_ranges = _validate_manifest(tracks_manifest, count, npz_hash, sidecar_hash)
    parsed_setup = _validate_setup(setup, has_ranges)
    timestamps = sensor_ns if setup["timestamp_source"] == "sensor_timestamp_ns" else host_ns
    declared_compatibility = inspect_capture_compatibility(metadata, mode_profile=mode_profile)
    archive_compatibility = inspect_capture_compatibility(
        metadata.get("capture_mode"), mode_profile=mode_profile, frame_count=count
    )
    if dimension_reason or "incompatible" in (
        declared_compatibility.status,
        archive_compatibility.status,
    ):
        reasons = declared_compatibility.reasons + archive_compatibility.reasons
        if dimension_reason:
            reasons = (dimension_reason,) + reasons
        compatibility = CaptureCompatibility(
            "incompatible",
            tuple(dict.fromkeys(reasons)),
        )
    else:
        compatibility = CaptureCompatibility(
            "unverified",
            tuple(dict.fromkeys(declared_compatibility.reasons + archive_compatibility.reasons)),
        )
    incompatible = compatibility.status == "incompatible"
    capture_mode = metadata.get("capture_mode")
    capture_frames = capture_mode.get("frames") if isinstance(capture_mode, Mapping) else None
    frame_context = (
        capture_frames.get("context_index", []) if isinstance(capture_frames, Mapping) else []
    )

    output_tracks = []
    observation_compared = interval_compared = 0
    observation_attempted = sum(len(track["observations"]) for track in tracks)
    interval_attempted = sum(max(0, len(track["observations"]) - 1) for track in tracks)
    width, height = calibrated.image_size
    for track in tracks:
        rows = []
        for source in track["observations"]:
            frame = source["frame_index"]
            raw_pixel = source.get("pixel_px")
            raw_range = source.get("radar_range_m")
            row = {
                "frame_index": frame,
                "pixel_px": None,
                "timestamp_ns": int(timestamps[frame]),
                "radar_range_m": None,
            }
            error = None
            try:
                pixel = _finite_vector(raw_pixel, (2,), "pixel_px")
                row["pixel_px"] = pixel.tolist()
            except (TypeError, ValueError):
                pixel = None
                error = "source pixel_px must be a finite numeric x/y pair"
            range_error = None
            if raw_range is not None:
                if (
                    isinstance(raw_range, bool)
                    or not isinstance(raw_range, (int, float))
                    or not math.isfinite(float(raw_range))
                    or float(raw_range) <= 0.0
                ):
                    range_error = "source radar_range_m must be null or finite and positive"
                else:
                    row["radar_range_m"] = float(raw_range)
            if incompatible:
                error = "capture is incompatible with the declared mode profile"
            elif frame >= len(frame_context) or frame_context[frame] is None:
                error = "source frame has no usable capture context"
            elif error is None and not (0 <= pixel[0] < width and 0 <= pixel[1] < height):
                error = "source pixel is outside the saved image"
            elif error is None and range_error is not None:
                error = range_error
            if error is None:
                try:
                    candidate_ray = calibrated.pixel_rays_lfu(
                        pixel, optical_to_world_lfu=parsed_setup["rotation"]
                    )
                    legacy_ray = unit_world_rays(
                        pixel,
                        image_width_px=width,
                        image_height_px=height,
                        **parsed_setup["legacy"],
                    )
                    angle = math.degrees(
                        math.atan2(
                            float(np.linalg.norm(np.cross(candidate_ray, legacy_ray))),
                            float(candidate_ray @ legacy_ray),
                        )
                    )
                    comparison = {
                        "candidate_ray_lfu": candidate_ray.tolist(),
                        "legacy_ray_lfu": legacy_ray.tolist(),
                        "angular_difference_deg": angle,
                    }
                    radar_range = row["radar_range_m"]
                    if radar_range is not None:
                        candidate_position = calibrated.reconstruct_at_radar_range(
                            pixel,
                            float(radar_range),
                            optical_to_world_lfu=parsed_setup["rotation"],
                            camera_origin_lfu=parsed_setup["camera"],
                            radar_origin_lfu=parsed_setup["radar"],
                        )
                        legacy_position = intersect_radar_range_sphere(
                            legacy_ray,
                            float(radar_range),
                            camera_origin_lfu=parsed_setup["camera"],
                            radar_origin_lfu=parsed_setup["radar"],
                        )
                        comparison.update(
                            candidate_position_lfu_m=candidate_position.tolist(),
                            legacy_position_lfu_m=legacy_position.tolist(),
                            position_difference_m=float(
                                np.linalg.norm(candidate_position - legacy_position)
                            ),
                        )
                    row.update(status="compared", **comparison)
                    observation_compared += 1
                except ValueError as exc:
                    error = str(exc)
            if error is not None:
                row.update(status="withheld", error=error)
            rows.append(row)

        intervals = []
        for first, second in zip(rows, rows[1:]):
            interval = {
                "start_frame_index": first["frame_index"],
                "end_frame_index": second["frame_index"],
            }
            error = None
            if second["frame_index"] != first["frame_index"] + 1:
                error = "source observations are not consecutive capture frames"
            elif first["status"] != "compared" or second["status"] != "compared":
                error = "an endpoint observation was withheld"
            elif (
                "candidate_position_lfu_m" not in first or "candidate_position_lfu_m" not in second
            ):
                error = "both endpoint observations require radar ranges"
            else:
                dt_ns = second["timestamp_ns"] - first["timestamp_ns"]
                interval["start_timestamp_ns"] = first["timestamp_ns"]
                interval["end_timestamp_ns"] = second["timestamp_ns"]
                interval["dt_ns"] = dt_ns
                if dt_ns <= 0:
                    error = "selected timestamps do not have a positive interval"
                else:
                    dt_s = dt_ns / 1_000_000_000.0
                    interval["dt_s"] = dt_s
                    candidate_motion = _motion(
                        np.asarray(first["candidate_position_lfu_m"]),
                        np.asarray(second["candidate_position_lfu_m"]),
                        dt_s,
                    )
                    legacy_motion = _motion(
                        np.asarray(first["legacy_position_lfu_m"]),
                        np.asarray(second["legacy_position_lfu_m"]),
                        dt_s,
                    )
                    candidate_displacement = np.asarray(candidate_motion["displacement_lfu_m"])
                    legacy_displacement = np.asarray(legacy_motion["displacement_lfu_m"])
                    candidate_velocity = np.asarray(candidate_motion["velocity_lfu_mps"])
                    legacy_velocity = np.asarray(legacy_motion["velocity_lfu_mps"])
                    interval.update(
                        status="compared",
                        label="same_feature_interval_motion",
                        candidate=candidate_motion,
                        legacy=legacy_motion,
                        differences={
                            "displacement_lfu_m": (
                                candidate_displacement - legacy_displacement
                            ).tolist(),
                            "displacement_difference_m": float(
                                np.linalg.norm(candidate_displacement - legacy_displacement)
                            ),
                            "velocity_lfu_mps": (candidate_velocity - legacy_velocity).tolist(),
                            "velocity_difference_mps": float(
                                np.linalg.norm(candidate_velocity - legacy_velocity)
                            ),
                            "speed_difference_mps": candidate_motion["speed_mps"]
                            - legacy_motion["speed_mps"],
                            "horizontal_direction_difference_deg": _wrapped_difference(
                                candidate_motion["horizontal_direction_deg"],
                                legacy_motion["horizontal_direction_deg"],
                            ),
                            "vertical_direction_difference_deg": _wrapped_difference(
                                candidate_motion["vertical_direction_deg"],
                                legacy_motion["vertical_direction_deg"],
                            ),
                        },
                    )
                    interval_compared += 1
            if error is not None:
                interval.update(status="withheld", error=error)
            intervals.append(interval)
        item = {key: track[key] for key in ("id", "object", "point_kind", "source")}
        if "radar_range_source" in track:
            item["radar_range_source"] = track["radar_range_source"]
        item.update(observations=rows, intervals=intervals)
        output_tracks.append(item)

    return {
        "version": 1,
        "diagnostic": "recorded_track_projection_comparison",
        "status": "incompatible" if incompatible else "conditional",
        "interpretation": "model_disagreement_not_accuracy",
        "accuracy_qualified": False,
        "live_use_authorized": False,
        "projection_assumption": setup["projection_assumption"],
        "timestamp_source": setup["timestamp_source"],
        "compatibility": {"status": compatibility.status, "reasons": list(compatibility.reasons)},
        "capture_identity": {"capture_npz_sha256": npz_hash, "metadata_sha256": sidecar_hash},
        "counts": {
            "tracks": len(output_tracks),
            "observations_attempted": observation_attempted,
            "observations_compared": observation_compared,
            "observations_withheld": observation_attempted - observation_compared,
            "intervals_attempted": interval_attempted,
            "intervals_compared": interval_compared,
            "intervals_withheld": interval_attempted - interval_compared,
        },
        "tracks": output_tracks,
    }
