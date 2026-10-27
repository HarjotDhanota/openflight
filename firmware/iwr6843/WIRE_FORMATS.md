# IWR6843 binary wire formats

Every binary payload the firmware and host exchange starts with a 4-byte
ASCII magic. Each payload type has its own magic, and its version numbers
belong to that magic alone. Never give an existing magic a new version for a
different kind of payload. A new payload type gets a new magic, starting at
version 1. Add a row here before any firmware or host code uses it.

| Magic | Payload | Versions | Status | Defined in |
| --- | --- | --- | --- | --- |
| `ILD1` | Full legacy capture (`l3dump`) | Up to 9; meanings in the header | Existing; leave unchanged | `dump_format.h` (`L3_DUMP_MAGIC`, `L3_DUMP_VERSION_*`) |
| `ILE1` / `ILT1` | Capture evidence block / dump transfer trailer | Existing layout | Existing; leave unchanged | `dump_format.h` (`L3_CAPTURE_EVIDENCE_MAGIC`, `L3_DUMP_TRAILER_MAGIC`) |
| `OFC2` | Compact clock reply (`cp`, 44 bytes) | Fixed packet layout | Existing; leave unchanged | `clock_compact_handler.h`, `clock_compact.h` |
| `ILO1` | Stage-2 overview (round 1 of the two-round transfer) | 1 | Implemented (`L3_TWO_ROUND`) | `two_round_format.h` (`L3_TWO_ROUND_OVERVIEW_*`, `L3TR_OV_*`) |
| `ILC1` | Stage-2 selected cells (round 2, IQ16) | 1 | Implemented (`L3_TWO_ROUND`) | `two_round_format.h` (`L3_TWO_ROUND_CELLS_*`, `L3TR_CL_*`) |
| `ILQ1` | Stage-2 cell request (host to radar) | 1 | Implemented (`L3_TWO_ROUND`) | `two_round_format.h` (`L3_TWO_ROUND_REQUEST_*`, `L3TR_RQ_*`) |

The text `clockProbe` reply (`OFCLKPROBE1`, `OFCLKSTAMP1`, `OFCLKPROBEEND1`
lines) is not binary and is not listed.

`ILO1`, `ILC1` and `ILQ1` follow `src/openflight/iwr6843/two_round.py`
byte for byte (little-endian; 44-, 28- and 12-byte headers; a declared total
length; a CRC-32 over every byte before it). `two_round_format.h` mirrors its
constants and field offsets, and `tests/test_iwr6843_firmware_two_round.py`
checks them against that module.

## The CLI around them

These are properties of this firmware's CLI port (SDK 03.06 CLI, UART read
in TEXT mode with echo):

- **Echo:** every command character is echoed as it arrives, so a command line
  `overview\n` comes back as `overview\n` before any reply. A carriage return
  is echoed as `\r\n`. Binary request bytes (below) are not echoed.
- **Line length:** the SDK reads a command into a 256-byte buffer, so a
  command line is at most 255 bytes including its newline. A longer line is
  cut, and its remainder runs as a second command.
- **Success:** a handler that succeeds is followed by `Done\n` and the prompt
  `l3dump:/>`.
- **Errors:** a stage-2 handler that refuses writes one line
  `Error: <reason>\n`; the CLI then adds `Error -1\n` and the prompt. The reply
  never contains `Done`.

## The two-round session

1. **`overview`**: with nothing frozen, this is the shot. It freezes the
   ring exactly as `l3dump` does (the request is stamped, the configured post
   frames are kept, the front end stops at a frame boundary) and starts a
   session. Reply: the `ILO1` payload, `\n`, `Done\n`, prompt. While that
   session is frozen, `overview` sends the same capture's overview again,
   byte for byte (a reconnecting host).
2. **`cells <session, 8 hex digits> <request bytes, decimal>`**: the request
   does not fit a command line (a hex request runs to 686 characters), so
   it travels in binary after a handshake:
   - the radar checks the session and size, then writes `ready <bytes>\n`;
   - only then does the host write exactly that many bytes: the whole `ILQ1`
     request, CRC included, with no newline. The UART has no receive FIFO,
     so bytes sent before `ready` are lost;
   - the radar waits at most 1000 ms for them, then decodes the request with
     the reference's checks. Reply: the `ILC1` payload, `\n`, `Done\n`,
     prompt.
   Any number of requests may follow on one frozen capture.
3. **`release <session>`**: rearms the ring. Reply: `released <session>\n`,
   `Done\n`, prompt.

While a session is frozen, only `overview`, `cells`, `release` and `help`
run. Every other command (`l3dump`, `sensorStart`, `sensorStop`, the capture
configuration commands, `stats`, `clockProbe`, `cp`, ...) is refused with
`Error: session <id> frozen: only overview, cells, release`. The SDK's
mmWave-extension configuration commands are switched off while frozen; they
answer `'<name>' is not recognized as a CLI command`.

A `cells` or `release` naming another session is refused with
`Error: stale session <id> (frozen <id>)`, and the capture stays frozen.
`cells` or `release` with nothing frozen answers `Error: no frozen capture`.

**Session id:** a 16-bit counter (never 0) in the high half over bits 8-23 of
the freeze tick in the low half. The counter makes it unique within a boot
for 65,535 sessions; the tick makes a session id from an earlier boot
unlikely to match.

**Hold timeout:** a session with no `overview` or `cells` for 30 s is
rearmed by the radar itself. Nothing is written to the port when this
happens. The next session's overview carries flag `0x0001`
(`FLAG_PREVIOUS_SESSION_TIMED_OUT`); a `release` clears it for the session
after.

## Evidence ticks in an overview

An overview carries the dump's `ILE1` block and `ILT1` trailer, but it
streams no IQ payload, so two ticks are redefined for it. Both are taken
once, by the session's first overview; a repeated overview carries the same
values.

- **`ILE1.header_tick`**: the session's dump prefix is complete. This is just
  before the overview's summary (sums and noise medians) is computed, after
  the freeze, stop and temperature read.
- **`ILT1.payload_end_tick`**: the summary is computed. This is just before
  the first overview byte is handed to the UART.

So `payload_end_tick - header_tick` is the radar's summary compute time, not
a transfer time. `request_tick`, `request_seq`, `freeze_tick`, `stop_tick`
and the frame records mean what they mean in a dump. Cells replies carry no
ticks.

## Computation the overview must reproduce

The radar computes the overview from its frozen ring as
`two_round.build_overview` does, and `two_round_core.h` matches it byte for
byte:

- **Sums:** exact int32 sums.
- **MTI power:** float64. The numerator is an int64 converted exactly; the
  denominator is `L^2` (burst) or `n^2` (window).
- **Noise medians:** float64, as numpy's median (the mean of the two middle
  values), rounded once to float32. They are found by an exact radix select
  over the float64 bit patterns: 11-bit histogram passes, then the last
  bucket is collected.
- **Map:** float64 sums over (TX, RX) in TX-major order.
- **log16 code:** `round(5000 * log10(max(p, 1)))`, computed exactly from a
  generated threshold table (`two_round_log16.h`, from `gen_log16_table.py`)
  without libm.

## Build support

`L3_TWO_ROUND` needs the configurable hybrid-cadence production build with
evidence:
- `CONFIGURABLE_CAPTURE`, `HYBRID_CADENCE_CAPTURE`;
- `HWA_CHAINED_SNAPSHOT_RING`, `SNAPSHOT_DUMP`;
- `N_TX=3`.

At run time it serves only an IQ16 capture (`captureFormat iq16`, the wide
IQ16 profile the recorded dumps came from).

A build without capture evidence (no `HYBRID_CADENCE_CAPTURE`) is
unsupported: the unmodified base already fails to compile that way.
