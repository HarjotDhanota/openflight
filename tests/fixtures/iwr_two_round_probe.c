#include <stdio.h>
#include <stdlib.h>
#include "two_round_core.h"

static l3_two_state_t state;
static l3_two_output_t output;
static long fail_after = -1L;

static int32_t write_file(void *context, const uint8_t *data, uint32_t bytes)
{
    if (fail_after >= 0L) {
        if ((long)bytes > fail_after) return -1;
        fail_after -= (long)bytes;
    }
    return (int32_t)fwrite(data, 1U, bytes, (FILE *)context);
}

static uint8_t *read_file(const char *path, uint32_t *bytes)
{
    FILE *file = fopen(path, "rb");
    long length;
    uint8_t *data;
    if (!file || fseek(file, 0L, SEEK_END) || (length = ftell(file)) < 0L ||
        length > 16000000L || fseek(file, 0L, SEEK_SET)) exit(10);
    data = (uint8_t *)malloc((size_t)length + 1U);
    if (!data || fread(data, 1U, (size_t)length, file) != (size_t)length) exit(11);
    fclose(file);
    *bytes = (uint32_t)length;
    return data;
}

int main(int argc, char **argv)
{
    l3_two_capture_t capture;
    l3_two_request_t request;
    uint8_t *input, *wire;
    uint32_t bytes, request_bytes, at, f, frame_bytes;
    FILE *file;
    if (argc != 6) return 1;
    input = read_file(argv[1], &bytes);
    if (bytes < 24U) return 2;
    memset(&capture, 0, sizeof(capture));
    capture.frames = (uint16_t)l3_two_get32(input);
    capture.loops = (uint16_t)l3_two_get32(input + 4U);
    capture.bins = (uint16_t)l3_two_get32(input + 8U);
    capture.prefix_bytes = (uint16_t)l3_two_get32(input + 12U);
    capture.suffix_bytes = (uint16_t)l3_two_get32(input + 16U);
    if (capture.frames > L3_TWO_MAX_FRAMES) return 2;
    at = 24U;
    if (at + capture.frames * 2U + capture.prefix_bytes + capture.suffix_bytes > bytes) return 2;
    for (f = 0U; f < capture.frames; f++) {
        capture.starts[f] = input[at++]; capture.counts[f] = input[at++];
    }
    capture.prefix = input + at; at += capture.prefix_bytes;
    capture.suffix = input + at; at += capture.suffix_bytes;
    for (f = 0U; f < capture.frames; f++) {
        frame_bytes = (uint32_t)capture.loops * 3U * 4U * capture.counts[f] * 4U;
        if (frame_bytes > bytes - at) return 2;
        capture.samples[f] = (const int16_t *)(input + at); at += frame_bytes;
    }
    if (at != bytes || l3_two_prepare(&state, &capture, l3_two_get32(input + 20U))) return 2;
    file = fopen(argv[3], "wb");
    if (!file) return 10;
    output.write = write_file; output.context = file;
    fail_after = strtol(argv[5], NULL, 10);
    if (l3_two_overview(&state, 0U, &output)) { fclose(file); return 4; }
    fclose(file);
    wire = read_file(argv[2], &request_bytes);
    if (l3_two_request(&state, wire, request_bytes, &request)) return 3;
    file = fopen(argv[4], "wb");
    if (!file) return 10;
    output.context = file;
    if (l3_two_cells(&state, &request, &output)) { fclose(file); return 4; }
    fclose(file);
    printf("state_bytes=%lu output_bytes=%lu crc=%08x\n", (unsigned long)sizeof(state),
           (unsigned long)sizeof(output),
           (unsigned)(l3_two_crc(0xFFFFFFFFU, (const uint8_t *)"123456789", 9U) ^ 0xFFFFFFFFU));
    free(wire); free(input);
    return 0;
}
