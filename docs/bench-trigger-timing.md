# Bench: what the trigger timing really is

The pipeline walks back from the sound trigger to contact by the sound's flight
time (1.575 m / 343 m/s = 4.59 ms, `impact_zone.contact_frame_from_trigger`).
The August session's ball motion says the trigger lands only 0.5–4 ms after
contact and jitters ±1.5 ms — a frame of error, and ~3° of attack angle. Two
suspects, two bench measurements. **Neither script changes a pipeline default.**

## 1. Camera stamp latency (`scripts/hardware-test/camera_latency_led.py`)

*Does `host_timestamp_ns` lag the exposure it names?*

**Wiring.** LED anode → SEN-14262 **GATE**, cathode → 330 Ω → GND (the gate
sources only a few mA; keep it under 5 mA). Point the LED into the camera,
defocused, filling a few dozen pixels. Nothing else in the frame may change.

**Run.** Arm the camera and clap near the microphone 10 times:

```bash
uv run python scripts/hardware-test/camera_latency_led.py --capture --repeat 10
```

It drives the shipped `CameraCaptureRuntime` (same ring, same GPIO listener,
same `frames.npz`), with auto-exposure **off** so the exposure window is fixed.
If picamera2/GPIO is unavailable, capture with `scripts/start-kiosk.sh` instead
and analyse offline:

```bash
uv run python scripts/hardware-test/camera_latency_led.py --archive ~/openflight_sessions
```

**Reading it.** The LED is found automatically as the pixel box with the largest
brightness step (`--roi X,Y,W,H` overrides). The first lit frame is only partly
exposed to the LED, so its brightness over the plateau is the fraction of the
exposure the LED was on for; that places the onset inside the frame. Output is a
row per capture and one verdict: `camera stamps lag exposure by X.XX ms
(MAD Y.YY, n=N)`.

**Expect.** 0–1 ms if the stamp is honest. A median near 2–3 ms with a small MAD
is the whole discrepancy, and becomes a constant config value. A large MAD means
it is not a constant and cannot be one.

## 2. What the GATE fires on (`scripts/hardware-test/gate_timing_probe.py`)

*Airborne sound, or shock through the mat/kickstand?*

**Wiring.** SEN-14262 **AUDIO** → USB sound card line-in (or an ADC) — the Pi has
no analog input. **GATE** → a spare BCM pin (as well as HOST_INT). Shared ground.
`uv pip install openflight[bench]` for `sounddevice`.

```bash
uv run python scripts/hardware-test/gate_timing_probe.py --gpio 27 --audio 1 --seconds 60 --out ~/bench/gate
uv run python scripts/hardware-test/gate_timing_probe.py --edges ~/bench/gate/edges.csv --wav ~/bench/gate/mic.wav
```

Hit 10 balls. Per impact it reports the envelope onset (first sample above
8× the noise floor), the GATE edge, their difference, and the airborne
expectation for `--distance-m`.

**Expect.** GATE **after** the onset by a few tenths of a millisecond = an
ordinary comparator delay, path is airborne. GATE **before** the onset
(`faster_than_air`) = the gate is riding structure-borne shock and the 4.59 ms
walk-back is wrong by construction.

No ADC? `--archive ~/openflight_sessions` pairs edges with each capture's
`trigger_host_timestamp_ns` instead, which measures the host's edge-to-stamp
latency only.

## Feeding the results back

- A constant, low-MAD camera latency → a config value applied to
  `host_timestamp_ns`; the acoustic walk-back stays.
- `faster_than_air`, or a camera latency that does not explain the gap →
  `contact_frame_from_ball` becomes the default contact clock and the acoustic
  walk-back becomes the fallback for shots with no usable ball track.
