#ifndef OPENFLIGHT_TWO_ROUND_FORMAT_H
#define OPENFLIGHT_TWO_ROUND_FORMAT_H

#include <stdint.h>

#define L3_TWO_OVERVIEW_MAGIC "ILO1"
#define L3_TWO_CELLS_MAGIC "ILC1"
#define L3_TWO_REQUEST_MAGIC "ILQ1"
#define L3_TWO_VERSION 1U
#define L3_TWO_TIMEOUT_FLAG 1U
#define L3_TWO_MAX_REQUEST_BYTES 124U
#define L3_TWO_MAX_FRAMES 64U
#define L3_TWO_MAX_BINS 128U
#define L3_TWO_MAX_LOOPS 16U

typedef struct __attribute__((packed)) {
    char magic[4];
    uint16_t version, header_bytes;
    uint32_t total_bytes, session;
    uint16_t frames, chirps;
    uint8_t tx, rx;
    uint16_t bins, summed_bins, map_cells;
    uint8_t map_code, scopes;
    float burst_noise, window_noise;
    uint16_t prefix_bytes, suffix_bytes, flags;
} l3_two_overview_t;

typedef struct __attribute__((packed)) {
    char magic[4];
    uint16_t version, header_bytes;
    uint32_t total_bytes, session;
    uint8_t sequence, reserved;
    uint16_t cells;
    uint32_t request_crc, cell_bytes;
} l3_two_cells_t;

typedef char l3_two_overview_size[(sizeof(l3_two_overview_t) == 44U) ? 1 : -1];
typedef char l3_two_cells_size[(sizeof(l3_two_cells_t) == 28U) ? 1 : -1];

#endif
