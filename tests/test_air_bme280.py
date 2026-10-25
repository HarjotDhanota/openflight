"""The BME280 driver, against Bosch's datasheet.

The bus is injected: ``FakeBus`` answers at the register level, as smbus2 would, so
the register map, the forced-mode handshake and the burst read are all exercised.

Temperature and pressure are checked against the worked example in Bosch's BMP280
datasheet (BST-BMP280-DS001, section 3.12: the same trimming words and the same
compensation as the BME280's, datasheet BST-BME280-DS002 section 4.2.3). Humidity has
no published worked example, so it is checked against the datasheet's own 32-bit
integer formula (section 4.2.3), structurally different from the driver's
floating-point one, on typical BME280 trimming words.
"""

import pytest

from openflight.air.bme280 import (
    BME280,
    BMP280_CHIP_ID,
    CHIP_ID_REG,
    CTRL_HUM_REG,
    CTRL_MEAS_REG,
    DATA_REG,
    PRIMARY_ADDRESS,
    SECONDARY_ADDRESS,
    STATUS_REG,
    AirSensorNotFound,
    BME280IdentityError,
    find_bme280,
)

BME280_CHIP_ID = 0x60

# BMP280 datasheet section 3.12, the worked example's trimming words.
DATASHEET_CALIB = {
    "T1": 27504,
    "T2": 26435,
    "T3": -1000,
    "P1": 36477,
    "P2": -10685,
    "P3": 3024,
    "P4": 2855,
    "P5": 140,
    "P6": -7,
    "P7": 15500,
    "P8": -14600,
    "P9": 6000,
    # typical BME280 humidity words (the example has none)
    "H1": 75,
    "H2": 362,
    "H3": 0,
    "H4": 313,
    "H5": 50,
    "H6": 30,
}
DATASHEET_ADC_T = 519888
DATASHEET_ADC_P = 415148
DATASHEET_T_C = 25.08
DATASHEET_P_PA = 100653.27
ADC_H = 30000


def _u16le(value):
    return [value & 0xFF, (value >> 8) & 0xFF]


def calibration_block_1(calib):
    """0x88..0xA1: temperature, pressure, a reserved byte, H1."""
    block = _u16le(calib["T1"]) + _u16le(calib["T2"] & 0xFFFF) + _u16le(calib["T3"] & 0xFFFF)
    block += _u16le(calib["P1"])
    for key in ("P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9"):
        block += _u16le(calib[key] & 0xFFFF)
    block += [0x00, calib["H1"] & 0xFF]
    assert len(block) == 26
    return block


def calibration_block_2(calib):
    """0xE1..0xE7: H2, H3, the 12-bit H4/H5 pair sharing 0xE5, H6."""
    h4, h5 = calib["H4"] & 0xFFF, calib["H5"] & 0xFFF
    return [
        *_u16le(calib["H2"] & 0xFFFF),
        calib["H3"] & 0xFF,
        (h4 >> 4) & 0xFF,
        (h4 & 0x0F) | ((h5 & 0x0F) << 4),
        (h5 >> 4) & 0xFF,
        calib["H6"] & 0xFF,
    ]


def data_block(adc_t=DATASHEET_ADC_T, adc_p=DATASHEET_ADC_P, adc_h=ADC_H):
    """0xF7..0xFE: pressure, temperature (20-bit each), humidity (16-bit)."""
    return [
        (adc_p >> 12) & 0xFF,
        (adc_p >> 4) & 0xFF,
        (adc_p << 4) & 0xF0,
        (adc_t >> 12) & 0xFF,
        (adc_t >> 4) & 0xFF,
        (adc_t << 4) & 0xF0,
        (adc_h >> 8) & 0xFF,
        adc_h & 0xFF,
    ]


class FakeBus:
    """A BME280 at the register level; records writes so the handshake can be checked."""

    def __init__(self, *, chip_id=BME280_CHIP_ID, addresses=(PRIMARY_ADDRESS,), calib=None):
        self.chip_id = chip_id
        self.addresses = set(addresses)
        self.calib = {**DATASHEET_CALIB, **(calib or {})}
        self.data = data_block()
        self.writes = []
        self.reads = []
        self.busy_reads = 0
        self.fail_data_reads = 0
        self.closed = False

    def _check(self, address):
        if address not in self.addresses:
            raise OSError(121, "Remote I/O error")

    def read_byte_data(self, address, register):
        self._check(address)
        self.reads.append(register)
        if register == CHIP_ID_REG:
            return self.chip_id
        if register == STATUS_REG and self.busy_reads > 0:
            self.busy_reads -= 1
            return 0x08
        return 0x00

    def read_i2c_block_data(self, address, register, length):
        self._check(address)
        self.reads.append(register)
        if register == 0x88:
            return calibration_block_1(self.calib)[:length]
        if register == 0xE1:
            return calibration_block_2(self.calib)[:length]
        if register == DATA_REG:
            if self.fail_data_reads > 0:
                self.fail_data_reads -= 1
                raise OSError(121, "Remote I/O error")
            return self.data[:length]
        return [0x00] * length

    def write_byte_data(self, address, register, value):
        self._check(address)
        self.writes.append((register, value))

    def close(self):
        self.closed = True


def _sensor(bus=None, address=PRIMARY_ADDRESS):
    sensor = BME280(bus=bus or FakeBus(), address=address)
    sensor.initialize()
    return sensor


def _datasheet_int_t_fine(adc_t, c):
    var1 = (((adc_t >> 3) - (c["T1"] << 1)) * c["T2"]) >> 11
    var2 = (((((adc_t >> 4) - c["T1"]) * ((adc_t >> 4) - c["T1"])) >> 12) * c["T3"]) >> 14
    return var1 + var2


def _datasheet_int_pressure_pa(adc_p, t_fine, c):
    """BME280 datasheet 4.2.3, BME280_compensate_P_int64 (Q24.8 Pa)."""
    var1 = t_fine - 128000
    var2 = var1 * var1 * c["P6"]
    var2 = var2 + ((var1 * c["P5"]) << 17)
    var2 = var2 + (c["P4"] << 35)
    var1 = ((var1 * var1 * c["P3"]) >> 8) + ((var1 * c["P2"]) << 12)
    var1 = (((1 << 47) + var1) * c["P1"]) >> 33
    p = 1048576 - adc_p
    p = (((p << 31) - var2) * 3125) // var1
    var1 = (c["P9"] * (p >> 13) * (p >> 13)) >> 25
    var2 = (c["P8"] * p) >> 19
    return (((p + var1 + var2) >> 8) + (c["P7"] << 4)) / 256.0


def _datasheet_int_humidity_pct(adc_h, t_fine, c):
    """BME280 datasheet 4.2.3, bme280_compensate_H_int32 (Q22.10 %RH)."""
    x = t_fine - 76800
    left = (((adc_h << 14) - (c["H4"] << 20) - (c["H5"] * x)) + 16384) >> 15
    right = (
        (((((x * c["H6"]) >> 10) * (((x * c["H3"]) >> 11) + 32768)) >> 10) + 2097152) * c["H2"]
        + 8192
    ) >> 14
    x = left * right
    x -= ((((x >> 15) * (x >> 15)) >> 7) * c["H1"]) >> 4
    x = min(max(x, 0), 419430400)
    return (x >> 12) / 1024.0


class TestDatasheetCompensation:
    def test_the_worked_example_temperature_and_pressure(self):
        sample = _sensor().read(timestamp=1.0)
        assert sample.temperature_c == pytest.approx(DATASHEET_T_C, abs=0.005)
        assert sample.pressure_hpa * 100.0 == pytest.approx(DATASHEET_P_PA, abs=0.05)

    @pytest.mark.parametrize(
        ("adc_t", "adc_p"), [(DATASHEET_ADC_T, DATASHEET_ADC_P), (480000, 360000), (540000, 300000)]
    )
    def test_pressure_matches_the_datasheet_integer_formula(self, adc_t, adc_p):
        bus = FakeBus()
        bus.data = data_block(adc_t=adc_t, adc_p=adc_p)
        expected = _datasheet_int_pressure_pa(
            adc_p, _datasheet_int_t_fine(adc_t, bus.calib), bus.calib
        )
        assert _sensor(bus).read().pressure_hpa * 100.0 == pytest.approx(expected, abs=1.0)

    @pytest.mark.parametrize("adc_h", [23000, 26000, ADC_H, 36000])
    def test_humidity_matches_the_datasheet_integer_formula(self, adc_h):
        bus = FakeBus()
        bus.data = data_block(adc_h=adc_h)
        expected = _datasheet_int_humidity_pct(
            adc_h, _datasheet_int_t_fine(DATASHEET_ADC_T, bus.calib), bus.calib
        )
        humidity = _sensor(bus).read().humidity_pct
        assert 0.0 < humidity < 100.0
        assert humidity == pytest.approx(expected, abs=0.02)

    def test_a_signed_trimming_word_is_read_signed(self):
        """dig_T3 is -1000 in the example: read unsigned it would be 64536."""
        assert _sensor().calibration.t3 == -1000
        assert _sensor().calibration.p2 == -10685

    @pytest.mark.parametrize(("h4", "h5"), [(313, 50), (-100, -200), (2047, -2048)])
    def test_the_packed_twelve_bit_humidity_words_round_trip(self, h4, h5):
        sensor = _sensor(FakeBus(calib={"H4": h4, "H5": h5}))
        assert (sensor.calibration.h4, sensor.calibration.h5) == (h4, h5)


class TestIdentity:
    def test_a_bme280_initializes(self):
        sensor = _sensor()
        assert sensor.chip_id == BME280_CHIP_ID

    def test_a_bmp280_is_refused_and_named(self):
        """Same pins and registers but no humidity channel: its density would need a
        guessed humidity, so it is not used."""
        with pytest.raises(BME280IdentityError, match="BMP280.*humidity"):
            _sensor(FakeBus(chip_id=BMP280_CHIP_ID))

    def test_an_unknown_chip_is_refused(self):
        with pytest.raises(BME280IdentityError, match="0x1f"):
            _sensor(FakeBus(chip_id=0x1F))


class TestForcedMode:
    def test_a_clear_status_before_conversion_starts_does_not_read_old_data(self, monkeypatch):
        elapsed = [0.0]
        monkeypatch.setattr(
            "openflight.air.bme280.time.sleep",
            lambda delay: elapsed.__setitem__(0, elapsed[0] + delay),
        )

        class DelayedBus(FakeBus):
            def read_i2c_block_data(self, address, register, length):
                if register == DATA_REG and elapsed[0] < 0.0162:
                    return data_block(adc_t=480000)
                return super().read_i2c_block_data(address, register, length)

        sample = _sensor(DelayedBus()).read()
        assert sample.temperature_c == pytest.approx(DATASHEET_T_C, abs=0.005)

    @pytest.mark.parametrize("channel", ["adc_t", "adc_p", "adc_h"])
    def test_a_skipped_channel_sentinel_is_not_a_measurement(self, channel):
        bus = FakeBus()
        bus.data = data_block(**{channel: 0x8000 if channel == "adc_h" else 0x80000})
        with pytest.raises(OSError, match="skipped"):
            _sensor(bus).read()

    def test_ctrl_hum_is_written_before_ctrl_meas(self):
        """Datasheet 5.4.3: ctrl_hum takes effect only on the next ctrl_meas write."""
        bus = FakeBus()
        _sensor(bus).read()
        registers = [register for register, _ in bus.writes]
        assert registers.index(CTRL_HUM_REG) < registers.index(CTRL_MEAS_REG)

    def test_each_read_is_one_forced_conversion(self):
        """Forced mode sleeps between samples, keeping self-heating off the reading."""
        bus = FakeBus()
        _sensor(bus).read()
        ctrl_meas = [value for register, value in bus.writes if register == CTRL_MEAS_REG][-1]
        assert ctrl_meas & 0b11 == 0b01

    def test_it_waits_for_the_conversion(self):
        bus = FakeBus()
        bus.busy_reads = 3
        _sensor(bus).read()
        assert bus.reads.count(STATUS_REG) >= 4

    def test_a_conversion_that_never_finishes_raises(self):
        bus = FakeBus()
        sensor = _sensor(bus)
        bus.busy_reads = 10_000
        with pytest.raises(OSError, match="conversion"):
            sensor.read()

    def test_a_bus_error_raises_and_the_next_read_recovers(self):
        bus = FakeBus()
        sensor = _sensor(bus)
        bus.fail_data_reads = 1
        with pytest.raises(OSError):
            sensor.read()
        assert sensor.read().temperature_c == pytest.approx(DATASHEET_T_C, abs=0.005)

    def test_the_sample_carries_its_timestamp(self):
        assert _sensor().read(timestamp=123.5).timestamp == 123.5


class TestFind:
    def test_the_primary_address_is_preferred(self):
        sensor = find_bme280(bus=FakeBus(addresses=(PRIMARY_ADDRESS, SECONDARY_ADDRESS)))
        assert sensor.address == PRIMARY_ADDRESS

    def test_the_secondary_address_is_tried(self):
        sensor = find_bme280(bus=FakeBus(addresses=(SECONDARY_ADDRESS,)))
        assert sensor.address == SECONDARY_ADDRESS

    def test_nothing_on_the_bus_names_both_addresses(self):
        bus = FakeBus(addresses=())
        with pytest.raises(AirSensorNotFound, match="0x76.*0x77"):
            find_bme280(bus=bus)
        assert bus.closed

    def test_a_bmp280_is_reported_as_the_reason(self):
        with pytest.raises(AirSensorNotFound, match="BMP280"):
            find_bme280(bus=FakeBus(chip_id=BMP280_CHIP_ID))

    def test_close_releases_the_bus(self):
        bus = FakeBus()
        find_bme280(bus=bus).close()
        assert bus.closed
