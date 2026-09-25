# Live calibrated camera-fusion candidate

Calibrated projection is opt-in. Without both flags below, OpenFlight keeps the
legacy reference-ball focal and pitch model. The calibrated path is an
experimental candidate and is not accuracy-qualified.

```bash
scripts/start-kiosk.sh \
  --camera-capture --iwr6843 --inclinometer \
  --rig-geometry config/enclosure_v3_rig_geometry.json \
  --camera-optical-calibration calibration/ov9281-mode.json \
  --camera-placement calibration/v3-placement.json
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
derived roll. It rotates the declared mount and both sensor origins about the
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
vertical launch remains the IWR measurement. No roll-compensation or accuracy
qualification is claimed for the RF estimator.

The per-shot `camera_fusion_context` binds the full calibrated model, placement,
observed pose, capture SHA-256, session UUID, shot number, range evidence, and
both tracker states. `replay_camera_fusion.py` restores this frozen context and
does not reread current calibration files.

One optical profile applies only to capture arms whose saved mode matches that
profile exactly. Other arms continue on the recorded legacy baseline and report
the calibrated candidate as rejected; run them with their own profile to opt in.
