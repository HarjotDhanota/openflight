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

## Compact packet mode

`record --compact` requires the separate compact scratch firmware. It first runs
a verbose identity probe, then sends `cp nonce_hi_hex8 nonce_lo_hex8 seq_hex8`
with a fresh nonce for each exchange, and finishes with another verbose identity
probe. Both context replies must agree on build, runtime token and configured
rate. The first and last context checks are outside each compact timing bracket.
They do not prove a target RF frame's runtime/capture association.

The fixed packet is 44 bytes: ASCII magic `OFC2`, then ten little-endian uint32
words: request high/low, runtime high/low, sequence, handler-entry tick,
pre-response tick, configured tick rate, frame-sequence snapshot and CRC32.
CRC32 uses IEEE polynomial 0xEDB88320 over the preceding 40 bytes, initialized and
finalized with XOR 0xFFFFFFFF. CRC detects corruption; it is not authentication.
Width/version are defined by the magic. This does not reuse an IWR dump version.

The command is 30 bytes and the packet is 44 bytes (DESIGN sizes). Under ASSUMED
8N1 framing and SOURCE-CONFIGURED 1,041,667 baud, their wire time is DERIVED
0.710400 ms, excluding echo/processing/USB/host delays. This replaces the verbose
example's DERIVED approximately 3.13 ms serialization minimum; neither number is
measured latency, an upper bound or proof of sub-millisecond synchronization.

The reader timestamps the read completing the packet before waiting for `Done`
and the complete known CLI prompt. It saves exact received bytes, cumulative read
sizes and their separate MONOTONIC marks. The counter envelope uses the packet
completion upper bound; whole-command completion remains a separate diagnostic.
If USB coalesces packet and trailer, both marks are late and equal. No estimated
USB delay or midpoint is subtracted to make the interval narrower.

The recorder uses the existing driver's serial handle while holding exclusive
ownership. Partial writes, CRC/identity errors, unknown prefixes, duplicated
packets, oversized/truncated data, missing readiness and changed runtime stop
collection. Bytes and available read marks are retained on transport failure.
Auditing refuses incomplete runs and verifies the packet timestamp against the
read that first contained the complete packet. It waits for CLI readiness before
the next command; it does not discard input to hide a mismatched response.

Compact recordings use `openflight.iwr-clock-compact.v1`; the same `audit` command
accepts them. Its `host_round_trip_ns` describes whole-command completion, while
`counter_bracket_width_ns` describes the bracket used for the counter envelope.
Packet proximity, oscillator continuity/rate and physical RF timing remain
conditional exactly as for verbose probes. No clock correction is installed.

USB/bridge buffering can still dominate. The CP2105 datasheet identifies a
full-speed USB device; it does not establish a latency bound for this Pi stack.
[CP2105 datasheet](https://www.silabs.com/documents/public/data-sheets/CP2105.pdf).
Do not apply another bridge's timeout command to it: AN571 specifies its receiver
max-timeout setting for CP2102N only, rather than CP2105.
[AN571, section 5.18](https://www.silabs.com/documents/public/application-notes/AN571.pdf).
No USB bridge, UART baud, kernel or saved device setting is changed here.

## Conditional UART serialization bounds

`uart_clock_bounds.map_compact_uart_tick` is a separate research calculation;
the existing audit and live capture behavior do not change. Decode saved compact
records with `compact_recording_observations` first. Supply an explicit
`UARTAssumptions` with the reviewed build fingerprint, framing, per-direction
maximum baud rates and a reference explaining those assumptions. A matching hash
identifies reviewed source; it does not establish physical timing by itself.

For this firmware contract, the handler reads `r` after the complete command
arrives and reads `t` before sending any packet byte. Under assumed 8N1 framing,
each preceding complete character occupies ten bit periods. Omit the last
character entirely in each direction to avoid relying on the receiver's stop-bit
sampling point. For the DESIGN command/packet lengths of 30/44 bytes:

```text
request_min = 290 bit periods / request_max_baud
reply_min   = 430 bit periods / reply_max_baud
time(r) >= h0 + request_min
time(t) <= h1 - reply_min
```

Baud ceilings must be expressed in bits per host-MONOTONIC second, accounting
for UART oscillator/divider error and host timebase error. Requested baud is not
a measured ceiling. USB queuing, echo, CRC computation and CLI trailer duration
receive no credit; unequal or arbitrarily stalled transport remains allowed.

The earlier counter envelopes can therefore be shifted inward by those two
minimum durations. Common shifts commute with intersection, so every supplied
probe remains included. Integer serialization credits round downward. Expand
each end by one counter cycle at the slowest assumed rate to cover counter-read
phase quantization. Reject an exchange if its bracket cannot contain these wire
minima plus the minimum handler duration, or if the final intersection is empty.
Original bytes and host timestamps are unchanged.

The result retains the unadjusted interval and its assumptions alongside the
narrowed interval. `physical_timing_qualified` and `wire_timing_qualified` remain
false, and `rf_acquisition_interval_ns` remains null. A conditional width below
1 ms is neither a measured clock error nor a justified accuracy claim of plus
or minus half that width. Counter continuity/rate, source timestamp placement,
wire ceilings, capture/runtime linkage and the RF/ADC offset require separate
qualification before this calculation can support physical fusion timing.

## Capture/runtime sidecars

The separate RESEARCH-only `capture_clock` recorder brackets one original full
dump with a compact probe on each side and verbose identity checks around the
whole session. It uses one already exclusively owned radar handle. The capture
callback receives that handle and must return only after CLI recovery; it must
not reopen UART or start another serial owner. No probe runs during acquisition
or a held transfer. The collector saves exact payload bytes before attempting
the post-probe, retaining partial bytes and failure records when available.

`scripts/hardware-test/iwr_capture_clock.py record` operates on a separately
configured, already running RF profile and requires the reviewed build hash. It
does not configure/start RF or install firmware. It creates a new directory,
records one capture and, on successful collection, stops the sensor and verifies
idle with the existing driver. Failed collection closes the port without
attempting further UART commands against an unknown CLI state. Failure may
leave RF running; inspect retained evidence and recover through the reviewed
board procedure. It never retries, resets or flashes the board automatically.

The JSONL start record identifies the host boot, serial connection and capture.
The capture record binds the original payload length and SHA-256 to the observed
host acquisition bracket. The auditor checks all compact byte/read evidence and
matching pre/post identities, payload length/hash, continuous associated frame
evidence, capture/probe rate agreement and lifecycle ordering. Every retained
frame must follow the pre-probe's frame sequence and counter, and precede the
post-probe. Buffered frames from before the pre-probe are rejected, rather than
silently omitted. A settle interval is an operator-selected collection policy;
its default does not guarantee that every profile has overwritten its ring.

The full host span between probes must fit inside the supplied proximity bound
and be shorter than the fastest assumed counter half-wrap. This independently
bounds proximity instead of inferring wrap count from raw counter values. Long
waits or slow full dumps can make the entire association unavailable; no distant
post-probe is dropped to manufacture a result. Both probes participate in every
frame start/done counter interval. Optional UART credits require the explicit
contract described above. Full dump versions without observed evidence and
sparse/two-round captures are currently unsupported by this sidecar auditor.

Offline example, with ASSUMED counter/proximity inputs:

```bash
uv run --no-sync python scripts/hardware-test/iwr_capture_clock.py audit capture-dir \
  --output capture-audit.json --rate-min-hz 199980000 \
  --rate-max-hz 200020000 --max-distance-ns 10000000000
```

Exit 0 means a conditional counter association, 1 unavailable evidence, and 2
collection/input/output error. Output files/directories must be new. Offline
audit never opens a device and hashes its input JSONL. Pre/post identity agreement
is a conditional software association, not an embedded capture runtime token or
cryptographic provenance. It relies on the reviewed collector, runtime identity
behavior and uninterrupted counter operation. Start means ISR entry and done
means processing/copy completion; neither is a calibrated ADC boundary.
`physical_timing_qualified` and `wire_timing_qualified` stay false, and
`rf_acquisition_interval_ns` stays null. No shot metric consumes this result.

## ADC configuration diagnostic

`scripts/hardware-test/iwr_config_trace.py` is a RESEARCH-only diagnostic for a
configuration response that did not complete. It runs verbose identity checks
around a fixed command list: `sensorStop`, `flushCfg`, `dfeDataOutputMode 1`,
`channelCfg 15 7 0`, `adcCfg 2 1`, then `stats`. Its allowlist excludes RF start,
profile/chirp/frame configuration, capture, loading and flashing. It requires
one exclusive Enhanced UART and the reviewed source build fingerprint.

The diagnostic retains original received bytes, all read marks (including empty
reads), write return and separate terminal/prompt marks. It does not flush input
or retry a command. Completion requires exactly one standalone `Done` or SDK
error line followed immediately by the complete known prompt. An echo, embedded
substring or `Done` without the prompt does not establish readiness. Unknown
suffixes and duplicate terminals remain invalid. I/O failure, rejection, byte
limit or timeout stops the sequence before any further UART command.

The default 10-second wait and maximum 15-second wait are DESIGN diagnostic
policies, not measured device latency or a production timeout fix. Serial
read/write calls use finite timeouts; the recorded final read can exceed the
requested deadline by one bounded synchronous call. Such bytes are retained
but not accepted as completion within the declared window. Original serial
timeout settings are restored. Read timestamps are host upper bounds, not
UART-pin timestamps or clock-offset measurements.

Use the same diagnostic on separately reviewed combined and clock-only RAM
images, keeping their exact source IDs and failed traces separate. Example
(placeholders require the supervised image/port procedure):

```text
uv run --no-sync python scripts/hardware-test/iwr_config_trace.py --port ENHANCED_UART --build REVIEWED_SOURCE_SHA256 --output NEW_TRACE.jsonl --timeout-s 10
```

Exit 0 reports complete command replies, matching pre/post identity and observed
idle/no-fault stats; 1 means diagnostic evidence unavailable; 2 means input,
output or collection setup failure. Failure closes UART without follow-up
commands against an unknown CLI. Restore normal boot through the supervised
board procedure. No diagnostic outcome qualifies physical synchronization.

## Pre-header dump diagnostic

`record_dump_trace` in the RESEARCH module `iwr6843.dump_trace` sends one
`l3dump` on an exclusively owned, already configured connection. It retains the
entire byte stream and cumulative read marks through a declared window (default
15 seconds; maximum 30 seconds), including silence beyond the product reader's
four-second stall policy. It does not flush input, retry or send recovery
commands. It restores serial timeout settings, saves partial bytes on I/O error
and marks late bytes ineligible for completion. The caller must persist the
returned raw bytes and JSON record even when the status is unavailable.

The opt-in firmware flag `L3_DUMP_STAGE_TRACE` emits `L3STAGE` lines before the
binary header: dump entry, HWA freeze, RF stop, stop margin, temperature query
and header entry. It requires the HWA ring and USB clock probe and rejects
two-round builds. Markers come from CLI context, never an ISR. Their counters
are separate reads, not an atomic state snapshot. Text writes alter scheduling
and UART traffic; this build is for fault localization, not calibration.

The recorder reports complete only for a valid binary dump followed by exactly
`Done` and the full prompt within the window. It retains the original prefix
and suffix separately from payload offsets; it does not mistake ASCII stages
for frame evidence. Headerless, incomplete, rejected and late transfers remain
unavailable. A finished diagnostic alone never qualifies physical RF timing or
sub-millisecond clock alignment. Use the supervised RAM/restore procedure and
verify the reviewed source ID before starting the one-capture experiment.

With `L3_DUMP_WIRE_TRACE`, the clock-only diagnostic also counts every binary
UART call from header through dump trailer and computes a source CRC32 before
each write. After the binary it emits a separate `L3WIRE` line with requested
bytes, returned bytes, errors, mismatches, first failing call/result and CRC32.
No retry or repair occurs. Stage text and the diagnostic line are outside the
checksum. This flag defaults off; the production binary format is unchanged.

Use `require_wire_trace=True` with that reviewed image. The recorder requires
matching declared/observed/requested/returned lengths, no write errors or
mismatches, a matching CRC32 and the complete prompt within the deadline. Missing
or malformed reports, short data and equal-length corruption stay unavailable.
`inspect_dump_trace` can inspect saved bytes offline under the same rules.
CRC32 is a transport diagnostic, not an authentication mechanism or proof that
the underlying IQ acquisition is correct. The checksum pass adds pauses between
writes and can alter the behavior under investigation. A passing instrumented
run cannot establish that the uninstrumented short transfer is fixed or qualify
physical clock timing. Callers must save accepted `.l3dump` files only when the
overall status is complete, retaining every other stream as raw evidence.

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

## Temporary RAM experiment

The research-only `scripts/hardware-test/load_iwr_clock_ram.py` implements the
ROM UART RAM-download path in [SWRA627, section 3.3](https://www.ti.com/lit/an/swra627/swra627.pdf).
It uses SRAM storage 4 and WRITE FILE TO RAM opcode 0x26. The SDK meta-image must
have its flash-only CRC32 footer removed; its internal CRC64 remains for the
ROM. `ram_image_from_flash` verifies the footer before removing it. The command
requires the reviewed RAM payload SHA-256 and validates its device/header length
before opening UART. It has no flash-write, erase or flash-fallback option.

On the confirmed PROC116A board, the CP2105 DTR/RTS pins are unconnected
([schematic, sheet 10](https://www.ti.com/lit/zip/swrr178)). USB therefore cannot
operate SOP or RESET. The operator prepares SOP2/S1.1 ON, SOP1/S1.2 OFF and
SOP0/S1.3 ON, preserving the functional UART-routing switches. The command opens
the exclusively locked Enhanced UART, then waits for the operator to press
RESET before starting the ROM handshake. It does not pulse DTR/RTS to attempt a
remote boot-mode change.

```bash
uv run python scripts/hardware-test/load_iwr_clock_ram.py temporary-clock.ram.bin \
  --port /dev/ttyUSB0 --sha256 REVIEWED_RAM_PAYLOAD_SHA256
```

Acknowledged transfer is not verified application startup. Check the expected
verbose `clockProbe` build/runtime before collecting timing evidence. To return
to saved firmware, restore functional SOP2/S1.1 OFF and press RESET. The RAM path
is intended to preserve flash, but its operation on this board is still pending
hardware validation. The saved release artifact is not a readback of the
currently installed application.
