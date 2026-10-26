"""Known-answer wire accounting with simulated UART results, not hardware delivery."""

import subprocess
import zlib

from tests.test_iwr6843_two_round_firmware import ROOT, build_native_probe


def test_requested_crc_and_returned_counts_include_failed_and_short_writes(tmp_path):
    source = (ROOT / "firmware/iwr6843/l3_dump.c").read_text(encoding="utf-8")
    start = source.index("#ifdef L3_DUMP_WIRE_TRACE\n")
    end = source.index("#ifdef CONFIGURABLE_CAPTURE\nstatic void l3_writeFrameDescriptor", start)
    (tmp_path / "wire_hook.inc").write_text(source[start:end], encoding="utf-8")
    docker, executable = build_native_probe(tmp_path, "iwr_dump_wire_probe.c")
    block = bytes((i * 37) % 256 for i in range(1024))
    expected_crc = zlib.crc32(b"".join(block[: 2**power] for power in range(11)))
    for scenario, total, first in [(0, 2047, 0), (1, 2046, 3), (2, 2043, -9), (3, 2048, 5)]:
        result = subprocess.run(
            [*docker, str(executable), str(scenario)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        fields = result.stdout.split()
        assert list(map(int, fields[:7])) == [
            11,
            2047,
            total,
            int(scenario == 2),
            int(scenario != 0),
            0 if scenario == 0 else 3,
            first,
        ]
        assert int(fields[7], 16) == expected_crc


def test_wire_wrapper_covers_binary_writes_but_ends_before_restart():
    source = (ROOT / "firmware/iwr6843/l3_dump.c").read_text(encoding="utf-8")
    begin = source.index("#define UART_writePolling l3_dumpWireWrite")
    descriptor = source.index("static void l3_writeFrameDescriptor(uint32_t slot")
    dump = source.index("int32_t l3_cli_dump(int32_t argc, char *argv[])\n{")
    stream = source.index("L3_WIRE_BEGIN();", dump)
    header = source.index("UART_writePolling(gDataUart, (uint8_t *)&h", dump)
    end = source.index("L3_WIRE_END();", header)
    restart = source.index("l3_restartCompletedHwaFrame()", end)
    unalias = source.index("#undef UART_writePolling", restart)
    stats = source.index('/* CLI "stats"', unalias)
    assert begin < descriptor < dump < stream < header < end < restart < unalias < stats
