# Replay recorded camera fusion

New camera-fusion shots preserve `camera_fusion_context` and
`camera_fusion_processing` in the shot log. Live processing and the offline
entry point call the same camera ball-flight, horizontal selection and club
delivery core. This replays the camera stage using its recorded radar evidence;
it is not a complete replay of raw OPS/IWR extraction or asynchronous shot
scheduling.

The input context identifies the session, shot and exact camera NPZ bytes. It
includes the effective geometry, lighting decision, OPS/IWR inputs, fitted
radar range evidence and both reference-ball trackers before processing. Tracker
limits and all retained samples are included, so an established anchor is not
silently replaced by an empty tracker. Processing returns both updated tracker
states and preserves separate stage failures.

From the repository root, replay a newly recorded shot:

```bash
uv run --extra camera python scripts/analysis/replay_camera_fusion.py /path/to/run/session_example.jsonl 1
```

For recordings copied from the Pi, explicitly identify the relocated capture:

```bash
uv run --extra camera python scripts/analysis/replay_camera_fusion.py /path/to/copied/session_example.jsonl 1 --capture /path/to/copied/camera/capture_001 --output replay-result.json
```

The explicit capture must match the NPZ hash in the recorded shot context.
Renaming or relocating identical bytes is allowed; substituting another shot
is not. Keep replay reports separately from original evidence.

Compare categorical statuses and numerical outputs with the recorded result.
Numerical tolerance permits platform-level floating-point differences; it is
not a physical accuracy budget. A reproducible wrong model is still wrong.
Independent calibration and reference error remain separate checks.

Older captures without complete context cannot establish exact individual-shot
replay. Missing trackers, rejected lighting, unavailable geometry, invalid
identity or changed input bytes must remain explicit. The live compatibility
path may still produce diagnostics when complete replay context is unavailable;
that does not qualify those shots for exact replay.

Replay restores either the recorded reference-ball model or the opt-in
[calibrated camera model](live-calibrated-fusion.md), including its declared
placement and pose. It does not promote calibration candidates or verify
physical timing. See the [master plan](../development/fusion-master-plan.md)
for the physical calibration and accuracy gates.

## Offline sensitivity variants

Run bounded, manifest-declared perturbations without changing live settings:

```bash
uv run --extra camera python scripts/analysis/replay_fusion_sensitivity.py run/session.jsonl 1 --manifest variants.json --output sensitivity.json
```

The exact v1 manifest is `{"schema_version": 1, "variants": [...]}`, with at
most 32 variants. For example, these are declared perturbations, not measured
errors or acceptance limits:

```json
{
  "schema_version": 1,
  "variants": [
    {"id": "ball-trigger-plus-1ms", "kind": "camera_trigger_offset_ns", "offset_ns": 1000000},
    {"id": "ball-no-depth", "kind": "remove_ball_range_evidence"},
    {"id": "club-no-depth", "kind": "remove_club_range_evidence"},
    {"id": "camera-right-1mm", "kind": "camera_translation_m", "lateral_m": 0.001, "forward_m": 0.0, "height_m": 0.0},
    {"id": "focal-plus-1pct", "kind": "calibrated_focal_scale", "scale": 1.01}
  ]
}
```

Each
unique variant has an `id` and one of: `camera_trigger_offset_ns` with integral
`offset_ns` within one second (it perturbs only the ball-stage trigger time);
`remove_ball_range_evidence`; `remove_club_range_evidence`; or
`camera_translation_m` with finite `lateral_m`, `forward_m`, and `height_m`
within one metre; or `calibrated_focal_scale` with `scale` from 0.5 to 1.5.
For calibrated snapshots, translation rebuilds the declared target-LFU camera
origin at the reference pose and focal scaling rebuilds the candidate intrinsics through the normal
validated constructor. These are assumed perturbations, never calibration
updates. Reports retain immutable baseline output, each result or unavailable
reason, deltas, original source hashes, and distinct effective-input hashes.
They do not refit/retrack, change live evidence, or make accuracy claims.
