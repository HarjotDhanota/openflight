"""Exercise the actual pre-header dump path with simulated SDK calls."""

import subprocess

import pytest

from tests.test_iwr6843_two_round_firmware import ROOT, build_native_probe


@pytest.mark.parametrize("enabled,skip_temperature", [(False, False), (True, False), (True, True)])
def test_preheader_stage_order_and_failure_boundaries(tmp_path, enabled, skip_temperature):
    source = (ROOT / "firmware/iwr6843/l3_dump.c").read_text(encoding="utf-8")
    start = source.index("#if defined(L3_DUMP_SKIP_TEMPERATURE)")
    end = source.index("#ifdef L3_CAPTURE_EVIDENCE\nstatic uint32_t gFrameSeq", start)
    sections = [source[start:end]]
    for name, next_name in [
        ("static int32_t l3_finishCaptureStop(void)\n{", "/* Shot dumps preserve"),
        ("static int32_t l3_stopCaptureAtBoundary(void)\n{", "/* sensorStop only"),
        (
            "int32_t l3_cli_dump(int32_t argc, char *argv[])\n{",
            "#ifdef CONFIGURABLE_CAPTURE\n    for (i = 0U; i < actualPre",
        ),
    ]:
        start = source.index(name)
        sections.append(source[start : source.index(next_name, start)])
    sections[-1] += (
        "    (void)i; (void)dumpCancelled; (void)headerTick; (void)oldestPre;\n    return 0;\n}\n"
    )
    (tmp_path / "dump_stage.inc").write_text("\n".join(sections), encoding="utf-8")
    flags = ["-DL3_DUMP_STAGE_TRACE=1"] if enabled else []
    if skip_temperature:
        flags.append("-DL3_DUMP_SKIP_TEMPERATURE=1")
    docker, executable = build_native_probe(tmp_path, "iwr_dump_stage_probe.c", flags)
    for scenario in range(5):
        result = subprocess.run(
            [*docker, str(executable), str(scenario)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "flags,message",
    [
        (["-DL3_DUMP_SKIP_TEMPERATURE=1"], "temperature isolation requires"),
        (
            ["-DL3_DUMP_SKIP_TEMPERATURE=1", "-DL3_DUMP_STAGE_TRACE=1", "-DL3_TWO_ROUND=1"],
            "clock-only diagnostic",
        ),
    ],
)
def test_temperature_isolation_rejects_untraced_or_two_round_builds(tmp_path, flags, message):
    source = (ROOT / "firmware/iwr6843/l3_dump.c").read_text(encoding="utf-8")
    start = source.index("#if defined(L3_DUMP_SKIP_TEMPERATURE)")
    end = source.index("#ifdef L3_CAPTURE_EVIDENCE\nstatic uint32_t gFrameSeq", start)
    (tmp_path / "dump_stage.inc").write_text(source[start:end], encoding="utf-8")
    with pytest.raises(AssertionError, match=message):
        build_native_probe(tmp_path, "iwr_dump_stage_probe.c", flags)
