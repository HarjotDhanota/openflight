"""Regression checks for the HWA snapshot ring freeze/rearm sequence."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

FIRMWARE = Path(__file__).parents[1] / "firmware" / "iwr6843" / "l3_dump.c"
FIRMWARE_MAKEFILE = Path(__file__).parents[1] / "firmware" / "Makefile"
CONFIG_DIR = Path(__file__).parents[1] / "config"
WIDE_CONFIG = CONFIG_DIR / "iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"
DENSE_CONFIG = CONFIG_DIR / "iwr6843_l3dump_dense_36f2ms_53bin_iq8.cfg"
DENSE_WIDE_LATE_CONFIG = CONFIG_DIR / "iwr6843_l3dump_dense_36f2ms_53bin_iq8_wide_late.cfg"


def _function_source(source: str, name: str, next_name: str) -> str:
    start = source.rindex(name)
    end = source.index(next_name, start)
    return source[start:end]


def test_hwa_dump_stops_at_boundary_before_streaming():
    source = FIRMWARE.read_text(encoding="utf-8")
    dump = _function_source(source, "int32_t l3_cli_dump", "static int32_t l3_cli_stats")

    stop = dump.index("l3_stopCaptureAtBoundary")
    stream = dump.index("UART_writePolling")

    assert stop < stream


def test_hwa_chain_processes_and_rearms_one_frame_at_a_time():
    source = FIRMWARE.read_text(encoding="utf-8")
    common = _function_source(source, "static int32_t l3_configHwaCommon", "static void l3_drain")
    output = _function_source(
        source,
        "static int32_t l3_configHwaOutputEdma",
        "static int32_t l3_configHwaSignatureEdma",
    )

    assert "gCapturePlan.chirpsPerFrame / 2U" in common
    assert "gCapturePlan.chirpsPerFrame / 2U" in output


def test_completed_frame_advances_circular_ring_slot():
    source = FIRMWARE.read_text(encoding="utf-8")
    callback = _function_source(
        source,
        "static void l3_hwaOutputDoneCB",
        "static int32_t l3_hwaStartRing",
    )

    assert "gRingFrame++" in callback
    assert "gRingFrame % RING_FRAMES" in callback


def test_freeze_request_keeps_rearming_until_post_trigger_target():
    source = FIRMWARE.read_text(encoding="utf-8")
    queue = _function_source(
        source, "static void l3_hwaMaybeQueueRearm", "static void l3_hwaChainDoneCB"
    )
    freeze = _function_source(
        source,
        "static int32_t l3_freezeHwaAfterPostFrames",
        "static int32_t l3_armHwaChain",
    )

    assert "gHwaFreezeRequested" in queue
    assert "gRingFrame >= gHwaFreezeTargetFrame" in queue
    assert "Semaphore_post(gHwaFreezeSemaphore)" in queue
    assert "gHwaFreezeTargetFrame = gRingFrame + HWA_POST_TRIGGER_FRAMES" in freeze


def test_sensor_stop_cancels_post_capture_at_next_completed_frame():
    source = FIRMWARE.read_text(encoding="utf-8")
    shutdown_freeze = _function_source(
        source,
        "static int32_t l3_freezeHwaForShutdown",
        "static int32_t l3_finishCaptureStop",
    )
    shutdown_stop = _function_source(
        source,
        "static int32_t l3_stopCaptureForShutdown",
        "static int32_t l3_armHwaChain",
    )
    sensor_stop = _function_source(
        source,
        "static int32_t l3_cli_sensorStop",
        "static void l3_initTask",
    )

    assert "gHwaShutdownRequested = 1U" in shutdown_freeze
    assert "gHwaFreezeRequested = 0U" in shutdown_freeze
    assert "gPostFramesCaptured = 0U" not in shutdown_freeze
    assert "gCapturePlan.postFrames" not in shutdown_freeze
    assert "l3_hwaMaybeQueueRearm()" in shutdown_freeze
    assert "l3_freezeHwaForShutdown" in shutdown_stop
    assert "l3_finishCaptureStop" in shutdown_stop
    assert "if (!gCaptureActive)" in sensor_stop
    assert "return l3_stopCaptureForShutdown()" in sensor_stop


def test_shutdown_waits_for_iq8_pack_without_rearming():
    source = FIRMWARE.read_text(encoding="utf-8")
    rearm = _function_source(
        source,
        "static void l3_hwaRearmTask",
        "/* Fill the 20-byte fixed dump header",
    )

    shutdown = rearm.index("gHwaShutdownRequested")
    pack = rearm.index("l3_waitForAllIq8Edma", shutdown)
    completion = rearm.index("Semaphore_post(gHwaFreezeSemaphore)", shutdown)
    restart = rearm.index("l3_restartCompletedHwaFrame", shutdown)

    assert shutdown < pack < completion < restart


def test_iq8_edma_pack_compacts_int16_scratch_without_cpu_loop():
    source = FIRMWARE.read_text(encoding="utf-8")
    pack = _function_source(
        source,
        "static int32_t l3_startIq8EdmaPack",
        "static void l3_waitForIq8EdmaScratch",
    )
    rearm = _function_source(
        source,
        "static void l3_hwaRearmTask",
        "/* Fill the 20-byte fixed dump header",
    )

    assert "param->aCount = 1U" in pack
    assert "param->bCount = (uint16_t)components" in pack
    assert "param->sourceBindex = (int16_t)sizeof(int16_t)" in pack
    assert "param->destinationBindex = 1" in pack
    assert "EDMA_startDmaTransfer" in pack
    assert "l3_restartCompletedHwaFrame" in rearm
    # Both rearm sites start the EDMA pack through one helper that also runs the IQ8 scan.
    assert rearm.count("l3_startIq8PackAndScan(pendingSlot, pendingScratch);") == 2
    assert "if (l3_startIq8EdmaPack(slot, scratch) != 0) {" in _function_source(
        source,
        "static void l3_startIq8PackAndScan",
        "static uint32_t l3_snapshotBinStartForNextFrame",
    )
    assert rearm.count("l3_packIq8CompletedFrame") == 2
    assert rearm.count("#else\n                    l3_packIq8CompletedFrame") == 1
    assert rearm.count("#else\n                l3_packIq8CompletedFrame") == 1


def test_iq8_edma_pack_waits_before_reusing_ping_pong_scratch():
    source = FIRMWARE.read_text(encoding="utf-8")
    rearm = _function_source(
        source,
        "static void l3_hwaRearmTask",
        "/* Fill the 20-byte fixed dump header",
    )

    assert "l3_waitForIq8EdmaScratch(nextScratch)" in rearm
    assert "l3_waitForAllIq8Edma()" in rearm


def test_dump_header_rotates_from_oldest_completed_frame():
    source = FIRMWARE.read_text(encoding="utf-8")
    dump = _function_source(source, "int32_t l3_cli_dump", "static int32_t l3_cli_stats")

    assert "gRingFrame % RING_FRAMES" in dump


def test_production_build_uses_configurable_compression_and_single_release():
    source = FIRMWARE_MAKEFILE.read_text(encoding="utf-8")
    target = _function_source(source, "build-native:", "clean:")

    assert "--define=N_TX=3" in target
    assert "--define=CONFIGURABLE_CAPTURE=1" in target
    assert "--define=HYBRID_CADENCE_CAPTURE=1" in target
    assert "--define=L3_RING_IQ8=1" in target
    assert "--define=L3_IQ8_EDMA_PACK=1" in target
    assert "--define=L3_IQ8_SPARSE_SCALE=1" not in target
    assert "--define=LOOPS=" not in target
    assert "--define=RING_FRAMES=" not in target
    assert "RELEASE_NAME ?= l3_dump_local_build.bin" in source
    assert '"$(RELEASE_DIR)/$(RELEASE_NAME)"' in target
    assert source.count("\nbuild-native:") == 1


ROLLBACK_IMAGE = "l3_dump_configurable_capture_20260818.bin"


def _release_guard(*variables: str) -> subprocess.CompletedProcess:
    """Run only the Makefile's release-target check (no TI tools, nothing is built)."""
    make = shutil.which("make")
    if make is None:
        pytest.skip("no make to run the firmware Makefile's release guard")
    return subprocess.run(
        [make, "-s", "-C", str(FIRMWARE_MAKEFILE.parent), "check-release-target", *variables],
        capture_output=True,
        text=True,
        check=False,
    )


def _default_release_name() -> str:
    source = FIRMWARE_MAKEFILE.read_text(encoding="utf-8")
    return re.search(r"^RELEASE_NAME \?= (\S+)$", source, re.MULTILINE).group(1)


def _git(*args: str) -> subprocess.CompletedProcess:
    """A git query in this checkout; skips where the tree is not a git checkout."""
    git = shutil.which("git")
    repo = FIRMWARE_MAKEFILE.parents[1]
    if git is None or not (repo / ".git").exists():
        pytest.skip("needs a git checkout")
    return subprocess.run(
        [git, *args], cwd=repo, capture_output=True, text=True, check=False, timeout=30
    )


def test_the_default_release_name_is_not_a_release():
    """A bare build names a scratch file, never a dated release or the rollback image."""
    default = _default_release_name()

    assert default != ROLLBACK_IMAGE
    assert not re.fullmatch(r"l3_dump_\w+_\d{8}\.bin", default)  # never a dated release name
    assert _git("ls-files", "--error-unmatch", f"firmware/releases/{default}").returncode != 0


def test_a_bare_builds_image_is_ignored_and_the_rollback_image_is_not():
    """A bare local build leaves no untracked file; the tracked rollback image is never ignored."""
    default = _default_release_name()

    assert _git("check-ignore", "-q", "--no-index", f"firmware/releases/{default}").returncode == 0
    rollback = f"firmware/releases/{ROLLBACK_IMAGE}"
    assert _git("check-ignore", "-q", "--no-index", rollback).returncode == 1
    assert _git("ls-files", "--error-unmatch", rollback).returncode == 0


def test_the_rollback_image_is_never_overwritten_by_name():
    """Naming the rollback image stops the build before anything runs."""
    result = _release_guard(f"RELEASE_NAME={ROLLBACK_IMAGE}")

    assert result.returncode != 0
    assert "refusing to overwrite" in result.stderr
    assert (FIRMWARE_MAKEFILE.parent / "releases" / ROLLBACK_IMAGE).exists()


def test_an_existing_release_is_refused_and_left_untouched(tmp_path):
    """Any existing file in the release directory is refused and kept byte for byte."""
    (tmp_path / "taken.bin").write_bytes(b"old image")

    result = _release_guard(f"RELEASE_DIR={tmp_path}", "RELEASE_NAME=taken.bin")

    assert result.returncode != 0
    assert "OVERWRITE_RELEASE=1" in result.stderr
    assert (tmp_path / "taken.bin").read_bytes() == b"old image"


def test_overwrite_release_lets_a_deliberate_rebuild_through(tmp_path):
    """OVERWRITE_RELEASE=1 is the one explicit way to replace a release."""
    (tmp_path / "taken.bin").write_bytes(b"old image")

    result = _release_guard(
        f"RELEASE_DIR={tmp_path}", "RELEASE_NAME=taken.bin", "OVERWRITE_RELEASE=1"
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("override", ["", "OVERWRITE_RELEASE=0", "OVERWRITE_RELEASE=yes"])
def test_only_overwrite_release_1_unlocks_an_existing_release(tmp_path, override):
    """Unset, 0 or any other value keeps the existing release."""
    (tmp_path / "taken.bin").write_bytes(b"old image")
    variables = [f"RELEASE_DIR={tmp_path}", "RELEASE_NAME=taken.bin"]

    result = _release_guard(*variables, *([override] if override else []))

    assert result.returncode != 0


def test_a_new_release_name_passes_the_guard(tmp_path):
    """A release name that is not taken yet passes."""
    result = _release_guard(f"RELEASE_DIR={tmp_path}", "RELEASE_NAME=l3_dump_new_20261011.bin")

    assert result.returncode == 0, result.stderr


def test_every_release_writer_checks_the_target_before_building():
    """Both build targets run the guard first; docker forwards the override."""
    source = FIRMWARE_MAKEFILE.read_text(encoding="utf-8")
    native = re.search(r"^build-native:(.*)$", source, re.MULTILINE).group(1).split()
    docker = re.search(r"^docker-build:(.*)$", source, re.MULTILINE).group(1).split()
    docker_recipe = _function_source(source, "docker-build:", "docker-shell:")

    assert native[0] == "check-release-target"  # before the toolchain check and the build
    assert docker[0] == "check-release-target"  # on the host, before the container runs
    assert 'OVERWRITE_RELEASE="$(OVERWRITE_RELEASE)"' in docker_recipe
    assert source.count('cp "$(FIRMWARE_DIR)/l3_dump.bin"') == 1
    assert "check-release-target" in re.search(r"^\.PHONY:(.*)$", source, re.MULTILINE).group(1)


def _config_lines(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("%")
    }


def test_wide_profile_uses_24_frames_at_3ms_with_53_bin_iq16_windows():
    lines = _config_lines(WIDE_CONFIG)

    assert "frameCfg 0 2 12 0 3 1 0" in lines
    assert "captureFormat iq16" in lines
    assert "phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 1" in lines


def test_dense_profile_uses_36_frames_at_2ms_with_53_bin_iq8_windows():
    lines = _config_lines(DENSE_CONFIG)

    assert "frameCfg 0 2 12 0 2 1 0" in lines
    assert "captureFormat iq8" in lines
    assert "phaseCaptureCfg 20 53 14 32 53 10 47 53 64 12 1" in lines


def test_dense_wide_late_profile_keeps_dense_timing_and_near_late_window():
    lines = _config_lines(DENSE_WIDE_LATE_CONFIG)

    assert "frameCfg 0 2 12 0 2 1 0" in lines
    assert "captureFormat iq8" in lines
    assert "iq8Scale 128" in lines
    assert "phaseCaptureCfg 20 53 14 32 53 10 47 53 47 12 1" in lines


def test_supported_profiles_keep_the_same_72ms_movie():
    for path, expected_frames, expected_period_ms in (
        (WIDE_CONFIG, 24, 3.0),
        (DENSE_CONFIG, 36, 2.0),
        (DENSE_WIDE_LATE_CONFIG, 36, 2.0),
    ):
        commands = {line.split()[0]: line.split() for line in _config_lines(path)}
        frame = commands["frameCfg"]
        phase = commands["phaseCaptureCfg"]
        assert sum(int(value) for value in (phase[3], phase[6], phase[10])) == expected_frames
        assert float(frame[5]) == expected_period_ms
        assert expected_frames * expected_period_ms == 72.0


def test_supported_profiles_fit_the_l3_capture_budget():
    tx, loops, rx = 3, 12, 4
    wide_bytes = tx * loops * rx * 24 * 53 * 4
    dense_bytes = tx * loops * rx * 36 * 53 * 2

    assert wide_bytes == 732_672
    assert dense_bytes == 549_504
    assert wide_bytes < 786_432
    assert dense_bytes < 688_128


def test_dynamic_window_start_is_recorded_per_ring_slot():
    source = FIRMWARE.read_text(encoding="utf-8")
    output = _function_source(
        source,
        "static int32_t l3_configHwaFrameOutput",
        "static void l3_drainHwaRearmSemaphore",
    )
    dump = _function_source(source, "int32_t l3_cli_dump", "static int32_t l3_cli_stats")

    assert "gFrameBinStart[ringSlot % RING_FRAMES]" in output
    assert "UART_writePolling(gDataUart, gFrameBinStart" in dump
