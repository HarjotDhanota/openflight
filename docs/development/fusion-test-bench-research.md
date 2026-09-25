# Fusion test-bench research and coverage matrix

Research date: 2026-09-23. Supports the [master plan](fusion-master-plan.md).
This document records recommended requirements, not implemented functionality
or hardware validation. Source guidance is distinguished from its proposed
application to OpenFlight. No new framework or hardware purchase is required
by this research.

## Final pre-Pi software closure (2026-09-24)

TB04 sensitivity replay, TB11 session-wide normalized replay candidates, and
TB13 partial-criteria templates plus UUID guards are implemented. Linux frozen
validation recorded 2266 passed, 7 historical-fixture skips and 9 warnings on
native WSL Ubuntu 24.04 without `lgpio`; Windows recorded 1497 passed, 9
fixture/symlink skips, 42 browser passes, and UI lint/build/scoped Ruff passes.
These are software checks, not Pi or physical calibration evidence. M1 physical
qualification, M3 selection, and M4 pose/strike/spin remain evidence-dependent.

## Findings that change our testing priorities

### 1. Verify calibration independently and check it between sessions

NIST describes stable check standards and tracking their results to detect bias
and changes over time. Kalibr demonstrates live camera-validation overlays and
reprojection statistics. These are useful precedents for our bench, not evidence
that our calibration is already valid.
Sources: [NIST calibration control](https://www.itl.nist.gov/div898/handbook/mpc/section3/mpc35.htm),
[Kalibr validator](https://github.com/ethz-asl/kalibr/wiki/Calibration-validator).

Application: use withheld target poses/distances to validate lens and rig
calibration. Before and after a reference session, record a repeatable target
check at the hitting region. Track changes after focus, mount, enclosure window,
feet, firmware or camera-mode changes. Record both the target's dimensional
uncertainty and placement uncertainty. A printed board with unverified scale is
not a millimeter reference. A static check cannot validate dynamic timing.

OpenCV separates the intrinsic camera matrix from camera pose and lens
distortion. Its calibration guidance scales focal lengths and principal-point
coordinates when an image is resized. That guidance does not establish that
two sensor readout modes share a field of view or a crop mapping.
Sources: [OpenCV calibration tutorial](https://docs.opencv.org/4.13.0/dc/dbb/tutorial_py_calibration.html),
[OpenCV camera model and resolution scaling](https://docs.opencv.org/5.0/main_modules/calib.html).

Application to the current M1 step: share the existing reference-ball model
between ball and club reconstruction, but label its inferred focal length,
inferred pitch and assumed image-center principal point. Preserve actual image
dimensions and reject incompatible capture dimensions. Independent optical
calibration, distortion handling and measured crop/readout mapping remain
separate prerequisites; the v3 nominal focal value does not satisfy them.

The [offline optical bench](../camera/optical-calibration.md) now fits a Brown
five-coefficient candidate from declared fit groups and evaluates frozen
intrinsics on separate validation groups. It retains source/pixel hashes,
detected corners and failures. Held-out poses are estimated from those same
target images, so their residuals do not establish independent physical accuracy.
Capture sidecars now retain startup configuration/driver readback and
frame-aligned crop/duration evidence. Driver readback is a module parameter,
not a measured effective sensor window. Actual calibration binding and hardware
target checks remain open.

### 2. Separate measurement noise from setup and golfer variation

NIST gauge studies distinguish repeatability, reproducibility, stability and
bias, including operator/configuration effects.
Source: [NIST gauge R&R](https://www.itl.nist.gov/div898/handbook/mpc/section4/mpc4.htm).

Application: repeat a fixed target with the rig untouched, then reposition the
rig and repeat, then repeat on another day/operator. These answer different
questions. For motion, use an independently characterized repeatable target
where practical and paired reference shots for actual golf swings. Spread in
twenty different swings is not sensor repeatability. Static fixtures validate
only the quantities they exercise; they do not establish full-swing accuracy.

### 3. Compare compatible quantities and retain their dependencies

NIST uncertainty propagation explicitly includes sensitivity and covariance.
Bland and Altman explain why correlation does not establish agreement between
measurement methods.
Sources: [NIST uncertainty propagation](https://www.nist.gov/pml/nist-technical-note-1297/nist-tn-1297-appendix-law-propagation-uncertainty),
[Bland and Altman, method agreement](https://www.sciencedirect.com/science/article/pii/S0020748909003204).

Application: only subtract estimates with matching units, axes, physical point,
time convention and speed definition. Radial and total speed are not directly
comparable. Distinguish sensor disagreement, fitted-model residual and external
reference error. Display shared radar depth, contact anchor or prior as a
dependency. Small disagreement between dependent estimates cannot establish
absolute accuracy. Do not interpret today's heuristic confidence as a calibrated
probability or use it to manufacture uncertainty bars.

Start with signed error plots against reference magnitude, speed, exposure and
time within session, plus bias, MAE, tail error and read rate. Use agreement plots
and limits only with their distribution/repeated-measurement assumptions checked.
Include reference uncertainty. Estimate calibration/timing sensitivity offline
first; label it modeled sensitivity until independently checked. Avoid a single
universal discrepancy threshold for every metric and operating condition.

### 4. Count missed attempts and preserve partial evidence

Repository finding: `export_session.py` lists incomplete pairs in
`excluded_shots.csv`, but skips copying any surviving per-shot camera/radar files
for those entries. `score_mode_study.py` correctly includes listed exclusions in
its attempt denominator. However, an attempt absent from every logged sensor
event is not counted by that mechanism.

Application: retain partial captures, parse errors and per-stage outcomes in the
export. Add an independent attempt ledger (reference shot sequence, operator
counter or independent witness), including an easy way to record a missed shot.
Distinguish true missed swings, false triggers and warm-ups. Preserve original
observations and annotations; corrections must be auditable.

Report separate denominators: physical attempts, detected shots, raw captures,
metric outputs, matched reference pairs and within-tolerance matched results.
An unmatched output is not known to be accurate. Do not present matched-only
accuracy as all-attempt accuracy. Keep clean benchmark subsets without deleting
the diagnostic evidence that explains exclusions.

### 5. Deliberately exercise failures and asynchronous results

NASA's testing guidance describes fault injection and testing known failure
modes. ROS recording tools explicitly monitor dropped messages.
Sources: [NASA testing analysis](https://swehb.nasa.gov/spaces/SWEHBVD/pages/140641632/Testing%2BAnalysis),
[ROS recording diagnostics](https://github.com/ros2/rosbag2/blob/rolling/README.md?plain=1).

Application: inject dropped/duplicated frames, timestamp jumps, missing radar
dumps, malformed payloads, out-of-order completion, processing timeouts, restarts
and browser reconnects. Define the expected result for each: valid partial output,
explicit rejection or explicit failure. Never allow stale data to become the
next shot's result. Add no-swing/background-motion recordings to measure false
triggers and false metric outputs.

Reuse the existing bounded enrichment queue and shot identity/finalization
mechanisms. A shot is finalized when every enabled stage reaches a terminal
state, including timeout, failure and unavailability; it need not have every
metric. Later reprocessing gets a new result revision. Join by session UUID,
shot ID and revision, with current configuration identity. UI state must survive
reconnect and mode/session changes without mixing runs.

### 6. Test the diagnostic workload itself

Application: compare capture-only operation with live metrics, replay overlays
and candidate processing enabled. Record sensor frame intervals, gaps, radar
capture completeness, queue depth, processing latency percentiles, temperature
and throttling. Use captured sensor timestamps for measurement timing and host
timestamps for transport/processing diagnostics according to their verified
clock conventions.

Hardware ownership stays with the capture service. The dashboard consumes its
results; it must not open a second camera/radar stream. Heavy research candidates
run after capture or offline when measured workload affects acquisition. Reuse
the current Python/Flask processing paths; ROS is a design reference, not a
proposed dependency or rewrite.

### 7. Design experiments around interactions and honest sample sizes

NIST recommends blocking controlled nuisance factors and randomizing others.
Factorial designs test combinations rather than isolating each variable in a
way that hides interactions. Source: [randomized blocks](https://www.itl.nist.gov/div898/handbook/pri/section3/pri332.htm),
[factorial designs](https://www.itl.nist.gov/div898/handbook/pri/section3/pri333.htm).

Application: screen a small mode-by-exposure matrix under defined lighting,
blocking by golfer/club/session. Exposure and lighting interact with tracking
thresholds; changing one at a time is insufficient for final selection. Hardware
modes require separate captures; algorithm candidates can reuse identical shots.
Do not pretend separate swings are paired physical observations of two modes.
Limit the initial matrix, then validate finalists on independent sessions.

Read rates need confidence intervals; NIST describes proportion intervals.
Source: [NIST proportion intervals](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).
For illustration, our direct exact-binomial calculation gives a one-sided 95%
lower success-probability bound of 54.9% after 5/5 successes, and 95.05% after
59/59. The calculation is `0.05 ** (1/n)` for all-success trials. It assumes
independent trials with a common success probability and a predeclared sampling
plan; it is not a blanket recommendation of 59 golf shots or proof of accuracy.
Repeated frames and tightly clustered session conditions do not provide that
many independent generalization trials.

### 8. Reproduce the exact implementation, not just its Git commit

Repository finding: exports obtain a commit identifier from the preflight log.
The working checkout currently contains uncommitted fusion changes, so commit
identity alone does not identify the algorithm that would run.

Application: record code revision, dirty status and a reproducible snapshot or
patch/content hashes for relevant modified and untracked source, dependency lock,
camera driver, firmware, estimator configuration and calibration. A hash alone
does not preserve missing source. Capture this at process start and bind it to
the run, rather than relying only on an earlier preflight. Keep immutable raw
records and versioned derived results. Define numerical tolerances for replay
across platforms; require exact matches for identities and categorical statuses.

## Live bench presentation

The user approved live outputs and completed-shot discrepancies. Build a compact
latest-shot view with a diagnostic detail view and a replay view. Expose current
live estimators first; add research candidates after input compatibility review.

Keep these independent: solver state (pending/accepted/rejected/failed), source
(sensor/fusion/model/fallback), and validation status (experimental or validated
within a stated operating envelope). Rejected candidates belong in diagnostics,
not the selected-result tile. Show raw differences immediately when compatible
results exist, mark partial comparisons, then show a finalized comparison once
enabled stages terminate. Reference error appears only after reference matching.

Provide a validation mode that hides metric feedback until a collection block
ends. Debug sessions can show everything. Log which mode was used. Allow simple
tester notes such as missed shot, wrong club, disturbed rig and unusual strike,
always attached to the correct attempt or shot.

## Architecture coverage matrix

Status updated 2026-09-24. Partial means existing coverage is useful
but does not close the requirement. Planned means no claim of implementation.

| ID | Requirement / failure to detect | Evidence needed | Milestone / current status |
|---|---|---|---|
| TB01 | Correct sensor origins, signs, full placement transform | Independent projection/invariance tests plus known physical targets | M1 software: calibrated live/replay camera rays and explicit origins/pivot/reference inclination/target alignment; signed positive integration and transform tests. Physical placement qualification and separate RF-model validation pending |
| TB02 | Lens/mode calibration generalizes | Withheld target views, edge/center residuals, crop mappings, focus identity | M1 software: calibration reports, live/replay Brown projection and frozen mode compatibility; per-shot mismatch explicitly falls back. Physical optical identity, native mapping verification and held-out accuracy remain pending |
| TB03 | Setup remains stable | Before/after check target and reposition/day/operator comparisons | M1 software requires approved geometry, physical setup attestation and stable upright LIS3DH; deviations above two degrees are flagged without blocking correction experiments. Independent before/after checks and reposition/day/operator hardware evidence remain pending |
| TB04 | Camera, radar and contact share verified timing | Independent optical/contact bench; replay with deliberate timing perturbations | M1/M2 software: clock-correlation bench and bounded frozen sensitivity replay implemented; actual sensor semantics and physical timing remain unverified |
| TB05 | All physical attempts counted | Independent attempt ledger, false-trigger/no-read cases | M1/M2 software: append-only ledger plus explicit hash-bound reviewed one-to-one physical-to-sensor reconciliation, including null/no-read and false-trigger cases; actual completeness and physical matching remain operator evidence |
| TB06 | Incomplete shots remain diagnosable | Export/reimport surviving files and error outcomes | M1/M2 software: surviving files and setup evidence retained; incomplete transport stays explicit; raw replay and commissioning report distinguish missing, rejected and ambiguous stages without replacing their evidence |
| TB07 | Snapshot identifies actual processing | Changed/deleted geometry tests; dirty-source and runtime version provenance | M0 geometry complete; M1 preserves disk source/runtime versions, calibration input hashes, frame-bound mode/crop observations and exact capture/annotation comparison inputs; saved-capture review rejects changed file hashes; native mapping, optical identity, loaded module bytes and firmware remain open |
| TB08 | Terminal results belong to the right shot | Timeout, late/duplicate update, restart and reconnect tests | M1 software implemented: UUID-bound pending/terminal snapshots, pinned logger writes, timeout/finalization failure outcomes and stale-response/reconnect tests; hardware acquisition evidence pending |
| TB09 | Diagnostics do not degrade acquisition | Paired workload bench, dropped-frame/capture and latency statistics | Bounded reader/polling plus offline commissioning report of saved failures, artifact presence, cadence and latency implemented; paired acquisition-load measurements remain an M1/M5 hardware gate |
| TB10 | Comparisons represent the same quantity | Unit/frame/time/speed contracts, dependency lineage, mismatch rejection | M1/M2 software: reviewed six-motion contracts retain feature/interval and radial/total distinctions. Canonical OPS radial speed stays unchanged; an optional single-FFT-window LOS candidate requires independent direction, measured origins and explicit shot/clock association, with no accuracy qualification |
| TB11 | Live and replay share computation | Same raw bundle/configuration gives equivalent values/status; preserve state/order | M2 software: production OPS/shared IWR processing and session-wide normalized candidate bridge preserve no-reads and frozen identities. Asynchronous acquisition equivalence and physical timing remain open |
| TB12 | Failures are handled explicitly | Sensor/data fault injection and negative-control recordings | M1/M2 partial: mandatory tester admission/runtime gate, stale/missing sensor and incorrect placement checks, run/session binding, export failures, stop/resume and final-photo handoff covered in software; physical disconnect and full sensor fault matrix pending |
| TB13 | Claimed accuracy includes availability and uncertainty | Reference matching, bias/tail errors, denominator checks, confidence intervals | M2 software: multisession reviewed matches, frozen criteria/held-out selection, session-bootstrap error intervals, nominal Wilson coverage and optional reviewed physical availability. Actual numerical limits, independent reference qualification and acceptance recordings remain open |
| TB14 | Best mode wins across conditions | Blocked mode/exposure/light screen, fixed holdout and interaction analysis | M3 planned; exposure ladder alone does not close gate |
| TB15 | Pose, strike and spin are separately identifiable | Known pose/strike/spin references and explicit hardware decision gates | M4 planned; motion tests do not satisfy these gates |
| TB16 | UI communicates partial/final/discrepant results correctly | Kiosk-size/touch tests, reconnect, unavailable values, blind-mode checks | M1 software implemented: final-photo handoff, operator tally, saved-track review and live diagnostic page with source/status labels, compatible recorded horizontal discrepancies and persistent hidden blocks; broader metric comparisons depend on M2 contracts |

Every future implementation should cite these IDs and attach its tests or
hardware record. Existing automated test totals must not mark a hardware row
complete. Capture examples used to tune algorithms remain separate from the
release holdout.

## Recommended implementation order

1. Commission the Pi and collect independent optical calibration, measured
   placement/origin/alignment, and sensor-timing evidence for the M1 exit gate.
2. Freeze numerical accuracy and availability limits before collecting
   reference-matched sessions, then retain every physical attempt and no-read.
3. Use held-out, reviewed reference sessions to evaluate the completed M2
   replay and scoring contracts, including their timing and geometry
   sensitivity variants.
4. With the M1 and M2 evidence gates satisfied, run the controlled M3
   comparison with before/after calibration checks. Retain the separate M4
   observability and M5 generalization gates.

This extends test coverage and diagnostic detail without changing the chosen
camera/radar motion architecture or declaring any additional metric validated.
