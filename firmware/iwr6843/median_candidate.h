#ifndef OPENFLIGHT_MEDIAN_CANDIDATE_H
#define OPENFLIGHT_MEDIAN_CANDIDATE_H
#define L3TR_MAX_FRAMES 64U
#define L3TR_MAX_BINS 64U
#define L3TR_MAX_SPAN 128U
#define L3TR_MAX_RX 4U
#define L3TR_N_LOOP_TX 3U
#define L3TR_SCOPE_BURST 0U
#define L3TR_SCOPE_WINDOW 1U
#define L3TR_CANDIDATES 512U
#define L3TR_HIST_BUCKETS 2048U
typedef struct {
    uint16_t nFrames, loops, nRx, nSamples;
    uint8_t start[64U], count[64U];
    const int16_t *frame[64U];
} l3tr_capture_t;
typedef struct {
    int32_t sums[128U][2][4][2];
    uint16_t holders[128U];
    int32_t loopSum[2][4][64U][2];
    uint32_t hist[2048U];
    uint64_t cand[512U];
    uint32_t passes;
    uint16_t spanStart;
} l3tr_work_t;
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

static inline int32_t l3tr_re(const int16_t *frame, uint32_t index)
{
    return (int32_t)frame[2U * index + 1U];
}

static inline int32_t l3tr_im(const int16_t *frame, uint32_t index)
{
    return (int32_t)frame[2U * index];
}

static inline uint32_t l3tr_index(const l3tr_capture_t *cap, uint32_t count, uint32_t loop,
                                  uint32_t tx, uint32_t rx, uint32_t bin)
{
    return ((loop * L3TR_N_LOOP_TX + tx) * cap->nRx + rx) * count + bin;
}

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

static l3tr_capture_t gMedianCapture;
static l3tr_work_t gMedianWork;
static void l3_two_loadMedian(const l3_two_state_t *s)
{
    uint32_t f;
    gMedianCapture.nFrames = s->capture.frames;
    gMedianCapture.loops = s->capture.loops;
    gMedianCapture.nRx = 4U;
    gMedianCapture.nSamples = s->capture.bins;
    for (f = 0U; f < s->capture.frames; f++) {
        gMedianCapture.start[f] = s->capture.starts[f];
        gMedianCapture.count[f] = s->capture.counts[f];
        gMedianCapture.frame[f] = s->capture.samples[f];
    }
    memcpy(gMedianWork.sums, s->sums, sizeof(s->sums));
    memcpy(gMedianWork.holders, s->holders, sizeof(s->holders));
    gMedianWork.spanStart = 0U;
    gMedianWork.passes = 0U;
}
#endif
