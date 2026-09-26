# Tester suite: design

Date: 2026-09-23. Branch: `feat/tester-capture-pilot` on the fork. Fork-only: none of
this goes upstream except the product fixes listed under "Phase 2".

## Purpose

One guided suite a tester runs from the fork, on the Pi's own screen, that finds
**the shortest exposure at full resolution (1280x800) where the whole camera pipeline
still works**, compares it against 640x400, and collects impact-location labels on
the way. Face angle and the other club metrics depend on those pixels.

## Decisions (agreed 2026-09-23)

- Modes: **1280x800 @ 120 fps** and **640x400 @ 288 fps**. 320x200 and 640x200 are
  out: every mode under 1280 wide is 2x-reduced, so only 1280x800 gains pixels on
  the club; 640x400 is the frame-rate comparison.
- **Fixed rungs, the same for every tester**: 1280x800 at 300 / 200 / 150 / 100 /
  75 us, then 640x400 at 300 / 150 / 75 us; 5 accepted swings per rung.
- **Skip what cannot work, on picture grounds only**, so unusable swings are never
  collected. Never skip because the current club pipeline failed live: its pixel
  and brightness thresholds are tuned for 320x200 and would reject the very data
  needed to fix them.
- **No distance, lens or setup input.** No tape. Focal length is the 2.8 mm
  baseline. The tester picks the club, as now.
- **Impact photos with the unit's own camera** (no phone), on the 1280x800 rungs.
- Optional: a tester with a TM4, Full Swing KIT or Mevo Gen 2 uploads its export
  into the package. No import or matching code in this phase.
- Dropped for this version: lens calibration, ball walk and light variants,
  marked-ball spin, setup photos.

## Flow

**A. Check.** The existing preflight (git revision, kernel, camera modes listed,
power throttling), one green/red verdict. In this version step B is the stream
test: its light screen opens each mode and captures, and fails if the mode cannot
stream. Separate OPS, IWR6843, LIS3DH and sound-trigger checks are a follow-up; a
missing device shows as the ladder's kiosk failing to start.

**B. Light.** The existing gain screen, once per mode, run automatically. It
records the light index (DN per unit gain per microsecond) and the black floor,
which set every rung's gain and let testers be pooled by light without typing lux.

**C. Ladder.** Per mode, one kiosk run started with study mode. For each rung:

1. The page sets the rung's exposure and gain live through the kiosk (no restart).
   The gain keeps the brightness the gain screen chose at 300 us: gain(rung) =
   gain(300 us) x 300 / exposure, capped at 12. Past the cap the rung is darker,
   which is part of the answer.
2. **Pre-rung check** on 5 raw frames: the hitting zone's signal above the black
   floor and its noise at this exposure and gain. Below 10 DN above the black
   floor (the "light floor"), the rung and
   every shorter one in this mode are skipped ("too dark in this light"), at no
   swings' cost.
3. The tester swings. After each swing a **verdict** (green / amber / red, with
   reasons) from the saved capture alone.
4. **Early exit**: 2 of the first 3 swings red on picture grounds fail the rung,
   and the shorter rungs in the mode are skipped.
5. After 5 accepted (green or amber) swings the page moves to the next rung.
6. On 1280x800 rungs, after each swing: **Photograph face** (below).

Between the two modes the kiosk restarts once, automatically (interim; see
Trade-offs).

**D. Package.** One zip: the gain screens, every run's logs and captures,
`ladder.json`, the impact photos, and the optional comparator export.

## Components

### 1. Kiosk study mode (`server.py`, fork-only)

A `--study-mode` flag. Without it nothing below exists and production is unchanged.

- `POST /api/camera/study/controls` `{exposure_us, gain}` calls the existing
  `CameraCaptureRuntime.update_image_controls` (live, no buffer restart). Refused
  (409) unless study mode is on and exposure is manual.
- `GET /api/camera/study/frames?n=5` returns the latest n raw frames from the
  rolling buffer with each frame's exposure and gain, as `.npz`.

Each saved capture already records per-frame exposure and gain in `frames.npz`,
so a rung change inside one run is recorded per swing.

### 2. Ladder engine (`tester_server.py`)

- `LADDER`: the eight rungs above, with the mode, fps and exposure of each.
- New job `ladder <mode>`: starts the kiosk with `--study-mode`, manual exposure,
  the mode's size and fps, IWR6843 with raw dumps, inclinometer and rig file, and
  **no `--iwr6843-tee-m`**. It remains explicitly raw-only until the guided
  workflow has a qualified tee-range solution; the raw dumps let every radar
  number be recomputed later.
- State per tester in `ladder.json`: current rung; per rung the gain applied, the
  pre-rung check, every swing's verdict and capture id, and skip reasons. The page
  and the package both read it.
- It watches the run's log directory for new `camera_*` capture folders; each new
  capture gets a verdict and counts toward its rung.
- The runner fixes owed from the pilot are part of this: a job timeout and a
  process-group kill.

### 3. Verdict (per swing, picture grounds only)

From `frames.npz` and `metadata.json`:

| Check | Red when |
|---|---|
| Frames | Delivered fps under 90% of the mode's, or any gap |
| Controls | Applied exposure or gain differ from the rung's |
| Light | Hitting-zone signal below the light floor, or clipped over 5% |

Amber (never red): the resting ball not found in the pre-impact frames, or found
in a different place from the rung's other swings. With no size known the detector
runs its size-free search, which can pick a door stop, so it informs and never
fails a swing.

The live club pipeline's results stay in the kiosk's session log, joined to the
verdict by capture id offline; they are never a reason to fail.

### 4. Impact photos

The tester sprays the face with foot powder before each 1280x800 swing. After the
swing, **Photograph face**: the page switches to a still setting (the exposure
that puts the hitting zone near 100 DN at gain 2, capped at 8000 us, under the
8.3 ms frame period at 120 fps), waits for it to take, fetches one raw frame, saves it as
`impact/<capture id>.pgm`, and restores the rung's settings. The live preview shows
a frame guide at about 0.5 m, where the face spans about 130 px and the ball's mark
about 35 px. The tester wipes and re-sprays.

### 5. Page (`tester.html`)

Steps A to D in order. The ladder panel shows the rung, its counter, the last
verdict and its reasons, skipped rungs and why, the face-photo button with the
preview, and the comparator upload. One button per step.

### 6. Docs (`docs/camera/tester-pilot.md`)

The new steps, the supplies (foot powder spray, a cloth), and troubleshooting rows
for each red reason. No phone or `--host` instructions.

## Trade-offs

- **One kiosk restart between the two modes (about 20 s), for today.** Rung
  changes inside a mode are live and instant; a camera-only mode reconfigure
  (generalising `update_vertical_crop`) is the follow-up that removes the restart.
- **The pre-rung floor is deliberately low.** It skips only rungs that are clearly
  unusable; where the real exposure floor lies is the analysis's job.
- **About 1 GB per tester** at 1280x800. The package is copied off the Pi on a USB
  stick; an upload path is later work.

## Testing

- Study endpoints: Flask test client with a fake capture runtime; refused without
  study mode or with auto exposure.
- Ladder engine: a fake kiosk client and synthetic capture folders; pre-rung skip,
  early exit, auto-advance, skip propagation to shorter rungs, and `ladder.json`
  persistence across a page reload.
- Verdict: synthetic captures for each red and amber reason.
- Impact photo: fake kiosk; the settings are restored after the photo, also on
  error.
- The existing suite stays green.

## Phase 2 (not today)

- Analysis: per mode and rung, face-angle consistency within a swing and agreement
  with the ball's start direction, giving the exposure floor per light index.
- Impact-mark extraction into face coordinates, as labels.
- Product fixes for the upstream camera PR: the club pipeline's pixel limits
  scaled by mode, and its brightness floors made noise-based.
- The camera-only mode reconfigure.
