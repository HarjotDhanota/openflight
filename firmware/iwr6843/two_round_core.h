#ifndef OPENFLIGHT_TWO_ROUND_CORE_H
#define OPENFLIGHT_TWO_ROUND_CORE_H

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "two_round_format.h"

typedef struct {
    uint16_t frames, loops, bins;
    uint8_t starts[L3_TWO_MAX_FRAMES], counts[L3_TWO_MAX_FRAMES];
    const int16_t *samples[L3_TWO_MAX_FRAMES];
    const uint8_t *prefix, *suffix;
    uint16_t prefix_bytes, suffix_bytes;
} l3_two_capture_t;

typedef struct {
    l3_two_capture_t capture;
    int32_t sums[L3_TWO_MAX_BINS][2][4][2];
    int32_t burst[L3_TWO_MAX_BINS][2][4][2];
    uint16_t holders[L3_TWO_MAX_BINS];
    uint32_t histogram[256];
    uint32_t session, elements;
    uint16_t held_bins, map_cells;
    float noise[2];
} l3_two_state_t;

typedef int32_t (*l3_two_write_fn)(void *, const uint8_t *, uint32_t);

typedef struct {
    l3_two_write_fn write;
    void *context;
    uint8_t buffer[512];
    uint32_t pending, crc;
    int32_t failed;
} l3_two_output_t;

typedef struct {
    const uint8_t *wire;
    uint32_t bytes, crc;
    uint16_t cells;
    uint8_t sequence;
} l3_two_request_t;

static uint32_t l3_two_get32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8U) |
           ((uint32_t)p[2] << 16U) | ((uint32_t)p[3] << 24U);
}

static void l3_two_put32(uint8_t *p, uint32_t value)
{
    uint32_t i;
    for (i = 0U; i < 4U; i++) p[i] = (uint8_t)(value >> (i * 8U));
}

static uint32_t l3_two_crc(uint32_t crc, const uint8_t *data, uint32_t size)
{
    uint32_t i, bit;
    for (i = 0U; i < size; i++) {
        crc ^= data[i];
        for (bit = 0U; bit < 8U; bit++)
            crc = (crc >> 1U) ^ ((crc & 1U) ? 0xEDB88320U : 0U);
    }
    return crc;
}

static void l3_two_flush(l3_two_output_t *out)
{
    if (!out->failed && out->pending &&
        out->write(out->context, out->buffer, out->pending) != (int32_t)out->pending)
        out->failed = -1;
    out->pending = 0U;
}

static void l3_two_emit(l3_two_output_t *out, const void *data, uint32_t size)
{
    const uint8_t *p = (const uint8_t *)data;
    uint32_t i;
    if (out->failed) return;
    out->crc = l3_two_crc(out->crc, p, size);
    for (i = 0U; i < size && !out->failed; i++) {
        out->buffer[out->pending++] = p[i];
        if (out->pending == sizeof(out->buffer)) l3_two_flush(out);
    }
}

static int32_t l3_two_finish(l3_two_output_t *out)
{
    uint8_t crc[4];
    l3_two_put32(crc, out->crc ^ 0xFFFFFFFFU);
    l3_two_emit(out, crc, sizeof(crc));
    l3_two_flush(out);
    return out->failed;
}

static int32_t l3_two_sample(const l3_two_state_t *s, uint32_t f, uint32_t loop,
                             uint32_t tx, uint32_t rx, uint32_t bin, uint32_t part)
{
    uint32_t index = ((((loop * 3U + tx * 2U) * 4U + rx) *
                       s->capture.counts[f] + bin) * 2U) + 1U - part;
    return s->capture.samples[f][index];
}

static void l3_two_burst(l3_two_state_t *s, uint32_t f)
{
    uint32_t b, t, r, p, loop;
    memset(s->burst, 0, sizeof(s->burst));
    for (b = 0U; b < s->capture.counts[f]; b++)
        for (t = 0U; t < 2U; t++) for (r = 0U; r < 4U; r++)
            for (p = 0U; p < 2U; p++) for (loop = 0U; loop < s->capture.loops; loop++)
                s->burst[b][t][r][p] += l3_two_sample(s, f, loop, t, r, b, p);
}

static double l3_two_power(const l3_two_state_t *s, uint32_t f, uint32_t loop,
                            uint32_t t, uint32_t r, uint32_t b, uint32_t scope)
{
    uint32_t absolute = s->capture.starts[f] + b;
    int64_t n = scope ? s->holders[absolute] : s->capture.loops;
    const int32_t *sum = scope ? s->sums[absolute][t][r] : s->burst[b][t][r];
    int64_t real = n * l3_two_sample(s, f, loop, t, r, b, 0U) - sum[0];
    int64_t imag = n * l3_two_sample(s, f, loop, t, r, b, 1U) - sum[1];
    return (double)(real * real + imag * imag) / ((double)n * (double)n);
}

static uint64_t l3_two_key(double power)
{
    uint64_t bits;
    memcpy(&bits, &power, sizeof(bits));
    return bits;
}

static double l3_two_median(l3_two_state_t *s, uint32_t scope)
{
    uint32_t shift, f, loop, t, r, b, bucket, rank = s->elements / 2U;
    uint32_t below = 0U;
    uint64_t prefix = 0U, mask = 0U, key, lower = 0U;
    double value, upper;
    /* Positive binary64 bit order equals numeric order; keep only 256 counters. */
    for (shift = 56U; ; shift -= 8U) {
        memset(s->histogram, 0, sizeof(s->histogram));
        for (f = 0U; f < s->capture.frames; f++) {
            l3_two_burst(s, f);
            for (loop = 0U; loop < s->capture.loops; loop++)
                for (t = 0U; t < 2U; t++) for (r = 0U; r < 4U; r++)
                    for (b = 0U; b < s->capture.counts[f]; b++) {
                        key = l3_two_key(l3_two_power(s, f, loop, t, r, b, scope));
                        if ((key & mask) == prefix)
                            s->histogram[(uint32_t)((key >> shift) & 255U)]++;
                    }
        }
        for (bucket = 0U; bucket < 256U; bucket++) {
            if (rank < s->histogram[bucket]) break;
            rank -= s->histogram[bucket];
        }
        prefix |= (uint64_t)bucket << shift;
        mask |= (uint64_t)255U << shift;
        if (shift == 0U) break;
    }
    memcpy(&upper, &prefix, sizeof(upper));
    if (rank > 0U) return upper;
    for (f = 0U; f < s->capture.frames; f++) {
        l3_two_burst(s, f);
        for (loop = 0U; loop < s->capture.loops; loop++)
            for (t = 0U; t < 2U; t++) for (r = 0U; r < 4U; r++)
                for (b = 0U; b < s->capture.counts[f]; b++) {
                    key = l3_two_key(l3_two_power(s, f, loop, t, r, b, scope));
                    if (key < prefix) {
                        below++;
                        if (key > lower) lower = key;
                    }
                }
    }
    if (!below) return upper;
    memcpy(&value, &lower, sizeof(value));
    return (value + upper) / 2.0;
}

static int32_t l3_two_prepare(l3_two_state_t *s, const l3_two_capture_t *capture,
                              uint32_t session)
{
    uint32_t f, b, t, r, p;
    if (!capture || !session || !capture->frames || capture->frames > L3_TWO_MAX_FRAMES ||
        !capture->loops || capture->loops > L3_TWO_MAX_LOOPS ||
        !capture->bins || capture->bins > L3_TWO_MAX_BINS || !capture->prefix ||
        !capture->prefix_bytes || (capture->suffix_bytes && !capture->suffix)) return -1;
    for (f = 0U; f < capture->frames; f++)
        if (!capture->samples[f] || !capture->counts[f] || capture->counts[f] > capture->bins ||
            (uint32_t)capture->starts[f] + capture->counts[f] > L3_TWO_MAX_BINS) return -1;
    memset(s, 0, sizeof(*s));
    s->capture = *capture;
    s->session = session;
    for (f = 0U; f < capture->frames; f++) {
        l3_two_burst(s, f);
        s->map_cells += capture->counts[f];
        for (b = 0U; b < capture->counts[f]; b++) {
            uint32_t absolute = capture->starts[f] + b;
            s->holders[absolute] += capture->loops;
            for (t = 0U; t < 2U; t++) for (r = 0U; r < 4U; r++) for (p = 0U; p < 2U; p++)
                s->sums[absolute][t][r][p] += s->burst[b][t][r][p];
        }
    }
    for (b = 0U; b < L3_TWO_MAX_BINS; b++) if (s->holders[b]) s->held_bins++;
    s->elements = (uint32_t)s->map_cells * capture->loops * 8U;
    s->noise[0] = (float)l3_two_median(s, 0U);
    s->noise[1] = (float)l3_two_median(s, 1U);
    return 0;
}

static uint16_t l3_two_log16(double power)
{
    double code, base, fraction;
    uint32_t rounded;
    if (power <= 1.0) return 0U;
    code = 5000.0 * log10(power);
    if (code >= 65535.0) return 65535U;
    base = floor(code);
    fraction = code - base;
    rounded = (uint32_t)base;
    if (fraction > 0.5 || (fraction == 0.5 && (rounded & 1U))) rounded++;
    return (uint16_t)rounded;
}

static int32_t l3_two_overview(l3_two_state_t *s, uint16_t flags, l3_two_output_t *out)
{
    l3_two_overview_t header;
    uint32_t scope, f, loop, b, t, r;
    double power;
    uint16_t code;
    memset(&header, 0, sizeof(header));
    memcpy(header.magic, L3_TWO_OVERVIEW_MAGIC, 4U);
    header.version = L3_TWO_VERSION;
    header.header_bytes = sizeof(header);
    header.total_bytes = sizeof(header) + s->capture.prefix_bytes + s->capture.suffix_bytes +
        (uint32_t)s->held_bins * 64U + (uint32_t)s->map_cells * s->capture.loops * 4U + 4U;
    header.session = s->session;
    header.frames = s->capture.frames;
    header.chirps = s->capture.loops * 3U;
    header.tx = 3U;
    header.rx = 4U;
    header.bins = s->capture.bins;
    header.summed_bins = s->held_bins;
    header.map_cells = s->map_cells;
    header.map_code = 1U;
    header.scopes = 2U;
    header.burst_noise = s->noise[0];
    header.window_noise = s->noise[1];
    header.prefix_bytes = s->capture.prefix_bytes;
    header.suffix_bytes = s->capture.suffix_bytes;
    header.flags = flags;
    out->pending = 0U; out->crc = 0xFFFFFFFFU; out->failed = 0;
    l3_two_emit(out, &header, sizeof(header));
    l3_two_emit(out, s->capture.prefix, s->capture.prefix_bytes);
    l3_two_emit(out, s->capture.suffix, s->capture.suffix_bytes);
    for (b = 0U; b < L3_TWO_MAX_BINS; b++) if (s->holders[b])
        l3_two_emit(out, s->sums[b], sizeof(s->sums[b]));
    for (scope = 0U; scope < 2U; scope++) for (f = 0U; f < s->capture.frames; f++) {
        l3_two_burst(s, f);
        for (loop = 0U; loop < s->capture.loops; loop++)
            for (b = 0U; b < s->capture.counts[f]; b++) {
                power = 0.0;
                for (t = 0U; t < 2U; t++) for (r = 0U; r < 4U; r++)
                    power += l3_two_power(s, f, loop, t, r, b, scope);
                code = l3_two_log16(power);
                l3_two_emit(out, &code, sizeof(code));
            }
    }
    return l3_two_finish(out);
}

static int32_t l3_two_request(const l3_two_state_t *s, const uint8_t *wire,
                              uint32_t bytes, l3_two_request_t *request)
{
    uint32_t at = 12U, end, f, run, first, length, previous, cells = 0U;
    if (!wire || !request || bytes < 16U || bytes > L3_TWO_MAX_REQUEST_BYTES ||
        memcmp(wire, L3_TWO_REQUEST_MAGIC, 4U) || wire[4] != L3_TWO_VERSION || !wire[5] ||
        ((uint32_t)wire[6] | ((uint32_t)wire[7] << 8U)) != s->capture.frames ||
        l3_two_get32(wire + 8U) != s->session) return -1;
    end = bytes - 4U;
    if ((l3_two_crc(0xFFFFFFFFU, wire, end) ^ 0xFFFFFFFFU) != l3_two_get32(wire + end)) return -1;
    for (f = 0U; f < s->capture.frames; f++) {
        if (at >= end) return -1;
        run = wire[at++];
        if (run * 2U > end - at) return -1;
        previous = 0U;
        while (run--) {
            first = wire[at++]; length = wire[at++];
            if (!length || first < previous || first + length > s->capture.counts[f]) return -1;
            previous = first + length;
            cells += length;
        }
    }
    if (at != end) return -1;
    request->wire = wire; request->bytes = bytes;
    request->crc = l3_two_get32(wire + end);
    request->sequence = wire[5]; request->cells = (uint16_t)cells;
    return 0;
}

static int32_t l3_two_cells(l3_two_state_t *s, const l3_two_request_t *request,
                            l3_two_output_t *out)
{
    l3_two_cells_t header;
    uint32_t f, run, first, length, b, chirp, rx, at = 12U, index;
    memset(&header, 0, sizeof(header));
    memcpy(header.magic, L3_TWO_CELLS_MAGIC, 4U);
    header.version = L3_TWO_VERSION; header.header_bytes = sizeof(header);
    header.cell_bytes = (uint32_t)s->capture.loops * 3U * 4U * 4U;
    header.total_bytes = sizeof(header) + request->cells * header.cell_bytes + 4U;
    header.session = s->session; header.sequence = request->sequence;
    header.cells = request->cells; header.request_crc = request->crc;
    out->pending = 0U; out->crc = 0xFFFFFFFFU; out->failed = 0;
    l3_two_emit(out, &header, sizeof(header));
    for (f = 0U; f < s->capture.frames; f++) {
        run = request->wire[at++];
        while (run--) {
            first = request->wire[at++]; length = request->wire[at++];
            for (b = first; b < first + length; b++)
                for (chirp = 0U; chirp < s->capture.loops * 3U; chirp++) for (rx = 0U; rx < 4U; rx++) {
                    index = ((chirp * 4U + rx) * s->capture.counts[f] + b) * 2U;
                    l3_two_emit(out, s->capture.samples[f] + index, 4U);
                }
        }
    }
    return l3_two_finish(out);
}

#endif
