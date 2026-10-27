#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <setjmp.h>
#include <stdint.h>
#include <string.h>
#include "dump_format.h"

#define CLI_MAX_CMD 32U
#define N_TX 3
#define L3_MAX_CAPTURE_FRAMES 64U
#define L3_CLI_TASK_PRIORITY 3U
typedef uintptr_t UArg;
typedef void *Task_Handle;
typedef struct { uint32_t priority, stackSize; } Task_Params;
typedef struct { const char *cmd, *helpString; int32_t (*cmdHandlerFxn)(int32_t, char *[]); } CLI_CmdTableEntry;
typedef struct { CLI_CmdTableEntry tableEntry[CLI_MAX_CMD]; } CLI_Cfg;

uint32_t Clock_tickPeriod = 1000U, gNow, gCalls, gTicks = 1000U, gRestarts, gSleeps;
int32_t gRestartError, gStartError;
jmp_buf gSleepReturn;
uint8_t gBytes[100000];
uint32_t gUsed;
uint32_t gTemperatureRequests;
uintptr_t Hwi_disable(void) { return 0U; }
void Hwi_restore(uintptr_t key) { (void)key; }
uint32_t Clock_getTicks(void) { return gNow; }
void Task_sleep(uint32_t ticks) {
    if (gSleeps++) longjmp(gSleepReturn, 1);
    gNow += ticks;
}
void Task_Params_init(Task_Params *p) { memset(p, 0, sizeof(*p)); }
Task_Handle Task_create(void (*fn)(UArg, UArg), Task_Params *p, void *unused) {
    (void)fn; (void)p; (void)unused; return (void *)1;
}
int32_t UART_writePolling(void *port, uint8_t *data, uint32_t bytes) {
    (void)port; assert(bytes <= sizeof(gBytes) - gUsed);
    memcpy(gBytes + gUsed, data, bytes); gUsed += bytes; return (int32_t)bytes;
}
void CLI_write(const char *format, ...) {
    char line[512]; va_list ap; int size;
    va_start(ap, format); size = vsnprintf(line, sizeof(line), format, ap); va_end(ap);
    assert(size >= 0 && (uint32_t)size < sizeof(line));
    UART_writePolling(NULL, (uint8_t *)line, (uint32_t)size);
}

void *gDataUart, *gCliUart;
uint32_t gCpuClock = 200000000U, gNumFrame = 10U, gRingFrame, gHwaFreezeRequestFrame;
uint32_t gHwaFreezeTargetFrame, gPreFramesCaptured = 1U, gPostFramesCaptured = 1U;
uint32_t gPostFramesObserved, gPostCaptureStarted, gActiveFrameIsPost, gActiveFrameShouldKeep;
uint32_t gHwaFreezeRestarts, gRequestSeq, gRequestTick, gFreezeTick, gStopTick, gHwaMissedFrameStarts;
uint8_t gCaptureActive = 1U;
struct { uint32_t preFrames, postFrames, loops; } gCapturePlan = {1U, 1U, 2U};
uint8_t gFrameBinStart[64] = {20U, 47U}, gFrameBinCount[64] = {2U, 2U};
uint16_t gFrameDeltaUs[64] = {0U, 3000U};
uint32_t gFrameOffset[64] = {0U, 192U}, gFrameSeq[64] = {10U, 11U};
uint32_t gFrameStartTick[64] = {100U, 200U}, gFrameDoneTick[64] = {150U, 250U};
uint16_t gFrameIq8Overflow[64], gFrameIq8MaxAbs[64];
uint8_t gFrameEvidenceFlags[64] = {1U, 1U};
uint8_t g_ring[384] __attribute__((aligned(8)));
uint32_t l3_ticks(void) { return ++gTicks; }
uint8_t l3_captureUsesIq8(void) { return 0U; }
void l3_resetEvidence(void) { }
int32_t l3_restartCompletedHwaFrame(void) { gRestarts++; return gRestartError; }
int32_t l3_startFrontEnd(void) { return gStartError; }
int32_t l3_stopCaptureAtBoundary(void) {
    gCaptureActive = 0U; gFreezeTick = ++gTicks; gStopTick = ++gTicks; return 0;
}
#ifndef L3_DUMP_SKIP_TEMPERATURE
int32_t l3_readTemperatureReport(l3_temperature_report_t *report) {
    (void)report; gTemperatureRequests++; return -1;
}
#endif
void l3_fill_header(l3_dump_header_t *h, uint16_t frames, uint16_t trigger) {
    memset(h, 0, sizeof(*h)); memcpy(h->magic, "ILD1", 4U);
    h->version = 6U; h->n_frames = frames; h->chirps_per_frame = 6U;
    h->n_tx = 3U; h->n_rx = 4U; h->n_samples = 2U; h->sample_fmt = 4U;
    h->trigger_frame = trigger; h->frame_period_us = 3000U;
}
int32_t stub(int32_t argc, char *argv[]) { (void)argc; (void)argv; gCalls++; return 0; }
#define l3_cli_sensorStart stub
#define l3_cli_sensorStop stub
#define l3_cli_dump stub
#define l3_cli_stats stub
#define l3_cli_hwaStats stub
#define l3_cli_hwaTest stub
#define l3_cli_hwaReal stub
#define l3_cli_captureCfg stub
#define l3_cli_phaseCaptureCfg stub
#define l3_cli_captureFormat stub
#define l3_cli_iq8Scale stub
#define l3_cli_perfStats stub
#define l3_cli_bench stub
#define ENTRY(name) { name, "SDK command", stub }
CLI_CmdTableEntry gCLIMMWaveExtensionTable[] = {
    ENTRY("version"), ENTRY("flushCfg"), ENTRY("dfeDataOutputMode"), ENTRY("channelCfg"),
    ENTRY("adcCfg"), ENTRY("profileCfg"), ENTRY("chirpCfg"), ENTRY("frameCfg"),
    ENTRY("advFrameCfg"), ENTRY("subFrameCfg"), ENTRY("lowPower"), ENTRY("contModeCfg"),
    ENTRY("bpmCfgAdvanced"), {NULL, NULL, NULL}
};
#include "extended_cli.h"

static int32_t command(CLI_Cfg *cfg, const char *name, char *argument)
{
    uint32_t i;
    char *args[2]; args[0] = (char *)name; args[1] = argument;
    for (i = 0U; i < CLI_MAX_CMD && cfg->tableEntry[i].cmd; i++)
        if (!strcmp(name, cfg->tableEntry[i].cmd))
            return cfg->tableEntry[i].cmdHandlerFxn(argument ? 2 : 1, args);
    return -99;
}

int main(void)
{
    CLI_Cfg cfg;
    uint32_t i, calls;
    volatile uint32_t count;
    memset(&cfg, 0, sizeof(cfg));
    for (i = 0U; i < sizeof(g_ring)/2U; i++) ((int16_t *)g_ring)[i] = (int16_t)i;
    assert(l3_registerExtendedCli(&cfg) == 0);
    for (count = 0U; count < CLI_MAX_CMD && cfg.tableEntry[count].cmd; count++)
        assert(cfg.tableEntry[count].cmdHandlerFxn);
    assert(count < CLI_MAX_CMD);
    assert(command(&cfg, "sensorStart", NULL) == 0);
#ifdef L3_TWO_ROUND
    {
        uint8_t saved[10000]; uint32_t saved_bytes, session;
        char hex[9]; uint32_t restarts;
        gUsed = 0U;
        assert(command(&cfg, "overview", NULL) == 0 && gTwoHeld && !gTwoBusy);
#ifdef L3_DUMP_SKIP_TEMPERATURE
        assert(gTemperatureRequests == 0U);
#else
        assert(gTemperatureRequests == 1U);
#endif
        session = gTwoState.session;
#ifdef L3_CAPTURE_EVIDENCE
        assert(gTwoFlags & L3_TWO_METADATA_SNAPSHOT_FLAG);
#endif
        saved_bytes = gUsed; assert(saved_bytes < sizeof(saved)); memcpy(saved, gBytes, saved_bytes);
        calls = gCalls;
        for (i = 0U; i < count; i++) {
            const char *name = cfg.tableEntry[i].cmd;
            if (!strcmp(name,"overview") || !strcmp(name,"cells") || !strcmp(name,"release")) continue;
            assert(command(&cfg, name, NULL) != 0 && gTwoHeld && gCalls == calls);
        }
#ifdef L3_USB_CLOCK_PROBE
        {
            char *args[] = {"clockProbe", "12345678", "87654321", "00000001"};
            for (i = 0U; i < count; i++) if (!strcmp(cfg.tableEntry[i].cmd, "clockProbe")) break;
            assert(i < count && cfg.tableEntry[i].cmdHandlerFxn(4, args) != 0);
            assert(gClockBootHi == 0U && gClockBootLo == 0U);
        }
#endif
        {
            uint8_t wire[20] = {'I','L','Q','1',1,1,2,0,0,0,0,0,1,0,1,0,0,0,0,0};
            char request_hex[41];
            uint32_t chirp, rx, cursor;
            l3_two_put32(wire + 8U, session);
            l3_two_put32(wire + 16U, l3_two_crc(0xFFFFFFFFU, wire, 16U) ^ 0xFFFFFFFFU);
            for (i = 0U; i < sizeof(wire); i++) snprintf(request_hex + i*2U, 3U, "%02x", wire[i]);
            gUsed = 0U;
            assert(command(&cfg, "cells", request_hex) == 0 && gTwoHeld);
            assert(gUsed == 128U && !memcmp(gBytes, "ILC1", 4U));
            assert(l3_two_get32(gBytes + 12U) == session && gBytes[16] == 1U);
            assert(l3_two_get32(gBytes + gUsed - 4U) ==
                   (l3_two_crc(0xFFFFFFFFU, gBytes, gUsed - 4U) ^ 0xFFFFFFFFU));
            cursor = 28U;
            for (chirp = 0U; chirp < 6U; chirp++) for (rx = 0U; rx < 4U; rx++) {
                assert(!memcmp(gBytes + cursor, g_ring + (chirp*4U + rx)*8U, 4U));
                cursor += 4U;
            }
            assert(command(&cfg, "cells", "not-hex") != 0 && gTwoHeld);
        }
        assert(command(&cfg, "release", "ffffffff") != 0 && gTwoHeld);
        gUsed = 0U;
        assert(command(&cfg, "overview", NULL) == 0 && gTwoState.session == session);
        assert(gUsed == saved_bytes && !memcmp(saved, gBytes, gUsed));
        snprintf(hex, sizeof(hex), "%08x", (unsigned)session);
        assert(command(&cfg, "release", hex) == 0 && !gTwoHeld && gCaptureActive);
        assert(command(&cfg, "profileCfg", NULL) == 0 && gCalls == calls + 1U);
        gPreFramesCaptured = gPostFramesCaptured = 1U;
        assert(command(&cfg, "overview", NULL) == 0 && gTwoState.session != session);
        restarts = gRestarts; gTwoBusy = 1U; gNow += 30001U;
        gSleeps = 0U;
        if (!setjmp(gSleepReturn)) l3_two_watchTask(0U,0U);
        assert(gTwoHeld && gRestarts == restarts);
        gTwoBusy = 0U; gSleeps = 0U;
        if (!setjmp(gSleepReturn)) l3_two_watchTask(0U,0U);
        assert(!gTwoHeld && gTwoTimeouts == 1U && gTwoLastTimeout != 0U);
        gPreFramesCaptured = gPostFramesCaptured = 1U;
        assert(command(&cfg, "overview", NULL) == 0 && (gTwoFlags & L3_TWO_TIMEOUT_FLAG));
        gRestartError = -1;
        snprintf(hex, sizeof(hex), "%08x", (unsigned)gTwoState.session);
        assert(command(&cfg, "release", hex) != 0 && gTwoFault && !gCaptureActive);
        assert(command(&cfg, "sensorStart", NULL) != 0);
        assert(command(&cfg, "stats", NULL) == 0);
    }
#else
    calls = gCalls;
    assert(command(&cfg, "sensorStop", NULL) == 0 && gCalls == calls + 1U);
#endif
    printf("commands=%u lifecycle=passed\n", (unsigned)count);
    return 0;
}
