# Camera mode study — tester guide

You collect paired camera and radar recordings across five camera arms (three
exposures at 320×200, then two further readout modes) with a 7-iron. We analyse
them against `mode-study-analysis.md`. The page never shows estimated club or
ball numbers; it shows whether each swing saved usable, paired data, and how
many of the five each arm has accepted.

## Requirements

- The v3 enclosure (`config/enclosure_v3_rig_geometry.json`), on its static
  feet. The geometry comes from that file; you measure nothing.
- Raspberry Pi 5, OV9281 with the OpenFlight high-speed driver installed,
  OPS243, IWR6843LEVM, sound trigger, and the LIS3DH connected. The
  inclinometer runs on every session; it is how ball height is solved.
- A 7-iron. Nothing else for this study.

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

To update later: `git fetch fork && git reset --hard fork/feat/tester-capture-pilot`.

## Running it

Stop the normal kiosk (it holds the camera and radars), then from the
repository root:

```bash
bash scripts/start-tester.sh
```

Open `http://127.0.0.1:8765` in the browser on the Pi's screen.

The runner holds each arm's exposure and gain fixed for the whole run;
auto-exposure is off by design. The OPS243 is expected on the GPIO UART
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
   image is soft, turn the lens barrel until it is. Then, for each arm:
   **Find gain** (a few seconds on the static scene — it picks the gain that
   lights the frame correctly at that arm's exposure, records the light level,
   and solves the ball's range from the same frame so it can be compared with
   your tape), then **Capture swings**. The club is set to 7-iron for you. Hit until the arm shows **5
   accepted**, then press **Stop**. Stopping and starting again is fine: each
   capture run is kept separately and counted together.
   - If an arm says **lighting required**, hit its five swings anyway. Its
     acceptance rate at your light level is part of the answer.
3. **Package everything** and send the archive through the agreed channel. Raw
   data stays out of Git.

## The arms

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

No checkerboard: focal length is a property of the camera module, lens and
mode, calibrated once by the maintainer and shared. No enclosure measurements:
the rig file carries them. No lux meter: the gain screen records a light index
that is comparable across every unit. No exposure or gain fields: both are
derived. No driver or wedge yet; no second camera; no fusion output on screen.

## What this pilot does not do

It changes no production camera default, driver table, geometry, calibration
constant, threshold, or fusion behaviour. Each of those is a separate
maintainer-approved pull request. Camera/radar agreement is a consistency
check, not proof of absolute accuracy.

## Troubleshooting

| What you see | Cause | Fix |
| --- | --- | --- |
| `fatal: ambiguous argument 'origin/feat/tester-capture-pilot'` | `origin` is the upstream repository; the study branch is on the fork | Add the fork as shown in *Before your first run* |
| `Failed to build lgpio` … `swig: No such file or directory` | Build tools missing | `sudo apt install -y swig liblgpio-dev python3-dev`, then start again |
| Find gain fails with `IndexError: list index out of range` in `Picamera2()`, or `rpicam-hello --list-cameras` has no `320x200` | The camera or its high-speed driver is not set up on this Pi | Follow the [camera README](README.md#raspberry-pi-packages) setup, then reboot |
| The live view is soft or fuzzy even at 10000 µs | The lens is out of focus; fitting the camera into the enclosure can turn it | Turn the lens barrel until the ball's edge is crisp, then run **Find gain** again |
| `Creating virtual environment at: .venv` on a Pi that ran OpenFlight before | The runner rebuilds the environment when it cannot import `picamera2`; lgpio compiles again | Expected once; needs the build tools above |
| Every swing rejected with no ball speed | The OPS243 was not found | Pass `--radar-port` with your port (`/dev/ttyAMA0` for the GPIO UART, `/dev/ttyACM0` for USB) |
| An arm's counter stays at 0 while swings save | The estimator rejected them; the status histogram in the archive says why | Hit the five anyway if it reads `lighting required`; its acceptance rate is part of the result |
| Stopped mid-arm | Nothing is lost | Press **Capture swings** again; runs are kept separately and counted together |

