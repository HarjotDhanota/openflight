# Camera LED timing calibration

These tools record the sound detector's visible GATE LED and its original Pi
GPIO observation, then compare exposure support against that event. They produce
an offline timing candidate. They never install a fusion correction or declare
physical millisecond accuracy.

## Work without the Pi

All analysis and ROI selection run on the PC. The recorder's dry-run validates
arguments without opening hardware or writing files:

```bash
uv run --extra camera python scripts/hardware-test/record_camera_led_timing.py --output-dir led-study --roi 0,0,640,400 --width 640 --height 400 --fps 300 --lens-label "your lens and focus" --dry-run
```

The dimensions, frame rate and lens label above are examples. Use the mode you
intend to measure. The default exposure sequence is 500, 800, 1500 microseconds,
with 20 fit captures followed by 10 validation captures at each setting. Requested
exposures must fit the frame period; analysis uses actual per-frame exposures.
Use a new output directory for every run. Repeated exposure settings in the
sequence are permitted, so a later run can check exposure-order effects.

Software tests use generated flashes with known exposure windows and event
times, including blind gaps and dropped frames. They also exercise the production
camera trigger/save path and mocked recorder failures. Simulation checks the
implementation, not the Pi's physical timing.

## Position the LED when the Pi returns

Stop the kiosk before starting this standalone recorder; it owns the camera and
BCM17 during collection. Keep the detector's existing power and GATE wiring.
Arrange the camera and detector so the onboard LED appears in the saved crop.
Keep the chosen lens, focus and mode, and use low gain to avoid saturation.

First collect a three-flash visibility check with a broad analysis ROI:

```bash
uv run --extra camera python scripts/hardware-test/record_camera_led_timing.py --output-dir ~/openflight_sessions/led-spotcheck --roi 0,0,640,400 --width 640 --height 400 --fps 300 --exposures-us 800 --gain 1 --fit-captures 2 --validation-captures 1 --lens-label "your lens and focus"
```

Clap once, wait for the saved-clip message and the ring to refill, then clap
again. The console prints each capture path. Use one of the first two captures
to select the LED region. Do not use the validation capture to choose settings.

## Select the LED region offline

Run this on the Pi or on the PC after copying the complete recording directory:

```bash
uv run --extra camera python scripts/analysis/preview_camera_led_roi.py PATH_TO_FIT_CAPTURE --out led-roi.html
```

Open `led-roi.html` locally and drag a tight box around the LED. Copy the printed
`--roi x0,y0,x1,y1` values. The right and bottom edges are exclusive. The picker
uses saved pixels and performs no network requests. Its automatically selected
frame is only a preview; `--frame-index N` selects another frame if necessary.

## Record the declared study

Run the recorder with a fresh output directory, the selected ROI, fixed gain and
the exact camera mode. For example, after replacing the illustrative ROI:

```bash
uv run --extra camera python scripts/hardware-test/record_camera_led_timing.py --output-dir ~/openflight_sessions/led-study-01 --roi 300,180,320,200 --width 640 --height 400 --fps 300 --exposures-us 500,800,1500 --gain 1 --lens-label "your lens and focus"
```

Each exposure block starts a fresh production camera runtime with automatic
exposure disabled. The split is attached at trigger acceptance, before inspection.
The recorder retains raw frames, mode metadata, original GATE evidence, a bracketed
MONOTONIC/BOOTTIME pair and the boot ID. `events.jsonl` records observed GATE events,
accepted/rejected triggers and save failures. `plan.json` and the runtime source
snapshot preserve the configuration and code identity. `manifest.json` retains
partial captures and the reason if the run is interrupted or fails.
Journal counts expose accepted triggers that have no manifest capture; those runs
remain incomplete for calibration. Raw events are retained for rejected triggers.

Repeat the study for each intended mode. A standalone recording establishes its
own load condition; full kiosk/radar-load validation remains a separate hardware
run. Do not run two camera owners simultaneously.

## Analyse the recordings

```bash
uv run --extra camera python scripts/analysis/analyze_camera_led_timing.py ~/openflight_sessions/led-study-01/manifest.json --out led-timing-report.json
```

The report keeps hashes, per-frame ROI brightness, optical frame bounds, frame
gaps, callback delays, exclusions and start/midpoint/end timestamp hypotheses.
Different boots, modes, duplicate event IDs or changed clock-offset epochs cannot
be fitted together. Historical clips without the required event and clock
evidence remain unusable for this calibration.

For each hypothesis, the fit captures give an interval for the correction added
to `SensorTimestamp`. The interval includes the complete exposure support between
the last dark and first fully lit frames. Its midpoint is fixed before validation;
validation never changes the fitted interval. Three distinct fit exposures spanning
at least 1000 microseconds are required before naming one supported convention.
Multiple compatible conventions remain unresolved.

Validation distances measure how far that fixed candidate lies outside optical
support. Zero distance means compatible with the interval, not zero timing error.
Missing validation observations cannot pass. An incomplete recording produces no
candidate. Analysis exits 0 if at least one convention is compatible with all
declared validation captures, 1 otherwise; neither exit code qualifies fusion.

`qualified_for_fusion` stays false and `physical_accuracy_ns` stays null. BOOTTIME
semantics, LED response, GPIO IRQ latency, source-to-sensor delay and behavior under
the intended load still need physical qualification. Preserve the raw data and
reports; do not average away common uncertainty or tune settings on validation data.
