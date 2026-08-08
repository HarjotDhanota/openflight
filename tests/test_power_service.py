from openflight.power.config import PowerConfig
from openflight.power.models import PackReading, RailReading, SourceReading
from openflight.power.service import PowerService


class FakeGauge:
    def __init__(self, volts=3.9, status="ok"):
        self.volts, self.status, self.closed = volts, status, False

    def initialize(self):
        pass

    def read(self, *, timestamp):
        return PackReading(
            status=self.status,
            timestamp=timestamp,
            volts=self.volts if self.status == "ok" else None,
            percent=62.0,
        )

    def close(self):
        self.closed = True


class FakeSource:
    def __init__(self, state="battery"):
        self.state = state

    def read(self, *, timestamp):
        return SourceReading(status="ok", timestamp=timestamp, state=self.state)

    def close(self):
        pass


class FakeRail:
    def __init__(self, volts=5.21, status="ok"):
        self.volts, self.status = volts, status

    def read(self, *, timestamp):
        return RailReading(
            status=self.status,
            timestamp=timestamp,
            ext5v_volts=self.volts if self.status == "ok" else None,
            throttled=0,
        )

    def close(self):
        pass


def build(config=None, **kw):
    return PowerService(
        gauge=kw.get("gauge", FakeGauge()),
        source=kw.get("source", FakeSource()),
        rail=kw.get("rail", FakeRail()),
        config=config or PowerConfig(dwell_samples=1),
        halt=kw.get("halt", lambda: True),
    )


def test_view_carries_both_halves():
    service = build()
    service.sample_once(0.0)
    view = service.latest_view()
    assert view.pack_percent == 62.0
    assert view.rail_level == "green"
    assert view.source == "battery"


def test_partial_failure_keeps_the_other_reader_working():
    service = build(gauge=FakeGauge(status="error"))
    service.sample_once(0.0)
    view = service.latest_view()
    assert view.pack_level == "unknown"
    assert view.rail_level == "green"  # rail unaffected


def test_reader_recovers_after_a_failure():
    gauge = FakeGauge(status="error")
    service = build(gauge=gauge)
    service.sample_once(0.0)
    gauge.status = "ok"
    service.sample_once(1.0)
    assert service.latest_view().pack_level == "ok"


def test_halt_called_only_at_the_deadline():
    calls = []
    config = PowerConfig(dwell_samples=1, auto_shutdown_enabled=True, shutdown_grace_seconds=60)
    service = build(config=config, gauge=FakeGauge(volts=3.1), halt=lambda: calls.append(1))
    service.sample_once(0.0)
    assert service.latest_view().pending_shutdown is not None
    assert calls == []
    service.sample_once(30.0)
    assert calls == []
    service.sample_once(61.0)
    assert calls == [1]


def test_cancel_prevents_halt():
    calls = []
    config = PowerConfig(dwell_samples=1, auto_shutdown_enabled=True, shutdown_grace_seconds=60)
    service = build(config=config, gauge=FakeGauge(volts=3.1), halt=lambda: calls.append(1))
    service.sample_once(0.0)
    assert service.cancel_shutdown(service.latest_view().pending_shutdown.id) is True
    service.sample_once(61.0)
    assert calls == []


def test_cancel_with_stale_id_returns_false():
    config = PowerConfig(dwell_samples=1, auto_shutdown_enabled=True, shutdown_grace_seconds=60)
    service = build(config=config, gauge=FakeGauge(volts=3.1))
    service.sample_once(0.0)
    assert service.cancel_shutdown("stale") is False


def test_on_view_fires_on_level_change_not_every_sample():
    seen = []
    gauge = FakeGauge(volts=3.9)
    service = PowerService(
        gauge=gauge,
        source=FakeSource(),
        rail=FakeRail(),
        config=PowerConfig(dwell_samples=1),
        on_view=seen.append,
        halt=lambda: True,
    )
    service.sample_once(0.0)
    service.sample_once(1.0)  # unchanged
    gauge.volts = 3.5
    service.sample_once(2.0)  # now "low"
    assert len(seen) == 2


def test_stop_is_idempotent_and_closes_readers():
    gauge = FakeGauge()
    service = build(gauge=gauge)
    service.stop()
    service.stop()
    assert gauge.closed is True


def test_hardware_cleanup_runs_before_the_halt():
    order = []
    config = PowerConfig(dwell_samples=1, auto_shutdown_enabled=True, shutdown_grace_seconds=0)
    service = PowerService(
        gauge=FakeGauge(volts=3.1),
        source=FakeSource(),
        rail=FakeRail(),
        config=config,
        pre_halt=lambda: order.append("cleanup"),
        halt=lambda: order.append("halt") or True,
    )
    service.sample_once(0.0)
    service.sample_once(1.0)
    assert order == ["cleanup", "halt"]


def test_failing_halt_is_reported_and_not_retried():
    calls = []

    def failing_halt():
        calls.append(1)
        return False

    config = PowerConfig(dwell_samples=1, auto_shutdown_enabled=True, shutdown_grace_seconds=0)
    service = build(config=config, gauge=FakeGauge(volts=3.1), halt=failing_halt)
    service.sample_once(0.0)
    service.sample_once(1.0)
    service.sample_once(2.0)
    service.sample_once(3.0)
    # A failed halt is a visible degraded state, not a loop.
    assert calls == [1]
    assert "shut down manually" in " ".join(service.latest_view().warnings).lower()


def test_cancel_before_any_sample_does_not_crash():
    assert build().cancel_shutdown("anything") is False
