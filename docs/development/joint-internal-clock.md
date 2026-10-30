# Isolated internal-trigger clock trial

This local trial combines the OPS timing/provenance fixes with the existing
IWR self-trigger clock prototype and sensor-timed camera buffering. It does not
enable internal-trigger fusion in the kiosk or install firmware.

The host branch starts at `53dd4d86`. The retained IWR firmware source is based
on `06d03795`; its underlying firmware tree matches `upstream/feat/iwr-self-trigger`
at `120ef5e7`. The separate USB clock overlay has source fingerprint
`c2dd981eb48225a75e431a7930f3ded38068e8720cc0007ef4dd163fe4514ce1`.
Use the already built and idle-board-tested RAM image, SHA-256
`12b15e27b138ad4ba62e17dfbf5cd75deb2151809274fe51f2c47d2b3da6e192`.
Do not merge the entire upstream branch into this host build: it also replaces
the metric pipeline, outside this timing experiment.

## Preparation without sensors

The external, sealed dependency package is
`research/fusion-timing-audit/usb-result-clock-test-20261027/package-001`
in the parent workspace. `run_joint_clock.py` checks the original manifest,
every member hash and the image identity before importing device helpers.
The old `run_idle.py` remains unchanged and still cannot start RF.

With that directory assigned to `OPENFLIGHT_CLOCK_PACKAGE`, run:

```bash
uv run --no-sync python scripts/hardware-test/run_joint_clock.py \
  --clock-package "$OPENFLIGHT_CLOCK_PACKAGE"
uv run --no-sync python -m pytest -q \
  scripts/hardware-test/test_joint_clock_active.py tests/test_joint_clock_rig.py \
  tests/test_ops_sync.py tests/test_record_ops_sync.py
```

The preflight opens no devices. The active collector's wire tests use the sealed
package's synthetic UART fixture. Their results are not hardware measurements.

## OPS/camera control, no swing required

Use an isolated system-camera environment on the Pi, with the kiosk inactive and
serial/GPIO/camera resources unowned. Output directories must be new.

```bash
uv run --no-sync python scripts/hardware-test/run_joint_clock.py \
  --clock-package "$OPENFLIGHT_CLOCK_PACKAGE" \
  --execute-ops-camera --output /path/to/new-control
```

This starts an input-only MONOTONIC GPIO recorder on BCM27 before arming the
native OPS threshold (25 mph, magnitude 25, S#6, configured 30 ksps). It starts
the camera at 640x400/300 fps, temporary manual 300 us/gain 4, with a 200 ms
pre-ring and 150 ms sensor-timed post-tail. It does not open the sound GPIO,
change stored settings, send OPS `S!`, or request an IWR capture.

After setup it explicitly requests idle, requires a unique P3 acknowledgement,
and rearms with PA, reusing the previously tested lower-power trial sequence.
P3 acknowledgement is not independent verification of effective RF power.
Original UART reads/writes and their host brackets are retained in a journal.

The native wait lasts four seconds, with an eight-second grace for any dump
already in progress. No event is an expected empty control. An event freezes
the camera once; raw OPS bytes and both GPIO edges are retained. Clock samples
are sampled latest-frame metadata, not a lossless recording of every camera
frame. GPIO falling stamps and serial receipt remain separate observations.

## Active IWR control and joint trial

```bash
uv run --no-sync python scripts/hardware-test/run_joint_clock.py \
  --clock-package "$OPENFLIGHT_CLOCK_PACKAGE" \
  --execute-ram-rf --output /path/to/new-rf-control
```

The runner waits with UARTs closed for `READY` after the operator sets the RAM
boot switches. It then opens only Enhanced if00, with DTR/RTS low, and waits for
`RAM` after the operator presses RESET. The same UART owner performs RAM loading,
identity/probe requests, RF configuration and stop checks. No flash command is
permitted. Afterward restore normal switches and reconnect the IWR USB; verify
normal idle operation separately.

The RF profile retains the reviewed 3-TX, 12-loop, 128-sample, 3 ms IQ16 plan.
Its only frame-profile change is a configured finite count of 2,000 frames
(nominal 6 seconds), rather than continuous frames. This configured bound is
not an independently observed RF shutdown guarantee. The detector is enabled
at nominal bin 32/SNR 1; that is a bench setting, not a measured ball position.
No empty-lane tuning, calibration or threshold optimization is performed.

The runner requires advancing RF frame counters, unchanged build/runtime,
CRC/nonce-valid probes, no missed starts or RF fault, and no unexpected held
capture. It retains all probes, rejects ambiguous wraps/resets and verifies
`active=0` after an acknowledged stop. If framing/identity becomes uncertain,
it preserves partial replies and refuses speculative recovery writes; stop
verification then remains unavailable and physical restoration is required.

Add `--with-ops-camera` for simultaneous native OPS arming and camera load.
It collects 64 probes spaced by 50 ms, within a four-second batch deadline.
The OPS wait begins after IWR reports active RF. The IWR detector remains
independent: OPS never forces an IWR capture. A shared host boot identifies
the trial, not a shared physical shot. A later held shot uses the existing
CRC-bound result-clock reader with explicit shot and generation identities;
the empty control does not exercise that path.

## Interpretation

The maps assume 199.98–200.02 MHz and an eight-second proximity bound. All
probe envelopes must intersect; no slow sample is removed. Counter envelopes
are conditional host/device mappings, not measured ADC time or contact.
Keep MONOTONIC, BOOTTIME, sensor metadata and IWR runtime counters distinct.
Neither matching triggers nor narrow intervals prove sub-ms physical alignment.
OPS edge-to-sample timing, IWR ISR-to-ADC timing, camera exposure semantics,
drift under load and same-shot association still require bench evidence.
