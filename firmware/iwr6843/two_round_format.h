#ifndef OPENFLIGHT_TWO_ROUND_FORMAT_H
#define OPENFLIGHT_TWO_ROUND_FORMAT_H

/* Stage-2 two-round transfer: wire identities only. PLACEHOLDER.
 *
 * Round 1 streams an overview of the frozen capture (ILO1); round 2 streams
 * the cells the host chose, at IQ16 (ILC1); `release` rearms. Each payload
 * type has its own magic and starts at version 1. Neither reuses ILD1, whose
 * versions mean the legacy full capture (see WIRE_FORMATS.md).
 *
 * The layout (endianness, header size, payload length, CRC coverage, the
 * capture/session identity and dimensions, the echoed request identity, and
 * how the per-frame evidence travels) is frozen first in the host protocol
 * module and copied here in phase F2. Nothing below is a layout yet: no
 * firmware writes these payloads and no host should parse them. */

#define L3_TWO_ROUND_OVERVIEW_MAGIC   "ILO1"
#define L3_TWO_ROUND_OVERVIEW_VERSION 1U
#define L3_TWO_ROUND_CELLS_MAGIC      "ILC1"
#define L3_TWO_ROUND_CELLS_VERSION    1U

#endif
