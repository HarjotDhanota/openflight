# Offline camera timing evidence

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
