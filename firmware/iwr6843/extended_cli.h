#ifndef OPENFLIGHT_EXTENDED_CLI_H
#define OPENFLIGHT_EXTENDED_CLI_H

#ifdef L3_USB_CLOCK_PROBE
#include "clock_probe.h"
#endif
#ifdef L3_TWO_ROUND
#include "two_round_cli.h"
extern CLI_CmdTableEntry gCLIMMWaveExtensionTable[];
static CLI_CmdTableEntry gTwoHandlers[CLI_MAX_CMD];
static uint32_t gTwoHandlerCount;
static Task_Handle gTwoWatchTask;

static int32_t l3_cli_sessionGuard(int32_t argc, char *argv[])
{
    uint32_t i;
    int32_t status;
    uintptr_t key = Hwi_disable();
    if (gTwoBusy) { Hwi_restore(key); return -1; }
    gTwoBusy = 1U;
    Hwi_restore(key);
    for (i = 0U; i < gTwoHandlerCount; i++) if (!strcmp(argv[0], gTwoHandlers[i].cmd)) break;
    if (i == gTwoHandlerCount ||
        (gTwoFault && strcmp(argv[0], "stats")) ||
        (gTwoHeld && strcmp(argv[0], "overview") && strcmp(argv[0], "cells") && strcmp(argv[0], "release"))) {
        CLI_write("Error: capture session held or recovery failed; use its session commands\n");
        gTwoBusy = 0U;
        return -1;
    }
    status = gTwoHandlers[i].cmdHandlerFxn(argc, argv);
    if (!strcmp(argv[0], "stats"))
        CLI_write("two_round held=%u fault=%u timeouts=%u last_timeout=%08x\n",
                  (unsigned)gTwoHeld, (unsigned)gTwoFault,
                  (unsigned)gTwoTimeouts, (unsigned)gTwoLastTimeout);
    gTwoLastActivity = Clock_getTicks();
    gTwoBusy = 0U;
    return status;
}
#endif

static int32_t l3_registerExtendedCli(CLI_Cfg *cfg)
{
    uint32_t index = 0U;
#define L3_ADD_COMMAND(command, help, handler) do { \
    if (index >= CLI_MAX_CMD - 1U) return -1; \
    cfg->tableEntry[index].cmd = command; \
    cfg->tableEntry[index].helpString = help; \
    cfg->tableEntry[index++].cmdHandlerFxn = handler; \
} while (0)
    L3_ADD_COMMAND("sensorStart", "Configure + start capture", l3_cli_sensorStart);
    L3_ADD_COMMAND("sensorStop", "Stop the front-end", l3_cli_sensorStop);
    L3_ADD_COMMAND("l3dump", "Freeze and stream a complete dump", l3_cli_dump);
    L3_ADD_COMMAND("stats", "Report capture counters", l3_cli_stats);
#ifdef ENABLE_HWA_SMOKE
    L3_ADD_COMMAND("hwastats", "Report HWA status", l3_cli_hwaStats);
    L3_ADD_COMMAND("hwatest", "Run HWA FFT self-test", l3_cli_hwaTest);
    L3_ADD_COMMAND("hwareal", "Run HWA FFT on current ADCBUF chirp", l3_cli_hwaReal);
#endif
#ifdef CONFIGURABLE_CAPTURE
    L3_ADD_COMMAND("captureCfg", "Configure capture windows", l3_cli_captureCfg);
    L3_ADD_COMMAND("phaseCaptureCfg", "Configure phased capture windows", l3_cli_phaseCaptureCfg);
#ifdef L3_RING_IQ8
    L3_ADD_COMMAND("captureFormat", "Select iq16 or iq8", l3_cli_captureFormat);
#ifdef L3_IQ8_EDMA_PACK
    L3_ADD_COMMAND("iq8Scale", "Select IQ8 scale", l3_cli_iq8Scale);
#endif
#endif
#endif
#ifdef L3_CAPTURE_EVIDENCE
    L3_ADD_COMMAND("perfstats", "Report timing and memory", l3_cli_perfStats);
    L3_ADD_COMMAND("l3bench", "Time a pass over the stopped ring", l3_cli_bench);
#endif
#ifdef L3_USB_CLOCK_PROBE
    L3_ADD_COMMAND("clockProbe", "USB clock diagnostic", l3_cli_clockProbe);
#ifdef L3_USB_CLOCK_COMPACT
    L3_ADD_COMMAND("cp", "Compact USB clock diagnostic", l3_cli_cp);
#endif
#endif
#ifdef L3_TWO_ROUND
    {
        uint32_t i;
        Task_Params params;
        L3_ADD_COMMAND("overview", "Freeze or repeat the held overview", l3_cli_overview);
        L3_ADD_COMMAND("cells", "Read held IQ16 cells", l3_cli_cells);
        L3_ADD_COMMAND("release", "Release a held session", l3_cli_release);
        for (i = 0U; gCLIMMWaveExtensionTable[i].cmdHandlerFxn; i++) {
            L3_ADD_COMMAND(gCLIMMWaveExtensionTable[i].cmd,
                           gCLIMMWaveExtensionTable[i].helpString,
                           gCLIMMWaveExtensionTable[i].cmdHandlerFxn);
        }
        gTwoHandlerCount = index;
        for (i = 0U; i < index; i++) {
            gTwoHandlers[i] = cfg->tableEntry[i];
            cfg->tableEntry[i].cmdHandlerFxn = l3_cli_sessionGuard;
        }
        Task_Params_init(&params);
        params.priority = L3_CLI_TASK_PRIORITY - 1U;
        params.stackSize = 2048U;
        gTwoWatchTask = Task_create(l3_two_watchTask, &params, NULL);
        if (gTwoWatchTask == NULL) return -1;
    }
#endif
#undef L3_ADD_COMMAND
    return 0;
}

#endif
