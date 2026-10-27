/* Host harness for firmware/iwr6843/two_round_core.h (tests/test_iwr6843_firmware_two_round.py).
 *
 *   harness overview <capture> <session-hex> <flags> <out>
 *   harness cells    <capture> <request> <out>
 *   harness log16    <doubles> <out>
 *   harness session  <capture> <script> <out> <events>
 *
 * A capture file is: u16 frames, loops, rx, samples; per frame u8 start, u8 count;
 * u32 + prefix bytes; u32 + suffix bytes; then every frame's IQ16 samples. The
 * session script is lines "L <cli line>", "B <hex bytes for the next read>",
 * "T <ms>" (time passes) and "F <reason>" (the next freeze fails); the out file
 * gets what the radar writes, framed as the SDK CLI frames it (no echo). */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "two_round_core.h"

static l3tr_capture_t gCap;
static l3tr_work_t gWork;
static uint8_t *gFile;
static uint8_t gPrefix[4096], gSuffix[64];
static uint8_t gRequest[L3TR_MAX_REQUEST_BYTES];
static FILE *gOut, *gEvents;

static uint8_t *slurp(const char *path, long *size)
{
    FILE *f = fopen(path, "rb");
    uint8_t *data;
    if (f == NULL) { perror(path); exit(2); }
    fseek(f, 0, SEEK_END);
    *size = ftell(f);
    fseek(f, 0, SEEK_SET);
    data = malloc((size_t)*size + 1U);
    if (fread(data, 1, (size_t)*size, f) != (size_t)*size) { perror(path); exit(2); }
    fclose(f);
    return data;
}

static void loadCapture(const char *path)
{
    long size;
    uint32_t at = 8U, f, n;
    gFile = slurp(path, &size);
    gCap.nFrames = (uint16_t)l3tr_get16(&gFile[0]);
    gCap.loops = (uint16_t)l3tr_get16(&gFile[2]);
    gCap.nRx = (uint16_t)l3tr_get16(&gFile[4]);
    gCap.nSamples = (uint16_t)l3tr_get16(&gFile[6]);
    for (f = 0U; f < gCap.nFrames; f++) {
        gCap.start[f] = gFile[at++];
        gCap.count[f] = gFile[at++];
    }
    n = l3tr_get32(&gFile[at]); at += 4U;
    memcpy(gPrefix, &gFile[at], n); gCap.prefix = gPrefix; gCap.prefixBytes = (uint16_t)n; at += n;
    n = l3tr_get32(&gFile[at]); at += 4U;
    memcpy(gSuffix, &gFile[at], n); gCap.suffix = gSuffix; gCap.suffixBytes = (uint16_t)n; at += n;
    for (f = 0U; f < gCap.nFrames; f++) {
        gCap.frame[f] = (const int16_t *)&gFile[at];
        at += (uint32_t)gCap.count[f] * gCap.loops * L3TR_N_LOOP_TX * gCap.nRx * 4U;
    }
    if ((long)at != size) { fprintf(stderr, "capture size %ld, parsed %u\n", size, at); exit(2); }
}

static int32_t writeOut(void *ctx, const uint8_t *bytes, uint32_t n)
{
    (void)ctx;
    return (int32_t)fwrite(bytes, 1, n, gOut);
}

/* ---- session ops over the loaded capture ---- */

static l3tr_capture_t gFrozen;
static const char *gFreezeFails;
static uint8_t gPending[L3TR_MAX_REQUEST_BYTES + 64];
static uint32_t gPendingBytes;
static uint32_t gTick = 0x12345600U;

static const char *opFreeze(void *ctx, l3tr_capture_t *cap, uint32_t *freezeTick)
{
    (void)ctx;
    fprintf(gEvents, "freeze\n");
    if (gFreezeFails != NULL) {
        const char *why = gFreezeFails;
        gFreezeFails = NULL;
        return why;
    }
    *cap = gCap;
    *freezeTick = gTick;
    gTick += 0x00010000U;
    return NULL;
}

static int32_t opRearm(void *ctx)
{
    (void)ctx;
    fprintf(gEvents, "rearm\n");
    return 0;
}

static void opStamp(void *ctx, uint32_t which)
{
    (void)ctx;
    fprintf(gEvents, "stamp %u\n", which);
}

static void opText(void *ctx, const char *line)
{
    (void)ctx;
    fputs(line, gOut);
}

static int32_t opRead(void *ctx, uint8_t *buffer, uint32_t n)
{
    (void)ctx;
    if (gPendingBytes != n) {
        gPendingBytes = 0U;
        return -1;
    }
    memcpy(buffer, gPending, n);
    gPendingBytes = 0U;
    return 0;
}

static int session(const char *script)
{
    l3tr_session_t st;
    l3tr_ops_t ops;
    char line[8192];
    FILE *in = fopen(script, "rb");

    memset(&st, 0, sizeof(st));
    ops.freeze = opFreeze; ops.rearm = opRearm; ops.stamp = opStamp; ops.write = writeOut;
    ops.text = opText; ops.readBytes = opRead; ops.ctx = NULL;
    while (fgets(line, sizeof(line), in) != NULL) {
        size_t len = strlen(line);
        while (len > 0U && (line[len - 1U] == '\n' || line[len - 1U] == '\r')) line[--len] = '\0';
        if (line[0] == 'T') {
            if (l3tr_sessionIdle(&st, &ops, (uint32_t)atoi(&line[2]))) fprintf(gEvents, "timeout\n");
        } else if (line[0] == 'F') {
            static char why[256];
            strncpy(why, &line[2], sizeof(why) - 1U);
            gFreezeFails = why;
        } else if (line[0] == 'B') {
            uint32_t i;
            gPendingBytes = (uint32_t)(len - 2U) / 2U;
            for (i = 0U; i < gPendingBytes; i++) {
                unsigned v;
                sscanf(&line[2 + 2 * i], "%2x", &v);
                gPending[i] = (uint8_t)v;
            }
        } else if (line[0] == 'L') {
            char *argv[40];
            int32_t argc = 0, status;
            char *tok = strtok(&line[2], " ");
            while (tok != NULL && argc < 40) { argv[argc++] = tok; tok = strtok(NULL, " "); }
            if (argc == 0) continue;
            if (strcmp(argv[0], L3TR_CMD_OVERVIEW) == 0) {
                status = l3tr_cmdOverview(&st, &ops, &gFrozen, &gWork);
            } else if (strcmp(argv[0], L3TR_CMD_CELLS) == 0) {
                status = l3tr_cmdCells(&st, &ops, &gFrozen, &gWork, gRequest, argc, argv);
            } else if (strcmp(argv[0], L3TR_CMD_RELEASE) == 0) {
                status = l3tr_cmdRelease(&st, &ops, argc, argv);
            } else if (l3tr_refuse(&st, &ops, argv[0])) {
                status = -1;
            } else {
                fprintf(gEvents, "ran %s\n", argv[0]);
                status = 0;
            }
            /* the SDK CLI's own framing (CLI_task) */
            fputs(status == 0 ? "Done\n" : "Error -1\n", gOut);
            fputs("l3dump:/>", gOut);
            fprintf(gEvents, "state frozen=%u session=%08x timed_out=%u\n", st.frozen,
                    st.sessionId, st.previousTimedOut);
        }
    }
    fclose(in);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc >= 6 && strcmp(argv[1], "overview") == 0) {
        loadCapture(argv[2]);
        if (l3tr_checkCapture(&gCap) != NULL) { fprintf(stderr, "%s\n", l3tr_checkCapture(&gCap)); return 3; }
        gOut = fopen(argv[5], "wb");
        if (l3tr_overviewPrepare(&gCap, &gWork) != 0) return 4;
        if (l3tr_overviewEmit(&gCap, &gWork, (uint32_t)strtoul(argv[3], NULL, 16),
                              (uint32_t)strtoul(argv[4], NULL, 0), writeOut, NULL) != 0) return 5;
        fclose(gOut);
        printf("passes %u\n", gWork.passes);
        return 0;
    }
    if (argc >= 5 && strcmp(argv[1], "cells") == 0) {
        long size;
        uint8_t *rq;
        l3tr_request_t req;
        const char *why;
        loadCapture(argv[2]);
        rq = slurp(argv[3], &size);
        gOut = fopen(argv[4], "wb");
        why = l3tr_requestDecode(&gCap, rq, (uint32_t)size, &req);
        if (why != NULL) { fprintf(gOut, "ERR %s", why); fclose(gOut); return 0; }
        if (l3tr_cellsEmit(&gCap, &gWork, &req, writeOut, NULL) != 0) return 5;
        fclose(gOut);
        return 0;
    }
    if (argc >= 4 && strcmp(argv[1], "log16") == 0) {
        long size, i;
        uint8_t *in = slurp(argv[2], &size);
        gOut = fopen(argv[3], "wb");
        for (i = 0; i + 8 <= size; i += 8) {
            uint64_t bits;
            uint8_t out[2];
            memcpy(&bits, &in[i], 8U);
            l3tr_put16(out, l3tr_log16(l3tr_double(bits)));
            fwrite(out, 1, 2, gOut);
        }
        fclose(gOut);
        return 0;
    }
    if (argc >= 6 && strcmp(argv[1], "session") == 0) {
        loadCapture(argv[2]);
        gOut = fopen(argv[4], "wb");
        gEvents = fopen(argv[5], "wb");
        session(argv[3]);
        fclose(gOut);
        fclose(gEvents);
        return 0;
    }
    fprintf(stderr, "usage\n");
    return 2;
}
