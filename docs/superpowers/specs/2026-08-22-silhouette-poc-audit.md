# Silhouette POC design audit

**Date:** 2026-08-22

**Branch:** `feat/silhouette-poc`

**Audited spec:** `2026-08-22-silhouette-poc-design.md`, revision 1

**Outcome:** revision 1 is not implementation-ready. Revision 2 resolves the
documentable issues, but camera mode B, plate scale, clubhead radar bias, and
end-to-end time alignment retain named hardware gates.

## Executive verdict

The architecture remains worth testing, but the recorded Phase 1 `GO` is not a
valid gate for the architecture in the spec. The evaluation perturbs **ball**
depth after estimating club pose from perfect marker correspondences; it never
applies radar range to the clubhead. It also scales image dimensions and focal
length together, preserving field of view, so it repeats the plate-scale/FOV
problem Appendix A asked it to catch. The 2.39 mm result is a useful regression
result for that implemented surrogate, not evidence that mono silhouette plus
club radar achieves 2.39 mm impact-vector error. A corrected Phase 1b is now the
first post-approval phase.

Audit verdicts:

| Item | Verdict | Resolution in revision 2 |
|---|---|---|
| A1 | **CONFIRMED** | Use 0.656 px/mm as the best-supported 320x200 value; retain 1.31 only as sensitivity until Gate 0. |
| A2 | **UNRESOLVABLE-without-hardware** | Replace “~446 fps feasible” with a theoretical 354–495 fps bracket and Gate B1. |
| A3 | **REFUTED-with-fix** | There is no production radar JSON schema; revision 2 defines a versioned replay envelope faithful to the dataclasses. |
| A4 | **UNRESOLVABLE-without-hardware** | Separate calibrated static bias, random range error, and signed club scattering-center residual; require Gate R. |
| A5 | **REFUTED-with-fix** | Attach the authoritative `shot` envelope and kiosk event/HTTP subset. |
| A6 | **REFUTED-with-fix** | Attach the exact NPZ, metadata, and session-log contracts. |
| A7 | **REFUTED-with-fix** | Add ambiguity, template-mismatch, blur, observability, and extrapolation gates. |
| A8 | **CONFIRMED** | Research code stays outside the wheel; add explicit Studio lint/test/build because current hooks do not cover it. |
| A9 | **REFUTED-with-fix** | Correct license/label claims; conditionally adopt annotation aids and GolfDB sanity checks, reject external meshes and Roboflow as core inputs. |
| A10 | **REFUTED-with-fix** | Phase 1 used fixed-FOV scaling; revision 2 requires explicit intrinsics and crop bounds per preset. |

Verdicts are about the revision-1 text, not a claim that the physical system has
already passed the named gates.

## A1 — 320x200 plate scale

**Verdict: CONFIRMED**, with Gate 0 retained because the repository contains two
contradictory representations and no committed raw capture.

Evidence:

- The high-speed patch programs sensor coordinates x=336..1151 (816 columns)
  and y=150..665 (516 rows), output 320x200, ISP offsets of four pixels, and
  `0x3814=0x22`, `0x3815=0x22`. The `.crop` member instead declares a native
  320x200 rectangle at x=480, y=150. Those statements cannot both describe the
  optical sampling footprint. [R1]
- The upstream OV9282 driver represents the 640x400 binned mode with the full
  1280x800 crop and increment registers; it does not rewrite `.crop` as a
  640x400 native window. This supports interpreting `.crop` in the local patch
  as metadata, not as proof of 1:1 sampling. [W1]
- The sensor datasheet defines `0x3814/0x3815` as horizontal/vertical
  odd/even-increment controls. [W2]
- The repository's driver test records a hardware comparison that found two
  native columns per output pixel plus a 96-column framing correction. [R2]
- A 42.67 mm ball at 0.656 px/mm is about 28 pixels; at 1.31 px/mm it is about
  56 pixels. This arithmetic is an **inference** from the two candidate scales.

Resolution: 0.656 px/mm is the best-supported working value. The `.crop`-derived
1.31 px/mm is a sensitivity case only. Code that halves image scale solely from
320-vs-640 dimensions is unsafe because both modes can cover the same sensor
footprint. [R1, R2]

**Named physical measurement — Gate 0:** run `detect_reference_ball` against one
unresized real 320x200 capture at the measured tee distance. A diameter near 28
pixels confirms 0.656 px/mm; near 56 pixels refutes it and selects 1.31 px/mm.
Record the raw PGM, settings, distance, detected diameter, and confidence.

## A2 — 1280x200 at about 446 fps

**Verdict: UNRESOLVABLE-without-hardware.** The revision-1 claim was the weakest
hardware assumption and must not define a buildable evaluation winner.

Evidence:

- OmniVision specifies 1280x800 at 120 fps and two-lane MIPI for OV9281. [W3]
- The upstream driver uses a 160 MHz pixel rate, 1456-pixel line length for
  full-width modes, and mode-specific minimum vertical blanking. [W1]
- **Inference:** at 1456 pixels/line, 200 active rows plus 22 blank rows gives
  about 495 fps; 200 plus the full-width mode's 110-row minimum gives about
  354 fps. The claimed 446 fps falls inside that bracket, but no source proves
  which blanking constraint a custom 1280x200 mode accepts.
- **Inference:** active RAW10 payload is about 1.142 Gbit/s
  (`1280*200*446*10`), below two 400 MHz DDR lanes' nominal 1.6 Gbit/s. That
  payload calculation excludes packet and blanking overhead and is not proof
  that the sensor, CSI receiver, driver, and libcamera pipeline sustain it.
- The local driver has no 1280x200 mode. The existing measurements demonstrate
  640x200 at about 536 fps and 320x200 at about 576 fps, not the 114 Mpixel/s
  active throughput required by this preset. [R1, R3]

Resolution: Preset B is an experimental target, with theoretical cadence
354–495 fps and target delivered cadence >=400 fps. It is not “buildable” for a
gate until measured. Revision 2 makes 320x200 plus short strobe the primary
hardware-grounded candidate and retains 640x200 as the already-demonstrated
high-cadence fallback. This is an engineering conclusion, not a cited sensor
guarantee.

**Named physical measurement — Gate B1:** after approval, add the isolated
1280x200 driver mode and capture at least 10,000 frames with the production
capture runtime. Require delivered_fps >=400, `gap_count == 0`, monotonic sensor
timestamps, no CSI errors, and a saved archive that round-trips through
`server._load_camera_capture_archive`. Record actual HTS/VTS/link settings. If
it fails, remove Preset B from buildable candidates.

## A3 — radar evidence schema

**Verdict: REFUTED-with-fix.** Revision 1 asked for a JSON shape that does not
exist in production.

Evidence:

- `ClubRangeEvidence` and `BallRangeEvidence` are Python dataclasses containing
  `track: BallTrack`, `geometry: Geometry`, and `impact_t_s: float`. [R4]
- `BallTrack` contains fitted speed/intercept/slope/RMS/inlier/time/confidence
  fields and an optional quadratic. `Geometry` contains frame/chirp/antenna/
  sample counts, timing, range-window fields, and optional per-frame arrays.
  [R5]
- Both range-evidence objects are intentionally removed by production
  serializers rather than emitted over Socket.IO. There is therefore no exact
  runtime radar JSON contract to copy. [R6]
- `track.range_at()` returns the fitted apparent range. The configuration stores
  a 0.0660069821 m static calibration bias, and `Calibration.true_range()`
  subtracts it. Existing camera consumers do not carry that calibration in the
  evidence object. [R5, R7]

Resolution: revision 2 defines `radar_evidence.json` schema version 1 as a
lossless replay representation of those dataclasses and adds explicit calibration
and OPS timing context. Deserialization must recreate the real dataclasses; it
must not invent a parallel “simplified” radar model. Static board bias is removed
before club scattering-center residual is modeled.

## A4 — IWR6843 clubhead range error and bias

**Verdict: UNRESOLVABLE-without-hardware.** Neither 3 mm random error nor a
one-sided uniform 0–40 mm clubhead bias is grounded for this target.

Evidence:

- TI distinguishes range resolution from range accuracy and reports millimetric
  accuracy for suitable point targets under favorable conditions; that is not a
  claim about a moving, rotating golf club. [W4]
- TI's range-bias calibration uses a known strong target. Its antenna-calibration
  note recommends a corner reflector because a metal plate can create multiple
  points and reduce calibration accuracy. A clubhead is a still more complex
  scattering target. [W5, W6]
- The 3.3 mm figure in the evidence base is an **inference** from
  `46.9 mm / sqrt(2*100)` at an assumed 20 dB SNR. It is conditional, not a
  measured club-track distribution. [R3]
- No TI source found quantifies the displacement between a golf club's radar
  scattering center and geometric face center. The 0–40 mm interval is therefore
  an **engineering sensitivity range**, not a calibrated probability model.
- The local device calibration already contains a +66 mm static range bias;
  mixing it with club-dependent scattering-center error would double-count two
  different effects. [R7]

Resolution: model three terms separately: (1) measured static board bias, removed
before fusion; (2) zero-mean track noise, initially stress-tested at sigma 3 mm
but ultimately taken from accepted `track.rms_bins`; and (3) signed,
club/pose-dependent scattering-center residual. Until Gate R, test residuals at
-40, -20, -10, 0, +10, +20, +40 mm and never call that sweep a distribution.

The Phase 1 observation that “radar bias maps approximately 1:1 to impact height”
is not evidence for clubhead sensitivity: the implementation applied the bias to
the ball. The iron/driver 10/20 mm statements in `RESULTS_0C_RADAR.md` are thus
invalid for the proposed fusion architecture. [R8, R9]

**Named physical measurement — Gate R:** mount the actual POC driver and 7-iron
on a metrology jig/rotary fixture at surveyed radar-to-face-center ranges and a
grid of face, loft, and lie angles. For each pose, record raw IWR tracks, subtract
the corner-reflector/known-target board calibration, and compare apparent range
with the optical/geometric face-center range. Repeat dynamically over representative
swings. Publish signed median, p90, pose dependence, and accepted-track RMS for
each club. That measurement decides whether iron-grade impact location remains
in scope.

## A5 — Socket.IO and kiosk contract

**Verdict: REFUTED-with-fix.** Revision 1 named the integration but omitted the
authoritative payload and the camera/session behavior needed by the current UI.

Evidence:

- The normal launch-monitor path emits `shot` as `{shot: shot_to_dict(shot),
  stats: monitor.get_session_stats()}`. `shot_to_dict` is authoritative for that
  path and includes ball/club speed, carry, angles, experimental fields, and the
  complete spin diagnostics. Swing-speed mode emits the same outer envelope but
  uses `swing_speed_to_shot_dict`, a separate UI-compatible variant. [R10]
- The TypeScript `Shot` interface omits some serialized fields, including
  `ball_speed_raw_mph`, `inclinometer`, and several spin diagnostics; copying it
  as the wire schema would lose data. [R11]
- On connect the kiosk requests session, trigger, radar, and camera-capture
  settings. Its camera view also uses `/camera/stream`,
  `/api/camera/preview.jpg`, and `/api/camera/exposure-quality`, and listens for
  camera settings/status/detection events. [R11]

Resolution: revision 2 attaches the existing envelope, stats fields, required
kiosk events/endpoints, and four optional research-only impact fields. The POC
replay adapter may emit the additions; `src/openflight/server.py` remains
unchanged. A later production integration must update the UI type and debug panel
explicitly rather than relying on undeclared properties.

## A6 — camera archive and session-log contract

**Verdict: REFUTED-with-fix.** The NPZ key list in revision 1 was not the
production archive schema, and the metadata/session schemas and trigger-frame
rule were incomplete.

Evidence:

- `_save_capture()` writes uncompressed `frames.npz` with exactly `frames`,
  `sensor_timestamp_ns`, `host_timestamp_ns`, `exposure_us`, `analogue_gain`,
  `pre_trigger_count`, `trigger_host_timestamp_ns`, and
  `trigger_epoch_timestamp`. Revision 1's key list was not the production
  archive schema. It also writes `metadata.json` and
  `first.pgm`/`trigger.pgm`/`last.pgm`. [R12]
- There is no stored `trigger_frame_index`; consumers derive it as
  `pre_trigger_count - 1`, while the saved trigger PGM clamps that expression to
  zero. Metadata combines the timing
  summary with sequence/timestamps/path/window counts/host trigger/brightness/
  storage/bytes/save time and the full capture-settings object. For fewer than
  two frames the timing summary omits interval percentiles. [R12]
- The session logger writes a `camera_capture` line with shot number/time,
  trigger time/delta, capture path/error, and the metadata object; `_write_entry`
  adds ISO-8601 `ts` and `type`. [R13]

Resolution: revision 2 makes the eight archive keys, derived trigger rule,
conditional metadata fields, and session nesting explicit and requires a
value/dtype/shape round-trip through PR #215's real loader.

## A7 — silhouette ambiguity and model risk

**Verdict: REFUTED-with-fix.** A four-parameter template optimizer alone does not
make rear-view clubhead pose observable or robust.

Evidence:

- The existing club-delivery path tracks Shi–Tomasi corners with pyramidal LK,
  forward/backward consistency, robust consensus, physical gates, and multiple
  pre-impact windows. It is not an existence proof for silhouette template
  fitting. [R14]
- Relevant prior art explicitly combines an imager's angular information with
  radar range/range-rate, a sensor separation vector, common coordinates/time,
  and extrapolation/interpolation when the image is not exactly at impact. It
  also uses club-specific face/hosel geometry. [W7, W8]
- **Inference:** rear-view silhouettes can have mirrored or shallow objective
  minima; shaft/hands/ball can merge with the component; driver crown/back shape
  differs by model; exposure integration can shift asymmetric edges; and impact
  occlusion makes the contact frame unsafe. These are model risks, not claims
  that the listed tests have already failed.

Resolution: restrict the POC to one named driver and one named 7-iron geometry;
fit strictly pre-impact frames; render exposure-integrated templates; enforce
component topology, temporal, radar, OPS-speed, and physical-shape gates; retain
multiple hypotheses; report Hessian/condition number and best-vs-second objective
margin; test zero-noise recovery, reverse motion, leave-one-template-out mismatch,
occlusion, shaft contamination, and acceleration. Extrapolation beyond the last
pre-impact observation requires a bounded horizon and residual/acceleration gate.
Patent references are engineering prior art, not legal advice; an FTO review is
required before commercial deployment of this architecture. [W7, W8, W9]

## A8 — packaging and boundary isolation

**Verdict: CONFIRMED**, with a tooling qualification.

Evidence:

- Hatch builds only `src/openflight`; `research/` is excluded from the production
  wheel. [R15]
- Current pre-commit hooks run Ruff/format over Python, Pylint only under
  `src/openflight/`, and ESLint only under `ui/src/`. A new Studio frontend below
  `research/` would not be linted by the current ESLint hook. [R16]

Resolution: keep generator/fusion/Studio/replay under `research/silhouette_poc`;
adapt at real archive/radar/socket boundaries; do not modify churn-heavy server
code. Implementation CI must explicitly run Studio lint, tests, and build (or a
later approved hook change) in addition to repository pre-commit. Importing the
real archive loader in the required round-trip test is allowed; replacing it with
a research copy is not.

## A9 — external tools, data, licenses, and prior art

**Verdict: REFUTED-with-fix.** The survey contained useful leads, but several
“Adopt” descriptions overstated label type, redistribution rights, or what the
asset could replace.

Decisions:

| Survey row | Verified facts | Decision |
|---|---|---|
| GolfDB | Repository code is CC BY-NC 4.0; 1,400 sourced videos have event, box, club, and view annotations. The repository does not establish redistributable rights to all source videos. [W10] | **Conditional adopt:** external, non-redistributed, non-gating sanity evaluation only after the user obtains it lawfully. Do not train on it, commit frames, or replace the owned proxy set. |
| Three Roboflow datasets | The 9,211-image “segmentation” page is actually object detection with opaque classes; the 300-image head set is public-domain object detection; the tracking set is CC BY 4.0 object detection and its version page includes generated augmentation. [W11–W13] | **Reject as core/segmentation/training input.** Optional 300-image detector sanity check only; no effect on success gates. |
| AnyLabeling + SAM | AnyLabeling is GPL-3.0; SAM 2 is Apache-2.0. [W14, W15] | **Adopt as an external offline annotation accelerator.** Human review owns every mask; do not vendor it or treat generated masks as truth. |
| CVAT + SAM | CVAT Community core is MIT; serverless assets/dependencies can have separate licenses. [W16] | **Adopt as the preferred alternative** when video interpolation/QA is useful, with the same human-QC rule and dependency review. |
| GrabCAD/CGTrader club meshes | GrabCAD public/commercial reuse depends on model-specific permission/attribution; CGTrader bars standalone redistribution and applies model-specific terms. [W17, W18] | **Reject as committed templates or reproducible dependencies.** Analytic templates remain canonical; a locally licensed mesh may be used only for an uncommitted sensitivity experiment. |

License corrections to the survey's “consider” rows: Rerun is dual MIT/Apache-2.0,
not MIT-only; BlenderProc is GPL-3.0; OpenShotGolf is GPL-2.0; CVAT Community is
MIT with caveats for separate assets. [W16, W19–W21] These tools are not needed
for revision-2 implementation, so no dependency is adopted now.

Prior-art correction: US10393870B2 concerns sports-ball spin determination, not
the claimed silhouette/radar impact-location method. More relevant documents are
US10471328B2 (radar plus imager and moving a club observation to impact) and
US10989791B2 (camera line-of-sight plus radar range/range-rate and a separation
vector). TrackMan publicly describes dual radar plus camera, inter-frame club
positioning, and silhouette-based club analysis. [W7–W9, W22]

No off-the-shelf, rear-view golf-club **pose** dataset or model was found in the
survey; this is a bounded search result, not proof none exists. Bounding-box
datasets do not resolve face-center pose.

## A10 — fixed FOV and plate-scale evaluation

**Verdict: REFUTED-with-fix.** The Phase 1 implementation repeats the flaw named
by Appendix A.

Evidence:

- `scaled_intrinsics()` scales `fx`, `fy`, `cx`, `cy`, width, and height by the
  same factor and documents that this preserves field of view. [R17]
- `budget_radar.py` selects plate scale by calling that helper. It therefore does
  not hold output dimensions/crop bounds fixed while changing focal length and
  does not test the real visibility tradeoff. [R8]

Resolution: every preset now has explicit output width/height, focal lengths,
principal point, crop/sampling transform, and bounds. At 1.575 m, provisional
horizontal focal lengths are approximately 1033 px for 0.656 px/mm, 2063 px for
1.31 px/mm, and 2095 px for 1.33 px/mm; these are **inferences** from
`f_px = plate_scale_px_per_mm * range_mm`, not calibrated intrinsics. Phase 1b
must render/project with those independent configurations and count lost
club/ball visibility as a failure. Gate 0 or a checkerboard calibration replaces
the provisional values when hardware exists.

## Phase 1 implementation audit

**Verdict: REFUTED-with-fix; the recorded `GO` is withdrawn as an architecture
gate.** The result file remains an honest record of the code that ran.

What was faithful:

- `budget_radar.py` enumerates the 120 revision-1 combinations: three plate-scale
  labels, two sync modes, stereo plus four “radar” bias modes, two exposures, and
  two clubs. Its tests pass (`8 passed` via the required `uv` command). [R8, R9]
- It uses a deterministic seed and writes the stated JSON/Markdown artifacts
  (`results_0c_radar.json` has 120 rows).
  [R8]

Gate-changing defects:

1. `run_budget()` estimates mono club pose with marker PnP, then calls
   `_perturb_ball()` with `ball_depth_bias_mm`. No club range measurement enters
   pose or face-center estimation. The test explicitly asserts a ball-depth
   shift. Thus the “radar bias” axis is not clubhead radar bias. [R9, R17]
2. Marker PnP receives known 3-D/2-D marker correspondences. That is materially
   more informative than an unmarked rear-view silhouette and bypasses the
   ambiguity under test. [R17]
3. Preset scaling preserves FOV, so visibility and crop tradeoffs are not tested.
   [R8, R17]
4. `RESULTS_0C_RADAR.md` reports medians but not the pre-registered p90 metric.
   [R18]
5. A fixed positive bias is used, whereas the stated model was uniform 0–B and a
   physical scattering-center residual can be signed and pose-dependent. [R8]
6. `_buildable()` labels Preset B at 10 us buildable even though the local driver
   has no 1280x200 mode and no sustained capture validates it. Therefore even the
   implemented surrogate does not support the phrase “best buildable cell.”
   [R1, R8]

Non-gate-changing qualifications:

- IQ sync is Gaussian sigma 33 us. The frame-quantized case uses a Gaussian with
  matching uniform variance (`2137/sqrt(12)`) rather than sampling the exact
  uniform interval. That is a minor distribution mismatch. [R8]
- Blur is converted to independent centroid noise at a fixed 45 m/s for both
  clubs. This can be pessimistic when an exposure-integrated template preserves
  recoverable edges, but it can also be optimistic for asymmetric blur,
  occlusion, or template mismatch. The result file correctly flags the first
  direction but not the second. It cannot prove ambient 500 us exposure is
  unusable. [R17, R18]
- The 10 us cells have negligible blur in this surrogate; their invalidation is
  from the pose/radar/FOV defects, not from the blur qualification.

Required Phase 1b corrections are pre-registered in revision 2 Appendix B. No
phases 2–7 may begin before approval and a passing corrected gate.

## Source index

Repository sources (all on `feat/silhouette-poc` at audit time):

- **R1:** `drivers/ov9281/ov9282-high-speed.patch` (`ov9282_320x200_regs`,
  `supported_modes`).
- **R2:** `tests/test_ov9281_driver_patch.py` (320x200 crop/scale hardware note).
- **R3:** `docs/Personal Research/camera-feasibility-verdict-2026-08.md` (read-only
  evidence base; no audit edits).
- **R4:** `src/openflight/iwr6843/club.py::ClubRangeEvidence` and
  `src/openflight/iwr6843/lcmf.py::BallRangeEvidence`.
- **R5:** `src/openflight/iwr6843/tracking.py::BallTrack`, `Geometry`, and
  `range_at`.
- **R6:** `src/openflight/server.py::shot_to_dict`, the non-serialized evidence
  fields on `src/openflight/launch_monitor.py::Shot`, and
  `src/openflight/iwr6843/club.py::ClubPathResult.to_dict`.
- **R7:** `config/iwr6843_calibration_reference.json` and
  `src/openflight/iwr6843/calibration.py::Calibration.true_range`.
- **R8:** `research/silhouette_poc/eval/budget_radar.py`.
- **R9:** `research/silhouette_poc/tests/test_budget_radar.py`.
- **R10:** `src/openflight/server.py` (`handle_shot`, Socket.IO handlers) and
  `src/openflight/launch_monitor.py::shot_to_dict`.
- **R11:** `ui/src/types/shot.ts`, `ui/src/hooks/useOpenFlightSocket.ts`, and
  `ui/src/components/camera/CameraFeed.tsx`.
- **R12:** `src/openflight/camera/capture_runtime.py::_save_capture` and
  `src/openflight/server.py::_load_camera_capture_archive`.
- **R13:** `src/openflight/session_logger.py::log_camera_capture`.
- **R14:** `src/openflight/camera/club_delivery.py`.
- **R15:** `pyproject.toml` (`tool.hatch.build.targets.wheel`).
- **R16:** `.pre-commit-config.yaml`.
- **R17:** `research/club_pose/sim/budget.py`.
- **R18:** `research/silhouette_poc/eval/RESULTS_0C_RADAR.md`.

External primary/authoritative sources checked 2026-08-22:

- **W1:** [Linux upstream OV9282 driver](https://github.com/torvalds/linux/blob/master/drivers/media/i2c/ov9282.c).
- **W2:** [OmniVision OV9281 datasheet mirror](https://www.v-visiontech.com/web/userfiles/download/OV9281_CSP5_DS_1.01.pdf).
- **W3:** [OmniVision OV9281 product page](https://www.ovt.com/products/ov9281/).
- **W4:** [TI, Understanding Range and Angular Resolution in mmWave Radar Devices](https://www.ti.com/document-viewer/lit/html/SWRA841).
- **W5:** [TI mmWave SDK user guide: range-bias calibration](https://dr-download.ti.com/software-development/software-development-kit-sdk/MD-PIrUeCYr3X/02.01.00.04/mmwave_sdk_user_guide.pdf).
- **W6:** [TI antenna calibration application note](https://www.ti.com/lit/an/spracx7/spracx7.pdf).
- **W7:** [US10471328B2](https://patents.google.com/patent/US10471328B2/en).
- **W8:** [US10989791B2](https://patents.google.com/patent/US10989791B2/en).
- **W9:** [TrackMan: Two Radars. One Camera. Zero Doubt.](https://www.trackman.com/blog/two-radars-one-camera-zero-doubt) and [TrackMan patent notice](https://www.trackman.com/legal/patents).
- **W10:** [GolfDB repository and license statement](https://github.com/wmcnally/golfdb).
- **W11:** [Roboflow Golf Club Segmentation Batch 10](https://universe.roboflow.com/fp-cdzly/golf-club-segmentation-batch-10).
- **W12:** [Roboflow Golf Club Head Object Detection](https://universe.roboflow.com/public-bezoe/golf-club---head-object-detection).
- **W13:** [Roboflow Golf Club Tracking v2](https://universe.roboflow.com/club-head-tracking/golf-club-tracking/dataset/2).
- **W14:** [AnyLabeling](https://github.com/vietanhdev/anylabeling).
- **W15:** [SAM 2](https://github.com/facebookresearch/sam2).
- **W16:** [CVAT Community](https://github.com/cvat-ai/cvat).
- **W17:** [GrabCAD model use and sharing](https://help.grabcad.com/article/246-how-can-models-be-used-and-shared).
- **W18:** [CGTrader Royalty Free License](https://help.cgtrader.com/hc/en-us/articles/360015124437-Royalty-Free-License).
- **W19:** [Rerun licensing](https://rerun.io/docs/reference/about).
- **W20:** [BlenderProc](https://github.com/DLR-RM/BlenderProc).
- **W21:** [OpenShotGolf license](https://raw.githubusercontent.com/jamespilgrim/OpenShotGolf/main/LICENSE).
- **W22:** [US10393870B2](https://patents.google.com/patent/US10393870B2/en).
