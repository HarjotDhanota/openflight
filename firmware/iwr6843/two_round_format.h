#ifndef OPENFLIGHT_TWO_ROUND_FORMAT_H
#define OPENFLIGHT_TWO_ROUND_FORMAT_H

/* Stage-2 two-round transfer wire format, v1.
 *
 * MUST stay byte-for-byte in sync with src/openflight/iwr6843/two_round.py
 * (OVERVIEW_HEADER "<4sHHIIHHBBHHHBBffHHH", REQUEST_HEADER "<4sBBHI",
 * CELLS_HEADER "<4sHHIIBBHII"); tests/test_iwr6843_firmware_two_round.py
 * checks every constant and offset below against that module.
 *
 * Every integer is little-endian. Every binary payload starts with its fixed
 * header, declares its total length (header to CRC inclusive) and ends with a
 * CRC-32 (IEEE 802.3 / zlib.crc32) over every byte before it.
 *
 * Overview (ILO1): header, then the dump prefix (dump header, temperature
 * report, timed descriptors, ILE1 block, frame records) and the dump suffix
 * (ILT1 trailer) exactly as l3dump would send them, then int32 sums
 * [held absolute bin ascending][TX1, TX3][RX][re, im], then uint16 log16
 * codes [burst, window][frame][loop][local bin], then the CRC.
 * Request (ILQ1): header, then per frame a run count (u8) and that many
 * (first local bin, length) u8 pairs, then the CRC.
 * Cells (ILC1): header, then each requested cell (frame ascending, bin
 * ascending) as [chirp][RX] int16 (Im, Re) pairs, then the CRC.
 * WIRE_FORMATS.md documents the CLI exchange around them. */

#define L3_TWO_ROUND_OVERVIEW_MAGIC   "ILO1"
#define L3_TWO_ROUND_OVERVIEW_VERSION 1U
#define L3_TWO_ROUND_CELLS_MAGIC      "ILC1"
#define L3_TWO_ROUND_CELLS_VERSION    1U
#define L3_TWO_ROUND_REQUEST_MAGIC    "ILQ1"
#define L3_TWO_ROUND_REQUEST_VERSION  1U

#define L3_TWO_ROUND_CRC_BYTES 4U

/* Overview header (44 bytes): field offsets. */
#define L3TR_OV_HEADER_BYTES   44U
#define L3TR_OV_MAGIC          0U   /* char[4] */
#define L3TR_OV_VERSION        4U   /* u16 */
#define L3TR_OV_HEADER_NBYTES  6U   /* u16 */
#define L3TR_OV_TOTAL_NBYTES   8U   /* u32 */
#define L3TR_OV_SESSION        12U  /* u32 */
#define L3TR_OV_N_FRAMES       16U  /* u16 */
#define L3TR_OV_CHIRPS         18U  /* u16 */
#define L3TR_OV_N_TX           20U  /* u8 */
#define L3TR_OV_N_RX           21U  /* u8 */
#define L3TR_OV_N_SAMPLES      22U  /* u16: widest frame */
#define L3TR_OV_N_SUM_BINS     24U  /* u16: absolute bins any frame holds */
#define L3TR_OV_N_MAP_CELLS    26U  /* u16: sum of the per-frame bin counts */
#define L3TR_OV_MAP_CODE       28U  /* u8 */
#define L3TR_OV_N_SCOPES       29U  /* u8 */
#define L3TR_OV_NOISE_BURST    30U  /* f32 */
#define L3TR_OV_NOISE_WINDOW   34U  /* f32 */
#define L3TR_OV_PREFIX_NBYTES  38U  /* u16 */
#define L3TR_OV_SUFFIX_NBYTES  40U  /* u16 */
#define L3TR_OV_FLAGS          42U  /* u16 */

/* Request header (12 bytes). */
#define L3TR_RQ_HEADER_BYTES   12U
#define L3TR_RQ_MAGIC          0U   /* char[4] */
#define L3TR_RQ_VERSION        4U   /* u8 */
#define L3TR_RQ_SEQUENCE       5U   /* u8: 1, 2, ... */
#define L3TR_RQ_N_FRAMES       6U   /* u16 */
#define L3TR_RQ_SESSION        8U   /* u32 */

/* Cells header (28 bytes). */
#define L3TR_CL_HEADER_BYTES   28U
#define L3TR_CL_MAGIC          0U   /* char[4] */
#define L3TR_CL_VERSION        4U   /* u16 */
#define L3TR_CL_HEADER_NBYTES  6U   /* u16 */
#define L3TR_CL_TOTAL_NBYTES   8U   /* u32 */
#define L3TR_CL_SESSION        12U  /* u32 */
#define L3TR_CL_SEQUENCE       16U  /* u8 */
#define L3TR_CL_RESERVED       17U  /* u8 */
#define L3TR_CL_N_CELLS        18U  /* u16 */
#define L3TR_CL_REQUEST_CRC    20U  /* u32: the request's CRC-32 */
#define L3TR_CL_CELL_NBYTES    24U  /* u32 */

/* Overview flags. */
#define L3TR_FLAG_PREVIOUS_SESSION_TIMED_OUT 0x0001U

/* Power map coding: code = round(5000 * log10(max(power, 1))), clipped. */
#define L3TR_POWER_CODE_LOG16      1U
#define L3TR_LOG16_CODES_PER_DECADE 5000U
#define L3TR_LOG16_MAX_CODE        0xFFFFU
#define L3TR_N_SCOPES              2U   /* burst, then window */

/* The protocol carries 3-TX TDM captures; TX1 and TX3 are summed. */
#define L3TR_N_LOOP_TX 3U

/* CLI commands and session. */
#define L3TR_CMD_OVERVIEW "overview"
#define L3TR_CMD_CELLS    "cells"
#define L3TR_CMD_RELEASE  "release"
#define L3TR_HOLD_TIMEOUT_MS 30000U

#endif
