#ifndef OPENFLIGHT_L3_CLI_TABLE_H
#define OPENFLIGHT_L3_CLI_TABLE_H

/* The CLI command table, filled in one place.
 *
 * Every enabled command is appended with a running index, so a disabled
 * feature never leaves a NULL gap. The SDK's CLI_open counts entries up to the
 * first NULL and ignores everything after it, so a gap would silently drop
 * every later command. Positions are not wire identifiers: the host
 * dispatches by name.
 *
 * l3_dump.c includes this once, after every handler is defined.
 * tests/test_iwr6843_firmware_cli_table.py compiles it against a stub of the
 * SDK's CLI_Cfg and checks the names, order and dispatch for each flag set. */

/* Commands each feature adds. Keep these beside the blocks in
 * l3_cliRegister: the host test fails if a count and its block disagree. */
#define L3_CLI_CORE_COUNT 4U
#ifdef ENABLE_HWA_SMOKE
#define L3_CLI_HWA_COUNT 3U
#else
#define L3_CLI_HWA_COUNT 0U
#endif
#ifdef CONFIGURABLE_CAPTURE
#define L3_CLI_CAPTURE_COUNT 2U
#if defined(L3_RING_IQ8) && defined(L3_IQ8_EDMA_PACK)
#define L3_CLI_IQ8_COUNT 2U
#elif defined(L3_RING_IQ8)
#define L3_CLI_IQ8_COUNT 1U
#else
#define L3_CLI_IQ8_COUNT 0U
#endif
#else
#define L3_CLI_CAPTURE_COUNT 0U
#define L3_CLI_IQ8_COUNT 0U
#endif
#ifdef L3_CAPTURE_EVIDENCE
#define L3_CLI_EVIDENCE_COUNT 2U
#else
#define L3_CLI_EVIDENCE_COUNT 0U
#endif

#define L3_CLI_COUNT (L3_CLI_CORE_COUNT + L3_CLI_HWA_COUNT + L3_CLI_CAPTURE_COUNT + \
                      L3_CLI_IQ8_COUNT + L3_CLI_EVIDENCE_COUNT)

/* CLI_open appends its built-in "help" after the last entry, so at most
 * CLI_MAX_CMD - 1 of the table's slots are ours. The SDK is not edited. */
#if L3_CLI_COUNT > (CLI_MAX_CMD - 1)
#error "too many CLI commands: CLI_open needs one free slot for its built-in help"
#endif

#define L3_CLI_ADD(cfg, next, name, help, fxn)              \
    do {                                                    \
        (cfg)->tableEntry[(next)].cmd           = (name);   \
        (cfg)->tableEntry[(next)].helpString    = (help);   \
        (cfg)->tableEntry[(next)].cmdHandlerFxn = (fxn);    \
        (next)++;                                           \
    } while (0)

/* Fills cfg->tableEntry from slot 0 and returns how many slots it used
 * (L3_CLI_COUNT). The caller's cfg must start zeroed.
 *
 * `inline` matters to the image: as a plain static function it still gets
 * inlined at -O3, but it shifts the compiler's file-wide inlining budget and
 * l3_iq8EdmaDoneCB stops inlining l3_ticks. Marked inline, the all-off build
 * is byte-identical to the build before this table moved here. */
static inline uint32_t l3_cliRegister(CLI_Cfg *cfg)
{
    uint32_t next = 0U;

    L3_CLI_ADD(cfg, next, "sensorStart", "Configure + start capture (no args)",
               l3_cli_sensorStart);
    L3_CLI_ADD(cfg, next, "sensorStop", "Stop the front-end", l3_cli_sensorStop);
    L3_CLI_ADD(cfg, next, "l3dump", "Freeze and stream one L3 snapshot dump", l3_cli_dump);
    L3_CLI_ADD(cfg, next, "stats", "Report capture counters", l3_cli_stats);
#ifdef ENABLE_HWA_SMOKE
    L3_CLI_ADD(cfg, next, "hwastats", "Report HWA smoke-test status", l3_cli_hwaStats);
    L3_CLI_ADD(cfg, next, "hwatest", "Run HWA FFT self-test", l3_cli_hwaTest);
    L3_CLI_ADD(cfg, next, "hwareal", "Run HWA FFT on current ADCBUF chirp", l3_cli_hwaReal);
#endif
#ifdef CONFIGURABLE_CAPTURE
    L3_CLI_ADD(cfg, next, "captureCfg",
               "captureCfg preStart preBins postStart postBins lateStart postFrames [postStride]",
               l3_cli_captureCfg);
    L3_CLI_ADD(cfg, next, "phaseCaptureCfg",
               "phaseCaptureCfg preStart preBins preFrames impactStart impactBins "
               "impactFrames postStart postBins lateStart ballFrames ballStride",
               l3_cli_phaseCaptureCfg);
#ifdef L3_RING_IQ8
    L3_CLI_ADD(cfg, next, "captureFormat", "captureFormat iq16|iq8", l3_cli_captureFormat);
#ifdef L3_IQ8_EDMA_PACK
    L3_CLI_ADD(cfg, next, "iq8Scale", "iq8Scale 16|32|64|128|256", l3_cli_iq8Scale);
#endif
#endif
#endif
#ifdef L3_CAPTURE_EVIDENCE
    L3_CLI_ADD(cfg, next, "perfstats",
               "Report worst-case elapsed times (ticks, including pre-emption), margin, heap and stacks",
               l3_cli_perfStats);
    L3_CLI_ADD(cfg, next, "l3bench", "Time one pass over the stopped ring", l3_cli_bench);
#endif
    return next;
}

#endif
