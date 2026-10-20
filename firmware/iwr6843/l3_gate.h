/* GATE retention state. Callers serialize access against GPIO/HWA interrupts. */
#ifndef L3_GATE_H
#define L3_GATE_H

#include <string.h>
#include "dump_format.h"

#define L3_GATE_MAX_READ_DELAY_US 250000U

static inline uint8_t l3_gateFresh(uint32_t elapsedTicks, uint32_t tickPeriodUs)
{
    return tickPeriodUs != 0U &&
           (uint64_t)elapsedTicks * tickPeriodUs <= L3_GATE_MAX_READ_DELAY_US;
}

typedef struct {
    l3_gate_evidence_t evidence;
    uint32_t raw_seq;
    uint8_t armed;
} l3_gate_state_t;

static inline void l3_gateArm(volatile l3_gate_state_t *gate)
{
    uint32_t arm = gate->evidence.arm_seq + 1U;
    memset((void *)&gate->evidence, 0, sizeof(gate->evidence));
    memcpy((void *)gate->evidence.magic, L3_GATE_EVIDENCE_MAGIC, 4U);
    gate->evidence.arm_seq = arm;
    gate->armed = 1U;
}

static inline void l3_gateDisarm(volatile l3_gate_state_t *gate)
{
    gate->armed = 0U;
}

static inline uint8_t l3_gateLatch(volatile l3_gate_state_t *gate, uint32_t tick,
                                  uint32_t frameSeq, uint8_t eligible)
{
    gate->raw_seq++;
    if (!gate->armed || !eligible || (gate->evidence.flags & L3_GATE_FLAG_LATCHED)) {
        if (gate->evidence.ignored_edges != UINT32_MAX) {
            gate->evidence.ignored_edges++;
        }
        return 0U;
    }
    gate->evidence.edge_seq = gate->raw_seq;
    gate->evidence.edge_tick = tick;
    gate->evidence.frame_seq = frameSeq;
    gate->evidence.flags = L3_GATE_FLAG_LATCHED;
    return 1U;
}

static inline void l3_gateComplete(volatile l3_gate_state_t *gate, uint32_t tick)
{
    if (gate->armed && gate->evidence.flags == L3_GATE_FLAG_LATCHED) {
        gate->evidence.freeze_tick = tick;
        gate->evidence.flags |= L3_GATE_FLAG_FROZEN;
    }
}

#endif
