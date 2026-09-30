# Behind-the-ball camera and 60 GHz radar fusion

*Working research log, updated 29 September 2026. For maintainers and engineers.*

OpenFlight is a Raspberry Pi golf launch monitor. This log covers its behind-the-ball subsystem: an OV9281 global-shutter camera and a TI IWR6843 radar in one enclosure. It keeps three kinds of statement apart:

- what the code does;
- what single tests showed;
- what the research suggests.

Nothing here is a validated accuracy figure yet. An earlier version overstated several results; see the [corrections log](#11-corrections-log).

Evidence tags:

| Tag | Meaning |
|---|---|
| **Code** | What the software does, with tests. Says nothing about accuracy. |
| **Measured** | Seen on a real capture. Conditions are in the experiment log. |
| **Computed** | Derived from a measurement plus stated assumptions. |
| **Inferred** | Physics, simulation or literature. Not measured here. |
| **Secondary** | From a forum, review or retailer, not the maker. |
| **Open** | Unknown until tested. |

## Contents

1. [What the subsystem does](#1-what-the-subsystem-does)
2. [Rig constants and conventions](#2-rig-constants-and-conventions)
3. [The range setup, step by step](#3-the-range-setup-step-by-step)
4. [Known problems in the current code](#4-known-problems-in-the-current-code)
5. [Experiment log](#5-experiment-log)
6. [Research: where the radar sees the ball](#6-research-where-the-radar-sees-the-ball)
7. [Research: setup geometry and lens height](#7-research-setup-geometry-and-lens-height)
8. [Research: impact location](#8-research-impact-location)
9. [Research: spin and spin axis](#9-research-spin-and-spin-axis)
10. [Status and next experiments](#10-status-and-next-experiments)
11. [Corrections log](#11-corrections-log)
12. [Reproducing the figures](#12-reproducing-the-figures)

## 1. What the subsystem does

The code does two jobs (**Code**).

1. **Range setup, before swings.**
   - The IWR6843 records the empty hitting area, then a resting ball.
   - The camera finds the same ball and locks an exposure for it.
   - The setup hands three values to the swing server: the radar's range to the ball, the lens height, and the ball's height.
2. **Swings.**
   - The swing server fuses radar and camera into horizontal launch, club path and attack angle.
   - It also shows an experimental face angle beside club path, derived from the D-plane: (horizontal launch − 0.2 × path) / 0.8.
   - None of these has been checked against truth on this enclosure.

## 2. Rig constants and conventions

Positions are relative to the camera lens, as seen from behind the unit looking at the target. The source column says how each value is known.

| Constant | Value | Source |
|---|---|---|
| Camera boresight | level | design |
| Lens height above the unit's feet | 95 mm at the default foot setting | Tape, 22 Sept. Used as the lens height above the hitting surface unless the setup's radar check finds a gross mismatch ([section 7](#7-research-setup-geometry-and-lens-height)). |
| IWR6843LEVM receive-antenna centre | in line with the lens sideways, 50.6 mm below (44.4 mm above the surface), 30 mm behind | Tape, 22 Sept; the height re-measured 30 Sept from the receive column's ends and middle, 40.2, 44.3 and 48.6 mm (it was 44 mm below the lens). Sideways alignment confirmed by the builder. |
| IWR6843 and OPS243 tilt | 10° up | Design mount angle; not measured on this box. |
| OPS243 | 85 mm left, 47 mm below, 20 mm behind | tape, 22 Sept |
| Microphone | 80 mm left, level with the lens | tape, 22 Sept |
| LIS3DH accelerometer | flat on the shell floor, turned 180° (its +Y points to the back) | Checked 23 Sept: the camera, the tape and the inclinometer agreed on a 3.3° tilt. |
| Focal length | 933 px at 1280×800; 467 px at 640×400 and 320×200 (both 2× binned) | Nominal: a 2.8 mm lens over 3 µm pixels. **Open:** checkerboard calibration is pending, the principal point is assumed to be the image centre, and lens distortion is uncalibrated. |
| Radar range bias | 66 mm | Calibrated with a corner reflector on the July rig. Which point of the reflector was taped is not recorded. |

Conventions (**Code**):

- **Axes:** lateral is positive toward target-right, forward is down the target line, and up is against gravity.
- **Pitch:** camera pitch is positive nose-up. This holds consistently through the LIS3DH, the camera rays, club delivery and the radar path.
- **Heights:** heights are above the hitting surface: the mat or grass the setup ball rests on. A ball on a tee is higher than that surface by the tee height.
- **Handedness:** right-handed golfer by default. Left-handed is an explicit flip.

## 3. The range setup, step by step

### 3.1 Static radar range (Code)

1. Pre-MTI range profiles from the empty capture and the ball capture are averaged as power.
2. A selector looks for the range bins where the ball capture is brighter by both a fractional and an absolute margin. It rejects scene changes, boundary peaks, clutter and unstable frames.
   - **Scene changes (changed 29 Sept).** A reflector that vanished between the captures rejects the setup only if it touches the ball's bins, or if its range-FFT leakage into them exceeds 10 % of the ball's own change. The firmware's FFT is unwindowed, so the leakage bound is the rectangular sidelobe envelope, 1/(π²k²) at k bins. Anything farther away is recorded as an ignored loss. On the Pi a door and hanging clothes 0.7 m behind a ball lost half their echo between captures and rejected a good reading; replayed with this rule, that setup gives 1.015 m against a 1.00 m tape (**Measured**, one setup). Two other setups that day are still rejected: the ball's echo never cleared the detection gates.
3. It takes a power-weighted centroid of those bins and subtracts the 66 mm bias.
   - **Camera window (added 29 Sept).** At Save, the camera's own range to the ball (from its apparent size, found without any radar hint) bounds the radar reading to ±2σ, with σ at least 20 % of the range. A radar pick outside that window, such as a still person, club or net at another distance, is replaced by re-selecting inside the window from the saved profiles, and both are recorded. A camera-steered reading is marked `camera_range_used` and can't count as independent evidence for qualification.
4. The result is treated as the slant range from the receive antennas to the ball's centre.

**Open:** whether that really is the ball's centre. See [section 6](#6-research-where-the-radar-sees-the-ball).

**Why it is slow.**

- Each capture takes roughly 11–13 s, and the setup takes two: empty, then ball.
- 7.0 s of each is the 732,812-byte range ring crossing the radar's UART at 1,041,667 baud (**Computed**). The rest (Python start-up and port search, configuration, a fixed 1 s settle, cleanup) is estimated.
- Every capture record now lists `stage_seconds` and `total_seconds`, so the Pi's real split will be on file.
- A 14-frame profile with the same range windows moves 427 KB, about 4.1 s; the static gate needs at least 12 frames. It is opt-in until a Pi comparison shows the same accepted range (**Code**).

### 3.2 Finding the resting ball (Code)

1. **Find candidate spots.** A disk filter runs over the image, binned to 320 px wide, at 12 ball sizes. Its peaks from all sizes are merged, so each place is fitted once, at the size it matches best.
2. **Drop places outside the hitting area.** A place is dropped when no distance along its ray lies inside the **hitting area**. The area is defined in the world, not in pixels:
   - 1.0–2.5 m from the radar;
   - within ±0.30 m of its axis (±0.45 m for this first pass);
   - a ball centre from 10 mm sunk into grass up to a 90 mm tee, with the lens 0–1 m above the surface.

   It replaced a rule that dropped everything above the camera's level line, which excluded teed balls. The size ranges come from commercial units: TrackMan 4 sits 1.8–2.9 m behind the ball, Garmin R10 1.8–2.4 m, MLM2PRO 2.0–2.4 m; this rig's sensors are tuned nearer.
3. **Fit, check and rank.** At most 8 places get a physical lit-sphere fit. A fitted candidate whose size-implied range puts it outside the hitting area is refused with the limit named, for example "0.67 m left of the radar axis" or "too high for a ball on a tee". The rest are ranked on two things:
   - their sideways offset from the radar (σ = 0.15 m, using the size-based range);
   - how far their implied lens height is from the rig's nominal 95 mm (σ = 60 mm).

   A candidate scoring worse than 2.5 is refused, even if it is the only one.
4. **Follow the ball.** Once the ball is selected, each live look re-fits only near it, holding the size the full search found. Save runs the full-frame search again as an independent check.
5. **Speed measures.**
   - The fit's slopes are computed with all finite-difference variants drawn in one array call, using the same steps scipy uses.
   - The search runs in spawned worker processes, so it doesn't hold Python's interpreter lock against camera capture.
   - At 1280×800, the search starts while the radar is still recording the ball.
   - Each tester's last verified exposure lock is tried first.

### 3.3 Locking the exposure (Code)

The lock is the shortest exposure, then the lowest gain, at which the detector holds the ball steadily and its pixels pass two gates:

- signal ≥ 20 DN above black;
- ≤ 5 % of ball pixels clipped.

Contrast against a surrounding ring and edge gradient are recorded but not gated (changed 29 Sept). In DN both scale with exposure × gain exactly as the background does, so a fixed floor could only push the search into clipping. On the Pi a white ball on a white door was selected and fitted at 2 ms × 12 with 3 DN of contrast; the old 12 DN contrast gate drove the search to 8 ms × 10 with 5.2 % of the ball clipped (**Measured**, setup-20260929-33644a87fccd).

The search assumes the ball's signal is proportional to exposure × gain (**Inferred**). Only the frame mean has been measured, in one room ([E2](#e2-28-sept-2026-evening-one-indoor-range-setup)). From one unclipped ball measurement at product P₀:

```
k_i    = value_i / P0
P_min  = max_i(threshold_i / k_i)
P_clip = P0 / (95th-percentile ball level / clip level)
```

- **Skipping settings.** Settings predicted to be more than 1.5× too dark, or 1.5× past clipping, are skipped. The rest are verified lowest exposure first. If the prediction is off by more than 1.5×, the lowest passing setting can be skipped.
- **Before the ball is visible,** the frame's own brightness sets the jump. A frame within 1 DN of black climbs at least 4× per step.
- **Clipped measurements** never feed the prediction.
- **End states:** locked; no ball in a well-lit picture; more light needed; rig moved. (The `low_contrast` state was removed with the contrast gate.)

### 3.4 Lens height (Code)

When the static radar has accepted the ball, Save solves the lens height from two measurements: the camera ray to the ball's centre, and the radar range. The ball is taken to lie on its pixel ray at the one distance whose range from the radar equals the measured range:

```
t = (û·o) + sqrt((û·o)² − |o|² + R²)
h = r + t · down(û)
```

Here û is the unit ray to the ball, o is the radar's position relative to the lens, R is the radar range, and r is the ball radius.

The setup ball rests directly on the hitting surface, never on a tee, so h is the lens height above that surface. The ball is only 2–3° below the camera's level line, so every degree of tilt error moves h by about 22 mm at 1.25 m. With the tilt sensor's zero offset and the uncalibrated principal point, one ball solves h only to about ±25–65 mm (**Inferred**).

The solve is therefore a gross-error check, not a measurement:
- The rig file's 95 mm stands unless the solve differs from it by more than 60 mm (or twice its own uncertainty, if larger). That catches a unit standing on a box.
- A solve that would put the radar less than 10 mm above the surface is refused.
- The setup records the nominal height, the solved height, which one was used, and why (`consistent`, `unit_raised` or `unit_lowered`).

[Section 7](#7-research-setup-geometry-and-lens-height) explains why more precision wouldn't pay.

### 3.5 Hand-off to swings (Code)

With a qualified range, or with the test switch `--use-unqualified-tee-range`, swings receive `--iwr6843-tee-m` and `--iwr6843-ball-height-m` (one ball radius until per-shot tee height exists). Only when the setup found a gross mismatch do they also receive `--solved-camera-height-m`. The swing server then:
- replaces the rig file's lens height for the session;
- moves the radar height with it;
- records both in the session geometry.

Without the switch and without qualification, swings run with the range-dependent metrics withheld.

## 4. Known problems in the current code

- **Fixed 29 Sept: the search region excluded teed balls.** The ball search dropped anything more than 1° above the camera's level line, and a ball on a tee on a mat can sit at or above lens height. The hitting area (step 3.2) replaced that rule. A live-preview overlay of the area is not built yet.
- **Fixed 29 Sept: heights from the ball's support broke the radar's floor-bounce model.** The two-ray multipath model in `trajectory.py` and the LCMF path use the radar's height above the reflecting floor. Measuring heights from a tee top, and lifting every height when the radar would go negative, gave that model the wrong floor.
  *Fix:* heights are now above the hitting surface, the lift is gone, and a teed ball's height is a separate input that never moves the radar height. Tee height per shot, from the camera frames before impact, is next.
- **The burst-MTI notch spacing may use the wrong loop time.** `iwr6843/shot.py` sets `NOTCH_SPACING_MS = 26.93`, which is λ/(2·90 µs), the 2-transmitter loop. The current 3-transmitter profiles have a 135 µs loop, which puts the notches every 17.9 m/s. The value was tuned against TrackMan data, so it will be checked on the July dumps before anything changes. **Inferred**
- **The lit-sphere fit's size is unreliable.** On the field frame, the free fit stopped exactly at its upper size limit, twice the seed size (35.86 px from a 17.93 px seed). Fits held 12 % apart score the same. So size-based range carries at least 20 % uncertainty, and the ranking's sideways offset inherits it.
- **Roll is recorded but not applied.** The nominal ray model and the calibrated projection applied the LIS3DH roll with opposite signs. Until the correct sign is derived from the known mount, roll is left out. The field frame can't settle it ([E2](#e2-28-sept-2026-evening-one-indoor-range-setup)).
- **Range–Doppler coupling is not corrected.** An up-chirp FMCW radar reads a receding target long by v × f_c / S, about v × 0.62 ms here. Impact time compares the moving track with the static tee range, so it may carry a constant bias of about 0.62 ms. **Inferred**; the sign hasn't been checked on data.
- **The camera model is nominal.** Focal length, principal point and distortion are all uncalibrated, so every distance and angle derived from the camera carries that error.

## 5. Experiment log

Each entry is one test. Its results describe that test only.

### E1. 24 Sept 2026: a real swing around contact

- **Capture:** session `harjot-pilot-test-1`, clip `camera_20260924_185037_219_001`. 1280×800, 115.1 fps, 298 µs × 12, 24 frames, 18 of them before the trigger.
- **Observed (Measured):**
  - The ball is at rest in f18.
  - In f19 the head covers the ball's spot and a faint ball remains.
  - By f20 the spot is empty. Contact lies between f18 and f20, most likely near f19.
  - The ball is darker than the mat in this clip.
- **Impact tool:** the experimental v1 impact-location tool read 0 of 9 clips in this session: 4 "ball never moved", 3 "resting ball not found", 2 "implausible range".
- **Doesn't show:** club speed at contact, or any impact position.

![Six frames of a swing around contact](figures/swing-contact-strip.jpg)

*Frames f15–f20, brightened 3× for print. Timestamps are from the sensor.*

### E2. 28 Sept 2026, evening: one indoor range setup

The setup epoch is named 20260929 because the name uses UTC.

- **Conditions:**
  - Indoors on carpet, with a white door behind the ball. Epoch `setup-20260929-865e4caff08b`.
  - LIS3DH pitch +1.75°, roll −2.92°.
  - Tape: about 1.25 m from the radar to the ball's centre (approximate).
- **Radar (Measured):** static range 1.246 m, accepted by the selector (unqualified). This is one reading against an approximate tape. The estimator's own ±14 mm bin scalloping (section 6) is larger than the difference.
- **Camera search (Measured):**
  - *Old search, live:* the pre-rewrite search selected the ball only now and then, and kept losing it to features above the camera's level line. Run offline on the saved frame, its candidate list didn't include the ball. Its exposure search measured the wrong objects and ended "more light needed".
  - *New search, offline:* the rewritten search selected the ball on the saved frame. It fitted the ball at the size limit, 36 px, where about 32 px is expected at 1.25 m with the nominal focal length. It refused one other candidate (score 5.2).
  - *Timing:* on a desktop CPU, one full search took 21 s before the rewrite and 3.3 s after. Not measured on the Pi.
- **Exposure response (Measured):** six steps from the live search log. Frame mean above black against exposure × gain is linear: slope 0.00335 DN per µs·gain, intercept 1.7 DN, R² = 0.993. Every applied exposure was within the code's tolerance of the request (10 µs or 2 %, whichever is larger).
- **Lens height (Computed):** 83 ± 21 mm from the radar range, with nominal intrinsics and roll not applied. With the unverified roll applied it was 80.8 mm. The rig's 95 mm lies within the uncertainty, so this frame doesn't show that the feet sank.
- **Roll:** the LIS3DH read −2.92°. In the image, the door's vertical edges lean −2.36° (left edge) and +1.18° (right edge), and the door frames aren't known to be plumb. Inconclusive. A horizontal line can't be used here: the enclosure faced the door at an angle, so perspective tilts it.
- **Doesn't show:** any accuracy. n = 1, in one room.

![Field frame with the selected ball and a refused candidate](figures/field-detection.jpg)

*The saved frame with the rewritten search's result. Green: the selected ball. Red: the refused candidate. Orange: the camera's level line at the LIS3DH pitch, drawn for reference.*

![The resting ball, enlarged](figures/ball-closeup.jpg)

*The ball, enlarged 3×. Its top edge touches the dark gap under the door. The rest of it is against carpet, and its base sits in the pile.*

### E3. Synthetic scenes (unit tests): behaviour checks, not field results

- **Exposure prediction.** On physically consistent synthetic scenes (ball and background both scaling with exposure × gain), the search locks the lowest passing setting in ≤ 8 settings. The old step-by-step search needed 11–15.
- **Batched fit.** The batched finite-difference fit matches scipy's to within 0.02 px on three synthetic balls.
- **Crowding regression.** A regression scene covers the old failure where ten bright spheres above the camera's level line crowded out a dimmer ball on the floor.

### E4. 28 Sept 2026 (Pi): IWR6843 USB stalls

- **Symptom:** setup captures failed at random configuration lines ("did not acknowledge").
- **Evidence:** at each failure the kernel logged `cp210x ttyUSB0: failed set request 0x12 status: -110`, a CP2105 purge timeout. The gaps between those log lines matched the gaps between failed captures to the second.
- **Recovery:** RESET on the radar board didn't clear it; replugging USB or rebooting did.
- **Isolation:** thirty open/close checks with the radar idle were clean, so the stall needs a real capture to happen.
- **Open:** whether the cause is power or the data transfer.

## 6. Research: where the radar sees the ball

At 60 GHz the wavelength is 4.85 mm, and the ball's size parameter ka is about 28, which is the optical regime. A metal sphere returns from its near surface, one radius (21.3 mm) short of its centre.

A golf ball, though, is a layered dielectric. Titleist's radar ball puts its reflective ink *under* the cover, which shows the cover lets X-band (10 GHz) radar through. Whether it is similarly transparent at 60 GHz isn't known. If it is, a second return from inside the far surface, focused by the ball itself, can dominate. **Inferred**

| Ball material (εr, loss tanδ) | Mean RCS | Range offset from centre, mean [spread] | Stronger return |
|---|---|---|---|
| Metal sphere | −28.5 dBsm | −21.5 [−37, −8] mm | near surface |
| 2.3, lossless | −24.6 dBsm | +45 [30, 59] mm | internal |
| 2.3, tanδ 0.01 | −30.8 dBsm | +42 [20, 62] mm | internal |
| 2.3, tanδ 0.02 | −35.6 dBsm | +36 [11, 64] mm | front slightly stronger; the two interfere |
| 2.3, tanδ 0.05 | −41.7 dBsm | −19 [−37, −5] mm | near surface |
| 3.0, tanδ 0.01 | −20.0 dBsm | +53 [37, 68] mm | internal |
| 4.5, tanδ 0.02 | −35.1 dBsm | bimodal [−43, +93] mm | both |

*These are homogeneous-sphere Mie backscatter results over the IWR's 60.3–63.5 GHz sweep, passed through a re-implementation of the repo's centroid estimator. The spread comes from where the ball falls within a range bin. No published 60 GHz permittivity or loss data exists for golf-ball materials, so which row a real ball matches is **Open**.*

- **Estimator error.** The estimator itself scallops by about ±14 mm with the ball's position inside a 47 mm range bin (rectangular window, no zero-padding). Power differencing also lets mat clutter leak in through a cross-term. **Inferred** (simulation)
- **Bias calibration.** The 66 mm bias was calibrated on a corner reflector, which is a point scatterer; a ball isn't one. **Open:** whether the reflector was taped to its apex.
- **What the offsets affect.** They lie along the line of sight, so they move launch angle by ≤ 0.3° and speed not at all. They do matter for absolute range, for the camera's mm-per-pixel scale (1.7–6 %) and for impact time. **Inferred**
- **Camera lighting is the bigger launch-angle risk.** Overhead light shades the bottom of the ball and moves a thresholded centre upward. At a 15° launch, with an unbiased tee anchor over a 0.3 m track, a 2 mm shift gives +0.53° and a 4 mm shift gives +1.06°. **Inferred**
- **A fix that works for any ball material:**
  1. Subtract the empty and ball profiles as complex numbers.
  2. Zero-pad 8×.
  3. Fit a front return and an internal return.
  4. Report the front return plus one radius.
- **The deciding experiment:** alternate a foil-wrapped ball (a known near-surface reflector) and a bare ball at taped spots, stepped 5–10 mm apart. Bare minus foil gives the ball's offset, and their power ratio places the ball in the table above.

## 7. Research: setup geometry and lens height

The question: how precisely does the setup need to know how high the unit sits above the hitting surface, and how do commercial units handle it?

### What uses the lens height

- **Not used:** camera club path, face angle, attack angle and impact location on the face. They come from camera rays, the radar range and gravity from the tilt sensor. Ball speed and horizontal launch don't use it either. **Code**
- **Used:** the radar's vertical launch angle. With the board mounted vertically, the swing server uses the floor-bounce (two-ray) model, which needs the radar's height above the floor between the unit and the ball. **Code**
- **Planned:** per-shot tee height, measured against the setup's surface.

### How much a wrong height costs (Inferred)

A noise-free simulation of the repo's two-ray solver (`trajectory._two_ray_solve`): the radar truly 51 mm above the floor, tilted 10°, floor reflection −0.5, the ball tracked from 1.6 to 4.5 m, and heights fitted with the assumed radar height.

| Assumed height error | Launch error at 8° | at 14° | at 20° |
|---|---|---|---|
| −25 mm | +1.04° | +0.80° | −0.14° |
| −10 mm | +1.35° | 0.00° | −0.06° |
| +10 mm | −0.90° | −0.03° | +0.05° |
| +25 mm | −1.06° | −0.09° | +0.11° |
| +50 mm | −1.30° | −0.18° | +0.22° |

- **Per point.** A 10 mm height error moves the fitted ball height by +5 to +12 mm, and 25 mm by +11 to +28 mm.
- **Erratic, not proportional.** Even the correct height reads 8.8° for a true 8° launch. The solver is marginal at low launch with the radar only 51 mm up. The validated July rig had it at 152 mm, with about six times the phase separation between the direct and floor paths.
- **So:** a height error of 10–25 mm (sunk feet, or a mat under the ball but not the unit) sits inside the model's present noise. An error of 100 mm or more (a unit on a box) does not.

### What one ball can tell you (Inferred)

The lens is about 69 mm above the ball's centre and the ball is 1.25–2 m away, so the ray to it points only 2–3° below level. Any single-ball height solve is h = r + t·sin(depression), and angle errors dominate:

| Error source | Height error at 1.25 m | at 2 m |
|---|---|---|
| 1° of pitch (tilt-sensor offset is ±2.3° uncalibrated, datasheet) | 21.9 mm | 34.9 mm |
| Principal point 20 px off (plausible uncalibrated) | 26.8 mm | 42.9 mm |
| Floor sloping 1 % (a typical range tee line) | 12.5 mm | 20 mm |
| Focal length +5 % | 3.3 mm | 3.3 mm |
| Radar range +14 mm (estimator scalloping) | 0.8 mm | 0.5 mm |

The range barely matters to height; the angle between the ray and the surface is everything. Combined, one ball gives about ±25–65 mm as built, and about 5–8 mm indoors after a once-per-unit calibration of the intrinsics and the tilt sensor.

Placing the setup ball at two spots, near the unit and at the hitting spot, gives a line whose distance from the lens doesn't change under any common rotation. In simulation that is about ±3 mm with no calibration of pitch. It was considered and dropped: each spot needs its own radar capture (about 11–13 s), every setup, and its only consumer can't use that precision yet.

### What commercial units do

From makers' manuals, help centres and patents, read 28 Sept.

| Unit | Level | Vertical datum | Tee height |
|---|---|---|---|
| TrackMan 4 | Inclinometer and self-levelling motorised legs | Unit on a surface level with the hitting area; ±5 cm tolerance (**Secondary**) | Not exposed; the launch point falls out of the radar and camera track |
| FlightScope Mevo+ / Gen 2 | Tilt and roll indicators; fixed 12° kickstand | Unit on the floor with a typed hitting-surface offset of at most 76 mm; never raise the unit on a block | Typed; third-party advice on including the tee is inconsistent |
| Full Swing KIT | "Level ground" | Same level as the hitting surface (**Secondary**) | Nothing published |
| Garmin Approach R10 | "Tipped too far" tilt gate | Bottom edge of the unit above the mat | Not measured |
| Rapsodo MLM2PRO | Accelerometer read at session start | Level ground | Not measured |
| Voice Caddie SC4 | Not published | Level with the top of the hitting surface | Raise the unit for tees over 1.5 in |

- **Common practice.** Every maker references gravity with an onboard tilt sensor. Most define the unit's base as level with the hitting surface and tell the user to make it so. None measures tee height per shot.
- **Patents.** TrackMan's US 8,085,188 locates the launch point assuming "the radar is at a given height above the launch position". Google Patents projects its expiry as 24 June 2027. Ball-placement guidance (US 7,641,565) is live until 25 July 2027; any preview overlay needs a review before it goes upstream.
- **FlightScope's floor.** Its "never on a block" rule suggests its radar model uses the floor in front of the unit, which fits the two-ray finding above. **Inferred**

**Decision, 29 Sept (Code).** Use the rig's lens height by default, as the commercial units do, with no typed input. The one-ball radar solve replaces it only on a gross mismatch (over 60 mm). If the floor-bounce model is ever tightened, the next step is a once-per-unit calibration (a ChArUco board and the tilt sensor's offset), not a per-session step.

## 8. Research: impact location

At first touch the face is tangent to the ball, so the contact point is one ball radius along the face normal from the ball's centre. This is geometry, not an estimate:

```
P = B − r·n̂        n̂ = (cosΛ·cosF, cosΛ·sinF, sinΛ)        F: face angle, Λ: dynamic loft
```

- **Path doesn't move the contact point.** The club's direction of travel doesn't appear in P.
- **Face angle and loft do.** Face angle moves P about 0.32 mm per degree along the face (at Λ = 30°). Dynamic loft puts P r·sinΛ below the ball's centre: 11–13 mm for Λ = 30–36°. So impact location needs both face angle and loft.
- **Path and attack angle matter through timing (Inferred).** Take a 7-iron at 38–40 m/s with a 5° path and a −4° attack angle. The head moves about 3.5 mm per ms across the face and 3.1 mm per ms up it, provided the head's depth comes from the tangency condition. If depth comes from a speed or scale model instead, the vertical rate rises to about 21 mm per ms.
- **At 120 fps, two more effects exceed the target accuracy (Inferred):**
  - the curve of the swing arc across the frame gap, up to about 11 mm at mid-gap;
  - the head slowing at impact, losing about 8.8 m/s, which is worth up to about 5.6 mm vertically.

  So straight-line interpolation across contact isn't acceptable.

**Expected accuracy (Inferred).** One sigma after calibrating against spray marks, for the 7-iron case above at about 1.35 m. "Ball clock" means taking the contact time from where the ball appears in the first frame after contact, assumed good to about 0.3 ms.

| Contact-time source | Heel–toe, 1280 @ 120 | Heel–toe, 640 @ 288 | High–low, 1280 @ 120 | High–low, 640 @ 288 |
|---|---|---|---|---|
| Ball clock | 2.7 mm | 3.2 mm | 3.6 mm | 4.6 mm |
| Acoustic gate, ±1.5 ms jitter (bias calibrated out) | 5.8 mm | 6.0 mm | 5.8 mm | 6.5 mm |
| Frame bracket only | 8.8 mm | 4.6 mm | 8.2 mm | 5.5 mm |

- **Acoustic figures.** These come from the 25 Aug session, on an earlier enclosure that was never measured. There the trigger landed 0.5–4 ms after contact, rather than the assumed 4.44 ms.
- **The experimental v1 tool isn't ready.** It read 0 of 9 real clips in E1. With the ball placed by hand, it picked contact 1–2 frames late. Its head-shape thresholds come from an earlier rig. It stays on its own branch.
- **Comparators:**
  - *TrackMan 4* reports impact offset and height relative to the face centre, using radar timing plus its camera. It needs impact lighting (maker's help pages).
  - *Mevo Gen 2* offers face impact location as an add-on that needs at least 300 lux (maker's pages). How it times contact isn't published.
  - *Full Swing KIT:* no impact location found (**Secondary**: third-party reviews).
  - None of them publishes an accuracy figure.
  - Outside the comparator set, the GCQuad manual's face-sticker placement is the only public face-centre construction found. It's a useful convention for the spray photos.
- **Ground-truth plan:**
  - *Marking:* foot spray on every shot, plus two caliper-measured paint dots on each face at the ends of a reference scoreline.
  - *Photos:* face photos taken square to a clamped phone, rectified using the dots.
  - *Sample size:* per club and camera mode, 15 calibration and 40 validation shots with deliberately spread strikes. That's about 220 shots for a 7-iron and a driver in both modes.
  - *Pass mark:* to claim ±3 mm, the observed 1σ over the 40 validation shots must be 2.4 mm or less.

## 9. Research: spin and spin axis

From behind, spin-axis tilt shows up as a rotation in the image plane, which a camera measures well. What this view rules out is tracking the ball's surface without markers: surface features rotate out of the visible half between frames. So measuring spin with the camera needs a marker that gives the ball's full orientation in every frame. **Inferred**

| Mode | Ball, first flight frames | 5 mm dot | Turn per frame, 3k / 9k rpm | Limit |
|---|---|---|---|---|
| 1280×800 @ 120 fps | 20–32 px | 2–4 px | 150° / 450° | Dots too small; the axis is unreadable near 7,200 rpm (one turn per frame). |
| 640×400 @ 288 fps | 10–16 px | 1–2 px | 62° / 188° | Resolution: the ball and dots are too small. |
| 1:1 ROI @ ≈ 300 fps (not built) | 20–32 px | 2–4 px | 60° / 180° | Needs a sensor-mode patch; dots are still small without a strobe. |

- **Blur (Inferred).** Seen from behind, the ball moves mostly along the line of sight, so its image blur is about a quarter of its travel. That's about 1 px at 100 µs, but about 7 px at 800 µs. The captures so far used 298 µs to 3 ms.
- **Radar spin rate.** An offline dechirp replay of one 61-shot OPS243 (24 GHz) session locked on 11 shots, with about 1 % median error. The lock threshold was chosen on the same data, so a held-out session is still needed (`docs/development/spin-replay.md`).
- **Axis from the flight curve.** Not possible at this range: one degree of tilt moves the ball only about 0.35 mm sideways over 3 m. **Inferred**
- **TrackMan truth.** Reportedly, TrackMan 4 calculates the spin axis from club data when indoors (**Secondary**). If so, only outdoor TrackMan numbers are independent truth for the axis.

| Stage | Hardware | Expected (Inferred) |
|---|---|---|
| 0. Replay | None: count usable flight frames and visible markings in existing captures | Scopes the problem |
| 1. Stripe ball | One thick great circle drawn on the ball, set vertical along the target line | Axis tilt ±2–4°, no rate |
| 2. IR strobe | 850 nm pulses of 10–20 µs from the sensor's STROBE output, a bandpass filter, a dot-coded ball, a 1:1 ROI mode | Rate 2–5 %, axis 3–6°; also freezes the club head |
| 3. Longer lens or larger sensor | A 6–12 mm lens or an AR0234 sensor | Only for a 1–2° axis tier |

### Spin axis from the IWR6843 (Inferred)

The idea: the top and bottom of a backspinning ball move at different speeds along the line of sight. Sorting the echo by Doppler and measuring each slice's angle across the receivers should therefore show the axis. If the axis is tilted, the fast and slow sides rotate with it.

- **The physics holds at 60 GHz.** The dimples form a lattice with rows 3.6–3.9 mm apart, about 0.75–0.8 wavelengths. That gives a Bragg ring at about 40° on the ball, whose top and bottom arcs return two Doppler lobes at ±0.64·ωr: ±4.3 m/s at 3,000 rpm, 1.05° apart in angle at 1.5 m. At 24 GHz the dimples are too small to form the ring, so the OPS243 never had this. The lobes are expected about 30 dB below the ball's main echo, give or take 10 dB.
- **The method is patented.** TrackMan US 10,850,179 claims three or more non-collinear receivers, frequency components of the spinning ball, the angular position of each, and the spin-axis projection as the line perpendicular to them. FlightScope US 10,151,831 measures time delays between receiver pairs, which is the same physics. A freedom-to-operate check is needed before shipping anything like it.
- **As mounted, the board is poor for axis tilt.** The board is rotated 90°, so its 8-element virtual array runs vertically. That suits launch angle and top-versus-bottom separation. Tilt needs a sideways baseline, and the only one is a single half-wavelength transmitter offset: about 2.2× below TrackMan's stated design minimum, with tens of milliseconds of dwell where TrackMan integrates over seconds.
- **Aliasing.** The current 3-transmitter loop (135 µs) covers ±8.97 m/s, so the lobes alias above about 6,200 rpm, which covers every wedge and short iron. A 2-transmitter research profile would clear about 9,300 rpm.
- **Distance matters sharply.** Tilt error grows as the cube of range: a ball at 1.0 m instead of 1.5 m is 3.4× better.

| Ball | Spin rate | Axis, board as mounted | Axis, board rotated 90° |
|---|---|---|---|
| Unmarked | Coarse, ±5–20 %; enough to settle the OPS243's 1× / 2× ambiguity | Not feasible | Marginal at 1 m, only if the lobes are at the strong end |
| One or more foil dots | About 1 % | About 6–20° | About 1.5–5° |

- **The board stays as mounted.** Rotating it would weaken the vertical array that launch angle and the floor-bounce model depend on. If a marked ball is acceptable, the camera stages above remain the better route to the axis.
- **Next:** an offline look at the July TrackMan-scored dumps for the two lobes. Stop rule: if driver shots within 2.5 m show less than 6 dB of lobe-to-noise and a correlation below 0.5 with TrackMan spin, unmarked radar spin is closed. Then a drill-spun ball on a rod, bare and with foil dots, with a smooth 42 mm sphere as the control whose lobes must vanish.

## 10. Status and next experiments

**Established, with scope:**

- The camera applies the requested exposure and gain (E2).
- The rewritten search found the ball on one real frame where the old one failed (E2).
- One static radar reading agreed with an approximate tape (E2).
- In code, with tests: the hitting-area search, heights above the hitting surface, the rig lens height with a radar gross-error check, and per-stage timings on every radar setup capture.
- Ball speed, club speed, smash and vertical launch were checked against TrackMan on the July rig (radar at 152 mm, a different enclosure). Not yet repeated on this box.

**Open:**

- Where the radar sees a real ball (section 6).
- The camera intrinsics (checkerboard), the roll sign, and the radar tilt on this box.
- The radar/camera horizontal offset on this box. About 5° was measured on the earlier rig.
- Face angle, impact location and spin axis, against any truth.
- Timing on the Pi, and the cause of the USB stall.
- Whether the 14-frame radar setup profile matches the 24-frame one.
- The burst-MTI notch spacing (section 4).
- Whether the IWR6843 sees the dimple lobes on real shots (section 9).

**Next, in order:**

1. On the Pi: run the ball-search benchmark, and five setups each with the 24- and 14-frame radar profiles, which also records the real per-stage timings.
2. Compare a phone level on the enclosure with the LIS3DH roll, then derive and re-enable roll.
3. Measure a foil-wrapped ball and a bare ball at taped radar ranges.
4. Measure per-shot tee height from the frames before impact.
5. Offline: check the notch spacing and look for the dimple lobes on the July TrackMan-scored dumps.
6. Record a spray-marked session with an alignment stick in view, for impact and direction truth.
7. Film an LED on the trigger line with the camera, to measure camera-timestamp latency against the trigger.
8. Try the stripe ball on existing hardware, for a first spin-axis tilt.

## 11. Corrections log

These claims from the first version of this document (28–29 Sept) were wrong or overstated.

| Earlier claim | Correction |
|---|---|
| Headline figures: 3.3 s find time, "first tape match", 83 mm lens height, 3–7 exposure settings. | These were one desktop run on one frame, one approximate tape, a computed value within its uncertainty of the rig value, and synthetic scenes. They are now in the experiment log with their conditions. |
| The gap under the door tilts +0.75°, so the LIS3DH roll is wrong. | Perspective tilts a horizontal line when the camera faces it at an angle, and two of the sample points fell on the ball. The roll evidence is inconclusive. |
| A resting ball can't be above the horizon. | False for a teed ball on a mat. The code rule is listed as a known problem. |
| The swing pipeline only ever uses height differences. | The radar's floor-bounce model uses height above the floor. |
| The ball's top half sits against the white door; contrast 12 DN. | Only its top edge touches the gap under the door. The 12 DN label was never measured. |
| Validated: focal length, the tee-range reference point, a tape match to 4 mm, the radar's position, vertical launch against TrackMan. | The focal length is nominal. The reference point is open (section 6). The tape was approximate. The offsets were taped. The TrackMan check was on the July rig. |
| The radar-based lens height is tighter than the size-based one. | Both are about ±2 cm, limited by the camera model's angular uncertainty. The radar solve's advantage is that it doesn't depend on the fitted size. |
| The ball is at rest through f18 and gone at f19. | The head covers the spot in f19; the spot is empty in f20. |
| 10.0° is the correct radar tilt for this enclosure. | 10.0° is the design mount angle. It hasn't been measured on this box. |
| A real ball reads −21 or +45 mm; 1–9 mm of lighting bias gives +0.5 to +1.1°. | The simulated range spans −21 to +53 mm, with a bimodal case. The +0.53° and +1.06° figures correspond to 2 mm and 4 mm. |
| Radar spin rate works (measured). | This was one offline OPS243 session, with the threshold chosen on the same data. |
| An LED on the trigger line settles contact timing. | It measures camera-timestamp latency against the trigger. |
| The radar-solved lens height is good to about ±21 mm. | That figure assumed 1° of angular uncertainty. The tilt sensor's zero offset (±2.3° uncalibrated) and an uncalibrated principal point make it about ±25–65 mm, so the solve is now only a gross-error check (section 7). |
| A radar setup capture takes about 20 s. (Said in discussion, not in this document.) | Roughly 11–13 s each, estimated from the code; the setup takes two. Per-stage timings are now recorded. |

## 12. Reproducing the figures

```
uv run python docs/research/camera-fusion/scripts/make_figures.py \
    --frame <E2 frame .png> --bundle <harjot-pilot-test-1 bundle .zip> --pitch-deg 1.75
```

The E2 frame and the E1 bundle aren't in the repository. The code state is `feat/tester-capture-pilot`. The research reports and simulation scripts live beside the project research guide (§1Q), in the separate research worktree.
