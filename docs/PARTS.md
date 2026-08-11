# OpenFlight Parts List

Hardware components for building the OpenFlight golf launch monitor.

> **Next step after gathering parts:** See the [Raspberry Pi Setup Guide](raspberry-pi-setup.md) for assembly and software installation.

## Core Components

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **OPS243 Radar** | Doppler radar for ball/club speed detection | [OmniPreSense](https://omnipresense.com/product/ops243-doppler-radar-sensor/) | $249 |
| **Raspberry Pi 5** | Main compute unit (4GB+ recommended) | [Adafruit](https://www.adafruit.com/product/5812) | $130 |

> **WARNING: Do NOT buy the OPS243-A-W (WiFi version).** The WiFi module locks the serial baud rate to 19200, which is far too slow for I/Q data transfer. OpenFlight requires the standard **OPS243** (USB only) which runs at 57600 baud over CDC-ACM. The WiFi version is not compatible.
| **7" Touchscreen Display** | HMTECH 7" 1024x600 IPS display | [Amazon](https://www.amazon.com/dp/B0D3QB7X4Z) | $46 |

> **Display alternative:** The [Raspberry Pi Touch Display 2](https://www.raspberrypi.com/products/touch-display-2/) (7" 720x1280, MIPI DSI) also works with the Pi 5. If you use it, print the `Touch_Display2_backplate.stl` and `Touch_Display2_shell.stl` from the IARC case instead of `monitor_shell.stl` — see the [IARC case instructions](../cad/IARC_case/README.md).

## Sound Trigger (for Rolling Buffer Mode)

The sound trigger detects club impact to precisely time radar captures. Essential for spin detection via rolling buffer mode.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **SparkFun SEN-14262** | Sound Detector with envelope/gate outputs | [SparkFun](https://www.sparkfun.com/products/14262) | $12 |
| **Through-hole resistor** | For R17 pad on SEN-14262 to reduce sensitivity (see note) | Any electronics supplier | $1 |
| **Jumper Wires** | 3 wires: GATE → HOST_INT, VCC → 3.3V, GND → GND | Any | $5 |

> **R17 resistor:** The SEN-14262 is rated for 5V but runs at 3.3V in this setup, which can cause the GATE output to stick high. Soldering a resistor into the R17 through-hole position (in parallel with the onboard 100kΩ R3) reduces preamp gain and fixes this. Start with 47kΩ; use a lower value (e.g. 33kΩ) if the sensor is still too sensitive for your environment.

### Sound Trigger Wiring

```
SEN-14262               Raspberry Pi           OPS243
┌───────────┐          ┌──────────┐          ┌──────────┐
│ VCC ──────┼──────────┤ 3.3V     │          │          │
│           │          │          │          │          │
│ GATE ─────┼──────────┼──────────┼──────────┤ HOST_INT │
│           │          │          │          │ (J3 P3)  │
│ GND ──────┼──────────┤ GND      ├──────────┤ GND      │
│           │          │          │          │ (J3 P10) │
└───────────┘          └──────────┘          └──────────┘
```

> **J3 pin 1 is a GPIO, not ground.** Ground is **pin 10**, and pin 1 sits at the
> **right** end of the J3 header, so the numbering runs right to left. This was
> corrected in [PR #166](https://github.com/jewbetcha/openflight/pull/166).

See [sound-trigger-wiring.md](sound-trigger-wiring.md) for detailed instructions and troubleshooting.

## Angle Radar (TI IWR6843) — CURRENT

This is the supported angle radar. It measures vertical and horizontal launch
angle, and supplies the pre-impact frames club path is derived from.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **TI IWR6843LEVM** | 60 GHz mmWave evaluation board, 4 RX × 3 TX | [TI](https://www.ti.com/tool/IWR6843LEVM) | $150 |
| **USB cable (data-capable)** | Connects the LEVM's CP2105 serial bridge to the Pi. Charge-only cables will not enumerate — check the connector on your board revision | Any | $5 |
| **Jumper wire** | 1 wire: detector `GATE` → Pi BCM17 / physical pin 11, alongside the existing `GATE` → OPS `HOST_INT` | Any | $1 |

The board needs **custom firmware** — it does not work out of the box. The
stock TI demo does not expose the raw radar cube OpenFlight needs. A validated
prebuilt image ships in `firmware/releases/`, so you do not need the TI
toolchain to flash it.

You also need physical access to the board's **boot-mode switch (S1.1)** and
**RESET button** to flash. Both are on the LEVM itself; nothing to buy.

### IWR6843 Setup

Two connection layouts are supported, and which one you can use depends on your
OPS243 variant:

| Layout | OPS243 connection | Extra parts needed |
|--------|-------------------|--------------------|
| **A (validated)** | Pi GPIO UART header | 4 jumper wires (5V, GND, TX, RX) |
| **B** | Powered USB hub | [Powered USB hub](https://www.amazon.com/dp/B0CN3F9Y1Z) (~$20) |

Layout A keeps the TI board on USB and moves the OPS243 to the Pi's GPIO
header, which is what the power budget requires — the Pi cannot supply both
radars over USB.

> [!WARNING]
> Layout A does **not** work with a **WiFi-equipped OPS243-A**. Its onboard WiFi
> module already drives the radar's UART receive line, so the Pi cannot send it
> commands. WiFi OPS boards must use Layout B with a powered hub.

Full instructions: **[IWR6843 Operator Guide](iwr6843/README.md)** for wiring,
flashing, mounting, and geometry; **[Moving the OPS243 to the Pi GPIO
UART](ops243-uart-migration.md)** for the OPS side of Layout A.

---

## Angle Radar (K-LD7) — DEPRECATED

> **⚠️ DEPRECATED — do not buy for new builds.** The K-LD7 angle radars have been superseded by a more capable radar chip. K-LD7 support remains in the software for existing builds but will not receive further development. The parts below are listed for reference only.

Two K-LD7 modules measure launch angle (vertical) and club path / aim direction (horizontal). The OPS243 handles speed; the K-LD7s provide **angle and distance only** (speed data aliases above 62 mph).

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **RFbeam K-LD7 (×2)** | 24 GHz FMCW radar for angle + distance | [RFbeam](https://rfbeam.ch/product/k-ld7-radar-transceiver/) | ~$60 ea |
| **FTDI USB-to-Serial adapter (×2)** | 3.3V FTDI board for K-LD7 UART (e.g. FT232RL) | [Amazon](https://www.amazon.com/s?k=ftdi+3.3v+usb+serial) | ~$10 |

> **EVAL board not required.** The K-LD7 bare module communicates over 3.3V UART (TX, RX, VCC, GND). Any 3.3V FTDI USB-to-serial adapter works. The official K-LD7 EVAL board (~$120 each) is only needed if you want the RFbeam GUI software for configuration — OpenFlight configures the radar over serial automatically.

### K-LD7 Connection

Each K-LD7 connects via a 3.3V FTDI adapter, appearing as `/dev/ttyUSB*` on Linux.

```
K-LD7 Module (UART) → FTDI 3.3V Adapter → USB → Raspberry Pi
```

One unit is mounted vertically (launch angle), one horizontally (club path / aim direction). A `--kld7-angle-offset` parameter corrects for mounting geometry — see the [setup guide](raspberry-pi-setup.md) for calibration.

## Environmental & Leveling Sensors — OPTIONAL

Two I²C sensors that share one bus and one pair of pins. Neither is required to
run OpenFlight; each removes a specific source of silent error.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **Adafruit BME280** | Temperature / pressure / humidity → measured air density for carry. Replaces the hardcoded ISA sea-level assumption, worth up to ~14 yd of driver carry at altitude | [Adafruit 2652](https://www.adafruit.com/product/2652) | $14.95 |
| **Adafruit LIS3DH** | 3-axis accelerometer → measured mount pitch and roll, so the unit corrects itself on uneven ground instead of needing `--iwr6843-tilt-deg` | [Adafruit 2809](https://www.adafruit.com/product/2809) | $4.95 |
| **QT → female sockets cable** | Connects the sensor chain to the Pi header. **Must be the FEMALE version** — the Pi header is male pins | [Adafruit 4397](https://www.adafruit.com/product/4397) | $0.95 |
| **QT → QT cable** | Chains BME280 to LIS3DH. Pick a length that reaches the radar mount plate | [Adafruit 4210](https://www.adafruit.com/product/4210) (100 mm) or [4401](https://www.adafruit.com/product/4401) (200 mm) | $0.95-1.25 |
| Spirit level | For the one-time leveling zero at assembly | Any | $5 |

**No soldering.** The chain is Pi header → 4397 → BME280 → 4210 → LIS3DH.

| 4397 wire | Pi physical pin |
|---|---|
| Red — 3.3V | 17 |
| Black — GND | 25 |
| Blue — SDA | 3 |
| Yellow — SCL | 5 |

> **Buy the BME280 from Adafruit or SparkFun, not a marketplace.** Generic
> "GY-BME280" modules very often carry a **BMP280** die, which has no humidity
> channel. Check with chip ID register `0xD0`: `0x60` = BME280, `0x58` = BMP280.
> Adafruit's board defaults to I²C address **0x77** (0x76 with the ADDR jumper
> closed); most generics default to 0x76.

> **I²C is not enabled by default on this build.** Add `dtparam=i2c_arm=on` to
> `/boot/firmware/config.txt` and add your user to the `i2c` group. See
> [hardware-integration.md](hardware-integration.md).

The LIS3DH mounts on the **radar's mount plate**, not the case, so it stays
rigid relative to the antenna when the mount is adjusted. See the leveling
design doc for the one-time zeroing procedure — a spirit level, once, at
assembly.

## Power & Accessories

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **27W USB-C Power Supply** | Official Pi 5 power supply (5V 5A). **Not optional** — below 5A the Pi caps total USB current at 600 mA instead of 1.6 A | [Adafruit](https://www.adafruit.com/product/5814) | $14 |
| micro-HDMI to HDMI cable | **Check what shipped with your display** — the Pi 5 has micro-HDMI ports, and some panels include a full-size HDMI cable that will not fit. The ROADOM 7" includes the correct micro-HDMI cable | Any | $8 if needed |
| MicroSD Card (32GB+) | For Pi OS and software | Any Class 10 | $10 |
| USB-A to Micro-USB Cable | For OPS243 radar connection | Any | $5 |

## Optional

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| Tripod Mount | For positioning the unit | 1/4"-20 mount | $10 |

---

## Cost Summary

| Category | ~Price |
|----------|--------|
| Core (OPS243, Pi 5, Display) | $355 |
| Sound Trigger (SEN-14262 + resistor + wires) | $18 |
| Power & Accessories (incl. micro-HDMI cable) | $37 |
| **Subtotal, no angle radar** | **~$410** |
| Angle Radar (IWR6843LEVM + cable + wire) — **current** | $156 |
| **Total with angle radar** | **~$566** |
| Environmental & leveling sensors (BME280 + LIS3DH + cables) — **optional** | $23 |
| Angle Radar (2× K-LD7 + FTDI adapters) — **deprecated** | $140 |

OpenFlight works without any angle radar: you get ball speed, club speed, smash
factor, spin rate, and estimated carry. The angle radar adds measured launch
angle (vertical and horizontal) and is what club path is derived from.

If you are building new, buy the **IWR6843**, not the K-LD7s. It costs about the
same as the two K-LD7s plus their FTDI adapters ($156 vs $140) and replaces both
of them with one board. The K-LD7 path is **deprecated** and kept only so
existing builds keep working.
