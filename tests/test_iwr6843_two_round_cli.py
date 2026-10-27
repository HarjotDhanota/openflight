"""Exercise actual CLI integration with fake RF, UART and scheduler services."""

from __future__ import annotations

import subprocess

import pytest

from tests.test_iwr6843_two_round_firmware import build_native_probe


@pytest.mark.parametrize(
    "stage,clock,evidence,iq8",
    [
        (False, False, True, True),
        (False, True, True, True),
        (True, False, True, True),
        (True, True, True, True),
        (True, False, False, False),
        (True, True, False, False),
    ],
)
@pytest.mark.parametrize("skip_temperature", [False, True])
def test_registration_and_frozen_session_lifecycle(
    tmp_path, stage, clock, evidence, iq8, skip_temperature
):
    clock_stub = tmp_path / "ti/sysbios/knl/Clock.h"
    clock_stub.parent.mkdir(parents=True)
    clock_stub.write_text("/* The harness supplies the scheduler stand-ins. */\n", encoding="utf-8")
    (tmp_path / "clock_probe_build_id.h").write_text(
        '#define L3_USB_CLOCK_BUILD_ID "native-test-only"\n', encoding="utf-8"
    )
    flags = [
        "-DCONFIGURABLE_CAPTURE=1",
        "-DHWA_CHAINED_SNAPSHOT_RING=1",
        "-DENABLE_HWA_SMOKE=1",
        "-DHYBRID_CADENCE_CAPTURE=1",
    ]
    if stage:
        flags += ["-DL3_TWO_ROUND=1"]
    if clock:
        flags += ["-DL3_USB_CLOCK_PROBE=1", "-DL3_USB_CLOCK_COMPACT=1"]
    if evidence:
        flags += ["-DL3_CAPTURE_EVIDENCE=1"]
    if iq8:
        flags += ["-DL3_RING_IQ8=1", "-DL3_IQ8_EDMA_PACK=1"]
    if skip_temperature:
        flags += ["-DL3_DUMP_SKIP_TEMPERATURE=1"]
    docker, exe = build_native_probe(tmp_path, "iwr_two_round_cli_probe.c", flags)
    result = subprocess.run([*docker, str(exe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "lifecycle=passed" in result.stdout
    if stage and clock and evidence and iq8:
        assert "commands=31 " in result.stdout
