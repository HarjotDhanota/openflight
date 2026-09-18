# Tester capture pilot

Testers collect paired camera and radar recordings. Maintainers analyse them.
This pilot deliberately ships no estimated club or ball output to the tester:
the page reports whether a capture saved usable data, nothing more.

## Requirements

- **Cormac's enclosure.** The pilot validates one enclosure design first.
  Results from other enclosures cannot be pooled with it.
- Raspberry Pi 5, OV9281 global-shutter camera, OPS243 Doppler radar,
  IWR6843LEVM, and a working sound trigger.
- The camera and radar must both be present. A camera-only session is not
  paired data and cannot answer the questions this pilot asks.

## Running it

Stop the normal kiosk first, then from the repository root:

```bash
bash scripts/start-tester.sh
```

Open `http://127.0.0.1:8765` on the Pi. The page runs four steps in order:

| Step | What it does |
| --- | --- |
| Preflight | Records commit, kernel, detected camera modes, and throttling state. |
| Checkerboard views | Auto-triggers 12 captures while you reposition a printed checkerboard. |
| Exposure screen | Sweeps exposure and gain on a static scene and scores brightness and clipping. |
| Capture paired swings | Starts the normal kiosk with pinned settings and keeps running while you hit shots. Press **Stop** when done. |

**Capture paired swings** runs `scripts/start-kiosk.sh` with `--debug`,
`--iwr6843`, and `--camera-capture`. `--debug` is what retains the raw IWR
`.l3dump` files; without it the radar side of the pairing is lost.

The service exposes only these fixed actions. It does not accept arbitrary
commands from the browser.

## Measurements

Enter the measured camera-lens-optical-centre to RX-midpoint vector, the camera
and radar heights, and the ball-to-antenna distance. They are written into
`tester.json` and shipped with the archive.

The pilot records these numbers; it does not solve geometry from them and it
does not ship enclosure constants. The previous v42 enclosure geometry is
invalid for this build. Camera intrinsics are solved off-device from the
checkerboard views, so intrinsics are per-mode: recapture them if you change
camera mode or refocus.

## Checking and sending

**Check and send** reports camera captures holding frames, radar dumps,
checkerboard views, and exposure runs, plus any problems — missing dumps,
captures that saved no `frames.npz`, camera and radar counts that do not pair
up, or measurements that were never recorded.

**Package data** writes one uncompressed archive. Send the whole archive, not
just video: it carries settings, measurements, camera frames, radar dumps, and
logs. Raw recordings stay out of Git.

## What this pilot does not do

- It does not change production camera defaults, driver tables, enclosure
  geometry, calibration constants, thresholds, or fusion behaviour. Each of
  those is a separate maintainer-approved pull request.
- It does not show estimated impact location, face angle, club path, or any
  other fusion output.
- Camera/radar agreement is a consistency check, not proof of absolute
  accuracy.

Disclose substantive AI assistance under `AI-POLICY.md`. Never describe replay
or simulation as hardware validation.
