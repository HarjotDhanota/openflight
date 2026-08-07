"""MAX1704x fuel-gauge driver (Geekworm X120x/X12xx family, UPS-Lite).

Register 0x04 is ModelGauge state of charge -- a modeled value that tracks
across charge and discharge, not a lookup on instantaneous VCELL. It stays
meaningful while the pack is charging, which is why the UI keeps showing a
percentage on external power.
"""

from __future__ import annotations

from typing import Protocol

from .models import PackReading

VCELL_REGISTER = 0x02
SOC_REGISTER = 0x04
# MAX17048 datasheet: VCELL LSB is 78.125 uV.
VCELL_MICROVOLTS_PER_LSB = 78.125


class SMBusLike(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Subset of smbus2 used by the driver, allowing deterministic tests."""

    def read_word_data(self, address: int, register: int) -> int:
        """Read one register word."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Close the bus."""
        ...  # pylint: disable=unnecessary-ellipsis


def swap16(raw: int) -> int:
    """Byte-swap a 16-bit word.

    SMBus reads little-endian and the gauge is big-endian, so every word needs
    this. Verified on hardware: 0x60cc -> 0xcc60 -> 52320 -> 4.088 V.
    """
    return ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)


class MAX1704X:
    """Read pack voltage and modeled state of charge over I2C."""

    DEFAULT_ADDRESS = 0x36

    def __init__(
        self,
        *,
        bus_number: int = 1,
        address: int = DEFAULT_ADDRESS,
        bus: SMBusLike | None = None,
    ):
        if bus is None:
            from smbus2 import SMBus  # pylint: disable=import-outside-toplevel,import-error

            bus = SMBus(bus_number)
        self.bus = bus
        self.address = address
        self._closed = False

    def initialize(self) -> None:
        """Verify the gauge answers.

        Raises:
            OSError: if the address does not ACK. The caller treats this as
                "no gauge fitted" and carries on with the other readers.
        """
        self.bus.read_word_data(self.address, VCELL_REGISTER)

    def read(self, *, timestamp: float) -> PackReading:
        """Read the gauge. Never raises: bus faults become an error status."""
        try:
            volts = swap16(self.bus.read_word_data(self.address, VCELL_REGISTER))
            soc = swap16(self.bus.read_word_data(self.address, SOC_REGISTER))
        except Exception as error:  # pylint: disable=broad-exception-caught
            # A sampling thread that dies on a transient bus glitch takes the
            # indicator down permanently; a status does not.
            return PackReading(status="error", timestamp=timestamp, error=str(error))
        return PackReading(
            status="ok",
            timestamp=timestamp,
            volts=volts * VCELL_MICROVOLTS_PER_LSB / 1_000_000,
            # ModelGauge reports slightly over 100% just after a full charge.
            percent=min(100.0, soc / 256),
        )

    def close(self) -> None:
        """Release the bus. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self.bus.close()
