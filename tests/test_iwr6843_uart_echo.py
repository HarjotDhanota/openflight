"""Compile the actual CLI UART initialization with optional echo suppression."""

import subprocess

import pytest

from tests.test_iwr6843_two_round_firmware import ROOT, build_native_probe


@pytest.mark.parametrize("suppress_echo", [False, True])
def test_cli_uart_echo_is_opt_in_without_changing_binary_output_or_line_input(
    tmp_path, suppress_echo
):
    source = (ROOT / "firmware/iwr6843/l3_dump.c").read_text(encoding="utf-8")
    start = source.index("    UART_Params_init(&uartParams);")
    end = source.index("    gCliUart = UART_open(0, &uartParams);", start)
    end += len("    gCliUart = UART_open(0, &uartParams);")
    (tmp_path / "uart_init.inc").write_text(source[start:end] + "\n", encoding="utf-8")
    flags = ["-DL3_UART_NO_ECHO=1"] if suppress_echo else []
    docker, executable = build_native_probe(tmp_path, "iwr_uart_init_probe.c", flags)
    result = subprocess.run(
        [*docker, str(executable), "0" if suppress_echo else "1"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
