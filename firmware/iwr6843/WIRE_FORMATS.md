# IWR6843 binary wire formats

Every binary payload the firmware writes to the host starts with a 4-byte
ASCII magic. Each payload type has its own magic, and its version numbers
belong to that magic alone. Never give an existing magic a new version for a
different kind of payload. A new payload type gets a new magic, starting at
version 1. Add a row here before any firmware or host code uses it.

| Magic | Payload | Versions | Status | Defined in |
| --- | --- | --- | --- | --- |
| `ILD1` | Full legacy capture (`l3dump`) | Up to 9; meanings in the header | Existing; leave unchanged | `dump_format.h` (`L3_DUMP_MAGIC`, `L3_DUMP_VERSION_*`) |
| `ILE1` / `ILT1` | Capture evidence block / dump transfer trailer | Existing layout | Existing; leave unchanged | `dump_format.h` (`L3_CAPTURE_EVIDENCE_MAGIC`, `L3_DUMP_TRAILER_MAGIC`) |
| `OFC2` | Compact clock reply (`cp`, 44 bytes) | Fixed packet layout | Existing; leave unchanged | `clock_compact_handler.h`, `clock_compact.h` |
| `ILO1` | Stage-2 overview (round 1 of the two-round transfer) | 1 | Reserved; layout not frozen | `two_round_format.h` (`L3_TWO_ROUND_OVERVIEW_*`) |
| `ILC1` | Stage-2 selected cells (round 2, IQ16) | 1 | Reserved; layout not frozen | `two_round_format.h` (`L3_TWO_ROUND_CELLS_*`) |

The text `clockProbe` reply (`OFCLKPROBE1`, `OFCLKSTAMP1`, `OFCLKPROBEEND1`
lines) is not binary and is not listed.

`ILO1` and `ILC1` are allocated in the firmware/host coordination of
26 Oct 2026. Their byte layout is frozen first in the host protocol module,
then copied into `two_round_format.h`. Until then no firmware writes them.
