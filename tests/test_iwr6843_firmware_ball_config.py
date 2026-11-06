"""Run the actual CLI/reset C with deterministic task and interrupt observations."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from openflight.iwr6843 import firmware_host as fw

FIRMWARE = Path(__file__).resolve().parents[1] / "firmware" / "iwr6843"


def _function(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def _observed(source: str) -> str:
    # These extracted functions contain no for statements or literal semicolons.
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return source.replace(";", "; checkpoint();")


def _linux_path(path: Path) -> str:
    path = path.resolve()
    return f"/mnt/{path.drive[0].lower()}/{path.as_posix()[3:]}"


HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <math.h>
#include "l3_ball.h"
#define L3_HSRAM_DIAG
#define L3_BALL_FRAMES_PER_UPDATE 8U
#define L3_TRIGGER_CFG_WAIT_MS 6U
#define L3_RING_MAX_BINS 64U
typedef unsigned int UInt;
typedef struct { unsigned dummy; } l3_angle_obs_t;
static l3_ball_cfg_t gBallCfg;
static l3_ball_t gBall;
static volatile uint8_t gBallBusy;
static l3_ball_angle_clock_t gBallAngleClock;
static uint8_t gTrigDestBall, gBallAngleValid;
static uint32_t gTrigFallbackFrames;
static l3_angle_obs_t gBallAngle;
typedef struct {
    l3_ball_cfg_t cfg;
    l3_ball_t ball;
    l3_ball_angle_clock_t clock;
    uint32_t fallback;
    uint8_t dest, angleValid;
} snapshot_t;
static snapshot_t before, after;
static unsigned mode, enabled, follow, masked, active, sleeps;
static unsigned mixed, observed, protectedSteps, initMasked, ioMasked;
static unsigned done, error, rearm, control, interrupts, detect;
static void snapshot(snapshot_t *out) {
    memset(out, 0, sizeof(*out));
    out->cfg = gBallCfg; out->ball = gBall; out->clock = gBallAngleClock;
    out->dest = gTrigDestBall; out->fallback = gTrigFallbackFrames;
    out->angleValid = gBallAngleValid;
}
static void checkpoint(void) {
    snapshot_t current;
    if (!active) return;
    if (masked) { protectedSteps++; return; }
    snapshot(&current);
    observed++; detect++; rearm++; control++; interrupts++;
    if (memcmp(&current, &before, sizeof(current)) &&
        memcmp(&current, &after, sizeof(current))) mixed++;
}
static uintptr_t Hwi_disable(void) {
    unsigned previous = masked;
    if (mode == 3U) gBallBusy = 1U;
    checkpoint();
    masked = 1U;
    return previous;
}
static void Hwi_restore(uintptr_t previous) {
    masked = (unsigned)previous;
    checkpoint();
}
static void Task_sleep(unsigned ticks) {
    (void)ticks;
    sleeps++;
    if (mode == 2U && sleeps == 3U) gBallBusy = 0U;
    checkpoint();
}
static void CLI_write(const char *format, ...) {
    if (masked) ioMasked++;
    if (!strcmp(format, "Done\n")) done++;
    if (!strncmp(format, "Error:", 6)) error++;
}
static int32_t l3_ballScan(uint32_t first, uint32_t count) {
    (void)first; (void)count; return 0;
}
static int32_t l3_angle_format(const l3_angle_obs_t *angle, char *out, uint32_t cap) {
    (void)angle; (void)out; (void)cap; return 0;
}
static void *observed_memset(void *dest, int value, size_t count) {
    unsigned char *bytes = dest;
    size_t index;
    for (index = 0; index < count; index++) {
        bytes[index] = (unsigned char)value;
        checkpoint();
    }
    return dest;
}
/* INIT */
/* CLI */
int main(int argc, char *argv[]) {
    int result;
    snapshot_t final;
    char *command[] = {"ball", "cfg", "1", "1", "2.5", "5", "7"};
    if (argc != 4) return 2;
    mode = (unsigned)atoi(argv[1]);
    enabled = (unsigned)atoi(argv[2]); follow = (unsigned)atoi(argv[3]);
    command[2] = enabled ? "1" : "0";
    command[3] = follow ? "1" : "0";
    if (mode == 4U) command[4] = "nan";
    l3_ball_cfg_defaults_at(&gBallCfg, 2U);
    gBallCfg.enabled = 1U;
    l3_ball_init(&gBall, &gBallCfg);
    gBall.state = L3_BALL_STATE_LOCKED; gBall.ballBin = 29U;
    gBall.updates = 99U; gBall.background[0] = 91.0F;
    gBall.current[0] = 103.0F; gBall.locks = 4U;
    gBallAngleClock.valid = 1U; gBallAngleClock.bin = 29U;
    gBallAngleClock.age = 3U;
    gTrigDestBall = 1U; gTrigFallbackFrames = 35U; gBallAngleValid = 1U;
    snapshot(&before);
    memset(&after, 0, sizeof(after));
    l3_ball_cfg_defaults_at(&after.cfg, L3_BALL_FRAMES_PER_UPDATE);
    after.cfg.enabled = (uint8_t)enabled; after.cfg.follow = (uint8_t)follow;
    after.cfg.minRatio = 2.5F; after.cfg.stableUpdates = 5U; after.cfg.buildUpdates = 7U;
    l3_ball_init(&after.ball, &after.cfg);
    after.angleValid = before.angleValid;
    gBallBusy = (uint8_t)(mode == 1U || mode == 2U);
    active = 1U;
    result = l3_cli_ball(7, command);
    snapshot(&final);
    printf("{\"result\":%d,\"mixed\":%u,\"old\":%d,\"new\":%d,"
           "\"sleeps\":%u,\"masked\":%u,\"steps\":%u,\"init_masked\":%u,"
           "\"io_masked\":%u,\"done\":%u,\"error\":%u,\"detect\":%u,"
           "\"rearm\":%u,\"control\":%u,\"interrupts\":%u,\"observed\":%u}\n",
           result, mixed, !memcmp(&final, &before, sizeof(final)),
           !memcmp(&final, &after, sizeof(final)), sleeps, masked, protectedSteps,
           initMasked, ioMasked, done, error, detect, rearm, control, interrupts, observed);
    return 0;
}
"""


@pytest.fixture(scope="module", name="command")
def compiled_command(tmp_path_factory):
    """Compile original algorithm plus extracted, instrumented production bodies."""
    cli = _function((FIRMWARE / "l3_dump.c").read_text(), "static int32_t l3_cli_ball(")
    init = _function((FIRMWARE / "l3_ball.c").read_text(), "void l3_ball_init(")
    rate = _function((FIRMWARE / "l3_ball.c").read_text(), "static float l3_ball_rate(")
    init = init.replace("l3_ball_init(", "observed_init(", 1)
    init = init.replace("{", "{ if (masked) initMasked++;", 1)
    init = init.replace("memset(", "observed_memset(")
    cli = cli.replace("l3_ball_init(", "observed_init(")
    cli = cli.replace("memset(", "observed_memset(")
    source = HARNESS.replace("/* INIT */", rate + "\n" + _observed(init))
    source = source.replace("/* CLI */", _observed(cli))
    directory = tmp_path_factory.mktemp("ball_config")
    harness = directory / "ball_config.c"
    harness.write_text(source)
    executable = directory / ("ball_config.exe" if os.name == "nt" else "ball_config")
    compiler = fw.host_compiler_command()
    prefix = []
    convert = str
    wsl = os.environ.get("OPENFLIGHT_TEST_WSL_DISTRO") if compiler is None else None
    if wsl and os.name == "nt":
        prefix = ["wsl", "-d", wsl, "--exec"]
        compiler = [*prefix, "gcc"]
        convert = _linux_path
    if compiler is None:
        pytest.skip("no local C compiler")
    subprocess.run(
        [
            *compiler,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-unused-function",
            "-O0",
            "-I",
            convert(FIRMWARE),
            convert(harness),
            convert(FIRMWARE / "l3_ball.c"),
            "-lm",
            "-o",
            convert(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return [*prefix, convert(executable)]


def _run(command, mode=0, enabled=1, follow=1):
    result = subprocess.run(
        [*command, str(mode), str(enabled), str(follow)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(("enabled", "follow"), [(1, 1), (0, 0)])
def test_reset_is_atomic_to_detect_rearm_control_and_interrupts(command, enabled, follow):
    """Readers see only a complete old or new state, including disable resets."""
    result = _run(command, enabled=enabled, follow=follow)
    assert result["mixed"] == 0, result
    assert result["new"] == 1 and result["result"] == 0, result
    assert result["done"] == 1 and result["error"] == 0, result
    assert result["init_masked"] == result["io_masked"] == result["masked"] == 0
    assert 0 < result["steps"] < 40, result
    assert min(result[key] for key in ("detect", "rearm", "control", "interrupts")) > 20


@pytest.mark.parametrize("mode", [1, 3])
def test_busy_timeout_or_last_boundary_refuses_without_mutation(command, mode):
    """Refuse both an expired wait and a reader arriving before ownership."""
    result = _run(command, mode=mode)
    assert result["result"] == -1 and result["old"] == 1, result
    assert result["done"] == 0 and result["error"] == 1, result
    assert result["mixed"] == result["masked"] == result["io_masked"] == 0
    assert result["sleeps"] == (6 if mode == 1 else 0), result


def test_inflight_update_can_finish_before_bounded_reset(command):
    """A busy detector that finishes within the wait permits reset."""
    result = _run(command, mode=2)
    assert result["sleeps"] == 3 and result["new"] == 1, result
    assert result["mixed"] == 0 and result["result"] == 0, result


def test_invalid_configuration_preserves_all_shared_fields(command):
    """Validation refuses before acquiring ownership or changing metadata."""
    result = _run(command, mode=4)
    assert result["result"] == -1 and result["old"] == 1, result
    assert result["mixed"] == result["sleeps"] == result["steps"] == 0
