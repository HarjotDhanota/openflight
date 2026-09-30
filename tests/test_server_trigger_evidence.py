"""Phase 7 workstream T: the server's side of the shared sound-trigger edge."""

from types import SimpleNamespace

from openflight import server
from openflight.rolling_buffer.trigger import SoundTrigger


class _ObservedMonitor:
    def __init__(self):
        self.observers = []

    def add_trigger_observer(self, observer):
        if observer not in self.observers:
            self.observers.append(observer)

    def remove_trigger_observer(self, observer):
        if observer in self.observers:
            self.observers.remove(observer)


def test_sound_trigger_hears_bcm17_edges_while_the_monitor_runs(monkeypatch):
    """P7-1: the OPS wait learns of each GATE edge, so a missed dump is caught."""
    iwr_monitor = _ObservedMonitor()
    trigger = SoundTrigger()
    monkeypatch.setattr(server, "iwr6843_runtime", SimpleNamespace(capture_monitor=iwr_monitor))
    monkeypatch.setattr(server, "monitor", SimpleNamespace(trigger=trigger))

    server._attach_gate_edge_listener()
    assert iwr_monitor.observers == [trigger.notify_gate_edge]

    server._detach_gate_edge_listener()
    assert iwr_monitor.observers == []


def test_gate_edge_listener_is_skipped_without_a_sound_trigger(monkeypatch):
    iwr_monitor = _ObservedMonitor()
    monkeypatch.setattr(server, "iwr6843_runtime", SimpleNamespace(capture_monitor=iwr_monitor))
    monkeypatch.setattr(server, "monitor", SimpleNamespace(trigger=object()))

    server._attach_gate_edge_listener()
    server._detach_gate_edge_listener()

    assert iwr_monitor.observers == []
