from openflight.power.source import GpioPldSource, NullSource


class FakePin:
    """Stand-in for a gpiozero input. True is high."""

    def __init__(self, level=True):
        self.level = level
        self.closed = False

    def read(self):
        return self.level

    def close(self):
        self.closed = True


def test_low_is_always_battery():
    # A pulled-up floating pin cannot read low, so low is unambiguous.
    source = GpioPldSource(pin=6, trusted=False, pin_reader=FakePin(level=False))
    assert source.read(timestamp=1.0).state == "battery"


def test_untrusted_high_is_unknown_not_external():
    source = GpioPldSource(pin=17, trusted=False, pin_reader=FakePin(level=True))
    reading = source.read(timestamp=1.0)
    assert reading.state == "unknown"
    assert reading.status == "ok"


def test_trusted_high_is_external():
    source = GpioPldSource(pin=6, trusted=True, pin_reader=FakePin(level=True))
    assert source.read(timestamp=1.0).state == "external"


def test_untrusted_pin_latches_after_proving_itself():
    pin = FakePin(level=False)
    source = GpioPldSource(pin=17, trusted=False, pin_reader=pin)
    assert source.read(timestamp=1.0).state == "battery"
    pin.level = True
    # Having gone low once, the line is proven driven; high now means external.
    assert source.read(timestamp=2.0).state == "external"


def test_null_source_is_always_unknown_and_absent():
    reading = NullSource().read(timestamp=1.0)
    assert reading.state == "unknown"
    assert reading.status == "absent"


def test_read_error_becomes_error_status():
    class Boom:
        def read(self):
            raise OSError("gpio gone")

        def close(self):
            pass

    reading = GpioPldSource(pin=6, trusted=True, pin_reader=Boom()).read(timestamp=1.0)
    assert reading.status == "error"
    assert reading.state == "unknown"
