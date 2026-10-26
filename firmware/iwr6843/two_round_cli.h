#ifndef OPENFLIGHT_TWO_ROUND_CLI_H
#define OPENFLIGHT_TWO_ROUND_CLI_H

#if !defined(CONFIGURABLE_CAPTURE) || !defined(HWA_CHAINED_SNAPSHOT_RING) || N_TX != 3
#error "L3_TWO_ROUND needs configurable three-TX HWA capture"
#endif
#ifdef L3_DUMP_IQ8
#error "L3_TWO_ROUND cannot use a build that only emits IQ8"
#endif

#include <ti/sysbios/knl/Clock.h>
#include "two_round_core.h"

static l3_two_state_t gTwoState;
static l3_two_output_t gTwoOutput;
static uint8_t gTwoPrefix[44U + L3_MAX_CAPTURE_FRAMES * 4U + 40U + L3_MAX_CAPTURE_FRAMES * 20U];
#ifdef L3_CAPTURE_EVIDENCE
static uint8_t gTwoSuffix[8U];
#endif
static uint8_t gTwoRequestWire[L3_TWO_MAX_REQUEST_BYTES];
static volatile uint8_t gTwoHeld, gTwoBusy, gTwoFault;
static uint32_t gTwoNextSession = 1U, gTwoLastActivity, gTwoTimeouts, gTwoLastTimeout;
static uint8_t gTwoPreviousTimeout;
static uint16_t gTwoFlags;

static int32_t l3_two_uartWrite(void *context, const uint8_t *data, uint32_t bytes)
{
    (void)context;
    return UART_writePolling(gDataUart, (uint8_t *)data, bytes);
}

static int32_t l3_two_rearm(void)
{
    gRingFrame = 0U;
    gHwaFreezeRequestFrame = 0U;
    gHwaFreezeTargetFrame = 0U;
    gPreFramesCaptured = 0U;
    gPostFramesCaptured = 0U;
    gPostFramesObserved = 0U;
    gPostCaptureStarted = 0U;
    gActiveFrameIsPost = 0U;
    gActiveFrameShouldKeep = 1U;
#ifdef L3_CAPTURE_EVIDENCE
    l3_resetEvidence();
#endif
    if (l3_restartCompletedHwaFrame() < 0) return -1;
    gHwaFreezeRestarts++;
    gCaptureActive = 1U;
    if (l3_startFrontEnd() < 0) {
        gCaptureActive = 0U;
        return -1;
    }
    return 0;
}

static int32_t l3_two_closeSession(uint8_t timedOut)
{
    int32_t status;
    if (timedOut) {
        gTwoTimeouts++;
        gTwoLastTimeout = gTwoState.session;
        gTwoPreviousTimeout = 1U;
    }
    status = l3_two_rearm();
    gTwoHeld = 0U;
    if (status != 0) gTwoFault = 1U;
    return status;
}

static void l3_two_watchTask(UArg arg0, UArg arg1)
{
    uint32_t timeout = 30000000U / Clock_tickPeriod;
    uint32_t poll = 100000U / Clock_tickPeriod;
    (void)arg0; (void)arg1;
    for (;;) {
        uintptr_t key;
        uint8_t recover = 0U;
        Task_sleep(poll ? poll : 1U);
        key = Hwi_disable();
        if (gTwoHeld && !gTwoBusy && (uint32_t)(Clock_getTicks() - gTwoLastActivity) > timeout) {
            gTwoBusy = 1U;
            recover = 1U;
        }
        Hwi_restore(key);
        if (recover) {
            (void)l3_two_closeSession(1U);
            gTwoBusy = 0U;
        }
    }
}

static void l3_two_append(uint32_t *at, const void *data, uint32_t bytes)
{
    memcpy(gTwoPrefix + *at, data, bytes);
    *at += bytes;
}

static int32_t l3_two_snapshot(void)
{
    l3_dump_header_t header;
    l3_temperature_report_t temperature;
    l3_two_capture_t capture;
    uint32_t pre, post, oldest, f, slot, at = 0U;
    uint8_t slots[L3_MAX_CAPTURE_FRAMES];
    int32_t tempStatus;
    if (!gCaptureActive || l3_captureUsesIq8()) return -1;
#ifdef L3_CAPTURE_EVIDENCE
    {
        uintptr_t key = Hwi_disable();
        gRequestSeq = gNumFrame; gRequestTick = l3_ticks();
        Hwi_restore(key);
    }
#endif
    if (l3_stopCaptureAtBoundary() != 0) { gTwoFault = 1U; return -1; }
    pre = gPreFramesCaptured < gCapturePlan.preFrames ? gPreFramesCaptured : gCapturePlan.preFrames;
    post = gPostFramesCaptured < gCapturePlan.postFrames ? gPostFramesCaptured : gCapturePlan.postFrames;
    oldest = gPreFramesCaptured >= gCapturePlan.preFrames ? gPreFramesCaptured % gCapturePlan.preFrames : 0U;
    if (!pre && !post) { gTwoFault = 1U; return -1; }
    for (f = 0U; f < pre; f++) slots[f] = (uint8_t)((oldest + f) % gCapturePlan.preFrames);
    for (f = 0U; f < post; f++) slots[pre + f] = (uint8_t)(gCapturePlan.preFrames + f);
    memset(&capture, 0, sizeof(capture));
    l3_fill_header(&header, (uint16_t)(pre + post), 0U);
    memset(&temperature, 0, sizeof(temperature));
    tempStatus = l3_readTemperatureReport(&temperature);
    if (!tempStatus) header.version = L3_DUMP_VERSION_CAPTURE_TEMPERATURE;
#ifdef L3_CAPTURE_EVIDENCE
    header.version = tempStatus ? L3_DUMP_VERSION_EVIDENCE : L3_DUMP_VERSION_EVIDENCE_TEMPERATURE;
#endif
    l3_two_append(&at, &header, sizeof(header));
    if (!tempStatus) l3_two_append(&at, &temperature, sizeof(temperature));
    for (f = 0U; f < pre + post; f++) {
        uint8_t descriptor[4];
        slot = slots[f];
        capture.starts[f] = descriptor[0] = gFrameBinStart[slot];
        capture.counts[f] = descriptor[1] = gFrameBinCount[slot];
        capture.samples[f] = (const int16_t *)&g_ring[gFrameOffset[slot]];
#ifdef HYBRID_CADENCE_CAPTURE
        {
            uint16_t delta = f ? gFrameDeltaUs[slot] : 0U;
            descriptor[2] = (uint8_t)delta; descriptor[3] = (uint8_t)(delta >> 8U);
            l3_two_append(&at, descriptor, 4U);
        }
#else
        l3_two_append(&at, descriptor, 2U);
#endif
    }
    gTwoFlags = gTwoPreviousTimeout ? L3_TWO_TIMEOUT_FLAG : 0U;
#ifdef L3_CAPTURE_EVIDENCE
    {
        l3_capture_evidence_t evidence;
        memset(&evidence, 0, sizeof(evidence));
        memcpy(evidence.magic, L3_CAPTURE_EVIDENCE_MAGIC, 4U);
        evidence.block_nbytes = sizeof(evidence);
        evidence.frame_record_nbytes = sizeof(l3_frame_evidence_t);
        evidence.tick_hz = gCpuClock;
        evidence.request_tick = gRequestTick; evidence.request_seq = gRequestSeq;
        evidence.freeze_tick = gFreezeTick; evidence.stop_tick = gStopTick;
        evidence.header_tick = l3_ticks(); evidence.hwa_missed_total = gHwaMissedFrameStarts;
        evidence.pre_frames = (uint8_t)pre; evidence.post_frames = (uint8_t)post;
        l3_two_append(&at, &evidence, sizeof(evidence));
        for (f = 0U; f < pre + post; f++) {
            l3_frame_evidence_t record;
            slot = slots[f];
            memset(&record, 0, sizeof(record));
            record.seq = gFrameSeq[slot]; record.start_tick = gFrameStartTick[slot];
            record.done_tick = gFrameDoneTick[slot]; record.flags = gFrameEvidenceFlags[slot];
            record.iq8_overflow = gFrameIq8Overflow[slot]; record.iq8_max_abs = gFrameIq8MaxAbs[slot];
            l3_two_append(&at, &record, sizeof(record));
        }
        memcpy(gTwoSuffix, L3_DUMP_TRAILER_MAGIC, 4U);
        l3_two_put32(gTwoSuffix + 4U, l3_ticks());
        capture.suffix = gTwoSuffix; capture.suffix_bytes = sizeof(gTwoSuffix);
        gTwoFlags |= L3_TWO_METADATA_SNAPSHOT_FLAG;
    }
#endif
    capture.frames = header.n_frames; capture.loops = gCapturePlan.loops; capture.bins = header.n_samples;
    capture.prefix = gTwoPrefix; capture.prefix_bytes = (uint16_t)at;
    if (l3_two_prepare(&gTwoState, &capture, gTwoNextSession) != 0) { gTwoFault = 1U; return -1; }
    gTwoNextSession = gTwoNextSession == 0xFFFFFFFFU ? 1U : gTwoNextSession + 1U;
    gTwoHeld = 1U; gTwoPreviousTimeout = 0U;
    gTwoLastActivity = Clock_getTicks();
    return 0;
}

static int32_t l3_cli_overview(int32_t argc, char *argv[])
{
    (void)argv;
    if (argc != 1) return -1;
    if (!gTwoHeld && l3_two_snapshot() != 0) {
        CLI_write("Error: no eligible IQ16 capture; capture/stop recovery may require reset\n");
        return -1;
    }
    gTwoOutput.write = l3_two_uartWrite; gTwoOutput.context = NULL;
    return l3_two_overview(&gTwoState, gTwoFlags, &gTwoOutput);
}

static int32_t l3_two_hexDigit(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int32_t l3_cli_cells(int32_t argc, char *argv[])
{
    uint32_t i, bytes;
    l3_two_request_t request;
    if (argc != 2 || !gTwoHeld) return -1;
    bytes = strlen(argv[1]);
    if ((bytes & 1U) || bytes > L3_TWO_MAX_REQUEST_BYTES * 2U) return -1;
    bytes /= 2U;
    for (i = 0U; i < bytes; i++) {
        int32_t hi = l3_two_hexDigit(argv[1][i * 2U]);
        int32_t lo = l3_two_hexDigit(argv[1][i * 2U + 1U]);
        if (hi < 0 || lo < 0) return -1;
        gTwoRequestWire[i] = (uint8_t)(hi * 16 + lo);
    }
    if (l3_two_request(&gTwoState, gTwoRequestWire, bytes, &request) != 0) return -1;
    gTwoOutput.write = l3_two_uartWrite; gTwoOutput.context = NULL;
    return l3_two_cells(&gTwoState, &request, &gTwoOutput);
}

static int32_t l3_cli_release(int32_t argc, char *argv[])
{
    uint32_t i, session = 0U;
    if (argc != 2 || strlen(argv[1]) != 8U || !gTwoHeld) return -1;
    for (i = 0U; i < 8U; i++) {
        int32_t digit = l3_two_hexDigit(argv[1][i]);
        if (digit < 0) return -1;
        session = (session << 4U) | (uint32_t)digit;
    }
    if (session != gTwoState.session || l3_two_closeSession(0U) != 0) return -1;
    CLI_write("released %08x\n", (unsigned)session);
    return 0;
}

#endif
