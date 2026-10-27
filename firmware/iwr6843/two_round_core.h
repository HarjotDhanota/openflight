#ifndef OPENFLIGHT_TWO_ROUND_CORE_H
#define OPENFLIGHT_TWO_ROUND_CORE_H

/* Stage-2 two-round transfer: everything that does not touch the SDK.
 *
 * The overview, request and cells codecs, the exact noise medians and log16
 * coding, and the frozen-session rules. l3_dump.c supplies the ring, the
 * freeze/rearm and the UART through l3tr_ops_t; the host tests
 * (tests/test_iwr6843_firmware_two_round.py) compile this header as it is and
 * compare its bytes with src/openflight/iwr6843/two_round.py and
 * two_round_sim.py, the reference implementation.
 *
 * Arithmetic follows the reference exactly: integer sums and MTI differences,
 * then float64 power (int64 numerator / float64 denominator, both exact), a
 * float64 median (mean of the two middle values) rounded once to float32, and
 * the map's float64 sums over (TX, RX) in TX-major order. Only IEEE-754 +, -,
 * *, / and comparisons are used (no libm), so the result does not depend on a
 * math library; build without FP contraction (-ffp-contract=off). */

#include <stdint.h>
#include <string.h>

#include "two_round_format.h"
#include "two_round_log16.h"

#ifndef L3TR_MAX_FRAMES
#define L3TR_MAX_FRAMES 64U
#endif
#define L3TR_MAX_LOOPS      16U
#define L3TR_MAX_RX         4U
#define L3TR_MAX_BINS       64U    /* per frame */
#define L3TR_MAX_SPAN       128U   /* absolute bins from the lowest start to the highest end */
#define L3TR_HIST_WIDTH     11U
#define L3TR_HIST_BUCKETS   (1U << L3TR_HIST_WIDTH)  /* 8 kB of uint32 */
#define L3TR_CANDIDATES     512U
#define L3TR_CHUNK_BYTES    1024U
#define L3TR_MAX_REQUEST_BYTES \
    (L3TR_RQ_HEADER_BYTES + L3TR_MAX_FRAMES * (1U + L3TR_MAX_BINS) + L3_TWO_ROUND_CRC_BYTES)

#define L3TR_SCOPE_BURST  0U
#define L3TR_SCOPE_WINDOW 1U

/* One frozen capture, frames in dump order. frame[f] holds the ring's IQ16
 * samples [chirp][RX][local bin] as (Im, Re) int16 pairs, chirp = loop*3 + tx. */
typedef struct {
    uint16_t       nFrames;
    uint16_t       loops;
    uint16_t       nRx;
    uint16_t       nSamples;          /* widest frame (the dump header's n_samples) */
    uint8_t        start[L3TR_MAX_FRAMES];
    uint8_t        count[L3TR_MAX_FRAMES];
    const int16_t *frame[L3TR_MAX_FRAMES];
    const uint8_t *prefix;            /* dump header .. frame records, as l3dump sends them */
    uint16_t       prefixBytes;
    const uint8_t *suffix;            /* the ILT1 trailer */
    uint16_t       suffixBytes;
} l3tr_capture_t;

/* Static working memory (about 25 kB; DATA_RAM in the firmware). */
typedef struct {
    int32_t  sums[L3TR_MAX_SPAN][2][L3TR_MAX_RX][2];      /* [abs - spanStart][TX1/TX3][rx][re/im] */
    uint16_t holders[L3TR_MAX_SPAN];                      /* loops x frames holding the bin */
    int32_t  loopSum[2][L3TR_MAX_RX][L3TR_MAX_BINS][2];   /* one frame's sums over its loops */
    uint32_t hist[L3TR_HIST_BUCKETS];
    uint64_t cand[L3TR_CANDIDATES];
    uint8_t  chunk[L3TR_CHUNK_BYTES];
    uint16_t spanStart;
    uint16_t spanCount;
    uint16_t nHeld;
    uint16_t nMapCells;
    float    noise[L3TR_N_SCOPES];
    uint32_t passes;                                      /* full passes over the ring, last overview */
} l3tr_work_t;

/* ------------------------------------------------------------------ bytes */

static inline void l3tr_put16(uint8_t *at, uint32_t value)
{
    at[0] = (uint8_t)value;
    at[1] = (uint8_t)(value >> 8U);
}

static inline void l3tr_put32(uint8_t *at, uint32_t value)
{
    at[0] = (uint8_t)value;
    at[1] = (uint8_t)(value >> 8U);
    at[2] = (uint8_t)(value >> 16U);
    at[3] = (uint8_t)(value >> 24U);
}

static inline uint32_t l3tr_get16(const uint8_t *at)
{
    return (uint32_t)at[0] | ((uint32_t)at[1] << 8U);
}

static inline uint32_t l3tr_get32(const uint8_t *at)
{
    return (uint32_t)at[0] | ((uint32_t)at[1] << 8U) | ((uint32_t)at[2] << 16U) |
           ((uint32_t)at[3] << 24U);
}

static inline uint64_t l3tr_bits(double value)
{
    uint64_t bits;
    memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static inline double l3tr_double(uint64_t bits)
{
    double value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

/* ------------------------------------------------------------------ CRC-32 (zlib) */

static uint32_t l3trCrcTable[256];
static uint8_t  l3trCrcReady;

static void l3tr_crcInit(void)
{
    uint32_t n, bit, c;

    for (n = 0U; n < 256U; n++) {
        c = n;
        for (bit = 0U; bit < 8U; bit++) {
            c = (c & 1U) ? (0xEDB88320U ^ (c >> 1U)) : (c >> 1U);
        }
        l3trCrcTable[n] = c;
    }
    l3trCrcReady = 1U;
}

/* Running CRC: start from 0, as zlib.crc32(data, value). */
static uint32_t l3tr_crc32(uint32_t crc, const uint8_t *bytes, uint32_t n)
{
    uint32_t i;

    if (!l3trCrcReady) {
        l3tr_crcInit();
    }
    crc = ~crc;
    for (i = 0U; i < n; i++) {
        crc = l3trCrcTable[(crc ^ bytes[i]) & 0xFFU] ^ (crc >> 8U);
    }
    return ~crc;
}

/* ------------------------------------------------------------------ output */

typedef int32_t (*l3tr_write_fn)(void *ctx, const uint8_t *bytes, uint32_t n);

typedef struct {
    l3tr_write_fn write;
    void         *ctx;
    uint8_t      *chunk;
    uint32_t      fill;
    uint32_t      crc;
    uint32_t      written;
    int32_t       error;
} l3tr_stream_t;

static void l3tr_streamOpen(l3tr_stream_t *s, l3tr_write_fn write, void *ctx, uint8_t *chunk)
{
    s->write = write;
    s->ctx = ctx;
    s->chunk = chunk;
    s->fill = 0U;
    s->crc = 0U;
    s->written = 0U;
    s->error = 0;
}

static void l3tr_streamFlush(l3tr_stream_t *s)
{
    if (s->fill > 0U && s->error == 0) {
        if (s->write(s->ctx, s->chunk, s->fill) != (int32_t)s->fill) {
            s->error = -1;
        }
    }
    s->fill = 0U;
}

static void l3tr_streamPut(l3tr_stream_t *s, const uint8_t *bytes, uint32_t n)
{
    s->crc = l3tr_crc32(s->crc, bytes, n);
    s->written += n;
    while (n > 0U) {
        uint32_t room = L3TR_CHUNK_BYTES - s->fill;
        uint32_t take = (n < room) ? n : room;
        memcpy(&s->chunk[s->fill], bytes, take);
        s->fill += take;
        bytes += take;
        n -= take;
        if (s->fill == L3TR_CHUNK_BYTES) {
            l3tr_streamFlush(s);
        }
    }
}

static void l3tr_streamPut16(l3tr_stream_t *s, uint32_t value)
{
    uint8_t out[2];
    l3tr_put16(out, value);
    l3tr_streamPut(s, out, 2U);
}

static void l3tr_streamPut32(l3tr_stream_t *s, uint32_t value)
{
    uint8_t out[4];
    l3tr_put32(out, value);
    l3tr_streamPut(s, out, 4U);
}

/* Appends the CRC of everything put so far and flushes. 0 on success. */
static int32_t l3tr_streamSeal(l3tr_stream_t *s)
{
    uint8_t out[4];
    uint32_t crc = s->crc;

    l3tr_put32(out, crc);
    l3tr_streamPut(s, out, 4U);
    l3tr_streamFlush(s);
    return s->error;
}

/* ------------------------------------------------------------------ log16 */

static const double l3trPow10[23] = {
    1e0, 1e1, 1e2, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9, 1e10, 1e11,
    1e12, 1e13, 1e14, 1e15, 1e16, 1e17, 1e18, 1e19, 1e20, 1e21, 1e22,
};

/* a*b = hi + lo exactly (Dekker, no FMA). */
static void l3tr_twoProduct(double a, double b, double *hi, double *lo)
{
    const double split = 134217729.0;  /* 2^27 + 1 */
    double ca = split * a, cb = split * b;
    double ahi = ca - (ca - a), alo = a - ahi;
    double bhi = cb - (cb - b), blo = b - bhi;

    *hi = a * b;
    *lo = ((ahi * bhi - *hi) + ahi * blo + alo * bhi) + alo * blo;
}

/* 1 when p / 10^k > t_c exactly (t_c = hi + lo to about 2^-106). Called only
 * when p / 10^k rounds to within 2 ulps of hi, so the subtractions are exact. */
static uint32_t l3tr_aboveThreshold(double p, uint32_t k, uint32_t c)
{
    double tHi = l3tr_double(l3trLog16Hi[c]);
    double tLo = l3tr_double(l3trLog16Lo[c]);
    double d;

    if (k == 0U) {
        d = (p - tHi) - tLo;
    } else {
        double scale = l3trPow10[k];
        double hi, lo;
        l3tr_twoProduct(scale, tHi, &hi, &lo);
        d = ((p - hi) - lo) - scale * tLo;
    }
    return (d > 0.0) ? 1U : 0U;
}

/* round(5000 * log10(max(p, 1))) clipped to 0..65535, rounded exactly. */
static uint16_t l3tr_log16(double p)
{
    uint32_t k = 0U, lo = 0U, hi = L3TR_LOG16_THRESHOLDS, code;
    double q;
    uint64_t qb;

    if (!(p >= 1.0)) {
        return 0U;
    }
    while (k < 22U && p >= l3trPow10[k + 1U]) {
        k++;
    }
    if (k * L3TR_LOG16_CODES_PER_DECADE > L3TR_LOG16_MAX_CODE) {
        return (uint16_t)L3TR_LOG16_MAX_CODE;
    }
    q = (k == 0U) ? p : p / l3trPow10[k];
    qb = l3tr_bits(q);
    while (lo < hi) {   /* lo = #{c : hi_c < q} */
        uint32_t mid = (lo + hi) / 2U;
        if (l3trLog16Hi[mid] < qb) {
            lo = mid + 1U;
        } else {
            hi = mid;
        }
    }
    /* q is within half an ulp of p / 10^k and hi_c of t_c: more than 2 ulps
     * apart, the comparison is exact. Thresholds are ~2e12 ulps apart, so only
     * one neighbour can be close. */
    if (lo > 0U && qb - l3trLog16Hi[lo - 1U] <= 2U) {
        if (!l3tr_aboveThreshold(p, k, lo - 1U)) {
            lo--;
        }
    } else if (lo < L3TR_LOG16_THRESHOLDS && l3trLog16Hi[lo] - qb <= 2U) {
        if (l3tr_aboveThreshold(p, k, lo)) {
            lo++;
        }
    }
    code = k * L3TR_LOG16_CODES_PER_DECADE + lo;
    return (uint16_t)((code > L3TR_LOG16_MAX_CODE) ? L3TR_LOG16_MAX_CODE : code);
}

/* ------------------------------------------------------------------ the capture */

static inline int32_t l3tr_re(const int16_t *frame, uint32_t index)
{
    return (int32_t)frame[2U * index + 1U];
}

static inline int32_t l3tr_im(const int16_t *frame, uint32_t index)
{
    return (int32_t)frame[2U * index];
}

/* Sample index of (loop, tx, rx, local bin) in a frame of `count` bins. */
static inline uint32_t l3tr_index(const l3tr_capture_t *cap, uint32_t count, uint32_t loop,
                                  uint32_t tx, uint32_t rx, uint32_t bin)
{
    return ((loop * L3TR_N_LOOP_TX + tx) * cap->nRx + rx) * count + bin;
}

/* NULL when the capture fits the protocol and this memory; else the reason. */
static const char *l3tr_checkCapture(const l3tr_capture_t *cap)
{
    uint32_t f, mapCells = 0U, low = 0xFFFFU, high = 0U;

    if (cap->nFrames == 0U || cap->nFrames > L3TR_MAX_FRAMES) {
        return "frame count outside 1-64";
    }
    if (cap->loops == 0U || cap->loops > L3TR_MAX_LOOPS || cap->nRx == 0U ||
        cap->nRx > L3TR_MAX_RX) {
        return "loops or RX outside the protocol";
    }
    if (cap->nSamples == 0U || cap->nSamples > 255U) {
        return "bins per frame do not fit a one-byte run";
    }
    for (f = 0U; f < cap->nFrames; f++) {
        if (cap->count[f] == 0U || cap->count[f] > L3TR_MAX_BINS ||
            cap->count[f] > cap->nSamples || cap->frame[f] == NULL) {
            return "a frame's bin count is outside the protocol";
        }
        mapCells += cap->count[f];
        if (cap->start[f] < low) {
            low = cap->start[f];
        }
        if ((uint32_t)cap->start[f] + cap->count[f] > high) {
            high = (uint32_t)cap->start[f] + cap->count[f];
        }
    }
    if (high - low > L3TR_MAX_SPAN) {
        return "the frames' range windows span too many bins";
    }
    if (mapCells * cap->loops > 0xFFFFU / 2U) {
        return "the map does not fit";
    }
    return NULL;
}

/* Per-frame sums over its loops, TX1/TX3 only. */
static void l3tr_frameLoopSums(const l3tr_capture_t *cap, l3tr_work_t *w, uint32_t f)
{
    const int16_t *data = cap->frame[f];
    uint32_t count = cap->count[f], t, r, b, l;

    for (t = 0U; t < 2U; t++) {
        uint32_t tx = (t == 0U) ? 0U : 2U;
        for (r = 0U; r < cap->nRx; r++) {
            for (b = 0U; b < count; b++) {
                int32_t re = 0, im = 0;
                for (l = 0U; l < cap->loops; l++) {
                    uint32_t at = l3tr_index(cap, count, l, tx, r, b);
                    re += l3tr_re(data, at);
                    im += l3tr_im(data, at);
                }
                w->loopSum[t][r][b][0] = re;
                w->loopSum[t][r][b][1] = im;
            }
        }
    }
}

/* Exact sums per absolute bin and the holders of each bin. */
static void l3tr_sums(const l3tr_capture_t *cap, l3tr_work_t *w)
{
    uint32_t f, low = 0xFFFFU, high = 0U, s;

    for (f = 0U; f < cap->nFrames; f++) {
        if (cap->start[f] < low) {
            low = cap->start[f];
        }
        if ((uint32_t)cap->start[f] + cap->count[f] > high) {
            high = (uint32_t)cap->start[f] + cap->count[f];
        }
    }
    w->spanStart = (uint16_t)low;
    w->spanCount = (uint16_t)(high - low);
    memset(w->sums, 0, sizeof(w->sums));
    memset(w->holders, 0, sizeof(w->holders));
    w->nMapCells = 0U;
    for (f = 0U; f < cap->nFrames; f++) {
        uint32_t count = cap->count[f], base = cap->start[f] - low, t, r, b;
        l3tr_frameLoopSums(cap, w, f);
        for (b = 0U; b < count; b++) {
            w->holders[base + b] = (uint16_t)(w->holders[base + b] + cap->loops);
            for (t = 0U; t < 2U; t++) {
                for (r = 0U; r < cap->nRx; r++) {
                    w->sums[base + b][t][r][0] += w->loopSum[t][r][b][0];
                    w->sums[base + b][t][r][1] += w->loopSum[t][r][b][1];
                }
            }
        }
        w->nMapCells = (uint16_t)(w->nMapCells + count);
    }
    w->nHeld = 0U;
    for (s = 0U; s < w->spanCount; s++) {
        if (w->holders[s] != 0U) {
            w->nHeld++;
        }
    }
}

/* The MTI power numerator of one element and its key (the bits of a
 * non-negative double, which order like the values). Burst: d = L*x - sum
 * over the frame's loops; the key is the numerator itself (one denominator,
 * L^2, for every element). Window: d = n*x - sum over the window's n samples;
 * the key is the power |d|^2 / n^2 as the reference computes it. */
static inline uint64_t l3tr_key(const l3tr_capture_t *cap, const l3tr_work_t *w, uint32_t scope,
                                const int16_t *data, uint32_t count, uint32_t base, uint32_t l,
                                uint32_t t, uint32_t r, uint32_t b)
{
    uint32_t at = l3tr_index(cap, count, l, (t == 0U) ? 0U : 2U, r, b);
    int64_t re, im, num;

    if (scope == L3TR_SCOPE_BURST) {
        int32_t n = (int32_t)cap->loops;
        re = (int64_t)n * l3tr_re(data, at) - w->loopSum[t][r][b][0];
        im = (int64_t)n * l3tr_im(data, at) - w->loopSum[t][r][b][1];
        num = re * re + im * im;
        return l3tr_bits((double)num);
    } else {
        int32_t n = (int32_t)w->holders[base + b];
        double dn = (double)n;
        re = (int64_t)n * l3tr_re(data, at) - w->sums[base + b][t][r][0];
        im = (int64_t)n * l3tr_im(data, at) - w->sums[base + b][t][r][1];
        num = re * re + im * im;
        return l3tr_bits((double)num / (dn * dn));
    }
}

/* The power an element contributes to the map, as the reference computes it. */
static inline double l3tr_power(const l3tr_capture_t *cap, uint32_t scope, uint64_t key)
{
    if (scope == L3TR_SCOPE_BURST) {
        return l3tr_double(key) / (double)((uint32_t)cap->loops * cap->loops);
    }
    return l3tr_double(key);
}

#define L3TR_PASS_HIST    0U
#define L3TR_PASS_COLLECT 1U

typedef struct {
    uint32_t kind;
    uint64_t prefix;      /* key >> above must equal this */
    uint32_t above;       /* bits below the fixed prefix (64 for "match everything") */
    uint32_t shift;       /* histogram digit: (key >> shift) & mask */
    uint64_t mask;
    uint32_t nCand;       /* collect: keys stored */
    uint32_t matched;     /* collect: keys matching */
    uint64_t minAbove;    /* collect: smallest key above the prefix */
    uint8_t  haveAbove;
} l3tr_pass_t;

/* One pass over every element of a scope. */
static void l3tr_scan(const l3tr_capture_t *cap, l3tr_work_t *w, uint32_t scope, l3tr_pass_t *p)
{
    uint32_t f, l, t, r, b;

    w->passes++;
    for (f = 0U; f < cap->nFrames; f++) {
        const int16_t *data = cap->frame[f];
        uint32_t count = cap->count[f], base = cap->start[f] - w->spanStart;
        if (scope == L3TR_SCOPE_BURST) {
            l3tr_frameLoopSums(cap, w, f);
        }
        for (l = 0U; l < cap->loops; l++) {
            for (t = 0U; t < 2U; t++) {
                for (r = 0U; r < cap->nRx; r++) {
                    for (b = 0U; b < count; b++) {
                        uint64_t key = l3tr_key(cap, w, scope, data, count, base, l, t, r, b);
                        uint64_t top = (p->above >= 64U) ? 0U : (key >> p->above);
                        if (top == p->prefix) {
                            if (p->kind == L3TR_PASS_HIST) {
                                w->hist[(uint32_t)((key >> p->shift) & p->mask)]++;
                            } else {
                                if (p->nCand < L3TR_CANDIDATES) {
                                    w->cand[p->nCand++] = key;
                                }
                                p->matched++;
                            }
                        } else if (p->kind == L3TR_PASS_COLLECT && top > p->prefix &&
                                   (!p->haveAbove || key < p->minAbove)) {
                            p->minAbove = key;
                            p->haveAbove = 1U;
                        }
                    }
                }
            }
        }
    }
}

/* The k-th smallest key (0-based) and the next one, exactly, by radix select
 * over the key bits: 11-bit histogram passes until the bucket holding rank k
 * fits the candidate buffer, then one pass that collects it. */
static int32_t l3tr_selectPair(const l3tr_capture_t *cap, l3tr_work_t *w, uint32_t scope,
                               uint32_t n, uint32_t k, uint64_t *lowKey, uint64_t *highKey)
{
    static const uint8_t shifts[6] = {52U, 41U, 30U, 19U, 8U, 0U};
    static const uint8_t widths[6] = {11U, 11U, 11U, 11U, 11U, 8U};
    l3tr_pass_t p;
    uint32_t level, rank = k, bucketCount = n, i, j;

    memset(&p, 0, sizeof(p));
    p.prefix = 0U;
    p.above = 63U;  /* the sign bit: zero for every non-negative double */
    for (level = 0U; level < 6U && bucketCount > L3TR_CANDIDATES; level++) {
        uint32_t below = 0U, bucket;
        memset(w->hist, 0, sizeof(w->hist));
        p.kind = L3TR_PASS_HIST;
        p.shift = shifts[level];
        p.mask = ((uint64_t)1U << widths[level]) - 1U;
        l3tr_scan(cap, w, scope, &p);
        for (bucket = 0U; bucket <= (uint32_t)p.mask; bucket++) {
            if (below + w->hist[bucket] > rank) {
                break;
            }
            below += w->hist[bucket];
        }
        if (bucket > (uint32_t)p.mask) {
            return -1;
        }
        rank -= below;
        bucketCount = w->hist[bucket];
        p.prefix = (p.prefix << widths[level]) | bucket;
        p.above = shifts[level];
    }
    p.kind = L3TR_PASS_COLLECT;
    p.nCand = 0U;
    p.matched = 0U;
    p.haveAbove = 0U;
    l3tr_scan(cap, w, scope, &p);
    if (p.matched != bucketCount) {
        return -1;
    }
    if (p.above == 0U) {
        /* every bit fixed: the bucket is one repeated key */
        *lowKey = p.prefix;
        if (rank + 1U < bucketCount) {
            *highKey = p.prefix;
        } else if (p.haveAbove) {
            *highKey = p.minAbove;
        } else {
            *highKey = p.prefix;
        }
        return 0;
    }
    if (p.nCand != bucketCount) {
        return -1;
    }
    for (i = 1U; i < p.nCand; i++) {   /* insertion sort: at most 512 keys */
        uint64_t key = w->cand[i];
        for (j = i; j > 0U && w->cand[j - 1U] > key; j--) {
            w->cand[j] = w->cand[j - 1U];
        }
        w->cand[j] = key;
    }
    *lowKey = w->cand[rank];
    if (rank + 1U < p.nCand) {
        *highKey = w->cand[rank + 1U];
    } else if (p.haveAbove) {
        *highKey = p.minAbove;
    } else {
        *highKey = *lowKey;
    }
    return 0;
}

/* The scope's median MTI power, as numpy's median then rounded to float32. */
static int32_t l3tr_median(const l3tr_capture_t *cap, l3tr_work_t *w, uint32_t scope, float *out)
{
    uint32_t f, n = 0U;
    uint64_t lowKey, highKey;
    double low, high;

    for (f = 0U; f < cap->nFrames; f++) {
        n += (uint32_t)cap->count[f] * cap->loops * 2U * cap->nRx;
    }
    if (n == 0U) {
        return -1;
    }
    if (l3tr_selectPair(cap, w, scope, n, (n - 1U) / 2U, &lowKey, &highKey) != 0) {
        return -1;
    }
    low = l3tr_power(cap, scope, lowKey);
    if ((n & 1U) != 0U) {
        *out = (float)low;
    } else {
        high = l3tr_power(cap, scope, highKey);
        *out = (float)((low + high) / 2.0);
    }
    return 0;
}

/* Sums and both medians: everything the overview header needs. */
static int32_t l3tr_overviewPrepare(const l3tr_capture_t *cap, l3tr_work_t *w)
{
    w->passes = 0U;
    l3tr_sums(cap, w);
    w->passes++;
    if (l3tr_median(cap, w, L3TR_SCOPE_BURST, &w->noise[0]) != 0) {
        return -1;
    }
    if (l3tr_median(cap, w, L3TR_SCOPE_WINDOW, &w->noise[1]) != 0) {
        return -1;
    }
    return 0;
}

static uint32_t l3tr_overviewBytes(const l3tr_capture_t *cap, const l3tr_work_t *w)
{
    return L3TR_OV_HEADER_BYTES + cap->prefixBytes + cap->suffixBytes +
           (uint32_t)w->nHeld * 2U * cap->nRx * 2U * 4U +
           L3TR_N_SCOPES * (uint32_t)w->nMapCells * cap->loops * 2U + L3_TWO_ROUND_CRC_BYTES;
}

/* Streams the overview (after l3tr_overviewPrepare). 0 on success. */
static int32_t l3tr_overviewEmit(const l3tr_capture_t *cap, l3tr_work_t *w, uint32_t session,
                                 uint32_t flags, l3tr_write_fn write, void *ctx)
{
    uint8_t h[L3TR_OV_HEADER_BYTES];
    l3tr_stream_t s;
    uint32_t scope, f, l, t, r, b, sbin;
    float noise;

    memset(h, 0, sizeof(h));
    memcpy(&h[L3TR_OV_MAGIC], L3_TWO_ROUND_OVERVIEW_MAGIC, 4U);
    l3tr_put16(&h[L3TR_OV_VERSION], L3_TWO_ROUND_OVERVIEW_VERSION);
    l3tr_put16(&h[L3TR_OV_HEADER_NBYTES], L3TR_OV_HEADER_BYTES);
    l3tr_put32(&h[L3TR_OV_TOTAL_NBYTES], l3tr_overviewBytes(cap, w));
    l3tr_put32(&h[L3TR_OV_SESSION], session);
    l3tr_put16(&h[L3TR_OV_N_FRAMES], cap->nFrames);
    l3tr_put16(&h[L3TR_OV_CHIRPS], (uint32_t)cap->loops * L3TR_N_LOOP_TX);
    h[L3TR_OV_N_TX] = (uint8_t)L3TR_N_LOOP_TX;
    h[L3TR_OV_N_RX] = (uint8_t)cap->nRx;
    l3tr_put16(&h[L3TR_OV_N_SAMPLES], cap->nSamples);
    l3tr_put16(&h[L3TR_OV_N_SUM_BINS], w->nHeld);
    l3tr_put16(&h[L3TR_OV_N_MAP_CELLS], w->nMapCells);
    h[L3TR_OV_MAP_CODE] = (uint8_t)L3TR_POWER_CODE_LOG16;
    h[L3TR_OV_N_SCOPES] = (uint8_t)L3TR_N_SCOPES;
    noise = w->noise[0];
    memcpy(&h[L3TR_OV_NOISE_BURST], &noise, 4U);   /* little-endian targets only */
    noise = w->noise[1];
    memcpy(&h[L3TR_OV_NOISE_WINDOW], &noise, 4U);
    l3tr_put16(&h[L3TR_OV_PREFIX_NBYTES], cap->prefixBytes);
    l3tr_put16(&h[L3TR_OV_SUFFIX_NBYTES], cap->suffixBytes);
    l3tr_put16(&h[L3TR_OV_FLAGS], flags);

    l3tr_streamOpen(&s, write, ctx, w->chunk);
    l3tr_streamPut(&s, h, sizeof(h));
    l3tr_streamPut(&s, cap->prefix, cap->prefixBytes);
    l3tr_streamPut(&s, cap->suffix, cap->suffixBytes);
    for (sbin = 0U; sbin < w->spanCount; sbin++) {
        if (w->holders[sbin] == 0U) {
            continue;
        }
        for (t = 0U; t < 2U; t++) {
            for (r = 0U; r < cap->nRx; r++) {
                l3tr_streamPut32(&s, (uint32_t)w->sums[sbin][t][r][0]);
                l3tr_streamPut32(&s, (uint32_t)w->sums[sbin][t][r][1]);
            }
        }
    }
    for (scope = 0U; scope < L3TR_N_SCOPES; scope++) {
        w->passes++;
        for (f = 0U; f < cap->nFrames; f++) {
            const int16_t *data = cap->frame[f];
            uint32_t count = cap->count[f], base = cap->start[f] - w->spanStart;
            if (scope == L3TR_SCOPE_BURST) {
                l3tr_frameLoopSums(cap, w, f);
            }
            for (l = 0U; l < cap->loops; l++) {
                for (b = 0U; b < count; b++) {
                    double total = 0.0;
                    for (t = 0U; t < 2U; t++) {
                        for (r = 0U; r < cap->nRx; r++) {
                            uint64_t key = l3tr_key(cap, w, scope, data, count, base, l, t, r, b);
                            total += l3tr_power(cap, scope, key);
                        }
                    }
                    l3tr_streamPut16(&s, l3tr_log16(total));
                }
            }
        }
    }
    return l3tr_streamSeal(&s);
}

/* ------------------------------------------------------------------ request */

typedef struct {
    uint8_t        sequence;
    uint32_t       session;
    uint32_t       crc;
    uint32_t       nCells;
    const uint8_t *body;        /* per-frame runs */
} l3tr_request_t;

/* Mirrors two_round.decode_request: NULL when valid, else the reason. */
static const char *l3tr_requestDecode(const l3tr_capture_t *cap, const uint8_t *data, uint32_t n,
                                      l3tr_request_t *out)
{
    uint32_t at, end, f, i;

    if (n < L3TR_RQ_HEADER_BYTES) {
        return "cell request truncated";
    }
    if (memcmp(&data[L3TR_RQ_MAGIC], L3_TWO_ROUND_REQUEST_MAGIC, 4U) != 0) {
        return "cell request: bad magic";
    }
    if (data[L3TR_RQ_VERSION] != L3_TWO_ROUND_REQUEST_VERSION) {
        return "cell request version is not supported";
    }
    if (n < L3TR_RQ_HEADER_BYTES + L3_TWO_ROUND_CRC_BYTES ||
        l3tr_crc32(0U, data, n - L3_TWO_ROUND_CRC_BYTES) != l3tr_get32(&data[n - 4U])) {
        return "cell request CRC mismatch";
    }
    if (l3tr_get16(&data[L3TR_RQ_N_FRAMES]) != cap->nFrames) {
        return "cell request frame count differs from the capture";
    }
    out->sequence = data[L3TR_RQ_SEQUENCE];
    out->session = l3tr_get32(&data[L3TR_RQ_SESSION]);
    out->crc = l3tr_get32(&data[n - 4U]);
    out->body = &data[L3TR_RQ_HEADER_BYTES];
    out->nCells = 0U;
    at = L3TR_RQ_HEADER_BYTES;
    end = n - L3_TWO_ROUND_CRC_BYTES;
    for (f = 0U; f < cap->nFrames; f++) {
        uint32_t runs, previousEnd = 0U;
        if (at >= end) {
            return "cell request truncated";
        }
        runs = data[at++];
        if (at + 2U * runs > end) {
            return "cell request truncated";
        }
        for (i = 0U; i < runs; i++) {
            uint32_t first = data[at], length = data[at + 1U];
            at += 2U;
            if (length == 0U || first < previousEnd || first + length > cap->count[f]) {
                return "cell request has a bad run";
            }
            previousEnd = first + length;
            out->nCells += length;
        }
    }
    if (at != end) {
        return "cell request has bytes past its last frame";
    }
    return NULL;
}

/* Streams the cells reply for a decoded request. 0 on success. */
static int32_t l3tr_cellsEmit(const l3tr_capture_t *cap, l3tr_work_t *w, const l3tr_request_t *rq,
                              l3tr_write_fn write, void *ctx)
{
    uint8_t h[L3TR_CL_HEADER_BYTES];
    uint32_t perCell = (uint32_t)cap->loops * L3TR_N_LOOP_TX * cap->nRx * 4U;
    const uint8_t *at = rq->body;
    l3tr_stream_t s;
    uint32_t f, i;

    memset(h, 0, sizeof(h));
    memcpy(&h[L3TR_CL_MAGIC], L3_TWO_ROUND_CELLS_MAGIC, 4U);
    l3tr_put16(&h[L3TR_CL_VERSION], L3_TWO_ROUND_CELLS_VERSION);
    l3tr_put16(&h[L3TR_CL_HEADER_NBYTES], L3TR_CL_HEADER_BYTES);
    l3tr_put32(&h[L3TR_CL_TOTAL_NBYTES],
               L3TR_CL_HEADER_BYTES + rq->nCells * perCell + L3_TWO_ROUND_CRC_BYTES);
    l3tr_put32(&h[L3TR_CL_SESSION], rq->session);
    h[L3TR_CL_SEQUENCE] = rq->sequence;
    h[L3TR_CL_RESERVED] = 0U;
    l3tr_put16(&h[L3TR_CL_N_CELLS], rq->nCells);
    l3tr_put32(&h[L3TR_CL_REQUEST_CRC], rq->crc);
    l3tr_put32(&h[L3TR_CL_CELL_NBYTES], perCell);

    l3tr_streamOpen(&s, write, ctx, w->chunk);
    l3tr_streamPut(&s, h, sizeof(h));
    for (f = 0U; f < cap->nFrames; f++) {
        uint32_t runs = *at++, count = cap->count[f];
        const int16_t *data = cap->frame[f];
        for (i = 0U; i < runs; i++) {
            uint32_t first = at[0], length = at[1], b, chirp, r;
            at += 2;
            for (b = first; b < first + length; b++) {
                for (chirp = 0U; chirp < (uint32_t)cap->loops * L3TR_N_LOOP_TX; chirp++) {
                    for (r = 0U; r < cap->nRx; r++) {
                        uint32_t index = (chirp * cap->nRx + r) * count + b;
                        l3tr_streamPut(&s, (const uint8_t *)&data[2U * index], 4U);
                    }
                }
            }
        }
    }
    return l3tr_streamSeal(&s);
}

/* ------------------------------------------------------------------ the session */

/* While a capture is frozen, only these run (plus the CLI's own help). */
static uint32_t l3tr_isSessionCommand(const char *name)
{
    return (strcmp(name, L3TR_CMD_OVERVIEW) == 0 || strcmp(name, L3TR_CMD_CELLS) == 0 ||
            strcmp(name, L3TR_CMD_RELEASE) == 0 || strcmp(name, "help") == 0)
               ? 1U
               : 0U;
}

typedef struct {
    uint8_t  frozen;
    uint8_t  firstOverviewSent;
    uint8_t  previousTimedOut;   /* how the last session ended: flags the next overview */
    uint16_t counter;
    uint32_t sessionId;
    uint32_t idleMs;
    uint32_t timeouts;
    uint32_t releases;
} l3tr_session_t;

/* A new id: a 16-bit counter (never 0) over 16 bits of the freeze tick. */
static uint32_t l3tr_sessionNewId(l3tr_session_t *st, uint32_t freezeTick)
{
    st->counter = (uint16_t)(st->counter + 1U);
    if (st->counter == 0U) {
        st->counter = 1U;
    }
    return ((uint32_t)st->counter << 16U) | ((freezeTick >> 8U) & 0xFFFFU);
}

typedef struct {
    /* stop the ring at a frame boundary and describe it (prefix/suffix filled,
     * the evidence ticks still to stamp); NULL on success, else the reason */
    const char *(*freeze)(void *ctx, l3tr_capture_t *cap, uint32_t *freezeTick);
    /* restart the ring exactly as after an l3dump; 0 on success */
    int32_t (*rearm)(void *ctx);
    /* stamp the evidence ticks of the first overview: 0 = header_tick (before
     * the summary is computed), 1 = payload_end_tick (after) */
    void (*stamp)(void *ctx, uint32_t which);
    int32_t (*write)(void *ctx, const uint8_t *bytes, uint32_t n);
    void (*text)(void *ctx, const char *line);
    /* read exactly n binary request bytes after the ready line; 0 on success */
    int32_t (*readBytes)(void *ctx, uint8_t *buffer, uint32_t n);
    void *ctx;
} l3tr_ops_t;

/* Formats "<word> <8 hex>" lines without printf. */
static void l3tr_hex8(char *out, uint32_t value)
{
    static const char digits[] = "0123456789abcdef";
    uint32_t i;
    for (i = 0U; i < 8U; i++) {
        out[i] = digits[(value >> (28U - 4U * i)) & 0xFU];
    }
    out[8] = '\0';
}

static void l3tr_textJoin(const l3tr_ops_t *ops, const char *a, const char *b, const char *c,
                          const char *d, const char *e)
{
    char line[160];
    const char *parts[5];
    uint32_t i, at = 0U;

    parts[0] = a; parts[1] = b; parts[2] = c; parts[3] = d; parts[4] = e;
    for (i = 0U; i < 5U; i++) {
        const char *p = parts[i];
        while (p != NULL && *p != '\0' && at < sizeof(line) - 2U) {
            line[at++] = *p++;
        }
    }
    line[at++] = '\n';
    line[at] = '\0';
    ops->text(ops->ctx, line);
}

static int32_t l3tr_parseHex32(const char *text, uint32_t *out)
{
    uint32_t value = 0U, i;
    for (i = 0U; text[i] != '\0'; i++) {
        char c = text[i];
        uint32_t digit;
        if (i >= 8U) return -1;
        if (c >= '0' && c <= '9') digit = (uint32_t)(c - '0');
        else if (c >= 'a' && c <= 'f') digit = (uint32_t)(c - 'a') + 10U;
        else if (c >= 'A' && c <= 'F') digit = (uint32_t)(c - 'A') + 10U;
        else return -1;
        value = (value << 4U) | digit;
    }
    if (i == 0U) return -1;
    *out = value;
    return 0;
}

static int32_t l3tr_parseDecimal(const char *text, uint32_t *out)
{
    uint32_t value = 0U, i;
    for (i = 0U; text[i] != '\0'; i++) {
        if (i >= 6U || text[i] < '0' || text[i] > '9') return -1;
        value = value * 10U + (uint32_t)(text[i] - '0');
    }
    if (i == 0U) return -1;
    *out = value;
    return 0;
}

/* Refuses a command that is not part of the frozen session. 1 = refused (an
 * error line was written), 0 = it may run. */
static uint32_t l3tr_refuse(l3tr_session_t *st, const l3tr_ops_t *ops, const char *name)
{
    char id[9];

    if (!st->frozen || l3tr_isSessionCommand(name)) {
        return 0U;
    }
    l3tr_hex8(id, st->sessionId);
    l3tr_textJoin(ops, "Error: session ", id, " frozen: only overview, cells, release", NULL, NULL);
    return 1U;
}

/* "overview": freezes the ring on the first call (the shot), then sends the
 * frozen capture's overview; again for the same session on a later call. */
static int32_t l3tr_cmdOverview(l3tr_session_t *st, const l3tr_ops_t *ops, l3tr_capture_t *cap,
                                l3tr_work_t *w)
{
    const char *why;
    uint32_t first = 0U, flags;

    if (!st->frozen) {
        uint32_t freezeTick = 0U;
        why = ops->freeze(ops->ctx, cap, &freezeTick);
        if (why != NULL) {
            l3tr_textJoin(ops, "Error: ", why, NULL, NULL, NULL);
            return -1;
        }
        why = l3tr_checkCapture(cap);
        if (why != NULL) {
            /* nothing usable was frozen: give the ring back at once */
            (void)ops->rearm(ops->ctx);
            l3tr_textJoin(ops, "Error: ", why, NULL, NULL, NULL);
            return -1;
        }
        st->frozen = 1U;
        st->firstOverviewSent = 0U;
        st->sessionId = l3tr_sessionNewId(st, freezeTick);
        first = 1U;
    }
    st->idleMs = 0U;
    if (first) {
        ops->stamp(ops->ctx, 0U);
    }
    if (l3tr_overviewPrepare(cap, w) != 0) {
        l3tr_textJoin(ops, "Error: overview summary failed", NULL, NULL, NULL, NULL);
        return -1;
    }
    if (first) {
        ops->stamp(ops->ctx, 1U);
    }
    flags = st->previousTimedOut ? L3TR_FLAG_PREVIOUS_SESSION_TIMED_OUT : 0U;
    if (l3tr_overviewEmit(cap, w, st->sessionId, flags, ops->write, ops->ctx) != 0) {
        return -1;
    }
    (void)ops->write(ops->ctx, (const uint8_t *)"\n", 1U);
    st->firstOverviewSent = 1U;
    st->idleMs = 0U;
    return 0;
}

/* "cells <session> <nbytes>": checks the session, writes "ready <nbytes>",
 * reads exactly nbytes of ILQ1 request, answers with ILC1. */
static int32_t l3tr_cmdCells(l3tr_session_t *st, const l3tr_ops_t *ops, const l3tr_capture_t *cap,
                             l3tr_work_t *w, uint8_t *requestBuffer, int32_t argc, char *argv[])
{
    uint32_t session, nbytes;
    l3tr_request_t rq;
    const char *why;
    char id[9], frozenId[9];

    if (!st->frozen) {
        l3tr_textJoin(ops, "Error: no frozen capture", NULL, NULL, NULL, NULL);
        return -1;
    }
    if (argc != 3 || l3tr_parseHex32(argv[1], &session) != 0 ||
        l3tr_parseDecimal(argv[2], &nbytes) != 0) {
        l3tr_textJoin(ops, "Error: bad request: usage cells <session hex> <request bytes>", NULL,
                      NULL, NULL, NULL);
        return -1;
    }
    l3tr_hex8(frozenId, st->sessionId);
    if (session != st->sessionId) {
        l3tr_hex8(id, session);
        l3tr_textJoin(ops, "Error: stale session ", id, " (frozen ", frozenId, ")");
        return -1;
    }
    if (nbytes < L3TR_RQ_HEADER_BYTES + L3_TWO_ROUND_CRC_BYTES || nbytes > L3TR_MAX_REQUEST_BYTES) {
        l3tr_textJoin(ops, "Error: bad request: ", argv[2], " bytes is outside the request size",
                      NULL, NULL);
        return -1;
    }
    st->idleMs = 0U;
    l3tr_textJoin(ops, "ready ", argv[2], NULL, NULL, NULL);
    if (ops->readBytes(ops->ctx, requestBuffer, nbytes) != 0) {
        l3tr_textJoin(ops, "Error: bad request: the request bytes did not arrive", NULL, NULL,
                      NULL, NULL);
        return -1;
    }
    why = l3tr_requestDecode(cap, requestBuffer, nbytes, &rq);
    if (why != NULL) {
        l3tr_textJoin(ops, "Error: bad request: ", why, NULL, NULL, NULL);
        return -1;
    }
    if (rq.session != st->sessionId) {
        l3tr_hex8(id, rq.session);
        l3tr_textJoin(ops, "Error: stale session ", id, " (frozen ", frozenId, ")");
        return -1;
    }
    st->idleMs = 0U;
    if (l3tr_cellsEmit(cap, w, &rq, ops->write, ops->ctx) != 0) {
        return -1;
    }
    (void)ops->write(ops->ctx, (const uint8_t *)"\n", 1U);
    st->idleMs = 0U;
    return 0;
}

/* "release <session>": rearms the ring; acknowledged with "released <session>". */
static int32_t l3tr_cmdRelease(l3tr_session_t *st, const l3tr_ops_t *ops, int32_t argc,
                               char *argv[])
{
    uint32_t session;
    char id[9];

    if (!st->frozen) {
        l3tr_textJoin(ops, "Error: no frozen capture", NULL, NULL, NULL, NULL);
        return -1;
    }
    l3tr_hex8(id, st->sessionId);
    if (argc != 2 || l3tr_parseHex32(argv[1], &session) != 0 || session != st->sessionId) {
        l3tr_textJoin(ops, "Error: stale session ", (argc >= 2) ? argv[1] : "", " (frozen ", id, ")");
        return -1;
    }
    if (ops->rearm(ops->ctx) != 0) {
        l3tr_textJoin(ops, "Error: rearm failed; the capture stays frozen", NULL, NULL, NULL, NULL);
        return -1;
    }
    st->frozen = 0U;
    st->previousTimedOut = 0U;
    st->releases++;
    l3tr_textJoin(ops, "released ", id, NULL, NULL, NULL);
    return 0;
}

/* Called periodically with the time since the last call: rearms a session
 * idle for the hold timeout (the host is gone). 1 when it rearmed. */
static uint32_t l3tr_sessionIdle(l3tr_session_t *st, const l3tr_ops_t *ops, uint32_t elapsedMs)
{
    if (!st->frozen) {
        return 0U;
    }
    st->idleMs += elapsedMs;
    if (st->idleMs <= L3TR_HOLD_TIMEOUT_MS) {
        return 0U;
    }
    if (ops->rearm(ops->ctx) != 0) {
        return 0U;   /* tried again on the next call */
    }
    st->frozen = 0U;
    st->previousTimedOut = 1U;
    st->timeouts++;
    return 1U;
}

#endif
