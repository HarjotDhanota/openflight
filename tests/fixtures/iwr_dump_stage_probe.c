#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#define HWA_CHAINED_SNAPSHOT_RING 1
#define CONFIGURABLE_CAPTURE 1
#define L3_CAPTURE_EVIDENCE 1
#define L3_USB_CLOCK_PROBE 1
#define L3_DUMP_VERSION_CAPTURE_TEMPERATURE 7
#define L3_DUMP_VERSION_EVIDENCE_TEMPERATURE 8
#define L3_DUMP_VERSION_EVIDENCE 9
#define EDMA3_CHANNEL_TYPE_DMA 0
#define L3_HWA_OUT_PING_CHANNEL 1
#define L3_HWA_OUT_PONG_CHANNEL 2
#define L3_HWA_SIGNATURE_CHANNEL 3

typedef struct { int version; } l3_dump_header_t;
typedef struct { int unused; } l3_temperature_report_t;
static struct { uint32_t preFrames, postFrames; } gCapturePlan = {9, 7};
static uint32_t gPreFramesCaptured = 9, gPostFramesCaptured = 7;
static uint32_t gNumFrame = 11;
#ifdef L3_DUMP_STAGE_TRACE
static uint32_t gRingFrame = 10, gHwaFrameDone = 10;
static uint32_t gHwaOutputDone = 10, gHwaRearms = 10, gHwaMissedFrameStarts;
#endif
static uint32_t gCaptureActive = 1, gRequestSeq, gRequestTick, gFreezeTick, gStopTick;
static void *gMMWaveHandle, *gHwaHandle, *gEdmaHandle, *gDataUart;
static int scenario, header_written;
static char events[1024];
static void event(const char *name) { strcat(events, name); strcat(events, " "); }
static uint32_t l3_ticks(void) { return 123; }
static uintptr_t Hwi_disable(void) { return 0; }
static void Hwi_restore(uintptr_t key) { (void)key; }
static int CLI_write(const char *format, ...) {
    va_list args;
    va_start(args, format);
    if (strstr(format, "L3STAGE")) { event(va_arg(args, const char *)); }
    va_end(args);
    return 0;
}
static int l3_freezeHwaAfterPostFrames(void) { event("freeze_call"); return scenario == 1 ? -1 : 0; }
static int MMWave_stop(void *handle, int32_t *error) {
    (void)handle; *error = -7; event("stop_call"); return scenario == 2 ? -1 : 0;
}
static void Task_sleep(int ticks) { assert(ticks == 10); event("sleep_call"); }
static int HWA_enable(void *handle, int enabled) { (void)handle; assert(!enabled); return 0; }
static int EDMA_disableChannel(void *handle, int channel, int type) {
    (void)handle; (void)channel; (void)type; return 0;
}
static void l3_fill_header(l3_dump_header_t *h, uint16_t frames, uint16_t trigger) {
    assert(frames == 16 && trigger == 0); h->version = 6;
}
static int l3_readTemperatureReport(l3_temperature_report_t *report) {
    (void)report; event("temp_call"); return scenario == 3 ? -1 : 0;
}
static int UART_writePolling(void *uart, uint8_t *data, uint32_t bytes) {
    (void)uart;
    if (!header_written) {
        assert(bytes == sizeof(l3_dump_header_t));
        assert(((l3_dump_header_t *)data)->version == (scenario == 3 ? 9 : 8));
        header_written = 1; event("header_call");
    } else { assert(bytes == sizeof(l3_temperature_report_t)); }
    return (int)bytes;
}
#include "dump_stage.inc"

int main(int argc, char **argv) {
    const char *expected;
    int result;
    assert(argc == 2); scenario = atoi(argv[1]);
    if (scenario == 4) { gCaptureActive = 0; }
    result = l3_cli_dump(0, NULL);
    assert(result == (scenario == 1 || scenario == 2 || scenario == 4 ? -1 : 0));
    assert(header_written == (scenario == 0 || scenario == 3));
#ifdef L3_DUMP_STAGE_TRACE
    if (scenario == 4) expected = "dump_enter ";
    else if (scenario == 1) expected = "dump_enter freeze_enter freeze_call freeze_error ";
    else if (scenario == 2) expected = "dump_enter freeze_enter freeze_call freeze_done stop_enter stop_call stop_error ";
    else expected = "dump_enter freeze_enter freeze_call freeze_done stop_enter stop_call stop_done sleep_call stop_margin_done stop_complete temp_enter temp_call temp_done header_enter header_call ";
#else
    if (scenario == 4) expected = "";
    else if (scenario == 1) expected = "freeze_call ";
    else if (scenario == 2) expected = "freeze_call stop_call ";
    else expected = "freeze_call stop_call sleep_call temp_call header_call ";
#endif
    if (strcmp(events, expected)) { fprintf(stderr, "%s\n!=\n%s\n", events, expected); return 1; }
    return 0;
}
