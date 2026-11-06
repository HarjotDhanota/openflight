"""Execute the live queued-frame ball call site with producer preemptions.

The task, ball reader and stale guard are extracted verbatim from l3_dump.c;
only hardware/angle/timing services are shimmed. Queue ownership and detector
update accounting use the real detect_queue.c and l3_ball.c implementations.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from pathlib import Path

import pytest

from openflight.iwr6843 import firmware_host as fw

FIRMWARE = Path(__file__).parents[1] / "firmware" / "iwr6843"


def _function(source: str, signature: str) -> str:
    start = source.index(signature + "\n{")
    end = source.index("\n}\n", start) + 3
    return source[start:end]


HARNESS = r"""
#include <stdint.h>
#include <string.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include "l3_ball.h"
#include "detect_queue.h"

#define L3_DETECT_POST_EPOCH 0xFFFFFFFFU
#define L3_TIMING_FLAG_POST 1U
#define L3_TIMING_FLAG_BEHIND 2U
#define L3_TIMING_FLAG_STALE 4U
#define L3_BALL_ANGLE_REFRESH_UPDATES 8U
#define L3_BALL_ANGLE_MIN_PEAK_RATIO 1.0F
#define L3_PROF_BALL_DETECT 0U
#define BIOS_WAIT_FOREVER 0U
typedef uintptr_t UArg;
typedef struct { uint32_t binStart, binCount; } l3_detect_frame_t;
typedef struct { int unused; } l3_angle_snapshot_t;
static struct { uint32_t preFrames, loops; } gCapturePlan;
static struct {
    uint32_t slot, epoch, acquired, dequeued, decided;
    uint8_t flags, depth;
} gDetectEvent;
static struct { uint8_t elevationValid; float elevationPeakRatio; } gBallAngle;
static L3DetectQueue gDetectQueue;
static l3_ball_t gBall;
static l3_ball_cfg_t gBallCfg;
static l3_ball_angle_clock_t gBallAngleClock;
static volatile uint32_t gPreFramesCaptured;
static uint32_t gDetectRingEpoch;
static uint32_t gDetectStale, gDetectStaleAfterRead, gDetectShed;
static uint8_t gDetectBehind, gBallBusy, gBallAngleValid;
static int gDetectSemaphore, gRadarCal, gTiming;
static uint32_t beforeCadence, duringRead, reads, triggerCalls, finishes;
static jmp_buf taskExit;

static uint32_t Cycleprofiler_getTimeStamp(void) { return 0U; }
static void Semaphore_pend(int semaphore, uint32_t timeout)
{
    (void)semaphore; (void)timeout;
    if (!l3detect_depth(&gDetectQueue)) { longjmp(taskExit, 1); }
}
static l3_detect_frame_t l3_detectFrameOf(uint32_t slot)
{
    l3_detect_frame_t frame = {20U, 2U};
    (void)slot;
    gPreFramesCaptured += beforeCadence;
    return frame;
}
static float l3_verticalStaticPower(const l3_detect_frame_t *frame,
                                  uint32_t bin, uint32_t step)
{
    (void)frame; (void)bin; (void)step;
    if (!reads++) { gPreFramesCaptured += duringRead; }
    return 100.0F;
}
static void l3_channelSnapshotStatic(const l3_detect_frame_t *frame,
                                     uint32_t bin, l3_angle_snapshot_t *snapshot)
{ (void)frame; (void)bin; (void)snapshot; }
static int l3_angle_estimate(const void *cal, const void *snapshot, void *angle)
{ (void)cal; (void)snapshot; (void)angle; return 0; }
static void l3_profileStage(uint32_t stage, uint32_t ticks)
{ (void)stage; (void)ticks; }
static void l3_timingEnsure(void) {}
static void l3_timing_record(void *timing, const void *event)
{ (void)timing; (void)event; finishes++; }
static void l3_considerBallTrack(uint32_t slot) { (void)slot; }
static void l3_considerSelfTrigger(uint32_t slot) { (void)slot; triggerCalls++; }

/* EXACT_FUNCTIONS */

void run_case(uint32_t epoch, uint32_t current, uint32_t before,
              uint32_t during, uint32_t cadence, uint32_t enabled,
              uint32_t loops)
{
    l3_ball_cfg_defaults_at(&gBallCfg, 8U);
    gBallCfg.enabled = (uint8_t)enabled;
    gBallCfg.framesPerUpdate = cadence;
    l3_ball_init(&gBall, &gBallCfg);
    memset(&gBallAngleClock, 0, sizeof(gBallAngleClock));
    memset(&gDetectEvent, 0, sizeof(gDetectEvent));
    gCapturePlan.preFrames = 9U;
    gCapturePlan.loops = loops;
    gPreFramesCaptured = current;
    gDetectRingEpoch = L3_DETECT_POST_EPOCH;
    gDetectStale = gDetectStaleAfterRead = gDetectShed = 0U;
    gDetectBehind = gBallBusy = gBallAngleValid = 0U;
    beforeCadence = before; duringRead = during;
    reads = triggerCalls = finishes = 0U;
    l3detect_init(&gDetectQueue);
    (void)l3detect_publish(&gDetectQueue, (uint16_t)(epoch % 9U), epoch, 0U);
    if (!setjmp(taskExit)) { l3_detectTask(0U, 0U); }
}
uint32_t result(uint32_t field)
{
    const uint32_t fields[] = {
        gBall.updates, reads, gDetectShed, gDetectStale,
        gDetectStaleAfterRead, gBallBusy, triggerCalls, finishes,
        gDetectEvent.epoch, gPreFramesCaptured, gDetectRingEpoch
    };
    return fields[field];
}
int main(int argc, char **argv)
{
    uint32_t args[7], i;
    if (argc != 8) { return 2; }
    for (i = 0U; i < 7U; i++) { args[i] = (uint32_t)strtoul(argv[i + 1U], NULL, 10); }
    run_case(args[0], args[1], args[2], args[3], args[4], args[5], args[6]);
    for (i = 0U; i < 11U; i++) { printf("%u ", (unsigned)result(i)); }
    return 0;
}
"""


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    compiler = fw.host_compiler_command()
    wsl = os.environ.get("OPENFLIGHT_TEST_WSL_DISTRO") if compiler is None else None
    if compiler is None and not wsl:
        pytest.skip("no C compiler for queued-frame cadence regression")
    source = (FIRMWARE / "l3_dump.c").read_text(encoding="utf-8")
    signatures = (
        "static int32_t l3_detectFrameStale(const l3_detect_frame_t *frame)",
        "static void l3_considerBall(uint32_t slot)",
        "static void l3_detectFinish(void)",
        "static void l3_detectTask(UArg arg0, UArg arg1)",
    )
    functions = "\n\n".join(_function(source, signature) for signature in signatures)
    build = tmp_path_factory.mktemp("ball_cadence")
    harness = build / "cadence.c"
    harness.write_text(HARNESS.replace("/* EXACT_FUNCTIONS */", functions), encoding="utf-8")
    output = build / "cadence.so"
    if wsl:

        def linux(path):
            return "/mnt/" + path.drive[0].lower() + path.as_posix()[2:]

        prefix = ["wsl", "--distribution", wsl, "--exec"]
        subprocess.run(
            [
                *prefix,
                "gcc",
                "-std=c99",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-O1",
                "-I",
                linux(FIRMWARE),
                "-o",
                linux(output),
                linux(harness),
                linux(FIRMWARE / "detect_queue.c"),
                linux(FIRMWARE / "l3_ball.c"),
                "-lm",
            ],
            check=True,
            timeout=120,
        )

        def run_wsl(args):
            done = subprocess.run(
                [*prefix, linux(output), *map(str, args)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return list(map(int, done.stdout.split()))

        return run_wsl
    subprocess.run(
        [
            *compiler,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-shared",
            "-fPIC",
            "-O1",
            "-I",
            str(FIRMWARE),
            "-o",
            str(output),
            str(harness),
            str(FIRMWARE / "detect_queue.c"),
            str(FIRMWARE / "l3_ball.c"),
        ],
        check=True,
        timeout=120,
    )
    library = ctypes.CDLL(str(output))
    library.run_case.argtypes = [ctypes.c_uint32] * 7
    library.run_case.restype = None
    library.result.argtypes = [ctypes.c_uint32]
    library.result.restype = ctypes.c_uint32

    def run_native(args):
        library.run_case(*args)
        return [library.result(index) for index in range(11)]

    return run_native


def run(lib, epoch, *, current=None, before=0, during=0, cadence=8, enabled=1, loops=12):
    values = lib(
        [epoch, epoch if current is None else current, before, during, cadence, enabled, loops]
    )
    names = (
        "updates",
        "reads",
        "behind",
        "stale_before",
        "stale_after",
        "busy",
        "trigger",
        "finished",
        "epoch",
        "current",
        "ring_epoch",
    )
    return dict(zip(names, values, strict=True))


@pytest.mark.parametrize("epoch,before,updates", [(7, 1, 0), (8, 1, 1), (15, 1, 0), (16, 1, 1)])
def test_producer_interrupt_after_behind_guard_keeps_queued_cadence(lib, epoch, before, updates):
    got = run(lib, epoch, before=before)
    assert got["updates"] == updates, got
    assert got["reads"] == 2 * updates
    assert (got["epoch"], got["current"]) == (epoch, epoch + before)
    assert (got["behind"], got["stale_before"], got["stale_after"], got["busy"]) == (0, 0, 0, 0)
    assert (got["trigger"], got["finished"], got["ring_epoch"]) == (1, 1, 0xFFFFFFFF)


@pytest.mark.parametrize("epoch", [1, 7, 8, 9, 15, 16, 24, 0xFFFFFFF8])
def test_stable_producer_preserves_eight_frame_cadence(lib, epoch):
    got = run(lib, epoch)
    assert got["updates"] == int(epoch % 8 == 0)
    assert got["epoch"] == epoch
    assert (got["behind"], got["stale_before"], got["stale_after"], got["busy"]) == (0, 0, 0, 0)


@pytest.mark.parametrize("kwargs", [{"cadence": 0}, {"enabled": 0}, {"loops": 0}])
def test_configuration_guards_avoid_reads_and_updates(lib, kwargs):
    got = run(lib, 8, before=1, **kwargs)
    assert (got["updates"], got["reads"], got["busy"]) == (0, 0, 0)
    assert (got["trigger"], got["finished"]) == (1, 1)


def test_behind_due_frame_is_shed_without_catching_up_on_next_frame(lib):
    due = run(lib, 8, current=9)
    assert (due["updates"], due["reads"], due["behind"], due["trigger"]) == (0, 0, 1, 1)
    following = run(lib, 9)
    assert following["updates"] == 0
    assert run(lib, 16)["updates"] == 1


def test_expired_slot_is_rejected_before_reading(lib):
    got = run(lib, 8, current=16)
    assert (got["updates"], got["reads"], got["stale_before"], got["trigger"], got["finished"]) == (
        0,
        0,
        1,
        0,
        0,
    )


@pytest.mark.parametrize("before,during", [(8, 0), (0, 8), (1, 7)])
def test_due_slot_overwritten_after_pop_never_updates(lib, before, during):
    got = run(lib, 8, before=before, during=during)
    assert (got["updates"], got["reads"], got["stale_after"], got["busy"]) == (0, 2, 1, 0)
    assert (got["trigger"], got["finished"]) == (1, 1)


@pytest.mark.parametrize("cadence", [1, 2, 3, 8, 16])
def test_configured_interval_uses_frame_identity(lib, cadence):
    got = run(lib, 2 * cadence, before=1, cadence=cadence)
    assert got["updates"] == 1, got
