# IWR UART payload registry

All numeric fields are little-endian. The C two-round definitions live in
`two_round_format.h`; the matching host is `src/openflight/iwr6843/two_round.py`.

| Magic | Purpose | Version and framing |
|---|---|---|
| `ILD1` | Complete capture | Legacy versions 1-9 in `dump_format.h`; no reassignment |
| `ILE1` | Capture evidence inside a dump prefix | Fixed sizes declared by its header |
| `ILT1` | Legacy dump trailer | Eight bytes; UART payload-end tick |
| `OFC2` | Compact USB clock reply | 44 bytes, including final IEEE CRC32 |
| `ILO1` | Frozen-session overview | Version 1; 44-byte header, declared total bytes, IEEE CRC32 |
| `ILC1` | Selected raw IQ16 cells | Version 1; 28-byte header, declared total bytes, IEEE CRC32 |
| `ILQ1` | Cell request, hex-encoded in a CLI argument | Version 1; 12-byte header, per-frame runs, IEEE CRC32 |

Two-round CRC coverage is every byte before the final four-byte CRC. The
overview retains capture dimensions, session identity, original dump prefix
and suffix, signed int32 sums, two float32 noise medians and log16 power maps.
The cells reply echoes the session, request sequence and request CRC. Its
samples are `[cell][chirp][RX][Im, Re]`, signed int16. No third-party format is used.

Commands are `overview`, `cells <request hex>`, and `release <session hex8>`.
A successful binary response is followed by `Done` and `l3dump:/>`; successful
release also returns `released <session hex8>`. CLI lines, including their
newline, must fit 255 bytes. The host splits runs into several requests when
needed; all refer to the same immutable capture. Reject invalid dimensions,
CRC, sequence zero, out-of-range/overlapping runs and stale sessions.

The C core accepts three TX, four RX, up to 64 frames, 16 loops and 128 absolute
bins. It streams through a 512-byte buffer. Exact medians use a 256-counter
radix selection over positive binary64 powers; no whole-cube power array or
whole-window map is allocated. Compute cost on the radar remains unmeasured.
The core itself does not start RF, own a frozen session or implement recovery;
those responsibilities belong to the CLI integration. A registry entry is not
an advertisement that the command is installed or hardware-qualified.

Overview flag bit 0 records timeout recovery of a previous session. Bit 1
identifies live metadata snapshots: the prefix `header_tick` and suffix
`payload_end_tick` then mark metadata assembly and readiness, respectively.
No complete raw dump crossed the UART. Updated host parsing preserves the
raw evidence and its source through sparse-file replay and TX projection,
and withholds the legacy UART transfer durations. Request, freeze, stop and
per-frame timestamps retain their acquisition meanings.
