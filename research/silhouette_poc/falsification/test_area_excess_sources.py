"""Quantify the pinned-range silhouette area excess source by source.

The current split seeds "shaft" only 60 px from the head core although the CAD
contains just 64 mm of hosel/ferrule (about 19 px at the measured plate scale).
This script captures the exact connected component selected by ``club_masks``,
re-splits it at that physical projected reach, then motion-integrates the CAD
render using the measured optical displacement during the recorded exposure.
No outline is dilated; the blur mask is a union of translated model renders.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import test_meshfit_depth_ab as meshfit  # noqa: E402
from silhouette_poc.generator.mesh_truth import TriangleMesh  # noqa: E402
from silhouette_poc.replay import head_split  # noqa: E402
from silhouette_poc.replay.fit_real import (  # noqa: E402
    CAMERA_CENTER_WORLD,
    _ray_world,
    measured_camera,
    render_mask_6dof,
)

CAD_STUB_MM = 61.8
PLATE_SCALE_PX_PER_MM = 0.295
PHYSICAL_REACH_PX = CAD_STUB_MM * PLATE_SCALE_PX_PER_MM


def swept_model(mask: np.ndarray, displacement_px: np.ndarray) -> np.ndarray:
    """Integrate translated copies of the exact model over one exposure."""
    out = np.zeros_like(mask, dtype=np.uint8)
    for fraction in np.linspace(-0.5, 0.5, 13):
        shift = fraction * displacement_px
        transform = np.asarray([[1.0, 0.0, shift[0]], [0.0, 1.0, shift[1]]])
        moved = cv2.warpAffine(
            mask.astype(np.uint8),
            transform,
            (mask.shape[1], mask.shape[0]),
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
        )
        out |= moved
    return out


def main() -> None:
    with open(meshfit.SESSION / "shots.csv", newline="", encoding="utf-8") as handle:
        rows = {
            int(row["shot_number"]): row
            for row in csv.DictReader(handle)
            if int(row["shot_number"]) not in meshfit.EXCLUDE
        }
    fits = {
        int(record["shot"]): record
        for record in json.loads(
            Path(__file__).with_name("meshfit_arm_C.json").read_text(encoding="utf-8")
        )
    }
    mesh_data = np.load(meshfit.MESH)
    mesh = TriangleMesh(
        mesh_data["vertices_local_mm"], mesh_data["faces"], "poc_7iron", "x" * 64
    )
    records = []
    original_split = meshfit.split_head
    for shot, row in rows.items():
        archive = np.load(meshfit.SESSION / row["archive_frames_npz"])
        frames = archive["frames"][:, :, ::-1]
        timestamps = archive["sensor_timestamp_ns"].astype(float) * 1e-9
        captured = []

        def capture_split(component):
            result = original_split(component)
            if result is not None:
                captured.append((component.copy(), result[0].copy()))
            return result

        meshfit.split_head = capture_split
        try:
            masks = meshfit.club_masks(frames)
        finally:
            meshfit.split_head = original_split
        current_to_component = {}
        for frame, current in masks.items():
            matches = [
                component
                for component, head in captured
                if np.array_equal(head, current)
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    f"shot {shot} frame {frame}: selected head matched {len(matches)} components"
                )
            current_to_component[frame] = matches[0]

        centroids = {}
        for frame, mask in masks.items():
            yy, xx = np.nonzero(mask)
            centroids[frame] = np.asarray([xx.mean(), yy.mean()])
        fit = fits[shot]
        pose_by_frame = dict(zip(fit["frames"], fit["poses"], strict=True))
        camera = measured_camera(frames.shape[2], frames.shape[1])
        exposure_s = float(row["camera_file_settings_exposure_us"]) * 1e-6
        for frame in fit["frames"]:
            if frame not in masks:
                continue
            current = masks[frame]
            old_reach = head_split.SHAFT_REACH_PX
            head_split.SHAFT_REACH_PX = PHYSICAL_REACH_PX
            try:
                aggressive_result = original_split(current_to_component[frame])
            finally:
                head_split.SHAFT_REACH_PX = old_reach
            if aggressive_result is None:
                continue
            aggressive = aggressive_result[0]
            yy, xx = np.nonzero(current)
            ray = _ray_world(np.asarray([xx.mean(), yy.mean()]), camera)
            centre = CAMERA_CENTER_WORLD + ray * meshfit.PINNED_MM
            pose = pose_by_frame[frame]
            model = render_mask_6dof(mesh, centre, *pose, camera)
            if model is None:
                continue
            neighbours = [candidate for candidate in centroids if candidate != frame]
            if neighbours:
                neighbour = min(
                    neighbours, key=lambda candidate: abs(candidate - frame)
                )
                velocity_px_s = (centroids[neighbour] - centroids[frame]) / (
                    timestamps[neighbour] - timestamps[frame]
                )
                blur_displacement = velocity_px_s * exposure_s
            else:
                blur_displacement = np.zeros(2)
            blurred = swept_model(model, blur_displacement)
            if shot == 15 and frame == 67:
                panels = []
                for observed, rendered, label in (
                    (current, model, "current split / sharp CAD"),
                    (aggressive, blurred, "64mm reach / motion CAD"),
                ):
                    panel = cv2.cvtColor(frames[frame], cv2.COLOR_GRAY2BGR)
                    panel[observed.astype(bool), 2] = 255
                    panel[rendered.astype(bool), 1] = 255
                    cv2.putText(
                        panel,
                        label,
                        (4, 14),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.35,
                        (255, 255, 255),
                        1,
                        cv2.LINE_AA,
                    )
                    panels.append(panel)
                render_path = (
                    Path(__file__).with_name("renders")
                    / "area_sources_shot_015_f67.png"
                )
                render_path.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(render_path), np.concatenate(panels, axis=1))
            model_area = int(model.sum())
            record = {
                "shot": shot,
                "club": row["club"],
                "frame": frame,
                "current_observed_area_px": int(current.sum()),
                "physical_reach_observed_area_px": int(aggressive.sum()),
                "shaft_leakage_px": int(current.sum() - aggressive.sum()),
                "shaft_leakage_fraction": float(
                    (current.sum() - aggressive.sum()) / max(current.sum(), 1)
                ),
                "model_area_px": model_area,
                "blur_displacement_px": float(np.linalg.norm(blur_displacement)),
                "blurred_model_area_px": int(blurred.sum()),
                "blur_area_gain_fraction": float(
                    (blurred.sum() - model_area) / model_area
                ),
                "current_observed_to_model_area": float(current.sum() / model_area),
                "deshafted_observed_to_model_area": float(
                    aggressive.sum() / model_area
                ),
                "deshafted_observed_to_blurred_model_area": float(
                    aggressive.sum() / max(blurred.sum(), 1)
                ),
                "required_linear_mesh_scale_after_split_and_blur": float(
                    math.sqrt(aggressive.sum() / max(blurred.sum(), 1))
                ),
            }
            records.append(record)
        print(
            f"shot {shot:>3}: {sum(record['shot'] == shot for record in records)} frames",
            flush=True,
        )

    output = Path(__file__).with_name("area_excess_sources.json")
    output.write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(f"\n=== {len(records)} fitted frames from all {len(rows)} shots ===")
    for key in (
        "current_observed_to_model_area",
        "shaft_leakage_fraction",
        "blur_displacement_px",
        "blur_area_gain_fraction",
        "deshafted_observed_to_blurred_model_area",
        "required_linear_mesh_scale_after_split_and_blur",
    ):
        values = np.asarray([record[key] for record in records])
        print(
            f"{key}: median {np.median(values):.3f}, "
            f"IQR {np.percentile(values, 25):.3f}..{np.percentile(values, 75):.3f}"
        )
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
