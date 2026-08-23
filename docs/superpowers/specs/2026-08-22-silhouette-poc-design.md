# Rear-view silhouette impact-location POC — design specification

**Revision:** 2 (audited 2026-08-22)

**Status:** awaiting maintainer approval; implementation is prohibited until
approval

**Branch:** `feat/silhouette-poc`

**Audit:** `2026-08-22-silhouette-poc-audit.md`

Revision 2 incorporates Appendix A items A1–A10 and withdraws the Phase 1 `GO`
as an architecture gate. `RESULTS_0C_RADAR.md` remains the reproducible result of
the surrogate that ran, but that surrogate perturbed ball depth rather than
constraining club pose with radar range and preserved field of view while changing
plate-scale labels. A corrected, pre-registered Phase 1b is the first
post-approval activity.

## 1. Purpose and boundaries

Build a research-only proof of concept for estimating clubface impact location
from one rear-view OV9281 camera, IWR6843 club/ball range evidence, and OPS impact
timing. The POC must answer whether a silhouette-constrained clubhead trajectory
can yield a useful face impact vector before production camera hardware is owned.

In scope after approval:

- deterministic synthetic generator and exact archive/replay artifacts;
- corrected analytic mono-camera/radar fusion;
- Sim Studio research web app;
- end-to-end synthetic evaluation;
- a small optional ML segmentation leg;
- a research replay adapter for the current kiosk contract.

Out of scope:

- edits to `src/openflight/server.py` or other PR #215/#228 churn surfaces;
- shipping a production club-delivery algorithm;
- claiming iron-grade performance before clubhead radar Gate R;
- treating a theoretical 1280x200 mode as buildable before Gate B1;
- redistributing third-party videos or club meshes;
- implementation before explicit approval of this revision.

## 2. Success criteria and gates

The primary output is a two-dimensional face impact vector in millimetres:
`[horizontal_mm, vertical_mm]`, with status and confidence. Positive-axis
conventions are fixed in the generated truth metadata and visualized in Studio.

For each club/preset cell, report:

- solve rate;
- median and p90 Euclidean impact-vector error;
- median and p90 signed horizontal and vertical error;
- silhouette IoU and fit residual;
- ambiguity/quality rejection rate;
- component failure categories, never silently converted to zero error.

Provisional synthetic pass thresholds:

| Club | Median vector error | p90 vector error | Solve rate |
|---|---:|---:|---:|
| Driver | <=10 mm | <=20 mm | >=80% |
| 7-iron | <=12 mm | <=24 mm | >=80% |

These are POC thresholds, not product accuracy claims. Iron success is conditional
on Gate R showing a compatible real scattering-center residual. Synthetic success
cannot close Gates 0, B1, R, or T.

Required physical gates:

- **Gate 0 — plate scale:** `detect_reference_ball` on an unresized real 320x200
  capture at a surveyed tee distance; about 28 px confirms 0.656 px/mm and about
  56 px selects 1.31 px/mm.
- **Gate B1 — custom mode:** 10,000-frame 1280x200 capture, delivered_fps >=400,
  zero gaps, monotonic timestamps, no CSI errors, and a real-loader archive
  round-trip. Until then Preset B is experimental only.
- **Gate R — club radar:** surveyed static pose grid plus dynamic swings for the
  actual driver and 7-iron; publish signed median/p90 face-center range residual,
  pose dependence, and accepted-track RMS after static radar calibration.
- **Gate T — time:** simultaneously observe a physical optical/electrical event
  in the camera, OPS path, and radar trigger path; bound camera-to-impact and
  radar-to-impact offset/jitter, including host-epoch/monotonic conversion.

## 3. Hardware and model truth table

| Quantity | Revision-2 value | Status and use |
|---|---:|---|
| OV9281 production mode | 320x200; requested 450 fps; measured 468 fps; 500 us; gain 2 | Measured existing mode |
| 320x200 plate scale | 0.656 px/mm at 1.575 m | Best-supported working value; Gate 0 pending |
| 320x200 alternate | 1.31 px/mm at 1.575 m | Sensitivity only; derived from contradictory `.crop` metadata |
| Existing high-cadence fallback | 640x200 at about 536 fps | Measured feasibility; same optical footprint must not be inferred from dimensions alone |
| Preset B | 1280x200 portrait; target >=400 fps; theoretical 354–495 fps; 10–20 us | Experimental, Gate B1 required; `~446 feasible` is withdrawn |
| Preset B plate scale | 1.33 px/mm at 1.575 m | Provisional optical target, not a calibrated mode |
| Camera optical-center height | 0.20955 m | Measured rig constant |
| Nominal tee range | 1.575 m | Measured rig constant; scenario must vary around it |
| IWR range-bin resolution | 0.0469 m | Resolution, not accuracy |
| IWR static board range bias | 0.0660069821 m in reference config | Calibration term; subtract before fusion |
| IWR random club-track error | sigma 3 mm initial sensitivity | Conditional assumption; replace with Gate R accepted-track RMS |
| Club scattering-center residual | signed -40..+40 mm stress points | Sensitivity range, not a probability distribution |
| OPS IQ impact jitter | Gaussian sigma 33 us | Evidence-base working model |
| Frame-quantized fallback | exact Uniform(-T/2,+T/2), T=2.137 ms | Do not replace with a Gaussian in evaluation |

The 320x200 sensor registers describe an 816x516 sensor window, 320x200 output,
and 2x increment controls. The `.crop` struct describes 320x200 native pixels and
is inconsistent with those registers and repository hardware notes. Revision 2
therefore resolves A1 in favor of 0.656 px/mm while retaining Gate 0.

## 4. Research layout and immutable artifacts

All new work remains under `research/silhouette_poc/` except tests that exercise
the boundary using existing production code. The production wheel packages only
`src/openflight`, so research code remains excluded.

Planned layout after approval:

```text
research/silhouette_poc/
  generator/           deterministic scenes, rendering, archive writer
  fusion/              analytic silhouette/radar solver and confidence gates
  ml/                  optional dataset and segmentation student
  studio/              research web app and API
  replay/              radar/archive/socket boundary adapters
  eval/                Phase 1b and end-to-end runners/results
  tests/                unit, property, contract, and e2e tests
```

Each generated shot directory is immutable after creation:

```text
shot_<seed>/
  frames.npz
  metadata.json
  first.pgm
  trigger.pgm
  last.pgm
  radar_evidence.json
  truth.json
  session.json
```

Randomness is controlled by one recorded root seed and named child streams.
Rerunning the same config and seed must reproduce arrays and JSON values exactly.

### 4.1 Camera archive contract (A6)

`frames.npz` is uncompressed and contains exactly the production keys:

- `frames`: uint8 array `[N,H,W]`;
- `sensor_timestamp_ns`: int64 sensor timestamps `[N]`;
- `host_timestamp_ns`: int64 host timestamps `[N]`;
- `exposure_us`: int32 exposure values `[N]`;
- `analogue_gain`: float32 gain values `[N]`;
- `pre_trigger_count`: int32 scalar;
- `trigger_host_timestamp_ns`: int64 scalar;
- `trigger_epoch_timestamp`: float64 scalar.

`first.pgm`, `trigger.pgm`, and `last.pgm` are byte-equivalent views of the
corresponding NPZ frames. The trigger PGM index is
`max(0, pre_trigger_count - 1)`; downstream club delivery derives
`pre_trigger_count - 1` when the key is present. There is no stored
`trigger_frame_index` or sensor sequence. `metadata.json` mirrors
`capture_runtime._save_capture()`:

```json
{
  "frame_count": 0,
  "delivered_fps": 0.0,
  "gap_count": 0,
  "median_interval_ms": 0.0,
  "p95_interval_ms": 0.0,
  "max_interval_ms": 0.0,
  "sequence": 0,
  "trigger_timestamp": 0.0,
  "completed_timestamp": 0.0,
  "capture_path": "...",
  "pre_trigger_frames": 0,
  "post_trigger_frames": 0,
  "trigger_host_timestamp_ns": 0,
  "mean_brightness": 0.0,
  "p99_brightness": 0.0,
  "storage_format": "npz_uncompressed",
  "npz_bytes": 0,
  "save_time_ms": 0.0,
  "settings": {
    "width": 320,
    "height": 200,
    "fps": 450,
    "pre_ms": 0.0,
    "post_ms": 0.0,
    "exposure_us": 10,
    "gain": 2.0,
    "stream": "...",
    "rotate_180": false,
    "mirror_horizontal": false,
    "roll_correction_deg": 0.0,
    "scaler_crop": null
  }
}
```

For fewer than two frames, the three interval fields are absent, matching the
runtime. Synthetic captures normally contain at least two frames. Extra research
truth never goes in this metadata object.

The non-negotiable contract test writes the archive and loads it through
`server._load_camera_capture_archive`; all eight arrays/scalars must round-trip
with equal values, shapes, and dtypes, and the derived trigger index must select
the trigger PGM frame. Metadata/PGMs are checked separately because the loader
returns only NPZ members. No copied research loader can substitute for this test,
and the production loader is not modified.

The production session-log wrapper for a capture is:

```json
{
  "ts": "2026-08-22T00:00:00.000000",
  "type": "camera_capture",
  "shot_number": 1,
  "shot_timestamp": 0.0,
  "trigger_timestamp": 0.0,
  "trigger_delta_ms": 0.0,
  "capture_path": "...",
  "capture_error": null,
  "metadata": {}
}
```

### 4.2 Radar and OPS replay contract (A3/A4)

Production has no radar-evidence JSON serializer. The version-1 research replay
schema is a lossless representation of real `ClubRangeEvidence` and
`BallRangeEvidence` dataclasses:

```json
{
  "schema_version": 1,
  "club": {
    "type": "ClubRangeEvidence",
    "track": {
      "speed_ms": 0.0,
      "slope_bins": 0.0,
      "intercept_bins": 0.0,
      "rms_bins": 0.0,
      "n_inliers": 0,
      "t_first": 0.0,
      "t_last": 0.0,
      "low_confidence": false,
      "quad_bins": null
    },
    "geometry": {
      "n_frames": 0,
      "chirps_per_frame": 0,
      "n_tx": 0,
      "n_rx": 0,
      "n_samples": 0,
      "frame_period_s": 0.0,
      "trigger_frame": 0,
      "loop_period_s": 0.0,
      "range_bin_start": 0,
      "range_fft_size": null,
      "range_bin_starts": null,
      "range_bin_counts": null,
      "frame_time_offsets_s": null
    },
    "impact_t_s": 0.0
  },
  "ball": null,
  "calibration": {
    "range_bias_m": 0.0660069821,
    "source": "config/iwr6843_calibration_reference.json",
    "tee_range_m": 1.575,
    "tee_ball_height_m": 0.04,
    "radar_height_m": 0.1524
  },
  "ops": {
    "impact_timestamp_epoch_s": 0.0,
    "impact_sigma_us": 33.0,
    "club_speed_mph": 0.0,
    "ball_speed_mph": 0.0
  }
}
```

`quad_bins` is either null or a three-number array. Optional geometry arrays are
null or arrays of integers/floats matching their dataclass tuple types. `ball`,
when present, has the same shape with type `BallRangeEvidence`. Unknown schema
versions fail closed.

The replay adapter must deserialize to the production dataclasses. Apparent range
from `track.range_at()` is converted to calibrated true range by subtracting
`calibration.range_bias_m` before random track error or club scattering-center
residual is assessed. Calibration bias, track noise, and scattering residual are
three separate terms and must be separately recorded in `truth.json`.

### 4.3 Truth and session contracts

`truth.json` is versioned and contains, at minimum:

- coordinate-frame definitions and unit strings;
- camera intrinsics, distortion, pose, output dimensions, crop/sampling mapping;
- club identity, analytic dimensions, face plane/center, head pose and twist for
  every rendered time;
- ball center/radius and requested face impact vector;
- exposure start/end, nominal impact time, camera/radar/OPS clock offsets;
- uncalibrated range, static bias, track noise, scattering-center residual, and
  final apparent range;
- visibility/occlusion masks and generated silhouette masks;
- root seed, child seeds, generator version, and complete scenario config.

`session.json` wraps the exact `camera_capture` record plus the radar-evidence
relative path and expected `shot` replay envelope. Paths are relative to the shot
directory. It is a research manifest, not a production session-log claim.

## 5. Explicit camera configurations (A1/A2/A10)

No evaluation may call `scaled_intrinsics()` to represent a plate-scale change.
Each preset owns independent output dimensions, focal lengths, principal point,
sensor crop, sampling transform, orientation, and visibility bounds.

Provisional horizontal focal length follows
`f_px = plate_scale_px_per_mm * nominal_range_mm`:

| Preset | Output | Plate scale | Provisional `fx` | Buildability |
|---|---:|---:|---:|---|
| A0 | 320x200 | 0.656 px/mm | 1033 px | Existing mode; Gate 0 pending |
| A1 sensitivity | 320x200 | 1.31 px/mm | 2063 px | Not a second physical mode |
| B | 1280x200 portrait | 1.33 px/mm | 2095 px | Gate B1 pending |

These focal lengths are derived placeholders, not calibration results. `fy`,
principal point, orientation, and crop/sampling transform are explicit config
fields and cannot be inferred by uniformly scaling a baseline. Rendering outside
the actual bounds is a visibility failure. Distortion defaults to zero only for
the initial synthetic leg and must be independently swept later.

The generator uses a global-shutter exposure integral, not a single sharp sample
plus centroid noise. It renders multiple temporal sub-samples from exposure start
to end, including club translation and rotation, then applies photometric noise,
thresholding, occlusion, and component extraction.

## 6. Analytic fusion contract (A7)

Inputs are pre-impact grayscale frames/masks, calibrated camera configuration,
deserialized radar evidence, OPS impact time/speed, and a named club template.
Only one named driver and one named 7-iron are in the POC; “generic driver/iron”
templates are not accepted as final evidence.

For every pre-impact frame:

1. segment candidate clubhead components while excluding the reference ball;
2. render an exposure-integrated analytic clubhead template;
3. optimize multiple pose hypotheses against silhouette distance/IoU;
4. constrain the 3-D state by calibrated radar range/range-rate in the common
   camera/radar coordinate frame, including the surveyed sensor separation;
5. use temporal state continuity and OPS club-speed bounds;
6. retain covariance/Hessian conditioning, objective residual, and the
   best-vs-second hypothesis margin.

The template state must include at least translation, rotation, scale/depth, and
club-specific face-center/hosel offset. Radar is applied to the club state—not the
ball—and the assumed scattering point is explicit. Ball range may constrain ball
position separately but cannot stand in for club range.

Impact state is interpolated or extrapolated from strictly pre-impact frames.
Constant-velocity extrapolation is allowed only within a configured maximum
horizon and when acceleration/angular-rate residuals pass. Occluded/contact/post-
impact frames cannot anchor the club fit.

A solve fails closed on any of:

- component topology/area/aspect outside club-specific bounds;
- component merged with shaft, hands, or ball without a valid occlusion model;
- radar track low confidence, insufficient inliers, or range outside calibration;
- optimizer ill-conditioning or insufficient hypothesis margin;
- temporal discontinuity or disagreement with OPS speed;
- out-of-frame face/template support;
- extrapolation horizon or acceleration gate exceeded;
- camera/radar/OPS time mapping unavailable.

Confidence is calibrated against held-out synthetic errors; it is not simply the
optimizer score. Required analytic tests include zero-noise recovery, signed
range-bias symmetry, forward/reverse motion, exposure blur, partial visibility,
ball/shaft occlusion, false components, leave-one-template-out mismatch, and
acceleration.

## 7. Optional ML leg and data policy (A9)

The ML leg is optional and begins only after the analytic end-to-end gate. Its
role is clubhead segmentation, not direct impact regression. Analytic fusion and
exact geometry remain the measurement layer.

Canonical training inputs are synthetic images/masks plus a small, owned,
hand-reviewed proxy capture set. Annotation may be accelerated externally with:

- **CVAT Community + SAM 2** (preferred for video/QA), or
- **AnyLabeling + SAM 2** (lighter local alternative).

Every generated mask is human-reviewed. These tools are not vendored and do not
replace the repository dataset builder.

GolfDB may be used only as a locally obtained, non-redistributed, non-gating
sanity evaluation because the repository's CC BY-NC code statement does not
establish redistribution rights for every sourced video. It does not replace the
owned proxy set and is not a training input.

The surveyed Roboflow sets are object-detection boxes, not trustworthy
segmentation truth; they are rejected as core training/gating inputs. The
300-image public-domain head detector set may be used for an optional detector
sanity check with source/version recorded.

GrabCAD and CGTrader meshes are rejected as committed templates or reproducible
dependencies. Analytic parameterized templates are canonical. A model-specific
mesh may be used only in an uncommitted local sensitivity experiment after its
exact license/permission is documented.

No external dataset, mesh, or tool may silently change a pre-registered gate.

## 8. Sim Studio

Studio is a research-only local web app under `research/silhouette_poc/studio`.
It exposes deterministic generation/replay/evaluation through a thin local API;
it never imports or modifies the production Flask server.

Minimum views:

- scenario configuration with preset/buildability and unresolved-gate badges;
- frame timeline showing exposure windows, OPS impact, radar samples, and trigger;
- overlays for truth mask, observed mask, hypotheses, face center, radar range ray,
  and impact vector;
- objective surface/hypothesis margin and condition diagnostics;
- batch grid with solve rate, median, p90, signed axes, IoU, and failure categories;
- artifact download and immutable config/seed display.

The current pre-commit ESLint hook covers only `ui/src`; therefore implementation
must add explicit Studio lint, unit-test, and production-build commands to the
verification workflow (or make a separately approved hook change). Repository
Python hooks still apply to research Python.

## 9. Kiosk replay contract (A5)

The authoritative normal-shot server envelope is:

```json
{"shot": {}, "stats": {}}
```

`shot` mirrors `launch_monitor.shot_to_dict` and may contain these production
keys:

```text
ball_speed_mph, ball_speed_raw_mph, club_speed_mph, smash_factor,
estimated_carry_yards, carry_range, club, player_name, timestamp, peak_magnitude,
launch_angle_vertical, launch_angle_horizontal, launch_angle_confidence,
launch_angle_vertical_confidence, launch_angle_horizontal_confidence,
launch_angle_vertical_source, launch_angle_horizontal_source, angle_source,
club_angle_deg, club_path_deg, experimental_attack_angle_deg,
experimental_attack_angle_status, experimental_club_path_deg,
experimental_club_path_status, experimental_fused_attack_angle_deg,
experimental_fused_club_path_deg, experimental_fused_status,
experimental_fused_attack_angle_confidence,
experimental_fused_club_path_confidence, experimental_camera_trace_deg,
experimental_aoa_offset_source, iwr6843_horizontal_deg,
iwr6843_horizontal_confidence, experimental_camera_horizontal_deg,
experimental_camera_horizontal_confidence, experimental_camera_horizontal_status,
experimental_camera_iwr_delta_deg, spin_axis_deg, inclinometer, spin_rpm,
spin_rpm_measured, spin_source, spin_method, spin_confidence, spin_quality,
spin_multipath_fade_hz, spin_snr, spin_modulation_depth, spin_peak_freq_hz,
spin_candidate_rpm, spin_seam_cycles, spin_at_lower_rail, spin_at_upper_rail,
spin_candidates, spin_phase_method, spin_phase_rpm, spin_phase_snr,
spin_phase_agreement_pct, spin_phase_confirmed, spin_rejection_reason,
carry_spin_adjusted
```

Research replay may additionally include four optional keys without modifying the
production serializer:

```text
experimental_impact_offset_mm: [horizontal_mm, vertical_mm] | null
experimental_impact_height_mm: number | null
experimental_impact_confidence: number | null
experimental_impact_status: string
```

Swing-speed mode uses the same outer envelope but a separate
`swing_speed_to_shot_dict` payload. It adds `mode`,
`swing_speed_duration_ms`, `swing_speed_reading_count`,
`swing_speed_trigger_mph`, `training_implement`, and
`training_implement_label`, and it emits null/default launch and spin fields.
The silhouette replay represents normal shots and must not claim swing-speed
mode. A contract test nevertheless records this union so a future adapter does
not mistake the normal serializer for the only `shot` variant.

Before production integration, `ui/src/types/shot.ts` and the debug panel must be
updated explicitly; the current TypeScript type is not the full wire schema.

Non-empty `stats` contains exactly `shot_count`, `avg_ball_speed`,
`max_ball_speed`, `min_ball_speed`, `std_dev`, `avg_club_speed`,
`avg_smash_factor`, and `avg_carry_est`. Empty stats contains the same fields
except `std_dev`. The swing-speed mock uses the same shape. No spin or mode fields
are part of the current server stats contract.

Minimum replay behavior for the current kiosk:

- client requests: `get_session`, `get_trigger_status`, `get_radar_config`,
  `get_camera_capture_settings`;
- server events: `shot`, `session_state`, `trigger_status`, `radar_config`,
  `camera_capture_settings`, `camera_capture_settings_error`, `camera_status`,
  and `ball_detection`;
- camera controls: `set_camera_capture_settings`, `toggle_camera`,
  `toggle_camera_stream`;
- camera HTTP paths: `/camera/stream`, `/api/camera/preview.jpg`, and
  `/api/camera/exposure-quality`.

The research adapter may report `camera_available: false` and return explicit
unavailable responses for camera HTTP paths, but it must not hang or fabricate a
live production camera. Kiosk replay remains Phase 7 and does not justify editing
`src/openflight/server.py`.

## 10. Corrected Phase 1b gate

Phase 1b replaces—not supplements—the invalid architecture interpretation of
Phase 1. Appendix B is frozen on approval. The runner must apply club radar range
inside the club-state solver, use silhouettes rather than marker correspondences,
and use explicit per-preset intrinsics/bounds.

The gate report includes every cell, seed/config hash, solve rate, median, p90,
signed errors, IoU, visibility failures, and rejection categories. It must also
show a zero-noise recovery cell and an oracle-depth reference.

A winning “buildable” cell must satisfy §2 and one of:

- existing 320x200 optical mode plus a specified short-pulse strobe design; or
- a mode that has passed Gate B1.

Preset B cannot win the buildable gate before Gate B1. The 500 us blur surrogate
is a diagnostic bound, not proof ambient exposure is impossible. End-to-end
exposure-integrated template fitting decides recoverability.

If no hardware-grounded candidate passes, stop with `NO-GO` and revise the
architecture; do not proceed to phases 2–7 by selecting a theoretical preset.

## 11. Test and verification policy

After approval, TDD is mandatory for every implementation change: add the failing
test, run it and observe the intended failure, implement the minimum change, then
run the focused and regression suites. Record red/green commands in the work log
or commit message when practical.

Required commands use Windows/`uv`, never `make`:

```powershell
uv run --group research pytest research/silhouette_poc/tests -q
uv run --group research pytest research/club_pose/tests -q
uv run --group research pytest research/ball_spin/tests -q
uv run pre-commit run --all-files
```

Use the actual suite paths present at implementation time if a research package
keeps tests adjacent. In addition, run Studio lint/test/build and the
`frames.npz` round-trip test through
`server._load_camera_capture_archive`. Existing research suites must remain
green. Generated golden artifacts must be small, deterministic, and reviewed;
large datasets stay out of Git.

## 12. Approved implementation sequence (blocked pending sign-off)

No item below begins until the maintainer explicitly approves revision 2.

### Phase 1 — repair the evaluation gate

- Replace marker-PnP/ball-depth surrogate with silhouette plus club-range fusion.
- Replace fixed-FOV scale changes with explicit preset cameras and visibility.
- Implement Appendix B exactly and publish corrected results.
- Stop on `NO-GO`; Preset B remains non-buildable until Gate B1.

### Phase 2 — generator and real archive boundary

- Build deterministic analytic driver/7-iron/ball scenes and exposure integration.
- Write §4 artifacts and exact schemas.
- Make the real production loader round-trip test pass.

### Phase 3 — fusion and confidence

- Implement multi-hypothesis silhouette/radar state estimation and gates.
- Add zero-noise, signed-bias, blur, mismatch, occlusion, and extrapolation tests.
- Calibrate synthetic confidence only on held-out scenarios.

### Phase 4 — Sim Studio

- Build the local API and diagnostic UI.
- Add explicit Studio lint/test/build verification.

### Phase 5 — end-to-end evaluation

- Run the frozen grid plus held-out stress cases.
- Publish all cells, p90, solve rates, signed errors, and failure taxonomy.
- Do not reinterpret unresolved physical gates as synthetic successes.

### Phase 6 — optional ML segmentation

- Proceed only if the analytic pipeline passes and segmentation is a measured
  bottleneck.
- Use synthetic plus owned/reviewed proxy data under §7 policy.
- Compare analytic-mask oracle, classical segmentation, and ML segmentation.

### Phase 7 — kiosk replay integration

- Serve the §9 research replay contract without touching the production server.
- Update production UI types/panel only in a separately approved integration
  change after the research result is accepted.

## 13. Prior art and product risk

The closest verified engineering prior art combines camera angular observations
with radar range/range-rate, explicit sensor separation, common time/coordinates,
club-specific face geometry, and interpolation/extrapolation to impact
(US10471328B2 and US10989791B2). TrackMan also publicly describes inter-frame club
positioning and silhouette analysis. Revision 2 incorporates those engineering
requirements; it does not make a freedom-to-operate conclusion. Legal/FTO review
is required before commercial deployment.

US10393870B2 is a sports-ball spin patent and is not the relevant architecture
reference cited by revision 1.

## Appendix A — audit resolution register

| Item | Verdict | Inline resolution | Remaining physical measurement |
|---|---|---|---|
| A1 | CONFIRMED | §§3, 5 select 0.656 and demote `.crop` alternative | Gate 0 reference-ball diameter |
| A2 | UNRESOLVABLE-without-hardware | §§2, 3, 5 withdraw 446-fps feasibility/buildability | Gate B1 sustained custom-mode capture |
| A3 | REFUTED-with-fix | §4.2 attaches the dataclass-faithful replay schema | Replay against first real accepted tracks |
| A4 | UNRESOLVABLE-without-hardware | §§3, 4.2 separate static bias/noise/signed residual | Gate R metrology pose grid and dynamic swings |
| A5 | REFUTED-with-fix | §9 attaches shot/stats/events/HTTP contract | Kiosk contract test during Phase 7 |
| A6 | REFUTED-with-fix | §4.1 attaches NPZ/metadata/session schemas | Real-loader round-trip is mandatory |
| A7 | REFUTED-with-fix | §6 adds observability, mismatch, blur, and extrapolation gates | Owned real proxy capture after analytic gate |
| A8 | CONFIRMED | §§4, 8, 11 preserve package boundaries and add Studio checks | None for packaging; CI commands verify |
| A9 | REFUTED-with-fix | §7 records conditional adoptions/rejections; audit has license evidence | Human rights/QC record for any optional data |
| A10 | REFUTED-with-fix | §5 bans fixed-FOV scaling as a plate-scale model | Gate 0/checkerboard calibration |

## Appendix B — Phase 1b pre-registered evaluation grid

This grid supersedes revision 1 Appendix B. Freeze it on approval and do not tune
thresholds after results are visible.

### B1. Core Cartesian grid

- **Club:** named POC driver; named POC 7-iron.
- **Camera preset:** A0 320x200 @ 0.656 px/mm; A1 sensitivity 320x200 @
  1.31 px/mm; B 1280x200 @ 1.33 px/mm. Use §5 independent intrinsics/crops.
- **Exposure:** 10 us; 500 us. Render an exposure integral at the per-club speed
  distribution, not fixed centroid noise.
- **Impact timing:** Gaussian sigma 33 us; exact Uniform(-T/2,+T/2) with
  T=2.137 ms.
- **Club depth source:** oracle sigma 3 mm reference; mono radar with track noise
  sigma 3 mm and signed scattering residual in
  {-40,-20,-10,0,+10,+20,+40} mm.
- **Ball depth:** independently modeled radar ball evidence, sigma 3 mm and zero
  residual in the core grid. It never substitutes for club depth.
- **Trials:** >=1,000 per cell from a recorded root seed, with identical truth
  scenarios shared across comparable cells.

Static board range bias is always injected into apparent radar evidence and then
removed by the replay/fusion calibration path. A separate negative test omits the
calibration and must show the expected signed error; it is not a candidate cell.

### B2. Mandatory validation/stress cases

- zero noise/blur/timing error, full visibility: numerical recovery tolerance;
- explicit FOV-edge trajectories and partial club/ball visibility;
- forward and reverse motion to detect signed blur/bias mistakes;
- ball overlap, shaft-connected component, false component, and dropped frame;
- club-template dimension perturbations and leave-one-template-out geometry;
- nonzero translation/angular acceleration and maximum extrapolation horizon;
- radar low-confidence, reduced inliers, measured RMS, and missing evidence;
- camera/radar extrinsic and time-offset perturbations;
- distortion and principal-point perturbations;
- signed scattering residual symmetry.

### B3. Reporting and decision

For every core/stress cell report §2 metrics, scenario count, seed/config hash,
buildability state, and exact failure taxonomy. Include p90; median-only reporting
is a protocol failure.

Decision order:

1. Verify zero-noise numerical recovery and archive/radar contract tests.
2. Compare oracle-depth and mono-radar degradation.
3. Select only candidates whose hardware status meets §10.
4. Apply driver and iron thresholds independently.
5. Label iron `HARDWARE-BLOCKED` until Gate R even if synthetic thresholds pass.
6. If no eligible driver candidate passes, verdict `NO-GO` and stop.

### B4. Interpretation of revision-1 results

`RESULTS_0C_RADAR.md` is retained unchanged as provenance. Its 2.39 mm driver
median, 9.33 mm at “20 mm bias,” and 13.43 mm iron result at “10 mm bias” describe
ball-depth perturbations after marker-based club PnP. They must not appear as
clubhead radar-bias performance or support iron/driver design decisions. The
500 us failures are a blur-as-centroid-noise sensitivity bound: that model can be
pessimistic when a blurred template retains information and optimistic when
asymmetric shape, occlusion, or mismatch causes bias.
