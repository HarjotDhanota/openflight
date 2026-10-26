#ifndef OPENFLIGHT_GATE_CLOCK_H
#define OPENFLIGHT_GATE_CLOCK_H

#include <stdint.h>

#define L3_GATE_CLOCK_CAPACITY 64U

typedef struct L3GateClockEvent_t {
    uint32_t seq_hi;
    uint32_t seq_lo;
    uint32_t tick;
    uint32_t frame_seq;
    uint32_t level;
} L3GateClockEvent;

typedef struct L3GateClock_t {
    uint32_t nonce_hi;
    uint32_t nonce_lo;
    uint32_t seq_hi;
    uint32_t seq_lo;
    uint32_t dropped;
    uint32_t discarded;
    uint32_t unattached;
    uint32_t read_slot;
    uint32_t write_slot;
    uint32_t count;
    L3GateClockEvent events[L3_GATE_CLOCK_CAPACITY];
} L3GateClock;

static inline uint32_t l3_gateSaturatingAdd(uint32_t value, uint32_t amount)
{
    return (UINT32_MAX - value < amount) ? UINT32_MAX : value + amount;
}

static inline int l3_gateParseHex8(const char *text, uint32_t *out)
{
    uint32_t value = 0U;
    uint32_t index;
    for (index = 0U; index < 8U; index++) {
        uint32_t digit;
        char c = text[index];
        if (c >= '0' && c <= '9') digit = (uint32_t)(c - '0');
        else if (c >= 'a' && c <= 'f') digit = (uint32_t)(c - 'a') + 10U;
        else if (c >= 'A' && c <= 'F') digit = (uint32_t)(c - 'A') + 10U;
        else return 0;
        value = (value << 4U) | digit;
    }
    if (text[8] != '\0') return 0;
    *out = value;
    return 1;
}

/* The caller serializes attach/pop against the interrupt producer. */
static inline int l3_gateAttach(volatile L3GateClock *state, uint32_t hi, uint32_t lo)
{
    if ((hi | lo) == 0U) return -1;
    if (state->nonce_hi == hi && state->nonce_lo == lo) return 0;
    state->discarded = l3_gateSaturatingAdd(state->discarded, state->count);
    state->count = 0U;
    state->read_slot = 0U;
    state->write_slot = 0U;
    state->nonce_hi = hi;
    state->nonce_lo = lo;
    return 1;
}

static inline int l3_gatePush(volatile L3GateClock *state, uint32_t tick,
                            uint32_t frame_seq, uint32_t level)
{
    L3GateClockEvent event;
    event.seq_hi = state->seq_hi;
    event.seq_lo = state->seq_lo;
    event.tick = tick;
    event.frame_seq = frame_seq;
    event.level = level;
    state->seq_lo++;
    if (state->seq_lo == 0U) state->seq_hi++;
    if ((state->nonce_hi | state->nonce_lo) == 0U) {
        state->unattached = l3_gateSaturatingAdd(state->unattached, 1U);
        return 0;
    }
    if (state->count == L3_GATE_CLOCK_CAPACITY) {
        state->dropped = l3_gateSaturatingAdd(state->dropped, 1U);
        return 0;
    }
    state->events[state->write_slot] = event;
    state->write_slot = (state->write_slot + 1U) % L3_GATE_CLOCK_CAPACITY;
    state->count++;
    return 1;
}

static inline int l3_gatePop(volatile L3GateClock *state, L3GateClockEvent *event)
{
    if (state->count == 0U) return 0;
    *event = state->events[state->read_slot];
    state->read_slot = (state->read_slot + 1U) % L3_GATE_CLOCK_CAPACITY;
    state->count--;
    return 1;
}

#endif
