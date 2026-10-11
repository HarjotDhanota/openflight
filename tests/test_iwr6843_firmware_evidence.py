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
from typing import Any

import pytest

from openflight.iwr6843 import dump
from openflight.iwr6843.driver import parse_perf_stats

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


def _build_c(source: str, tmp_path: Path, *, flags: tuple[str, ...] = ()) -> Path:
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
            *flags,
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


def _run_c(source: str, tmp_path: Path, *args: str, flags: tuple[str, ...] = ()) -> str:
    exe = _build_c(source, tmp_path, flags=flags)
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
#ifndef __MRC  /* a harness may bring its own clock */
#define __MRC(cp, op1, crn, crm, op2) ((uint32_t)((cp) + (op1) + (crn) + (crm) + (op2)))
#endif
#define __MCR(cp, op1, src, crn, crm, op2) ((void)((cp) + (op1) + (src) + (crn) + (crm) + (op2)))
typedef struct { unsigned long totalSize, totalFreeSize, largestFreeSize; } Memory_Stats;
static Memory_Stats gStubHeap;  /* all zero unless a harness sets it */
static void Memory_getStats(void *heap, Memory_Stats *stats)
{
    (void)heap;
    memcpy(stats, &gStubHeap, sizeof(*stats));
}
static long gHwiDepth;
static unsigned long gHwiSections;
static void (*gOnHwiRestore)(void);  /* plays a pre-empting writer, if a harness sets it */
static uintptr_t Hwi_disable(void)
{
    gHwiDepth++;
    return 7U;
}
static void Hwi_restore(uintptr_t key)
{
    (void)key;
    gHwiDepth--;
    gHwiSections++;
    if (gHwiDepth == 0 && gOnHwiRestore != NULL) {
        gOnHwiRestore();
    }
}
"""


def _evidence_block(source: str) -> str:
    start = source.index("/* W4A-EVIDENCE-BEGIN */")
    end = source.index("/* W4A-EVIDENCE-END */", start)
    return source[start:end]


@pytest.mark.parametrize("pack_flags", [(), ("-DL3_IQ8_EDMA_PACK",)], ids=["cpu", "edma"])
def test_the_evidence_block_compiles_in_order_as_c99(tmp_path, pack_flags):
    """Every helper is declared before use (S12), with and without the EDMA pack's perf
    state. Not a substitute for the TI build."""
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
            *pack_flags,
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
    int8_t iq8[2];
    uint16_t scale[2] = {16U, 128U};
    int version, frame, k, option, unassociated = 0, iq8_format = 0, all_scanned = 0;
    FILE *f;

    if (argc < 3) {
        return 2;
    }
    for (option = 3; option < argc; option++) {
        if (strcmp(argv[option], "unassociated") == 0) {
            unassociated = 1;
        } else if (strcmp(argv[option], "iq8") == 0) {
            iq8_format = 1;  /* fmt 5: the scale table, then the evidence, then int8 I/Q */
        } else if (strcmp(argv[option], "all-scanned") == 0) {
            all_scanned = 1;
        } else {
            return 2;
        }
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
    h.sample_fmt = iq8_format ? L3_SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED
                              : L3_SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED;
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
    if (iq8_format) {
        put(f, scale, sizeof scale);  /* n_frames uint16, before the evidence */
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
        r.flags = (uint8_t)(L3_FRAME_FLAG_ASSOCIATED |
                            ((frame || all_scanned) ? L3_FRAME_FLAG_IQ8_SCANNED : 0U));
        if (unassociated && frame == 1) {  /* as l3_recordFrameEvidence writes it */
            r.seq = L3_FRAME_NO_STAMP;
            r.start_tick = L3_FRAME_NO_STAMP;
            r.flags = (uint8_t)L3_FRAME_FLAG_IQ8_SCANNED;
        }
        put(f, &r, sizeof r);
    }
    for (k = 0; k < 48; k++) {
        if (iq8_format) {
            iq8[0] = (int8_t)(-(k + 1));  /* TI order: Im first */
            iq8[1] = (int8_t)(k + 1);
            put(f, iq8, sizeof iq8);
        } else {
            iq[0] = (int16_t)(-(k + 1));  /* TI order: Im first */
            iq[1] = (int16_t)(k + 1);
            put(f, iq, sizeof iq);
        }
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


@pytest.mark.skipif(
    sys.byteorder != "little", reason="the firmware and the parser are little-endian"
)
def test_a_c_written_unassociated_record_reads_as_unobserved(tmp_path):
    """I1: a no-stamp record parses to None, and the request relation stays unobserved."""
    from openflight.iwr6843.frame_evidence import frame_timing_report

    out = tmp_path / "unassociated.l3dump"
    _run_c(FIXTURE_WRITER, tmp_path, str(out), "8", "unassociated")

    meta, _cube = dump.parse_dump(out.read_bytes())
    # pylint: disable=unsubscriptable-object
    record = meta["frame_evidence"][1]
    report = frame_timing_report(meta)
    request = report["request"]

    assert (record["seq"], record["start_tick"], record["associated"]) == (None, None, False)
    assert record["done_tick"] == 0x010927C0 + 340000
    assert [(f["kind"], f["frame"]) for f in report["findings"]] == [("unassociated", 1)]
    assert report["observed_start_offsets_us"] == [0.0, None]
    assert request["status"] == "unobserved"
    assert request["first_post_seq"] is None
    assert request["frames_request_to_first_post"] is None
    assert request["request_to_first_post_start_us"] is None


# Drives the firmware's own l3_recordFrameEvidence (the evidence block, host-compiled with
# EVIDENCE_PRELUDE's stand-ins). Made-up stamps for a known answer; not a hardware result.
RECORD_HARNESS = r"""
#include <stdio.h>

static void show(uint32_t slot)
{
    printf("%u %u %u %u\n", (unsigned)gFrameSeq[slot], (unsigned)gFrameStartTick[slot],
           (unsigned)gFrameDoneTick[slot], (unsigned)gFrameEvidenceFlags[slot]);
}

int main(void)
{
    gActiveFrameSeq = 41U;
    gActiveFrameStartTick = 0x1234U;
    gActiveFrameFresh = 1U;
    l3_recordFrameEvidence(3U, 0x2000U);
    l3_recordFrameEvidence(4U, 0x3000U);  /* no fresh armed start since slot 3 */
    show(3U);
    show(4U);
    printf("%u\n", (unsigned)gActiveFrameFresh);
    return 0;
}
"""


def test_a_record_without_a_fresh_start_carries_no_stamp(tmp_path):
    """I1, firmware: only a fresh start's seq and tick are copied; otherwise L3_FRAME_NO_STAMP."""
    source = EVIDENCE_PRELUDE + _evidence_block(FIRMWARE.read_text(encoding="utf-8"))
    out = _run_c(source + RECORD_HARNESS, tmp_path, flags=("-Wno-unused-variable",))

    associated, unassociated, fresh = out.splitlines()

    assert associated.split() == ["41", str(0x1234), str(0x2000), str(dump.FRAME_FLAG_ASSOCIATED)]
    no_stamp = str(dump.FRAME_NO_STAMP)
    assert unassociated.split() == [no_stamp, no_stamp, str(0x3000), "0"]
    assert fresh == "0"


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
    assert int(define("L3_FRAME_NO_STAMP"), 0) == dump.FRAME_NO_STAMP


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

    slot = callback.index("completedSlot = l3_activeRingSlot();")
    record = callback.index("l3_recordFrameEvidence(completedSlot,")
    assert slot < record < callback.index("gPostFramesCaptured++")
    assert record < callback.index("gPreFramesCaptured++")
    assert slot < callback.index("gIq8PendingSlot = completedSlot;")
    assert "gActiveFrameFresh = 0U;" in callback


def _ring_slot_helper(source: str) -> str:
    return _function_source(source, "static inline uint32_t l3_activeRingSlot(void)", "#endif")


def test_the_ring_slot_formula_is_written_once():
    """I2: one helper owns the active frame's ring slot; arming and output-done share it."""
    source = FIRMWARE.read_text(encoding="utf-8")
    helper = _ring_slot_helper(source)
    callback = _function_source(
        source, "static void l3_hwaOutputDoneCB", "static int32_t l3_hwaStartRing"
    )
    arm = _function_source(
        source, "static int32_t l3_configHwaFrameOutput", "static void l3_drainHwaRearmSemaphore"
    )

    assert source.count("gCapturePlan.preFrames + gPostFramesCaptured") == 1
    assert "gCapturePlan.preFrames + gPostFramesCaptured" in helper
    assert "gPreFramesCaptured % gCapturePlan.preFrames" in helper
    assert callback.count("l3_activeRingSlot()") == 1
    # The callback's remaining modulus is the wrap check, after the counter advanced.
    assert callback.count("gPreFramesCaptured % gCapturePlan.preFrames") == 1
    assert "(gPreFramesCaptured % gCapturePlan.preFrames) == 0U" in callback
    assert "gPreFramesCaptured % gCapturePlan.preFrames" not in arm
    assert arm.count("ringSlot = l3_activeRingSlot();") == 1
    # The slot is taken once the frame's role is set on both branches.
    assert arm.rindex("gActiveFrameIsPost = 0U;") < arm.index("ringSlot = l3_activeRingSlot();")
    assert arm.index("ringSlot = l3_activeRingSlot();") < arm.index(
        "if (ringSlot >= gCapturePlan.totalFrames)"
    )


def test_the_record_consumes_the_fresh_start():
    """A record is associated only with a fresh start, and consumes it."""
    source = FIRMWARE.read_text(encoding="utf-8")
    record = _function_source(
        source, "static void l3_recordFrameEvidence", "/* W4A-EVIDENCE-END */"
    )

    snapshot = record.index("uint8_t fresh = gActiveFrameFresh;")
    consumed = record.index("gActiveFrameFresh = 0U;")
    assert snapshot < consumed < record.index("return;")  # consumed on every path
    assert "fresh ? gActiveFrameSeq : L3_FRAME_NO_STAMP" in record
    assert "fresh ? gActiveFrameStartTick : L3_FRAME_NO_STAMP" in record
    assert "fresh ? L3_FRAME_FLAG_ASSOCIATED : 0U" in record
    assert record.count("gActiveFrameFresh") == 2  # one read, one consume: no re-read


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


def test_the_request_seq_and_tick_are_read_together():
    """A frame-start ISR between the two reads would skew frames_request_to_first_post."""
    source = FIRMWARE.read_text(encoding="utf-8")
    dump_cli = _function_source(source, "int32_t l3_cli_dump", "static int32_t l3_cli_stats")

    disable = dump_cli.index("Hwi_disable();")
    seq = dump_cli.index("gRequestSeq = gNumFrame;")
    tick = dump_cli.index("gRequestTick = l3_ticks();")
    restore = dump_cli.index("Hwi_restore(")
    assert disable < seq < tick < restore < dump_cli.index("l3_stopCaptureAtBoundary")


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


# --- Task 4: the IQ8 pre-narrowing scan -------------------------------------------------
# Made-up values for known answers; none of this is a hardware result. The host compiler
# checks the scan's C; it does not replace the owner's TI build (Task 9).

SCAN_PROBE = """#include <stdio.h>
#include "l3_evidence.h"
int main(void) {
  const int16_t v[] = {0, 127, -128, 128, -129, 32767, -32768, 5, -5};
  static int16_t all[65536];
  uint16_t maxAbs = 0;
  uint32_t overflow;
  int32_t value;
  for (value = -32768; value <= 32767; value++) {
    all[value + 32768] = (int16_t)value;
  }
  overflow = l3_iq8ScanComponents(v, 9, &maxAbs);
  printf("%u %u\\n", (unsigned)overflow, (unsigned)maxAbs);
  overflow = l3_iq8ScanComponents(v, 3, &maxAbs);
  printf("%u %u\\n", (unsigned)overflow, (unsigned)maxAbs);
  overflow = l3_iq8ScanComponents(v, 0, &maxAbs);
  printf("%u %u\\n", (unsigned)overflow, (unsigned)maxAbs);
  overflow = l3_iq8ScanComponents(all, 65536, &maxAbs);
  printf("%u %u\\n", (unsigned)overflow, (unsigned)maxAbs);
  return 0;
}
"""


def test_scan_counts_only_values_outside_int8(tmp_path):
    lines = _run_c(SCAN_PROBE, tmp_path).splitlines()

    assert lines == ["4 32768", "0 128", "0 0", "65280 32768"]


# Scans every int16 value on its own with the firmware's l3_iq8ScanComponents and writes,
# per value, the overflow count (uint8) and then the magnitude (uint16), to argv[1].
SCAN_EACH_VALUE_PROBE = r"""#include <stdio.h>
#include "l3_evidence.h"
int main(int argc, char **argv) {
  static uint8_t overflow[65536];
  static uint16_t maxAbs[65536];
  int32_t value;
  FILE *f;
  if (argc != 2) {
    return 2;
  }
  for (value = -32768; value <= 32767; value++) {
    const int16_t one = (int16_t)value;
    overflow[value + 32768] = (uint8_t)l3_iq8ScanComponents(&one, 1U, &maxAbs[value + 32768]);
  }
  f = fopen(argv[1], "wb");
  fwrite(overflow, 1, sizeof overflow, f);
  fwrite(maxAbs, 1, sizeof maxAbs, f);
  return fclose(f);
}
"""


@pytest.mark.skipif(
    sys.byteorder != "little", reason="the R4F and the EDMA model here are little-endian"
)
def test_the_scan_rule_is_exactly_the_values_the_low_byte_copy_changes(tmp_path):
    """R-D1: the firmware's scan, run on each int16, flags exactly what the pack changes.

    The reference models l3_startIq8EdmaPack: aCount 1, source B-index 2 bytes, so the EDMA
    copies the byte at each int16's even (low, little-endian) address into an int8.
    """
    import numpy as np

    out = tmp_path / "scan_each_value.bin"
    _run_c(SCAN_EACH_VALUE_PROBE, tmp_path, str(out))
    raw = out.read_bytes()
    flagged = np.frombuffer(raw, dtype=np.uint8, count=65536)
    magnitude = np.frombuffer(raw, dtype="<u2", offset=65536, count=65536)

    values = np.arange(-32768, 32768, dtype="<i2")
    copied = values.view(np.int8)[0::2].astype(np.int32)
    changed = copied != values.astype(np.int32)

    np.testing.assert_array_equal(flagged.astype(bool), changed)
    assert set(np.unique(flagged)) == {0, 1}
    np.testing.assert_array_equal(magnitude.astype(np.int32), np.abs(values.astype(np.int32)))
    assert int(changed.sum()) == 65280  # every value but -128..127


def _rearm_task(source: str) -> str:
    return _function_source(
        source, "static void l3_hwaRearmTask", "/* Fill the 20-byte fixed dump header"
    )


def test_the_scan_runs_after_each_pack_starts_and_before_the_task_lets_go():
    """Both rearm pack sites start the pack and scan through one helper (DRY)."""
    source = FIRMWARE.read_text(encoding="utf-8")
    task = _rearm_task(source)
    call = "l3_startIq8PackAndScan(pendingSlot, pendingScratch);"

    freeze_call = task.index(call)
    assert freeze_call < task.index("l3_waitForAllIq8Edma();")
    assert freeze_call < task.index("Semaphore_post(gHwaFreezeSemaphore)")
    normal_call = task.rindex(call)
    assert freeze_call != normal_call
    assert task.count(call) == 2
    # The normal path arms the next frame first, so the scan never delays the HWA.
    assert task.index("errCode = l3_restartCompletedHwaFrame();") < normal_call
    assert normal_call < task.rindex("gHwaRearmBusy = 0U;")
    assert "(void)l3_startIq8EdmaPack(" not in task


def test_a_pack_that_did_not_start_leaves_its_frame_unscanned():
    """No fill-ins: a frame the EDMA never narrowed must not read 'scanned, clean'."""
    source = FIRMWARE.read_text(encoding="utf-8")
    helper = _function_source(
        source,
        "static void l3_startIq8PackAndScan",
        "static uint32_t l3_snapshotBinStartForNextFrame",
    )

    failed = helper.index("if (l3_startIq8EdmaPack(slot, scratch) != 0) {")
    assert (
        failed < helper.index("return;", failed) < helper.index("l3_scanIq8Scratch(slot, scratch);")
    )
    assert helper.index("#ifdef L3_IQ8_SCAN") < helper.index("l3_scanIq8Scratch(slot, scratch);")


def test_the_scan_marks_the_frame_and_saturates_its_count():
    source = FIRMWARE.read_text(encoding="utf-8")
    scan = _function_source(
        source, "static void l3_scanIq8Scratch", "static uint32_t l3_snapshotBinStartForNextFrame"
    )

    assert (
        "l3_iq8ScanComponents(&g_iq16FrameScratch[scratch][0], gFrameBytes[slot], &maxAbs)" in scan
    )
    assert "(overflow > 0xFFFFU) ? 0xFFFFU : (uint16_t)overflow" in scan
    assert "gFrameEvidenceFlags[slot] |= L3_FRAME_FLAG_IQ8_SCANNED;" in scan


def test_the_scan_exists_only_where_the_low_byte_copy_narrows():
    """The scan's rule is the EDMA low-byte copy's; the CPU shift-pack build is not scanned."""
    source = FIRMWARE.read_text(encoding="utf-8")

    assert (
        "#if defined(L3_CAPTURE_EVIDENCE) && defined(L3_RING_IQ8) && defined(L3_IQ8_EDMA_PACK)\n"
        "#define L3_IQ8_SCAN 1\n#endif"
    ) in source
    assert source.index("#define L3_CAPTURE_EVIDENCE 1") < source.index("#define L3_IQ8_SCAN 1")
    scan = source.index("static void l3_scanIq8Scratch")
    assert source.rindex("#ifdef L3_IQ8_SCAN", 0, scan) > source.rindex("#endif", 0, scan)


def test_the_capture_says_whether_it_scanned():
    source = FIRMWARE.read_text(encoding="utf-8")
    writer = _function_source(
        source, "static void l3_writeCaptureEvidence", "static void l3_writeFrameEvidence"
    )

    assert (
        "evidence.flags = l3_iq8ScanGuaranteed(l3_captureUsesIq8(), gCapturePlan.preFrames)\n"
        "                         ? L3_CAPTURE_FLAG_IQ8_SCAN\n"
        "                         : 0U;"
    ) in writer
    scan_flag = writer.index("evidence.flags = l3_iq8ScanGuaranteed(")
    assert writer.rindex("#ifdef L3_IQ8_SCAN", 0, scan_flag) < scan_flag
    assert "    evidence.flags = 0U;" in writer[scan_flag:]  # every other build: unobserved
    assert '#include "l3_evidence.h"' in source
    assert source.count("l3_resetPerf();") == 1


def test_perf_resets_with_the_evidence_at_sensor_start():
    """High-water marks run from sensorStart; the post-dump restart keeps them."""
    source = FIRMWARE.read_text(encoding="utf-8")
    block = _evidence_block(source)
    dump_cli = _function_source(source, "int32_t l3_cli_dump", "static int32_t l3_cli_stats")

    assert "static void l3_resetPerf(void)" in block
    for name in ("gPerfScanMaxCycles", "gPerfScanFrames", "gIq8OverflowComponents"):
        assert f"static uint32_t {name};" in block
        assert f"    {name} = 0U;" in block
    assert "    l3_resetEvidence();\n    l3_resetPerf();\n#endif\n#ifdef L3_RING_IQ8\n" in source
    assert "l3_resetPerf();" not in dump_cli


def _scan_source(source: str) -> str:
    start = source.index("static void l3_scanIq8Scratch")
    return source[start : source.index("#endif", start)]


# Drives the firmware's own l3_scanIq8Scratch (with the evidence block) on the host, with
# stand-ins for the SDK and the capture plan: a 16-word scratch, frames of 4-6 components
# and a fake cycle counter that advances gStep per read. Made-up values, known answers.
SCRATCH_HARNESS_PRELUDE = r"""
#include <stdint.h>
static uint32_t gNow;
static uint32_t gStep;
#define __MRC(cp, op1, crn, crm, op2) (gNow += gStep)
"""

SCRATCH_HARNESS_STANDINS = r"""
#define L3_IQ16_SCRATCH_WORDS 16U
static int16_t g_iq16FrameScratch[2][L3_IQ16_SCRATCH_WORDS];
static uint32_t gFrameBytes[L3_MAX_CAPTURE_FRAMES];
static struct { uint8_t totalFrames; } gCapturePlan = {4U};
"""

SCRATCH_HARNESS_MAIN = r"""
#include <stdio.h>

static void show(uint32_t slot)
{
    printf("%u %u %u\n", (unsigned)gFrameIq8Overflow[slot], (unsigned)gFrameIq8MaxAbs[slot],
           (unsigned)gFrameEvidenceFlags[slot]);
}

int main(void)
{
    const int16_t ping[6] = {12, -200, 127, -128, 300, -32768};
    const int16_t pong[4] = {1, -2, 3, -4};
    uint32_t i;

    for (i = 0U; i < 6U; i++) {
        g_iq16FrameScratch[0][i] = ping[i];
    }
    for (i = 0U; i < 4U; i++) {
        g_iq16FrameScratch[1][i] = pong[i];
    }
    for (i = 0U; i < 4U; i++) {
        gFrameEvidenceFlags[i] = L3_FRAME_FLAG_ASSOCIATED;
        gFrameIq8Overflow[i] = 0x1111U;  /* stale values a skipped scan must leave alone */
        gFrameIq8MaxAbs[i] = 0x2222U;
    }
    gFrameBytes[0] = 6U;
    gFrameBytes[1] = 4U;
    gFrameBytes[2] = 0U;                          /* the pack refuses it: no data */
    gFrameBytes[3] = L3_IQ16_SCRATCH_WORDS + 1U;  /* more than the scratch holds */

    gStep = 300U;
    gNow = 0xFFFFFE00U;  /* start reads 0xFFFFFF2C, the end read wraps to 0x58 */
    l3_scanIq8Scratch(0U, 0U);
    gStep = 100U;
    l3_scanIq8Scratch(1U, 1U);
    l3_scanIq8Scratch(2U, 0U);
    l3_scanIq8Scratch(3U, 0U);
    l3_scanIq8Scratch(1U, 2U);   /* no third scratch */
    l3_scanIq8Scratch(4U, 0U);   /* past the plan's frames */
    for (i = 0U; i < 4U; i++) {
        show(i);
    }
    printf("%u %u %u %llu\n", (unsigned)gPerfScanMaxCycles, (unsigned)gPerfScanFrames,
           (unsigned)gIq8OverflowComponents, (unsigned long long)gPerfScanCyclesTotal);
    printf("hwi_depth=%ld hwi_sections=%lu\n", gHwiDepth, gHwiSections);
    l3_resetPerf();
    printf("%u %u %u %llu\n", (unsigned)gPerfScanMaxCycles, (unsigned)gPerfScanFrames,
           (unsigned)gIq8OverflowComponents, (unsigned long long)gPerfScanCyclesTotal);
    return 0;
}
"""


def test_the_scan_records_each_frame_and_its_cost(tmp_path):
    """Host-compiled l3_scanIq8Scratch: per-slot counts, the scanned flag, totals, cycles."""
    source = FIRMWARE.read_text(encoding="utf-8")
    program = (
        SCRATCH_HARNESS_PRELUDE
        + EVIDENCE_PRELUDE
        + _evidence_block(source)
        + SCRATCH_HARNESS_STANDINS
        + _scan_source(source)
        + SCRATCH_HARNESS_MAIN
    )
    lines = _run_c(program, tmp_path, flags=("-Wno-unused-variable",)).splitlines()
    associated = dump.FRAME_FLAG_ASSOCIATED
    scanned = associated | dump.FRAME_FLAG_IQ8_SCANNED

    assert lines[0] == f"3 32768 {scanned}"  # -200, 300 and -32768 wrap
    assert lines[1] == f"0 4 {scanned}"
    assert lines[2] == f"{0x1111} {0x2222} {associated}"  # zero components: unscanned
    assert lines[3] == f"{0x1111} {0x2222} {associated}"  # oversized frame: unscanned
    # worst elapsed scan ticks (across the wrap), frames scanned, components, elapsed total
    assert lines[4] == "300 2 3 400"
    assert lines[5] == "hwi_depth=0 hwi_sections=2"  # one locked update per scan that ran
    assert lines[6] == "0 0 0 0"


@pytest.mark.skipif(
    sys.byteorder != "little", reason="the firmware and the parser are little-endian"
)
@pytest.mark.parametrize("version", [8, 9])
def test_c_struct_written_iq8_dumps_parse_scale_table_then_evidence(tmp_path, version):
    """Controller ruling (Task 3 review): production IQ8 v8/v9 layout, independent of dump.py.

    fmt 5 puts the n_frames uint16 scale table after the descriptors and before the
    evidence block; the payload is int8 Im/Re times the frame's scale.
    """
    from openflight.iwr6843.frame_evidence import sample_integrity_report

    out = tmp_path / f"iq8_v{version}.l3dump"
    _run_c(FIXTURE_WRITER, tmp_path, str(out), str(version), "iq8")

    raw = out.read_bytes()
    meta, cube = dump.parse_dump(raw)
    # pylint: disable=unsubscriptable-object

    assert meta["sample_fmt"] == dump.SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED
    assert meta["version"] == version
    assert meta["iq8_scales"] == (16, 128)
    assert meta["capture_evidence"]["header_tick"] == 0x44444444
    assert meta["capture_evidence"]["flags"] == dump.CAPTURE_FLAG_IQ8_SCAN
    assert meta["frame_evidence"][1]["iq8_overflow"] == 2
    assert meta["frame_evidence"][1]["iq8_max_abs"] == 300
    assert [record["iq8_scanned"] for record in meta["frame_evidence"]] == [False, True]
    assert meta["dump_trailer"] == {"payload_end_tick": 0x55555555}
    assert cube[0, 0, 0, 0] == 16 * (1 - 1j)
    assert cube[1, 2, 3, 1] == 128 * (48 - 48j)
    assert len(raw) == meta["header_nbytes"] + dump.payload_nbytes(meta, raw)
    # Frame 0 was not scanned: the whole capture's integrity is unobserved, never clean.
    report = sample_integrity_report(meta, cube)
    assert (report["format"], report["status"]) == ("iq8", "unobserved")
    assert report["reason"] == "frames [0] were not scanned before narrowing"


@pytest.mark.skipif(
    sys.byteorder != "little", reason="the firmware and the parser are little-endian"
)
def test_a_c_written_fully_scanned_iq8_dump_reports_its_overflow(tmp_path):
    """Every frame scanned: the report carries the firmware's counts, frame by frame."""
    from openflight.iwr6843.frame_evidence import sample_integrity_report

    out = tmp_path / "iq8_scanned.l3dump"
    _run_c(FIXTURE_WRITER, tmp_path, str(out), "8", "iq8", "all-scanned")

    meta, cube = dump.parse_dump(out.read_bytes())
    report = sample_integrity_report(meta, cube)

    assert report["status"] == "overflowed"
    assert report["frames_scanned"] == 2
    assert report["overflow_components"] == 2
    assert report["frames_with_overflow"] == [1]
    assert report["max_abs"] == 300


# Ruling on Task 4 concern 2: with a one-frame pre ring the next frame reuses the slot being
# scanned, so an output-done during a long scan could tag the new record with the old
# frame's counts. The capture then never claims the scan; the host reads "unobserved".
GUARANTEE_PROBE = """#include <stdio.h>
#include "l3_evidence.h"
int main(void) {
  printf("%u %u %u %u %u %u\\n",
         (unsigned)l3_iq8ScanGuaranteed(1U, 0U), (unsigned)l3_iq8ScanGuaranteed(1U, 1U),
         (unsigned)l3_iq8ScanGuaranteed(1U, 2U), (unsigned)l3_iq8ScanGuaranteed(1U, 21U),
         (unsigned)l3_iq8ScanGuaranteed(0U, 21U), (unsigned)L3_IQ8_SCAN_MIN_PRE_FRAMES);
  return 0;
}
"""


def test_the_scan_is_claimed_only_when_no_slot_can_be_reused_mid_scan(tmp_path):
    """Host-run plan check: IQ8 and at least two pre frames, or the capture flag stays off."""
    assert _run_c(GUARANTEE_PROBE, tmp_path).split() == ["0", "0", "1", "1", "0", "2"]


def test_the_capture_flag_follows_the_plan_check():
    """Source contract: the writer takes the flag from the plan check and the live plan."""
    source = FIRMWARE.read_text(encoding="utf-8")
    writer = _function_source(
        source, "static void l3_writeCaptureEvidence", "static void l3_writeFrameEvidence"
    )

    assert writer.count("l3_iq8ScanGuaranteed(l3_captureUsesIq8(), gCapturePlan.preFrames)") == 1
    assert "l3_captureUsesIq8() ? L3_CAPTURE_FLAG_IQ8_SCAN" not in source
    # The scan itself is unchanged: it still marks every frame it covers.
    scan = _scan_source(source)
    assert "preFrames" not in scan
    assert "gFrameEvidenceFlags[slot] |= L3_FRAME_FLAG_IQ8_SCANNED;" in scan


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


# --- Task 5 fix round: no torn or mismatched perf pairs ------------------------------------


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


# --- Task 5 fix round 2: the IQ8 overflow total saturates, and says so ---------------------

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
