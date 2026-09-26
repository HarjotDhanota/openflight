# OV9281 camera-mode study protocol

This study determines which OV9281 readout, frame-rate, exposure, and lighting
combination supplies the best evidence for OpenFlight's ball and club
estimators. It is a controlled hardware experiment, not a visual-quality
comparison.

The companion [camera-study webpage](../../ui/public/camera-study.html) is the
operator runbook and guided test runner. On the Raspberry Pi, stop the normal
kiosk and launch it once from the repository root:

```bash
bash scripts/start-camera-study.sh
```

Open `http://127.0.0.1:8765` in the Pi browser. Set the study ID and camera mode,
then use the numbered buttons for preflight, checkerboard generation,
calibration capture and solve, exposure sweep, and swing capture. The page
shows the live process output and artifact counts. **Package entire study**
creates one uncompressed ZIP containing every saved mode under that study ID;
send that ZIP through the agreed external storage channel. The raw data still
stays out of Git. The service exposes only the fixed study actions; it does not
accept commands from the browser.

## Approval boundary

A contributor may collect data and open a draft report pull request. This study
does not authorize changing production camera defaults, driver tables,
enclosure geometry, calibration constants, thresholds, or fusion behavior.
Each production change requires a separate maintainer-approved pull request.

Raw frames, radar dumps, audio, and videos stay outside Git. The draft report
contains manifests, hashes, summary data, small diagnostic artifacts, and a
link to the raw archive.

## Research corrections that define this study

The protocol follows the latest camera and club-measurement research, including
`docs/Personal Research/research-2026-08-28/E_optics_vantage.md`, the updates in
`markerless-club-data-guide-v2-research-corrected.md`, and the D-plane audit.
Where older documents conflict, the later measured correction wins.

The cross-check also covers `camera-hardware-spec-v1.md`,
`camera-club-path-aoa-accuracy-2026-08.md`,
`camera-feasibility-verdict-2026-08.md`, research-2026-08-28 reports A through
F, and the current driver, capture, timing, exposure, calibration, and fusion
implementations. Their requirements map into this protocol as follows:

| Research area | Requirement carried into this study |
| --- | --- |
| Sensor/readout and optics | Verify the resolved readout, compare matched subsampled/native modes, and calibrate each distinct mode at final focus. |
| Exposure and observability | Measure lux, moving-target blur, local contrast, clipping, and localization uncertainty; do not approve settings from global brightness alone. |
| Timing and cadence | First test the existing free-running camera using sensor timestamps, the shared sound edge, and independently detected contact. Add an electrical frame clock only if measured uncertainty moves an endpoint beyond its error budget. |
| Club model and metric physics | Use per-club registration/truth, pre-impact delivery observations, and endpoint-specific sufficiency rather than a generic mesh or one frame-count gate. |
| Radar-camera fusion | Rebuild enclosure geometry/extrinsics and test same-point association before scoring fused outputs. |
| Comparator/patent review | Treat commercial systems as architecture precedents, not validation of OpenFlight accuracy or its selected camera mode. |

1. `320x200`, `640x200`, `640x100`, and `640x400` are approximately 2x
   subsampled. They are commonly called binned modes, but the driver uses
   subsampling/skipping rather than photon-summing binning. They do not provide
   different angular samples per output pixel. Their principal differences are
   crop, field of view, row count, and cadence.
2. `640x400` is therefore not a higher-detail version of `320x200`. It is the
   full-width/full-height subsampled field. A stationary ball should occupy
   approximately the same number of pixels in both modes at fixed geometry.
3. `1280x800` and `1280x720` are the stock 1:1 native-readout candidates. They
   should approximately double angular sampling relative to the 2x-subsampled
   modes, but the smaller native pixel and doubled plate scale make their
   short-exposure light requirement much harder. The research predicts roughly
   15x the current incident light for `1280x800` to hold club blur near 1.5 px;
   that prediction must be measured, not assumed.
4. OMNIVISION specifies `1280x800` at 120 FPS. Earlier OpenFlight documents used
   approximately 140 or 144 FPS without a bench measurement. This study treats
   120 FPS as the initial request and reports delivered sensor cadence.
5. Reducing requested FPS does not reduce motion blur. Exposure time, projected
   image velocity, and plate scale determine blur. Lower FPS only changes the
   temporal sample count unless it enables a different readout.
6. A static mean/clipping exposure screen is necessary but insufficient. The
   selected setting must also pass a moving-club blur measurement and preserve
   local ball/club edges.
7. Output array dimensions do not prove which sensor mode Picamera2 selected.
   Save the reported mode list, libcamera log, resolved camera configuration,
   `ScalerCrop`, and raw stream format.
8. Use the sensor exposure time for optical timing. Picamera2 defines
   `SensorTimestamp` at the camera start-of-frame interrupt, when the first
   pixel is read out, so the approximate exposure midpoint is
   `SensorTimestamp - ExposureTime / 2`. Host callback timestamps describe
   delivery after image extraction. Report sensor cadence and host-minus-sensor
   timing separately.
9. There is no research basis for a universal six-pre-impact-frame gate.
   Required observations depend on the endpoint: temporal tracking, radar-timed
   interpolation, impact detail, and ball flight have different requirements.
10. Fusion geometry must be measured from Cormac's enclosure design and the
    completed as-built unit. Camera-only mode characterization may proceed
    before those extrinsics exist; fusion scoring may not.

## Questions and falsifiable hypotheses

| ID | Question | Falsifying result |
| --- | --- | --- |
| H0 | Did the camera select the requested native readout? | Resolved configuration or crop differs from the arm definition. |
| H1 | Do all subsampled modes retain the same angular sampling? | Ball/checkerboard scale differs beyond calibration uncertainty at fixed geometry. |
| H2 | Does 1:1 readout approximately double ball and club pixel spans? | Calibrated scale does not improve materially, or improvement is software resampling. |
| H3 | Are additional native pixels useful after blur, noise, and clipping? | Edge/localization uncertainty or downstream error does not improve. |
| H4 | How much temporal sampling matters independently of readout? | The same `640x400` readout at 120 and 250 FPS produces no endpoint-relevant difference. |
| H5 | Can the Pi capture and persist every arm without gaps or throttling? | Timestamp gaps, callback errors, buffer failures, or thermal throttling occur. |
| H6 | Does the existing event-aligned timing meet each endpoint's accuracy budget? | Measured camera/radar contact residual or a timing perturbation moves an output beyond its preregistered error limit. |
| H7 | Does a finalist improve frozen full-stack outputs against truth? | Availability, bias, error, or failure rate is worse than the baseline. |

Useful calculations:

```text
expected observations = delivered_fps * observation_window_s
delivered_fps = (frame_count - 1) / (last_sensor_time - first_sensor_time)
blur_px = speed_m_s * exposure_us / 1000 * plate_scale_px_per_mm
maximum_exposure_us = 1000 * target_blur_px / (speed_m_s * plate_scale_px_per_mm)
optical_time = SensorTimestamp - ExposureTime / 2
```

At 40 m/s and a 2 px target, the nominal exposure ceilings are approximately
169 microseconds at `0.295 px/mm` and 85 microseconds at `0.591 px/mm`. Replace
those nominal scales with the calibration result for the actual enclosure and
mode.

## Cormac enclosure design gate

Cormac's design is the enclosure reference for this study. Geometry must come
from the completed as-built unit rather than nominal dimensions alone.

Before full-stack scoring:

- Create a revision-named rig-geometry file for Cormac's design.
- Use the centroid of the four RX RF-group phase centers as the IWR board-frame
  origin. Measure the camera-to-board translation and roll/pitch/yaw from the
  as-built enclosure.
- Record the installed IWR board rotation as 90 degrees counterclockwise when
  viewed from the front/antenna side.
- Treat the board as seven RF groups: three TX and four RX. Each STEP box in
  the supplied model contains two radiators; it is not two independent MIMO
  channels.
- Store the individual TX1-TX3 and RX1-RX4 RF-group phase-center coordinates
  in the board frame. Derive all seven camera-frame coordinates from the rigid
  camera-to-board transform; do not collapse the array to one averaged point.
- For a camera-derived 3-D target point `x`, predict every channel's bistatic
  range as `(norm(x - TX_i) + norm(x - RX_j)) / 2`. Compare all 12 predictions
  with channel range, calibrated relative phase, and range rate after channel
  calibration and TDM motion compensation.
- Measure camera and radar boresight pitch/yaw, enclosure deployed tilt, lens
  and radar height, ball height, tee range, and net range.
- Calibrate intrinsics and distortion at the exact focus and each distinct
  readout/crop. Do not infer focal length from the detected ball being scored.
- Use a camera-visible, mechanically registered radar reflector at multiple
  surveyed positions to solve extrinsics; reserve positions as a holdout and
  report residuals separately for all 12 TX/RX pairs.
- Keep camera hardware and wiring flush with or behind the antenna radiator
  plane and outside the forward RF aperture.

Camera-only cadence, field-of-view, exposure, and persistence tests do not
require completed extrinsics. Fusion results do.

## Mode matrix

Run the matched core arms first. A requested rate is a configuration input;
sensor timestamps determine delivered cadence.

| ID | Request | Readout class | What the comparison isolates |
| --- | --- | --- | --- |
| `binned-crop-320-450` | 320x200 at 450 FPS | approximately 2x subsampled, central crop | Current production reference. |
| `binned-strip-640-450` | 640x200 at 450 FPS | approximately 2x subsampled, wide strip | Compare with 320x200 at equal FPS and sampling; tests field width/crop rather than detail. |
| `binned-full-640-120` | 640x400 at 120 FPS | approximately 2x subsampled, full field | Same-FPS control for native 1280x800; isolates sampling/light trade. |
| `binned-full-640-250` | 640x400 at 250 FPS | same readout as preceding arm | Compare with 640x400 at 120 FPS; isolates temporal sampling. |
| `native-full-1280-120` | 1280x800 at 120 FPS | 1:1 native, full field | Primary pixels-versus-light/cadence candidate. |

Secondary arms run only after the core matrix:

| ID | Request | Purpose |
| --- | --- | --- |
| `native-crop-1280x720-120` | 1280x720 at 120 FPS | Tests whether a native row crop retains the required corridor; use only if reported by the camera. |
| `binned-strip-640x100-450` | 640x100 at 450 FPS | Timing/throughput characterization only; prior work found it vertically fragile. Do not promote it from timing data alone. |

The critical matched comparisons are:

- `320x200@450` versus `640x200@450`: crop/coverage at the same subsampled
  angular scale and requested FPS;
- `640x400@120` versus `1280x800@120`: subsampled versus native 1:1 at the
  same field and requested FPS;
- `640x400@120` versus `640x400@250`: temporal sampling at the same readout.

Do not add an unimplemented `1280x200` 1:1 strip to a field study. That mode
requires a separate driver change and bench-validation pull request.

## Controlled variables and experimental structure

- Fix enclosure, focus, lens, camera/radar position, tee, target line, ball,
  mat, backdrop, club, and golfer within a block.
- Record lux at both the stationary ball and the club corridor. A single room
  reading is insufficient for directional lighting.
- Preserve the same illumination for matched arms. Run an explicit lux ladder
  as a separate factor: baseline, then approximately 2x, 4x, 8x, and 16x when
  the hardware can provide it safely.
- Lock exposure and gain within an arm. Full-stack automatic exposure may
  select a startup value, but production locks it before arming; record the
  seed, selected value, decision, and saved exposure-state file.
- Use native raw frames. Do not digitally resize one capture to create another
  mode.
- Randomize or alternate matched-arm order to reduce warm-up, fatigue, and
  lighting drift.
- Preserve every attempt and invalid reason. Replacing failed clips without
  counting them biases the reliability result.
- Freeze analysis code, thresholds, and calibration before the truth holdout.

## Procedure

### Stage 0: mode and software provenance

1. Record the OpenFlight commit, kernel, Pi firmware, Picamera2/libcamera
   versions, driver patch/hash, camera module, lens marking, focus position,
   and storage device.
2. Save `rpicam-hello --list-cameras` in full.
3. For every arm, save Picamera2/libcamera's resolved sensor configuration,
   raw format, crop, and `ScalerCrop`. Stop the arm if the readout is not what
   its ID claims.

### Stage 1: calibration and field of view

1. At final focus, capture at least 10-15 checkerboard views spanning distance,
   tilt, and all image corners for every distinct readout/crop.
2. Run `scripts/hardware-test/calibrate_camera_intrinsics.py`. Require at least
   eight usable views, at least 55% coverage, and at most 0.5 px RMS
   reprojection error, matching the script's gates.
3. With a surveyed stationary target, measure horizontal/vertical field, ball
   diameter, and pixels per millimeter. The subsampled arms should agree in
   angular scale; `1280x800` should show approximately twice the sampling.
4. If scale changes unexpectedly, stop and resolve mode substitution, crop,
   focus, or calibration before collecting swings.

### Stage 2: lux, exposure, and blur

1. At each lux rung, run the static exposure sweep for the selected arm.
2. Treat mean brightness 80-150, clipped pixels at most 1%, and dark pixels at
   most 5% as a screening result only.
3. Select candidates at or below the mode-specific blur ceiling. If no
   candidate is bright enough, increase measured incident light rather than
   accepting a longer exposure and calling it equivalent.
4. Capture a moving club at known or reference speed. Measure edge-spread blur,
   local contrast, saturation, ball/club segmentation uncertainty, and any
   polarity reversal against the local background.
5. The shortest statically acceptable exposure is not automatically the
   winner; score the moving target.

### Stage 3: cadence and timing contract

1. Run the production architecture first: leave the OV9281 free-running and
   use the existing sound edge only to freeze the camera and IWR rolling
   buffers. It is a capture event, not a camera-frame trigger.
2. Preserve `SensorTimestamp`, `ExposureTime`, and the host callback timestamp
   for every camera frame. Use `SensorTimestamp - ExposureTime / 2` as the
   approximate optical midpoint, and characterize the sensor-to-host mapping
   instead of using callback arrival as exposure time.
3. Estimate physical contact independently from the camera's ball departure
   and from the radar range walk. Report their signed residual, median absolute
   residual, and spread in milliseconds and camera frames. The existing
   21-shot study found camera/radar impact agreement within 0.66 frame; the new
   enclosure and every candidate mode must remeasure it.
4. Sweep the camera-to-radar alignment across at least plus/minus one delivered
   frame and report the change in each downstream result. Pre-register the
   endpoint error limit before using the result to approve a mode.
5. Do not require external triggering when event alignment and timing
   sensitivity pass that limit. An external trigger controls when the sensor
   starts a frame; it is not needed to operate the rolling buffer and it does
   not, by itself, place an IWR sample on the same clock.
6. Only after the baseline fails, prototype a shared electrical edge. Start
   with a Pi 5 RP1 hardware-PWM output fanned through verified
   buffers/level-shifters to OV9281 FSIN and an IWR timing-capture input. Extend
   the IWR firmware to latch device time plus frame, loop, and TX/chirp index,
   and verify voltage, polarity, missed pulses, fixed offset, and jitter with a
   logic analyzer or oscilloscope.
7. Use an RP2040 PIO or Arduino hardware timer only if the Pi hardware output
   fails the electrical timing gate. Do not substitute `digitalWrite` or a
   Python timing loop.
8. Do not equate the OPS 30 kHz sample spacing with end-to-end impact-time
   accuracy.

### Stage 4: camera-only moving-target screen

Collect a minimum pilot of five valid 7-iron clips per core arm. This is a
screen, not enough evidence to change production. Record:

- ball and club pixel spans with uncertainty;
- complete field coverage and edge margins;
- usable pre-impact and post-impact observations by endpoint;
- measured blur and local edge quality;
- delivered FPS, gaps, buffer errors, archive bytes, save latency, temperature,
  and throttling;
- invalid attempts and reasons.

There is no single frame-count pass/fail value. Report whether the actual
consumer can fit its specified model and how its uncertainty changes. A
radar-timed interpolation endpoint and a camera-only trajectory endpoint must
not share an invented gate.

### Stage 5: finalist full-stack and truth study

Select at most two finalists. Full-stack scoring requires the new geometry,
intrinsics, timing calibration, and frozen processing.

- Capture driver, wedge, 7-iron, and 9-iron to break the club/speed confound.
- Capture a per-club address photo for the actual outline/scale; do not use the
  generic 690CB mesh as truth for an arbitrary club.
- Use foot spray or impact tape for strike-location truth when that endpoint is
  evaluated.
- Capture simultaneous TrackMan or another independent reference when
  available. Freeze matching, calibration, and thresholds before the holdout.
- For club delivery, evaluate pre-impact sensor-time observations and
  extrapolate to contact. Report cross-impact results only as a collision
  diagnostic.
- Run the camera/radar same-point residual test. A changing residual means the
  optical feature and radar scattering center cannot be fused as one point.
- Report direct measurements, fused measurements, and model-derived D-plane
  quantities as separate fields.

## Required measurements

Every attempted clip records:

- requested and resolved sensor mode, crop, raw format, output shape, and FPS;
- calibrated `fx`, `fy`, `cx`, `cy`, distortion, reprojection RMS, and
  calibration hash;
- exposure/gain request and actual metadata, lux at ball/corridor, local
  contrast, clipped/dark fractions, and motion blur;
- sensor and host timestamp summaries, gap count, camera/radar contact
  residual, timing-sensitivity sweep, and timing uncertainty; when the optional
  external-trigger escalation is run, also record camera-FSIN offset, IWR
  sync-latch position, camera-to-IWR clock mapping, and missed pulses;
- ball diameter/center uncertainty, club span/edge uncertainty, coverage, and
  endpoint-specific usable observations;
- archive size/save time, callback errors, Pi temperature, and throttling;
- fusion availability, withholding reason, same-point residual, and timing
  perturbation range;
- per-channel measured-minus-predicted range and calibrated relative-phase
  residuals for all 12 TX/RX combinations;
- reference row, signed error, absolute error, and invalid reason when truth is
  available.

## Decision rule

An arm is invalid if its resolved readout differs from its definition, any
scored clip has a sensor-timestamp gap, required scene content is clipped, the
capture is corrupted/throttled, or provenance is incomplete.

Then decide in this order:

1. Verify the matched-pair hypotheses before comparing downstream output.
2. Compare calibrated spatial uncertainty, not total output pixels.
3. Compare delivered temporal observations and timing uncertainty, not
   requested FPS.
4. Reject spatial gains purchased with unacceptable blur, noise, clipping,
   light requirements, or missing field coverage.
5. Require finalist availability and frozen-pipeline error to be no worse than
   baseline on a blinded truth set.
6. Report ball, club-delivery, impact-detail, and full-fusion decisions
   separately. One mode need not win every endpoint.
7. If the result depends on an unmeasured timing, geometry, or calibration
   assumption, make no production change.

## Current tooling limitations

- Camera-only captures do not persist Picamera2's resolved camera
  configuration. Save terminal/libcamera logs and report this provenance gap.
- The exposure sweep scores static global brightness and clipping; it does not
  measure moving-club blur or local edge uncertainty.
- Camera-only capture does not calculate ball span, club span, blur, or
  endpoint-specific usable observations. Use a documented annotation method or
  add analysis automation in a separate reviewed pull request.
- Production auto-exposure calibrates at startup and then locks. It does not
  provide a within-session lux experiment; restart and archive state for every
  controlled lighting arm.
- Some production club fusion paths still consume host timestamps. The study
  must preserve and analyze sensor timestamps even if production has not yet
  switched.
- Current firmware does not latch a synchronization edge into the IWR
  frame/loop/chirp clock, and the current camera path does not drive OV9281
  FSIN. That prevents an independent absolute clock mapping, but it does not
  block event-aligned fusion: the production paths already estimate physical
  contact from the ball/range evidence. Require the electrical path only if the
  baseline residual or sensitivity sweep fails its endpoint error limit.

## Contributor deliverables

The external raw archive contains camera frames, metadata, JSONL, OPS capture,
IWR `.l3dump`, mode/configuration logs, calibration captures, timing reports,
lux records, truth exports, and setup notes. Include electrical sync traces only
when the optional external-trigger escalation was run.

The draft pull request contains only:

- a study manifest with hashes and exact commands;
- one summary CSV row per attempted clip;
- calibration and exposure reports;
- preregistered matched-pair and finalist results;
- small diagnostic plots/crops needed to audit the conclusion;
- no production setting change.

The pull request remains draft until maintainer review. Disclose substantive AI
assistance under `AI-POLICY.md`; never describe replay or simulation as hardware
validation.
