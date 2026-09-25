# Offline calibrated projection comparison

This M1 tool compares a checkerboard calibration candidate with the existing
reference-ball projection model on the same supplied pixel coordinates. It is
an offline diagnostic. It neither changes live fusion nor qualifies the camera,
mount, timing or radar association.

First create a candidate using the [optical calibration bench](optical-calibration.md).
Use the exact declared profile that produced it. A different resolution, crop,
orientation, lens or focus requires separate evidence; the adapter does not
scale an existing calibration to another camera mode.

## Run the comparison

Create `projection-input.json` with saved pixel coordinates, a declared physical
camera rotation, and the legacy model inputs. This is a synthetic 320x200
example, not the measured rig configuration. Replace every setup value with the
recorded or independently established inputs for the comparison:

```json
{
  "version": 1,
  "pixel_points": [[100, 80], [160, 100], [220, 120]],
  "optical_rdf_to_world_lfu": [[1, 0, 0], [0, 0, 1], [0, -1, 0]],
  "legacy": {
    "focal_px": 500.0,
    "pitch_rad": 0.0,
    "horizontal_pixel_sign": 1.0,
    "roll_correction_deg": 0.0
  },
  "radar_ranges_m": 1.5,
  "camera_origin_lfu": [0.0, 0.0, 0.0],
  "radar_origin_lfu": [0.0, 0.0, 0.0]
}
```

The range and origin fields are optional as a group. Omit them for ray-only
comparison. `radar_ranges_m` can be a single shared range or one range per point.
The range is measured from the radar origin, not the camera.

```shell
uv run --extra camera python scripts/analysis/compare_camera_projection.py compare calibration-result/camera_intrinsics_candidate.json profile.json projection-input.json --output projection-comparison.json
```

Inspect compatibility with a capture separately:

```shell
uv run --extra camera python scripts/analysis/compare_camera_projection.py inspect-capture calibration-result/camera_intrinsics_candidate.json profile.json metadata.json --output capture-compatibility.json
```

Both reports record hashes of the exact input file bytes. Existing outputs
require `--overwrite`, and an output cannot replace an input. Comparison returns
exit code 0 when the diagnostic succeeds. Inspection writes its report and
returns 1 for incompatible evidence or 3 for unverified evidence. Input/output
errors return 2. An unverified result is not authorization to use that calibration
for the capture.

## Coordinate contract

The camera matrix and Brown distortion coefficients describe the **saved image**.
Supply pixel coordinates from that image without first undoing its rotation or
mirror. The adapter removes distortion in saved-image coordinates, then undoes
the declared horizontal mirror and 180-degree rotation in normalized coordinates.

Supply a proper rotation matrix from the **physical optical camera** axes
(right, down, forward) into world axes (lateral, forward, up). An aligned camera
uses this example matrix:

```json
[[1, 0, 0], [0, 0, 1], [0, -1, 0]]
```

That example is not a measurement of the rig. Account for the actual mount
orientation in the supplied rotation. A reflection is not a physical rotation;
the saved-image mirror is handled separately. Do not also apply preview roll
correction or mirror to the candidate's input points.

The legacy comparison takes its focal scale, pitch, horizontal pixel sign and
roll correction explicitly. These are the current model's inputs; the tool does
not infer that they are independently calibrated. In particular, the legacy
image-center convention can differ from a fitted principal point.

## Meaning of the comparison

Angular differences compare the two ray directions. Optional range-assisted
position differences use the same radar slant range and camera/radar origins
for both models. Coordinates and origins use lateral/forward/up order in meters.
The camera must lie inside the radar range sphere, as required by the shared
intersection helper.

These values are **model disagreement**, not error against an independent
reference. Shared pixels, assumed placement and shared radar range can hide
common errors. Intrinsic reprojection residuals, this comparison, and physical
accuracy measurements answer different questions.

Capture compatibility inspection checks recorded configuration and per-frame
evidence separately. A known conflict is incompatible; absent evidence stays
unverified. The current sidecars cannot establish native sampling, optical unit,
lens or focus identity, so a capture with matching recorded fields still does
not become verified. No command promotes a candidate into live fusion.

The inverse distortion calculation uses iterative OpenCV point undistortion and
checks its forward reprojection. This checks numerical consistency at the
supplied points, not global uniqueness or physical validity of the lens model.
See [OpenCV's point undistortion contract](https://docs.opencv.org/4.13.0/d9/d0c/group__calib3d.html).
