#ifndef OPENFLIGHT_CLOCK_COMPACT_HANDLER_H
#define OPENFLIGHT_CLOCK_COMPACT_HANDLER_H

#include "clock_compact.h"

static int32_t l3_cli_cp(int32_t argc, char *argv[])
{
    uint32_t rxTick = l3_ticks();
    uint32_t hi, lo, seq;
    uint8_t packet[L3_COMPACT_PACKET_BYTES];
    if (argc != 4 || !l3_gateParseHex8(argv[1], &hi) ||
        !l3_gateParseHex8(argv[2], &lo) || !l3_gateParseHex8(argv[3], &seq) ||
        (hi | lo) == 0U) return -1;
    if ((gClockBootHi | gClockBootLo) == 0U) {
        gClockBootHi = hi;
        gClockBootLo = lo;
    }
    memcpy(packet, "OFC2", 4U);
    l3_compactPut32(&packet[4], hi);
    l3_compactPut32(&packet[8], lo);
    l3_compactPut32(&packet[12], gClockBootHi);
    l3_compactPut32(&packet[16], gClockBootLo);
    l3_compactPut32(&packet[20], seq);
    l3_compactPut32(&packet[24], rxTick);
    l3_compactPut32(&packet[32], gCpuClock);
    l3_compactPut32(&packet[36], gNumFrame);
    l3_compactPut32(&packet[28], l3_ticks());
    l3_compactPut32(&packet[40], l3_compactCRC32(packet, 40U));
    if (UART_writePolling(gCliUart, packet, sizeof(packet)) != (int32_t)sizeof(packet)) return -1;
    return 0;
}

#endif
