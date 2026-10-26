# IWR clock evidence (research only)

The current rig connects the IWR through USB only. This tool prepares a bounded
software-counter exchange for that connection; it does not require a GPIO wire,
request GPIO, configure RF, flash firmware or change shot metrics. The kiosk never
imports the research module. The matching firmware is a separate scratch prototype,
not an installed or qualified capability.

## USB exchange contract

`clockProbe nonce_hi_hex8 nonce_lo_hex8 sequence_hex8` returns three versioned
ASCII records followed by the normal standalone CLI `Done`:

```text
OFCLKPROBE1 build=<64hex> request=<16hex> boot=<16hex> seq=<decimal> tick_hz=<decimal> counter_bits=32
OFCLKSTAMP1 rx_tick=<decimal> tx_tick=<decimal> frame_seq=<decimal> physical=unqualified
OFCLKPROBEEND1 request=<16hex> seq=<decimal>
```

The request nonce is fresh and nonzero for every exchange, including retries.
The firmware reads the existing PMU counter on handler entry, validates the
request, then reads it again before formatting any response. Neither stamp is a
physical USB receive/transmit timestamp. `frame_seq` is a nearby sequence snapshot,
not an RF-frame acquisition stamp or an association to a saved dump.

The runtime token (`boot`) is the first valid request nonce since application
startup. Later replies retain it; a restart followed by a fresh request changes
it. It is a probabilistic host-seeded runtime identity, not a persistent hardware
boot counter. It does not detect every possible PMU malfunction or counter reset
that occurs without restarting the application. Never retry a request nonce.

The recorder uses the existing exclusive, DTR/RTS-safe driver and an explicit port.
It brackets the whole command with host MONOTONIC reads. It retains reply text,
including rejected/unsupported replies, and ends on the first failure or changed
runtime. It does not collect during an overlapping dump or another serial owner's
capture. Starting it requires exclusive use of that port. Port opening can affect
the USB stack even though no RF configuration or GPIO command is sent.

After the matching firmware has separately passed a controlled installation
review, an example collection command is:

```bash
uv run --no-sync python scripts/hardware-test/iwr_clock_probe.py record \
  --port /dev/ttyUSB0 --count 32 --interval-s 0.1 --output iwr-probes.jsonl
```

Do not run this example expecting support from the currently identified firmware.
The counts/spacing are protocol choices, not measured device capacities. Output
must be new; interrupted runs remain incomplete and cannot qualify a mapping.

## Conditional interval math

Let `[h0,h1]` enclose the complete host command. Let `r,t` be the device's
handler-entry and pre-response ticks. For target counter `x`, causality gives:

```text
lower = h0 + minimum_duration(x - r)
upper = h1 + maximum_duration(x - t)
```

Duration extrema use explicitly supplied oscillator-rate bounds, with signed
differences and outward integer rounding. There is no equal-delay assumption or
midpoint correction. An IWR configured at 200 MHz is not thereby measured to run
at exactly that rate. Counter operation through idle/clock gating is unvalidated.
Rate bounds are supplied assumptions until an independent procedure qualifies them.

Each target must have a declared matching build/runtime epoch and a justified
distance from each probe shorter than the fastest possible half-wrap. The raw
counter cannot establish that distance by itself. Missing boot context, changed
connections, reused requests, sequence gaps, ambiguous spans and inconsistent
interval intersections are rejected. Every supplied probe participates; there is
no outlier removal. A narrow intersection remains conditional on the rate,
counter-continuity and target-epoch assumptions.

The offline audit requires rate bounds and proximity explicitly. For example,
these are **assumed protocol values**, not rig calibration:

```bash
uv run python scripts/hardware-test/iwr_clock_probe.py audit iwr-probes.jsonl \
  --rate-min-hz 199980000 --rate-max-hz 200020000 \
  --max-distance-ns 1000000000 --output iwr-probes-audit.json
```

Without a target, the audit reports each exchange's own pre-response-counter
envelope. Optional `--target-tick`, `--target-build` and `--target-boot` must be
supplied together to intersect all probes at a declared nearby counter. They do
not prove that a saved RF frame belongs to that runtime. A pipeline needs observed
capture/runtime linkage and counter-continuity checks before using such a mapping.

Exit status: 0 conditional diagnostic available; 1 retained recording/mapping
unavailable; 2 input/output/collection error. Audits hash the original recording.
`physical_timing_qualified` is always false; `rf_acquisition_interval_ns` is null.
Even a width below 1 ms is not a claim of RF/camera/contact synchronization.

## GPIO sidecar parsing

The research module also parses the previous optional GPIO1 `clockGate` sidecar.
It checks framing, identity, count/sequence integrity and retains overflow,
discarded/unattached counts and late-low sampled levels. It never pairs the first
IWR edge with the first Pi edge by assumption, invents a Pi timestamp, or converts
a GPIO ISR tick into RF acquisition time. The current USB-only setup produces no
such shared-edge evidence. The GPIO prototype's startup/physical-access review is
still open; the USB-only prototype avoids that initialization entirely.

Hardware qualification still requires firmware/host compatibility and rollback,
counter continuity/drift/reset checks, UART-load/RF-frame integrity checks, explicit
RF frame/chirp/ADC timing, camera optical timing and independent physical evidence.
No software-only result removes those requirements.
