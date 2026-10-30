"""A requested software trigger shares the existing dump reader and preserves bytes."""

import base64
import threading

import pytest
import serial

from openflight.ops243 import OPS243Radar

RAW = b'{"sample_time":"1"}\r\n{"trigger_time":"2"}\r\n{"I":[1]}\r\n{"Q":[2]}\r\n'


class RequestedCapturePort:
    is_open = True

    def __init__(self, chunks=(), write_result=3, flush_error=False):
        self.chunks = list(chunks)
        self.writes = []
        self.reader_threads = set()
        self.write_result = write_result
        self.flush_error = flush_error

    @property
    def in_waiting(self):
        return len(self.chunks[0]) if self.chunks else 0

    def read(self, size):
        self.reader_threads.add(threading.get_ident())
        chunk = self.chunks.pop(0)
        assert size == len(chunk)
        return chunk

    def write(self, command):
        self.writes.append((command, threading.get_ident()))
        if self.write_result == len(command):
            self.chunks.extend([RAW[:8], RAW[8:]])
        return self.write_result

    def flush(self):
        if self.flush_error:
            raise serial.SerialException("flush failed")

    def reset_input_buffer(self):
        raise AssertionError("the active reader must not discard input")


def make_radar(port):
    radar = OPS243Radar()
    radar.serial = port
    return radar


def requested():
    event = threading.Event()
    event.set()
    return event


def test_request_writes_once_on_reader_and_keeps_original_bytes():
    port = RequestedCapturePort()
    radar = make_radar(port)
    assert (
        radar.wait_for_hardware_trigger(timeout=0.2, software_trigger_event=requested())
        == RAW.decode()
    )
    assert port.writes == [(b"S!\r", threading.get_ident())]
    assert port.reader_threads == {threading.get_ident()}
    row = radar.last_hardware_capture_evidence
    assert base64.b64decode(row["raw_capture_base64"]) == RAW
    command = row["software_trigger_command"]
    assert command["started_mono_ns"] <= command["write_return_mono_ns"]
    assert command["write_return_mono_ns"] <= command["flush_return_mono_ns"]
    assert command["bytes_written"] == 3
    assert command["error"] is None
    assert row["physical_timing_qualified"] is False


@pytest.mark.parametrize("chunks", [[RAW], [RAW[:5], RAW[5:]]])
def test_existing_dump_wins_without_sending_another_trigger(chunks):
    port = RequestedCapturePort(chunks)
    radar = make_radar(port)
    assert (
        radar.wait_for_hardware_trigger(timeout=0.2, software_trigger_event=requested())
        == RAW.decode()
    )
    assert port.writes == []
    assert radar.last_hardware_capture_evidence["software_trigger_command"] is None


def test_cancelled_idle_wait_never_sends_requested_trigger():
    port = RequestedCapturePort()
    radar = make_radar(port)
    assert (
        radar.wait_for_hardware_trigger(
            timeout=0.2, cancel_event=requested(), software_trigger_event=requested()
        )
        == ""
    )
    assert port.writes == []
    assert radar.last_hardware_capture_evidence["outcome"] == "cancelled"


@pytest.mark.parametrize("write_result,flush_error", [(0, False), (2, False), (3, True)])
def test_failed_command_is_not_retried_or_reported_as_confirmed(write_result, flush_error):
    port = RequestedCapturePort(write_result=write_result, flush_error=flush_error)
    radar = make_radar(port)
    with pytest.raises(serial.SerialException):
        radar.wait_for_hardware_trigger(timeout=0.2, software_trigger_event=requested())
    assert len(port.writes) == 1
    row = radar.last_hardware_capture_evidence
    assert row["outcome"] == "error"
    assert row["software_trigger_command"]["bytes_written"] == write_result
    assert row["software_trigger_command"]["error"] == "SerialException"
    assert row["physical_timing_qualified"] is False


def test_request_arriving_during_wait_wakes_single_owner():
    port = RequestedCapturePort()
    radar = make_radar(port)
    event = threading.Event()
    timer = threading.Timer(0.02, event.set)
    timer.start()
    try:
        assert (
            radar.wait_for_hardware_trigger(timeout=0.3, software_trigger_event=event)
            == RAW.decode()
        )
    finally:
        timer.join()
    assert port.writes == [(b"S!\r", threading.get_ident())]


def test_no_request_does_not_issue_command_or_reuse_previous_command():
    port = RequestedCapturePort()
    radar = make_radar(port)
    radar.wait_for_hardware_trigger(timeout=0.2, software_trigger_event=requested())
    assert (
        radar.wait_for_hardware_trigger(timeout=0.01, software_trigger_event=threading.Event())
        == ""
    )
    assert len(port.writes) == 1
    assert radar.last_hardware_capture_evidence["software_trigger_command"] is None


def test_cli_prefix_is_retained_before_requested_capture():
    prefix = b'{"PowerReduction":"3"}\r\n'
    port = RequestedCapturePort([prefix])
    radar = make_radar(port)
    assert (
        radar.wait_for_hardware_trigger(timeout=0.2, software_trigger_event=requested())
        == RAW.decode()
    )
    row = radar.last_hardware_capture_evidence
    assert base64.b64decode(row["preamble_base64"]) == prefix
    assert len(port.writes) == 1


@pytest.mark.parametrize("prefix", [b'{"sample', b'{"trigger_veloc', b'{"I":[1]}', b'{"Q":[2]}'])
def test_incomplete_or_orphan_capture_blocks_an_extra_command(prefix):
    port = RequestedCapturePort([prefix])
    radar = make_radar(port)
    assert radar.wait_for_hardware_trigger(timeout=0.03, software_trigger_event=requested()) == ""
    assert port.writes == []
    assert base64.b64decode(radar.last_hardware_capture_evidence["preamble_base64"]) == prefix


def test_native_trigger_notice_without_dump_blocks_requested_command():
    notice = b'{"trigger_velocity_detected":"1.2"}\r\n'
    port = RequestedCapturePort([bytes([value]) for value in notice])
    radar = make_radar(port)
    assert radar.wait_for_hardware_trigger(timeout=0.6, software_trigger_event=requested()) == ""
    assert port.writes == []
    assert radar.last_hardware_capture_evidence["notifications"][0]["payload"] == {
        "trigger_velocity_detected": "1.2"
    }


def test_uncertain_recovery_does_not_send_a_requested_trigger():
    port = RequestedCapturePort()
    radar = make_radar(port)
    radar._hardware_trigger_recovery_required = True
    assert radar.wait_for_hardware_trigger(timeout=0.02, software_trigger_event=requested()) == ""
    assert port.writes == []


def test_cancellation_after_command_keeps_arriving_dump():
    cancel = threading.Event()
    port = RequestedCapturePort()
    original_write = port.write

    def write(command):
        result = original_write(command)
        cancel.set()
        return result

    port.write = write
    radar = make_radar(port)
    assert (
        radar.wait_for_hardware_trigger(
            timeout=0.2, cancel_event=cancel, software_trigger_event=requested()
        )
        == RAW.decode()
    )
    assert radar.last_hardware_capture_evidence["outcome"] == "capture"
