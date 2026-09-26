# Radar-camera fusion master plan

Updated: 2026-09-25. Owner: Harjot. Status: active; M0 complete, M1 in progress.

## Current Pi commissioning checkpoint

- [x] Pre-Pi software closure is complete: replay, commissioning, held-out
      criteria/template checks, UUID guards, session-wide candidate bridge and
      TB04 sensitivity diagnostics are implemented.
- [x] Linux frozen snapshot: 2266 passed, 7 skipped (absent historical
      capture/log fixtures), 9 warnings; native WSL Ubuntu 24.04, `lgpio`
      omitted, with no Pi hardware claimed.
- [x] Windows relevant suite: 1497 passed, 9 skipped (4 symlink and 5 missing
      fixture cases); browser: 42 passed; UI lint/build and scoped Ruff passed;
      current scoped Pylint minimum is 9.72.
- [x] First Pi ladder run persisted nine Arm 5 captures with camera frames, IWR
      dumps and a radar session log. This is acquisition evidence only; the
      session has not been reviewed for timing or accuracy.
- [x] The recovery baseline is published through `ba082d8` on
      `feat/tester-capture-pilot`; the old uncommitted ZIP overlay is no longer
      the installation path.
- [x] M1 operator-flow simplification makes the guided ladder the normal
      acquisition path and keeps manual single-arm work in a closed advanced
      section. The focused tester browser suite passed 31 checks.
- [x] Fusion diagnostics and saved-track review now bound stalled header and
      body reads. Their complete focused browser suites passed 23 checks,
      including the new timeout recovery cases.
- [ ] Full-progress paired validation still rereads growing JSONL evidence per
      capture. Reduced polling cadence limits frequency but does not remove the
      growing read cost.
- [x] Current local validation: 149 focused backend tests passed and 4 directory-
      symlink cases were skipped on Windows; 239 UI unit tests, lint and build
      passed; full-source Pylint exited successfully at 9.66/10 with existing
      warnings. Tester browser checks passed 31 plus one focused packaging case;
      diagnostic/review browser checks passed 23. These are not Pi hardware,
      acquisition-load or accuracy validation.
- [x] Session review workflow (`agent/review-workflow`): one background
      analyse-review-package job, immutable hash-inventoried session bundle,
      per-attempt statuses with rejection evidence and experimental spin,
      browser import with hash checks, and `--verify` reproduction. Windows:
      affected backend suites pass; full suite 2340 passed with the same 28
      Windows-only failures as `f7c67ac`; tester/review/diagnostics browser
      suites pass. Not yet run on the Pi or on session 1's raw captures.
- [x] The first guided automatic-range attempt preserved an empty IWR result
      with `stage=connect` and `no IWR6843 CLI found`; the earlier tester
      preflight had never contacted the IWR. The normal tester now requires a
      read-only single-port CLI preflight, accepts an optional stable
      Enhanced/UARTA `if00` path for preflight and static captures, and exposes
      the preserved stage/type/message/remedy. Reboot recovery now retains the
      reconfirm/start-over error and persists a non-retryable stale epoch. This
      correction also requires the post-dump `Done` and active CLI health before
      marking static evidence usable, while preserving raw bytes and avoiding
      further commands when recovery is uncertain. An unusable capture revokes
      the server's CLI preflight until the hardware check passes again; Retry
      itself remains state-only. It is software tested only; the board
      connection and static capture still need a Pi run.
- [x] Live Pi use exposed that guided Arm 5/6 camera capture started the server
      LiveView without showing its frames in the normal automatic-range card.
      The card now polls that existing owner through the read-only status/frame
      endpoints, shows warmup, raw frames with the existing detector ring and
      explicit detector verdicts, shows camera errors, and stops polling
      outside camera-capture phases. Server-side guided ownership releases the
      camera on evaluation, failure, start-over and general stop without
      stopping an unrelated manual live view. This is browser/backend tested;
      it does not validate Pi frame timing, camera quality or range accuracy.
- [x] M1 shot-output latency now preserves the provisional OPS event while one
      bounded camera match/archive load overlaps optional IWR processing. The
      final log separates monotonic camera stages and a versioned host-
      monotonic shot contract now distinguishes OPS idle-inclusive wait to the
      first valid dump marker, UART response transport, FFT analysis, IWR
      capture wait/UART transport/estimator analysis and server emit invocation.
      Physical trigger-edge timing and an independent OPS acquisition-window
      boundary are unavailable; browser receive and paint are also explicitly
      unavailable. Legacy timing numbers remain for compatibility and document
      their actual aggregate or ambiguous provenance. Deadline fallback records
      that work continues and records a discard only if the late result actually
      returns. Reversed IWR monotonic boundaries now remain signed until the
      contract withholds them, and OPS-only publication timing is persisted in
      a pinned post-emit `shot_publication` record without adding another shot.
      Synthetic fake-clock, concurrency, failure, deterministic-output and
      lifecycle checks passed; Pi throughput, physical-edge timing, memory,
      hardware UART timing and browser latency remain unvalidated.
- [x] An opt-in offline IQ8 qualification tool now rejects unproven pairings,
      distinguishes same-cube, same-event derivation and independently
      reference-matched swings, and records deterministic input/provenance
      hashes, payload/UART reduction and estimator parity aggregates. It does
      not change the IQ16 production default or establish Pi throughput,
      hardware reliability, reference accuracy or production eligibility.
- [ ] M1 physical gate remains open. M3 selection and M4 pose/strike/spin are
      evidence-dependent. Preserve the first session and finish its failure and
      discrepancy review before changing estimators.

The older unresolved-failure notes below are historical and superseded where
they describe the now-fixed simulator retry and Linux cancellation failures.

## Objective and working agreement

Deliver reproducible, reference-validated ball launch and club delivery on the
measured v3 rig, then establish which pose, impact and spin metrics the sensors
can support. Similarity to Trackman's broad sensor-fusion approach is not a
claim of equivalent accuracy.

This is the execution plan for fusion and the testing suite. Work in milestone
order, in small reviewable changes. Each change must name its milestone,
evidence and validation. Update the checkpoint below at handoff. Do not start
an unrelated estimator, merge a research branch wholesale, or tune around a
failed prerequisite.

Change direction when evidence supports it. Before implementing a deviation,
record the current approach, alternative, evidence, expected benefit, cost,
affected acceptance gates and decision in the decision log. A demonstrated
physical/model error or independently validated improvement is sufficient;
novelty, prettier frames and agreement between dependent estimates are not.
Keep the previous method available for comparison until the replacement passes
its gate. User changes to priorities supersede this plan.

The [test-bench research and coverage matrix](fusion-test-bench-research.md)
maps architecture requirements to automated, hardware and reference-data
evidence (TB01-TB16). Cite these IDs in subsequent work and update their status.
The fork may contain the complete diagnostic bench; code-line count is not a
product constraint. Keep individual changes focused for correctness and review.

### Qualification-suite scope

The suite is the end-to-end qualification harness for OpenFlight's measurement
stack. It covers setup and admission evidence; trigger and acquisition behavior;
raw-evidence preservation and timing; processing and sensor fusion; metric
availability, provenance and discrepancies; acquisition fault recovery; replay
and reference benchmarking; and reproducible contribution packages. Its job is
to establish whether one recorded physical attempt can be traced from admitted
hardware through raw observations to reviewable metrics and a repeatable
comparison result, including partial captures and failures.

This scope does not replace the separate test programs for simulator connectors,
generic application UI outside the tester workflows, cloud services and
accounts, installation and update behavior, the quality or validity of operator
consent, or the representativeness of a collected dataset. Those systems may
reuse exported evidence, but passing this suite does not qualify them, prove
that consent was informed, or establish that contributors and operating
conditions represent the intended user population.

## Starting point

- Implementation base: `feat/tester-capture-pilot`, initially `a6d7c8b`.
- Physical baseline: `config/enclosure_v3_rig_geometry.json`, measured on
  Harjot's Pi 5 rig on September 22-23. Preserve its offsets and provenance.
- Measured mounts do not establish optical intrinsics, radar electronic
  calibration, target-line alignment or timing. The file explicitly marks
  focal length, principal point and distortion as uncalibrated. Adjustable
  feet can change the recorded camera height.
- August 25 captures describe a different, unmeasured rig. Use them for
  regression and diagnostics, never as validation of v3 absolute accuracy.
- The tester branch supplies acquisition and export. The integration/research
  branches contain useful calibration, timing, head-track, pose and impact
  experiments; some assumptions and findings have been retracted. Preserve
  uncommitted work in those checkouts and review individual components before
  reuse. The ball-range branch contains a separate resting-ball improvement;
  the club-comparison branch contains reference-comparison extensions.

## Architecture decisions

1. Retain OPS/IWR ball measurements as the benchmark baseline. Develop camera
   bearings plus radar range/radial-velocity trajectory estimation alongside it.
2. Use a common coordinate convention, explicit sensor origins and clock
   mappings. Record what is measured, assumed and missing. Calibrate optics
   independently of a ball range that the same optics are intended to verify.
3. Reuse one processing implementation for live operation and offline replay.
   Start with short per-shot robust fits; introduce more complex estimators
   only when a measured failure justifies them.
4. Treat pre-impact club motion and post-impact ball motion separately. State
   the physical point and time represented by every metric. A radar scattering
   center, image centroid and face center are not automatically the same point.
5. Keep translation, orientation, impact location and spin as distinct inference
   problems. D-plane inversion is model-derived. Clubhead translation does not
   uniquely determine its rotation axis. Launch direction minus club path is
   not a general spin-axis measurement.
6. Preserve raw observations and report provenance, availability and error for
   each metric. A numerical output or a heuristic confidence score is not proof
   of measurement. Estimated simulator fallbacks must remain identifiable.

## Metrics and acceptance policy

The first release gate covers ball speed, vertical/horizontal launch, club
speed, path and attack angle. Pose, impact and spin are separate gates; they
must not silently become prerequisites for shipping validated motion metrics.

Before collecting the acceptance dataset, freeze numerical limits for bias,
MAE, 90th-percentile absolute error, gross-error rate and successful-read rate,
plus operating conditions and sample size. Define a gross error explicitly.
Select limits from the intended coaching/simulation use and reference
uncertainty; do not derive them from whatever the current implementation achieves.
Numerical product limits are still open, not implied by historical results.

Score all attempts and preserve no-reads. Report reference-missing and ambiguous
matches separately. Pair by physical shot identity with auditable manual review;
do not discard bad agreement to improve scores. Hold out complete sessions,
including another day and eventually another rig. Report uncertainty on both
error and availability; five successful screening swings are not a reliability
claim. Match reference point/time/sign conventions and identify reference fields
that are themselves estimated. See [Trackman's metric definitions](https://www.trackman.com/blog/club-data-definitions).

## Milestones and exit gates

### M0 - Establish a reproducible geometry baseline

- [x] Identify the measured v3 file and distinguish it from legacy captures.
- [x] Audit current consumers and document unused/assumed geometry below.
- [x] Record the complete loaded rig plus a content fingerprint in session
      metadata; prove it survives source-file changes and export.
- [x] Preserve the existing no-rig behavior and legacy capture readability.

Exit: an exported new session identifies and reconstructs the geometry loaded
for that run without depending on the original file remaining on disk. This
gate is about reproducibility, not calibrated optical or physical accuracy.

### M1 - Make acquisition, geometry and timing contracts trustworthy

Geometry work:

- [x] Carry measured forward separation through startup, session configuration,
  club/ball projection and ball-size consistency checks. Independent projection
  and startup tests cover positive, negative and legacy-zero offsets.
- [x] Share the current reference-ball projection model and recorded effective
  scalar inputs between ball and club reconstruction; validate replay loading
  without reading the current rig file.
- [x] Implement calibrated live/replay camera transforms, explicit placement
      rotations/origins and strict mode/pose evidence.
- [ ] Independently measure and qualify optical-center/radar-origin and target
      placement on the physical rig; RF model validation is a separate gate.
- Define optical-center versus lens-front and radar-origin conventions; retain
  uncertainty for physically unmeasured offsets instead of inventing precision.
- Integrate independent intrinsic calibration with correct crop/readout mapping.
  A cropped field of view and reduced pixel sampling require different mappings.
- Preserve per-session ball position/height, unit orientation and target line as
  separate setup quantities; a single ball does not identify every setup degree
  of freedom. Record which values override CLI defaults and why.

Timing and acquisition work:

- Preserve sensor and host timestamps, exposure and trigger observations. Verify
  optical timing and clock relationships with an independent LED/GATE/contact
  bench. Do not infer contact solely from a clock already used by both estimates.
- Compare host timing with verified optical timing in replay before promotion.
  The [Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
  describes SensorTimestamp at first-pixel readout; verify the actual mode/driver.
- Fix tester stop/resume/mode-transition lifecycle defects with regression tests:
  stopping must stop the ladder worker; resuming must reapply/verify controls;
  the final full-resolution photo must remain associated with its capture.
- Validate radar readiness and actual applied mode/exposure, not command intent.
- Require tester setup eligibility before acquisition: approved measured-v3
  configuration, current stable LIS3DH observations, operator physical setup
  confirmation and runtime checks in the process that owns the sensors. Save
  trigger-time eligibility with captures, and exclude setup failures from both
  accepted data and exposure-failure decisions. Preserve rejected evidence.
- Preserve surviving partial captures and an independent physical-attempt tally,
  including swings that never generated a sensor event. Record exact runtime
  software identity, including uncommitted changes, alongside calibration.
- Add independent before/after calibration checks and fault-injection cases;
  use the coverage matrix to track evidence rather than only test counts.

Live diagnostics (approved extension):

- Show current fusion outputs after each shot, with pending and terminal states,
  source/validation labels, rejection reasons and finalized discrepancies.
  Reuse the live pipeline and shot identities; do not duplicate sensor ownership.
- Bind updates to session, shot and result revision. Test late results, timeouts,
  browser reconnects, mode transitions and stale values on all supported kiosks.
- Provide a mode that hides metrics until a validation block ends. Measure the
  diagnostic workload's effect on acquisition before enabling heavy candidates.

Exit: known-target projection tests, explicit fallback behavior, independent
calibration residuals and hardware timing evidence within the allocated metric
error budget. Hardware evidence must identify rig, mode, conditions and date.
Mock tests cannot satisfy this hardware gate.

### M2 - Build the common replay and accuracy benchmark

- Reuse the existing exporters and reference comparator. Add dependable shot
  matching and configuration grouping; preserve raw failed attempts.
- Define metric/provenance records and candidate versions. Run production and
  candidate estimators on identical data with identical setup inputs.
- Compare only compatible units, axes, physical points, time conventions and
  radial/total speed definitions. Separate disagreement from reference error;
  disclose shared inputs. Report bias/tail error and read rate with uncertainty.
- Test live/replay equivalence and useful invariants (for example, coordinate
  transforms or consistent timestamp-origin changes) with explicit tolerances.
- Test sensitivity to timing, geometry, lens calibration and association errors.
  Compare host/sensor timing, pre-impact/cross-impact windows, image features/
  head centroid, and radar-depth/no-depth candidates as controlled ablations.
- Manually annotate a small representative set to establish whether accurately
  located image features can recover the reference motion. If they can, improve
  tracking; if they cannot, investigate calibration, modeling or observability.
- Correct the OPS radial-versus-total-speed model and add independent synthetic
  projections. Never score agreement with a speed imposed as a solver constraint.

Exit: reproducible per-shot results, matched references, coverage/error reports,
frozen acceptance limits and a held-out dataset definition. Regression data and
acceptance data are explicitly separated.

### M3 - Select camera mode, exposure and motion estimator

- Screen full-resolution/120 fps, reduced-resolution/288 fps and the existing
  fast mode as a control. Verify actual readout/crop/FPS metadata. Investigate a
  native-resolution high-speed crop only as a bounded hardware feasibility test.
- Compare a few viable exposures rather than treating shortest exposure as the
  objective. Whole-scene brightness alone does not prove club-feature usability.
- Express tracking windows in time and spatial gates in calibrated units. Audit
  hard-coded frame counts, ball sizes and pixel thresholds for mode dependence.
- Randomize/alternate blocks and control lighting. Retain all attempts. Use
  small screens to eliminate failures, then collect fixed-count paired reference
  sessions for finalists. Do not mistake golfer variation for instrument error.

Exit: choose the simplest candidate meeting the predeclared accuracy and
availability limits on held-out motion data. Record why other candidates lost.
Do not select settings solely by image appearance or accepted-shot accuracy.

### M4 - Resolve pose, strike and spin feasibility

- Pose: test known club orientations and independent reference delivery. Compare
  direct features with model-derived D-plane output under distinct provenance.
- Strike: use calibrated physical strike labels, a defined face origin and
  rectified/scale-calibrated photographs. Same-image hand annotations establish
  consistency, not independent millimeter accuracy. Isolate spray-induced ball
  interaction changes when evaluating spin.
- Spin: compare existing radar estimators on held-out reference shots; report
  harmonic errors and coverage. Test optical spin feasibility separately. A spin
  frequency does not by itself measure a 3D axis.
- If calibrated best-case observations cannot distinguish the required poses,
  strikes or spin, stop tuning that solver and compare added illumination,
  visible markers or another viewpoint. Record cost and operational consequences.

Exit: each metric passes its independent acceptance gate, remains explicitly
model-derived/experimental, or has a documented hardware decision. No obligation
to emit every metric from the existing sensors.

### M5 - Validate and release

Freeze candidates and validate across new sessions, relevant clubs/speeds,
off-center strikes, lighting and setup variation. Include another rig before
claiming transferability. Check processing latency and dropped captures under
the complete live workload. Promote only metrics that pass their gates and
retain replay regressions and a rollback path. Report the validated operating
envelope and unresolved limitations.

## Community evidence access roadmap

The current local contribution package is the reproducibility boundary; no
hosted intake, upload, publication-review service, public catalog or contributor
download portal exists yet. Build those capabilities as separate, auditable
stages rather than treating package creation as publication:

1. **Private immutable intake.** Accept a consent-bound source package into
   private storage, verify its manifest and content hashes, assign a stable
   submission ID and preserve the original bytes without mutation. Record the
   applicable license, declared permissions, protocol/configuration/software
   identities and receipt time. Return a contributor receipt containing the
   stable ID, hashes and accepted visibility state.
2. **Publication review.** Review consent scope, licenses, redaction, capture
   integrity, protocol compatibility and metric provenance. Keep raw camera and
   radar evidence private by default. Publication eligibility, model-training
   eligibility and retention/deletion state remain separate decisions, with an
   auditable disposition for rejected or withdrawn submissions.
3. **Versioned derived releases.** Publish only approved derived records and
   explicitly approved raw evidence. Each release receives an immutable version,
   stable sample/session IDs, source and artifact hashes, schema/protocol/config
   versions, licenses and reproducible manifests. Corrections create a new
   release and retain the relationship to superseded records.
4. **Public catalog and contributor access.** Expose release metadata, coverage,
   known limitations and downloadable artifacts through a versioned catalog.
   Contributors can use their receipts to inspect the recorded disposition and
   download the artifacts they are authorized to access. Catalog entries must
   not imply accuracy, consent quality or population representativeness merely
   because a package passed structural validation.
5. **Frozen evaluation partitions.** Assign complete physical sessions, rigs
   and collection days to train, validation or held-out test partitions before
   candidate selection. Preserve the partition assignment in manifests and
   releases, prevent one physical attempt or dependent derivative from crossing
   partitions, and restrict held-out labels/results during development. Version
   any later partition change and explain why it was necessary.

The roadmap requires access control, durable object storage, receipt and
withdrawal semantics, publication review tooling, catalog/version APIs and
operational security work outside this measurement-suite implementation. Until
those exist and are independently tested, contribution packages remain local
artifacts for deliberate private transfer and review, not evidence that an
upload or public-data service is available.

## Remaining delivery work from the current checkpoint

M0 and the pre-Pi M1/M2 software closure are complete. M1's hardware exit gate
remains open; M3 through M5 have not passed their evidence gates. There are no
known pre-Pi software blockers for the supported shared sound-trigger path. The
OPS-only native hardware mode is implemented, but synchronized soundless fusion
remains blocked on a qualified auxiliary trigger edge. Test counts measure
regression coverage, not percentage of product completion. A reliable calendar
estimate needs actual calibration, timing and reference-data results, since
those can expose modeling or sensor limitations.

The first release covers the six motion metrics listed in the acceptance policy.
Keep the following work packages visible until their outcomes are demonstrated:

| Package | Remaining implementation | Required evidence |
|---|---|---|
| Saved-capture review | Implemented frame annotation, comparison UI, bound downloads and visible failures; representative use remains to be evaluated | Synthetic UI/API checks, then representative annotated recordings |
| Live diagnostic workflow | Implemented existing fusion snapshots, pending/final states, identity/revisions, compatible recorded discrepancies and hidden-metric blocks | Software reconnect/late-result/timeout checks passed; paired hardware acquisition-load measurements remain |
| Tester setup eligibility | Implemented approved-profile admission, physical confirmation, required sensor/placement checks and capture-bound paired evidence | Software rejection/recovery checks; actual Pi disconnect, restart and workload tests remain |
| Calibrated geometry and timing | Opt-in calibrated live/replay camera projection, explicit placement rotations/origins and strict mode/pose evidence implemented; offline timing bench and bounded sensitivity replay available | Independent optics/origin/target calibration, verified clock mappings and physical validation remain; IWR RF model still uses its existing scalar pitch correction |
| Common processing and accuracy benchmark | Production OPS/IWR raw replay, recomputed camera inputs, session-wide normalized candidate bridge, multisession scoring, frozen criteria and reviewed physical-attempt reconciliation implemented | Independent speed/reference evidence, numerical limits and held-out recordings remain; deterministic replay does not establish asynchronous acquisition or physical accuracy |
| Candidate selection | Mode/exposure/window comparisons and justified tracking improvements based on annotated observations | Paired reference recordings, retained no-reads, read-rate and error uncertainty |
| Release integration | Freeze qualified candidates, expose only qualified metrics, retain rollback and replay regressions | New-session accuracy, operating-envelope and full-workload latency/drop tests |

Pose, strike and spin remain a separate feasibility and acceptance workstream.
They do not block release of independently validated motion metrics. Completing
software does not close a hardware gate; a metric that the sensors cannot
support must remain experimental or require a documented hardware change.

Prioritize the next package that removes a demonstrated obstacle to collecting
or evaluating evidence. Avoid adding optional bench features while the next
decision is waiting on recordings or calibration measurements.

## Geometry consumer audit (M0)

| Consumer | Current behavior | Required follow-up |
|---|---|---|
| `RigGeometry.enclosure_setup` | Derives heights, lateral/forward separation and IWR pitch | General orientation is not propagated |
| `server.init_rig_geometry` / session config | Records complete loaded snapshot, fingerprint, path and derived subset (M0 complete) | Consume the same contract in future replay work |
| `CameraDeliveryGeometry` / `_pixels_to_world` | Shared reference-ball baseline or opt-in calibrated Brown model with frozen placement and observed inclination; club output order retained | Physical optics/origin/placement qualification |
| Camera ball-flight geometry | Same shared calibrated or legacy projection as club delivery; rejected calibration leaves an explicitly labeled legacy candidate | Measured target-line orientation and held-out accuracy |
| Tester setup | Uses nominal mode focal scale and rig/inclinometer pitch; floor-ball height differs from live tee height; expected size uses an approximate distance | Distinguish capture-only defaults from accuracy-qualified setup and consolidate once independent optics/placement are available |
| Recorded camera geometry / replay constructor | Restores per-shot calibrated model, mode/pose evidence, tracker state and radar inputs; raw replay can replace upstream evidence with reprocessed OPS/IWR results | Physical calibration and asynchronous acquisition validation |
| Research head-track/pose | Separate geometry and contact-time assumptions | Explicit adapter and evidence before reuse in the live system |

## Recorded geometry contract (M0)

New `session_start.config.rig_geometry.snapshot` records `parameters` (all
loaded `RigGeometry` fields, including defaults and provenance) and `sha256`.
The fingerprint covers UTF-8 encoded JSON with sorted keys, compact separators,
ASCII escaping and no non-finite numbers. It identifies the loaded parameter
record, not the source file's formatting or a hardware calibration certificate.

Export reconstructs `rig_geometry.json` from that session snapshot and records
`enclosure.rig_geometry_source = session_snapshot` plus
`enclosure.rig_geometry_sha256` in the manifest. Validation compares the
exported geometry and manifest with the session snapshot and its fingerprint.
Session metadata also retains the existing effective camera/radar configuration;
the snapshot does not imply every stored parameter was used by every estimator.

Legacy sessions still export. A file found at the recorded path is labeled
`unverified_file_at_export`; absent geometry is `unavailable`. Neither label
claims to reconstruct the rig at capture time. Existing exports without the
new fields remain readable. The additions preserve export contract version 1.

## Effective camera geometry contract (M1)

`session_start.config.effective_camera_geometry` records the inputs consumed by
the ball and club camera estimators, separately from the complete rig file.
Its versioned parameters and fingerprint preserve heights, radar-to-ball slant
range, camera origin offsets, image dimensions, mirror sign, roll correction,
regulation-ball diameter and the ball-only horizontal output adjustment.
Missing setup produces an explicit unavailable record.

The legacy model still infers focal scale and pitch from the resting ball. It assumes
an image-center principal point, omits lens distortion and assumes the ball is
directly downrange of the radar. Internal world coordinates are lateral and
forward from the radar horizontally, with height above the floor; the club
adapter retains its existing lateral/up/forward output order. Lens-front versus
optical-center separation remains physically unresolved. No nominal rig optics
or general mount rotation is silently promoted to calibrated geometry. The
opt-in [calibrated live path](../camera/live-calibrated-fusion.md) instead freezes
an independent optical artifact and explicit placement contract per shot. Its
dynamic transform requires stable upright gravity evidence; deviations above
two degrees remain flagged experimental observations. This does not qualify
the RF estimator's separate geometry model.

Replay uses `EffectiveCameraGeometryInputs.from_recorded_session(config)` and
its `delivery_geometry()` or `ball_geometry()` constructor. A present invalid
snapshot fails validation rather than falling back to current files or legacy
fields. Sessions without the new block use their recorded camera/IWR fields,
with the existing zero-offset, unmirrored and zero-roll defaults. Archive image
dimensions must match the geometry; no crop or resize transform is inferred.
The runtime crop control still records its changes separately in camera config
events; this startup scalar snapshot does not reconstruct a per-shot crop or
readout mapping. This is geometry-input reproducibility, not a complete
live/replay equivalence claim or independent calibration.

## Decision log

| Date | Decision and evidence | Consequence |
|---|---|---|
| 2026-09-23 | User accepted calibrated radar-camera fusion and requested one evidence-driven master plan | Adopt staged gates and this change-control rule |
| 2026-09-23 | User confirmed physical rig geometry is now solid; v3 file contains measured mounts but pending optics | Preserve measurements; prioritize consumption, optical calibration and timing |
| 2026-09-23 | Session metadata only preserves a path and derived subset | Begin M0 with a complete loaded-geometry snapshot; do not claim this fixes fusion accuracy |
| 2026-09-23 | User confirmed the geometry readback, including IWR 30 mm behind the camera | Apply +0.030 m camera-forward offset; retain zero for legacy configurations |
| 2026-09-23 | User approved live fusion outputs and finalized discrepancy displays on the fork | Extend M1 diagnostics and M2 comparisons; preserve independent accuracy gates |
| 2026-09-23 | Test-bench research and code inspection identified missed-attempt counting, omitted partial evidence and incomplete software identity | Add TB01-TB16 coverage, partial-capture retention, independent tally and runtime provenance within existing milestones |
| 2026-09-23 | The full-resolution boundary advances the current rung before its last impact photo can be taken | Persist a pending capture/rung identity and require photo or recorded skip before restarting the camera mode; preserve the choice across Stop/Resume and reload |
| 2026-09-23 | Sensor logs cannot establish how many physical swings were missed, and the preflight commit omits dirty source | Add an independent operator ledger with auditable corrections and a session-bound source snapshot; keep count agreement separate from shot matching and label disk-source coverage explicitly |
| 2026-09-23 | Ball and club range-assisted reconstruction duplicate focal/pitch inference and pixel projection, while live geometry construction is duplicated separately | Consolidate the current reference-ball model and effective input mapping, with replay from recorded inputs and explicit assumptions; preserve legacy valid results and defer independent optics, full placement rotations and the OPS speed model to their stated gates |
| 2026-09-23 | Capture sidecars omit actual driver crop offset/native sampling and sample resolved controls before applying requested ScalerCrop | Build an offline checkerboard candidate bench with explicit profile identity, disjoint fit/validation views and retained failures; keep mapping unverified and live promotion disabled until actual capture identity is recorded |
| 2026-09-24 | Frame callbacks discard reported crop, while asynchronous saves consult mutable runtime settings; driver offset is read separately by the UI | Freeze startup configuration and driver readback with captured frames, retain request-level crop/duration and observed saved-image transforms, and preserve unknown mapping rather than infer calibration compatibility |
| 2026-09-24 | Candidate intrinsics describe saved pixels, while live geometry still infers scale/pitch and capture evidence cannot qualify optical identity or native mapping | Add an offline Brown-model projection and comparison tool with exact declared-profile matching, explicit physical optical-to-world rotation, saved-orientation handling and capture rejection reasons; retain unverified binding and leave live selection gated |
| 2026-09-24 | Ball/club estimator summaries retain aggregate results but not the selected pixel observations; raw archives retain frame timing | Add a hash-bound observation-track replay contract for explicit annotations/tracker exports; compare identical points/times/ranges under a declared calibration hypothesis, preserve failures, and label feature-interval motion separately from live estimator results or accuracy |
| 2026-09-24 | Offline comparisons require hand-authored pixel manifests, slowing the manual-observation check; the tester already resolves saved run scopes | Add a saved-capture review page with frame annotation and the existing comparison core behind scoped read-only capture APIs; export annotations/reports, reject changed capture hashes and stale UI responses, and keep acquisition ownership and live accuracy gates unchanged |
| 2026-09-24 | Live processing already emits provisional/final shot events and has ordered timeout fallback, while saved session logs contain only final shots | Persist bounded diagnostic snapshots at the existing processing stages, with pinned session/shot identity and revision; present them through a scoped tester reader and hideable live view, preserving terminal reasons and limiting comparisons to compatible recorded quantities without rerunning fusion |
| 2026-09-24 | User prioritizes preventing invalid tester data; existing preflight lists software/camera information but does not require a working LIS3DH or prove the configured rig matches the study | Prioritize a server-enforced setup admission and runtime readiness gate before further placement work: require the approved measured-v3 configuration, explicit physical setup attestation, fresh stable inclination and required sensor readiness; preserve blocked/invalid evidence and keep review/export available. Initially required upright placement within two degrees of expected pitch/roll as an operational restriction; the deviation cutoff is superseded by the next decision. Configuration matching and operator attestation remain distinct from independent physical verification |
| 2026-09-24 | User wants larger placement deviations retained to test whether existing correction works; a two-degree admission cutoff excludes that evidence | Replace the two-degree pitch/roll rejection with a persistent, visible nonblocking deviation flag. Preserve finite/stable sensor, upright rig and required hardware gates. Record measured deviations and threshold with each capture; acceptance for acquisition does not establish successful tilt correction. Prioritize physical validation and shared geometry/timing over optional bench features |

| 2026-09-24 | User requests completing software before morning Pi commissioning; audit found automatic canonical OPS correction using fallback launch angles and scalar IWR geometry despite unresolved OPS sampling semantics | Complete independently testable M1/M2 software now while leaving hardware gates open. Retain measured OPS radial speed and expose correction as an experimental candidate with explicit input assumptions and evidence-contract regression checks; share the camera processing core between live and replay. Do not promote a total-speed correction without timing and reference evidence. Add auditable reference scoring and timing analysis where current evidence supports implementation |
| 2026-09-24 | Community/tester data sharing needs reproducible local artifacts without weakening the FlightWeb raw-data boundary | Add consent-bound, hash-inventoried local contribution packages with private-review default and restrictive public-derived output. Keep upload, publication review, consent quality, and physical accuracy outside the package builder. |
| 2026-09-24 | Comparison with `feat/live-camera-shot-analysis` found useful impact-departure anchoring and launch-path cleanup, while the tester branch already has the stronger calibrated geometry, range and replay contracts | Add impact-aware anchoring as independent evidence beside the existing scene detector, retain both observations and reasons, withhold unresolved disagreement, share one resolved anchor across ball and club stages, and clean each launch path before calibrated reconstruction. Keep the existing metric architecture and require common-capture reference evidence before any accuracy claim |
| 2026-09-24 | Final software audit found normalized raw-replay candidates used reviewed metric names that the benchmark adapter did not accept end to end | Add explicit aliases for all six reviewed motion contracts and verify a normalized raw candidate through the benchmark CLI; preserve radial/total distinctions and conditional club-delivery semantics |
| 2026-09-24 | OmniPreSense documents OPS243-A rolling-buffer speed triggering with `STnnn` and `SMnnn`, but firmware 1.3.1 has a vendor data-sequence bug; the user plans to remove the acoustic trigger while retaining older-radar support | Add canonical opt-in `--trigger hardware` only for OPS243-A firmware 1.3.2+ in the 1.3 release train while preserving the sound and host-mediated speed modes. Guard `GC`, restore all detector settings, record trigger provenance and reject IWR/camera combinations until a timely shared edge is qualified. Do not treat first UART byte as impact or assume the internal trigger drives an output pin |
| 2026-09-24 | Source and firmware audit found that the default OPS `S#16` split delays its first dump byte until its 68.27 ms post-trigger span has elapsed, while the 72 ms IWR profiles retain only 27-28 ms before the host dump request | Reject OPS UART first-byte fanout as the production IWR trigger. Treat an OPS trigger-output GPIO as an option only after vendor confirmation and measured electrical/timing qualification. If no such output is available, use an independently qualified IWR firmware-local trigger and fan its GPIO edge to OPS `HOST_INT` and the Pi/camera. Preserve the sound trigger as a supported fallback until soundless hardware validation passes |
| 2026-09-24 | The test bench is intended to accelerate community development and its collected evidence must be usable by contributors, not held by one maintainer | Define a consent-based contribution and dataset contract: immutable checksummed source packages remain private by default; contributors choose visibility for raw camera/radar evidence; published releases use stable IDs, protocol/config/source hashes, redacted operator metadata, licenses and reproducible manifests. Keep upload, publication and model-training eligibility as separate states |
| 2026-09-24 | First Pi commissioning persisted nine Arm 5 camera/IWR captures and exposed that read-only ladder polling wrote `ladder.json` for every partial tester ID typed into the UI; the first valid rung then reached its required boundary-photo checkpoint, which looked like a stalled run while later sound events saved rejected raw captures; the earlier system outage had no persistent journal | Preserve the captures as acquisition evidence without claiming accuracy, make unstarted ladder status reads non-persistent, state the boundary-photo pause and no-swing action explicitly, and retain persistent system/application logs for subsequent failure diagnosis |
| 2026-09-24 | The same capture held about 115.2 camera fps with zero reported gaps, while the face preview updated only with the 1.5 s ladder poll and browser polling repeated full progress and attempt-ledger reads; accepted shots also needed roughly 7 s for the IWR dump and roughly 11 s for final fusion | Treat the camera capture rate and operator-visible latency as separate contracts: poll previews independently at reduced resolution, prevent overlapping requests, bound stalled requests, remove duplicate ledger reads, reduce full-progress polling, and package rotating tester-service diagnostics |
| 2026-09-24 | Session 1 review needed five terminal replays; replay cannot run from an extracted archive (absolute capture paths), drops recorded provenance, hides experimental spin, and the package has no manifest or derived results ([audit](../camera/session-review-workflow.md)) | Replace package + terminal replay with one background analyze-review-package job producing an immutable hash-inventoried session bundle with relative paths, per-attempt statuses and a review page that imports the bundle anywhere. Estimator thresholds unchanged; the camera gate question stays blocked on frames |
| 2026-09-24 | M1 recovery and usability review found duplicate normal and advanced acquisition/export paths that can send a real tester into an incomplete single-arm run or package the wrong scope | Make the guided setup, hardware, light, exposure-ladder, review and package sequence the normal workflow; keep manual single-arm controls as a collapsed advanced investigation path. Preserve rig/readiness gates, raw evidence schemas and diagnostic truth, and do not treat this navigation change as hardware or accuracy validation |
| 2026-09-25 | The user rejected routine radar-to-ball measurement, while audit found that the tester still requires tape for manual swings and otherwise silently inherits the server's 1.575 m default; current camera optics and IWR impact timing are not qualified to replace it accurately | Introduce a versioned tee-range evidence contract and an explicit unresolved/raw-only acquisition path. Preserve optional tape as validation truth that is never silently selected, require independent source groups before an automatic solution can be selected, and withhold range-dependent canonical fusion until a qualified solution exists. Add camera and IWR candidates to the test bench before promotion; do not change estimator acceptance gates |
| 2026-09-25 | Static IWR range differencing needs two matched pre-MTI captures, but production shot profiles move their range windows and concurrent setup capture could steal the runtime's single UART | Add a fixed-window diagnostic profile, raw-first setup capture with exact input hashes, and one interprocess lock shared by auto-detection and runtime ownership. Preserve failures as unusable evidence, keep firmware identity declared rather than read-back verified, and do not select or promote an IWR tee-range candidate in this slice |
| 2026-09-25 | Live Pi evidence showed the hardware check reported ready although the guided empty capture immediately preserved `stage=connect`, `RuntimeError`, `no IWR6843 CLI found`; code inspection confirmed preflight checked Git, OS, camera enumeration and throttling but never IWR reachability | Require a read-only IWR single-port CLI probe in the normal tester admission, keep configuration and dump acquisition in the later static-capture step, surface the preserved structured error with an operator remedy, and allow one explicit stable CP2105 Enhanced/UARTA `if00` path while retaining auto-detection. This changes no estimator or promotion gate and makes no hardware-success claim |
| 2026-09-25 | After reboot restored IWR auto-detection, later guided attempts again found no CLI (the Pi evidence does not show which step, if any, caused that; a controlled explicit-port capture completed cleanly); audit found `read_dump` accepted a complete payload after waiting at most one second for trailing `Done`, below the driver's documented multi-second CP2105 stalls, then cleanup sent commands into an uncertain firmware state | Use the full remaining capture timeout for the dump handler to return `Done`, require an active post-dump `stats` response, extend cleanup command windows beyond the known stall interval, preserve complete or partial raw bytes on recovery failure, and close without `sensorStop` when CLI state is uncertain. Keep Retry as a state transition only; require RESET and a passing hardware check before another capture |
| 2026-09-25 | Independent audit of `78d87d6`: auto-detection discarded what each port did, so `no IWR6843 CLI found` could not distinguish silence, an open failure or a still-streaming dump; it also opened the CP2105 Standard interface whenever Enhanced missed, and Linux raises DTR/RTS on every open; a partial dump was labelled `post_dump_cli_health`, and an empty buffer was written as a raw file; the start-over persistence ran outside the flow lock and could return a 500; the legacy camera-evidence renderer and a stale action error could hide the guided failure; the hardware check showed only an exit code; kiosk runs ignored the verified stable port | Report every probe outcome in the error, never open the CP2105 Standard interface and probe Enhanced first, treat an `l3dump` Error without a header as an ordinary responsive-CLI failure, label saved raw bytes complete/incomplete from their own header and attribute truncation to `read_dump`, persist start-over under the lock, let the guided state own its panel, show the check's last log line, and pass `--iwr6843-port` to kiosk runs when configured. No physical root cause is claimed; the discriminating Pi retest is recorded with the audit |
| 2026-09-25 | Live Pi automatic range entered `camera_arm5_capturing` and started LiveView, but the guided UI neither displayed nor polled that server-owned stream; resetting the flow also left the camera open because ownership was not recorded | Show the existing raw LiveView and detector ring/verdict through GET-only status/frame polling inside the guided card, bind polling to tester/epoch/arm, preserve flow-summary errors separately, and track minimal server-side guided ownership so evaluation, failure, start-over and general stop release only the camera they own. Do not start a second stream or alter range estimators and gates |
| 2026-09-25 | Camera floor/size cues are one dependent optical source, moving-IWR impact range can reuse the range it is meant to establish, and arm-local copies can drift after setup | Keep estimator candidates non-selectable and centralize promotion in a versioned policy. Resolve only same-epoch qualified Arm 5 camera plus static empty/present IWR evidence with exact calibration/config hashes and independent inputs; select static IWR without averaging after absolute and normalized agreement gates. Store immutable setup epochs under `calibration/tee-range` and put only epoch ID/digest references in arms and sessions |
| 2026-09-25 | Moving IWR range and camera↔IWR path association are useful offline diagnostics, but impact extrapolation can be circular and the camera association consumes the same IWR ranges | Preserve the full tee-independent moving range series in replay. Build a non-selectable candidate only from explicitly independent qualified impact timing and matching qualified range calibration. Run the camera association only with saved, qualified camera/range/clock/OPS inputs, retain all alternatives and rejections, and prohibit both diagnostics from tee-range promotion or independent-source counting |

| 2026-09-25 | Guided camera acquisition can use the already-computed static IWR range to reduce provisional search cost, but allowing that conditioned result into promotion would make the nominally independent camera agreement circular | Add a versioned, non-promoting IWR-to-camera search hint that narrows only the floor/range and diameter hypotheses; range alone does not constrain azimuth. Label conditioned previews as radar-guided and ineligible for promotion, fall back to the broad detector when the hint is invalid, stale or too broad, and require Save to rerun the unchanged full-frame 0.5-4 m camera estimator on the exact frames before any camera candidate or promotion. Retain the hint, timings, rejections and a post-Save camera-to-IWR ranking diagnostic without changing the static IWR result or `TeeRangeSolution` candidates |
| 2026-09-25 | Provisional OPS output already avoids blocking the UI, but final enrichment serially waited for IWR, then matched and loaded camera evidence; a roughly 768 KiB IWR dump at 1,041,667 baud has a roughly 7.55 s theoretical wire floor, and the watchdog cannot cancel its worker | Keep the provisional OPS event, overlap exactly one camera association/archive load with IWR processing, retain the existing IWR/K-LD7/camera estimator and qualification order, and record monotonic stage durations. Bound the prefetch to one worker and one archive. On a deadline, finalize OPS-only while truthfully recording that work continues and that any late result will be discarded; record the discard only when it occurs. This is software concurrency evidence, not Pi throughput or hardware timing validation |
| 2026-09-25 | Latency review found that `trigger_latency_ms` has parse/re-arm or whole-wait semantics by trigger mode, `pipeline_ms.iwr6843` combines transport and analysis, and `pipeline_ms.initial_ui` stops at the server rather than the browser | Preserve those numeric fields for historical readers, but make a versioned host-monotonic duration contract authoritative. Measure only observable boundaries, split IWR UART and estimator work, identify golfer-idle wait explicitly, and mark physical-edge, independent OPS acquisition-window, browser receive and browser paint timing unavailable until separately instrumented or validated |
| 2026-09-25 | IQ8 can halve complex-sample payload, but differing capture cadence and unmatched swings can make apparent estimator parity meaningless | Add a non-promotional offline comparison contract with hash-verified source, transformation and reference provenance. Require identical observed profile/cadence for same-event encodings; score distinct swings only against their own independent references. Keep IQ16 as production default until Pi workload, retained-failure and frozen reference gates pass |

## Soundless trigger architecture

The current supported path uses one SEN-14262 edge for OPS `HOST_INT` and Pi
BCM17. The Pi edge queues the IWR dump and is forwarded to the camera, giving
all three captures one trigger observation. Removing the microphone must retain
that shared-event property; correlating three independently delayed software
notifications is not an equivalent replacement.

At the production OPS setting, 4,096 samples at 30 ksps span 136.53 ms.
`S#16` divides them into 68.27 ms before and 68.27 ms after the OPS trigger.
The first valid UART dump marker therefore cannot arrive until at least 68.27
ms after that trigger, before UART startup, the driver's 10-20 ms polling and
the subsequent IWR CLI request. Although each supported IWR profile spans 72
ms in total, the wide profile retains 9 x 3 ms = 27 ms before the request and
15 x 3 ms = 45 ms after it; the dense profile retains 14 x 2 ms = 28 ms before
and 22 x 2 ms = 44 ms after it. The camera defaults retain 150 ms before and
50 ms after their received trigger.

Consequently, total IWR duration is not the UART fanout budget. Even compared
only with 72 ms, OPS first-byte delivery has 3.73 ms of theoretical margin; in
the configured capture plans it must place impact inside just 27-28 ms of
pre-request history. If the native OPS speed trigger precedes impact by the
20-40 ms described for the existing speed path, its first marker is still
approximately 28-48 ms after impact before host latency. First-byte timing can
help reconstruct the OPS trigger epoch, but it cannot recover IWR samples that
have already left the ring. It is therefore diagnostic evidence and may support
an experiment, but it is not an accepted production trigger for IWR capture.
It also cannot initiate the current external-trigger OPS dump by itself.

An OPS GPIO asserted synchronously with its internal trigger would be the
simplest soundless source. The repository and current wiring contract document
`HOST_INT` as an input and UART Tx/Rx; they contain no qualified trigger-output
pin, command, voltage or pulse-width contract. Do not design around such an
output until OmniPreSense confirms the capability and Pi measurements establish
its polarity, voltage, pulse width and latency relative to both the OPS trigger
record and physical impact. If qualified, fan that edge to the Pi while using
the OPS internal trigger for its own capture, and verify that the IWR's 44-45 ms
post-request span still contains impact across the full trigger-lead and host
latency distribution.

If OPS has no qualified output, the controlled long-term architecture is an
IWR firmware-local trigger. It must detect and latch the event within the
continuous firmware capture, change the existing capture plan to its post-event
phase locally, and emit one documented GPIO edge to OPS `HOST_INT` and Pi
BCM17. Local latching protects the shortest prehistory from Linux and UART
latency; the shared edge preserves the existing OPS and camera fanout. The
sound-trigger path remains supported as the fallback until this architecture
passes its hardware gates.

The implementation boundary is deliberately narrow:

- Add an opt-in firmware state machine using existing compact HWA frame
  evidence, with explicit arm, one trigger sequence/frame, refractory handling,
  local pre-to-post transition, rearm after dump and a documented GPIO pulse.
  Do not change capture math or claim detector accuracy from synthetic inputs.
- In the host IWR path, retrieve the already latched capture and record the
  firmware trigger identity, Pi edge, OPS trigger observation and derived
  impact time separately. Continue forwarding the Pi edge to the camera.
- Keep OPS in its persisted rolling-buffer configuration and drive its existing
  `HOST_INT`; suppress startup/configuration pulses and arm only after OPS, IWR
  and camera report ready. Preserve the existing sound mode and provenance.
- Cover firmware ring wrap and exact 9/15 and 14/22 frame allocation, one-edge
  behavior, refractory/busy cases and rearm. Cover host source selection,
  timestamp/provenance retention, camera fanout and startup/shutdown ordering
  without treating those tests as hardware qualification.

Pi validation must measure a physical contact reference against the IWR-local
trigger, emitted GPIO edge, OPS trigger/dump marker and camera frames. It must
confirm voltage and pulse width; latency distribution and worst case; retained
pre-impact club and post-impact ball evidence in both IWR profiles and `S#16`;
camera impact placement; startup false edges; missed, duplicate and false
triggers; refractory/rearm behavior; and acquisition under normal processing
load. Soundless commissioning remains blocked until those measurements pass
and the captured trigger provenance agrees with the observed content.

## Current checkpoint

Usability/recovery update, 2026-09-24: present setup, hardware, light,
exposure-ladder capture, review and packaging as one normal operator path.
Keep manual single-arm acquisition and export controls in a collapsed advanced
section for directed investigation. This changes navigation and instructions;
it preserves readiness gates, evidence schemas and the meaning of diagnostics.
Diagnostic, run-discovery, capture-discovery and frame reads now have a
five-second client deadline. Offline saved-track comparison has a bounded
five-minute client deadline because its Pi runtime is not yet characterized;
timing out does not cancel server computation or automatically retry the action.
Do not claim bounded progress-read cost until paired-validation read behavior is
fixed and tested.

Tester-service recovery now leaves a job running until callback output is
durable, turns setup and callback failures into visible error states, appends
repeated action logs, and lets the global Stop action close a standalone live
preview. These changes improve recovery evidence and controls; they do not
establish hardware readiness or loss-free acquisition.

Software-closure decision, 2026-09-24: the user requests continuing until only
Pi validation remains. Audit and close the existing M1/M2 workflows end to end:
capture, replay, normalized candidate scoring, reviewed acceptance, diagnostics
and installation. Implement missing adapters or reproducibility checks using
the existing estimators; do not invent calibration evidence, acceptance limits
or a new RF model. Record a concrete pre-Pi completion checklist and leave
physical experiments and evidence-dependent candidate selection explicit.

Trigger-architecture update, 2026-09-24: the new OPS243-A native speed trigger
reopens a bounded software package before final soundless validation. Implement
the vendor-documented internal trigger as opt-in with strict capability checks
and preserve existing trigger modes. The OPS dump, IWR ring and camera clip must
retain distinct trigger observations and content-derived impact estimates. Do
not accept soundless commissioning until the auxiliary-capture trigger path and
its latency have been measured on hardware.

OPS-only implementation checkpoint, 2026-09-24: canonical `--trigger hardware`
now enforces OPS243-A firmware 1.3.2+ within the 1.3 release train, 30 ksps,
bounded `ST`/`SM`/`S#` inputs, guarded `GC` configuration, complete detector
setting restoration, capture validation and recovery-aware re-arm. Startup
rejects IWR6843 or camera capture with this mode because the OPS has no
qualified timely fanout edge. The sound and host-mediated speed paths remain
unchanged. Automated software checks do not close the Raspberry Pi or soundless
fusion hardware gates. The earlier no-known-pre-Pi-blockers statement applies
only to the supported shared sound-trigger path.

Implementation decision, 2026-09-24: the user explicitly requests implementing
the remaining software now. Extend M1/M2 with opt-in, recorded calibrated
projection and placement transforms in the shared live/replay path, replay of
raw radar evidence through existing processors, an explicit OPS line-of-sight
speed candidate, and machine-readable acceptance/workload evaluation. Reuse
existing estimators and keep canonical outputs and unavailable-input behavior
explicit. Software can implement these paths before field testing; it cannot
manufacture optical calibration, verified clock semantics, reference shots or
product acceptance limits. Promotion remains gated on that independent evidence.

- Completed: M0 session snapshot, fingerprint validation and export regression
  coverage. All changes are local to the tester-pilot checkout.
- M1 first implementation: forward camera/radar separation now reaches both
  live estimators and the logged effective camera configuration. Radar tee range
  and camera-to-ball forward distance remain distinct. A CLI fallback supports
  rigs without a geometry file; measured rig values take precedence.
- M1 capture-integrity implementation (TB06/TB12): exports retain surviving
  files from excluded shots with a separate manifest inventory. Missing or
  malformed camera metadata no longer aborts later shots. Complete-pair and
  availability accounting remain separate from diagnostic file retention.
- M1 lifecycle implementation (TB12): Stop cancels the ladder worker and pending
  mode restarts; resumed active rungs reapply saved controls before polling.
  Each mode start reserves its run directory, including stops before the kiosk
  writes files. Actual exposure/gain remains checked in each capture's existing
  verdict; applying controls is not proof of sensor readback or radar readiness.
- M1 final-photo handoff (TB12/TB16): the outgoing full-resolution mode holds
  its final capture for a photo or a recorded skip, including early mode exits.
  The exact capture/rung identity survives Stop/Resume and reload; stale actions
  are rejected. Ordinary photos still work across an exposure-step change.
  The tester page shows the pending choice, retries failures, and prevents
  duplicate actions. Software checks do not validate physical photo quality.
- M1 independent observations (TB05/TB13/TB16): the tester can record swings,
  reported misses, warmups and false triggers against an explicit saved run.
  Retries preserve request identity; corrections retain their audit history.
  Manual UI entries do not infer exposure-rung identity from an automatically
  advancing ladder. The run stays pinned until deliberately changed.
  Exports carry the ledger, with observed counts separate from logged sensor
  attempts. Neither ledger presence nor count agreement proves complete physical
  coverage, shot matching or accuracy; physical availability remains unknown.
- M1 software evidence (TB07): sessions preserve an allowlisted disk-source
  archive, content hashes, Git identity and installed runtime versions. Export
  uses the recorded archive, including dirty source, rather than the checkout at
  analysis time. This does not prove which bytes were already imported or record
  device firmware, all configuration, or later changes. Missing legacy evidence
  and failed collection remain explicitly unavailable.
- M1 common geometry foundation (TB01/TB07): ball and club range-assisted
  reconstruction share focal/pitch inference, pixel normalization and ray/sphere
  projection. Effective scalar inputs have a versioned session-start snapshot
  and a validated replay constructor. Impossible placements, incompatible image
  dimensions and ambiguous range intersections are withheld. Independent
  optics, crop mapping and general placement rotations remain open.
- M1 optical bench (TB02/TB03/TB07): an offline checkerboard tool fits camera
  intrinsics/distortion on declared fit groups and evaluates frozen parameters
  on disjoint validation groups. Reports retain hashes, corners, detection
  failures and pixel residuals. Profile identities are declared; mode binding
  remains unverified and no candidate changes live fusion or the rig defaults.
  Held-out board poses are fitted from the images, not independently measured.
  See the [collection and CLI guide](../camera/optical-calibration.md).
- M1 capture-mode evidence (TB02/TB07): saved frames retain their startup
  context, reported per-frame crop/duration and observed dimensions. Contexts
  preserve requested settings, configured streams/sensor and camera properties,
  plus nullable startup driver-parameter readback. Mixed/missing contexts remain
  explicit; asynchronous saves cannot substitute later settings. Complete and
  partial exports preserve each sidecar. Driver readback is not proof of applied
  native crop, and the checked-in 320x200 driver reports a crop different from
  its programmed native window. Calibration binding remains unverified.
- M1 calibrated projection bench (TB01/TB02/TB07): an offline adapter loads the
  exact declared calibration profile, undistorts saved pixels, accounts for
  saved orientation and applies an explicit physical optical-to-world rotation.
  The comparison CLI reports angular and optional range-assisted position
  disagreement against the current model, with input hashes. Capture inspection
  checks recorded contexts and frame alignment; matching available fields still
  remains unverified. No live estimator selects these candidates.
  See the [projection comparison guide](../camera/calibrated-projection.md).
- M1 recorded-track bench (TB07/TB10/TB11): explicit ball-center and club-feature
  annotations or tracker exports bind to exact capture and metadata hashes.
  Both projection models use identical pixels, integer frame times and supplied
  ranges. Reports preserve missing/invalid observations, withhold intervals across
  gaps, and label feature motion separately from impact/launch metrics. Known
  capture conflicts block numerics; matching evidence remains a conditional
  hypothesis. Live estimators do not yet preserve their selected pixel tracks,
  so this is not live/replay equivalence or an accuracy qualification.
  See the [recorded-track guide](../camera/recorded-track-comparison.md).
- M1 saved-capture review (TB07/TB10/TB16): a separate tester page selects an
  existing run/capture, annotates independent physical-point tracks and calls
  the shared offline comparison core. Scoped APIs read saved files only and
  reject changed hashes; browser drafts bind to both source files. Annotation
  and report downloads retain missing points and conditional/withheld results.
  Drafts are local to the browser and must be downloaded separately from the
  capture package. This is separate from the live result display, and its workload
  has not been qualified during real acquisition.
- M1 live diagnostic workflow (TB08/TB09/TB16): the existing shot pipeline now
  records UUID-bound pending/terminal snapshots with explicit fallback reasons.
  A bounded reader and tester page expose provenance, compatible recorded
  horizontal discrepancies and persistent hidden-metric blocks. Queue/deadline
  fallbacks preserve partial OPS results; logging failure does not interrupt
  canonical shot output. Completed processing is not an accuracy qualification.
  See the [diagnostics guide](../camera/live-fusion-diagnostics.md).
- Next implementation: finish the common placement/rotation and per-session
  setup contract, preserving recorded hypotheses and legacy behavior. Use the
  saved-capture workflow on a representative set and collect independent
  optical/placement and timing evidence before promoting calibrated profiles.
  Measure diagnostic acquisition overhead on the Pi. Keep provisional/live and
  finalized results distinct through the coverage matrix.
  The OPS radial-versus-total-speed model remains a
  separate M2 task; the camera/OPS fallback test still uses its existing
  total-speed assumption and does not validate real Doppler semantics.
- M1 tester eligibility (TB03/TB06/TB08/TB12/TB16): acquisition now requires the
  approved measured-v3 fingerprint and process-local operator confirmation.
  Admission and kiosk runtime enforce stable upright LIS3DH observations with
  a nonblocking flag above two degrees of placement deviation, while the kiosk
  verifies observed camera and radar transport state. Runtime readiness is bound to the exact log run and
  session. Each camera trigger freezes its eligibility evidence; paired
  acceptance waits for matching camera, saved IWR dump and terminal shot logs.
  Failed setup evidence stays available and does not count toward exposure
  failure or accepted data. Neither configuration matching nor attestation
  independently verifies the physical unit or qualifies fusion accuracy.
  See the [setup gate guide](../camera/tester-setup-gate.md).
- Hardware evidence still needed: v3 optical calibration, radar calibration
  verification, independent timing bench and matched reference sessions.
- No milestone involving hardware accuracy is complete. Do not apply the v3
  geometry retroactively to the August archive.
- Validation: 402 tests passed, one existing session-log replay test skipped
  because its fixture log is absent. Suites covered rig geometry, server wiring,
  export, capture provenance, session logging, inclinometer, study mode, scoring
  and tester service. Initial five new regression cases failed before the fix.
  Ruff lint/format checks passed; scoped Pylint met the 9.0 threshold at 9.71/10
  with reported diagnostics. No real hardware validation was performed.
- M1 validation: 135 focused tests passed, including 30 cases that failed before
  the implementation. Broader compatibility checks: 496 passed, one skipped
  for the absent historical session log. Ruff lint passed; scoped Pylint met
  the threshold at 9.23/10 with reported diagnostics. Formatting passed for
  the changed files except two pre-existing formatting differences in club
  delivery, verified identical to HEAD and preserved to avoid unrelated edits.
  Synthetic projection and mocked startup checks are not hardware validation.
- The test-bench report links primary guidance from NIST, Kalibr, ROS and NASA.
  Its coverage matrix distinguishes implemented software checks from pending
  diagnostic features and hardware evidence. Partial-capture replay tooling,
  independent attempt coverage verification and shot matching remain open.
- Latest validation (TB06/TB12): 539 passed, one historical-log replay test
  skipped because `session_20260402_121507_range.jsonl` is absent. Fourteen
  new regression cases reproduced the export and stop/resume defects before
  their fixes; additional checks cover successful and failed mode transitions.
  Ruff lint/format passed for this increment, and scoped Pylint met the 9.0
  threshold at 9.70/10 with reported diagnostics. These are synthetic captures
  and mocked kiosk/process tests; no hardware or end-to-end UI validation was
  performed. Changes remain local and uncommitted.
- Final-photo validation (TB12/TB16): Codex 5.6 agents implemented the backend
  and tester page, with independent review. Focused backend checks passed
  130 tests; the broader compatibility run passed 552 with the same one
  missing-log skip. Seven Playwright checks passed, including touch layouts
  at 800x400, 800x480 and 1024x600, delayed requests, retry and custom tester-ID
  restoration after reload. Ruff and ESLint passed; scoped Pylint scored
  9.69/10. Photo/state writes use same-directory temporary files and atomic
  replacement, with failure/reload and Stop-during-write coverage. Packaging
  waits for active photo work and excludes concurrent ladder restart; three
  focused package tests passed after the final runner-identity recheck. All
  captures, processes and browser API responses in these checks were simulated;
  real-camera timing, photo quality and fusion accuracy remain unvalidated.
- Attempt/provenance validation (TB05/TB07/TB13/TB16): Codex 5.6 agents
  implemented the changes with independent review. Compatibility checks passed
  448 tests; ledger, exporter, scoring and tester checks passed 163. Two tests
  were skipped across those runs: the absent historical session log and a
  directory-symlink check unavailable on this Windows environment. Final focused
  regressions passed for mixed ledger coverage, repeated shot events and a ledger
  without its final newline. Five additional exporter integration checks passed
  for frozen source, missing/tampered evidence and altered derived counts.
  Fourteen Playwright checks passed, covering final-photo behavior and operator
  tally retry/reload/run identity at all three kiosk sizes. Ruff and ESLint
  passed; scoped Pylint scored 9.73/10. These are synthetic/mocked checks, not
  hardware accuracy validation. Changes remain local and uncommitted.
- Shared-geometry validation (TB01/TB07): Codex 5.6 agents implemented and
  independently reviewed the common projection and effective input contract.
  The combined regression run passed 655 tests with the same two historical-log
  and Windows-symlink skips. Checks include physical projection fixtures,
  roll/mirror/origin equivalence, impossible/ambiguous intersections, snapshot
  replay and tamper rejection, archive dimension mismatch, and retained radar
  fallback. Ruff lint passed; formatting passed for this increment except the
  same two pre-existing club-delivery layout differences. Scoped Pylint passed
  the threshold at 9.20/10 with diagnostics. No browser behavior changed in this
  increment. All evidence is synthetic/mocked; no real-hardware calibration,
  timing or accuracy gate was completed.
- Optical-bench validation (TB02/TB03/TB07): Codex 5.6 agents implemented the
  core and CLI, followed by review and failure-path hardening. The combined
  calibration/geometry/replay/export run passed 152 tests. Its 22 optical cases
  include known-parameter recovery, a real detector-to-report run on 13 rendered
  checkerboards, split/group isolation, duplicate pixels across encodings,
  retained solver failures and protected input files. Ruff lint/format and
  scoped Pylint passed the required threshold. This is synthetic software
  evidence; no real camera calibration or fusion accuracy was qualified.
  Changes remain local and uncommitted.
- Capture-mode validation (TB02/TB07): Codex 5.6 implementation and independent
  review were followed by a combined run of 504 passing tests, with two existing
  missing-log/Windows-symlink skips. Coverage includes callback-to-save metadata,
  missing/malformed readback, saved orientation, changing request crop, startup
  ordering/restart, mixed contexts, complete/partial export, replay, calibration,
  session logging and tester/server behavior. Ruff lint/format and diff checks
  passed; scoped Pylint scored 9.54/10 with diagnostics. Camera requests and
  startup were mocked; no hardware accuracy or capture-throughput claim is made.
  Changes remain local and uncommitted.
- Calibrated-projection validation (TB01/TB02/TB07): Codex 5.6 agents implemented
  the offline adapter and CLI, followed by integration review. All 113 relevant
  projection, optical, geometry, capture and provenance tests passed, including
  28 adapter/CLI cases. Checks cover distorted rays with all saved orientations,
  proper physical rotations, offset range intersections, exact profile loading,
  numerical rejection, runtime context hashes/alignment, true checkerboard fit
  through serialized artifact to comparison, and preserved sidecar frame counts.
  Installed OpenCV 5 uses explicit convergence criteria through its merged point
  undistortion API; tests also exercise API selection. Ruff lint/format and diff
  checks passed; scoped Pylint scored 10.00/10. No live estimator was changed,
  and no real hardware calibration or accuracy gate was completed. Changes
  remain local and uncommitted.
- Recorded-track validation (TB07/TB10/TB11): Codex 5.6 agents implemented the
  offline core and CLI, followed by integration review and failure-path fixes.
  All 158 relevant projection, optical, geometry, capture and provenance tests
  passed, including 45 new track/CLI cases. Checks cover known forward/diagonal
  motion, timestamp-origin invariance, undefined directions, missing measurements,
  frame gaps, invalid range intersections, incompatible dimensions/metadata,
  exact input hashes, corrupt archives and protected input files. Ruff lint and
  format checks passed; scoped Pylint scored 9.87/10 with complexity diagnostics.
  CLI help and diff checks passed. These are synthetic software checks; no real
  recording, hardware accuracy, timing or live/replay equivalence was validated.
  Changes remain local and uncommitted.
- Saved-review validation (TB07/TB10/TB16): Codex 5.6 agents implemented the
  read-only capture APIs and annotation/comparison page, followed by integration
  review. The final tester, ladder, ledger, export and track-comparison run
  passed 243 tests with two unavailable Windows directory-symlink checks
  skipped. All 24 relevant Playwright checks passed across the review, attempt
  and photo-handoff pages, including all three kiosk sizes, drag/tap behavior,
  draft identity, stale responses and exact nanosecond downloads. Ruff checks,
  UI lint/build and diff checks passed; scoped backend Pylint scored 9.81/10
  with diagnostics. A separate browser-to-actual-Flask smoke check used synthetic
  files to annotate two points, compute one interval and download a report
  preserving integer timestamps; 800x400 and 1024x600 views were inspected.
  This is software evidence only: no hardware accuracy, acquisition-load or
  physical annotation quality gate was completed. Changes remain local.
- Live-diagnostics validation (TB08/TB09/TB16): Codex 5.6 agents implemented
  backend and UI, followed by integration review. The server/logger/tester/fusion
  regression run passed 316 tests with two existing missing-fixture/Windows
  symlink skips; final focused diagnostic checks passed 19 tests, including
  real finalization, logger failure and terminal exception paths. The separate
  ladder/ledger/export/review run passed 112 tests with one Windows symlink skip.
  All 34 relevant Playwright checks passed, covering run/session/revision races,
  reconnects, hidden-block persistence and all three kiosk sizes. Ruff checks,
  UI lint/build and diff checks passed; scoped Pylint scored 9.55/10. A real
  Flask/browser/logger smoke check with a synthetic shot verified pending to
  terminal updates, hide/end/reveal and reload; the 800x400 view was inspected.
  These checks do not establish hardware accuracy or acquisition overhead.
  Changes remain local and uncommitted.
- Setup-gate validation (TB03/TB06/TB08/TB12/TB16): Codex 5.6 agents implemented
  admission, runtime checks, paired evidence and UI, followed by integration
  review. The tester/ladder/policy regression passed 159 tests with three
  platform/symlink skips; the broader runtime regression passed 329 with one
  absent historical-log fixture skip. Final focused setup, runtime, paired and
  ladder-integration checks passed 35 tests with two unavailable Windows
  symlink checks skipped. Export/review/diagnostic compatibility passed 91
  tests with one Windows symlink skip. The 21 tester Playwright checks passed,
  followed by seven focused checks after final UI corrections. Ruff and UI
  lint/build passed; scoped Pylint met the threshold (backend 9.70, runtime
  9.65, paired helper 9.42). Actual Flask/browser checks with simulated sensor
  observations verified confirmation, direct POST rejection, tester switching
  and placement loss; a real-policy handoff check verified exact runtime-run
  binding and LIS ownership restoration. Packages retained setup evidence.
  No physical rig, disconnect, calibration or acquisition-load gate was
  completed. Changes remain local and uncommitted.
- Placement-warning revision (M1, TB03/TB06/TB08): Codex 5.6 agents replaced
  the two-degree admission/runtime cutoff with a visible nonblocking warning.
  Measured pitch/roll, signed deviations and threshold remain in admission and
  frozen trigger evidence; invalid, unstable and non-upright readings still
  block. Exactly two degrees passes without warning. Final focused validation
  passed 64 setup/camera-buffer tests, including both signed angle boundaries,
  a confirmed tilted swing launch with saved admission warnings, and deep-copy
  retention of trigger warnings. Earlier revision checks passed 28 setup/runtime,
  95 tester/integration (one skip), and 182 server tests (one skip); skips were
  the existing Windows symlink and missing historical fixture checks. Eight
  focused Playwright tests, Ruff, UI lint/build passed; scoped Pylint scored
  9.73/10. Final UI copy clarifies that tilting can change actual lens height.
  These are simulated software checks, not hardware correction validation.
  Next priority is Pi commissioning and independent geometry/timing evidence,
  followed by the common accuracy benchmark. Changes remain local.
- Pre-Pi software batch (M1/M2, TB04/TB10/TB11/TB13): Codex 5.6 agents
  implemented a shared camera-stage live/replay core, explicit canonical OPS
  radial-speed contract, reviewed-match accuracy benchmark and offline timing
  bench. Independent review found and resolved session-rollover persistence,
  archive-validation, typed comparison and protected-output failures. Context
  includes both tracker histories and exact capture/session identity. Missing
  context remains unavailable; raw OPS/IWR extraction is not replayed by this
  camera-stage tool. Live total-speed correction remains withheld because the
  available geometry proxy does not establish measured OPS-relative inputs.
- Final combined regression for this batch passed 969 tests, with five skips:
  four unavailable Windows symlink checks and the missing historical session
  fixture. All 42 relevant Playwright checks passed, along with UI lint/build,
  CLI help and diff checks. Scoped Ruff passed; scoped Pylint scores were 9.66
  for fusion/replay, 9.52 for accuracy and 9.85 for timing. A test-order leak in
  simulated camera setup was fixed before the final combined run.
- The full pre-batch suite was not clean on this host: 2110 passed, 11 skipped
  and 28 failed. Twenty-seven failures involved Windows/POSIX permissions,
  symlink or Bash/WSL execution; one existing simulator reconnect/backoff timing
  test also failed independently and remains unresolved. This is not a claim
  that the complete suite passes. No physical Pi checks were performed.
- The earlier morning handoff included current local source, built UI and a hash
  manifest for installation over a fresh clone at the recorded base revision.
  Follow [Pi commissioning](../camera/pi-commissioning.md), then preserve all
  attempts and diagnostics. The implementation checkpoint below supersedes its
  software status. Neither batch closes a hardware or accuracy exit gate;
  changes remain uncommitted.

- Remaining-software implementation (M1/M2, TB01/TB02/TB05/TB07/TB09/TB10/TB11/TB13):
  Codex 5.6 agents integrated calibrated optics and full camera placement in the
  shared live/replay core, including mode compatibility, stable upright pose,
  reference inclination, target alignment, pivot and explicit origins. A
  positive signed-trajectory test exercises actual initialization, live capture
  metadata, radar range evidence and saved-context replay. Rejection preserves
  an explicitly labeled legacy fallback; deviation above two degrees is still
  permitted and flagged. The separate IWR RF model remains scalar-pitch based.
- Raw replay now uses production OPS processing and the same hardware-free IWR
  estimator as live acquisition. Captures freeze effective configuration,
  startup calibration/CFG bytes and per-shot recovery/tilt/speed inputs.
  Recomputed camera processing receives the new radar results, including
  missing range; recorded-context replay remains a separate comparison. The
  LOS speed candidate binds one FFT window to declared independent direction,
  measured origins and an explicit common-clock association. It remains
  unvalidated and never overwrites canonical OPS radial speed.
- The benchmark accepts multiple session exports, stable software-content
  grouping, reviewed six-motion-metric contracts, frozen limits and a separate
  hash-bound held-out selection. Session-bootstrap error uncertainty and Wilson
  availability bounds retain their assumptions. Reviewed ledger reconciliation
  measures physical availability separately from sensor-record coverage. The
  commissioning report exposes saved failures, artifact presence, frame gaps
  and recorded latency without claiming verified clock semantics or load limits.
- Next evidence: commission the Pi, measure optics/origins/alignment and timing,
  freeze intended accuracy limits, collect reference-matched held-out sessions,
  and evaluate candidates. These results may justify further modeling changes;
  software regression success is not a Trackman-equivalent accuracy claim.
- Frozen final validation passed the full Linux suite: 2,266 tests passed, seven
  unavailable historical-capture fixtures skipped, and nine warnings under WSL
  Ubuntu 24.04 on a native filesystem. `lgpio` was omitted because no hardware
  was used. Windows relevant checks passed 1,497 tests with nine skips (four
  symlink checks and five unavailable local fixtures). All 42 relevant
  Playwright checks, UI lint/build, and scoped Ruff passed; minimum scoped
  Pylint was 9.72/10. Revision 4 of the local Pi handoff replaces the earlier
  packages. No Pi hardware or accuracy test was performed.
- Detector refinement (M1/M2, TB01/TB04/TB10/TB11/TB12): the shared camera
  path now compares the existing scene ball with an impact-departure candidate,
  records both candidates, rejection reasons, pixel/size disagreement and
  selection provenance, and withholds unresolved disagreement. A stable session
  anchor can resolve occlusion or disagreement; the selected anchor is shared
  with club delivery. The existing diagnostic view exposes their bounded pixel
  distance and agreement state after processing. Every launch-path candidate
  receives robust outlier removal and single-frame-gap interpolation before
  metric reconstruction.
  Focused detector/delivery/projection validation passed 161 tests; replay,
  calibrated, server and diagnostic compatibility passed 255 tests with one
  existing skip. Ruff and formatting passed; scoped Pylint scored 9.10/10.
- Raw benchmark closure (M2, TB10/TB11/TB13): the accuracy benchmark accepts
  the normalized raw-replay names for total ball speed, vertical and horizontal
  launch, club speed, club path and attack angle under their reviewed contracts.
  An end-to-end CLI regression confirms all six comparisons remain compatible;
  the benchmark suite passed 20 tests and Ruff passed. No acceptance limit or
  reference result was inferred.
- Automatic tee-range foundation (M1/M2, TB01/TB07/TB10): approved for the next
  software slice. The tester will preserve optional tape as validation truth,
  record versioned camera/IWR candidates by independent source group, and use
  an explicit unresolved state when no qualified solution exists. Unresolved
  sessions may collect immutable raw evidence but must not inherit the legacy
  1.575 m default or publish range-dependent canonical fusion. This checkpoint
  does not promote the nominal camera focal length, prove IWR impact timing, or
  establish automatic-range accuracy; those remain physical validation gates.
- Camera tee-range integration (M1/M2, TB01/TB07/TB10): the tester now evaluates
  the stationary ball in the active saved-image mode using measured rig origins,
  lens/ball heights, current LIS3DH pitch/roll and a compatible calibrated model
  when configured. It persists all camera candidates, ambiguity, rejection,
  disagreement and uncertainty in placement, arm and per-run tee-range evidence.
  Camera candidates remain non-selectable and the solution remains unresolved;
  optional tape is validation truth only. Software checks do not qualify optics,
  automatic range, or range-dependent fusion.
- Static IWR capture foundation (M1, TB06/TB07/TB09/TB12): the driver now owns
  its single serial device through an interprocess lock shared with auto-detect,
  and an offline setup command records fixed-window empty/ball raw dumps before
  deriving profiles. Results bind the exact declared firmware, config, rig and
  calibration bytes and retain invalid dumps as unusable evidence. This is
  software validation only; the profile, hardware behavior and range candidate
  remain unqualified and are not wired into the tester or fusion selection.
- Tee-range promotion boundary (M1/M2, TB01/TB07/TB10): immutable setup epochs,
  digest-checked current/arm/session references and a single qualification-gated
  resolver are implemented. The resolver requires exact rig, Arm 5 calibration,
  IWR firmware/config/profile/range-calibration and scope identities; accepted
  independent camera/static-IWR evidence from one epoch; uncertainty and plausible
  interval limits; and both absolute and normalized residual limits. Manual and
  moving-IWR evidence cannot support promotion. Static IWR remains the selected
  value and camera remains an agreement check. Focused contract and regression
  validation passed 229 tests with one existing skip on Windows. No setup is
  accuracy-qualified by this software change and no Pi hardware was exercised.
- Guided tee-range integration (M1/M2, TB01/TB07/TB10): the main tester now
  persists IWR and both camera steps as a restart-safe epoch, centrally
  revalidates its digest-bound solution and freezes one command for both modes.
  Missing qualification or disagreement stays raw-only; tape remains Advanced
  validation. Software tests do not establish Pi operation or range accuracy.
- Moving-range replay diagnostics (M1/M2, TB07/TB10/TB12): raw replay now
  preserves the tee-independent IWR fit as a full fitted range/time series and
  records a structured withheld result unless impact timing proves independence
  from IWR range and the matching range-bias calibration is qualified. The
  camera↔IWR anchor runs only with saved qualified camera, clock, OPS and IWR
  inputs, retaining alternatives, rejection reasons and prerequisites in the
  session review. Both paths are explicitly non-promoting and the camera result
  is marked dependent on the same IWR series. Trigger/capture containment,
  two-frame timed-support and extrapolation limits, and image-bound impact
  checks prevent a selected anchor from relying on unbounded back-projection.
  Synthetic validation does not qualify the Pi optics, sensor clocks, radar
  calibration or automatic range.
- Tee-range integrity hardening (M1/M2, TB01/TB07/TB10): candidate evidence is
  now deeply immutable and serialization returns detached JSON. Resolved epoch
  loads reconstruct promotion from non-selectable evidence under the embedded
  qualification and require exact canonical equality, so persisted promotion,
  residual and policy claims cannot bypass the resolver. Epoch references hash
  the exact bytes written. Static IWR capture reserves an ID before hardware
  ownership, publishes raw/result files without replacement and binds results
  to the raw digest; solution writes use unique durable temporary files.
  Focused fusion/integrity validation passed 224 tests with one skip. The full
  camera-enabled Windows suite passed 2,506 tests with 12 skips and retained the
  same 28 platform-specific bash, permission and symlink failures as the base.
  Ruff and formatting passed; scoped Pylint scored 9.96/10. No Pi hardware or
  automatic-range accuracy validation was performed.
- Guided tee-range admission hardening (M1/M2, TB01/TB07/TB10): qualification
  schema v2 binds the exact camera placement and stable saved-image mode profile,
  while static IWR promotion requires a finite positive measured bias uncertainty
  from the exact hashed calibration. Every guided API step revalidates the bound
  configuration, operator confirmation and LIS3DH orientation. Finalization now
  publishes the immutable epoch/current pointer before terminal flow state and
  ladder admission requires that terminal reference to equal the current pointer.
  Restart reconciliation waits only for a live bounded static-capture reservation,
  and camera retries retain uniquely versioned frames and attempt records. Missing,
  changed or legacy identities remain raw-only or require an explicit start-over;
  no shipped calibration value or estimator acceptance gate changed. Software
  validation does not establish Pi resource behavior or automatic-range accuracy.
- Guided IWR reachability correction (M1, TB06/TB12): a Pi empty-capture
  failure established that the old hardware check did not probe the IWR6843.
  The tester now separates a required read-only CLI preflight from the later
  static configuration/dump capture, preserves auto-detection, supports one
  stable Enhanced/UARTA `if00` override for both paths, and presents the
  capture's saved stage, exception type, message and remedy. A restart requires
  reconfirmation and persists the old epoch as start-over-only instead of
  restoring its Retry button. Static evidence is now usable only after trailing
  `Done`, active CLI health and verified cleanup; recovery failures retain raw
  evidence and avoid follow-up commands in an uncertain handler state. The
  implementation does not alter tee-range estimation or promotion policy.
  Automated software checks do not establish that the Pi board is powered,
  enumerated or flashed.
- Guided camera association correction (M1/M2, TB01/TB07/TB10): the automatic
  range preview and Save path now share the same independent camera floor-plane
  estimator with one frozen arm, calibrated model, rig identity and LIS3DH pose.
  The live result retains every camera candidate and rejection, draws a ring only
  for a selected candidate on the exact analyzed median, and requires three
  consistent analyses spanning at least one second before Save. Save reruns the
  estimator on the exact recent frames and preserves a withheld attempt if that
  result is no longer safe. Generic study/tape previews retain their existing
  detector. No IWR range, tape value, prior canonical range, promotion rule or
  estimator threshold participates in this camera association. Software tests
  do not establish Pi frame-rate cost or camera-range accuracy.
- Bidirectional acquisition implementation (M1/M2, TB01/TB07/TB10): an
  accepted same-epoch static IWR candidate can now provide a versioned,
  identity-bound range/row/diameter hint to the provisional live camera search;
  radar range never narrows azimuth. Missing, rejected, stale, malformed or too-
  broad hints retain their reason and fall back to the broad full-frame camera
  detector. Conditioned stability can enable Save but is labeled radar-guided,
  non-independent and non-promoting. Save hashes and reruns the exact recent
  frame window through the unconditioned full-frame 0.5-4 m estimator, and
  withholds the camera candidate unless that result confirms the provisional
  object. The independent result may rank the retained static radar hypothesis
  only in a diagnostic record that is excluded from `TeeRangeSolution`.
  Provenance retains source/camera identities, uncertainties, fallback reasons
  and host-duration timings. Focused validation passed 31 range/analyzer tests,
  five guided-flow cases and all 18 tee-range Playwright checks; scoped Ruff
  passed. UI lint/build and scoped Pylint were not completed after an external
  interruption. No Pi throughput, hardware behavior or range accuracy was
  validated.
- Shot-output latency implementation (M1, TB04/TB08/TB09/TB12): provisional
  OPS publication remains immediate. A single bounded camera prefetch now
  matches and loads one archive while the long IWR stage runs, then reuses that
  archive after the unchanged radar and K-LD7 stages. Session evidence records
  the monotonic camera/enrichment stages. A versioned duration contract uses
  `time.monotonic_ns` for host-observable OPS first-marker wait, response
  transport and analysis; IWR capture wait, `read_dump` transport, estimator
  analysis and aggregate processing; and callback-to-server-emit invocation.
  It does not fabricate a physical edge or OPS acquisition-window boundary,
  and marks browser receive/paint unavailable. The retained `latency_ms`,
  `trigger_latency_ms`, `pipeline_ms.initial_ui` and `pipeline_ms.iwr6843`
  numbers are compatibility fields with explicit actual provenance, not the
  authoritative stage contract. Deadline evidence no longer implies
  cancellation: it records continuing work and a separate late-result discard
  if completion occurs. Tests use synthetic waits, clock discontinuities and
  captures; no Raspberry Pi latency, memory, UART-throughput, physical-edge or
  browser-rendering validation was performed.
