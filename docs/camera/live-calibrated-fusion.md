# Live calibrated camera fusion

The kiosk measures each saved-image mode on that mode's own calibrated camera:
principal point, Brown-five distortion and the placement that puts the camera in
the rig (including the camera-vs-LIS3DH pitch and roll, through the placement's
reference pose). `scripts/start-kiosk.sh` passes the 6 mm lens's set by default,
the same way it passes the rig file:

```bash
scripts/start-kiosk.sh --camera-capture --iwr6843 --inclinometer
# = ... --rig-geometry config/enclosure_v3_rig_geometry.json #       --camera-calibration-dir config/camera-calibration/6mm
```

A calibration directory holds one `<W>x<H>.optical.json` + `<W>x<H>.placement.json`
pair per mode. The server picks the pair whose `mode_profile.saved_image` is the
live capture's mode at setup and the clip's capture-mode evidence at fusion. A
mode without a pair, a pair refused at startup (another rig's placement, a missing
file) or an absent directory measures on the nominal camera (principal point at the
image centre) and says why: the session record's `camera_calibrated_fusion` names
every pair (content and file SHA-256s), the active mode's pair `in_use`, or its
`fallback_reason`; a shot fused nominally carries the reason in
`calibrated_camera_reason`. `scripts/camera/build_camera_placements.py` rebuilds a
set's placements for the loaded rig file whenever the rig file changes.

Every consumer of the calibrated camera ranges from one radar origin. The placement's
`radar_origin_lfu` is the IWR RX row (what its consistency check compares with the rig
file), but the IWR's ranges start at its phase centre: the shot fusion, the surface
solve and the swing-tilt solve all place the model at the rig file's lens and IWR
phase centre (`origins` in the model snapshot, `origin_basis`
`rig_lens_and_iwr_phase_centre`); a model without them keeps the placement's origins.
Capture-mode evidence the clip cannot prove (no requested scaler crop, an unknown
driver mapping) labels the calibrated camera in `calibrated_camera_reason` and the
model's mode evidence; only a contradiction rejects it.

One explicit pair overrides the directory (the tester's opt-in route):

```bash
scripts/start-kiosk.sh   --camera-capture --iwr6843 --inclinometer   --camera-optical-calibration calibration/ov9281-mode.json   --camera-placement calibration/v3-placement.json
```

The tester server accepts the same two calibration paths and forwards them to
every swing and ladder kiosk child:

```bash
bash scripts/start-tester.sh \
  --camera-optical-calibration calibration/ov9281-mode.json \
  --camera-placement calibration/v3-placement.json
```

The optical file is the version 1 candidate produced by
`calibrate_camera_intrinsics.py`. Its exact camera, lens, focus, sensor mode,
crop, saved orientation, and Brown-five coefficients are frozen into each shot.
Contradictory saved capture-mode evidence rejects the calibrated candidate.
Malformed or mutually inconsistent startup files stop server startup because
the requested experiment cannot be defined. A later per-shot pose or mode
mismatch only rejects that shot's calibrated candidate; acquisition continues
and records the explicitly labelled legacy baseline.

The placement file is explicit; no value is inferred from the LIS3DH. The
numbers below only show the schema and are not calibration values:

```json
{
  "schema": "openflight.camera.placement",
  "version": 1,
  "world_frame": "target_lfu",
  "rig_geometry_sha256": "SHA256_FROM_THE_LOADED_RIG_SNAPSHOT",
  "optical_to_enclosure_lfu": [[1,0,0],[0,0,1],[0,-1,0]],
  "enclosure_to_target_lfu": [[1,0,0],[0,1,0],[0,0,1]],
  "camera_origin_lfu": [0.0,0.0,0.095],
  "radar_origin_lfu": [0.0,-0.030,0.051],
  "enclosure_pivot_lfu": [0.0,0.0,0.0],
  "reference_pose_deg": {"pitch": 0.0,"roll": 0.0},
  "rig_offset_consistency_tolerance_m": 0.002,
  "origin_provenance": {
    "camera_origin_lfu": "illustrative; replace with measured optical center",
    "radar_origin_lfu": "illustrative; replace with measured antenna center",
    "enclosure_pivot_lfu": "illustrative; replace with declared measured pivot"
  }
}
```

LFU means target-right, downrange, and up. The mount rotation maps optical RDF
axes into the enclosure LFU axes. The alignment rotation maps the enclosure at
the recorded reference pose into the target frame and therefore carries the
declared target-line heading. Origins and the pivot are meters in target LFU.
The placement rig hash must match the loaded rig exactly.

For each shot, the server freezes the observed calibrated pitch and gravity-
derived roll (the zeroed roll only on a unit that verified and zeroed it). It rotates the declared mount and both sensor origins about the
declared pivot relative to the reference pose. The LIS3DH supplies inclination;
it does not measure yaw, optical-center height, target alignment, or any origin.
Missing or non-finite gravity, missing capture-mode evidence, a mode mismatch,
or a changed artifact withholds the calibrated candidate while acquisition and
the legacy baseline remain available.

This transform corrects the camera rays and the camera/IWR origins used by the
shared camera candidate. It does not replace the IWR phase and ground-reflection
estimator's own geometry model; that estimator still receives its existing
scalar effective pitch correction. The persisted shared-core result exposes the
experimental calibrated-camera vertical estimate even when the canonical
vertical launch remains the IWR measurement. The roll is applied (to the
camera and the IWR) only on a unit whose tilt check verified and zeroed it; no accuracy
qualification is claimed for the RF estimator.

The per-shot `camera_fusion_context` binds the full calibrated model, placement,
observed pose, capture SHA-256, session UUID, shot number, range evidence, and
both tracker states. `replay_camera_fusion.py` restores this frozen context and
does not reread current calibration files.

An explicit pair applies only to its own saved mode; other modes measure on the
nominal camera, labelled. The tester itself still refuses a pair for another mode.

The lens catalogue's calibration directory chooses the newest matching file
separately for 1280x800 and 640x400. Its recorded `calibration_by_arm` identifies
the source for each focal length. When only one mode has a file, the existing
scaled focal fallback is recorded as `scaled_from_other_mode`. This directory
lookup supplies focal lengths; the calibration set above applies the full
principal point, Brown distortion and placement model.
