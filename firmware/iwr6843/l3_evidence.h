/* Pure evidence helpers for the L3 dump firmware (W4a).
 *
 * No SDK types: the host test suite compiles this header with the Mac's C compiler
 * (tests/test_iwr6843_firmware_evidence.py) to check it on known answers. That check
 * does not replace the owner's TI build. Every helper is static inline, so a build that
 * uses none of them gets no unused-function diagnostic.
 */
#ifndef L3_EVIDENCE_H
#define L3_EVIDENCE_H

#include <stdint.h>

/* Count the int16 components a low-byte copy would change (those outside int8,
 * -128..127) and return the largest magnitude through maxAbsOut. -32768's magnitude,
 * 32768, fits the uint16 output. */
static inline uint32_t l3_iq8ScanComponents(const int16_t *source, uint32_t components,
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

/* The capture may claim its IQ8 scan only when no frame can reuse the slot being scanned
 * while the scan runs. The HWA processes at most one more frame during a scan (the rearm
 * task arms the next frame before scanning); with a single pre frame that next frame
 * lands in the same ring slot, and its output-done record could be tagged with the
 * scanned frame's counts. Two or more pre frames (post frames never share slots) rule
 * that out. Returns 1 when the scan's per-frame records can be trusted. */
#define L3_IQ8_SCAN_MIN_PRE_FRAMES 2U

static inline uint8_t l3_iq8ScanGuaranteed(uint8_t usesIq8, uint32_t preFrames)
{
    return (usesIq8 && preFrames >= L3_IQ8_SCAN_MIN_PRE_FRAMES) ? 1U : 0U;
}

/* One pass over complexCount interleaved int16 (I, Q) pairs: sum of I*I + Q*Q (l3bench).
 * Each square is at most 2^30, so the 32-bit product is exact; the two are added in 64 bits
 * because their sum can reach 2^31. */
static inline uint64_t l3_powerSumIq16(const int16_t *iq, uint32_t complexCount)
{
    uint64_t sum = 0U;
    uint32_t i;

    for (i = 0U; i < complexCount; i++) {
        int32_t a = (int32_t)iq[2U * i];
        int32_t b = (int32_t)iq[2U * i + 1U];

        sum += (uint64_t)(uint32_t)(a * a) + (uint64_t)(uint32_t)(b * b);
    }
    return sum;
}

/* The same pass over int8 pairs (the IQ8 ring, before its per-frame scale). */
static inline uint64_t l3_powerSumIq8(const int8_t *iq, uint32_t complexCount)
{
    uint64_t sum = 0U;
    uint32_t i;

    for (i = 0U; i < complexCount; i++) {
        int32_t a = (int32_t)iq[2U * i];
        int32_t b = (int32_t)iq[2U * i + 1U];

        sum += (uint64_t)(uint32_t)(a * a + b * b);
    }
    return sum;
}

#endif /* L3_EVIDENCE_H */
