# Offline optical calibration bench

This M1 bench fits a checkerboard-based camera calibration independently of the
resting-ball scale used by today's live fusion. It produces a candidate and a
diagnostic report. It does not update the rig file or enable calibrated optics
in the live estimators.

Capture metadata does not yet establish the complete native sensor crop,
sampling and saved-image mapping. A declared mode profile
keeps datasets separate, but every candidate remains **unverified for reuse on
future captures**. Low reprojection error alone does not qualify fusion accuracy.

## Collect and partition the images

Use original saved grayscale frames, such as capture PGM images, without
resizing, cropping, rotating or correcting them after capture. Preview images
may use different transformations and are not interchangeable with saved frames.
Keep camera module, lens, focus, mode, crop and saved-image orientation fixed.
A change to any of them requires a separate profile and dataset.

Use a flat checkerboard with a measured square pitch. Record the number of
**inner corners**, not the number of squares, and the square pitch in millimeters.
Record board identity and pitch uncertainty when available; an ordinary printout
is not automatically a dimensional reference.

Collect varied board positions, tilts and distances, including the image edges
and the hitting region. The bench requires at least ten detected fit groups and
three detected validation groups. These are minimum input requirements, not a
statistical accuracy guarantee. Prefer one image per distinct board placement.
Frames from the same capture or unchanged board placement share a `group_id`;
they cannot appear in both fit and validation sets.

Assign the split before examining fit results. The validation images do not
contribute to intrinsic fitting. Their poses are fitted using the frozen camera
matrix and distortion coefficients, then their corner residuals are reported.
This tests projection on held-out images, not independently measured pose or
millimeter accuracy. If you tune against this validation set, reserve new images
for the final check.

## Declare the profile and run the bench

Create `profile.json` with the structure below. Replace the identity labels and
mode values with your collection record. The numeric values shown describe an
example 320x200 dataset, not a verified profile for your hardware. Keep unknown
crop/readout fields `null`; zero means a known zero, not unknown.

```json
{
  "version": 1,
  "camera_id": "replace-with-camera-record",
  "lens_id": "replace-with-lens-record",
  "focus_id": "replace-with-locked-focus-record",
  "sensor_output": {
    "mode_id": "replace-with-locked-readout-id",
    "width": 320,
    "height": 200,
    "bit_depth": 8,
    "raw_format": "R8"
  },
  "saved_image": {
    "width": 320,
    "height": 200,
    "stream": "raw",
    "rotate_180": false,
    "mirror": false
  },
  "crop_readout_mapping": {
    "native_sensor_crop": null,
    "scaler_crop": null,
    "driver_vertical_offset_px": null,
    "sensor_output_mapping": null,
    "saved_image_mapping": null
  }
}
```

Calculate its identity on the analysis machine:

```shell
uv run --extra camera python scripts/analysis/calibrate_camera_intrinsics.py profile-hash profile.json
```

Create a JSON manifest with `version: 1`, `mode_profile` containing that same
profile object, `board`, and `views`. For example, the board object can be:

```json
{
  "inner_corners_columns": 7,
  "inner_corners_rows": 5,
  "square_size_mm": 25.0,
  "board_id": "replace-with-measured-board-record",
  "square_size_uncertainty_mm": null
}
```

The board dimensions and 25 mm pitch are examples; replace them with the actual
board measurements. Each item in `views` has this form, using the hash printed
above and a path relative to the manifest:

```json
{
  "id": "fit-01",
  "group_id": "placement-01",
  "image": "images/placement-01.pgm",
  "split": "fit",
  "profile_sha256": "COPY_PROFILE_HASH_HERE"
}
```

Use `split: "validation"` for the separate validation groups, then run:

```shell
uv run --extra camera python scripts/analysis/calibrate_camera_intrinsics.py calibrate manifest.json --output-dir calibration-result
```

The directory contains `camera_intrinsics_candidate.json`,
`camera_intrinsics_report.json`, and `camera_intrinsics_summary.md`. Existing
named outputs require an explicit `--overwrite`. Keep the original images with
the report: source-byte hashes, decoded-pixel hashes and extracted corner
coordinates allow later inspection without silently substituting new inputs.

Mixed profile identities, mismatched image dimensions, duplicate decoded images,
and groups shared between splits invalidate the dataset. Missing/unreadable
images and failed corner detection remain in the report; a candidate requires
enough usable fit and validation groups. An unsuccessful run returns nonzero.

## Interpret the result

Compare fit and held-out residuals, inspect each view and retain failed
detections. The report includes pixel residuals and coverage diagnostics; no
error threshold automatically promotes a candidate. Declared groups and profile
labels are operator evidence, not independently verified camera identities.

The lens model is OpenCV's five-coefficient Brown model. Fisheye models, automatic
cross-mode scaling and live calibration selection are outside this step.
The [offline projection comparison](calibrated-projection.md) exercises a
candidate on declared-profile pixels and reports differences from the existing
model; it does not qualify reuse on a capture.
Actual mode/crop provenance, independent target checks, placement and timing
validation remain required by the [fusion master plan](../development/fusion-master-plan.md).

## Capture evidence and remaining mapping limits

New runtime sidecars include versioned `capture_mode` evidence tied to the saved
frames. Request-reported crop and frame duration are distinct from requested
controls and the configuration reported at startup. A missing value means
unknown; it must not be filled from a later capture or interpreted as zero.

`capture_mode.contexts` holds the recorded startup contexts, each identified by
a content hash. The arrays under `capture_mode.frames` follow the saved NPZ
frame order: context index, request `ScalerCrop`, frame duration in microseconds,
and observed saved width/height. A null context index means no context was
recorded for that frame. `context_status` describes context coverage only;
`uniform` does not imply constant per-frame crop, exposure or cadence.

The context's settings describe the saved stream and applied rotation/mirror.
`roll_correction_deg` is a preview setting; it is not applied to saved frames.
The configuration controls are startup configuration evidence, while request
metadata reports each frame. Exposure and gain remain in the NPZ arrays.

The driver module's `strip_y_offset` value is read at startup. That is a module
parameter readback, not a sensor-register measurement: the checked-in driver
clamps it and applies it only in supported modes at stream start. In its
320x200 mode, the programmed native window also differs from the declared crop.
Do not derive a native-to-saved-image mapping from that parameter, the reported
`ScalerCrop`, or image dimensions alone. See the
[driver patch](../../drivers/ov9281/ov9282-high-speed.patch).

The evidence preserves what the software observed and applied to saved pixels;
it does not establish the optical unit, lens or focus identity. Existing
calibration profiles remain operator declarations, and candidates stay
unverified. Exported per-shot sidecars are the capture-specific evidence; the
run manifest's legacy `capture.resolved` field is only one capture's example.

Sources: [OpenCV calibration and reprojection error](https://docs.opencv.org/4.13.0/d4/d94/tutorial_camera_calibration.html),
[OpenCV pose estimation](https://docs.opencv.org/doc/doxygen/html/d5/d1f/calib3d_solvePnP.html),
[Picamera2 configuration and sensor modes](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf).
