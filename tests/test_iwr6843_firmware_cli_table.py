"""Firmware contract for the IWR6843 CLI command table (firmware/iwr6843/l3_cli_table.h).

The table is compiled on the host against a stub of the SDK's CLI_Cfg, for each
feature-flag set, then run through a copy of CLI_open's count-and-append-help
walk and its by-name dispatch. Each stub handler reports its own name, so a
pass proves the registered names, their order and which handler a name reaches.
Builds only this fork's headers, never TI's SDK. Skips without a C compiler: it
uses $CC, then cc/gcc/clang on PATH, then ``python -m ziglang cc`` if installed.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIRMWARE_DIR = Path(__file__).parents[1] / "firmware" / "iwr6843"
FIRMWARE = FIRMWARE_DIR / "l3_dump.c"
TABLE = FIRMWARE_DIR / "l3_cli_table.h"

# The flags the production build passes (firmware/Makefile build-native) that
# the table reads; N_TX, SNAPSHOT_DUMP and HWA_CHAINED_SNAPSHOT_RING do not
# change it.
PRODUCTION = (
    "ENABLE_HWA_SMOKE",
    "CONFIGURABLE_CAPTURE",
    "HYBRID_CADENCE_CAPTURE",
    "L3_RING_IQ8",
    "L3_IQ8_EDMA_PACK",
)
# Without the hybrid cadence there is no capture evidence (L3_CAPTURE_EVIDENCE).
EVIDENCE_OFF = tuple(flag for flag in PRODUCTION if flag != "HYBRID_CADENCE_CAPTURE")

HANDLERS = {
    "sensorStart": "l3_cli_sensorStart",
    "sensorStop": "l3_cli_sensorStop",
    "l3dump": "l3_cli_dump",
    "stats": "l3_cli_stats",
    "hwastats": "l3_cli_hwaStats",
    "hwatest": "l3_cli_hwaTest",
    "hwareal": "l3_cli_hwaReal",
    "captureCfg": "l3_cli_captureCfg",
    "phaseCaptureCfg": "l3_cli_phaseCaptureCfg",
    "captureFormat": "l3_cli_captureFormat",
    "iq8Scale": "l3_cli_iq8Scale",
    "perfstats": "l3_cli_perfStats",
    "l3bench": "l3_cli_bench",
}

PRODUCTION_ORDER = [
    "sensorStart",
    "sensorStop",
    "l3dump",
    "stats",
    "hwastats",
    "hwatest",
    "hwareal",
    "captureCfg",
    "phaseCaptureCfg",
    "captureFormat",
    "iq8Scale",
    "perfstats",
    "l3bench",
]


def _compiler() -> list[str]:
    if os.environ.get("CC"):
        return shlex.split(os.environ["CC"], posix=os.name != "nt")
    for name in ("cc", "gcc", "clang"):
        found = shutil.which(name)
        if found:
            return [found]
    if importlib.util.find_spec("ziglang") is not None:
        return [sys.executable, "-m", "ziglang", "cc"]
    pytest.skip("no host C compiler ($CC, cc, gcc, clang or ziglang) to build the CLI table")
    raise AssertionError  # unreachable: pytest.skip raises


def _evidence_derivation() -> str:
    """l3_dump.c's own rule for L3_CAPTURE_EVIDENCE, so the harness derives it the same way."""
    source = FIRMWARE.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = re.search(
        r"#if defined\(CONFIGURABLE_CAPTURE\) && defined\(HYBRID_CADENCE_CAPTURE\)\n"
        r"#define L3_CAPTURE_EVIDENCE 1\n#endif\n",
        source,
    )
    assert match, "l3_dump.c no longer derives L3_CAPTURE_EVIDENCE as this harness expects"
    return match.group(0)


def _harness() -> str:
    stubs = "\n".join(
        f"static int32_t {fn}(int32_t argc, char *argv[]) "
        f'{{ (void)argc; (void)argv; gRan = "{fn}"; return 0; }}'
        for fn in sorted(set(HANDLERS.values()))
    )
    return f"""#include <stdint.h>
#include <stdio.h>
#include <string.h>
{_evidence_derivation()}
/* Stand-ins for the SDK's ti/utils/cli/cli.h; not the SDK. */
#ifndef CLI_MAX_CMD
#define CLI_MAX_CMD 32
#endif
typedef int32_t (*CLI_CmdHandler)(int32_t argc, char *argv[]);
typedef struct {{ char *cmd; char *helpString; CLI_CmdHandler cmdHandlerFxn; }} CLI_CmdTableEntry;
typedef struct {{ CLI_CmdTableEntry tableEntry[CLI_MAX_CMD]; }} CLI_Cfg;

static const char *gRan;
static int32_t CLI_help(int32_t argc, char *argv[]) {{ (void)argc; (void)argv; gRan = "CLI_help"; return 0; }}
{stubs}

#include "l3_cli_table.h"

int main(void)
{{
    static CLI_Cfg cfg;
    uint32_t used = l3_cliRegister(&cfg);
    uint32_t count = 0U, index, probe;
    printf("used %u expected %u\\n", (unsigned)used, (unsigned)L3_CLI_COUNT);
    /* CLI_open (src/cli.c): count to the first NULL, then append "help". */
    for (index = 0U; index < CLI_MAX_CMD; index++) {{
        if (cfg.tableEntry[index].cmd == NULL) break;
        count++;
    }}
    for (index = count; index < CLI_MAX_CMD; index++) {{
        if (cfg.tableEntry[index].cmd != NULL) printf("orphan %u %s\\n", (unsigned)index, cfg.tableEntry[index].cmd);
    }}
    if (count >= CLI_MAX_CMD) {{ printf("no-room-for-help\\n"); return 1; }}
    cfg.tableEntry[count].cmd = "help";
    cfg.tableEntry[count].cmdHandlerFxn = CLI_help;
    count++;
    /* CLI_task: the first entry whose name matches runs. */
    for (index = 0U; index < count; index++) {{
        for (probe = 0U; probe < count; probe++) {{
            if (strcmp(cfg.tableEntry[probe].cmd, cfg.tableEntry[index].cmd) == 0) break;
        }}
        gRan = "none";
        cfg.tableEntry[probe].cmdHandlerFxn(0, NULL);
        printf("cmd %u %s %s\\n", (unsigned)index, cfg.tableEntry[index].cmd, gRan);
    }}
    return 0;
}}
"""


def _compile(tmp_path: Path, flags, *, extra: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    program = tmp_path / "cli_table.c"
    program.write_text(_harness(), encoding="utf-8")
    exe = tmp_path / ("cli_table.exe" if os.name == "nt" else "cli_table")
    return subprocess.run(
        [
            *_compiler(),
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-unused-function",  # stubs exist for every handler; a flag set registers some
            *(f"-D{flag}=1" for flag in flags),
            *extra,
            "-I",
            str(FIRMWARE_DIR),
            str(program),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _registered(tmp_path: Path, flags) -> list[tuple[int, str, str]]:
    built = _compile(tmp_path, flags)
    assert built.returncode == 0, built.stderr
    exe = tmp_path / ("cli_table.exe" if os.name == "nt" else "cli_table")
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.splitlines()
    used = out[0].split()
    assert used[1] == used[3], f"l3_cliRegister used {used[1]} slots, L3_CLI_COUNT says {used[3]}"
    assert not [line for line in out if line.startswith("orphan")], out
    rows = []
    for line in out[1:]:
        _, index, name, ran = line.split()
        rows.append((int(index), name, ran))
    return rows


def _expected(flags) -> list[str]:
    on = set(flags)
    names = ["sensorStart", "sensorStop", "l3dump", "stats"]
    if "ENABLE_HWA_SMOKE" in on:
        names += ["hwastats", "hwatest", "hwareal"]
    if "CONFIGURABLE_CAPTURE" in on:
        names += ["captureCfg", "phaseCaptureCfg"]
        if "L3_RING_IQ8" in on:
            names.append("captureFormat")
            if "L3_IQ8_EDMA_PACK" in on:
                names.append("iq8Scale")
        if "HYBRID_CADENCE_CAPTURE" in on:
            names += ["perfstats", "l3bench"]
    return names


MATRIX = {
    "production": PRODUCTION,
    "evidence_off": EVIDENCE_OFF,
    "no_hwa_smoke": tuple(flag for flag in PRODUCTION if flag != "ENABLE_HWA_SMOKE"),
    "iq16_only": ("ENABLE_HWA_SMOKE", "CONFIGURABLE_CAPTURE", "HYBRID_CADENCE_CAPTURE"),
    "minimal": (),
}


@pytest.mark.parametrize("name", sorted(MATRIX))
def test_every_enabled_command_registers_contiguously_and_dispatches_to_its_handler(tmp_path, name):
    flags = MATRIX[name]
    rows = _registered(tmp_path, flags)
    expected = _expected(flags)

    assert [row[1] for row in rows] == [*expected, "help"]
    assert [row[0] for row in rows] == list(range(len(expected) + 1))
    assert [row[2] for row in rows] == [HANDLERS[n] for n in expected] + ["CLI_help"]


def test_the_production_build_keeps_its_slot_order(tmp_path):
    rows = _registered(tmp_path, PRODUCTION)

    assert [row[1] for row in rows] == [*PRODUCTION_ORDER, "help"]


def test_a_disabled_feature_leaves_no_gap(tmp_path):
    """Without HWA smoke, capture commands move up: nothing sits past a NULL."""
    rows = _registered(tmp_path, MATRIX["no_hwa_smoke"])

    assert rows[4][1] == "captureCfg"
    assert rows[-2][1] == "l3bench"


def test_the_table_refuses_to_build_without_a_slot_for_help(tmp_path):
    full = len(PRODUCTION_ORDER)

    refused = _compile(tmp_path, PRODUCTION, extra=(f"-DCLI_MAX_CMD={full}",))
    fits = _compile(tmp_path, PRODUCTION, extra=(f"-DCLI_MAX_CMD={full + 1}",))

    assert refused.returncode != 0
    assert "CLI_open needs one free slot for its built-in help" in refused.stderr
    assert fits.returncode == 0, fits.stderr


def test_l3_dump_registers_through_the_shared_table_only():
    source = FIRMWARE.read_text(encoding="utf-8")

    assert "tableEntry[" not in source
    include = source.index('#include "l3_cli_table.h"')
    register = source.index("(void)l3_cliRegister(&cliCfg);")
    assert register < source.index("CLI_open(&cliCfg);")
    for handler in sorted(set(HANDLERS.values())):
        definition = re.search(rf"^(?:static )?int32_t {handler}\(int32_t argc", source, re.M)
        assert definition, handler
        assert definition.start() < include, f"{handler} is defined after the table include"


def test_registration_is_inline_so_the_all_off_image_is_unchanged():
    """A plain static function shifted -O3's inlining elsewhere (l3_iq8EdmaDoneCB)."""
    assert "static inline uint32_t l3_cliRegister(CLI_Cfg *cfg)" in TABLE.read_text(
        encoding="utf-8"
    )
