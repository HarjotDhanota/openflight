import pytest

from openflight.power.max1704x import MAX1704X, swap16


class FakeBus:
    def __init__(self, words=None, raises=None):
        self.words = words or {}
        self.raises = raises
        self.closed = False

    def read_word_data(self, address, register):
        if self.raises:
            raise self.raises
        return self.words[(address, register)]

    def close(self):
        self.closed = True


def test_swap16_matches_bench_reading():
    # Verified on hardware: i2cget -y 1 0x36 0x02 w -> 0x60cc
    assert swap16(0x60CC) == 0xCC60 == 52320


def test_voltage_conversion_matches_bench_reading():
    bus = FakeBus({(0x36, 0x02): 0x60CC, (0x36, 0x04): 0x003E})
    gauge = MAX1704X(bus=bus)
    gauge.initialize()
    reading = gauge.read(timestamp=1.0)
    assert reading.status == "ok"
    assert reading.volts == pytest.approx(4.0875, abs=1e-4)


def test_percent_conversion():
    # 0x3E00 byte-swapped from 0x003E -> 15872 / 256 = 62.0
    bus = FakeBus({(0x36, 0x02): 0x60CC, (0x36, 0x04): 0x003E})
    gauge = MAX1704X(bus=bus)
    gauge.initialize()
    assert gauge.read(timestamp=1.0).percent == pytest.approx(62.0)


def test_percent_is_clamped_to_100():
    # ModelGauge can report slightly over 100 immediately after a full charge.
    bus = FakeBus({(0x36, 0x02): 0x60CC, (0x36, 0x04): 0x0069})
    gauge = MAX1704X(bus=bus)
    gauge.initialize()
    assert gauge.read(timestamp=1.0).percent == 100.0


def test_initialize_raises_when_the_gauge_does_not_ack():
    # The caller uses this to decide "no gauge fitted" and carry on with the
    # other readers, so it must propagate rather than return a status.
    gauge = MAX1704X(bus=FakeBus(raises=OSError("no ACK")))
    with pytest.raises(OSError):
        gauge.initialize()


def test_bus_error_after_init_becomes_error_status_not_exception():
    # Initialize against a healthy bus, then break it. A gauge that answered
    # once and later glitches must degrade to a status: an exception here
    # would kill the sampling thread and take the indicator down for good.
    bus = FakeBus({(0x36, 0x02): 0x60CC, (0x36, 0x04): 0x003E})
    gauge = MAX1704X(bus=bus)
    gauge.initialize()
    bus.raises = OSError("no ACK")
    reading = gauge.read(timestamp=1.0)
    assert reading.status == "error"
    assert reading.volts is None
    assert "no ACK" in reading.error


def test_close_is_idempotent():
    bus = FakeBus({(0x36, 0x02): 0x60CC, (0x36, 0x04): 0x003E})
    gauge = MAX1704X(bus=bus)
    gauge.close()
    gauge.close()
    assert bus.closed is True
