# No-swing OPS sample-clock controls

`scripts/hardware-test/ops_sample_clock.py` is an isolated bench collector. Its
default invocation opens no devices. It never opens IWR, camera or sound GPIO,
changes persisted settings, or computes shot metrics.

Explicit execution on an idle Pi:

```bash
uv run --no-sync python scripts/hardware-test/ops_sample_clock.py \
  --execute --output /path/to/new-control
```

The collector requires inactive OpenFlight, an unowned OPS UART, observed firmware
1.3.3, and an input-only GPIO27 recorder starting low. It runs six software-requested
captures at S#6,16,26,26,16,6, at configured 30 ksps. Native detection uses a guarded
198 mph / magnitude 2000 configuration. This is not an electrical disconnection of
the sound input. A native notification racing the software request fails the run.
P3 must be uniquely acknowledged before explicit PA; actual RF power is unmeasured.
Each buffer has at least 350 ms to fill after rearm. One failed capture stops the
batch; original UART bytes, command/read brackets, GPIO records and failures stay
on disk. The collector requests PI and closes the UART in cleanup. PI response is
retained, not independently verified RF power-off evidence.

The driver response must equal the original bytes read during its S! transaction.
This detects its ASCII decoder dropping corrupt bytes. All four capture fields
must be unique, both ADC arrays must contain 4096 valid paired values, and device
time strings must be finite and ordered. GPIO pulse associations use the observed
arm-to-complete-response window and stay `associated_unqualified`. The completion
time is an upper window bound, never mislabelled as first UART byte time.

At configured 30 ksps, the first-to-last sample span is 136.500 ms; 4096 sample
periods occupy 136.533333... ms. Neither is the rising-to-falling GPIO pulse width:
the returned buffer includes prehistory. Device trigger minus sample time is
reported separately from configured S# history. No actual sample rate or signed
pin-to-ADC offset is estimated from pulse width, and sample_mapping remains null.
Whether software triggering produces the same pin behavior as native triggering
is an experiment question, not an assumption.

Run device-free checks with:

```bash
uv run --no-sync python -m pytest -q tests/test_ops_sample_clock.py \
  tests/test_ops_sync.py tests/test_record_ops_sync.py
```

No native trigger, contact, speed, optical timing or physical sub-ms accuracy is
qualified by these software tests or by an empty bench capture.
