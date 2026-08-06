"""Bosch BME280 / BMP280 driver over I2C.

Reads temperature, absolute station pressure and (on the BME280) relative
humidity, which is everything ``density.air_density`` needs. A ~$4 part
removes what is otherwise the largest systematic error in the carry
pipeline: a 14-yard bias on a driver in Denver, and 5-6 yards on a hot
afternoon at a sea-level venue.

Why this rather than a library
------------------------------
``smbus2`` is a thin ioctl wrapper with no compensation math, so the Bosch
algorithm has to live somewhere regardless. The Adafruit CircuitPython stack
would supply it, but pulls in Blinka and its board-detection layer -- a
large dependency tree for one sensor on one board. What is implemented here
is ~150 lines of arithmetic transcribed from the datasheet.

Why forced mode
---------------
Self-heating is the dominant error term. In continuous mode the die sits
1-3 C above ambient inside a warm enclosure, which is 0.26-0.77 yd on a
driver -- larger than the pressure, temperature and humidity spec errors
combined. A single forced measurement every couple of seconds, with the part
asleep in between, keeps that near 0.1 C. Everything else in the error
budget is noise by comparison.

Safety
------
``read()`` never raises. It runs on a background poll thread whose value is
consumed on the shot path, and a loose jumper wire must not be able to stop
someone hitting balls. Every failure returns ``None`` and the caller falls
back to the previous source.

NOTE: the compensation is cross-checked against the datasheet's
floating-point formulation in the tests, but has NOT yet been confirmed
against a physical chip. The failure mode would be a consistent offset
rather than a crash. Confirm against a reference thermometer and barometer
before trusting the numbers in the field.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# I2C addresses. SDO to ground gives 0x76, SDO to VDDIO gives 0x77; breakout
# boards disagree about which they strap by default, so both are tried.
PRIMARY_ADDRESS = 0x76
SECONDARY_ADDRESS = 0x77

CHIP_ID_REG = 0xD0
RESET_REG = 0xE0
CTRL_HUM_REG = 0xF2
STATUS_REG = 0xF3
CTRL_MEAS_REG = 0xF4
CONFIG_REG = 0xF5
DATA_REG = 0xF7  # 0xF7..0xFE: pressure, temperature, humidity

CALIB_1_REG = 0x88  # 0x88..0xA1, 26 bytes: T1-T3, P1-P9, H1
CALIB_1_LEN = 26
CALIB_2_REG = 0xE1  # 0xE1..0xE7, 7 bytes: H2-H6
CALIB_2_LEN = 7

BME280_CHIP_ID = 0x60
BMP280_CHIP_ID = 0x58
# BME680 answers 0x61. It is a different part with a different register map,
# so it is rejected rather than half-supported.
SUPPORTED_CHIP_IDS = {BME280_CHIP_ID: "bme280", BMP280_CHIP_ID: "bmp280"}

# Oversampling. x1 on temperature and humidity, x4 on pressure: pressure is
# the noisiest channel and the one density is most sensitive to, while extra
# temperature oversampling buys accuracy that self-heating would swamp.
OVERSAMPLE_X1 = 0b001
OVERSAMPLE_X4 = 0b011
MODE_SLEEP = 0b00
MODE_FORCED = 0b01

STATUS_MEASURING = 0b1000

# A x4 pressure conversion takes well under 20 ms. This is a wedged-bus
# backstop, not a timing budget.
MAX_CONVERSION_POLLS = 50
CONVERSION_POLL_S = 0.002


class Bme280Error(Exception):
    """Raised only during construction, when the part is not one we support."""


@dataclass(frozen=True)
class SensorReading:
    """One set of conditions from the sensor."""

    temp_c: float
    pressure_hpa: float
    # None on a BMP280, which has no humidity channel at all. Deliberately not
    # 0.0: that is indistinguishable from bone-dry air and would be applied to
    # the density as though it had been measured.
    humidity_pct: Optional[float]
    chip: str


@dataclass(frozen=True)
class Calibration:
    """Per-part trimming constants, burnt in at the factory."""

    t1: int
    t2: int
    t3: int
    p1: int
    p2: int
    p3: int
    p4: int
    p5: int
    p6: int
    p7: int
    p8: int
    p9: int
    h1: int = 0
    h2: int = 0
    h3: int = 0
    h4: int = 0
    h5: int = 0
    h6: int = 0


def _u16(data, index):
    return data[index] | (data[index + 1] << 8)


def _s16(data, index):
    value = _u16(data, index)
    return value - 65536 if value & 0x8000 else value


def _s8(value):
    return value - 256 if value & 0x80 else value


def _s12(value):
    """Sign-extend a 12-bit two's-complement value (dig_H4 and dig_H5)."""
    return value - 4096 if value & 0x800 else value


class BME280:
    """A BME280 or BMP280 on an I2C bus.

    Args:
        bus: Anything with smbus2's ``read_byte_data``, ``read_i2c_block_data``
            and ``write_byte_data``. Injected so tests need no hardware.
        address: I2C address. Defaults to the primary.

    Raises:
        Bme280Error: If the chip ID is not a part this driver understands.
            Construction is the only place this raises -- reads never do.
    """

    def __init__(self, bus, address: int = PRIMARY_ADDRESS):
        self._bus = bus
        self.address = address
        chip_id = bus.read_byte_data(address, CHIP_ID_REG)
        if chip_id not in SUPPORTED_CHIP_IDS:
            raise Bme280Error(
                f"chip ID 0x{chip_id:02x} at 0x{address:02x} is not a BME280 or BMP280"
            )
        self.chip = SUPPORTED_CHIP_IDS[chip_id]
        self.has_humidity = self.chip == "bme280"
        self.calibration = self._read_calibration()

    def _read_calibration(self) -> Calibration:
        block = self._bus.read_i2c_block_data(self.address, CALIB_1_REG, CALIB_1_LEN)
        values = {
            "t1": _u16(block, 0),
            "t2": _s16(block, 2),
            "t3": _s16(block, 4),
            "p1": _u16(block, 6),
            "p2": _s16(block, 8),
            "p3": _s16(block, 10),
            "p4": _s16(block, 12),
            "p5": _s16(block, 14),
            "p6": _s16(block, 16),
            "p7": _s16(block, 18),
            "p8": _s16(block, 20),
            "p9": _s16(block, 22),
        }
        if not self.has_humidity:
            return Calibration(**values)

        values["h1"] = block[25]
        second = self._bus.read_i2c_block_data(self.address, CALIB_2_REG, CALIB_2_LEN)
        # dig_H4 and dig_H5 share the byte at 0xE5, split across its nibbles:
        # H4 takes the low nibble, H5 the high one. Getting this wrong is the
        # classic BME280 driver bug, and it produces humidity that looks
        # plausible until the air is very dry or very wet.
        values["h2"] = _s16(second, 0)
        values["h3"] = second[2]
        values["h4"] = _s12(((second[3] & 0xFF) << 4) | (second[4] & 0x0F))
        values["h5"] = _s12(((second[5] & 0xFF) << 4) | ((second[4] >> 4) & 0x0F))
        values["h6"] = _s8(second[6])
        return Calibration(**values)

    def read(self) -> Optional[SensorReading]:
        """Take one forced-mode measurement.

        Returns:
            The conditions, or ``None`` if anything went wrong -- a bus error,
            a chip that never finished converting, or an unreadable block.
            Never raises: this feeds the shot path.
        """
        try:
            self._start_forced_measurement()
            if not self._wait_for_conversion():
                logger.warning(
                    "[SENSOR] %s at 0x%02x never finished converting", self.chip, self.address
                )
                return None
            block = self._bus.read_i2c_block_data(self.address, DATA_REG, 8)
        except OSError as exc:
            # Loose wiring, a bus in use, or the part briefly browning out.
            # Transient by nature, so nothing is latched off -- the next poll
            # tries again.
            logger.warning("[SENSOR] Read from 0x%02x failed: %s", self.address, exc)
            return None

        raw_pressure = (block[0] << 12) | (block[1] << 4) | (block[2] >> 4)
        raw_temp = (block[3] << 12) | (block[4] << 4) | (block[5] >> 4)
        raw_humidity = (block[6] << 8) | block[7]

        temp_c, t_fine = self._compensate_temperature(raw_temp)
        pressure_hpa = self._compensate_pressure(raw_pressure, t_fine) / 100.0
        humidity = self._compensate_humidity(raw_humidity, t_fine) if self.has_humidity else None

        return SensorReading(
            temp_c=temp_c,
            pressure_hpa=pressure_hpa,
            humidity_pct=humidity,
            chip=self.chip,
        )

    def _start_forced_measurement(self) -> None:
        # ctrl_hum MUST be written before ctrl_meas: the datasheet specifies
        # that changes to ctrl_hum only take effect after a subsequent write to
        # ctrl_meas. Reversed, humidity silently samples at the wrong
        # oversampling and reads plausibly wrong.
        if self.has_humidity:
            self._bus.write_byte_data(self.address, CTRL_HUM_REG, OVERSAMPLE_X1)
        self._bus.write_byte_data(self.address, CONFIG_REG, 0x00)  # no IIR, no standby
        self._bus.write_byte_data(
            self.address,
            CTRL_MEAS_REG,
            (OVERSAMPLE_X1 << 5) | (OVERSAMPLE_X4 << 2) | MODE_FORCED,
        )

    def _wait_for_conversion(self) -> bool:
        """Poll status until the measuring bit clears. Bounded, so a wedged bus
        cannot pin the poll thread indefinitely."""
        for _ in range(MAX_CONVERSION_POLLS):
            if not self._bus.read_byte_data(self.address, STATUS_REG) & STATUS_MEASURING:
                return True
            time.sleep(CONVERSION_POLL_S)
        return False

    def _compensate_temperature(self, raw: int):
        """Returns (degrees C, t_fine). t_fine carries the temperature into the
        pressure and humidity compensation, which is why it is threaded
        through rather than recomputed."""
        cal = self.calibration
        var1 = (raw / 16384.0 - cal.t1 / 1024.0) * cal.t2
        var2 = (raw / 131072.0 - cal.t1 / 8192.0) ** 2 * cal.t3
        t_fine = var1 + var2
        return t_fine / 5120.0, t_fine

    def _compensate_pressure(self, raw: int, t_fine: float) -> float:
        cal = self.calibration
        var1 = t_fine / 2.0 - 64000.0
        var2 = var1 * var1 * cal.p6 / 32768.0
        var2 = var2 + var1 * cal.p5 * 2.0
        var2 = var2 / 4.0 + cal.p4 * 65536.0
        var1 = (cal.p3 * var1 * var1 / 524288.0 + cal.p2 * var1) / 524288.0
        var1 = (1.0 + var1 / 32768.0) * cal.p1
        if var1 == 0.0:
            # Only reachable from a corrupt calibration block; guarded because
            # the alternative is ZeroDivisionError on the poll thread.
            return 0.0
        pressure = 1048576.0 - raw
        pressure = (pressure - var2 / 4096.0) * 6250.0 / var1
        var1 = cal.p9 * pressure * pressure / 2147483648.0
        var2 = pressure * cal.p8 / 32768.0
        return pressure + (var1 + var2 + cal.p7) / 16.0

    def _compensate_humidity(self, raw: int, t_fine: float) -> float:
        cal = self.calibration
        h = t_fine - 76800.0
        h = (raw - (cal.h4 * 64.0 + cal.h5 / 16384.0 * h)) * (
            cal.h2 / 65536.0 * (1.0 + cal.h6 / 67108864.0 * h * (1.0 + cal.h3 / 67108864.0 * h))
        )
        h = h * (1.0 - cal.h1 * h / 524288.0)
        # The datasheet clamps here. A working part legitimately reports a
        # little over 100% in fog, but far outside that is a fault.
        return min(100.0, max(0.0, h))


def detect(bus, addresses=(PRIMARY_ADDRESS, SECONDARY_ADDRESS)) -> Optional[BME280]:
    """Find a supported part on the bus.

    Args:
        bus: An smbus2-compatible bus.
        addresses: Addresses to probe, in preference order.

    Returns:
        The sensor, or ``None`` if nothing supported answered. No sensor
        fitted is the common case, not an error, so this does not raise.
    """
    for address in addresses:
        try:
            sensor = BME280(bus, address)
        except (OSError, Bme280Error) as exc:
            logger.debug("[SENSOR] Nothing usable at 0x%02x: %s", address, exc)
            continue
        logger.info("[SENSOR] Found %s at 0x%02x", sensor.chip, address)
        return sensor
    return None


def open_bus(bus_number: int = 1):
    """Open the Pi's I2C bus. Imported lazily so smbus2 is only needed on a Pi.

    Returns:
        An open bus, or ``None`` if smbus2 is missing or the bus device is
        absent -- both of which mean "no sensor", not "crash".
    """
    try:
        from smbus2 import SMBus  # noqa: PLC0415 -- optional, Linux-only dependency
    except ImportError:
        logger.info("[SENSOR] smbus2 is not installed; no air-density sensor")
        return None
    try:
        return SMBus(bus_number)
    except (OSError, FileNotFoundError) as exc:
        logger.info("[SENSOR] I2C bus %d unavailable (%s); is I2C enabled?", bus_number, exc)
        return None
