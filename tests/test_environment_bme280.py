"""Tests for the BME280 / BMP280 I2C driver.

The bus is injected, so nothing here needs hardware or a Linux I2C device.
``FakeBus`` models the chip at the register level -- the driver talks to it
exactly as it would to smbus2, so register addresses, the forced-mode
handshake and the burst read are all exercised for real.

Compensation is cross-checked rather than self-checked: the driver implements
Bosch's fixed-point algorithm, and ``reference_compensate`` below implements
the floating-point one from the same datasheet. They are structurally
different, so a transcription slip in either shows up as a disagreement.

Calibration constants here are synthetic but type-correct (unsigned vs
signed, 8- vs 12- vs 16-bit), chosen to land in realistic ranges. They are
NOT copied from a datasheet worked example, so these tests prove the
algorithm is implemented consistently -- not that it matches one specific
physical chip. Confirming that needs the real part; see the PR notes.
"""

import pytest

from openflight.environment.bme280 import (
    BME280,
    BMP280_CHIP_ID,
    CHIP_ID_REG,
    CTRL_HUM_REG,
    CTRL_MEAS_REG,
    DATA_REG,
    PRIMARY_ADDRESS,
    SECONDARY_ADDRESS,
    STATUS_REG,
    Bme280Error,
    detect,
)

BME280_CHIP_ID = 0x60

# Synthetic but type-correct calibration. See the module docstring.
CALIB = {
    "T1": 28960,  # unsigned short
    "T2": 26619,  # signed short
    "T3": 50,
    "P1": 37045,  # unsigned short
    "P2": -10666,
    "P3": 3024,
    "P4": 6212,
    "P5": -96,
    "P6": -7,
    "P7": 15500,
    "P8": -14600,
    "P9": 6000,
    "H1": 75,  # unsigned char
    "H2": 355,  # signed short
    "H3": 0,  # unsigned char
    "H4": 322,  # signed 12-bit
    "H5": 50,  # signed 12-bit
    "H6": 30,  # signed char
}

# Raw ADC words. 20-bit for temperature and pressure, 16-bit for humidity.
RAW_TEMP = 519888
RAW_PRESSURE = 326816
RAW_HUMIDITY = 32768


def _u16le(value):
    return [value & 0xFF, (value >> 8) & 0xFF]


def _s16le(value):
    return _u16le(value & 0xFFFF)


def calibration_block_1(calib=None):
    """0x88..0xA1 -- temperature, pressure, and H1."""
    c = {**CALIB, **(calib or {})}
    block = []
    block += _u16le(c["T1"])
    block += _s16le(c["T2"])
    block += _s16le(c["T3"])
    block += _u16le(c["P1"])
    for key in ("P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9"):
        block += _s16le(c[key])
    block += [0x00]  # 0xA0 is unused -- one byte, not two
    block += [c["H1"] & 0xFF]  # 0xA1
    assert len(block) == 26, len(block)
    return block


def calibration_block_2(calib=None):
    """0xE1..0xE7 -- the humidity constants, including the packed 12-bit pair."""
    c = {**CALIB, **(calib or {})}
    h4, h5 = c["H4"], c["H5"]
    return [
        *_s16le(c["H2"]),
        c["H3"] & 0xFF,
        (h4 >> 4) & 0xFF,
        (h4 & 0x0F) | ((h5 & 0x0F) << 4),
        (h5 >> 4) & 0xFF,
        c["H6"] & 0xFF,
    ]


def data_block(raw_temp=RAW_TEMP, raw_pressure=RAW_PRESSURE, raw_humidity=RAW_HUMIDITY):
    """0xF7..0xFE -- pressure, then temperature, then humidity."""
    return [
        (raw_pressure >> 12) & 0xFF,
        (raw_pressure >> 4) & 0xFF,
        (raw_pressure << 4) & 0xF0,
        (raw_temp >> 12) & 0xFF,
        (raw_temp >> 4) & 0xFF,
        (raw_temp << 4) & 0xF0,
        (raw_humidity >> 8) & 0xFF,
        raw_humidity & 0xFF,
    ]


class FakeBus:
    """A BME280 at the register level.

    Records every write so the forced-mode handshake can be asserted, which
    matters: the datasheet requires ctrl_hum to be written BEFORE ctrl_meas,
    because ctrl_hum only takes effect on a subsequent ctrl_meas write. Get
    that order wrong and humidity silently reads at the wrong oversampling.
    """

    def __init__(
        self, *, chip_id=BME280_CHIP_ID, addresses=(PRIMARY_ADDRESS,), calib=None, data=None
    ):
        self.chip_id = chip_id
        self.addresses = set(addresses)
        self.calib = calib
        self.data = data if data is not None else data_block()
        self.writes = []
        self.reads = []
        self.closed = False
        self.busy_reads = 0  # how many times status should report "measuring"

    def _check(self, address):
        if address not in self.addresses:
            raise OSError(121, "Remote I/O error")

    def read_byte_data(self, address, register):
        self._check(address)
        self.reads.append(register)
        if register == CHIP_ID_REG:
            return self.chip_id
        if register == STATUS_REG:
            if self.busy_reads > 0:
                self.busy_reads -= 1
                return 0x08  # measuring
            return 0x00
        return 0x00

    def read_i2c_block_data(self, address, register, length):
        self._check(address)
        self.reads.append(register)
        if register == 0x88:
            return calibration_block_1(self.calib)[:length]
        if register == 0xE1:
            return calibration_block_2(self.calib)[:length]
        if register == DATA_REG:
            return self.data[:length]
        return [0x00] * length

    def write_byte_data(self, address, register, value):
        self._check(address)
        self.writes.append((register, value))

    def close(self):
        self.closed = True


def reference_compensate(raw_temp, raw_pressure, raw_humidity, calib=None):
    """Bosch's FLOATING-POINT compensation, independent of the driver's fixed-point one."""
    c = {**CALIB, **(calib or {})}

    var1 = (raw_temp / 16384.0 - c["T1"] / 1024.0) * c["T2"]
    var2 = (raw_temp / 131072.0 - c["T1"] / 8192.0) ** 2 * c["T3"]
    t_fine = var1 + var2
    temp_c = t_fine / 5120.0

    var1 = t_fine / 2.0 - 64000.0
    var2 = var1 * var1 * c["P6"] / 32768.0
    var2 = var2 + var1 * c["P5"] * 2.0
    var2 = var2 / 4.0 + c["P4"] * 65536.0
    var1 = (c["P3"] * var1 * var1 / 524288.0 + c["P2"] * var1) / 524288.0
    var1 = (1.0 + var1 / 32768.0) * c["P1"]
    pressure_pa = 1048576.0 - raw_pressure
    pressure_pa = (pressure_pa - var2 / 4096.0) * 6250.0 / var1
    var1 = c["P9"] * pressure_pa * pressure_pa / 2147483648.0
    var2 = pressure_pa * c["P8"] / 32768.0
    pressure_pa = pressure_pa + (var1 + var2 + c["P7"]) / 16.0

    h = t_fine - 76800.0
    h = (raw_humidity - (c["H4"] * 64.0 + c["H5"] / 16384.0 * h)) * (
        c["H2"] / 65536.0 * (1.0 + c["H6"] / 67108864.0 * h * (1.0 + c["H3"] / 67108864.0 * h))
    )
    humidity = h * (1.0 - c["H1"] * h / 524288.0)

    return temp_c, pressure_pa / 100.0, humidity


class TestChipDetection:
    """Which part is actually on the bus, and at which address."""

    def test_a_bme280_is_recognised(self):
        sensor = BME280(FakeBus())
        assert sensor.chip == "bme280"
        assert sensor.has_humidity is True

    def test_a_bmp280_is_recognised_and_reports_no_humidity_channel(self):
        """The BMP280 is pin- and register-compatible but has no humidity
        sensor. Boards are widely mislabelled, so this must be detected rather
        than assumed -- reading its (absent) humidity registers returns zeros,
        which would otherwise look like a real 0% RH reading."""
        sensor = BME280(FakeBus(chip_id=BMP280_CHIP_ID))
        assert sensor.chip == "bmp280"
        assert sensor.has_humidity is False

    def test_an_unknown_chip_id_is_refused(self):
        with pytest.raises(Bme280Error, match="0x1f"):
            BME280(FakeBus(chip_id=0x1F))

    def test_the_secondary_address_is_tried_when_nothing_answers_at_the_primary(self):
        """SDO tied high moves the part to 0x77, and breakout boards differ on
        which they default to."""
        bus = FakeBus(addresses=(SECONDARY_ADDRESS,))

        sensor = detect(bus)

        assert sensor is not None
        assert sensor.address == SECONDARY_ADDRESS

    def test_the_primary_address_is_preferred_when_both_answer(self):
        bus = FakeBus(addresses=(PRIMARY_ADDRESS, SECONDARY_ADDRESS))
        assert detect(bus).address == PRIMARY_ADDRESS

    def test_nothing_on_the_bus_is_none_rather_than_an_exception(self):
        """No sensor fitted is the common case, not an error."""
        assert detect(FakeBus(addresses=())) is None

    def test_a_wrong_chip_at_a_matching_address_is_none(self):
        assert detect(FakeBus(chip_id=0x1F)) is None


class TestForcedModeHandshake:
    """Self-heating is the dominant error term, and forced mode is the fix."""

    def test_ctrl_hum_is_written_before_ctrl_meas(self):
        """Datasheet 5.4.3: ctrl_hum only takes effect after a write to
        ctrl_meas. Reversing these two silently leaves humidity oversampling
        at whatever it was, which reads as a plausible but wrong value."""
        bus = FakeBus()
        BME280(bus).read()

        registers = [reg for reg, _ in bus.writes]
        assert CTRL_HUM_REG in registers
        assert CTRL_MEAS_REG in registers
        assert registers.index(CTRL_HUM_REG) < registers.index(CTRL_MEAS_REG)

    def test_the_chip_is_left_in_sleep_between_readings(self):
        """Continuous mode warms the die 1-3 C above ambient, which is 0.26-0.77
        yd on a driver -- larger than every other sensor error combined. The
        mode bits of the final ctrl_meas write must request a single forced
        measurement, after which the part returns to sleep on its own."""
        bus = FakeBus()
        BME280(bus).read()

        ctrl_meas = [value for reg, value in bus.writes if reg == CTRL_MEAS_REG][-1]
        assert ctrl_meas & 0b11 == 0b01  # forced, not normal (0b11)

    def test_it_waits_for_the_conversion_rather_than_reading_a_stale_sample(self):
        bus = FakeBus()
        bus.busy_reads = 3

        reading = BME280(bus).read()

        assert reading is not None
        assert bus.reads.count(STATUS_REG) >= 4

    def test_a_conversion_that_never_completes_gives_up_instead_of_hanging(self):
        """This runs on a background thread, but a wedged bus must not pin a
        core forever."""
        bus = FakeBus()
        bus.busy_reads = 10_000

        assert BME280(bus).read() is None


class TestCompensation:
    """Cross-checked against the datasheet's floating-point formulation."""

    def test_matches_the_reference_implementation(self):
        expected_t, expected_p, expected_h = reference_compensate(
            RAW_TEMP, RAW_PRESSURE, RAW_HUMIDITY
        )

        reading = BME280(FakeBus()).read()

        assert reading.temp_c == pytest.approx(expected_t, abs=0.02)
        assert reading.pressure_hpa == pytest.approx(expected_p, abs=0.2)
        assert reading.humidity_pct == pytest.approx(expected_h, abs=0.5)

    def test_the_compensated_values_are_physically_plausible(self):
        """A sign error or a misread calibration word lands far outside these."""
        reading = BME280(FakeBus()).read()

        assert -40.0 < reading.temp_c < 60.0
        assert 800.0 < reading.pressure_hpa < 1100.0
        assert 0.0 <= reading.humidity_pct <= 100.0

    @pytest.mark.parametrize("raw_temp", [400000, 460000, 519888, 560000])
    def test_temperature_rises_monotonically_with_the_raw_word(self, raw_temp):
        cooler = BME280(FakeBus(data=data_block(raw_temp=raw_temp - 20000))).read()
        warmer = BME280(FakeBus(data=data_block(raw_temp=raw_temp))).read()

        assert warmer.temp_c > cooler.temp_c

    def test_a_signed_calibration_word_is_read_as_signed(self):
        """dig_T2 and most of the pressure constants are signed. Reading them
        unsigned still produces a number, just a wildly wrong one -- which is
        why this asserts the sign flips the result rather than checking a
        magnitude."""
        positive = BME280(FakeBus(calib={"T2": 26619})).read()
        negative = BME280(FakeBus(calib={"T2": -26619})).read()

        assert positive.temp_c > 0
        assert negative.temp_c < 0

    def test_the_packed_twelve_bit_humidity_constants_round_trip(self):
        """dig_H4 and dig_H5 share the byte at 0xE5, split across its nibbles.
        Getting the packing wrong is the classic BME280 driver bug."""
        sensor = BME280(FakeBus(calib={"H4": 322, "H5": 50}))

        assert sensor.calibration.h4 == 322
        assert sensor.calibration.h5 == 50

    def test_negative_packed_humidity_constants_stay_negative(self):
        sensor = BME280(FakeBus(calib={"H4": -100, "H5": -200}))

        assert sensor.calibration.h4 == -100
        assert sensor.calibration.h5 == -200


class TestBmp280HasNoHumidity:
    def test_humidity_is_none_rather_than_zero(self):
        """Zero would be indistinguishable from bone-dry air, and would be
        applied to the density as though it had been measured."""
        reading = BME280(FakeBus(chip_id=BMP280_CHIP_ID)).read()

        assert reading.humidity_pct is None
        assert reading.temp_c is not None
        assert reading.pressure_hpa is not None

    def test_it_does_not_write_ctrl_hum_to_a_chip_without_one(self):
        bus = FakeBus(chip_id=BMP280_CHIP_ID)
        BME280(bus).read()

        assert CTRL_HUM_REG not in [reg for reg, _ in bus.writes]


class TestBusFailuresNeverRaise:
    """A sensor read must never be able to stop someone hitting balls."""

    class ExplodingBus(FakeBus):
        def read_i2c_block_data(self, address, register, length):
            if register == DATA_REG:
                raise OSError(121, "Remote I/O error")
            return super().read_i2c_block_data(address, register, length)

    def test_a_bus_error_mid_read_returns_none(self):
        assert BME280(self.ExplodingBus()).read() is None

    def test_a_later_read_can_still_succeed(self):
        """A loose Dupont wire drops out and comes back; one bad read must not
        latch the sensor off for the rest of the session."""
        bus = self.ExplodingBus()
        sensor = BME280(bus)
        assert sensor.read() is None

        bus.read_i2c_block_data = FakeBus.read_i2c_block_data.__get__(bus, FakeBus)

        assert sensor.read() is not None
