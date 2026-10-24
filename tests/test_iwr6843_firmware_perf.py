"""Firmware perfstats and l3bench: the W4a performance counters and the kernel bench.

Host-compiled like tests/test_iwr6843_firmware_evidence.py, whose helpers this reuses.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from openflight.iwr6843.perf_diagnostics import parse_perf_stats
from tests.test_iwr6843_firmware_evidence import (
    EVIDENCE_PRELUDE,
    FIRMWARE,
    FIRMWARE_DIR,
    SCRATCH_HARNESS_PRELUDE,
    SCRATCH_HARNESS_STANDINS,
    _build_c,
    _evidence_block,
    _function_source,
    _rearm_task,
    _run_c,
    _scan_source,
)

# --- Task 5: perfstats and l3bench ---------------------------------------------------------


# Made-up values with known answers. The int16 kernel's worst case (-32768 in every component)
# overflows 32 bits after two complex samples, so the 64-bit accumulation is exercised.
POWER_PROBE = """#include <stdio.h>
#include "l3_evidence.h"
int main(void) {
  const int16_t iq16[] = {3, 4, -32768, 0, -1, 1};
  const int8_t iq8[] = {-128, 127, 2, -2};
  const int16_t worst16[] = {-32768, -32768, -32768, -32768, -32768, -32768, -32768, -32768};
  const int8_t worst8[] = {-128, -128, -128, -128};
  unsigned long long a = (unsigned long long)l3_powerSumIq16(iq16, 3);
  unsigned long long b = (unsigned long long)l3_powerSumIq8(iq8, 2);
  unsigned long long c = (unsigned long long)l3_powerSumIq16(iq16, 0);
  unsigned long long d = (unsigned long long)l3_powerSumIq16(worst16, 4);
  unsigned long long e = (unsigned long long)l3_powerSumIq8(worst8, 2);
  unsigned long long f = (unsigned long long)l3_powerSumIq8(iq8, 0);
  printf("%llu %llu %llu %llu %llu %llu\\n", a, b, c, d, e, f);
  return 0;
}
"""


def test_power_sums_are_exact_known_answers(tmp_path):
    """Run under UBSan: a signed int32 overflow (I*I + Q*Q can reach 2**31) would wrap to
    the right answer on this host by accident, so only the sanitizer can catch it."""
    sanitize = ("-fsanitize=undefined", "-fno-sanitize-recover=undefined")
    out = _run_c(POWER_PROBE, tmp_path, flags=sanitize).split()

    assert out == [
        str(9 + 16 + 32768**2 + 0 + 1 + 1),
        str(16384 + 16129 + 4 + 4),
        "0",
        str(8 * 32768**2),  # 2**33: past 32 bits
        str(4 * 128**2),
        "0",
    ]


UNUSED_HEADER_PROBE = '#include "l3_evidence.h"\nint main(void) { return 0; }\n'


def test_header_helpers_are_inline_so_builds_that_skip_them_do_not_warn(tmp_path):
    """Controller ruling (Task 4 review): every helper is static inline, so a build that uses
    none of them gets no unused-function warning (here an error)."""
    _build_c(UNUSED_HEADER_PROBE, tmp_path, flags=("-Wunused-function",))
    header = (FIRMWARE_DIR / "l3_evidence.h").read_text(encoding="utf-8")

    for name in (
        "l3_iq8ScanComponents",
        "l3_iq8ScanGuaranteed",
        "l3_powerSumIq16",
        "l3_powerSumIq8",
    ):
        assert re.search(rf"static inline \w+ {name}\(", header), name


def test_perfstats_and_l3bench_are_registered_in_production_builds():
    source = FIRMWARE.read_text(encoding="utf-8")
    table = source[source.rindex("cliCfg.tableEntry[0].cmd") : source.index("CLI_open(&cliCfg);")]

    assert 'cliCfg.tableEntry[11].cmd           = "perfstats";' in table
    assert "cliCfg.tableEntry[11].cmdHandlerFxn = l3_cli_perfStats;" in table
    assert 'cliCfg.tableEntry[12].cmd           = "l3bench";' in table
    assert "cliCfg.tableEntry[12].cmdHandlerFxn = l3_cli_bench;" in table
    entry = table.index("cliCfg.tableEntry[11].cmd")
    assert table.rindex("#ifdef L3_CAPTURE_EVIDENCE", 0, entry) > table.rindex("#endif", 0, entry)
    assert table.index("cliCfg.tableEntry[10].cmd") < entry


def test_l3bench_refuses_a_running_capture_before_timing():
    source = FIRMWARE.read_text(encoding="utf-8")
    bench = _function_source(source, "static int32_t l3_cli_bench", "#ifdef ENABLE_HWA_SMOKE")

    assert bench.index("if (gCaptureActive) {") < bench.index("start = l3_ticks();")
    assert "l3_powerSumIq8((const int8_t *)g_ring" in bench
    assert "l3_powerSumIq16((const int16_t *)g_ring" in bench


def test_margin_is_measured_on_armed_frame_starts_only():
    source = FIRMWARE.read_text(encoding="utf-8")
    isr = _function_source(
        source, "static void l3_frameStartISR", "static int32_t l3_startFrontEnd"
    )

    note = isr.index("l3_perfNoteArmedFrameStart(now);")
    assert isr.index("gActiveFrameFresh = 1U;") < note < isr.index("gHwaArmedForFrame = 0U;")


def test_the_margin_reference_is_stamped_before_the_chain_reads_as_armed():
    """A frame start that lands just after the arm still yields its (tiny) margin.

    Stamping after the arm flag (in the rearm task's bookkeeping) would let that frame
    start find no reference and drop exactly the near-miss margin_min exists to catch.
    """
    source = FIRMWARE.read_text(encoding="utf-8")
    start_ring = _function_source(
        source, "static int32_t l3_hwaStartRing", "static int32_t l3_configHwaProcessParam"
    )
    rearm_done = _function_source(
        source, "static void l3_perfNoteRearmDone", "static void l3_perfNoteTaskDone"
    )

    note = start_ring.index("    l3_perfNoteArmed();\n")
    assert note < start_ring.index("gHwaArmedForFrame = 1U;")
    assert start_ring.rindex("#ifdef L3_CAPTURE_EVIDENCE", 0, note) > start_ring.rindex(
        "HWA_setDMA2ACCManualTrig", 0, note
    )
    assert "gLastArmTick" not in rearm_done
    assert "gArmTickValid" not in rearm_done


def test_the_rearm_task_times_its_rearm_and_its_whole_iteration():
    source = FIRMWARE.read_text(encoding="utf-8")
    task = _rearm_task(source)

    start = task.index("uint32_t taskStart = l3_ticks();")
    assert start < task.index("l3_perfNoteRearmDone(taskStart);")
    assert task.index("gHwaRearms++;") < task.index("l3_perfNoteRearmDone(taskStart);")
    assert task.rindex("l3_perfNoteTaskDone(taskStart);") < task.rindex("gHwaRearmBusy = 0U;")
    # The whole iteration includes the normal path's IQ8 pack start and scan.
    assert task.rindex("l3_startIq8PackAndScan(") < task.rindex("l3_perfNoteTaskDone(taskStart);")


def test_pack_time_and_overlap_are_counted():
    source = FIRMWARE.read_text(encoding="utf-8")
    start = _function_source(
        source, "static int32_t l3_startIq8EdmaPack", "static void l3_waitForIq8EdmaScratch"
    )
    callback = _function_source(
        source, "static void l3_hwaOutputDoneCB", "static int32_t l3_hwaStartRing"
    )
    done = _function_source(
        source, "static void l3_iq8EdmaDoneCB", "static int32_t l3_startIq8EdmaPack"
    )

    assert start.index("gIq8PackStartTick[scratch] = l3_ticks();") < start.index(
        "EDMA_startDmaTransfer"
    )
    assert done.index("gIq8EdmaBusy[scratch] = 0U;") < done.index("l3_perfNotePackDone(scratch);")
    assert "gPerfPackOverlap++;" in callback
    overlap = callback.index("gPerfPackOverlap++;")
    assert overlap < callback.index("l3_recordFrameEvidence(completedSlot,")


def test_task_handles_are_kept_for_the_stack_report():
    """R-D3: one Task_stat call in a loop over a table of tasks, not one block per task."""
    source = FIRMWARE.read_text(encoding="utf-8")
    stacks = _function_source(
        source, "static void l3_writeStackLine", "static int32_t l3_cli_perfStats"
    )

    assert "gInitTask = Task_create(l3_initTask, &taskParams, NULL);" in source
    assert "gCtrlTask = Task_create(l3_mmwaveCtrlTask, &taskParams, NULL);" in source
    assert "gRearmTask = Task_create(l3_hwaRearmTask, &taskParams, NULL);" in source
    assert stacks.count("Task_stat(") == 1
    assert "Task_stat(tasks[i], &stat);" in stacks
    assert "Task_self()" in stacks  # the CLI task: perfstats runs in it
    assert "Hwi_getStackInfo(&hwiInfo, TRUE);" in stacks
    assert "#include <xdc/runtime/Memory.h>" in source


def _perf_region(source: str) -> str:
    """The perfstats and l3bench handlers with their #ifdef L3_CAPTURE_EVIDENCE guard."""
    stacks = source.index("static void l3_writeStackLine")
    start = source.rindex("#ifdef L3_CAPTURE_EVIDENCE", 0, stacks)
    return source[start : source.index("#ifdef ENABLE_HWA_SMOKE", stacks)]


def test_every_perfstats_write_fits_the_cli_write_buffer():
    """Each CLI_write formats into a 256-byte buffer; every call stays well under it."""
    region = _perf_region(FIRMWARE.read_text(encoding="utf-8"))
    formats = re.findall(r'CLI_write\("([^"]*)"', region)
    longest_name = max(len(name) for name in ("init", "ctrl", "rearm", "cli"))

    assert len(formats) >= 4
    for fmt in formats:
        width = len(fmt.replace("%u", "").replace("%s", "").replace("\\n", "\n"))
        width += 10 * fmt.count("%u") + longest_name * fmt.count("%s")
        assert width < 200, fmt


# Drives the firmware's own perfstats and l3bench handlers (with the evidence block) on the
# host. Everything SDK-side is a stand-in: CLI_write prints, Task_stat returns made-up stack
# figures, the cycle counter is gNow (advanced by gStep per read). Known answers only; this
# checks the reply's shape and arithmetic, never that TI's compiler or SYS/BIOS agree.
PERF_HARNESS_PRELUDE = r"""
#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
static uint32_t gNow;
static uint32_t gStep;
#define __MRC(cp, op1, crn, crm, op2) (gNow += gStep)
"""


PERF_HARNESS_STANDINS = r"""
#define TRUE 1
typedef int *Task_Handle;
typedef struct { unsigned long stackSize, used; } Task_Stat;
typedef struct { unsigned long hwiStackPeak, hwiStackSize; } Hwi_StackInfo;
static int gTaskObjects[4];
static Task_Handle gInitTask = &gTaskObjects[0];
static Task_Handle gCtrlTask = &gTaskObjects[1];
static Task_Handle gRearmTask = &gTaskObjects[2];
static Task_Handle Task_self(void) { return &gTaskObjects[3]; }
static void Task_stat(Task_Handle task, Task_Stat *stat)
{
    unsigned long index = (unsigned long)(task - gTaskObjects);

    stat->used = 100UL * (index + 1UL);
    stat->stackSize = 1024UL * (index + 1UL);
}
static int Hwi_getStackInfo(Hwi_StackInfo *info, int computeStackDepth)
{
    info->hwiStackPeak = computeStackDepth ? 900UL : 0UL;  /* the peak needs the scan */
    info->hwiStackSize = 2048UL;
    return 0;
}
static void CLI_write(const char *format, ...)
{
    va_list args;

    if (gHwiDepth != 0) {
        printf("[CLI_write with interrupts off]");  /* UART polling must not run locked */
    }
    va_start(args, format);
    vprintf(format, args);
    va_end(args);
}
static uint32_t gCpuClock = 200000000U;
static volatile uint8_t gCaptureActive;
static struct { uint32_t usedBytes; } gCapturePlan;
static uint8_t g_ring[64] __attribute__((aligned(8)));
static uint8_t gUsesIq8;
static uint8_t l3_captureUsesIq8(void) { return gUsesIq8; }
static uint32_t l3_captureBytesPerComplex(void) { return gUsesIq8 ? 2U : 4U; }
"""


PERF_HARNESS_MAIN = r"""
static void drive(void)
{
    uint32_t taskStart;

    l3_resetPerf();
    gStep = 0U;
    /* Iteration 1: woke at 1000, armed at 1400, rearm bookkeeping 1450, done 1900. */
    gNow = 1000U; taskStart = l3_ticks();
    gNow = 1400U; l3_perfNoteArmed();
    gNow = 1450U; l3_perfNoteRearmDone(taskStart);
    gNow = 1900U; l3_perfNoteTaskDone(taskStart);
    l3_perfNoteArmedFrameStart(2400U);   /* margin 1000 */
    l3_perfNoteArmedFrameStart(2500U);   /* no new arm: no second margin */
    /* Iteration 2 straddles the counter wrap. */
    gNow = 0xFFFFFF00U; taskStart = l3_ticks();
    gNow = 0xFFFFFFF0U; l3_perfNoteArmed();
    gNow = 0x10U; l3_perfNoteRearmDone(taskStart);    /* 0x110 */
    gNow = 0x200U; l3_perfNoteTaskDone(taskStart);    /* 0x300 */
    l3_perfNoteArmedFrameStart(0x30U);                /* margin 0x40 */
#ifdef L3_IQ8_EDMA_PACK
    gIq8PackStartTick[1] = 5000U;
    gNow = 5300U; l3_perfNotePackDone(1U);
    gIq8PackStartTick[0] = 6000U;
    gNow = 6100U; l3_perfNotePackDone(0U);
    gPerfPackOverlap = 3U;
#endif
    gPerfScanMaxCycles = 700U;
    gPerfScanFrames = 2U;
    gPerfScanCyclesTotal = 0x100000005ULL;
    gIq8OverflowComponents = 9U;
    gStubHeap.totalSize = 32768UL;
    gStubHeap.totalFreeSize = 9000UL;   /* the low point, sampled after a dump */
    gStubHeap.largestFreeSize = 8000UL;
    l3_sampleHeap();
    gStubHeap.totalFreeSize = 12000UL;
    gStubHeap.largestFreeSize = 11000UL;
}

/* Every ISR- or task-written perf value changes at once, as if an interrupt (frame start,
 * EDMA completion) or the rearm task ran the moment perfstats re-enabled interrupts. A
 * reply printed from one snapshot never shows these. */
static void preempt(void)
{
    gPerfRearmMaxCycles = 111111U;
    gPerfRearms = 111U;
    gPerfTaskMaxCycles = 222222U;
    gPerfTaskRuns = 222U;
    gPerfMarginMin = 33U;
    gPerfMarginFrames = 333U;
    gPerfScanMaxCycles = 444444U;
    gPerfScanFrames = 444U;
    gPerfScanCyclesTotal = 0x400000004ULL;
    gIq8OverflowComponents = 44U;
    gIq8OverflowSaturated = 1U;
#ifdef L3_IQ8_EDMA_PACK
    gPerfPackMaxCycles = 555555U;
    gPerfPacks = 555U;
    gPerfPackOverlap = 55U;
#endif
}

int main(int argc, char **argv)
{
    const char *mode = argc > 1 ? argv[1] : "";

    if (strcmp(mode, "fresh") == 0) {
        gStubHeap.totalSize = 32768UL;
        gStubHeap.totalFreeSize = 30000UL;
        gStubHeap.largestFreeSize = 29000UL;
        l3_resetPerf();
    } else if (strcmp(mode, "reset") == 0) {
        drive();
        l3_resetPerf();   /* a new sensorStart: the heap low-water runs from boot */
    } else if (strcmp(mode, "no-ctrl") == 0) {
        drive();
        gCtrlTask = NULL;
    } else if (strcmp(mode, "saturated") == 0) {
        drive();
        gIq8OverflowComponents = 0xFFFFFFFFU;
        gIq8OverflowSaturated = 1U;
    } else if (strcmp(mode, "preempted") == 0) {
        drive();
        gOnHwiRestore = preempt;
    } else {
        drive();
    }
    printf("result=%d\n", (int)l3_cli_perfStats(0, NULL));
    printf("hwi_depth=%ld\n", gHwiDepth);
    return 0;
}
"""


BENCH_HARNESS_MAIN = r"""
int main(int argc, char **argv)
{
    static const int16_t iq16[10] = {-32768, -32768, -32768, -32768, -32768,
                                     -32768, -32768, -32768, 3, 4};
    static const int8_t iq8[4] = {-128, 127, 2, -2};
    const char *mode = argc > 1 ? argv[1] : "";
    const uint32_t before = 0xFFFFFF80U;
    int32_t result;

    memcpy(g_ring, iq16, sizeof(iq16));
    gCapturePlan.usedBytes = (uint32_t)sizeof(iq16);
    if (strcmp(mode, "iq8") == 0) {
        memset(g_ring, 0x55, sizeof(g_ring));   /* bytes past the plan must not count */
        memcpy(g_ring, iq8, sizeof(iq8));
        gCapturePlan.usedBytes = (uint32_t)sizeof(iq8);
        gUsesIq8 = 1U;
    } else if (strcmp(mode, "running") == 0) {
        gCaptureActive = 1U;
    } else if (strcmp(mode, "no-plan") == 0) {
        gCapturePlan.usedBytes = 0U;
    }
    gStep = 250U;
    gNow = before;   /* the end read wraps */
    result = l3_cli_bench(0, NULL);
    printf("result=%d reads=%u\n", (int)result, (unsigned)((gNow - before) / gStep));
    return 0;
}
"""


def _perf_program(main: str) -> str:
    source = FIRMWARE.read_text(encoding="utf-8")
    return (
        PERF_HARNESS_PRELUDE
        + EVIDENCE_PRELUDE
        + _evidence_block(source)
        + PERF_HARNESS_STANDINS
        + _perf_region(source)
        + main
    )


_SCAN_BUILD = ("-DL3_IQ8_EDMA_PACK", "-DL3_IQ8_SCAN", "-Wno-unused-variable")


_CPU_PACK_BUILD = ("-Wno-unused-variable",)


def _perfstats(tmp_path: Path, mode: str, flags: tuple[str, ...] = _SCAN_BUILD) -> list[str]:
    return _run_c(_perf_program(PERF_HARNESS_MAIN), tmp_path, mode, flags=flags).splitlines()


def _fields(lines: list[str]) -> dict[str, Any]:
    """The firmware's own four reply lines through the host driver's one grammar."""
    assert [line.split()[0] for line in lines] == ["perf", "perf", "heap", "stacks"]
    return parse_perf_stats("\n".join(lines))


def test_perfstats_replies_four_parseable_lines_with_the_measured_marks(tmp_path):
    lines = _perfstats(tmp_path, "driven")
    values = _fields(lines[:4])

    assert lines[4:] == ["result=0", "hwi_depth=0"]
    assert values["tick_hz"] == 200_000_000
    assert values["rearm_max"] == 450
    assert values["rearms"] == 2
    assert values["task_max"] == 900
    assert values["task_runs"] == 2
    assert values["margin_min"] == 0x40  # measured from the arm, across the wrap
    assert values["margin_frames"] == 2
    assert values["scan_max"] == 700
    assert values["scan_frames"] == 2
    assert values["scan_total"] == (1 << 32) + 5
    assert values["pack_max"] == 300
    assert values["packs"] == 2
    assert values["pack_overlap"] == 3
    assert values["iq8_overflow"] == 9
    assert values["iq8_overflow_saturated"] is False
    assert values["heap_size"] == 32768
    assert values["heap_free"] == 12000
    assert values["heap_free_min"] == 9000  # the sampled low point, not the current value
    assert values["heap_largest"] == 11000
    assert values["stack_init"] == {"used": 100, "size": 1024}
    assert values["stack_ctrl"] == {"used": 200, "size": 2048}
    assert values["stack_rearm"] == {"used": 300, "size": 3072}
    assert values["stack_cli"] == {"used": 400, "size": 4096}
    assert values["stack_hwi"] == {"used": 900, "size": 2048}


def test_perfstats_with_nothing_measured_shows_zero_counts_and_the_margin_sentinel(tmp_path):
    """No fill-ins: every maximum comes with its count, and an unmeasured margin is
    4294967295 beside margin_frames=0, never a plausible-looking 0."""
    values = _fields(_perfstats(tmp_path, "fresh")[:4])

    assert values["margin_min"] is None  # the 4294967295 sentinel beside margin_frames=0
    assert values["unavailable"]["margin_min"] == "no samples"
    for count in ("rearms", "task_runs", "margin_frames", "scan_frames", "packs"):
        assert values[count] == 0, count
    assert values["heap_free_min"] == 30000  # perfstats samples the heap itself


def test_a_new_sensor_start_resets_the_marks_but_not_the_heap_low_water(tmp_path):
    values = _fields(_perfstats(tmp_path, "reset")[:4])

    assert values["margin_min"] is None
    assert values["rearm_max"] is values["task_max"] is values["pack_max"] is None
    assert values["scan_total"] is None
    assert values["rearms"] == values["task_runs"] == values["packs"] == 0
    assert values["heap_free_min"] == 9000  # since boot


def test_a_task_without_a_handle_is_reported_missing_not_zero(tmp_path):
    lines = _perfstats(tmp_path, "no-ctrl")

    assert (
        lines[3] == "stacks init=100/1024 ctrl=not_created rearm=300/3072 cli=400/4096 hwi=900/2048"
    )


def test_a_build_without_the_scan_says_so_instead_of_printing_zeros(tmp_path):
    """Controller ruling: the CPU-pack build has no scan or EDMA pack to time."""
    lines = _perfstats(tmp_path, "driven", flags=_CPU_PACK_BUILD)

    assert len(lines) == 6
    assert lines[1] == "perf scan=not_in_build pack=not_in_build"
    assert lines[0].startswith("perf tick_hz=200000000 rearm_max=450 ")
    assert lines[2].startswith("heap size=32768 ")
    assert lines[3].startswith("stacks init=")


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        # 4 x (2 * 32768**2) + 3*3 + 4*4 = 2**33 + 25
        ("iq16", ["bench samples=5 cycles=250 sum_hi=2 sum_lo=25", "result=0 reads=2"]),
        ("iq8", ["bench samples=2 cycles=250 sum_hi=0 sum_lo=32521", "result=0 reads=2"]),
        ("running", ["Error: stop the sensor before l3bench", "result=-1 reads=0"]),
        ("no-plan", ["Error: no capture plan; send a config first", "result=-1 reads=0"]),
    ],
)
def test_l3bench_times_one_pass_over_the_planned_ring(tmp_path, mode, expected):
    """Host-run l3bench: the stored samples only, the exact 64-bit sum in two halves, the
    elapsed ticks across a counter wrap; a running or unplanned capture is never timed."""
    program = _perf_program(BENCH_HARNESS_MAIN)

    assert _run_c(program, tmp_path, mode, flags=_SCAN_BUILD).splitlines() == expected


def test_firmware_guide_documents_the_commands_and_names_the_scan_time_honestly():
    guide = (Path(__file__).parents[1] / "docs" / "development" / "firmware.md").read_text(
        encoding="utf-8"
    )
    geometry = guide[guide.index("## Changing Capture Geometry") :]
    geometry = geometry[: geometry.index("\n## ", 1)]

    assert "| `perfstats` |" in geometry
    assert "| `l3bench` |" in geometry
    assert "sampled" in geometry  # heap free_min is sampled, not continuous
    assert "worst-case elapsed scan time, including pre-emption" in guide
    assert "worst-case cycle count" not in guide


def _locked_section(function: str) -> tuple[str, str]:
    """(inside, outside) of a function's single Hwi_disable/Hwi_restore section."""
    assert function.count("key = Hwi_disable();") == 1
    start = function.index("key = Hwi_disable();")
    end = function.index("Hwi_restore(key);", start)
    return function[start:end], function[:start] + function[end:]


@pytest.mark.parametrize(
    ("name", "next_name", "updates"),
    [
        (
            "static void l3_scanIq8Scratch",
            "static void l3_startIq8PackAndScan",
            (
                "gPerfScanMaxCycles = cycles;",
                "gPerfScanCyclesTotal += cycles;",
                "gPerfScanFrames++;",
                "gIq8OverflowComponents += overflow;",
                "gIq8OverflowComponents = 0xFFFFFFFFU;",
                "gIq8OverflowSaturated = 1U;",
            ),
        ),
        (
            "static void l3_perfNoteRearmDone",
            "static void l3_perfNoteTaskDone",
            ("gPerfRearmMaxCycles = cycles;", "gPerfRearms++;"),
        ),
        (
            "static void l3_perfNoteTaskDone",
            "static void l3_perfNoteArmedFrameStart",
            ("gPerfTaskMaxCycles = cycles;", "gPerfTaskRuns++;"),
        ),
    ],
)
def test_task_side_perf_updates_are_one_locked_section(name, next_name, updates):
    """The rearm task (priority 2) can be pre-empted by the CLI task (priority 3) between
    two stores: a 64-bit total torn in half, or a maximum without its count. Each update
    group runs with interrupts (and so task switches) off."""
    source = FIRMWARE.read_text(encoding="utf-8")
    inside, outside = _locked_section(_function_source(source, name, next_name))

    for update in updates:
        assert update in inside, update
        assert update not in outside, update
    assert "l3_ticks()" not in inside  # the timing itself stays outside the lock


def test_perfstats_prints_one_snapshot_taken_with_interrupts_off(tmp_path):
    """An interrupt or the rearm task landing just after the snapshot changes nothing in the
    reply (no 'packs=1 pack_max=0'), and no CLI_write runs with interrupts off."""
    (tmp_path / "driven").mkdir()
    (tmp_path / "preempted").mkdir()
    driven = _perfstats(tmp_path / "driven", "driven")
    preempted = _perfstats(tmp_path / "preempted", "preempted")

    assert preempted == driven
    assert "interrupts off" not in "\n".join(preempted)


def test_perfstats_snapshots_every_isr_or_task_written_value_once():
    source = FIRMWARE.read_text(encoding="utf-8")
    handler = _function_source(
        source, "static int32_t l3_cli_perfStats", "static int32_t l3_cli_bench"
    )
    inside, outside = _locked_section(handler)

    for name in (
        "gPerfRearmMaxCycles",
        "gPerfRearms",
        "gPerfTaskMaxCycles",
        "gPerfTaskRuns",
        "gPerfMarginMin",
        "gPerfMarginFrames",
        "gPerfScanMaxCycles",
        "gPerfScanFrames",
        "gPerfScanCyclesTotal",
        "gIq8OverflowComponents",
        "gIq8OverflowSaturated",
        "gPerfPackMaxCycles",
        "gPerfPacks",
        "gPerfPackOverlap",
    ):
        assert inside.count(name) == 1, name
        assert name not in outside, name  # printed from the copy only
    assert "CLI_write" not in inside
    assert handler.index("Hwi_restore(key);") < handler.index("CLI_write(")


def test_the_margin_is_documented_as_including_isr_entry_latency():
    source = FIRMWARE.read_text(encoding="utf-8")
    helper = _function_source(
        source, "static void l3_perfNoteArmedFrameStart", "static void l3_perfNotePackDone"
    )
    comment = source[
        source.rindex("/*", 0, source.index("static void l3_perfNoteArmedFrameStart")) :
    ]
    guide = (Path(__file__).parents[1] / "docs" / "development" / "firmware.md").read_text(
        encoding="utf-8"
    )
    row = next(line for line in guide.splitlines() if line.startswith("| `perfstats` |"))

    assert "entry latency" in comment[: comment.index("*/")]
    assert "gPerfMarginMin" in helper
    assert "frame-start ISR entry latency" in row


# Drives l3_scanIq8Scratch near the 32-bit limit of the session's overflow total. Frame 0
# (ping) has 3 components outside int8, frame 1 (pong) has none. Made-up values.
SATURATION_HARNESS_MAIN = r"""
#include <stdio.h>
#include <string.h>

static void show(void)
{
    printf("%u %u\n", (unsigned)gIq8OverflowComponents, (unsigned)gIq8OverflowSaturated);
}

int main(void)
{
    const int16_t ping[6] = {12, -200, 127, -128, 300, -32768};
    uint32_t i;

    for (i = 0U; i < 6U; i++) {
        g_iq16FrameScratch[0][i] = ping[i];
    }
    gFrameBytes[0] = 6U;
    gFrameBytes[1] = 4U;   /* the pong scratch is all zeros: no overflow */
    gStep = 10U;

    gIq8OverflowComponents = 0xFFFFFFFCU;
    l3_scanIq8Scratch(0U, 0U);      /* + 3 lands exactly on 0xFFFFFFFF: still exact */
    show();
    l3_scanIq8Scratch(1U, 1U);      /* + 0: still exact */
    show();
    l3_scanIq8Scratch(0U, 0U);      /* + 3 would wrap to 2: saturates instead */
    show();
    l3_scanIq8Scratch(1U, 1U);      /* stays saturated */
    show();
    gIq8OverflowComponents = 0xFFFFFFFEU;
    gIq8OverflowSaturated = 0U;
    l3_scanIq8Scratch(0U, 0U);      /* + 3 from 0xFFFFFFFE: saturates */
    show();
    l3_resetPerf();                 /* a new sensorStart clears both */
    show();
    printf("hwi_depth=%ld\n", gHwiDepth);
    return 0;
}
"""


def test_the_overflow_total_saturates_instead_of_wrapping(tmp_path):
    """No fill-ins: a wrapped total would read as a small, measured-looking count."""
    source = FIRMWARE.read_text(encoding="utf-8")
    program = (
        SCRATCH_HARNESS_PRELUDE
        + EVIDENCE_PRELUDE
        + _evidence_block(source)
        + SCRATCH_HARNESS_STANDINS
        + _scan_source(source)
        + SATURATION_HARNESS_MAIN
    )
    lines = _run_c(program, tmp_path, flags=("-Wno-unused-variable",)).splitlines()

    assert lines == [
        "4294967295 0",
        "4294967295 0",
        "4294967295 1",
        "4294967295 1",
        "4294967295 1",
        "0 0",
        "hwi_depth=0",
    ]


def test_perfstats_flags_a_saturated_overflow_total_in_the_parser_grammar(tmp_path):
    """iq8_overflow stays numeric (Task 6's grammar); iq8_overflow_saturated=1 marks it as a
    floor, not a count."""
    lines = _perfstats(tmp_path, "saturated")
    values = _fields(lines[:4])

    assert values["iq8_overflow"] == 4294967295
    assert values["iq8_overflow_saturated"] is True
    assert lines[1].endswith(" iq8_overflow=4294967295 iq8_overflow_saturated=1")


def test_the_saturation_flag_resets_with_the_perf_marks_and_is_documented():
    source = FIRMWARE.read_text(encoding="utf-8")
    block = _evidence_block(source)
    guide = (Path(__file__).parents[1] / "docs" / "development" / "firmware.md").read_text(
        encoding="utf-8"
    )
    row = next(line for line in guide.splitlines() if line.startswith("| `perfstats` |"))

    assert "static uint8_t  gIq8OverflowSaturated;" in block
    reset = _function_source(block, "static void l3_resetPerf", "static void l3_perfNoteArmed")
    assert "    gIq8OverflowSaturated = 0U;" in reset
    assert "iq8_overflow_saturated=1" in row


def test_the_overflow_saturation_comment_states_the_scans_real_component_counts():
    """M9: the scan covers at most N_TX x L3_MAX_LOOPS x N_RX x 64 bins x 2 components
    (24,576 with 3 TX), and 15,264 for profile C (3 TX x 12 loops x 4 RX x 53 bins x 2),
    the number docs/development/firmware.md gives; the comment says both."""
    source = FIRMWARE.read_text(encoding="utf-8")
    start = source.index("static void l3_scanIq8Scratch")
    function = source[start : source.index("static void l3_startIq8PackAndScan", start)]

    assert 3 * 16 * 4 * 64 * 2 == 24_576 and 3 * 12 * 4 * 53 * 2 == 15_264
    assert "24,576" in function and "15,264" in function
    assert "about 24k" not in function
