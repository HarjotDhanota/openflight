"""Minimal Bosch BME280 I2C driver: temperature, station pressure and humidity.

Forced mode, one conversion per read with the part asleep between: in normal mode
self-heating puts the die 1-3 C above the air. The BMP280 shares the register map but
has no humidity channel, so it is refused rather than given a guessed humidity.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..inclinometer.lis3dh import SMBusLike

PRIMARY_ADDRESS = 0x76
SECONDARY_ADDRESS = 0x77
ADDRESSES = (PRIMARY_ADDRESS, SECONDARY_ADDRESS)

CHIP_ID_REG = 0xD0
CTRL_HUM_REG = 0xF2
STATUS_REG = 0xF3
CTRL_MEAS_REG = 0xF4
CONFIG_REG = 0xF5
DATA_REG = 0xF7  # 0xF7..0xFE: pressure, temperature, humidity
CALIB_1_REG = 0x88  # 0x88..0xA1: T1-T3, P1-P9, a reserved byte, H1
CALIB_1_LEN = 26
CALIB_2_REG = 0xE1  # 0xE1..0xE7: H2-H6
CALIB_2_LEN = 7

BME280_CHIP_ID = 0x60
BMP280_CHIP_ID = 0x58

OVERSAMPLE_X1 = 0b001
OVERSAMPLE_X4 = 0b011
MODE_FORCED = 0b01
STATUS_MEASURING = 0b1000
# Datasheet 9.1: T x1, P x4, H x1 needs at most 16.2 ms, rounded up before polling.
FORCED_CONVERSION_S = 0.017
# x1 temperature and humidity, x4 pressure: the conversion takes under 20 ms.
MAX_CONVERSION_POLLS = 50
CONVERSION_POLL_S = 0.002


class BME280IdentityError(RuntimeError):
    """Raised when the part at the address is not a BME280."""


class AirSensorNotFound(RuntimeError):
    """No usable BME280 on the bus; the message is the reason."""


@dataclass(frozen=True)
class AirSample:
    """One forced-mode measurement."""

    timestamp: float
    temperature_c: float
    pressure_hpa: float  # absolute station pressure, not reduced to sea level
    humidity_pct: float


@dataclass(frozen=True)
class Calibration:
    """The part's factory trimming words."""

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
    h1: int
    h2: int
    h3: int
    h4: int
    h5: int
    h6: int


def _u16(data, index):
    return data[index] | (data[index + 1] << 8)


def _s16(data, index):
    value = _u16(data, index)
    return value - 0x10000 if value & 0x8000 else value


def _s8(value):
    return value - 0x100 if value & 0x80 else value


def _s12(value):
    return value - 0x1000 if value & 0x800 else value


class BME280:
    """Read a BME280 in forced mode; ``bus`` is injectable for tests."""

    def __init__(
        self,
        *,
        bus_number: int = 1,
        address: int = PRIMARY_ADDRESS,
        bus: SMBusLike | None = None,
    ):
        if bus is None:
            from smbus2 import SMBus  # pylint: disable=import-outside-toplevel,import-error

            bus = SMBus(bus_number)
        self.bus = bus
        self.bus_number = bus_number
        self.address = address
        self.chip_id: int | None = None
        self.calibration: Calibration | None = None
        self._closed = False

    def initialize(self) -> None:
        """Verify the chip ID and read the trimming words."""
        chip_id = self.bus.read_byte_data(self.address, CHIP_ID_REG)
        if chip_id == BMP280_CHIP_ID:
            raise BME280IdentityError(
                f"a BMP280 at 0x{self.address:02x} has no humidity sensor; a BME280 is required"
            )
        if chip_id != BME280_CHIP_ID:
            raise BME280IdentityError(
                f"chip ID 0x{chip_id:02x} at 0x{self.address:02x} is not a BME280 "
                f"(0x{BME280_CHIP_ID:02x})"
            )
        self.chip_id = chip_id
        self.calibration = self._read_calibration()

    def _read_calibration(self) -> Calibration:
        first = self.bus.read_i2c_block_data(self.address, CALIB_1_REG, CALIB_1_LEN)
        second = self.bus.read_i2c_block_data(self.address, CALIB_2_REG, CALIB_2_LEN)
        if len(first) != CALIB_1_LEN or len(second) != CALIB_2_LEN:
            raise OSError("BME280 returned a short calibration block")
        # dig_H4 and dig_H5 share 0xE5: H4 takes its low nibble, H5 its high one.
        return Calibration(
            t1=_u16(first, 0),
            t2=_s16(first, 2),
            t3=_s16(first, 4),
            p1=_u16(first, 6),
            p2=_s16(first, 8),
            p3=_s16(first, 10),
            p4=_s16(first, 12),
            p5=_s16(first, 14),
            p6=_s16(first, 16),
            p7=_s16(first, 18),
            p8=_s16(first, 20),
            p9=_s16(first, 22),
            h1=first[25],
            h2=_s16(second, 0),
            h3=second[2],
            h4=_s12((second[3] << 4) | (second[4] & 0x0F)),
            h5=_s12((second[5] << 4) | (second[4] >> 4)),
            h6=_s8(second[6]),
        )

    def read(self, *, timestamp: float | None = None) -> AirSample:
        """One forced conversion. A bus error or a conversion that never ends raises."""
        if self.calibration is None:
            raise OSError("BME280 read before initialize()")
        # ctrl_hum takes effect only on the next ctrl_meas write (datasheet 5.4.3)
        self.bus.write_byte_data(self.address, CTRL_HUM_REG, OVERSAMPLE_X1)
        self.bus.write_byte_data(self.address, CONFIG_REG, 0x00)
        self.bus.write_byte_data(
            self.address,
            CTRL_MEAS_REG,
            (OVERSAMPLE_X1 << 5) | (OVERSAMPLE_X4 << 2) | MODE_FORCED,
        )
        time.sleep(FORCED_CONVERSION_S)
        for _ in range(MAX_CONVERSION_POLLS):
            if not self.bus.read_byte_data(self.address, STATUS_REG) & STATUS_MEASURING:
                break
            time.sleep(CONVERSION_POLL_S)
        else:
            raise OSError(f"BME280 at 0x{self.address:02x} never finished its conversion")
        data = self.bus.read_i2c_block_data(self.address, DATA_REG, 8)
        if len(data) != 8:
            raise OSError(f"BME280 returned {len(data)} data bytes; expected 8")
        raw_pressure = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
        raw_temperature = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
        raw_humidity = (data[6] << 8) | data[7]
        if raw_temperature == 0x80000 or raw_pressure == 0x80000 or raw_humidity == 0x8000:
            raise OSError("BME280 reported a skipped temperature, pressure or humidity channel")
        temperature_c, t_fine = self._temperature(raw_temperature)
        return AirSample(
            timestamp=time.time() if timestamp is None else timestamp,
            temperature_c=temperature_c,
            pressure_hpa=self._pressure_pa(raw_pressure, t_fine) / 100.0,
            humidity_pct=self._humidity_pct(raw_humidity, t_fine),
        )

    # The three compensations are the datasheet's floating-point ones (4.2.3, 8.1).
    def _temperature(self, raw: int) -> tuple[float, float]:
        cal = self.calibration
        var1 = (raw / 16384.0 - cal.t1 / 1024.0) * cal.t2
        var2 = (raw / 131072.0 - cal.t1 / 8192.0) ** 2 * cal.t3
        t_fine = var1 + var2
        return t_fine / 5120.0, t_fine

    def _pressure_pa(self, raw: int, t_fine: float) -> float:
        cal = self.calibration
        var1 = t_fine / 2.0 - 64000.0
        var2 = var1 * var1 * cal.p6 / 32768.0
        var2 = var2 + var1 * cal.p5 * 2.0
        var2 = var2 / 4.0 + cal.p4 * 65536.0
        var1 = (cal.p3 * var1 * var1 / 524288.0 + cal.p2 * var1) / 524288.0
        var1 = (1.0 + var1 / 32768.0) * cal.p1
        if var1 == 0.0:
            raise OSError("BME280 calibration gives a zero pressure divisor")
        pressure = (1048576.0 - raw - var2 / 4096.0) * 6250.0 / var1
        var1 = cal.p9 * pressure * pressure / 2147483648.0
        var2 = pressure * cal.p8 / 32768.0
        return pressure + (var1 + var2 + cal.p7) / 16.0

    def _humidity_pct(self, raw: int, t_fine: float) -> float:
        cal = self.calibration
        h = t_fine - 76800.0
        h = (raw - (cal.h4 * 64.0 + cal.h5 / 16384.0 * h)) * (
            cal.h2 / 65536.0 * (1.0 + cal.h6 / 67108864.0 * h * (1.0 + cal.h3 / 67108864.0 * h))
        )
        h = h * (1.0 - cal.h1 * h / 524288.0)
        return min(100.0, max(0.0, h))

    def close(self) -> None:
        """Close the owned I2C bus."""
        if not self._closed:
            self.bus.close()
            self._closed = True


def find_bme280(*, bus_number: int = 1, bus: SMBusLike | None = None) -> BME280:
    """The BME280 at 0x76 or 0x77, initialized; else ``AirSensorNotFound`` with why."""
    if bus is None:
        try:
            from smbus2 import SMBus  # pylint: disable=import-outside-toplevel,import-error
        except ImportError as error:
            raise AirSensorNotFound("smbus2 is not installed") from error
        try:
            bus = SMBus(bus_number)
        except OSError as error:
            raise AirSensorNotFound(f"I2C bus {bus_number} unavailable: {error}") from error
    failures = []
    for address in ADDRESSES:
        sensor = BME280(bus_number=bus_number, address=address, bus=bus)
        try:
            sensor.initialize()
        except (OSError, BME280IdentityError) as error:
            failures.append(f"0x{address:02x}: {error}")
            continue
        return sensor
    bus.close()
    raise AirSensorNotFound(f"no BME280 on I2C bus {bus_number} ({'; '.join(failures)})")
