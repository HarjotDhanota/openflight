# Behind-the-ball camera and 60 GHz radar fusion

*Working research log, corrected 29 September 2026. For maintainers and engineers.*

OpenFlight is a Raspberry Pi golf launch monitor. This log covers its behind-the-ball subsystem: an OV9281 global-shutter camera and a TI IWR6843 radar in one enclosure. It keeps three kinds of statement apart:

- what the code does;
- what single tests showed;
- what the research suggests.

Nothing here is a validated accuracy figure yet. An earlier version overstated several results; see the [corrections log](#10-corrections-log).

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
7. [Research: impact location](#7-research-impact-location)
8. [Research: spin and spin axis](#8-research-spin-and-spin-axis)
9. [Status and next experiments](#9-status-and-next-experiments)
10. [Corrections log](#10-corrections-log)
11. [Reproducing the figures](#11-reproducing-the-figures)

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
| Lens height above the unit's feet | 95 mm at the default foot setting | Tape, 22 Sept. The actual height depends on the surface and is solved at setup. |
| IWR6843LEVM receive-antenna centre | in line with the lens sideways, 44 mm below, 30 mm behind | Tape, 22 Sept. Sideways alignment confirmed by the builder. |
| IWR6843 and OPS243 tilt | 10° up | Design mount angle; not measured on this box. |
| OPS243 | 85 mm left, 47 mm below, 20 mm behind | tape, 22 Sept |
| Microphone | 80 mm left, level with the lens | tape, 22 Sept |
| LIS3DH accelerometer | flat on the shell floor, turned 180° (its +Y points to the back) | Checked 23 Sept: the camera, the tape and the inclinometer agreed on a 3.3° tilt. |
| Focal length | 933 px at 1280×800; 467 px at 640×400 and 320×200 (both 2× binned) | Nominal: a 2.8 mm lens over 3 µm pixels. **Open:** checkerboard calibration is pending, the principal point is assumed to be the image centre, and lens distortion is uncalibrated. |
| Radar range bias | 66 mm | Calibrated with a corner reflector on the July rig. Which point of the reflector was taped is not recorded. |

Conventions (**Code**):

- **Axes:** lateral is positive toward target-right, forward is down the target line, and up is against gravity.
- **Pitch:** camera pitch is positive nose-up. This holds consistently through the LIS3DH, the camera rays, club delivery and the radar path.
- **Heights:** see [known problems](#4-known-problems-in-the-current-code). The current code measures them from the ball's support, and that choice is being revised.
- **Handedness:** right-handed golfer by default. Left-handed is an explicit flip.

## 3. The range setup, step by step

### 3.1 Static radar range (Code)

1. Pre-MTI range profiles from the empty capture and the ball capture are averaged as power.
2. A selector looks for the range bins where the ball capture is brighter by both a fractional and an absolute margin. It rejects scene changes, boundary peaks, clutter and unstable frames.
3. It takes a power-weighted centroid of those bins and subtracts the 66 mm bias.
4. The result is treated as the slant range from the receive antennas to the ball's centre.

**Open:** whether that really is the ball's centre. See [section 6](#6-research-where-the-radar-sees-the-ball).

### 3.2 Finding the resting ball (Code)

1. **Find candidate spots.** A disk filter runs over the image, binned to 320 px wide, at 12 ball sizes. Its peaks from all sizes are merged, so each place is fitted once, at the size it matches best.
2. **Drop impossible places.** A place is dropped if the ray through it points more than 1° above the camera's level line, or if its size and row imply a lens height outside 0–1 m above the ball's support. The first rule is wrong for teed balls; see [known problems](#4-known-problems-in-the-current-code).
3. **Fit and rank.** At most 8 places get a physical lit-sphere fit. Candidates are ranked on two things:
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

The lock is the shortest exposure, then the lowest gain, at which the ball passes four gates:

- signal ≥ 20 DN above black;
- contrast against a surrounding ring ≥ 12 DN;
- edge gradient ≥ 8 DN;
- ≤ 5 % of ball pixels clipped.

The search assumes each gate value is proportional to exposure × gain (**Inferred**). Only the frame mean has been measured, in one room ([E2](#e2-28-sept-2026-evening-one-indoor-range-setup)). From one unclipped ball measurement at product P₀:

```
k_i    = value_i / P0
P_min  = max_i(threshold_i / k_i)
P_clip = P0 / (95th-percentile ball level / clip level)
```

- **Skipping settings.** Settings predicted to be more than 1.5× too dark, or 1.5× past clipping, are skipped. The rest are verified lowest exposure first. If the prediction is off by more than 1.5×, the lowest passing setting can be skipped.
- **Before the ball is visible,** the frame's own brightness sets the jump. A frame within 1 DN of black climbs at least 4× per step.
- **Clipped measurements** never feed the prediction.
- **End states:** locked; `low_contrast` (the ball was bright enough but never stood out); no ball in a well-lit picture; more light needed; rig moved.

### 3.4 Lens height (Code)

When the static radar has accepted the ball, Save solves the lens height from two measurements: the camera ray to the ball's centre, and the radar range. The ball is taken to lie on its pixel ray at the one distance whose range from the radar equals the measured range:

```
t = (û·o) + sqrt((û·o)² − |o|² + R²)
h = r + t · down(û)
```

Here û is the unit ray to the ball, o is the radar's position relative to the lens, R is the radar range, and r is the ball radius.

The solve rests on four assumptions:
- the radar range reaches the ball's centre (**Open**);
- the nominal camera intrinsics;
- the LIS3DH pitch;
- roll not applied.

Its stated uncertainty is about ±21 mm at 1.25 m. That comes from the camera model's 1° angular uncertainty, which covers pitch, principal point and distortion together.

If the radar hasn't accepted the ball, an apparent-size solve is used instead. It's weaker, because the fitted size is unreliable.

### 3.5 Hand-off to swings (Code)

Swings receive `--iwr6843-tee-m`, `--solved-camera-height-m` and `--iwr6843-ball-height-m` when the range is qualified, or when the test switch `--use-unqualified-tee-range` is set. The swing server then:
- replaces the rig file's lens height for the session;
- moves the radar height with it;
- records both in the session geometry.

Without the switch and without qualification, swings run with the range-dependent metrics withheld.

## 4. Known problems in the current code

- **The search region excludes teed balls.** Step 2 of the ball search drops anything more than 1° above the camera's level line. With the lens 80–95 mm above the floor, a ball on a tee on a mat can sit at or above lens height, especially a few metres out.
  *Planned fix:* a hitting area like the commercial units use: a zone about 1.0–2.5 m out and ±0.15–0.3 m sideways, projected into the image with the tilt sensor and shown on the live preview. It narrows further once the radar range is known.
- **Heights measured from the ball's support break the radar's floor-bounce model for teed balls.** Most of the swing geometry uses only height differences. But the two-ray multipath model in `trajectory.py` and the LCMF path uses the radar's height above the reflecting floor. Measuring heights from a tee top, and lifting every height when the radar would go negative, gives that model the wrong floor.
  *Planned fix:* the setup ball rests on the hitting surface, as the surface reference. The radar's height above the floor comes from that setup. Tee height is measured per shot, from the camera frames before impact.
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

## 7. Research: impact location

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

## 8. Research: spin and spin axis

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

## 9. Status and next experiments

**Established, with scope:**

- The camera applies the requested exposure and gain (E2).
- The rewritten search found the ball on one real frame where the old one failed (E2).
- One static radar reading agreed with an approximate tape (E2).
- Ball speed, club speed, smash and vertical launch were checked against TrackMan on the July rig (radar at 152 mm, a different enclosure). Not yet repeated on this box.

**Open:**

- Where the radar sees a real ball (section 6).
- The camera intrinsics (checkerboard), the roll sign, and the radar tilt on this box.
- The radar/camera horizontal offset on this box. About 5° was measured on the earlier rig.
- Face angle, impact location and spin axis, against any truth.
- Timing on the Pi, and the cause of the USB stall.

**Next, in order:**

1. Compare a phone level on the enclosure with the LIS3DH roll, then derive and re-enable roll.
2. Measure a foil-wrapped ball and a bare ball at taped radar ranges.
3. Build the hitting-area search region and per-shot tee height. These fix the two code problems in section 4.
4. Record a spray-marked session with an alignment stick in view, for impact and direction truth.
5. Film an LED on the trigger line with the camera, to measure camera-timestamp latency against the trigger.
6. Try the stripe ball on existing hardware, for a first spin-axis tilt.

## 10. Corrections log

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

## 11. Reproducing the figures

```
uv run python docs/research/camera-fusion/scripts/make_figures.py \
    --frame <E2 frame .png> --bundle <harjot-pilot-test-1 bundle .zip> --pitch-deg 1.75
```

The E2 frame and the E1 bundle aren't in the repository. The code state is `feat/tester-capture-pilot`. The research reports and simulation scripts live beside the project research guide (§1Q), in the separate research worktree.
