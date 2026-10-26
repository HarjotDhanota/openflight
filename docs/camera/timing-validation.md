# Offline camera timing evidence

## Camera-only clock preflight

This check can run before an OPS firmware upgrade, without either radar or a
sound-trigger connection. Stop camera capture applications first; the tool uses
the kiosk's camera path and will not stop another camera owner.

```bash
UV_PYTHON=/usr/bin/python3 uv run --no-sync python scripts/hardware-test/camera_clock_preflight.py \
  --width 1280 --height 800 --fps 120 --seconds 10 --load idle \
  --output-dir camera-clock-idle
```

Use a system-Python environment with Picamera2 and compatible camera dependencies.
`--load gil` and `--load cpu` reuse the existing camera starvation test's loads;
run them separately and retain every report. Existing output directories are
refused. The diagnostic fixes requested exposure/gain and disables automatic
exposure; those temporary controls are not an optical-quality qualification.
It does not request GPIO, send radar commands or change saved kiosk settings.

The tool saves timestamps only in `frames.jsonl`, bracketed MONOTONIC/BOOTTIME
reads in `clock-pairs.jsonl`, and a `report.json` with source hashes, requested and
resolved mode, observed buffer count, boot identity, observed cadence/gaps and
delivery-lag intervals.
No images are retained. It rejects missing/malformed clocks, changed boot IDs,
nonconstant observed clock offsets and duplicate/reordered frames for conversion.
Clock observations enclose the saved frame callback times. A successful exit
means the observations are usable and no cadence gaps were seen in that run;
it is not a sub-millisecond accuracy pass. Exit 1 retains diagnostic failures or
gaps; exit 2 means invalid input, device access or I/O failure.

The conversion is the clock-read envelope: for BOOTTIME minus MONOTONIC in
`[low, high]`, a sensor timestamp `s` maps to MONOTONIC `[s-high, s-low]`. Callback
delivery lag is `[host-s+low, host-s+high]`. It is reported, never fitted away.
The envelope assumes no suspend between observations. The sensor clock domain
is declared according to libcamera, while exposure/readout semantics still need
an independent optical event. Small clock-read widths cannot qualify that offset.

The camera requests 16 capture buffers through 120 fps and 32 above it. Live Pi
preflight tests exhausted the old 8-buffer pool during same-process JSON/GIL
stalls. The larger pools retained frames in the tested modes while callbacks
still arrived late. This uses more buffer memory and is a bounded-stall
mitigation, not a callback-latency guarantee or an acquisition-clock correction.

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
