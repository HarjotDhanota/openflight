"""Firmware contract for the IWR6843 frame-evidence block.

Host-compiled checks build only this fork's headers, never TI's SDK, and skip without a compiler.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from openflight.iwr6843 import dump

FIRMWARE_DIR = Path(__file__).parents[1] / "firmware" / "iwr6843"
FIRMWARE = FIRMWARE_DIR / "l3_dump.c"


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


FORMAT_PROBE = r"""#include <stdio.h>
#include "dump_format.h"
int main(void) {
  printf("%zu %zu %zu %s %s\n", sizeof(l3_capture_evidence_t), sizeof(l3_frame_evidence_t),
         sizeof(l3_dump_trailer_t), L3_CAPTURE_EVIDENCE_MAGIC, L3_DUMP_TRAILER_MAGIC);
  printf("%lu %lu %lu %lu %lu %lu\n", (unsigned long)L3_DUMP_VERSION_EVIDENCE_TEMPERATURE,
         (unsigned long)L3_DUMP_VERSION_EVIDENCE, (unsigned long)L3_FRAME_FLAG_ASSOCIATED,
         (unsigned long)L3_FRAME_FLAG_IQ8_SCANNED, (unsigned long)L3_CAPTURE_FLAG_IQ8_SCAN,
         (unsigned long)L3_FRAME_NO_STAMP);
  return 0;
}
"""


def test_c_struct_sizes_and_constants_match_the_python_parser(tmp_path):
    sizes, constants = _run_c(FORMAT_PROBE, tmp_path).splitlines()

    assert sizes.split() == [
        str(dump.CAPTURE_EVIDENCE.size),
        str(dump.FRAME_EVIDENCE.size),
        str(dump.DUMP_TRAILER.size),
        "ILE1",
        "ILT1",
    ]
    assert [int(value) for value in constants.split()] == [
        dump.DUMP_VERSION_EVIDENCE_TEMPERATURE,
        dump.DUMP_VERSION_EVIDENCE,
        dump.FRAME_FLAG_ASSOCIATED,
        dump.FRAME_FLAG_IQ8_SCANNED,
        dump.CAPTURE_FLAG_IQ8_SCAN,
        dump.FRAME_NO_STAMP,
    ]


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
