"""The OPS243 must keep dumping when a GATE edge races its re-arm.

The re-arm (PA, S#16, PA) restarts sampling on the first PA, so an edge during it starts a
dump; flushing the input then lost the dump's start marker and nothing re-armed afterwards.
"""

import json
import threading
import time

from spin_synth import synth_capture

from openflight.ops243 import OPS243Radar
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.trigger import SoundTrigger


def _dump(i_samples=(2168, 2187, 2155, 2154), q_samples=(2048, 2050, 2047, 2049)) -> bytes:
    lines = [
        '{"sample_time": "964.003"}',
        '{"trigger_time": "964.105"}',
        json.dumps({"I": [int(v) for v in i_samples]}),
        json.dumps({"Q": [int(v) for v in q_samples]}),
    ]
    return "\r\n".join(lines).encode("ascii")


# Short, but slow enough that the re-arm's 0.35 s of command sleeps ends mid-dump.
_PADDED_DUMP = _dump(i_samples=[2048] * 300, q_samples=[2048] * 300)


class FakeOps:
    """OPS243 UART double: edges start dumps only while armed; a mid-dump PA is lost."""

    BYTES_PER_MS = 4.0

    def __init__(self, dump: bytes, *, armed: bool = True, edge_on_pa: int | None = None):
        self.is_open = True
        self.dump = dump
        self.armed = armed
        self.edge_on_pa = edge_on_pa
        self.writes = []
        self.dumps_started = 0
        self._rx = bytearray()
        self._stream = None
        self._pa_count = 0
        self._lock = threading.Lock()

    def _pump(self):
        if self._stream is None:
            return
        started, sent = self._stream
        due = min(len(self.dump), int((time.monotonic() - started) * 1000 * self.BYTES_PER_MS))
        if due > sent:
            self._rx += self.dump[sent:due]
            self._stream = (started, due)
        if due >= len(self.dump):
            self._stream = None

    def edge(self):
        """A rising edge on HOST_INT."""
        with self._lock:
            self._pump()
            if self.armed and self._stream is None:
                self.armed = False
                self._stream = (time.monotonic(), 0)
                self.dumps_started += 1

    @property
    def in_waiting(self):
        with self._lock:
            self._pump()
            return len(self._rx)

    def read(self, size):
        with self._lock:
            self._pump()
            chunk = bytes(self._rx[:size])
            del self._rx[:size]
            return chunk

    def reset_input_buffer(self):
        with self._lock:
            self._pump()
            self._rx.clear()

    def write(self, data):
        self.writes.append(data)
        if data == b"PA":
            with self._lock:
                self._pump()
                self._pa_count += 1
                if self._stream is None:
                    self.armed = True
            if self._pa_count == self.edge_on_pa:
                self.edge()
        return len(data)

    def flush(self):
        pass

    @property
    def pa_count(self):
        return self._pa_count


def _radar(serial_obj) -> OPS243Radar:
    radar = OPS243Radar.__new__(OPS243Radar)
    radar.serial = serial_obj
    radar.last_hardware_trigger_first_byte_timestamp = None
    return radar


def test_dump_started_during_rearm_is_read_not_flushed():
    ops = FakeOps(_PADDED_DUMP, armed=False, edge_on_pa=1)
    radar = _radar(ops)

    radar.rearm_rolling_buffer(16)
    response = radar.wait_for_hardware_trigger(timeout=2.0, dump_grace=2.0)

    assert ops.dumps_started == 1
    assert response == _PADDED_DUMP.decode("ascii")


def test_dump_in_flight_when_the_wait_begins_is_kept():
    ops = FakeOps(_PADDED_DUMP)
    radar = _radar(ops)
    ops.edge()
    time.sleep(0.05)

    response = radar.wait_for_hardware_trigger(timeout=2.0, dump_grace=2.0)

    assert response == _PADDED_DUMP.decode("ascii")


def test_markerless_dump_ends_the_wait_early_as_discarded():
    tail = _PADDED_DUMP[_PADDED_DUMP.index(b'{"I"') :]
    ops = FakeOps(tail)
    radar = _radar(ops)
    ops.edge()
    started = time.monotonic()

    response = radar.wait_for_hardware_trigger(timeout=10.0, dump_grace=2.0)

    assert response == ""
    assert time.monotonic() - started < 3.0
    assert radar.last_hardware_trigger_outcome == "discarded_dump"
    assert radar.last_hardware_trigger_discarded_bytes == len(tail)


def test_sound_trigger_rearms_after_a_timeout():
    # The board is idle after a lost dump: only a PA brings it back.
    ops = FakeOps(_PADDED_DUMP, armed=False)
    radar = _radar(ops)
    trigger = SoundTrigger(pre_trigger_segments=16)

    assert trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=0.2) is None

    assert ops.pa_count >= 1 and ops.armed
    ops.edge()
    assert radar.wait_for_hardware_trigger(timeout=2.0, dump_grace=2.0)


def test_sound_trigger_does_not_rearm_when_cancelled():
    ops = FakeOps(_PADDED_DUMP, armed=False)
    radar = _radar(ops)
    cancel = threading.Event()
    cancel.set()

    assert (
        SoundTrigger().wait_for_trigger(
            radar, RollingBufferProcessor(), timeout=5.0, cancel_event=cancel
        )
        is None
    )
    assert ops.writes == []


def test_sound_trigger_rearms_after_a_discarded_dump():
    tail = _PADDED_DUMP[_PADDED_DUMP.index(b'{"I"') :]
    ops = FakeOps(tail)
    radar = _radar(ops)
    ops.edge()
    trigger = SoundTrigger(pre_trigger_segments=16)

    assert trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=10.0) is None

    assert ops.pa_count >= 1 and ops.armed
    reasons = [diag["reason"] for diag in trigger.drain_diagnostics()]
    assert reasons == ["dump_discarded"]


def test_gate_edge_without_a_dump_is_logged_and_rearmed():
    ops = FakeOps(_PADDED_DUMP, armed=False)
    radar = _radar(ops)
    trigger = SoundTrigger(pre_trigger_segments=16)
    threading.Timer(0.1, lambda: trigger.notify_gate_edge(time.time())).start()
    started = time.monotonic()

    assert trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=10.0) is None

    assert time.monotonic() - started < 2.0
    assert ops.pa_count >= 1 and ops.armed
    diagnostics = trigger.drain_diagnostics()
    assert [diag["reason"] for diag in diagnostics] == ["edge_without_dump"]


def test_gate_edge_that_starts_a_dump_is_not_flagged():
    i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=80.0, amplitude=400.0)
    ops = FakeOps(_dump(i_samples, q_samples))
    ops.BYTES_PER_MS = 400.0
    radar = _radar(ops)
    radar.read_clock_sync = lambda **_kwargs: {"clock_sync_method": "no_valid_reads"}

    trigger = SoundTrigger(pre_trigger_segments=16)

    def edge():
        trigger.notify_gate_edge(time.time())
        ops.edge()

    threading.Timer(0.1, edge).start()

    capture = trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=5.0)

    assert capture is not None
    assert [diag["reason"] for diag in trigger.drain_diagnostics()] == ["accepted"]


def test_gate_edges_from_before_the_wait_do_not_end_it():
    ops = FakeOps(_PADDED_DUMP, armed=True)
    radar = _radar(ops)
    trigger = SoundTrigger(pre_trigger_segments=16)
    trigger.notify_gate_edge(time.time() - 5.0)
    started = time.monotonic()

    assert trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=0.6) is None

    assert time.monotonic() - started >= 0.55
    assert trigger.drain_diagnostics() == []


def test_sound_path_never_flushes_a_dump_already_arriving():
    """Upstream's flush must never run on the sound path: it drops a waiting dump's marker."""
    i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=80.0, amplitude=400.0)
    ops = FakeOps(_dump(i_samples, q_samples))
    ops.BYTES_PER_MS = 400.0
    radar = _radar(ops)
    radar.read_clock_sync = lambda **_kwargs: {"clock_sync_method": "no_valid_reads"}
    flushes_before_read = []
    dump_read = []
    real_reset = ops.reset_input_buffer
    real_wait = radar.wait_for_hardware_trigger

    def spy_reset():
        if not dump_read:
            flushes_before_read.append(ops.in_waiting)
        real_reset()

    def spy_wait(*args, **kwargs):
        response = real_wait(*args, **kwargs)
        dump_read.append(response)
        return response

    ops.reset_input_buffer = spy_reset
    radar.wait_for_hardware_trigger = spy_wait
    ops.edge()
    time.sleep(0.05)
    assert ops.in_waiting > 0

    trigger = SoundTrigger(pre_trigger_segments=16)
    capture = trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=5.0)

    assert flushes_before_read == []
    assert capture is not None
    assert [diag["reason"] for diag in trigger.drain_diagnostics()] == ["accepted"]
