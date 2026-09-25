# OPS243 Internal Hardware Trigger

`--trigger hardware` lets an OPS243-A start its own rolling-buffer capture from
its native speed and magnitude detector. It is opt-in. The default remains the
SEN-14262 sound trigger, and the existing host-mediated `speed` strategy remains
available.

## Compatibility and scope

This mode requires OPS243-A firmware **1.3.2 or newer in the 1.3 release
train**. Firmware 1.3.1 advertises the commands but has a vendor data-sequence
bug, so OpenFlight rejects it. Other release trains, malformed replies and an
unresponsive version query are also rejected before the radar is armed.

This is currently an **OPS-only** capture mode. The native OPS trigger has no
qualified low-latency GPIO fanout edge, and the first UART dump byte arrives too
late to trigger the short IWR6843 history reliably. OpenFlight therefore rejects
`--trigger hardware` when `--iwr6843` or `--camera-capture` is enabled. Use
`--trigger sound` for synchronized OPS/IWR/camera collection until a shared
soundless edge or an independent IWR trigger is physically qualified.

## Command

```bash
scripts/start-kiosk.sh \
  --trigger hardware \
  --trigger-threshold 25 \
  --trigger-magnitude 25 \
  --pre-trigger-segments 6 \
  --sample-rate 30
```

The kiosk wrapper forwards these options unchanged. Hardware mode requires 30
ksps, accepts `S#0` through `S#32`, and accepts `SM1` through `SM2000`. The
outbound speed threshold is sent to the OPS as a negative `ST` value because
outbound motion has negative radar velocity.

## Arming and recovery

OpenFlight verifies the firmware, idles the sensor, protects the mode transition
with a temporary outbound threshold, enters `GC`, and restores every detector
setting that `GC` clears. The requested `SM` and `ST` values are sent last so a
retained threshold cannot start a dump while configuration is incomplete.

Every completed dump is parsed and then re-armed with the same guarded `GC`
sequence. Captures without an outbound reading of at least 35 mph are retained
in trigger diagnostics but rejected as shots. A serial write timeout does not
discard a valid capture; the next wait ignores trailing output until it finds a
fresh `sample_time` or `trigger_time` marker.

Session provenance records the trigger mode, OPS model and firmware, threshold,
magnitude, `S#` split, sample rate, and the unavailable auxiliary fanout status.
No setting is written to OPS flash by this path.

## Hardware validation still required

Automated tests verify command ordering, validation, marker parsing, re-arm and
CLI plumbing without physical hardware. Before relying on this mode, validate
firmware reporting, false-trigger rate, missed shots, repeated captures and raw
I/Q completeness on the target Raspberry Pi and OPS243-A. This software support
does not establish a synchronized soundless fusion trigger.
