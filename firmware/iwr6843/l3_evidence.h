/* Pure evidence helpers for the L3 dump firmware (W4a).
 *
 * No SDK types: the host test suite compiles this header with the Mac's C compiler
 * (tests/test_iwr6843_firmware_evidence.py) to check it on known answers. That check
 * does not replace the owner's TI build.
 */
#ifndef L3_EVIDENCE_H
#define L3_EVIDENCE_H

#include <stdint.h>

/* Count the int16 components a low-byte copy would change (those outside int8,
 * -128..127) and return the largest magnitude through maxAbsOut. -32768's magnitude,
 * 32768, fits the uint16 output. */
static uint32_t l3_iq8ScanComponents(const int16_t *source, uint32_t components,
                                     uint16_t *maxAbsOut)
{
    uint32_t overflow = 0U;
    uint32_t maxAbs = 0U;
    uint32_t i;

    for (i = 0U; i < components; i++) {
        int32_t value = (int32_t)source[i];
        uint32_t magnitude = (value < 0) ? (uint32_t)(-value) : (uint32_t)value;

        if (value > 127 || value < -128) {
            overflow++;
        }
        if (magnitude > maxAbs) {
            maxAbs = magnitude;
        }
    }
    *maxAbsOut = (uint16_t)maxAbs;
    return overflow;
}

#endif /* L3_EVIDENCE_H */
