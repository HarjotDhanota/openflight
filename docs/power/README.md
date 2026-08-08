# Battery and power status

OpenFlight can monitor three independent signals:

- whole-pack voltage and state of charge from a MAX1704x fuel gauge;
- external-power state from an active-low power-loss-detect (PLD) GPIO;
- Raspberry Pi 5 `EXT5V_V` rail voltage and undervoltage history.

The MAX1704x reader supports the gauge used by Geekworm X120x/X12xx boards and
UPS-Lite hardware. The currently verified board profile is `x1209`, whose PLD
line is BCM GPIO 6. Other boards can still provide pack data when their gauge
answers at the configured I2C address, but their PLD wiring must be declared
explicitly and verified.

Rail health is available only on a Raspberry Pi 5 with the PMIC ADC exposed by
`vcgencmd pmic_read_adc`. Readers are independent: hardware with only a gauge
shows the battery bar, while a Pi 5 without a UPS can still show the rail dot.

## Quick setup for X1209

Create `~/.config/openflight/power.json` containing:

```json
{"board": "x1209"}
```

Monitoring is enabled by default when supported hardware is detected. No GPIO
is configured unless a known board profile or `pld_gpio` explicitly declares
one.

## Configuration

All settings are optional. An invalid setting falls back independently to its
default, and an unreadable file falls back to all defaults.

| Key | Default | Accepted value |
|---|---:|---|
| `board` | `null` | `null` or known profile (`"x1209"`) |
| `enabled` | `true` | boolean |
| `sample_interval_s` | `2.0` | finite number, `0.5`–`60.0` |
| `heartbeat_seconds` | `10.0` | finite number, `1.0`–`300.0` |
| `rail_amber_volts` | `5.0` | finite number, `0.0`–`6.0` |
| `rail_red_volts` | `4.9` | finite number, `0.0`–`6.0`; must be below amber |
| `pack_low_volts` | `3.6` | finite number, `2.5`–`4.3` |
| `pack_critical_volts` | `3.4` | finite number, `2.5`–`4.3`; must be below low |
| `shutdown_volts` | `3.2` | finite number, `2.5`–`4.3`; cannot exceed critical |
| `auto_shutdown_enabled` | `false` | boolean |
| `shutdown_grace_seconds` | `60` | integer, `10`–`600` |
| `dwell_samples` | `15` | integer, `1`–`600` |
| `deadband_volts` | `0.05` | finite number, `0.0`–`1.0` |
| `pld_gpio` | `null` | `null` or BCM GPIO integer `0`–`27` |
| `i2c_bus` | `1` | integer, `0`–`20` |
| `i2c_address` | `54` | integer `8`–`119`, decimal string, or hex string such as `"0x36"` |

`pld_trusted` is intentionally not configurable. A known board profile makes
its shipped PLD wiring trusted immediately. A bare `pld_gpio` declaration does
not: a HIGH input could be a floating pull-up, so it is reported as `unknown`
until that line has read LOW once. LOW means battery operation and proves the
line is driven; subsequent HIGH readings then mean external power. This keeps a
floating GPIO from suppressing low-battery protection.

## Command-line options

- `--power` enables monitoring even if `power.json` disables it.
- `--no-power` disables monitoring and wins over every other setting.
- `--power-board x1209` selects a verified board profile.
- `--power-shutdown` opts into automatic low-voltage shutdown.
- `--power-shutdown-volts 3.2` overrides the shutdown threshold.

The same options pass through `scripts/start-kiosk.sh`. CLI values override
`power.json`; the file overrides defaults.

Automatic shutdown is off by default. When enabled, it arms only after the
configured low-voltage dwell while the trusted source state is `battery`. The
UI provides a grace-period countdown and an explicit **Keep running** action;
canceling latches for the rest of the server process so it does not repeatedly
re-arm. A failed `systemctl poweroff` is reported and is not retried.

## Hardware verification

Check that the gauge answers at `0x36` on bus 1:

```bash
i2cdetect -y 1
```

The table should contain `36`. For an X1209, configure GPIO 6 as a pulled-up
input and inspect it:

```bash
pinctrl set 6 ip pu
pinctrl get 6
```

With the verified active-low wiring, `hi` means external/wall power and `lo`
means battery power. Check both directions by disconnecting and reconnecting
the wall supply. Do not assume another board uses this pin or polarity.

The displayed percentage is the fuel gauge's whole-pack estimate. In a 1S4P
holder all cells share one pack voltage, so the gauge cannot identify one weak,
disconnected, or failed parallel cell. Cell-level inspection remains a
separate maintenance check.

On a Pi 5, rail voltage comes from `EXT5V_V` and the rail status considers only
the current and historical undervoltage bits. CPU frequency caps, throttling,
and thermal-limit bits are not supply faults.
