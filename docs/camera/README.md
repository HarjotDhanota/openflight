# OV9281 High-Speed Camera

OpenFlight can capture a short, synchronized camera movie around impact. The
camera does not replace the OPS243 or IWR6843: OPS anchors speed and trigger
timing, the IWR6843 measures radar angles, and the camera provides a visual
record of the clubhead and early ball flight.

Camera capture is experimental. Camera-assisted horizontal launch, club path,
and attack angle must remain quality-gated until they are validated against a
launch-monitor source of truth.

The [speed contract](speed-contract.md) covers the OPS radial speed and the
experimental total-speed correction.

## Supported Hardware

The tested camera is an InnoMaker OV9281 monochrome global-shutter module on a
Raspberry Pi 5. Other OV9281 modules may expose different register programming
or lens geometry even when they use the same sensor.

Required hardware:

- Raspberry Pi 5 running Raspberry Pi OS.
- InnoMaker OV9281 monochrome global-shutter camera.
- Correct Pi 5 camera ribbon cable for the selected CAM/DISP connector.
- Rigid, focusable camera mount.
- Shared sound-trigger wiring on BCM17 when using OPS and IWR6843 capture.

Power the Pi off before connecting or disconnecting the ribbon cable. Confirm
that the cable contacts face the correct direction for both the Pi connector
and camera board before applying power.

## Mounting And Sensor Offset

Mount the camera rigidly and point it down the target line. The tested original
assembly placed the camera directly above the IWR6843 antenna center, about
`8.25 in` (`0.20955 m`) above the hitting surface. A laterally offset camera is
also supported: its position comes from the enclosure's rig file
(`--rig-geometry`), never from a flag.

The rig file gives the signed horizontal distance from the radar antenna center
to the camera lens optical center. The sign is defined while standing behind the
unit and looking down the target line:

- Positive: camera is to target-right of the radar center.
- Negative: camera is to target-left of the radar center.
- Zero: camera and radar optical centers share the same vertical centerline.

OpenFlight records this value with the session and uses it in camera-only ball
depth, camera/IWR horizontal launch, Club Path, and Attack Angle geometry.

This physical distance is different from the setup's horizontal offset. The meter
value describes where the camera is mounted; the degree value is the one
horizontal offset, −(target yaw)
(positive = boresight right of the target line; added to every horizontal angle,
camera and IWR alike; see
[target line reference](../how-it-works/positioning.md#target-line-reference)). Do not convert the physical separation into degrees, and do not use
the degree setting as a substitute for the measured distance.

The rig file also gives the forward separation: positive means the camera is
ahead of the IWR reference, toward the ball (**+0.030 m** in the shipped v3 rig
file). Club and ball
reconstruction use this offset in camera position and camera-to-ball distance;
the resting ball's slant range remains measured from the radar. The camera needs
a rig file: without one the kiosk refuses `--camera-capture`.

The useful image does not need to contain the golfer or the full shaft. It must
contain:

- The stationary ball and impact point.
- The clubhead for the final frames before impact.
- The clubhead through impact.
- The first several milliseconds of ball flight.

TrackMan-aligned 7-iron and 9-iron captures showed that a `200`-row view was
needed to retain both the final clubhead approach and early ball flight. The
tested high-speed `320x200` mode defaults to a vertically centered `(480,150)` crop.
A `150`-row crop was too tight for robust combined clubhead and ball-path
tracking. A raised `200`-row crop remains a future experiment, not part of the
checked-in driver.

This result has not yet been validated for wedges or driver. Use the alignment
preview before each new physical mounting configuration.

## Raspberry Pi Packages

Install the Raspberry Pi camera stack and preview dependency:

```bash
sudo apt update
sudo apt install -y rpicam-apps python3-picamera2 ffmpeg
```

The main `scripts/setup/setup.sh` installer also installs FFmpeg automatically
on Raspberry Pi systems when it is missing. Picamera2 remains an operating-
system package because it must match Raspberry Pi OS and its libcamera stack.

Install OpenFlight's optional camera image-processing dependency from the
repository root:

```bash
uv sync --extra camera
```

OpenCV is intentionally not installed for radar-only OpenFlight systems.

The OV9281 is not auto-detected. Enable it in `/boot/firmware/config.txt`
under `[all]`, using `dtoverlay=ov9281,cam0` when the ribbon is in CAM/DISP 0:

```text
dtoverlay=ov9281
```

Reboot after enabling or changing camera hardware:

```bash
sudo reboot
```

Verify that the camera is detected:

```bash
rpicam-hello --list-cameras
```

The standard Raspberry Pi driver should list the stock OV9281 modes. The
OpenFlight high-speed driver additionally lists `640x200`, `640x100`, and
`320x200` raw modes.

## High-Speed Driver

The Raspberry Pi kernel's stock OV9281/OV9282 driver does not expose the
cropped high-speed modes used by OpenFlight. The repository includes a narrow
kernel patch and installer under:

```text
drivers/ov9281/
scripts/setup/install_ov9281_high_speed_driver.sh
```

The experimental `320x200` register sequence is derived from InnoMaker's
[CAM-OV9281RAW-V2 reference repository](https://github.com/INNO-MAKER/CAM-OV9281RAW-V2)
and adapted to Raspberry Pi's upstream `ov9282` driver.

Install the driver on the Pi with:

```bash
cd ~/openflight
scripts/setup/install_ov9281_high_speed_driver.sh
sudo reboot
```

The installer resolves the stock driver source matching the running Raspberry
Pi kernel (from Raspberry Pi's `linux-source` package, or from its kernel branch
on GitHub when that package is not yet published), builds only the `ov9282`
module against the installed kernel headers,
backs up the stock module, installs the patched module, and runs `depmod`. It
also grants members of the `video` group access to the live vertical sensor
position control. Kernel upgrades require rebuilding the module for the new
kernel before the custom modes are available again.

Restore the stock module with:

```bash
cd ~/openflight
scripts/setup/install_ov9281_high_speed_driver.sh --restore
sudo reboot
```

Do not unload or replace the active camera module while the camera pipeline is
running. Stop OpenFlight and reboot after installing or restoring a driver.

## Capture Modes

Measured modes on the test Pi 5:

| Mode | Purpose | Observed cadence |
|---|---|---:|
| `640x400` | Full-field setup and baseline capture | about `288 FPS` |
| `640x200` | Wide impact strip | about `536 FPS` |
| `320x200` | Dense impact and early-flight capture | about `576 FPS` |
| `640x100` | Timing experiment only; vertically fragile | not recommended |

The production experiment should use the fixed `320x200` mode requested at
`450 FPS`. Higher requests require exposures too short for reliable golf
capture in the tested lighting environments.
The delivered cadence is expected to be lower than the request and is recorded
in each capture's `metadata.json`.

## Alignment Preview

Stop OpenFlight before opening the camera preview:

```bash
cd ~/openflight
CAMERA_WIDTH=320 \
CAMERA_HEIGHT=200 \
CAMERA_FPS=120 \
CAMERA_EXPOSURE_US=500 \
CAMERA_GAIN=2 \
scripts/hardware-test/preview_camera_alignment.sh
```

Use the preview to place the ball near the horizontal center and ensure the
clubhead and expected launch corridor remain within the vertical window. The
preview is rotated for the tested inverted camera mount.

There is no typed roll correction: level the camera physically. Saved raw
frames are never resampled, so calibration can be revised during offline replay.

## Camera Tab Controls

When `--camera-capture` is enabled, the Camera tab keeps the preview visible
beside an operator-control column. Exposure and analogue gain are selected
automatically from the resting ball (see *Automatic exposure* below). The tab
shows the current shutter and gain, why they are what they are (**Ball-driven**,
**Too bright**, **Too dark**, **Holding: no ball**, **Waiting for the ball**), the
blur ceiling, the ball's signal and peak, the label too-bright or too-dark clips
carry, and whether camera-assisted analysis is eligible. There is no manual
exposure override in the UI.

With the OpenFlight driver and the fixed `320x200` capture mode, **View up** and
**View down** move the real sensor window in safe 10-pixel steps. Each move
briefly restarts the camera and refills the pre-trigger ring. Wait for the tab
to report **Armed** before hitting. The centered starting position provides
70 output pixels of safe UI travel in either direction. The controls account
for the tested 180-degree camera mount, so the visible scene moves in the
direction printed on the button.

Resolution, requested frame rate, and pre/post-trigger timing are shown as
read-only capture provenance. Changing those values requires a controlled
camera restart and is intentionally not part of the live-control path.

## Automatic Exposure

The rule: **use the lowest exposure unless more is needed**, with the resting ball
as the light meter. The policy is `camera/swing_exposure.py`; its inputs and their
derivation are in `config/swing_exposure.json`.

- **Limits.** The kiosk reads each mode's exposure and gain range from the camera
  and records it in `~/.config/openflight/camera-sensor-limits.json`. A camera that
  reports no limits leaves the startup controls in place (status **unavailable**).
- **Blur ceiling.** The longest exposure that keeps the club sharp: 4 mm of smear on
  approach and 1 mm near impact at the measured club-edge speeds gives **294 µs**.
  The policy never goes past it.
- **Choosing a setting.** The ball's brightest part is aimed at 0.55 of clip, using
  the shortest exposure (in steps of about √2) that some gain within the cap reaches.
  Gain rises first; exposure lengthens only when the gain is capped.
  - Still clipping at the shortest exposure and gain 1: clips are labelled **too
    bright: shade the ball area (or an ND filter)**.
  - Still too dark at the ceiling with the gain capped: clips are labelled **too
    dark: add light**.
  - Neither label blocks anything: the shot is analysed and labelled.
- **Until locked.** About once a second, between clips, the kiosk measures the
  resting ball in the setup's box and sets the controls for the next swing. A ball
  between 0.40 and 0.75 of clip is left alone; a mild change waits for two looks
  that agree; a clipped or too-dark ball moves at once. A passing ball whose
  setting is held locks the controls.
- **Locked.** The controls are held still. After each shot (a clip an OPS shot
  claims; a false trigger's clip is not used), the ball in that clip's own resting
  frames is measured at the controls they were taken at, and any change is applied
  for the next swing, never while a clip is being collected. The change lands when
  the OPS shot claims the clip, a few seconds after impact and after the camera has
  re-armed; outdoor shots were 25–46 s apart, so the next swing gets it. With no shot for
  5 minutes, the live ball is measured once. No passing ball, a second change in a
  row, a moved box or a restarted search goes back to measuring once a second.
- **No ball.** The last setting is kept, with the reason. After about 3 s with no
  ball, the policy sweeps its range one step brighter, then darker, until the ball
  is found (status **Searching for the ball**); if none is, it holds the last good
  setting and says **no ball found in the box (searched the full range)**.
- **Decisions.** The last twenty, with their inputs, are in
  `auto_exposure.recent_decisions` on `/api/camera/exposure-quality`, each with its
  `trigger` (`seed`, `searching`, `after_shot` or `idle_recheck`); each clip's
  metadata carries the decision in force at its trigger and the `metering_state`.
- **Before the first ball.** `--camera-light-index` seeds the policy with the setup's
  measured light. Without it the kiosk uses the light remembered from its last
  ball-judged setting, else the startup `--camera-capture-exposure-us` ×
  `--camera-capture-gain`.

The last ball-judged setting and its light are stored at:

```text
~/.config/openflight/camera-exposure.json
```

What it means in practice (6 mm lens):

- **Indoors**, the policy typically ends at the blur ceiling with the gain capped,
  the ball above the gate but under the 0.55 target. More light is the only way
  to shorter exposures.
- **In full sun**, 1280×800 sits at the sensor's shortest exposure. 640×400, twice
  as bright, can clip there and is labelled too bright: shade the ball or fit an
  ND filter.

The standalone calibration sweep remains useful when diagnosing an unusual
lighting installation:

```bash
cd ~/openflight
uv run --no-project --python /usr/bin/python3 \
  python scripts/hardware-test/calibrate_camera_exposure.py \
  --rotate-180 \
  --fps 300 \
  --width 640 \
  --height 400 \
  --exposures-us 250,350,500,700,900,1100 \
  --gains 1,2,4 \
  --settle-ms 150
```

Prefer adding light to the hitting area over relying on long shutter times or
high gain. Both can reduce clubhead edge quality even when the preview appears
bright enough.

## Standalone Trigger Test

Use the clap-buffer test before starting OpenFlight:

```bash
cd ~/openflight
uv run --no-project --python /usr/bin/python3 \
  python scripts/hardware-test/test_camera_clap_buffer.py \
  --width 320 \
  --height 200 \
  --fps 450 \
  --pre-ms 150 \
  --post-ms 50 \
  --exposure-us 500 \
  --gain 2 \
  --rotate-180 \
  --captures 5
```

Check that every capture reports the expected pre/post frame counts, plausible
delivered FPS, and `gaps=0`.

## Running OpenFlight

Enable synchronized rolling capture with `--camera-capture`:

```bash
scripts/start-kiosk.sh \
  --debug \
  --iwr6843 \
  --camera-capture \
  --camera-capture-width 320 \
  --camera-capture-height 200 \
  --camera-capture-fps 450 \
  --camera-capture-pre-ms 150 \
  --camera-capture-post-ms 50 \
  --camera-capture-rotate-180 \
  --session-location home
```

When capture is enabled, OpenFlight keeps a rolling pre-trigger frame buffer and freezes it
from the same sound-trigger event used by the radar pipeline.

## Shot Replay

A shot with a matched high-speed camera capture exposes **Replay** in the Live
header and a play control in its Shots row. Replay opens a full-screen,
touch-friendly 60 FPS slow-motion player with play/pause, restart, scrubbing,
looping, selectable `1x`, `0.5x`, `0.25x`, and `0.1x` playback speeds, and an
impact marker derived from the recorded trigger frame. Each replay carries its
saved-frame orientation, so the player applies the operator-facing left/right
correction only when needed and never flips a capture twice. Saved frames used
by camera geometry remain unchanged.

The browser-ready H.264 MP4 is intentionally lazy. Shot processing registers
only the existing `frames.npz`; OpenFlight does not run FFmpeg until the user
selects Replay. The first request creates `replay.mp4` atomically beside the raw
capture, and later requests reuse that file. Closing the player while it is
preparing does not affect the shot or raw frames. Preparation and playback
failures are shown as retryable player states, while server-side failures are
logged without interrupting launch-monitor operation. FFmpeg preparation is
serialized across replays to avoid simultaneous CPU and memory spikes on the
Raspberry Pi.

## Saved Artifacts

Camera captures are written under:

```text
~/openflight_sessions/<location>/camera/camera_<timestamp>_<sequence>/
```

Each capture contains:

- `frames.npz`: grayscale frames and per-frame timing/control metadata.
- `metadata.json`: delivered cadence, frame gaps, brightness, timing, settings,
  and the capture-time automatic-exposure decision.
- `first.pgm`: first buffered frame.
- `trigger.pgm`: frame nearest the hardware trigger.
- `last.pgm`: final post-trigger frame.
- `replay.mp4`: optional 60 FPS slow-motion video, created only after the first
  manual Replay selection and then cached.

The session JSONL contains a `camera_capture` entry linking the shot number to
the camera directory. Keep the JSONL, OPS capture, IWR6843 dump, and camera
directory together when copying a session for offline analysis.

## Troubleshooting

### No camera detected

First confirm `/boot/firmware/config.txt` has the `dtoverlay=ov9281` line.

1. Stop OpenFlight.
2. Power the Pi off.
3. Reseat both ends of the ribbon cable.
4. Boot and run `rpicam-hello --list-cameras`.

### `picamera2` import fails under `uv`

Raspberry Pi OS installs Picamera2 for the system Python. Run hardware scripts
with:

```bash
uv run --no-project --python /usr/bin/python3 python <script>
```

For OpenFlight itself, first confirm `python3-picamera2` is installed through
`apt`, then run `uv sync --extra camera`. The camera runtime exposes Raspberry
Pi OS's package directory to the OpenFlight environment automatically.

### Requested high-speed mode is missing

The custom module was not built for the running kernel, or a kernel update
restored the stock driver. Re-run the high-speed driver installer and reboot.

### Frames are dark

Verify that the lens cap is removed and that light reaches the clubhead path,
not just the stationary ball. If the Camera tab remains on **Lighting needed**
at the brightest validated setting, add or redirect light. Preview and raw
capture continue while radar-only measurements remain active.

### Highlights are solid white

Redirect intense light away from reflective surfaces and the ball. The
automatic controller will move darker, but clipped ball and club pixels cannot
provide reliable centroids even when the image looks bright enough to a person.

### Frame gaps increase

Stop preview applications, verify that only one process owns the camera, and
check the Pi for thermal throttling. Capture to RAM first; persistence happens
after the frame window freezes.

### Camera works once and then remains busy

Stop OpenFlight cleanly and look for stale `rpicam-*`, `ffplay`, or Python
processes. Reboot rather than hot-unloading the camera kernel module.

## Current Limitations

- The `320x200` crop has only been evaluated with 7-iron and 9-iron TrackMan shots.
- Camera pose is not yet a complete metric calibration.
- Camera-assisted club path and attack angle remain experimental.
- A kernel update requires rebuilding the custom module.
- The tested down-the-line view cannot independently measure downrange speed;
  OPS remains necessary for converting image-plane motion into delivery angles.
