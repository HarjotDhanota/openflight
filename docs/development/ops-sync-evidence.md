# OPS sync evidence recorder

This research tool records the v1.3.3 OPS sync output as GPIO input events. It
does not send radar commands, change firmware, drive a GPIO output, install a
clock correction or change shot metrics. The manufacturer identifies GPIO4_0
on OPS J3 pin 1 as high from speed-trigger detection until rolling-buffer sampling
finishes. [Manufacturer notes](https://github.com/omnipresense/OPS243-A_Firmware/blob/efaceefb8fa547071cb925c0d20d2e38363ace67/README.md).

Confirm board/image compatibility, electrical levels and available Pi line before
connecting it. `--line` is a GPIO-chip offset, not a physical header pin. GPIO27
on the Pi RP1 chip is the proposed input (physical pin 13), pending wiring. The
existing BCM17 sound GATE remains a separate signal.

## Record on the Pi

The optional `timing` extra declares libgpiod Python bindings v2.2 or later.
The inspected Pi already provides compatible system bindings. Preserve the
system-Python camera environment and use its installed dependencies:

```bash
UV_PYTHON=/usr/bin/python3 uv run --no-sync python scripts/hardware-test/record_ops_sync.py record \
  --chip /dev/gpiochip0 --line 27 --duration-s 10 --output ops-sync.jsonl
```

Confirm the chip label and line names on the actual Pi. Each output must be a
new file. The tool refuses a used line and exclusively requests one input with
both edges, MONOTONIC timestamps, unchanged bias and no debounce. It releases
the request on success, interruption or read failure. It records boot identity,
initial level, kernel/binding versions, global/line sequences, kernel edge time
and separate Python arrival time. End records preserve errors and pending
events. A missing end marker invalidates association.

Releasing the request can leave the pad configured as an input. Record its
original pinctrl state for standalone experiments and restore it afterward once
the line is confirmed unclaimed; the recorder does not restore pad mux settings.

## Audit and associate

```bash
uv run python scripts/hardware-test/record_ops_sync.py audit ops-sync.jsonl \
  --captures ops-captures.json --output ops-sync-audit.json
```

The manifest has schema `openflight.ops-sync-captures.v1` and a `captures` list.
Each entry needs `capture_id`, `boot_id`, `armed_mono_ns`, `dump_start_mono_ns`
and `raw_capture`. The latter is a relative path inside the manifest directory
to original OPS JSON-line output containing sample_time, trigger_time, I and Q.
The existing I/Q parser validates the arrays; the harness computes source SHA-256
and recording/manifest hashes itself.

Arm and dump-start times must be observed on the declared Pi boot and retained
by the capture producer. Do not invent them from expected post-trigger duration,
wall time, desired metrics or pulse width.

## Live capture evidence

With session logging enabled, the monitor automatically writes an
`ops_capture_evidence` entry for each sound/internal-hardware-trigger wait,
before parsing, rejection, clock queries or re-arm. It includes a unique capture
ID, Linux boot identity, host MONOTONIC command/read observations, original dump
bytes in base64 with SHA-256, bounded preamble bytes and the
`trigger_velocity_detected` notifications found there. Notification values remain
as received; their host read-completion time is separate. A late first marker
observation does not identify byte arrival or physical contact. Accepted
`rolling_buffer_capture` entries carry the same capture ID.

`armed_mono_ns` is the observed start of the host arm-command sequence, before
the first PA for sound operation, or before the final live threshold write for
initial internal-trigger configuration. It is a conservative window boundary,
not a device acknowledgement or proof that sampling began then. Re-arm failures
leave an incomplete command context. The context is copied before waiting so
the next re-arm cannot overwrite it. Missing boot/command evidence stays missing.

The journal keeps up to 64 KiB of preamble, 1,024 pre-marker read observations
and 256 KiB of original dump bytes. Overflow is explicit and prevents association.
Timeouts, cancellation and serial errors also retain diagnostic entries; a
rejected shot is not relabelled as accepted. Logging failure is reported without
discarding an otherwise usable capture. Disabled session logging saves no journal.
The software-speed/S! path does not produce this hardware-trigger journal.

Extract a live session into a new directory, then audit it against a concurrent
GPIO recording from the same Pi boot:

```bash
uv run python scripts/hardware-test/record_ops_sync.py export session.jsonl \
  --output-dir ops-captures
uv run python scripts/hardware-test/record_ops_sync.py audit ops-sync.jsonl \
  --captures ops-captures/captures.json --output ops-sync-audit.json
```

Export preserves exact bytes and hashes the source session. It refuses existing
output directories, duplicate capture IDs, unsafe names, corrupt base64 and hash
mismatch. It retains incomplete captures with failure reasons and exit status 1;
the auditor refuses them. Invalid input/I/O returns 2. Original dumps containing
multiple sample/trigger/I/Q records are ambiguous and cannot associate.

Start the GPIO recorder before the arm commands, not just before a swing: the
whole conservative command-to-marker window must fit inside the recording.
An idle timeout/re-arm can be useful to establish a fresh observed window while
recording. The session journal does not request any GPIO lines or configure the
new OPS output. It provides no live sample-clock correction or trigger fanout.

Each capture needs exactly one complete pulse in its arm-to-dump window, starting
from an observed low state. Lost/duplicate sequences, unordered timestamps,
invalid clock context, reset markers, incomplete recordings, orphan edges,
partial/multiple pulses, boot mismatch and pulse reuse prevent association.
A reset invalidates the whole recording; start a new one after a device reset.
Unreported OPS resets cannot be detected by GPIO alone. Sequence counter overflow
is rejected; no wrap is guessed. Start with a quiet input: events before the
initial-level sample invalidate the recording.

Exit 0 means structural checks pass and any supplied captures associate; exit 1
preserves a report with stream/association failures; exit 2 means an input,
output or device-access error. An idle recording without captures can exit 0
with zero edges. This is not a timing-accuracy pass.

Every match is `associated_unqualified`; sample mapping and physical-edge
uncertainty remain unavailable. The falling edge is a candidate end-of-sampling
anchor. ADC-boundary offset, IRQ jitter, oscillator accuracy and sample order
need independent physical validation. GPIO timestamps do not identify physical
ball contact or establish sub-millisecond camera/OPS/IWR alignment by themselves.
