"""Firmware contract for W4a's observed evidence.

Source-contract checks follow tests/test_iwr6843_firmware_rearm.py. The host-compiled
checks build only this fork's own headers with the Mac's C compiler; they never touch
TI's SDK and say so when no compiler is present.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from openflight.iwr6843 import dump

FIRMWARE_DIR = Path(__file__).parents[1] / "firmware" / "iwr6843"
FIRMWARE = FIRMWARE_DIR / "l3_dump.c"
FORMAT = FIRMWARE_DIR / "dump_format.h"


def _function_source(source: str, name: str, next_name: str) -> str:
    start = source.rindex(name)
    end = source.index(next_name, start)
    return source[start:end]


def _compiler() -> str:
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("no host C compiler (cc) to build the firmware's pure headers")
    return compiler


def _build_c(source: str, tmp_path: Path) -> Path:
    program = tmp_path / "probe.c"
    program.write_text(source, encoding="utf-8")
    exe = tmp_path / "probe"
    subprocess.run(
        [
            _compiler(),
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-unused-function",  # the header's helpers are file-static; a probe uses some
            "-I",
            str(FIRMWARE_DIR),
            str(program),
            "-o",
            str(exe),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return exe


def _run_c(source: str, tmp_path: Path, *args: str) -> str:
    exe = _build_c(source, tmp_path)
    return subprocess.run([str(exe), *args], check=True, capture_output=True, text=True).stdout


# Stand-ins for the SDK names the evidence block uses, so the host compiler can check the
# block's declarations and order (S12). They are not the SDK; the owner's TI build is.
EVIDENCE_PRELUDE = """#include <stdint.h>
#include <string.h>
#include "dump_format.h"
#include "l3_evidence.h"
#define L3_CAPTURE_EVIDENCE 1
#define L3_RING_IQ8 1
#define L3_MAX_CAPTURE_FRAMES 64U
#define __MRC(cp, op1, crn, crm, op2) ((uint32_t)((cp) + (op1) + (crn) + (crm) + (op2)))
#define __MCR(cp, op1, src, crn, crm, op2) ((void)((cp) + (op1) + (src) + (crn) + (crm) + (op2)))
typedef struct { unsigned long totalSize, totalFreeSize, largestFreeSize; } Memory_Stats;
static void Memory_getStats(void *heap, Memory_Stats *stats)
{
    (void)heap;
    memset(stats, 0, sizeof(*stats));
}
"""


def _evidence_block(source: str) -> str:
    start = source.index("/* W4A-EVIDENCE-BEGIN */")
    end = source.index("/* W4A-EVIDENCE-END */", start)
    return source[start:end]


def test_the_evidence_block_compiles_in_order_as_c99(tmp_path):
    """Every helper is declared before use (S12). Not a substitute for the TI build."""
    probe = tmp_path / "evidence_block.c"
    probe.write_text(
        EVIDENCE_PRELUDE + _evidence_block(FIRMWARE.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            _compiler(),
            "-std=c99",
            "-fsyntax-only",
            "-Wall",
            "-Werror",
            "-Werror=implicit-function-declaration",
            "-Wno-unused-function",
            "-Wno-unused-variable",
            "-Wno-unused-but-set-variable",
            "-I",
            str(FIRMWARE_DIR),
            str(probe),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_the_tick_source_opens_the_evidence_block():
    """The block's first mention of l3_ticks is its definition."""
    block = _evidence_block(FIRMWARE.read_text(encoding="utf-8"))
    definition = block.index("static uint32_t l3_ticks(void)")

    assert block.index("l3_ticks(") == definition + len("static uint32_t ")


FIXTURE_WRITER = r"""#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dump_format.h"

static void put(FILE *f, const void *p, size_t n) { fwrite(p, 1, n, f); }

int main(int argc, char **argv)
{
    l3_dump_header_t h;
    l3_temperature_report_t t;
    l3_capture_evidence_t e;
    l3_frame_evidence_t r;
    l3_dump_trailer_t tr;
    uint8_t desc[4];
    int16_t iq[2];
    int version, frame, k;
    FILE *f;

    if (argc != 3) {
        return 2;
    }
    version = atoi(argv[2]);
    f = fopen(argv[1], "wb");
    memset(&h, 0, sizeof h);
    memcpy(h.magic, L3_DUMP_MAGIC, 4);
    h.version = (uint16_t)version;
    h.n_frames = 2;
    h.chirps_per_frame = 3;
    h.n_tx = 3;
    h.n_rx = 4;
    h.n_samples = 2;
    h.sample_fmt = L3_SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED;
    h.frame_period_us = 3000;
    put(f, &h, sizeof h);
    if (version == L3_DUMP_VERSION_EVIDENCE_TEMPERATURE) {
        memset(&t, 0, sizeof t);
        t.device_time_ms = 1234;
        t.tmpRx0Sens = 41;
        put(f, &t, sizeof t);
    }
    for (frame = 0; frame < 2; frame++) {
        desc[0] = 20;
        desc[1] = 2;
        desc[2] = (uint8_t)(frame ? (3000 & 0xFF) : 0);
        desc[3] = (uint8_t)(frame ? (3000 >> 8) : 0);
        put(f, desc, sizeof desc);
    }
    memset(&e, 0, sizeof e);
    memcpy(e.magic, L3_CAPTURE_EVIDENCE_MAGIC, 4);
    e.block_nbytes = (uint16_t)sizeof e;
    e.frame_record_nbytes = (uint16_t)sizeof r;
    e.tick_hz = 200000000U;
    e.request_tick = 0x11111111U;
    e.request_seq = 7U;
    e.freeze_tick = 0x22222222U;
    e.stop_tick = 0x33333333U;
    e.header_tick = 0x44444444U;
    e.hwa_missed_total = 3U;
    e.pre_frames = 1U;
    e.post_frames = 1U;
    e.flags = L3_CAPTURE_FLAG_IQ8_SCAN;
    e.reserved = 0xABU;
    put(f, &e, sizeof e);
    for (frame = 0; frame < 2; frame++) {
        memset(&r, 0xCD, sizeof r);  /* reserved bytes non-zero on purpose */
        r.seq = (uint32_t)(8 + frame);
        r.start_tick = 0x01000000U + 600000U * (uint32_t)frame;
        r.done_tick = r.start_tick + 340000U;
        r.iq8_overflow = (uint16_t)(2 * frame);
        r.iq8_max_abs = (uint16_t)(90 + 210 * frame);
        r.flags = (uint8_t)(L3_FRAME_FLAG_ASSOCIATED | (frame ? L3_FRAME_FLAG_IQ8_SCANNED : 0U));
        put(f, &r, sizeof r);
    }
    for (k = 0; k < 48; k++) {
        iq[0] = (int16_t)(-(k + 1));  /* TI order: Im first */
        iq[1] = (int16_t)(k + 1);
        put(f, iq, sizeof iq);
    }
    memcpy(tr.magic, L3_DUMP_TRAILER_MAGIC, 4);
    tr.payload_end_tick = 0x55555555U;
    put(f, &tr, sizeof tr);
    return fclose(f);
}
"""


@pytest.mark.skipif(
    sys.byteorder != "little", reason="the firmware and the parser are little-endian"
)
@pytest.mark.parametrize("version", [8, 9])
def test_c_struct_written_v8_and_v9_dumps_parse(tmp_path, version):
    """Independent of dump.py's packer (S5): the bytes come from dump_format.h's structs."""
    from openflight.iwr6843.frame_evidence import frame_timing_report

    out = tmp_path / f"v{version}.l3dump"
    _run_c(FIXTURE_WRITER, tmp_path, str(out), str(version))

    meta, cube = dump.parse_dump(out.read_bytes())
    # parse_dump fills these keys for v8/v9; pylint only sees their initial None.
    # pylint: disable=unsubscriptable-object

    assert meta["version"] == version
    assert (meta["temperature_report"] is None) == (version == 9)
    assert meta["capture_evidence"]["header_tick"] == 0x44444444
    assert meta["capture_evidence"]["flags"] == dump.CAPTURE_FLAG_IQ8_SCAN
    assert meta["frame_evidence"][1] == {
        "seq": 9,
        "start_tick": 0x010927C0,
        "done_tick": 0x010927C0 + 340000,
        "iq8_overflow": 2,
        "iq8_max_abs": 300,
        "associated": True,
        "iq8_scanned": True,
    }
    assert meta["dump_trailer"] == {"payload_end_tick": 0x55555555}
    assert cube[1, 2, 3, 1] == 48 - 48j
    assert frame_timing_report(meta)["status"] == "continuous"


def test_c_struct_sizes_match_the_python_parser(tmp_path):
    """sizeof each C evidence struct equals the parser's struct size."""
    out = _run_c(
        '#include <stdio.h>\n#include "dump_format.h"\n'
        "int main(void) {\n"
        '  printf("%zu %zu %zu\\n", sizeof(l3_capture_evidence_t),\n'
        "         sizeof(l3_frame_evidence_t), sizeof(l3_dump_trailer_t));\n"
        "  return 0;\n}\n",
        tmp_path,
    )

    assert out.split() == [
        str(dump.CAPTURE_EVIDENCE.size),
        str(dump.FRAME_EVIDENCE.size),
        str(dump.DUMP_TRAILER.size),
    ]


def test_dump_format_constants_match_the_parser():
    """dump_format.h's versions, magics and flags equal dump.py's."""
    header = FORMAT.read_text(encoding="utf-8")

    def define(name):
        return re.search(rf"#define {name}\s+(\S+)", header).group(1).removesuffix("U")

    assert int(define("L3_DUMP_VERSION_EVIDENCE_TEMPERATURE")) == (
        dump.DUMP_VERSION_EVIDENCE_TEMPERATURE
    )
    assert int(define("L3_DUMP_VERSION_EVIDENCE")) == dump.DUMP_VERSION_EVIDENCE
    assert define("L3_CAPTURE_EVIDENCE_MAGIC") == '"ILE1"'
    assert define("L3_DUMP_TRAILER_MAGIC") == '"ILT1"'
    assert int(define("L3_FRAME_FLAG_ASSOCIATED"), 0) == dump.FRAME_FLAG_ASSOCIATED
    assert int(define("L3_FRAME_FLAG_IQ8_SCANNED"), 0) == dump.FRAME_FLAG_IQ8_SCANNED
    assert int(define("L3_CAPTURE_FLAG_IQ8_SCAN"), 0) == dump.CAPTURE_FLAG_IQ8_SCAN


def test_production_builds_turn_the_evidence_on():
    """Configurable hybrid-cadence builds define L3_CAPTURE_EVIDENCE."""
    source = FIRMWARE.read_text(encoding="utf-8")

    assert (
        "#if defined(CONFIGURABLE_CAPTURE) && defined(HYBRID_CADENCE_CAPTURE)\n"
        "#define L3_CAPTURE_EVIDENCE 1\n#endif"
    ) in source


def test_frame_start_isr_stamps_only_armed_frames():
    """Only an armed frame start sets the active sequence, tick and fresh flag."""
    source = FIRMWARE.read_text(encoding="utf-8")
    isr = _function_source(
        source, "static void l3_frameStartISR", "static int32_t l3_startFrontEnd"
    )

    assert isr.index("uint32_t now = l3_ticks();") < isr.index("gNumFrame++")
    missed = isr.index("gHwaMissedFrameStarts++")
    armed = isr.index("gActiveFrameSeq = gNumFrame;")
    assert missed < isr.index("else {", missed) < armed
    assert "gActiveFrameStartTick = now;" in isr
    assert "gActiveFrameFresh = 1U;" in isr
    assert armed < isr.index("gHwaArmedForFrame = 0U;")


def test_output_done_records_the_completed_slot_before_counters_advance():
    """The record lands in the completed frame's slot, before the counters move."""
    source = FIRMWARE.read_text(encoding="utf-8")
    callback = _function_source(
        source, "static void l3_hwaOutputDoneCB", "static int32_t l3_hwaStartRing"
    )

    record = callback.index("l3_recordFrameEvidence(")
    assert record < callback.index("gPostFramesCaptured++")
    assert record < callback.index("gPreFramesCaptured++")
    assert (
        "gCapturePlan.preFrames + gPostFramesCaptured"
        in callback[max(0, record - 300) : record + 300]
    )
    assert "gActiveFrameFresh = 0U;" in callback


def test_the_record_consumes_the_fresh_start():
    """A record is associated only with a fresh start, and consumes it."""
    source = FIRMWARE.read_text(encoding="utf-8")
    record = _function_source(
        source, "static void l3_recordFrameEvidence", "/* W4A-EVIDENCE-END */"
    )

    assert "gActiveFrameFresh ? L3_FRAME_FLAG_ASSOCIATED : 0U" in record
    assert record.index("L3_FRAME_FLAG_ASSOCIATED") < record.rindex("gActiveFrameFresh = 0U;")


def test_dump_stamps_request_freeze_stop_and_header_in_order():
    """Request, freeze, RF-stop and header ticks are taken in that order."""
    source = FIRMWARE.read_text(encoding="utf-8")
    stop = _function_source(
        source,
        "static int32_t l3_stopCaptureAtBoundary",
        "static int32_t l3_stopCaptureForShutdown",
    )
    dump_cli = _function_source(source, "int32_t l3_cli_dump", "static int32_t l3_cli_stats")

    assert stop.index("l3_freezeHwaAfterPostFrames") < stop.index("gFreezeTick = l3_ticks();")
    assert stop.index("l3_finishCaptureStop") < stop.index("gStopTick = l3_ticks();")
    assert dump_cli.index("gRequestTick = l3_ticks();") < dump_cli.index("l3_stopCaptureAtBoundary")
    header_write = dump_cli.index("UART_writePolling(gDataUart, (uint8_t *)&h, sizeof(h));")
    assert dump_cli.index("headerTick = l3_ticks();") < header_write
    assert "L3_DUMP_VERSION_EVIDENCE_TEMPERATURE" in dump_cli[:header_write]


def test_dump_streams_evidence_after_metadata_and_before_payload_then_the_trailer():
    """Evidence follows the descriptors and scales; the trailer ends an uncancelled dump."""
    source = FIRMWARE.read_text(encoding="utf-8")
    dump_cli = _function_source(source, "int32_t l3_cli_dump", "static int32_t l3_cli_stats")

    evidence = dump_cli.index("l3_writeCaptureEvidence(actualPre, actualPost, headerTick);")
    assert dump_cli.index("l3_writeU16Le(gFrameIq8Scale[slot]);") < evidence
    assert evidence < dump_cli.index(
        "l3_writeFrameEvidence((oldestPre + i) % gCapturePlan.preFrames);"
    )
    assert evidence < dump_cli.index("UART_writePolling(gDataUart, &g_ring[gFrameOffset[slot]],")
    trailer = dump_cli.index("l3_writeDumpTrailer();")
    last_payload = dump_cli.rindex("UART_writePolling(gDataUart, &g_ring[gFrameOffset[slot]],")
    assert last_payload < trailer
    assert "if (!dumpCancelled) {" in dump_cli[trailer - 80 : trailer]


def test_evidence_is_reset_at_sensor_start_and_before_each_restart():
    """Evidence flags are cleared at sensorStart and before each post-dump restart."""
    source = FIRMWARE.read_text(encoding="utf-8")
    dump_cli = _function_source(source, "int32_t l3_cli_dump", "static int32_t l3_cli_stats")

    assert dump_cli.index("l3_resetEvidence();") < dump_cli.index("l3_restartCompletedHwaFrame()")
    assert source.count("l3_resetEvidence();") == 2


def test_cycle_counter_starts_before_any_frame_can_start():
    """The cycle counter is enabled first thing in the init task."""
    source = FIRMWARE.read_text(encoding="utf-8")
    init = _function_source(source, "static void l3_initTask", "int32_t main(void)")

    assert init.index("l3_cycleCounterInit();") < init.index("UART_init();")
