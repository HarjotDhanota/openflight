# Build Checklist — Harjot's Unit

*Working parts list for this specific build. Layout A (OPS243 on GPIO UART, IWR6843 on USB) + X1209 UPS + the two optional I²C sensors. Last updated 2026-07-29.*

Pin assignments and wiring are in [hardware-integration.md](hardware-integration.md). Generic parts list is [PARTS.md](PARTS.md).

---

## A. Already have — confirm

| Part | Notes |
|---|---|
| Raspberry Pi 5, 4 GB | |
| Pi 5 Active Cooler | **Check clearance against the X1209's 13.5 mm standoffs** |
| 27 W USB-C PSU (5V/5A) | Now feeds the **X1209**, not the Pi directly |
| microSD 32 GB+ | |
| OPS243-A (non-WiFi) | On GPIO UART |
| ROADOM 7" 1024×600 | HDMI + USB touch + 3-pin GPIO power cable |
| **micro-HDMI → HDMI cable** | ✅ shipped with the display. Use Pi 5 **HDMI0** (the port nearest USB-C) for the primary display |
| SEN-14262 + 47 kΩ in R17 | ✅ working as of 2026-07-29 |
| Raspberry Pi Pico | Future camera trigger |
| Ender 3 Pro | |
| 2× K-LD7 + FTDI | Deprecated, not in this build |
| **IWR6843LEVM** | ❓ **Confirm you actually have this** — the checked-in calibration is another board's |

## B. Just bought

| Part | |
|---|---|
| Geekworm X1209 UPS HAT | Top-mount, 5.1 V 6 A, 40-pin pass-through |
| Geekworm X12-A1 | 4-cell 18650 holder, per-cell fuses, stackable |
| 4× Samsung 35E 3500 mAh | ⚠️ **Confirm these are FLAT TOP / UNPROTECTED** — protected cells are ~3 mm longer and may not seat |

Expect **~3.1 h runtime** at 14 W typical, 2.4 h at 18 W.

## C. Still to buy — sensors

| Part | Product | Price |
|---|---|---|
| BME280 (air density) | [Adafruit 2652](https://www.adafruit.com/product/2652) | $14.95 |
| LIS3DH (mount tilt) | [Adafruit 2809](https://www.adafruit.com/product/2809) | $4.95 |
| QT → **female sockets**, 150 mm | [Adafruit 4397](https://www.adafruit.com/product/4397) | $0.95 |
| QT → QT, sensor to sensor | [4210](https://www.adafruit.com/product/4210) 100 mm or [4401](https://www.adafruit.com/product/4401) 200 mm | $0.95-1.25 |

**Not 4209** — that's the male-header version and will not mate with the Pi's male pins.

Pick the QT-QT length by measuring from wherever the BME280 sits (rear intake grille, cool-air path) to the **radar mount plate**, where the LIS3DH has to live. If in doubt take the 200 mm.

## D. Still to buy — cables and bits

| Part | Why | Price |
|---|---|---|
| Female–female Dupont jumpers | OPS243 J3 ×4, plus spares | ~$5 |
| XH2.54 2-pin pigtail | *Only if* you move the display onto a UPS 5 V output — see §F | ~$3 |
| Spirit level | One-time leveling zero at assembly | ~$5 |
| Inline USB-A power meter | Measure the IWR6843 — the last unknown in the power budget | ~$12 |

**Roughly $40 for everything in C and D.**

## E. Deliberately NOT buying

| Part | Why not |
|---|---|
| Digital angle gauge (~$15) | Optional accuracy upgrade only (±0.25° vs ±0.6°). A spirit level is enough |
| GPIO 1-to-2 splitter | Was needed to solve 5 V contention. The X1209's 5 V outputs solve it instead |
| ADXL355 / SCL3300 | ±1.15° factory offset means they need the same zeroing step. $33-72 for nothing |
| 21700 cells | X12-A1 is 18650-only. Stack a second X12-A1 if you want more later |
| Separate 12 V adapter | Your 27 W USB-C PSU feeds the X1209 directly. Optional upgrade for more charge headroom |

## F. Decisions still open

**1. Where the display gets its 5 V.** Two options:

- **Header pin 2** (as originally planned) — no extra cable, but 550 mA rides the Pi's rail
- **X1209 5 V XH2.54 output** — needs one pigtail, takes 550 mA off the Pi entirely, frees a header pin

Second is better engineering. Either works.

**2. Connector types to verify before ordering Dupont wires** — see hardware-integration.md §3.3:

- What's physically on the OPS243 J3 header?
- What terminates the display's 3-pin GPIO cable?
- What USB connector is on the IWR6843LEVM?

## G. Configuration this build needs

`/boot/firmware/config.txt`:
```
enable_uart=1
dtparam=i2c_arm=on
usb_max_current_enable=1
```

Pi EEPROM (`sudo rpi-eeprom-config -e`) — required by the X1209:
```
POWER_OFF_ON_HALT=1
PSU_MAX_CURRENT=5000
```

`PSU_MAX_CURRENT=5000` is what keeps the USB budget at 1.6 A when running off the UPS instead of a PD supply. Without it you drop to 600 mA against a ~550 mA draw.

Groups: `sudo usermod -aG dialout,gpio,i2c "$USER"`

Expected `i2cdetect -y 1`: **0x36** (UPS fuel gauge), **0x76 or 0x77** (BME280), **0x18** (LIS3DH). No collisions.

## H. Pins now spoken for

Beyond the 13 in hardware-integration.md §3, the X1209 adds:

| Pin | BCM | Use |
|---|---|---|
| 31 | GPIO6 | UPS power-loss detect — **was reserved for camera strobe, move that** |
| 36 | GPIO16 | UPS battery charge control |
| 3, 5 | GPIO2/3 | I²C — shared with the sensors, no conflict |

Still leaves ~18 free pins.
