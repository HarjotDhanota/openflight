"""Known-answer GPIO/freeze tests; no physical edge or timing accuracy is simulated."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIRMWARE = Path(__file__).parents[1] / "firmware" / "iwr6843"


@pytest.fixture(scope="module")
def c_toolchain():
    if compiler := shutil.which("cc"):
        return [compiler], [], str
    if sys.platform == "win32" and shutil.which("wsl"):
        prefix = ["wsl", "-d", "Ubuntu-24.04", "--exec"]
        check = subprocess.run([*prefix, "gcc", "--version"], capture_output=True, timeout=20)
        if check.returncode == 0:

            def linux_path(path):
                path = Path(path).resolve()
                return f"/mnt/{path.drive[0].lower()}{path.as_posix()[2:]}"

            return [*prefix, "gcc"], prefix, linux_path
    pytest.skip("requires a host C compiler, or gcc in WSL Ubuntu-24.04")


def run_c(tmp_path, c_toolchain, source):
    compiler, runner, path = c_toolchain
    probe = tmp_path / "gate.c"
    probe.write_text(source, encoding="utf-8")
    executable = tmp_path / "gate-probe"
    subprocess.run(
        [
            *compiler,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-unused-function",
            "-I",
            path(FIRMWARE),
            path(probe),
            "-o",
            path(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return subprocess.run(
        [*runner, path(executable)], check=True, capture_output=True, timeout=15
    ).stdout


def function_source(name):
    source = (FIRMWARE / "l3_dump.c").read_text(encoding="utf-8")
    match = re.search(rf"(?:static )?(?:void|int32_t) {name}\([^)]*\)\n\{{", source)
    assert match is not None
    return source[match.start() : source.index("\n}", match.end()) + 2]


HARNESS = r"""
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "l3_gate.h"
#define L3_GATE_INPUT 1
#define L3_CAPTURE_EVIDENCE 1
#define CONFIGURABLE_CAPTURE 1
#define HWA_CHAINED_SNAPSHOT_RING 1
#define BIOS_NO_WAIT 0
#define SOC_XWR68XX_GPIO_28 28
#define SOC_XWR68XX_PINP4_PADBB 27
#define SOC_XWR68XX_PINP4_PADBB_GPIO_28 0
#define PINMUX_OUTEN_RETAIN_HW_CTRL 0
#define PINMUX_INPEN_RETAIN_HW_CTRL 0
#define GPIO_CFG_INPUT 1
#define GPIO_CFG_IN_INT_RISING 2
#define GPIO_CFG_IN_INT_LOW 4
static volatile l3_gate_state_t gGate;
static uint8_t gGateEnabled = 1, gGateConfigured;
static uint32_t gNumFrame = 100, gRingFrame = 40;
static uint32_t gHwaFreezeRequestFrame, gHwaFreezeTargetFrame, gHwaFreezeRequests;
static uint32_t gHwaFreezeTimeouts, gHwaFreezeCompletions;
static uint8_t gCaptureActive = 1, gHwaShutdownRequested, gHwaFreezeRequested;
static uint8_t gPostCaptureStarted, gActiveFrameShouldKeep = 1, gActiveFrameIsPost;
static uint32_t gPostFramesCaptured, gPostFramesObserved;
static uint8_t gHwaDoneSeen = 1, gHwaOutputSeen = 1, gHwaRearmPending;
static struct { uint32_t postFrames; } gCapturePlan = {2};
static unsigned gFreezeTick, gStopTick, now = 1000, stops, gpioEnabled, waitCalls;
static uint32_t gGateClockTick, rtosNow = 100, Clock_tickPeriod = 1000;
static uint32_t Clock_getTicks(void) { return rtosNow; }
static int freezeSem, rearmSem, finishOnWait;
static int *gHwaFreezeSemaphore = &freezeSem, *gHwaRearmSemaphore = &rearmSem;
static void l3_requestHwaPostFrames(void);
static void l3_hwaMaybeQueueRearm(void);
static uint32_t l3_ticks(void) { return now++; }
static uintptr_t Hwi_disable(void) { return 0; }
static void Hwi_restore(uintptr_t key) { (void)key; }
static void GPIO_disableInt(uint32_t pin) { assert(pin == 28); gpioEnabled = 0; }
static void GPIO_enableInt(uint32_t pin) { assert(pin == 28); gpioEnabled = 1; }
static void GPIO_clearInt(uint32_t pin) { assert(pin == 28); }
static void GPIO_setConfig(uint32_t pin, uint32_t cfg) { assert(pin == 28 && cfg == 7); }
static void GPIO_setCallback(uint32_t pin, void (*callback)(uint32_t)) {
    assert(pin == 28 && callback != NULL);
}
static int Pinmux_Set_OverrideCtrl(int pin, int a, int b) {
    assert(pin == 27 && a == 0 && b == 0); return 0;
}
static int Pinmux_Set_FuncSel(int pin, int func) { assert(pin == 27 && func == 0); return 0; }
#define CLI_write(...) ((void)0)
static void Semaphore_post(int *sem) { (*sem)++; }
static int Semaphore_pend(int *sem, unsigned timeout) {
#ifdef TEST_IQ8
    if (timeout == BIOS_WAIT_FOREVER) {
        if (*sem) { (*sem)--; return 1; }
        longjmp(workerExit, 1);
    }
#endif
    if (timeout) {
        waitCalls++;
        if (finishOnWait) {
            gActiveFrameIsPost = 1;
            gPostFramesCaptured = 2;
            gHwaRearmPending = 0;
            l3_hwaMaybeQueueRearm();
        }
    }
    if (!*sem) return 0;
    (*sem)--; return 1;
}
static int stopFails;
static void *gMMWaveHandle, *gHwaHandle, *gEdmaHandle;
#define L3_HWA_OUT_PING_CHANNEL 1
#define L3_HWA_OUT_PONG_CHANNEL 2
#define L3_HWA_SIGNATURE_CHANNEL 3
#define EDMA3_CHANNEL_TYPE_DMA 0
static int MMWave_stop(void *handle, int32_t *error) {
    (void)handle; *error = -1; stops++; return stopFails ? -1 : 0;
}
static void Task_sleep(unsigned ticks) { (void)ticks; }
static int HWA_enable(void *handle, unsigned enabled) { (void)handle; (void)enabled; return 0; }
static int EDMA_disableChannel(void *handle, unsigned channel, unsigned type) {
    (void)handle; (void)channel; (void)type; return 0;
}
"""


def integrated_source(prefix="", extra=""):
    names = (
        "l3_gateCallback",
        "l3_gateBoundary",
        "l3_gateBeginCapture",
        "l3_gateEndCapture",
        "l3_cli_gateCfg",
        "l3_requestHwaPostFrames",
        "l3_hwaMaybeQueueRearm",
        "l3_freezeHwaAfterPostFrames",
        "l3_freezeHwaForShutdown",
        "l3_finishCaptureStop",
        "l3_stopCaptureAtBoundary",
        "l3_stopCaptureForShutdown",
        "l3_cli_sensorStop",
    )
    return prefix + HARNESS + extra + "\n".join(function_source(name) for name in names)


def test_c_gate_layout_matches_the_python_decoder(tmp_path, c_toolchain):
    from openflight.iwr6843.dump import GATE_EVIDENCE

    raw = run_c(
        tmp_path,
        c_toolchain,
        r"""
#include <stdio.h>
#include "l3_gate.h"
int main(void) {
    l3_gate_state_t gate = {0};
    l3_gateArm(&gate);
    l3_gateLatch(&gate, 0xFFFFFF00U, 101U, 1U);
    l3_gateComplete(&gate, 100U);
    return fwrite(&gate.evidence, sizeof(gate.evidence), 1, stdout) != 1;
}
""",
    )
    assert GATE_EVIDENCE.unpack(raw) == (b"ILG1", 1, 1, 0xFFFFFF00, 101, 100, 0, 3)


def test_legacy_fixed_ring_request_still_compiles_without_gpio(tmp_path, c_toolchain):
    source = r"""
#include <assert.h>
#include <stdint.h>
#define HWA_POST_TRIGGER_FRAMES 8U
static uint32_t gHwaFreezeRequested, gHwaFreezeRequestFrame, gHwaFreezeTargetFrame;
static uint32_t gHwaFreezeRequests, gRingFrame = 40U;
"""
    run_c(
        tmp_path,
        c_toolchain,
        source
        + function_source("l3_requestHwaPostFrames")
        + r"""
int main(void) {
    l3_requestHwaPostFrames();
    assert(gHwaFreezeRequested && gHwaFreezeRequests == 1);
    assert(gHwaFreezeRequestFrame == 40 && gHwaFreezeTargetFrame == 48);
    return 0;
}
""",
    )


@pytest.mark.parametrize("edma", [False, True], ids=["cpu-pack", "edma-pack"])
def test_iq8_freeze_acknowledges_only_after_final_pack(tmp_path, c_toolchain, edma):
    prefix = r"""
#include <setjmp.h>
#define TEST_IQ8 1
#define L3_RING_IQ8 1
#define BIOS_WAIT_FOREVER 0xFFFFFFFFU
static jmp_buf workerExit;
"""
    if edma:
        prefix += "#define L3_IQ8_EDMA_PACK 1\n"
    extra = r"""
typedef unsigned long UArg;
static uint8_t gHwaRearmBusy, gIq8Pending, gIq8PendingScratch, gIq8ActiveScratch;
static uint32_t gIq8PendingSlot, gHwaRearms, gHwaRearmErrors;
static unsigned packed, packComplete;
static int l3_captureUsesIq8(void) { return 1; }
static int l3_restartCompletedHwaFrame(void) { assert(0); return 0; }
static void l3_perfNoteRearmDone(uint32_t tick) { (void)tick; }
static void l3_perfNoteTaskDone(uint32_t tick) { (void)tick; }
static void l3_gateCallback(uint32_t index);
static void l3_packIq8CompletedFrame(uint32_t slot, uint8_t scratch) {
    assert(slot == 7 && scratch == 1 && gGate.evidence.flags == 1);
    packed++; packComplete = 1;
}
static void l3_startIq8PackAndScan(uint32_t slot, uint8_t scratch) {
    assert(slot == 7 && scratch == 1 && gGate.evidence.flags == 1);
    packed++;
}
static void l3_waitForAllIq8Edma(void) {
    assert(packed == 1 && !packComplete && gGate.evidence.flags == 1);
    l3_gateCallback(28);
    packComplete = 1;
}
static void l3_waitForIq8EdmaScratch(uint8_t scratch) { (void)scratch; assert(0); }
"""
    main = r"""
int main(void) {
    l3_gateBeginCapture();
    l3_gateCallback(28);
    gActiveFrameIsPost = 1;
    gPostFramesCaptured = 2;
    gIq8Pending = 1; gIq8PendingSlot = 7; gIq8PendingScratch = 1;
    l3_hwaMaybeQueueRearm();
    assert(rearmSem == 1 && freezeSem == 0 && gGate.evidence.flags == 1);
    if (!setjmp(workerExit)) l3_hwaRearmTask(0, 0);
    assert(packComplete && packed == 1 && freezeSem == 1);
    assert(gGate.evidence.flags == 3 && !gCaptureActive);
    assert(gHwaFreezeRequests == 1 && !gHwaRearmBusy);
    assert(l3_stopCaptureAtBoundary() == 0);
    assert(stops == 1 && !gGate.armed);
    return 0;
}
"""
    run_c(
        tmp_path,
        c_toolchain,
        integrated_source(prefix, extra) + function_source("l3_hwaRearmTask") + main,
    )


def test_failed_rf_stop_can_be_retried_without_losing_the_latch(tmp_path, c_toolchain):
    run_c(
        tmp_path,
        c_toolchain,
        integrated_source()
        + r"""
int main(void) {
    l3_gateBeginCapture();
    l3_gateCallback(28);
    gActiveFrameIsPost = 1;
    gPostFramesCaptured = 2;
    l3_hwaMaybeQueueRearm();
    stopFails = 1;
    assert(l3_stopCaptureAtBoundary() == -1);
    assert(gGate.armed && gGate.evidence.flags == 3);
    stopFails = 0;
    assert(l3_cli_sensorStop(0, NULL) == 0);
    assert(!gGate.armed && gGate.evidence.flags == 0 && stops == 2);
    return 0;
}
""",
    )


def test_disabled_gate_mode_still_uses_serial_freezing(tmp_path, c_toolchain):
    run_c(
        tmp_path,
        c_toolchain,
        integrated_source()
        + r"""
int main(void) {
    gGateEnabled = 0;
    finishOnWait = 1;
    assert(l3_stopCaptureAtBoundary() == 0);
    assert(gHwaFreezeRequests == 1 && stops == 1 && gGate.evidence.flags == 0);
    return 0;
}
""",
    )


def test_delayed_readback_does_not_refreeze_or_consume_a_later_edge(tmp_path, c_toolchain):
    run_c(
        tmp_path,
        c_toolchain,
        integrated_source()
        + r"""
int main(void) {
    char *enable[] = {"gateCfg", "1"};
    gCaptureActive = 0;
    assert(l3_cli_gateCfg(2, enable) == 0);
    gCaptureActive = 1;
    l3_gateBeginCapture();
    assert(gpioEnabled && gGate.armed);
    l3_gateCallback(28);
    assert(gGate.evidence.edge_tick == 1000);
    assert(gHwaFreezeRequests == 1 && gHwaFreezeRequestFrame == 40);
    l3_gateCallback(28);
    assert(gHwaFreezeRequests == 1 && gGate.evidence.ignored_edges == 1);
    gActiveFrameIsPost = 1;
    gPostFramesCaptured = 2;
    l3_hwaMaybeQueueRearm();
    assert(!gCaptureActive && freezeSem == 1);
    assert(gGate.evidence.flags == 3);
    assert(l3_stopCaptureAtBoundary() == 0);
    assert(stops == 1 && waitCalls == 0 && gHwaFreezeRequests == 1);
    assert(!gpioEnabled && !gGate.armed);
    gCaptureActive = 1;
    l3_gateBeginCapture();
    assert(freezeSem == 0 && gGate.evidence.flags == 0);
    assert(l3_freezeHwaAfterPostFrames() == -1);
    assert(gHwaFreezeRequests == 1);
    return 0;
}
""",
    )


def test_early_readback_waits_for_original_freeze_and_timeout_preserves_it(tmp_path, c_toolchain):
    run_c(
        tmp_path,
        c_toolchain,
        integrated_source()
        + r"""
int main(void) {
    l3_gateBeginCapture();
    l3_gateCallback(28);
    assert(l3_freezeHwaAfterPostFrames() == -1);
    assert(gHwaFreezeRequested && gHwaFreezeRequests == 1);
    assert(gHwaFreezeTimeouts == 1 && gGate.evidence.flags == 1);
    finishOnWait = 1;
    assert(l3_stopCaptureAtBoundary() == 0);
    assert(stops == 1 && gGate.evidence.flags == 3);
    assert(gHwaFreezeRequests == 1 && waitCalls == 2);
    return 0;
}
""",
    )


def test_sensor_stop_releases_a_frozen_capture_without_waiting_again(tmp_path, c_toolchain):
    run_c(
        tmp_path,
        c_toolchain,
        integrated_source()
        + r"""
int main(void) {
    char *disable[] = {"gateCfg", "0"};
    l3_gateBeginCapture();
    l3_gateCallback(28);
    assert(l3_cli_gateCfg(2, disable) == -1);
    gActiveFrameIsPost = 1;
    gPostFramesCaptured = 2;
    l3_hwaMaybeQueueRearm();
    assert(l3_cli_sensorStop(0, NULL) == 0);
    assert(stops == 1 && !gGate.armed && gGate.evidence.flags == 0 && waitCalls == 0);
    assert(l3_cli_gateCfg(2, disable) == 0);
    assert(!gGateEnabled);
    return 0;
}
""",
    )


def test_stale_gate_cannot_be_read_as_a_later_shot_after_pmu_wrap(tmp_path, c_toolchain):
    run_c(
        tmp_path,
        c_toolchain,
        integrated_source()
        + r"""
int main(void) {
    l3_gateBeginCapture();
    l3_gateCallback(28);
    gActiveFrameIsPost = 1;
    gPostFramesCaptured = 2;
    l3_hwaMaybeQueueRearm();
    rtosNow += 22000;
    assert(l3_stopCaptureAtBoundary() == -1);
    assert(stops == 0 && gHwaFreezeRequests == 1);
    assert(l3_cli_sensorStop(0, NULL) == 0);
    assert(stops == 1);
    gCaptureActive = 1;
    l3_gateBeginCapture();
    assert(gGate.evidence.flags == 0);
    return 0;
}
""",
    )


def test_first_edge_survives_ringing_busy_edges_and_counter_wrap(tmp_path, c_toolchain):
    run_c(
        tmp_path,
        c_toolchain,
        r"""
#include <assert.h>
#include <stdint.h>
#include "l3_gate.h"
int main(void) {
    l3_gate_state_t gate = {0};
    assert(l3_gateFresh(250, 1000));
    assert(!l3_gateFresh(251, 1000));
    assert(!l3_gateFresh(0, 0));
    assert(!l3_gateFresh(UINT32_MAX, UINT32_MAX));
    assert(sizeof(l3_gate_evidence_t) == 32);
    assert(!l3_gateLatch(&gate, 10, 1, 0));
    l3_gateArm(&gate);
    assert(!l3_gateLatch(&gate, 11, 1, 0));
    gate.raw_seq = UINT32_MAX;
    assert(l3_gateLatch(&gate, UINT32_MAX - 10, 400, 1));
    assert(gate.evidence.edge_seq == 0);
    assert(!l3_gateLatch(&gate, 5, 401, 1));
    l3_gateComplete(&gate, 100);
    assert(!l3_gateLatch(&gate, 200, 402, 0));
    assert(gate.evidence.edge_tick == UINT32_MAX - 10);
    assert(gate.evidence.frame_seq == 400);
    assert(gate.evidence.freeze_tick == 100);
    assert(gate.evidence.ignored_edges == 3);
    assert(gate.evidence.flags == (L3_GATE_FLAG_LATCHED | L3_GATE_FLAG_FROZEN));
    l3_gateDisarm(&gate);
    assert(!l3_gateLatch(&gate, 300, 403, 1));
    l3_gateArm(&gate);
    assert(gate.evidence.arm_seq == 2);
    assert(gate.evidence.flags == 0);
    assert(gate.evidence.ignored_edges == 0);
    assert(l3_gateLatch(&gate, 0, 404, 1));
    assert(gate.evidence.edge_tick == 0);
    assert(gate.evidence.edge_seq == 4);
    return 0;
}
""",
    )


def test_unlatched_shutdown_does_not_manufacture_a_gate_and_counts_saturate(tmp_path, c_toolchain):
    run_c(
        tmp_path,
        c_toolchain,
        r"""
#include <assert.h>
#include <stdint.h>
#include "l3_gate.h"
int main(void) {
    l3_gate_state_t gate = {0};
    l3_gateArm(&gate);
    l3_gateComplete(&gate, 123);
    assert(gate.evidence.flags == 0);
    gate.evidence.ignored_edges = UINT32_MAX;
    assert(!l3_gateLatch(&gate, 123, 5, 0));
    assert(gate.evidence.ignored_edges == UINT32_MAX);
    l3_gateDisarm(&gate);
    assert(!l3_gateLatch(&gate, 123, 5, 1));
    return 0;
}
""",
    )
