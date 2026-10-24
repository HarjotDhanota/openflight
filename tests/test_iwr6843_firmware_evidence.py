"""Firmware contract for the IWR6843 frame-evidence block.

Host-compiled checks build only this fork's headers, never TI's SDK, and skip without a compiler.
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


# Stand-ins for the SDK names the evidence block uses; not the SDK.
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
    """Every helper is declared before use, with and without the EDMA pack's perf state."""
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
    """Independent of dump.py's packer: the bytes come from dump_format.h's structs."""
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


@pytest.mark.skipif(
    sys.byteorder != "little", reason="the firmware and the parser are little-endian"
)
def test_a_c_written_unassociated_record_reads_as_unobserved(tmp_path):
    out = tmp_path / "unassociated.l3dump"
    _run_c(FIXTURE_WRITER, tmp_path, str(out), "8", "unassociated")

    meta, _cube = dump.parse_dump(out.read_bytes())
    # pylint: disable=unsubscriptable-object
    record = meta["frame_evidence"][1]
    assert (record["seq"], record["start_tick"], record["associated"]) == (None, None, False)
    assert record["done_tick"] == 0x010927C0 + 340000


# Drives the firmware's l3_recordFrameEvidence on the host with made-up stamps.
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
    """Only a fresh start's seq and tick are copied; otherwise L3_FRAME_NO_STAMP."""
    source = EVIDENCE_PRELUDE + _evidence_block(FIRMWARE.read_text(encoding="utf-8"))
    out = _run_c(source + RECORD_HARNESS, tmp_path, flags=("-Wno-unused-variable",))

    associated, unassociated, fresh = out.splitlines()

    assert associated.split() == ["41", str(0x1234), str(0x2000), str(dump.FRAME_FLAG_ASSOCIATED)]
    no_stamp = str(dump.FRAME_NO_STAMP)
    assert unassociated.split() == [no_stamp, no_stamp, str(0x3000), "0"]
    assert fresh == "0"


def test_c_struct_sizes_match_the_python_parser(tmp_path):
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


def test_dump_stamps_request_freeze_stop_and_header_in_order():
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
    source = FIRMWARE.read_text(encoding="utf-8")
    dump_cli = _function_source(source, "int32_t l3_cli_dump", "static int32_t l3_cli_stats")

    assert dump_cli.index("l3_resetEvidence();") < dump_cli.index("l3_restartCompletedHwaFrame()")
    assert source.count("l3_resetEvidence();") == 2


def test_cycle_counter_starts_before_any_frame_can_start():
    """The cycle counter is enabled first thing in the init task."""
    source = FIRMWARE.read_text(encoding="utf-8")
    init = _function_source(source, "static void l3_initTask", "int32_t main(void)")

    assert init.index("l3_cycleCounterInit();") < init.index("UART_init();")


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


# Writes, per int16 value, l3_iq8ScanComponents' overflow count (uint8) and magnitude (uint16).
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
    """The firmware's scan, run on each int16, flags exactly what the pack changes.

    The EDMA pack copies each int16's low (little-endian) byte into an int8.
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
    source = FIRMWARE.read_text(encoding="utf-8")
    task = _rearm_task(source)
    call = "l3_startIq8PackAndScan(pendingSlot, pendingScratch);"

    freeze_call = task.index(call)
    assert freeze_call < task.index("l3_waitForAllIq8Edma();")
    assert freeze_call < task.index("Semaphore_post(gHwaFreezeSemaphore)")
    normal_call = task.rindex(call)
    assert freeze_call != normal_call
    assert task.count(call) == 2
    # The scan must never delay the HWA: the next frame is armed first.
    assert task.index("errCode = l3_restartCompletedHwaFrame();") < normal_call
    assert normal_call < task.rindex("gHwaRearmBusy = 0U;")
    assert "(void)l3_startIq8EdmaPack(" not in task


def test_a_pack_that_did_not_start_leaves_its_frame_unscanned():
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


# Drives l3_scanIq8Scratch on the host: a 16-word scratch, frames of 4-6 components and
# a fake cycle counter that advances gStep per read.
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
    assert lines[4] == "300 2 3 400"
    assert lines[5] == "hwi_depth=0 hwi_sections=2"  # one locked update per scan that ran
    assert lines[6] == "0 0 0 0"


@pytest.mark.skipif(
    sys.byteorder != "little", reason="the firmware and the parser are little-endian"
)
@pytest.mark.parametrize("version", [8, 9])
def test_c_struct_written_iq8_dumps_parse_scale_table_then_evidence(tmp_path, version):
    """Production IQ8 v8/v9 layout: scale table after the descriptors, then the evidence block."""
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


# With a one-frame pre ring the next frame reuses the slot being scanned, so the capture
# never claims the scan and the host reads "unobserved".
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
    source = FIRMWARE.read_text(encoding="utf-8")
    writer = _function_source(
        source, "static void l3_writeCaptureEvidence", "static void l3_writeFrameEvidence"
    )

    assert writer.count("l3_iq8ScanGuaranteed(l3_captureUsesIq8(), gCapturePlan.preFrames)") == 1
    assert "l3_captureUsesIq8() ? L3_CAPTURE_FLAG_IQ8_SCAN" not in source
    scan = _scan_source(source)
    assert "preFrames" not in scan
    assert "gFrameEvidenceFlags[slot] |= L3_FRAME_FLAG_IQ8_SCANNED;" in scan
