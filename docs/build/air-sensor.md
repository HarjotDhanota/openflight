# BME280 Air-Density Sensor

Carry depends on the air the ball flies through. A BME280 on the Pi's I2C bus
measures temperature, station pressure and humidity, and OpenFlight flies each
shot's as-measured ("raw") flight in that air. Without a sensor the raw flight
uses ISA sea-level air (15 °C, 1013.25 hPa, dry: 1.225 kg/m³) and every shot
says so, with the reason.

The normalized flight never uses the sensor. It stays at TrackMan's reference
(25 °C, 1013.25 hPa, RH 50 %, no wind), so normalized numbers compare across
days and venues. See [ballistics](../how-it-works/ballistics.md#raw-and-normalized).

This is optional, and there is nothing to switch on: the kiosk looks for a
BME280 at startup and uses it when it answers.

## What To Buy

| Part | Product |
|------|---------|
| BME280 breakout | [Adafruit BME280, product 2652](https://www.adafruit.com/product/2652) (STEMMA QT) or any BME280 breakout with a regulator and I2C pull-ups |
| Cable | The same Qwiic/STEMMA QT-to-Dupont cable as the [inclinometer](inclinometer.md#what-to-buy), or a STEMMA QT cable from the LIS3DH's spare socket |

Buy a **BME280**, not a BMP280. They look alike and are often mislabelled, but
the BMP280 has no humidity sensor. OpenFlight reads the chip ID and refuses a
BMP280 rather than guess its humidity: the Conditions tab then says
"a BMP280 at 0x76 has no humidity sensor; a BME280 is required".

## Wiring

Power down the Pi first. The BME280 shares I2C bus 1 with the LIS3DH: the LIS3DH
answers at `0x18`/`0x19` and the BME280 at `0x76`/`0x77`, so they do not clash.

| BME280 pin | Raspberry Pi physical pin | Pi signal |
|------------|---------------------------|-----------|
| `VIN` | **1** or **17** | 3.3 V |
| `GND` | **6**, **9**, **14** or **20** | Ground |
| `SDA` (`SDI`) | **3** | GPIO2 / I2C SDA |
| `SCL` (`SCK`) | **5** | GPIO3 / I2C SCL |

Both addresses are tried, `0x76` first; boards differ in which one they strap.
Do not wire the sensor to a 5 V pin.

## Placement

Put the sensor in the air the ball flies through, outside the enclosure's warm
air: away from the Pi, its cooler's exhaust, and the back of the screen. A sensor
next to the Pi reads the enclosure, not the range. OpenFlight reads it in forced
mode every 5 s and lets it sleep in between, which keeps the sensor's own
self-heating off the reading.

Each forced read waits 17 ms before checking completion. This is the rounded-up
16.2 ms maximum derived for temperature ×1, pressure ×4 and humidity ×1 from
[Bosch's datasheet, section 9.1](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bme280-ds002.pdf).
It then polls the status bit and refuses a conversion that never finishes, a
short register read, or the documented skipped-channel sentinel values. These
checks are covered with an injected bus; physical sensor validation is pending.

## Enable And Verify

I2C is the same setup as the inclinometer's
([Enable I2C on the Pi](inclinometer.md#enable-i2c-on-the-pi)). Then:

```bash
i2cdetect -y 1                                            # expect 76 or 77
uv run python scripts/hardware-test/read_bme280.py --count 5
```

The script prints temperature, station pressure, humidity and the density the
raw flight would use. Station pressure is the absolute pressure where the unit
stands, so it is lower than the sea-level pressure a weather report gives at
altitude; that is expected.

## What The Kiosk Does

- **Startup.** The server looks for the BME280 on bus 1 and prints
  `Air sensor: BME280 at 0x76`, or `Air sensor: none, flights use ISA air (…)`
  with the reason. `--air-sensor-bus N` reads another bus; `--no-air-sensor`
  skips the sensor, and every shot then records ISA air with that reason.
- **Each shot** records `air_density_kg_m3`, `air_density_source` (`bme280` or
  `isa_default`) and `air_conditions` (temperature, pressure, humidity and the
  reading's age, or the reason ISA air was used). The flight's `as_measured`
  block names the same source. A late update to a shot keeps the air it was
  first flown in.
- **Stale or failing sensor.** One missed read is ignored. When no good reading
  is newer than 60 s, or a reading lies outside the BME280's range (−40 to 85 °C,
  300 to 1100 hPa, 0 to 100 %), shots use ISA air and record why. Nothing is
  filled in.
- **Conditions tab.** What the sensor measured and the altitude the air plays
  like, or that standard air is assumed and why.
- **Session log.** `session_start` carries the sensor's address and the air at
  start; an `air_conditions` entry is written whenever the source or the reason
  changes; every `shot_detected` carries the shot's air.

The sensor corrects air density only. Nothing in OpenFlight measures wind.

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `smbus2 is not installed` | Run `uv sync` on the Pi. |
| `I2C bus 1 unavailable` | Enable I2C and reboot; check `/dev/i2c-1` exists. |
| `no BME280 on I2C bus 1 (0x76: …; 0x77: …)` | Wiring, power, and `i2cdetect -y 1`. |
| `a BMP280 … has no humidity sensor` | The board is a BMP280; replace it with a BME280. |
| `the last BME280 reading is … s old` | A loose wire or a bus fault after startup; the reason ends with the last read error. |
| Temperature reads several degrees warm | Move the sensor away from the Pi and the screen. |
