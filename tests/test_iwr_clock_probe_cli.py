"""Clock probe collection and audit using synthetic firmware/host clocks."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from openflight.iwr6843.clock_evidence import recording_observations
from tests.test_iwr6843_clock_evidence import BOOT, BUILD, reply_text

SCRIPT = Path(__file__).parents[1] / "scripts/hardware-test/iwr_clock_probe.py"
SPEC = importlib.util.spec_from_file_location("iwr_clock_probe", SCRIPT)
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class SyntheticRadar:
    def __init__(self, action=None):
        self.commands = []
        self.action = action

    def cmd(self, line, window):
        self.commands.append((line, window))
        _, hi, lo, seq = line.split()
        index = int(seq, 16)
        result = reply_text(hi + lo, index, rx=1000 + index * 1000, tx=1400 + index * 1000)
        return self.action(result, index) if self.action else result


def collect(radar=None, count=2, boot=None, nonce=None):
    radar = radar or SyntheticRadar()
    rows = []
    counter = iter(range(900_000_000, 1_000_000_000, 1_000_000))
    nonces = iter(f"{seq + 1:016x}" for seq in range(count))
    sleeps = []
    cli.record_probes(
        radar,
        rows.append,
        count=count,
        interval_s=0.1,
        now=lambda: next(counter),
        nonce=nonce or (lambda: next(nonces)),
        sleep=sleeps.append,
        boot=boot or (lambda: "synthetic-host-boot"),
    )
    return rows, radar, sleeps


def test_collector_sends_only_identified_probe_commands_and_records_whole_brackets():
    rows, radar, sleeps = collect()
    assert [command for command, _ in radar.commands] == [
        "clockProbe 00000000 00000001 00000000",
        "clockProbe 00000000 00000002 00000001",
    ]
    assert sleeps == [0.1]
    assert [row["type"] for row in rows] == ["start", "probe", "probe", "end"]
    assert rows[-1]["probe_count"] == 2
    assert rows[-1]["error"] is None
    obs = recording_observations(rows)
    assert obs[0].before_ns == 901_000_000
    assert obs[0].after_ns == 902_000_000
    assert obs[1].before_ns == 903_000_000


@pytest.mark.parametrize("mode", ["unsupported", "reset", "io_error"])
def test_collector_retains_failure_and_stops_without_reconfiguring(mode):
    def action(text, index):
        if mode == "unsupported":
            return "'clockProbe' is not recognized as a CLI command\nl3dump:/>"
        if mode == "reset":
            return text.replace(f"boot={BOOT}", "boot=0000000000000010") if index else text
        raise OSError("synthetic transport failure")

    radar = SyntheticRadar(action)
    rows = []
    clock = iter(range(1000, 10000, 100))
    nonces = iter([BOOT, "0000000000000002", "0000000000000003"])
    with pytest.raises((ValueError, OSError)):
        cli.record_probes(
            radar,
            rows.append,
            count=3,
            interval_s=0,
            now=lambda: next(clock),
            nonce=lambda: next(nonces),
            sleep=lambda _: None,
            boot=lambda: "host-test",
        )
    assert rows[-1]["type"] == "end" and rows[-1]["error"]
    assert len(radar.commands) == (2 if mode == "reset" else 1)
    if mode == "unsupported":
        assert "not recognized" in rows[1]["reply_text"]
    with pytest.raises(ValueError):
        recording_observations(rows)


def test_collector_detects_reused_nonce_before_sending_it_again():
    radar = SyntheticRadar()
    with pytest.raises(ValueError, match="reused"):
        collect(radar, nonce=lambda: BOOT)
    assert len(radar.commands) == 1


@pytest.mark.parametrize("count", [0, -1, True, 1001])
def test_invalid_count_sends_no_commands(count):
    radar = SyntheticRadar()
    with pytest.raises(ValueError):
        collect(radar, count=count)
    assert radar.commands == []


@pytest.mark.parametrize(
    "change",
    [
        "no_end",
        "error_end",
        "host_reset",
        "connection",
        "missing_probe",
        "seq",
        "reordered",
        "nonce_reuse",
        "device_reset",
        "outside_bracket",
        "missing_start",
        "duplicate_end",
        "boot_missing",
        "boolean_count",
        "missing_error_status",
    ],
)
def test_offline_audit_refuses_incomplete_or_mixed_recordings(change):
    rows, _, _ = collect()
    if change == "no_end":
        rows.pop()
    elif change == "error_end":
        rows[-1]["error"] = "OSError"
    elif change == "host_reset":
        rows[-1]["boot_id"] = "another-host-boot"
    elif change == "connection":
        rows[-1]["connection_id"] = "another-connection"
    elif change == "missing_probe":
        rows.pop(1)
    elif change == "seq":
        rows[2]["seq"] = 4
    elif change == "reordered":
        rows[1], rows[2] = rows[2], rows[1]
    elif change == "nonce_reuse":
        rows[2]["request"] = rows[1]["request"]
    elif change == "device_reset":
        rows[2]["reply_text"] = rows[2]["reply_text"].replace(
            f"boot={BOOT}", "boot=0000000000000002"
        )
    elif change == "outside_bracket":
        rows[2]["after_ns"] = rows[-1]["mono_ns"] + 1
    elif change == "missing_start":
        rows.pop(0)
    elif change == "duplicate_end":
        rows.append(copy.deepcopy(rows[-1]))
    elif change == "boot_missing":
        rows[0]["boot_id"] = None
    elif change == "boolean_count":
        rows[-1]["probe_count"] = True
    else:
        rows[-1].pop("error")
    result = cli.audit(
        rows, rate_hz_bounds=(199_980_000, 200_020_000), max_distance_ns=1_000_000_000
    )
    assert result["status"] == "unavailable"
    assert result["target_mapping"] is None
    assert result["physical_timing_qualified"] is False


def test_offline_audit_reports_bounds_and_no_rf_accuracy():
    rows, _, _ = collect()
    result = cli.audit(
        rows, rate_hz_bounds=(200_000_000, 200_000_000), max_distance_ns=1_000_000_000
    )
    assert result["status"] == "conditional_software_counter_only"
    assert result["probe_envelopes"][0]["host_round_trip_ns"] == 1_000_000
    assert result["probe_envelopes"][0]["interval_width_ns"] == 998_000
    assert result["rf_acquisition_interval_ns"] is None
    assert result["physical_timing_qualified"] is False


def test_target_audit_rejects_inconsistent_exchanges_instead_of_averaging():
    rows, _, _ = collect()
    result = cli.audit(
        rows,
        rate_hz_bounds=(200_000_000, 200_000_000),
        max_distance_ns=1_000_000_000,
        target={"tick": 2000, "build": BUILD, "boot": BOOT},
    )
    assert result["status"] == "unavailable"
    assert "no common intersection" in result["reason"]


def test_cli_audit_hashes_source_and_refuses_overwrite(tmp_path):
    rows, _, _ = collect()
    source, output = tmp_path / "probes.jsonl", tmp_path / "audit.json"
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))
    args = [
        "audit",
        str(source),
        "--output",
        str(output),
        "--rate-min-hz",
        "200000000",
        "--rate-max-hz",
        "200000000",
        "--max-distance-ns",
        "1000000000",
    ]
    assert cli.main(args) == 0
    report = json.loads(output.read_text())
    assert len(report["source_sha256"]) == 64
    assert cli.main(args) == 2
    assert json.loads(output.read_text()) == report


def test_cli_returns_failure_for_truncated_recording(tmp_path):
    rows, _, _ = collect()
    source, output = tmp_path / "probes.jsonl", tmp_path / "audit.json"
    source.write_text("".join(json.dumps(row) + "\n" for row in rows[:-1]))
    assert (
        cli.main(
            [
                "audit",
                str(source),
                "--output",
                str(output),
                "--rate-min-hz",
                "200000000",
                "--rate-max-hz",
                "200000000",
                "--max-distance-ns",
                "1000000000",
            ]
        )
        == 1
    )
    assert json.loads(output.read_text())["status"] == "unavailable"
