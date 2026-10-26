#ifndef OPENFLIGHT_USB_CLOCK_PROBE_H
#define OPENFLIGHT_USB_CLOCK_PROBE_H

#include "gate_clock.h"
#include "clock_probe_build_id.h"

static uint32_t gClockBootHi;
static uint32_t gClockBootLo;

static int32_t l3_cli_clockProbe(int32_t argc, char *argv[])
{
    uint32_t rxTick = l3_ticks();
    uint32_t hi, lo, seq, txTick, frameSeq;
    if (argc != 4 || !l3_gateParseHex8(argv[1], &hi) ||
        !l3_gateParseHex8(argv[2], &lo) || !l3_gateParseHex8(argv[3], &seq) ||
        (hi | lo) == 0U) return -1;
    if ((gClockBootHi | gClockBootLo) == 0U) {
        gClockBootHi = hi;
        gClockBootLo = lo;
    }
    frameSeq = gNumFrame;
    txTick = l3_ticks();
    CLI_write("OFCLKPROBE1 build=%s request=%08x%08x boot=%08x%08x seq=%u tick_hz=%u counter_bits=32\n",
              L3_USB_CLOCK_BUILD_ID, (unsigned)hi, (unsigned)lo,
              (unsigned)gClockBootHi, (unsigned)gClockBootLo,
              (unsigned)seq, (unsigned)gCpuClock);
    CLI_write("OFCLKSTAMP1 rx_tick=%u tx_tick=%u frame_seq=%u physical=unqualified\n",
              (unsigned)rxTick, (unsigned)txTick, (unsigned)frameSeq);
    CLI_write("OFCLKPROBEEND1 request=%08x%08x seq=%u\n",
              (unsigned)hi, (unsigned)lo, (unsigned)seq);
    return 0;
}

#ifdef L3_USB_CLOCK_COMPACT
#include "clock_compact_handler.h"
#endif

#endif
