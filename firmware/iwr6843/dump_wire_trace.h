#ifndef L3_DUMP_WIRE_TRACE_H
#define L3_DUMP_WIRE_TRACE_H

#include <stdint.h>
#include <string.h>

typedef struct {
    uint32_t calls, requested, returned, errors, mismatches;
    uint32_t first_call, crc;
    int32_t first_result;
} l3_wire_trace_t;

static inline void l3_wire_reset(l3_wire_trace_t *state)
{
    memset(state, 0, sizeof(*state));
    state->crc = 0xFFFFFFFFU;
}

static inline void l3_wire_begin_write(l3_wire_trace_t *state,
                                      const uint8_t *data, uint32_t size)
{
    uint32_t i, bit, crc = state->crc;
    state->calls++;
    state->requested += size;
    for (i = 0U; i < size; i++) {
        crc ^= data[i];
        for (bit = 0U; bit < 8U; bit++) {
            crc = (crc >> 1U) ^ ((crc & 1U) ? 0xEDB88320U : 0U);
        }
    }
    state->crc = crc;
}

static inline void l3_wire_end_write(l3_wire_trace_t *state,
                                    uint32_t size, int32_t result)
{
    if (result < 0) state->errors++;
    else state->returned += (uint32_t)result;
    if (result < 0 || (uint32_t)result != size) {
        state->mismatches++;
        if (state->first_call == 0U) {
            state->first_call = state->calls;
            state->first_result = result;
        }
    }
}

static inline uint32_t l3_wire_crc32(const l3_wire_trace_t *state)
{
    return state->crc ^ 0xFFFFFFFFU;
}

#endif
