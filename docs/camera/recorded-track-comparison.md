# Recorded ball and club track comparison

This offline bench compares two projection models on the same recorded image
observations. It uses actual frame timestamps from `frames.npz` and retains the
identity of each annotated or exported track. It does not rerun the live
tracker, recreate its original feature selection, or validate fusion accuracy.

The existing live ball and club summaries do not contain their selected pixel
tracks. Supply explicitly recorded annotations or a tracker export instead.
Keep one physical feature per track: a ball center or one identified club
feature. A club feature's motion is not automatically clubhead-center motion.

## Review saved captures in the tester

Start the tester with `bash scripts/start-tester.sh`, then open **Review saved
capture tracks** from its page, or visit `http://127.0.0.1:8765/track-review.html`.
For an existing session copied to another computer, run:

```shell
uv run --extra camera python -m openflight.camera.tester_server --sessions-root PATH_TO_TESTER_PILOT --no-inclinometer
```

The sessions root must retain the tester's directory layout:
`TESTER/ARM/paired/run-*/ARM/camera/camera_*/{frames.npz,metadata.json}`.
This page reads saved captures; it does not start a camera, select live estimator
candidates or take ownership of the radars. Annotation itself does not need
OpenCV; numerical comparison uses the existing `camera` dependency extra.

Select a tester, saved run and capture. Create one named track for the ball
center or one physical club feature. Step through the saved frames, mark the
same point, and explicitly mark frames where it cannot be located. Refine
coordinates when needed rather than treating a rough finger tap as a precise
measurement. Keep different club features in separate tracks. Supply a range
only when its association with that physical point is independently supported.

Drafts stay in browser storage and are tied to both source-file hashes. Use
**Download manifest** to preserve and share them: browser drafts are not
included by the tester's package action. Keep that manifest with the original
capture files. Editing or replacing a capture must not silently rebind old
annotations to the new bytes.

Load the candidate, declared profile and setup JSON files described below, then
run the comparison. Review missing observations, withheld intervals and
compatibility reasons alongside the model differences. Download the report and
retain the three input files so the comparison can be reproduced with the CLI.
Changing annotations or comparison inputs invalidates the previous displayed
report; an old result must not describe a newly edited track. Report downloads
preserve the server's response text so browser number parsing cannot round
large integer nanosecond timestamps in the saved evidence.

The web report fingerprints the UTF-8 JSON strings submitted by the browser,
plus exact capture and metadata bytes. These document hashes identify submitted
content; they are not a guarantee of the original upload's encoding or byte
order mark. The CLI fingerprints original files directly.

## Inputs and binding

Keep the original `frames.npz` and its `metadata.json` together. Track annotations
bind to the SHA-256 hashes of those exact files. An export renaming a sidecar to
`camera_metadata.json` preserves its bytes and therefore its hash. A different
file must not be substituted merely because it has the same frame dimensions.

Supply the candidate and exact declared profile from the
[optical calibration bench](optical-calibration.md). The physical camera
rotation and the legacy model inputs follow the
[projection comparison contract](calibrated-projection.md).

The setup explicitly declares `projection_assumption` as
`declared_profile_hypothesis` and selects either `sensor_timestamp_ns` or
`host_timestamp_ns` as `timestamp_source`. No clock is silently substituted for
the chosen clock. All motion intervals use differences of recorded integer
nanoseconds before converting to seconds.

Compatibility conflicts block numerical comparison. Matching available evidence
still leaves calibration binding unverified. With the explicit setup hypothesis,
the tool can report conditional model differences on those tracks; it does not
claim that the candidate is physically applicable to the recording. The output
remains unqualified and is not approved for live use.

## Run the comparison

Create `track-setup.json`. This is a synthetic aligned-camera example, not the
measured rig configuration. Use the recorded legacy inputs and independently
established physical rotation for your comparison:

```json
{
  "version": 1,
  "projection_assumption": "declared_profile_hypothesis",
  "timestamp_source": "sensor_timestamp_ns",
  "optical_rdf_to_world_lfu": [[1, 0, 0], [0, 0, 1], [0, -1, 0]],
  "legacy": {
    "focal_px": 500.0,
    "pitch_rad": 0.0,
    "horizontal_pixel_sign": 1.0,
    "roll_correction_deg": 0.0
  }
}
```

Create `tracks.json` with the following structure. Replace the two hash
placeholders with lowercase SHA-256 digests of the exact source files, and
replace the example observations with actual annotations. Frame indices are
zero-based and must be unique and increasing within each track. Pixels are
`[column, row]` in the saved image, before any display transformation.

```json
{
  "version": 1,
  "capture_npz_sha256": "REPLACE_WITH_FRAMES_SHA256",
  "metadata_sha256": "REPLACE_WITH_METADATA_SHA256",
  "tracks": [{
    "id": "ball-1",
    "object": "ball",
    "point_kind": "ball_center",
    "source": {
      "kind": "manual_annotation",
      "description": "Ball-center clicks in the saved images"
    },
    "observations": [
      {"frame_index": 0, "pixel_px": [160, 100], "radar_range_m": null},
      {"frame_index": 1, "pixel_px": null, "radar_range_m": null},
      {"frame_index": 2, "pixel_px": [170, 95], "radar_range_m": null}
    ]
  }]
}
```

For a club feature, use `object: "club"`, `point_kind: "club_feature"` and a
distinct track ID. A tracker export uses `source.kind: "tracker_export"` and
describes its producer and feature selection. Keep missing detections as null
pixels; skipping frame indices also prevents motion across the gap.

On PowerShell, obtain each digest with:

```powershell
(Get-FileHash -Algorithm SHA256 -LiteralPath frames.npz).Hash.ToLowerInvariant()
(Get-FileHash -Algorithm SHA256 -LiteralPath metadata.json).Hash.ToLowerInvariant()
```

For metric motion, supply positive `radar_range_m` values for consecutive
observations and a `radar_range_source` description on every track with ranges.
Also add both `camera_origin_lfu` and `radar_origin_lfu` to the setup, each an
array of lateral/forward/up coordinates in meters. Omit both origins when no
track supplies ranges. Missing ranges can be null or omitted.

```shell
uv run --extra camera python scripts/analysis/compare_camera_tracks.py calibration-result/camera_intrinsics_candidate.json profile.json track-setup.json tracks.json --frames frames.npz --metadata metadata.json --output track-comparison.json
```

The report records hashes of all six input files, runtime versions, tool source
hash, track identities, per-observation results and interval counts. The CLI
hashes and decodes the same byte buffers. Direct Python callers must compute
and supply the actual capture and metadata digests themselves.

Exit code **3** means a conditional report was written; **1** means incompatible
capture evidence was reported with numerical comparisons withheld; **2** means
invalid inputs or an output error. There is currently no accuracy-pass outcome.
An existing report requires `--overwrite`; output cannot replace an input.
Malformed manifest structure, identity, ordering or hashes reject the input.
Unusable individual pixels/ranges remain withheld observations in the report.

## Interpreting tracks and motion

Each supplied observation retains its original frame index and usable saved-image
pixel coordinate. Both models use that observation. Invalid observations and
missing ranges remain visible with reasons rather than disappearing from counts.
Malformed or nonfinite measurement values become null in the finite JSON report;
retain the hashed source manifest to inspect their original representation.
Without a declared radar range, the bench can compare ray directions but cannot
recover metric position or velocity from a single camera observation.

Ranges must refer to the tracked physical point and the radar origin declared in
the setup. Record how they were obtained and associated. The tool does not
derive range from OPS speed, presume radar/feature association, or validate
camera/radar synchronization.

Velocity comparisons use adjacent observations of the same identified feature
in consecutive recorded frames with valid positions and positive elapsed time.
They do not bridge an invalid observation, a missing frame index, or a missing
range. Ball and club tracks remain separate, and club features are not averaged
into a fictitious clubhead center.

Reported velocity, speed and horizontal/vertical direction describe finite
feature-motion intervals. They are not instantaneous impact speed, club path,
attack angle or launch angle. Zero displacement has no defined direction, and
purely vertical displacement has no defined horizontal direction.
Model differences share the pixels, timing and supplied radar ranges; agreement
does not establish accuracy against an independent reference.

Signed speed and direction differences are candidate minus legacy. Angular
separation and position/velocity difference magnitudes are nonnegative.
