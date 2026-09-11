# Bench: what the trigger timing really is

The pipeline walks back from the sound trigger to contact by the sound's flight
time (1.575 m / 343 m/s at 20 C = 4.59 ms, `impact_zone.contact_frame_from_trigger`;
`--temperature-c` moves that speed via `openflight.acoustics`). The August
session's ball motion says the trigger lands only 0.5–4 ms after contact and
jitters ±1.5 ms — a frame of error, and ~3° of attack angle. Two suspects: the
camera's stamps lag the exposure, or the GATE fires on something faster than
airborne sound. **Neither script changes a pipeline default.**

> **Set the exposure near the frame interval for every run below.** A global
> shutter is blind between exposures; at 800 µs and 467 fps that dead gap is
> 1.34 ms — larger than the effect being measured. An event landing in it can
> only be bounded (reported as the `±ms` column). At `--exposure-us 2000` the
> gap is 0.14 ms.

## 1. The walk-back, on one clock — `camera_latency_led.py --shot` (primary)

*Does the GATE really fire 4.59 ms after contact?* One wiring, no ADC.

**Wiring.** LED anode → SEN-14262 **GATE**, cathode → 330 Ω → GND. Point it into
the camera, defocused, over a few dozen pixels well away from the ball. Nothing
else in frame may change. Hit 10 balls with the kiosk running
(`scripts/start-kiosk.sh` writes the archives), then:

```bash
uv run python scripts/hardware-test/camera_latency_led.py --shot \
    --archive ~/openflight_sessions --distance-m 1.575
```

**Reading it.** Contact is the ball leaving the tee — found with
`club_motion.detect_reference_ball` on the pre-trigger frames, then the first
frame whose disc changes, placed inside its own exposure. The GATE edge is the
LED lighting, fitted the same way. Both come off the **same camera clock**, so
any stamp latency cancels exactly. Verdict: `gate fires X.XX ms after contact
(MAD Y.YY, n=N); airborne expectation 4.59 ms -> airborne | early | late`.

**Expect.** `airborne` (within ±0.3 ms of 4.59) means the shipped walk-back is
right and the August residual is elsewhere. `early` means the trigger beats
airborne sound — it is coming through the mat or the enclosure. A large MAD
means it is not a constant, and no single correction can fix it.

## 2. Camera stamp latency — `camera_latency_led.py` (clap mode)

*Do the host stamps lag the exposure they name?* Same wiring, no ball.
```bash
uv run python scripts/hardware-test/camera_latency_led.py --capture --repeat 10
uv run python scripts/hardware-test/camera_latency_led.py --archive ~/openflight_sessions
```

`--capture` drives the shipped `CameraCaptureRuntime` with auto-exposure off.
Clap near the microphone; the LED onset is compared with the capture's own
`trigger_host_timestamp_ns`. Verdict: `camera stamps lag exposure by X.XX ms
(MAD Y.YY, n=N)`; expect 0–1 ms if the stamp is honest. This characterises the
stamps only.

## 3. The piezo alternative — `gate_timing_probe.py --contact-channel`

Use when the camera route is unavailable, or to confirm it independently.
**Wiring.** SEN-14262 **AUDIO** → USB sound card left in; a piezo taped under the
mat beside the ball → right in (its delay to contact is negligible). **GATE** →
a spare BCM pin as well as HOST_INT. Shared ground.
`uv pip install openflight[bench]` for `sounddevice`.

```bash
uv run python scripts/hardware-test/gate_timing_probe.py --gpio 27 --audio 1 \
    --channels 2 --seconds 60 --out ~/bench/gate
uv run python scripts/hardware-test/gate_timing_probe.py --edges ~/bench/gate/edges.csv \
    --wav ~/bench/gate/bench.wav --contact-channel 1
```

Per impact: `heard-contact_ms` (4.59 ms if the microphone hears the ball through
the air) and `gate-contact_ms` — the true walk-back — classified as in §1.

**Without a piezo** (`--wav` alone) the probe compares GATE with the envelope
onset of the *same* AUDIO signal GATE is derived from, which can only measure the
board comparator's threshold delay (expect 0 to +0.5 ms; negative means our
8× noise detector is the conservative one). It **cannot** test the
structure-borne hypothesis. With no ADC at all, `--archive` pairs edges with each
capture's `trigger_host_timestamp_ns`: host edge-to-stamp latency only.

## Feeding the results back

- §1 (or §3) says `airborne` with a small MAD → the shipped acoustic walk-back
  stands; look elsewhere for the August residual.
- §1 says `early`, or the MAD is a frame or more → `contact_frame_from_ball`
  becomes the default contact clock and the acoustic walk-back becomes the
  fallback for shots with no usable ball track.
- §2 finds a constant, low-MAD lag → a config value applied to
  `host_timestamp_ns`; it does not by itself change the contact clock.
