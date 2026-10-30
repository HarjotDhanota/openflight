"""Known-answer checks for sample-clock diagnostics and bounded collector cleanup."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/hardware-test/ops_sample_clock.py"
SPEC = importlib.util.spec_from_file_location("ops_sample_clock", SCRIPT)
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


def raw_capture(**changes):
    data = {
        "sample_time": "10.000",
        "trigger_time": "10.026",
        "I": [2048] * 4096,
        "Q": [2049] * 4096,
    }
    data.update(changes)
    return "\n".join(json.dumps({key: value}) for key, value in data.items()).encode()


def test_grid_uses_first_to_last_sample_span_and_preserves_device_strings():
    result = bench.dump_summary(raw_capture(), 6)
    assert result["nominal_first_to_last_ns"] == 136_500_000
    assert result["nominal_buffer_periods_ns"] == 136_533_334
    assert result["device_trigger_minus_sample_ns"] == 26_000_000
    assert result["nominal_pretrigger_ns"] == 25_600_000
    assert result["sample_mapping"] is None
    assert result["sample_time_text"] == "10.000"


def test_numeric_device_timestamps_use_exact_decimal_parsing():
    raw = raw_capture().replace(b'"10.000"', b"6808.427").replace(b'"10.026"', b"6808.453")
    result = bench.dump_summary(raw, 6)
    assert result["device_trigger_minus_sample_ns"] == 26_000_000
    assert result["sample_time_text"] == "6808.427"


@pytest.mark.parametrize(
    "change",
    [
        {"I": [0]},
        {"Q": [1] * 4095},
        {"sample_time": "nan"},
        {"trigger_time": "9"},
        {"Q": [True] * 4096},
    ],
)
def test_rejects_incomplete_or_invalid_sample_evidence(change):
    with pytest.raises(ValueError):
        bench.dump_summary(raw_capture(**change), 6)


def test_duplicate_timestamp_is_not_silently_last_wins():
    with pytest.raises(ValueError):
        bench.dump_summary(b'{"sample_time":"10.1"}\n' + raw_capture(), 6)


def test_finite_bench_rounds_stop_and_close_after_capture_failure(tmp_path):
    calls = []

    class Radar:
        serial = None

        def connect(self):
            calls.append("connect")

        def validate_internal_trigger_firmware(self):
            return "1.3.3"

        def configure_for_internal_speed_trigger(self, **kwargs):
            calls.append(kwargs)

        def _send_command(self, text):
            calls.append(text)
            return '{"PowerReduction":3}' if text == "P3" else ""

        def rearm_internal_speed_trigger(self):
            calls.append("arm")
            return True

        def trigger_capture(self, **kwargs):
            raise RuntimeError("read failed")

        def disconnect(self):
            calls.append("closed")

    with pytest.raises(RuntimeError, match="read failed"):
        bench.collect(Radar(), tmp_path, lambda *args: None, sleep=lambda _: None)
    assert calls[-2:] == ["PI", "closed"]
    assert not (tmp_path / "capture-01.raw").exists()


def test_no_hardware_access_is_default(monkeypatch, capsys):
    monkeypatch.setattr(bench, "execute", lambda _: pytest.fail("device opened"))
    assert bench.main([]) == 0
    assert json.loads(capsys.readouterr().out)["devices_opened"] is False


def test_partial_power_ack_stops_before_arm(tmp_path):
    radar = SimpleNamespace(
        serial=None,
        connect=lambda: None,
        validate_internal_trigger_firmware=lambda: "1.3.3",
        configure_for_internal_speed_trigger=lambda **_: None,
        _send_command=lambda _: "",
        rearm_internal_speed_trigger=lambda: pytest.fail("armed without acknowledgement"),
        disconnect=lambda: None,
    )
    with pytest.raises(ValueError, match="P3"):
        bench.collect(radar, tmp_path, lambda *args: None, sleep=lambda _: None)


def successful_radar(payload, calls):
    class Port:
        in_waiting = len(payload)

        def read(self, _):
            return payload

    class Radar:
        serial = Port()

        def connect(self):
            pass

        def validate_internal_trigger_firmware(self):
            return "1.3.3"

        def configure_for_internal_speed_trigger(self, **settings):
            calls.append(settings["pre_trigger_segments"])

        def _send_command(self, text):
            return '{"PowerReduction":3}' if text == "P3" else ""

        def rearm_internal_speed_trigger(self):
            return True

        def trigger_capture(self, **_):
            return self.serial.read(self.serial.in_waiting).decode("ascii", errors="ignore")

        def disconnect(self):
            calls.append("closed")

    return Radar()


def test_six_controls_keep_exact_uart_bytes_and_declared_order(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    calls, journal = [], []
    payload = raw_capture()
    result = bench.collect(
        successful_radar(payload, calls),
        tmp_path,
        lambda name, row: journal.append((name, row)),
        sleep=lambda _: None,
    )
    assert calls == [6, 16, 26, 26, 16, 6, "closed"]
    assert len(result) == 6
    assert all((tmp_path / row["raw_capture"]).read_bytes() == payload for row in result)
    assert all(row["configured_request_source"] == "software" for row in result)
    assert sum(name == "serial.jsonl" for name, _ in journal) == 6


@pytest.mark.parametrize("extra", [b"\xff", b'\n{"trigger_velocity_detected":25}\n'])
def test_driver_decode_loss_or_competing_notice_preserves_raw_and_fails(
    tmp_path, monkeypatch, extra
):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    payload = raw_capture() + extra
    calls = []
    with pytest.raises(ValueError):
        bench.collect(
            successful_radar(payload, calls), tmp_path, lambda *args: None, sleep=lambda _: None
        )
    assert (tmp_path / "capture-01.raw").read_bytes() == payload
    assert calls[-1] == "closed"


def test_connect_failure_sends_no_cleanup_commands(tmp_path):
    def failed_connect():
        raise RuntimeError("no connection")

    radar = SimpleNamespace(
        connect=failed_connect,
        disconnect=lambda: None,
        _send_command=lambda _: pytest.fail("write without connection"),
    )
    with pytest.raises(RuntimeError, match="no connection"):
        bench.collect(radar, tmp_path, lambda *args: None)
