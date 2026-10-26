#ifndef OPENFLIGHT_CLOCK_COMPACT_H
#define OPENFLIGHT_CLOCK_COMPACT_H

#include <stdint.h>

#define L3_COMPACT_PACKET_BYTES 44U

static void l3_compactPut32(uint8_t *bytes, uint32_t value)
{
    uint32_t i;
    for (i = 0U; i < 4U; i++) bytes[i] = (uint8_t)(value >> (i * 8U));
}

static uint32_t l3_compactCRC32(const uint8_t *bytes, uint32_t count)
{
    uint32_t crc = 0xFFFFFFFFU;
    uint32_t i, bit;
    for (i = 0U; i < count; i++) {
        crc ^= bytes[i];
        for (bit = 0U; bit < 8U; bit++)
            crc = (crc >> 1U) ^ ((crc & 1U) ? 0xEDB88320U : 0U);
    }
    return crc ^ 0xFFFFFFFFU;
}

#endif
