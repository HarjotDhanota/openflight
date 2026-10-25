#!/usr/bin/env python3
"""Print BME280 temperature, station pressure, humidity and the air density flights use."""

import argparse
import time

from openflight.air.bme280 import AirSensorNotFound, find_bme280
from openflight.air.conditions import from_sample


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", type=int, default=1, help="I2C bus number (default: 1)")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--count", type=int, default=0, help="Readings to print; 0 runs forever")
    args = parser.parse_args()

    try:
        sensor = find_bme280(bus_number=args.bus)
    except AirSensorNotFound as error:
        raise SystemExit(f"BME280 not found: {error}") from error
    print(f"BME280 detected on I2C-{args.bus} at 0x{sensor.address:02x}")
    try:
        index = 0
        while args.count == 0 or index < args.count:
            sample = sensor.read()
            air = from_sample(sample, now=sample.timestamp)
            density = (
                f"{air.air_density_kg_m3:.4f} kg/m3" if air.measured else f"ISA ({air.reason})"
            )
            print(
                f"T={sample.temperature_c:.2f} C  p={sample.pressure_hpa:.2f} hPa  "
                f"RH={sample.humidity_pct:.1f} %  density={density}"
            )
            index += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        sensor.close()


if __name__ == "__main__":
    main()
