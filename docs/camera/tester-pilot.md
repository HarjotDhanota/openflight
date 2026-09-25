# Camera mode study — tester guide

For the first run of the complete fusion bench, follow the
[Pi commissioning procedure](pi-commissioning.md) alongside this guide.

The test suite finds the shortest camera exposure that still works at full
resolution (1280×800), and compares it with 640×400, while recording everything
the camera and both radars see. You hit a 7-iron; the page sets each exposure
itself, gives a verdict after every swing, skips exposures your light cannot
support, and takes a photo of the club face after each full-resolution swing.
The capture walkthrough does not show estimated club or ball numbers. Its
separate [saved-capture review page](recorded-track-comparison.md) supports
manual annotations and conditional geometry comparisons after recording.
Use **View fusion diagnostics** for existing pipeline results as shots finish,
including source labels, partial outcomes and recorded camera/IWR disagreements.
The [diagnostics guide](live-fusion-diagnostics.md) explains hidden-metric blocks
and why a completed result does not establish measurement accuracy.

## Requirements

- The measured v3 enclosure (`config/enclosure_v3_rig_geometry.json`) at its
  recorded foot extension, with the lens 95 mm above the supporting floor.
  A matching file alone does not prove that your physical unit matches it.
- Raspberry Pi 5, OV9281 with the OpenFlight high-speed driver installed,
  OPS243, IWR6843LEVM, sound trigger, and the LIS3DH connected. The
  inclinometer runs on every session; it is how ball height is solved.
- A 7-iron, balls, foot powder spray, and a cloth. Confirm the physical setup
  before starting; the [setup requirements](tester-setup-gate.md) list the
  approved sensor offsets and LIS3DH orientation.

## Before your first run

Once per Pi:

```bash
sudo apt update && sudo apt install -y swig liblgpio-dev python3-dev
```

`lgpio`, the Pi 5 GPIO library OpenFlight uses for the sound trigger, is
compiled from source and needs SWIG (a build-time code generator from the
Raspberry Pi OS repositories; it runs only during that build) and the lgpio
and Python headers. `start-tester.sh` checks for them and prints this line if
they are missing.

Set up the camera as in the [camera README](README.md#raspberry-pi-packages),
including the high-speed driver. `rpicam-hello --list-cameras` must list
`320x200` before you start.

Getting the study branch before it is merged: your clone's `origin` is the
upstream repository, which does not have it. Add the fork once:

```bash
git remote add fork https://github.com/HarjotDhanota/openflight.git
git fetch fork
git checkout -B feat/tester-capture-pilot fork/feat/tester-capture-pilot
```

These branch commands do not install unpublished local work. For the morning
source archive, use its `PI_START_HERE.md` and a fresh clone instead. Preserve
existing Pi configuration, source edits and recordings when updating; do not
reset a dirty checkout.

## Running it

Stop the normal kiosk (it holds the camera and radars), then from the
repository root:

```bash
bash scripts/start-tester.sh
```

Open `http://127.0.0.1:8765` in the browser on the Pi's screen.

The tester also writes a rotating service log to
`~/openflight_sessions/tester_pilot/tester-server.log`. It remains available
after the terminal or browser disconnects, and **Package everything** includes
it under `diagnostics/` with any rotated copies. Refreshing the page is safe:
the tester ID and active ladder state are restored from browser and server
state. If the ladder was deliberately stopped, use **Resume ladder**.

The runner holds each arm's exposure and gain fixed for the whole run;
auto-exposure is off by design. It reads the enclosure's inclinometer the
whole time, with the service the kiosk runs, shows the enclosure's tilt, and
applies it to the camera the kiosk's way; during **Capture swings** it hands
the sensor to the kiosk and takes it back afterwards. The OPS243 is expected on the GPIO UART
(`/dev/ttyAMA0`); pass `--radar-port <port>` to `start-tester.sh` if yours is
elsewhere. The first `start-tester.sh` builds the environment and takes a few
minutes; later starts are quick.

1. **Who and where.** A tester ID, indoors or outdoors, and one tape
   measurement: the radar window to the centre of the ball, in mm. That is
   everything you type; the frames carry the light level and any flicker. Keep
   the light the same for the whole session; if it changes, start a new tester
   ID.
2. **Arms, top to bottom.** Select an arm and **Start live view** first: it
   shows the camera exactly as that arm will capture, with a contrast-boosted
   copy beneath it, and you can change exposure and gain while it runs. At
   10000 µs, check the ball is in view and its edge is crisp; if the whole
   image is soft, turn the lens barrel until it is. With your tape distance
   entered, the ring is the ball at the size that distance gives, and the
   page warns if the picture alone disagrees by more than a quarter. Then,
   for each arm: **Find gain** (a few seconds on the static scene — it picks
   the gain that lights the frame correctly at that arm's exposure, records
   the light level, and solves the ball's range from the same frame so it can
   be compared with your tape), then **Capture swings**. The club is set to
   7-iron for you. Hit until the arm shows **5 accepted**, then press
   **Stop**. Stopping and starting again is fine: each capture run is kept
   separately and counted together.
   - If an arm says **lighting required**, hit its five swings anyway. Its
     acceptance rate at your light level is part of the answer.
3. **Package everything** with the [community contribution package
   workflow](community-contributions.md), then send the archive through the
   agreed channel. Raw data stays out of Git.

## Test suite

On the study page, fill in **Who and where**, then work down **Test suite**:

1. **A. Check the hardware.** Ready means the software and camera answered.
2. **B. Measure the light.** Runs the camera's light screen at both modes, about
   a minute each. Keep the room as you will hit in; it also proves each camera
   mode streams.
3. **C. Start the exposure ladder.** The page sets each exposure itself and shows
   which one you are on. Hit a normal shot, wait for the verdict, repeat. It moves
   on after 5 good swings, and skips exposures your light cannot support.
   On the 1280×800 exposures: spray the face before each swing; after it, hold
   the face about 0.5 m from the lens inside the dashed box, press
   **Photograph face**, then wipe it. Before leaving full resolution, the page
   holds the final capture for its photo. Photograph it or choose **Skip photo**
   before continuing; do not take another swing while this choice is pending.
   The kiosk then restarts in the next mode (about 20 s). A failed photo can be
   retried without losing the pending capture.
4. **D. Package the data.** If you have a TM4, Full Swing KIT or Mevo Gen 2
   export, choose it first. Copy the archive off the Pi on a USB stick; it can be
   about 1 GB. Use the [community contribution package
   workflow](community-contributions.md) to record consent and create a local,
   checksum-verified shareable archive.

Reloading the page restores the ladder display. If the ladder was stopped,
press **C** to resume. A pending final photo stays associated with its original
capture across Stop and Resume; photograph that same strike mark, or skip it
if the face has already been wiped or used for another swing. Skipped photos
are recorded with the capture in the data package.

### Record every attempt

Use the operator tally during either the ladder or a single-arm capture. Check
the selected arm and run, then press **Record swing** once for each physical
swing. If the system missed it, use **Record missed shot** instead; do not press
both for the same swing. Record warmups and false triggers with **Other event**.
**Undo last** removes the latest observation from the tally while preserving
its audit record in the package.

The selected run stays pinned when the ladder advances so the last swing can
still be recorded against its original run. Select the new run before recording
its swings. Saved runs remain available after Stop and page reload. If a save
is uncertain, confirm the pending entry with the retry control before recording
another; its original identity prevents a duplicate.

The tally compares recorded swings with logged sensor shots. A matching count
does not prove that the same shots were matched or that every swing was recorded.
Physical capture rate and accuracy remain unknown until coverage and independent
reference matching are established. Missing tallies show unknown values, not zero
swings. Use one tester service to write a capture directory at a time.

New sessions also preserve a source archive beside their logs, and exports verify
and retain it. This records the allowlisted source on disk at session startup,
including local edits, plus software versions. It does not certify loaded module
bytes or device firmware. Collection failures are recorded without stopping capture.

## The arms

The single-arm controls below the suite are for investigating one mode by hand.

| Arm | Mode | Exposure | Why it exists |
| --- | --- | --- | --- |
| 1 | 320×200 @ 450 | 300 µs | reference: 2× sampling, high frame rate |
| 2 | 320×200 @ 450 | 175 µs | arm 1 at a shorter exposure → blur against noise |
| 3 | 320×200 @ 450 | 87 µs | 1.5 px at the full 130 mph head speed → blur against noise |
| 4 | 640×400 @ 120 | 300 µs | arm 1 at 1:1's frame rate → frame rate alone |
| 5 | 1280×800 @ 120 | 300 µs | arm 4 at 1:1 sampling → pixels alone |

Exposure is not a setting you choose. 300 µs keeps a 7-iron's smear under
4 mm on the frames the estimators use; arms 2 and 3 are deliberately shorter
so the data shows what exposure costs. Gain is found per arm from a static
screen and stops at 12×, above which the sensor adds offset rather than
signal; an arm that needs more at your light is telling you its floor.

## What counts as accepted

The delivery estimator reports a named status for every swing. **Accepted**
means it produced a club delivery (`ok`, `fused`, `chained_high`,
`approach_high`). Everything else — `low_light`, `overexposed`,
`rejected_insufficient_features`, `no_impact`, … — is recorded with its reason
and counted against the arm. The page also names pairing problems: camera
frames without radar dumps, or counts that do not line up.

## What is deliberately absent

The swing study does not require each tester to collect checkerboard images.
Independent optical calibration is a separate maintainer bench step; the v3
nominal focal length is still uncalibrated, and transfer between units, focus
settings and readout modes is not established. No enclosure measurements:
the rig file carries them. No lux meter: the gain screen records a light index
that is comparable across every unit. No exposure or gain fields: both are
derived. No driver or wedge yet; no second camera; no fusion output on screen.

## What this pilot does not do

Apart from finding a room-lit resting ball, which goes upstream as its own
pull request, it changes no production camera default, driver table,
geometry, calibration constant, threshold, or fusion behaviour. Each of those
is a separate maintainer-approved pull request. Camera/radar agreement is a consistency
check, not proof of absolute accuracy.

## Troubleshooting

| What you see | Cause | Fix |
| --- | --- | --- |
| `fatal: ambiguous argument 'origin/feat/tester-capture-pilot'` | `origin` is the upstream repository; the study branch is on the fork | Add the fork as shown in *Before your first run* |
| `Failed to build lgpio` … `swig: No such file or directory` | Build tools missing | `sudo apt install -y swig liblgpio-dev python3-dev`, then start again |
| Find gain fails with `IndexError: list index out of range` in `Picamera2()`, or `rpicam-hello --list-cameras` has no `320x200` | The camera or its high-speed driver is not set up on this Pi | Follow the [camera README](README.md#raspberry-pi-packages) setup, then reboot |
| The live view is soft or fuzzy even at 10000 µs | The lens is out of focus; fitting the camera into the enclosure can turn it | Turn the lens barrel until the ball's edge is crisp, then run **Find gain** again |
| The ball line says `no ball of the size the distance gives` and that the floor is clipped white | The carpet or mat around the ball is saturated, so a white ball cannot be told from it | Lower the exposure or the gain until the floor has texture again |
| The ball line says `no ball of the size the distance gives` in a dim picture | The ball stands too little above the picture's noise at this exposure and gain | Check the radar-window distance, then raise the gain; the arm's own setting is still worth recording |
| `Removed virtual environment at: .venv` after running a repository script | Plain `uv run` uses the repository's Python 3.11 and rebuilds the environment without the Pi's camera library | Run repository scripts on the Pi with `.venv/bin/python`, not `uv run`; `start-tester.sh` restores the environment |
| `Creating virtual environment at: .venv` on a Pi that ran OpenFlight before | The runner rebuilds the environment when it cannot import `picamera2`; lgpio compiles again | Expected once; needs the build tools above |
| Every swing rejected with no ball speed | The OPS243 was not found | Pass `--radar-port` with your port (`/dev/ttyAMA0` for the GPIO UART, `/dev/ttyACM0` for USB) |
| An arm's counter stays at 0 while swings save | The estimator rejected them; the status histogram in the archive says why | Hit the five anyway if it reads `lighting required`; its acceptance rate is part of the result |
| Stopped mid-arm | Nothing is lost | Press **Capture swings** again; runs are kept separately and counted together |
| The page is slow, frozen, or disconnects | The browser, tester service, Wi-Fi, or Pi may have stalled independently | Preserve the session and run `tail -n 250 ~/openflight_sessions/tester_pilot/tester-server.log`; packaged data now carries this log automatically |
| Refreshing the page shows a held photo step | The ladder restored its durable boundary-photo checkpoint | Do not swing; use **Photograph face**, **Skip photo**, or **Resume ladder** if it says the ladder is stopped |
| Ladder verdict red: `frames: ... fps delivered` or `gap(s)` | The Pi could not keep up with the camera mode | Close other programs, check the power supply, run **A** again |
| Ladder verdict red: `controls: exposure ...` or `gain ...` | The camera did not take the exposure's setting | Press **C** again; the ladder carries on where it stopped |
| Ladder verdict red: `light: too dark` | This exposure is below what your light supports | Expected on the shortest exposures; the ladder skips the rest |
| Ladder verdict red: `light: ... clipped` | The ball area is washed out | Dim or move the light; this exposure will fail |
| Ladder verdict amber: `resting ball not found` | The camera could not pick the ball out without knowing its distance | The swing still counts; keep placing the ball in the same spot |
| `run the gain step for both modes first` | Step **B** did not finish for both modes | Run **B** again |

