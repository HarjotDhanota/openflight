# Offline camera timing evidence

## Capture buffers

The camera requests 16 capture buffers through 120 fps and 32 above it. Live Pi
preflight tests exhausted the old 8-buffer pool during same-process JSON/GIL
stalls. The larger pools retained frames in the tested modes while callbacks
still arrived late. This uses more buffer memory and is a bounded-stall
mitigation, not a callback-latency guarantee or an acquisition-clock correction.

The address-ball and tester ball-search pools lower their own Linux threads to
nice 10 when starting. Already lower-priority threads keep their settings. This
includes native threads created before the pool initializer: Linux nice is
[per-thread](https://man7.org/linux/man-pages/man2/getpriority.2.html).
The capture/parent process keeps its priority and CPU affinity. Unsupported
platforms keep their existing scheduling; Linux permission/enumeration failures
are logged and analysis continues. Under CPU contention, analysis can take
longer. This is best-effort scheduling, not a real-time or frame-retention
guarantee. Independent physical timing validation is still required.

## Offline mapping candidate

The M1 timing bench fits camera sensor-to-host timestamp correlation on declared
fit captures and evaluates separate validation captures without refitting.
It preserves input hashes, cadence/gap diagnostics, residuals and unsuccessful
inputs. It does not install a clock correction in live fusion.

Host timestamps are recorded when frames reach the application. Their
relationship to sensor timestamps includes readout, delivery and scheduling
delay. A small regression residual is therefore not proof of physical event
timing. The [Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
describes `SensorTimestamp` at the start-of-frame interrupt as the first pixel
is written out; the actual camera mode and driver still need independent
verification.

## Prepare a dataset

Keep the original modern capture directories, each containing `frames.npz`
and `metadata.json`. Use one unchanged camera mode and one declared boot/clock
domain. Record that identity during collection; the same camera mode on two
boots does not establish one clock domain. The declaration is operator
evidence, not automatic boot verification.

Choose fit and validation captures before inspecting residuals. A manifest
relative to the capture directories looks like:

```json
{
  "version": 1,
  "clock_domain_id": "replace-with-pi-boot-and-clock-record",
  "captures": [
    {"id": "fit-01", "path": "captures/fit-01", "split": "fit"},
    {"id": "validation-01", "path": "captures/validation-01", "split": "validation"}
  ],
  "independent_events": []
}
```

Run from the repository root:

```bash
uv run --extra camera python scripts/analysis/validate_camera_timing.py timing-manifest.json --output-dir timing-result
```

Keep `camera_timing_report.json` and `camera_timing_candidate.json` with the
manifest and raw files. Existing output requires `--overwrite`. An unusable
dataset returns nonzero and retains its failure report. A successfully fitted
candidate remains unqualified for live use.

## Add independent physical events

To investigate physical timing, independently observe an LED/GATE/contact
event and identify the bounding optical frames. Record the event source,
its timestamp clock, measurement uncertainty and original evidence. Do not
reuse the camera trigger timestamp as an independent contact measurement.

An optional `independent_events` entry has this form:

```json
{
  "id": "transition-01",
  "capture_id": "validation-01",
  "optical_frame_interval": [10, 11],
  "independent_host_timestamp_ns": 123456789000,
  "uncertainty_ns": 100000,
  "provenance": "replace-with-independent-instrument-and-clock-mapping-record",
  "source": "event-evidence/transition-01.json"
}
```

The numbers are schema examples, not measurements or recommended uncertainty.
Frame indexes are zero-based. The timestamp must already be expressed in the
declared host clock domain using an independently established mapping. Include
the actual exposure/readout ambiguity in the interpretation: an illuminated
frame alone does not locate a transition at one exact nanosecond.

The report compares bounded intervals and preserves incomplete observations.
Interval overlap does not establish zero timing error. No acceptance threshold
is chosen automatically, and no result closes the hardware timing gate in the
[master plan](../development/fusion-master-plan.md).

## Shared gate and capture duration

The live camera brackets each `CLOCK_BOOTTIME` read with two monotonic reads.
Metadata retains `boottime_minus_monotonic_bounds_ns`; a scheduler stall widens
that interval instead of becoming a precise clock offset. The legacy scalar
`boottime_minus_monotonic_ns` remains for compatibility, not as an uncertainty
bound. The conversion assumes no suspend between the clock reads and the edge.

When the shared `GateEvent` has a usable kernel tick, metadata and `frames.npz`
retain `gate_boottime_interval_ns`. This includes the GPIO reading bracket and
the clock conversion bracket; physical IRQ/timestamp uncertainty remains
unmeasured. Head tracking uses the whole interval rather than a callback-minus-
midpoint estimate. Legacy clips keep their previous interpretation. A malformed
new interval leaves contact unavailable rather than substituting a callback.

With this gate interval, the ring collects the requested number of distinct,
increasing sensor timestamps after its upper bound. Delayed pre-gate frames are
retained but do not spend that tail. Frames already delivered after the gate
count even if the callback is late. `pre_trigger_frames` and `post_trigger_frames`
still describe arrival order; the latter can exceed the requested tail length.
`tail_after_sensor_ns` and `post_tail_frames_by_sensor_timestamp` describe the
new count. Without a usable gate-to-sensor mapping, the ring keeps its legacy
arrival-count behavior and does not claim an exposure-timed tail.

This is a timestamp count, not a validated optical exposure count. The
[libcamera control definition](https://github.com/raspberrypi/libcamera/blob/main/src/libcamera/control_ids_core.yaml)
describes the first active row being exposed, whereas the
[Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
describes the start-of-frame interrupt when the first pixel is written out.
Verify the actual OV9281 driver/mode against an independent event before
selecting a start/end/mid-exposure correction. Do not infer such a correction
from frame delivery lag.

The existing head-track sound-delay model (0.5–4 ms) is not repaired by this clock
mapping. Acoustic travel, electronics latency, radar trigger segmentation and
FFT time support are separate terms. A shared physical-contact estimate must
include them explicitly; it must not relabel a sound-arrival edge as impact.
