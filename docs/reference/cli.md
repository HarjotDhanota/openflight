---
icon: lucide/terminal
---

# CLI Flags

Every flag accepted by the server, grouped by subsystem.

`scripts/start-kiosk.sh` accepts most of these and forwards them. Use
`--dry-run` to print the exact command the script would run:

```bash
scripts/start-kiosk.sh --iwr6843 --dry-run
```

!!! note "Generated from the source"

    This table is derived from the argparse definitions in
    `src/openflight/server.py`. If a flag here disagrees with the code, the
    code is right — please open an issue.

## Server & web

Binding, ports, and debug output.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--mock`, `-m` | flag | Run in mock mode without radar |
| `--mock-swing-speed` | flag | Run swing speed training mode with simulated reps and no OPS radar |
| `--mock-enrichment-ms` | float; default `0.0` | With --mock, simulate IWR6843/camera enrichment taking MS milliseconds: shots arrive provisional, then final with direction data (default: off) |
| `--host` | default `0.0.0.0` | Host to bind to (default: 0.0.0.0) |
| `--web-port` | int; default `8080` | Web server port (default: 8080) |
| `--startup-status-file` | path | Write structured initialization progress for the optional kiosk splash |
| `--debug`, `-d` | flag | Enable verbose FFT/CFAR debug output |
| `--radar-log` | flag | Log raw radar data to console (Python logging) |
| `--show-raw` | flag | Show raw radar readings in console (signed values) |

## OPS243 radar & transport

Serial port, baud, and sample rate.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--port`, `-p` | — | Serial port for radar |
| `--ops-baud` | int | — |
| `--sample-rate` | int; default `30` | Radar sample rate in ksps (default: 30). Lower = longer buffer but lower max speed. 25=174mph/164ms, 27=187mph/152ms |

## Trigger & capture

How a capture is initiated and framed.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--trigger` | choices: `hardware`, `sound`, `speed`; default `sound` | Trigger strategy; hardware is OPS-only |
| `--trigger-threshold`, `--speed-trigger-threshold`, `--trigger-speed` | float | Internal or host speed-trigger threshold in mph; hardware default `25` |
| `--trigger-magnitude` | int; default `25` | OPS243 internal trigger magnitude (`SMn`, 1-2000) |
| `--pre-trigger-segments` | int; default `6` | Internal hardware-trigger pre-trigger segments (`S#n`, 0-32) |
| `--sound-pre-trigger` | int; default `16` | Pre-trigger segments S#n, 0-32 (default: 16 = 50/50 split, each segment ~4.27ms at 30ksps) |

## IWR6843 angle radar

The supported angle radar.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--iwr6843` | flag | Enable TI IWR6843 L3 capture and LCMF-v2 vertical launch angle (per-antenna paths from the rig file). Requires `--rig-geometry`. |
| `--rig-geometry` | path; no default in the server | Enclosure rig file; the radar's height, tilt and offset and the camera's height and offsets come only from it (required with `--iwr6843` and `--camera-capture`). `start-kiosk.sh` passes `config/enclosure_v3_rig_geometry.json` unless another file is given. |
| `--iwr6843-port` | — | TI serial port (auto-detect by default) |
| `--iwr6843-config` | default `config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg` | TI RF config matching the flashed L3 firmware |
| `--iwr6843-cal` | default `config/iwr6843_calibration_reference.json` | TI complex array/range calibration JSON |
| `--iwr6843-trigger-pin` | int; default `17` | BCM GPIO receiving the shared sound-trigger edge (default: 17) |
| `--iwr6843-ball-height-m` | float; default one ball radius (`0.02135`) | Ball-center height above the surface it rests on (default: one ball radius, a ball on the ground; a teed ball sits higher) |
| `--iwr6843-tx-order` | choices: `auto`, `normal`, `reversed`; default `auto` | TI TDM chirp order; auto reads the chirp masks from the cfg |
| `--iwr6843-capture-timeout` | float; default `16.0` | Maximum seconds an OPS shot waits for its TI UART dump |
| `--iwr6843-output-dir` | — | Raw TI dump directory when --debug is enabled (default: <session-log-dir>/iwr6843) |
| `--iwr6843-horizontal-phase-reference-rad` | float | Static target-line phase measured by horizontal aim calibration. Subtracted from the TX2 horizontal proxy before angle conversion. |

## Inclinometer

LIS3DH enclosure tilt compensation.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--inclinometer` | flag | Enable LIS3DH enclosure tilt compensation for the camera and the IWR6843 |
| `--lis3dh-mount-file` | path; default `~/.config/openflight/lis3dh-mount.json` | This unit's checked LIS3DH mount, saved by the orientation check; without the file the rig file's `lis3dh_mount_yaw_deg` stands |

## Unit setup

The setup itself (the ball box, the aim, the resting ball's range, the net range and
the lens height) is not a flag. It comes only from the unit's setup record
([`setup.json`](configuration.md#configopenflightsetupjson)), which the setup screen
writes and the kiosk applies between shots, without a restart.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--setup-file` | path; default `~/.config/openflight/setup.json` | The unit's setup record. A missing or unreadable file starts with nothing set; a section confirmed against another lens, rig file or camera tilt starts stale and is not applied |
| `--camera-tilt-calibration-path` | path; unset | The unit's per-lens camera-tilt file: each swing with a measured IWR launch point and the resting ball's row adds a pair to it; two that agree calibrate the tilt |
| `--camera-vertical-offset-deg` | float; unset | The unit's calibrated camera tilt (mount pitch plus principal-point row), applied on swings with each shot's LIS3DH pitch; recorded in session_start |
| `--camera-vertical-offset-source` | unset | Where `--camera-vertical-offset-deg` came from (`unit_calibration`). Only a calibrated tilt narrows the setup's box margin; recorded |

The old setup flags are refused: `--camera-ball-box`, `--camera-hitting-zone`,
`--camera-setup-ball`, the `--target-line-*` flags,
`--camera-capture-horizontal-offset-deg`, `--iwr6843-azimuth-offset-deg`, every
`--iwr6843-tee-*` flag, `--tee-side-offset-m`, `--iwr6843-net-m` / `--net-range-m`,
`--iwr6843-net-range-source`, `--solved-camera-height-m`, the
`--scene-lens-height-solved-*` flags, `--camera-capture-mount-height-m`,
`--camera-capture-lateral-offset-m`, `--camera-capture-forward-offset-m`,
`--iwr6843-tilt-deg`, `--iwr6843-radar-height-m`, `--inclinometer-zero-offset` and
`--camera-capture-roll-deg`.

## Ballistics & spin

Carry model and spin handling.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--ballistics` | flag | Use the physics-based carry simulator (drag + Magnus, RK4). This is the default; shots without a vertical launch angle fall back to the legacy table estimator. |
| `--no-ballistics` | flag | Disable the physics simulator and use the legacy carry table for all shots. |
| `--calculated-spin` | flag | Replace radar-measured spin with the kinematic estimate (170*v*sin(LA)^1.2) when the launch angle was measured. The 24 GHz OPS return carries no usable spin line (see src/openflight/spin_estimate.py); the measured value is kept in spin_rpm_measured for offline scoring |

## Swing speed

Club-only training mode.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--swing-speed` | flag | Run club-only swing speed training mode (no impact or ball required) |
| `--swing-speed-threshold` | float; default `30.0` | Outbound speed threshold that starts a swing speed rep (default: 30 mph) |
| `--swing-speed-max` | float; default `130.0` | Maximum plausible swing speed accepted from OPS reports; use 0 to disable (default: 130 mph) |
| `--swing-speed-min-readings` | int; default `3` | Minimum qualifying radar readings required to count a swing speed rep (default: 3) |
| `--swing-speed-single-peak` | float; default `60.0` | Peak speed that can count as a swing from one radar reading (default: 60 mph) |
| `--swing-speed-num-reports` | int; default `8` | Number of OPS speed candidates to report per sample cycle (default: 8) |
| `--swing-speed-end-ms` | float; default `1000.0` | Milliseconds below threshold before ending a swing speed rep (default: 1000) |
| `--swing-speed-cooldown-ms` | float; default `750.0` | Cooldown after a swing speed rep before accepting another (default: 750) |
| `--swing-speed-rejected-cooldown-ms` | float; default `100.0` | Cooldown after an ignored short motion before re-arming (default: 100) |

## Logging & session data

Where session logs go and what they capture.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--session-location`, `-l` | default `range` | Location identifier for session logs (e.g., 'range', 'course', 'home') |
| `--log-dir` | — | Directory for session logs (default: ~/openflight_sessions) |
| `--profiles-path` | path | Profile store (default: `OPENFLIGHT_PROFILES_PATH` or `~/.config/openflight/profiles.json`) |
| `--no-logging` | flag | Disable session logging |

## Simulators & power

Outbound connectors and battery status.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--battery` | — | Show battery and external-power status using the selected provider (`geekworm`, or `mock` for a simulated battery) |
| `--sim` | flag | Enable simulator connectors from config/sim.json (GSPro / OpenGolfSim / PAR-TEE). Off by default. |
| `--ble` | flag | Advertise shots over Bluetooth LE for the OpenFlight iOS app ([iOS BLE](../ios-ble.md)) |

## High-speed camera capture

Optional rolling-buffer capture and replay. See [camera setup](../camera/README.md).

| Flag | Type / default | Description |
| --- | --- | --- |
| `--camera-capture` | flag | Enable high-speed rolling-buffer capture and replay |
| `--camera-shot-analysis` | flag | Refused with an error: it takes ball distance from pixel size and OPS speed, which the sensor roles do not allow |
| `--camera-capture-width` | int; default `640` | Capture width |
| `--camera-capture-height` | int; default `400` | Capture height |
| `--camera-capture-fps` | float; default `300` | Capture frame rate |
| `--camera-capture-pre-ms` | float; default `150` | Milliseconds retained before the trigger |
| `--camera-capture-post-ms` | float; default `50` | Milliseconds retained after the trigger |
| `--camera-capture-exposure-us` | int; default `1000` | Startup exposure. Automatic exposure moves it inside the blur ceiling (294 µs), then re-checks the resting ball before every swing ([automatic exposure](../camera/README.md#automatic-exposure)) |
| `--camera-capture-gain` | float; default `4.0` | Startup analogue gain; automatic exposure keeps it within the sensor's cap |
| `--camera-light-index` | float; unset | The setup's measured light: the 1280×800 resting ball's DN above black per µs × gain. It seeds the automatic swing exposure before the first resting-ball look; 640×400 doubles it (measured). Unset, the light remembered from the last ball-judged setting, else the startup controls |
| `--camera-armed-profile` | path | Qualified armed exposure profile. Capture refuses exposure or gain above its ceiling. There is no default ceiling. |
| `--camera-lens-id` | `stock-2.8mm`, `6mm` or `8mm`; unset | The lens on the unit ([lenses](configuration.md#configlensesjson)). Its focal length (nominal, or from a checkerboard calibration kept for it) replaces the rig file's for every camera metric; recorded in session_start and every clip's metadata. Unset, the rig file's own (stock) lens, recorded as such |
| `--camera-capture-stream` | `raw` or `main-y`; default `raw` | Camera stream to persist |
| `--camera-capture-scaler-crop` | `X,Y,W,H` | Optional Picamera2 scaler crop |
| `--camera-capture-rotate-180` | flag | Rotate saved frames 180 degrees |
| `--camera-capture-mirror-horizontal` | flag | Mirror saved frames left-to-right after rotation |

## K-LD7 (deprecated)

Retained for existing builds only. See [Legacy (K-LD7)](../legacy/index.md).

| Flag | Type / default | Description |
| --- | --- | --- |
| `--kld7` | flag | [DEPRECATED] Enable K-LD7 vertical angle radar (launch angle) |
| `--kld7-port` | — | K-LD7 vertical serial port (auto-detect if not specified) |
| `--kld7-angle-offset` | float; default `1.5` | K-LD7 vertical boresight offset in degrees. Not user-measurable without a corner reflector; 1.5 is the calibrated default for the standard mount (default: 1.5) |
| `--kld7-mount-tilt` | float | K-LD7 vertical radar mount tilt in degrees. REQUIRED with --kld7 — measure it with a phone inclinometer against the radar face; there is no default because a wrong tilt silently corrupts the launch angle |
| `--kld7-ball-distance` | float; default `5.0` | Radar-to-tee distance in feet (default: 5.0) |
| `--net-distance` | float; default `10.0` | Ball-to-net/screen distance in feet (two_ray). For nets beyond the ~11ft FSK range wrap, far-flight frames are de-aliased and kept instead of dropped (default: 10.0; nets at/inside the wrap are unaffected). |
| `--kld7-radar-height-inches` | float; default `4.0` | K-LD7 radar height above the ball in inches, used by the ball-speed cosine correction geometry (default: 4.0) |
| `--kld7-vertical-raw` | flag | TEST MODE: show the raw vertical launch angle for every shot the estimator produces, bypassing all display guardrails (plausibility, soft-lane, estimator-agreement, confidence floor). Default off. |
| `--kld7-horizontal` | flag | [DEPRECATED] Enable K-LD7 horizontal angle radar (club path) |
| `--kld7-horizontal-port` | — | K-LD7 horizontal serial port |
| `--kld7-horizontal-offset` | float | K-LD7 horizontal angle offset in degrees (default: 0.0) |

## Wrapper-only flags

Handled by `scripts/start-kiosk.sh` itself rather than passed through.

| Flag | Description |
| --- | --- |
| `--dry-run` | Print the command that would run, then exit |
| `--startup-splash` | Show component progress while the kiosk starts |
| `--startup-splash-port` | Port used by the temporary splash server |
| `--port`, `--web-port` | Set the kiosk web port |
| `--radar-port`, `--ops-port` | Forward the OPS serial port as the server's `--port` |
| `--buffer-split` | Buffer split preset (`balanced`, `post-heavy`, `pre-heavy`) or raw segment count |
| `--no-browser` | Start the server without opening the kiosk window; `OPENFLIGHT_NO_BROWSER=1` does the same |

## Related

- [Running & modes](../using/running.md) — the common invocations
- [Configuration files](configuration.md) — settings that are not flags
- [Constants](constants.md) — values compiled in rather than passed
