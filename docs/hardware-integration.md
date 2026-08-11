# Hardware Integration: Pinout and Power Architecture

*Written 2026-07-28. Covers the Layout A build (OPS243-A on GPIO UART, IWR6843 on USB) plus the two planned I²C sensors and reserved capacity for the camera subsystem.*

**Read this before wiring anything.** Three conflicts below are hard blockers that are invisible until you have the parts in your hands.

Every figure is tagged:

- **READ** — quoted from a datasheet, repo doc, or vendor documentation
- **MEASURED** — from published third-party measurement
- **ESTIMATED** — engineering estimate, stated as such
- **UNKNOWN** — not established; needs a bench measurement

---

## 1. Device inventory

| Device | Function | Transport | Power source | Draw @ 5V |
|---|---|---|---|---|
| Raspberry Pi 5 (4GB) | Compute | — | USB-C PD | 3.0 W idle / 7.0 W load **MEASURED** |
| OPS243-A | 24 GHz CW Doppler — speed, spin, trigger | GPIO UART0 | Header 5V | 1.5 W active, 0.6 W idle, 1.6 W max **READ** |
| IWR6843LEVM | 60 GHz FMCW — angles, club | USB (1 port, 2 tty) | USB bus | **UNKNOWN** — see §5.2 |
| ROADOM 7" 1024×600 | Display + touch | HDMI + USB HID | Header 5V *or* USB *or* wall | ~2.75 W (550 mA) **READ** (class figure) |
| SEN-14262 | Sound trigger | GPIO17 + OPS J3 pin 3 | Header 3.3V | ~0.02 W **ESTIMATED** |
| BME280 | Air density (weather branch) | I²C-1 | Header 3.3V | <0.001 W **READ** (3.6 µA @ 1 Hz) |
| LIS3DH / ADXL345 | Mount tilt (leveling branch) | I²C-1 | Header 3.3V | <0.002 W **READ** |
| *K-LD7 ×2 (deprecated)* | *24 GHz FMCW angles* | *2× USB via FTDI* | *USB bus* | *200 mA each* **READ** |
| *IMX296 camera (future)* | *Club pose* | *CSI-2* | *CSI connector* | *~0.5 W* **ESTIMATED** |
| *RP2040 Pico (future)* | *µs camera trigger* | *Secondary UART* | *Header 3.3V/5V* | *~0.5 W* **ESTIMATED** |

The two new I²C sensors are, combined, roughly **one four-thousandth** of the system's power draw. Power is not the reason to think carefully about them — pin allocation is.

---

## 2. Conflicts found

### 2.1 BLOCKER — the display and the OPS243-A both want the header's 5V pins

The 40-pin header has exactly **two** 5V pins: physical 2 and 4. Both are already claimed:

- `docs/ops243-uart-migration.md:60` — OPS243-A J3 pin 9 (`5V`) → "Pi physical pin **2 or 4**"
- The ROADOM's included 3-pin GPIO power cable carries **two 5V wires and one ground**, intended for pins **2, 4, and 6**

The two 5V wires are not two rails. Pins 2 and 4 are the same node, fed straight from the Pi's 5V rail; the cable doubles up purely to halve the per-wire current and voltage drop. At the panel's ~550 mA a single 26 AWG Dupont wire is well within rating.

**Resolutions, ranked:**

| # | Approach | 5V pins used | USB ports used | Verdict |
|---|---|---|---|---|
| **1** | **Display on ONE 5V wire (pin 2); OPS243 on pin 4** | 2 | 1 (touch only) | **Recommended.** Single power source, no splice, forward-compatible with battery. Accept slightly more voltage drop on the panel. |
| 2 | Splice pin 2 three ways (display ×2 + OPS243) | 1 | 1 | Electrically cleanest; adds a solder joint. The repo already mandates a 3-way splice for GATE, so this is a familiar technique. |
| 3 | Display on its own wall adapter (included in the box) | 0 | 1 | Zero risk, best for bench work. Two wall plugs — kills portability. |
| 4 | Display powered from its upper micro-USB port | 0 | 2 | **Avoid.** Moves 550 mA into the USB current budget, which is the scarcest resource in the system (§5.3). |

### 2.2 BLOCKER — I²C has never been enabled on this build

`grep -rn "i2c\|smbus\|dtparam" src/ scripts/ docs/` returns **no functional hits** — one incidental mention in `docs/Personal Research/` (an InnoMaker camera utility named `i2c.py`) and one binary match inside a PDF, nothing else outside `archive/`.

It is worse than that: **nothing in the repo enables any interface automatically.** `scripts/setup/setup.sh` touches no `config.txt`, no `raspi-config`, no `dtparam`. Even UART enablement is a manual step, and it isn't in `docs/raspberry-pi-setup.md` — it lives in `docs/ops243-uart-migration.md:95` and `docs/iwr6843/README.md:190`. Both sensor branches assume an I²C bus that does not currently exist and that no script will create.

GPIO2/GPIO3 (physical 3 and 5) are unclaimed, so there is no pin conflict — but `dtparam=i2c_arm=on` must be added and the Pi rebooted before either sensor can enumerate. This belongs in `docs/raspberry-pi-setup.md`, not buried in a feature branch.

### 2.3 BLOCKER — UART0 is gone, so the future Pico needs a different one

The camera subsystem needs an MCU (Pico/RP2040) generating µs-accurate XTR trigger pulses and timestamping the impact edge — `docs/Personal Research/camera-hardware-spec-v1.md:29,65,82` describes the role but assigns **no Pi-side pins**, so this conflict is inferred, not documented. The obvious default would be UART0 on GPIO14/15, and those pins now carry the OPS243-A (`ops243-uart-migration.md:62-63`). The OPS243 is not moving back to USB — the whole point of the migration was to free the USB bus.

The Pi 5 supports secondary UARTs via `dtoverlay=uart2-pi5` / `uart3-pi5` / `uart4-pi5` (there is **no** UART5 on Pi 5). Published GPIO mappings for these overlays are **inconsistent between sources** — one says GPIO4-7 is UART2 on Pi 5, another places UART3 on GPIO4/5. Do not wire from a blog post.

**Action:** reserve GPIO4/5 (pins 7, 29) *and* GPIO8/9 (pins 24, 21) as candidates, and confirm the actual mapping on the Pi itself with `dtoverlay -h uart2-pi5` before soldering. Marked **UNKNOWN** in the map below until verified on hardware.

### 2.4 Minor — pin 1 is taken, so the I²C sensors use pin 17

`docs/sound-trigger-wiring.md:56` puts SEN-14262 VCC on 3.3V physical pin 1. The header's other 3.3V pin is 17, which is free. Two I²C sensors at a combined ~200 µA could trivially share pin 1, but using pin 17 keeps the sound-trigger wiring untouched — worth it, since that circuit is the one thing in the build that is known-good and finicky.

### 2.5 Pre-existing repo bugs found while mapping this

Not caused by the new work, but they will bite during bring-up:

1. **`docs/sound-trigger-wiring.md` is stale.** It documents GATE as a two-node connection (detector → OPS `HOST_INT`). `docs/iwr6843/README.md:151-167` requires a **three-node splice** adding Pi BCM17. `README.md:59` still points new builders at the stale doc.
2. **`scripts/hardware-test/test_sound_trigger_software.py:160`** hardcodes `lgpio.gpiochip_open(0)` while `gpio_factory.py:42` uses **gpiochip4** on a Pi 5. That test will claim the wrong chip and never see an edge.
3. **`trigger.py:588`** docstring says `debounce_ms` default is 200; the signature at `:576` says 20.

---

## 3. The 40-pin header map

![OpenFlight full system wiring: the Raspberry Pi 5 40-pin header with the SEN-14262 sound detector and the BME280 plus LIS3DH I2C sensor chain above it, the OPS243-A radar and the 7-inch display below it, and a panel listing the IWR6843, HDMI, touch, camera and power connections that do not use the header.](assets/system-wiring.svg)

*Regenerate with `python3 docs/assets/system-wiring-gen.py`. Drawn in the same style as the diagrams added in [PR #166](https://github.com/jewbetcha/openflight/pull/166).*

`C` = current build · `NEW` = the two sensor branches · `RSV` = reserved for camera subsystem · `—` = free

| Phys | BCM / Function | Status | Assignment |
|---|---|---|---|
| 1 | 3.3V | **C** | SEN-14262 VCC |
| 2 | 5V | **C** | **Display 5V** (single wire — see §2.1) |
| 3 | GPIO2 / SDA1 | **NEW** | **I²C SDA — BME280 + accelerometer** |
| 4 | 5V | **C** | **OPS243-A J3 pin 9 (5V)** |
| 5 | GPIO3 / SCL1 | **NEW** | **I²C SCL — BME280 + accelerometer** |
| 6 | GND | **C** | **Shared trigger ground: SEN-14262 + OPS243 J3 pin 10.** Keep these two on one pin — `ops243-uart-migration.md:75-77` calls this ground "load-bearing twice over" (UART return path *and* the trigger's voltage reference). Do not add the display's return here. |
| 7 | GPIO4 | **RSV** | Pico UART candidate (verify overlay mapping) |
| 8 | GPIO14 / TXD0 | **C** | Pi TX → OPS243 J3 pin 6 (`RxD`) |
| 9 | GND | **NEW** | I²C sensor ground |
| 10 | GPIO15 / RXD0 | **C** | Pi RX ← OPS243 J3 pin 7 (`TxD`) |
| 11 | GPIO17 | **C** | Sound-trigger GATE in (pull-down, rising edge) |
| 12 | GPIO18 | — | |
| 13 | GPIO27 | — | |
| 14 | GND | **C** | **Display ground** — kept off pin 6 so the panel's ~550 mA return doesn't share a pin with the trigger reference |
| 15 | GPIO22 | — | |
| 16 | GPIO23 | — | |
| 17 | 3.3V | **NEW** | **I²C sensor VCC (BME280 + accelerometer)** |
| 18 | GPIO24 | — | |
| 19 | GPIO10 / MOSI | — | |
| 20 | GND | — | |
| 21 | GPIO9 / MISO | **RSV** | Pico UART candidate |
| 22 | GPIO25 | — | |
| 23 | GPIO11 / SCLK | — | |
| 24 | GPIO8 / CE0 | **RSV** | Pico UART candidate |
| 25 | GND | — | |
| 26 | GPIO7 / CE1 | — | |
| 27 | ID_SD | **DO NOT USE** | HAT EEPROM I²C-0 — reserved by the Pi |
| 28 | ID_SC | **DO NOT USE** | HAT EEPROM I²C-0 — reserved by the Pi |
| 29 | GPIO5 | **RSV** | Pico UART candidate |
| 30 | GND | — | |
| 31 | GPIO6 | **RSV** | Camera strobe/status return (optional) |
| 32 | GPIO12 | — | |
| 33 | GPIO13 | — | |
| 34 | GND | — | |
| 35 | GPIO19 | — | |
| 36 | GPIO16 | — | |
| 37 | GPIO26 | — | |
| 38 | GPIO20 | — | |
| 39 | GND | — | |
| 40 | GPIO21 | — | |

**Result: the full build — current hardware, both new sensors, and the reserved camera pins — uses 19 of 40 pins (17 assigned + 2 reserved by the Pi), leaving 21 free.** Pin pressure is not the constraint. Power and USB ports are.

### 3.1 Off-header interfaces

| Interface | Device | Note |
|---|---|---|
| CSI-2 (MIPI CAM/DISP ×2) | IMX296 global-shutter camera (future) | Does not touch the 40-pin header. Pi 5 has two 4-lane connectors, so a second camera is possible without contention. |
| micro-HDMI ×2 | Display | One used — **HDMI0**, the port nearest USB-C, for the primary display. The Pi 5 uses micro-HDMI, so check what cable shipped with your panel; the ROADOM 7" includes the correct one. |
| USB-A ×4 | See §5.3 | The actual bottleneck |

### 3.3 Connector types — what physically mates

The Pi's 40-pin header is **male pins on 0.1" pitch**, so everything landing on it must terminate in **female** sockets.

| Connection | Cable | Note |
|---|---|---|
| SEN-14262 → header | Female-to-female Dupont ×3 | Detector ships with a male 0.1" header |
| OPS243 J3 → header | Female-to-female Dupont ×4 | J3 is a 10-pin header on the radar |
| I²C chain → header | **[Adafruit 4397](https://www.adafruit.com/product/4397)** — QT to **female sockets**, $0.95 | **Not 4209**, which is the male-header version and will not mate with the Pi |
| BME280 ↔ LIS3DH | [Adafruit 4210](https://www.adafruit.com/product/4210) 100 mm, or [4401](https://www.adafruit.com/product/4401) 200 mm | Pick for the reach to the radar mount plate |
| Display power → header | Its own 3-pin GPIO cable | Use **one** 5V wire only (§2.1) |

4397 wire colours: **red → pin 17 (3.3V), black → pin 25 (GND), blue → pin 3 (SDA), yellow → pin 5 (SCL)**.

### 3.2 I²C address map

| Device | Address | Alternate | Conflict? |
|---|---|---|---|
| BME280 | 0x76 **or** 0x77 — board-dependent | — | No |
| LIS3DH | 0x18 | 0x19 | No |
| ADXL345 | 0x53 | 0x1D | No |

**BME280 address is not standardised across breakouts.** Adafruit's board (ID 2652) defaults to **0x77**; most generic breakouts default to 0x76. On the Adafruit board, soldering the ADDR jumper closed (or tying SDO to GND) moves it to 0x76. Either works — the driver must accept both and `i2cdetect` output must be read against the actual board, not an assumption.

Both sensors share one bus with no address collision. **One `smbus2` dependency and one bus serves both feature branches** — which is the argument for a shared `src/openflight/i2c.py` helper rather than two independent `_load_smbus()` seams.

---

## 4. Which display cables you actually need

The ROADOM LE070-01 ships with an HDMI cable, two micro-USB cables, an FPC ribbon, a 3-pin GPIO power cable, a wall adapter, a stylus, stands and screws. The panel has **two micro-USB ports — the upper one is Power, the lower one is Touch** — plus an HDMI input.

| Cable | Needed? | Why |
|---|---|---|
| HDMI (Pi micro-HDMI → display HDMI) | **Yes** | Video. The only video path on this panel. |
| micro-USB → USB-A, into the **Touch** port | **Yes** | Touch HID. Costs one Pi USB port. |
| 3-pin GPIO power cable | **Yes — but use only one 5V wire** | Panel power from header pin 2, ground to pin 14. Leaves pin 4 for the OPS243-A and keeps the trigger ground on pin 6 uncluttered. |
| micro-USB → USB-A, into the **Power** port | **No** | Redundant with the GPIO cable, and it would move 550 mA into the USB budget. Keep as a spare. |
| Wall adapter | **No** (bench option) | Only if you choose resolution 3 in §2.1. |
| FPC ribbon | **No** | For the panel's internal/DSI variant. Not used in HDMI operation. |

---

## 5. Power budget

The repo asserts "the Pi cannot power both radars over USB" in four places and never once quantifies it. Here is the number.

### 5.1 System draw at 5V

| Load | Typical | Peak | Basis |
|---|---|---|---|
| Pi 5 (4GB), active | 7.0 W | ~12 W | **MEASURED** — 2.7-3.6 W idle, 7 W under stress |
| Display panel + backlight | 2.75 W | 3.0 W | **READ** — 550 mA @ 5V class figure |
| OPS243-A | 1.5 W | 1.6 W | **READ** — datasheet p1, p7 |
| IWR6843LEVM | **2.5 W assumed** | **2.5 W assumed** | **UNKNOWN** — see §5.2 |
| Touch HID | 0.25 W | 0.25 W | **ESTIMATED** |
| SEN-14262 | 0.02 W | 0.02 W | **ESTIMATED** |
| BME280 + accelerometer | <0.01 W | <0.01 W | **READ** |
| **Total** | **~14.0 W** | **~19.4 W** | |

Both columns carry the same 2.5 W IWR6843 placeholder, so they are comparable. Against the official 27 W (5V/5A) supply: **~48% headroom typical, ~28% at peak.** Comfortable — but note the peak figure moves directly with the one number nobody has measured. If the IWR6843 turns out to draw 5 W rather than 2.5 W, peak headroom falls to ~19%.

Adding the deprecated K-LD7 pair back would add ~2.5 W (2× 200 mA modules + 2 FTDI bridges), taking typical to ~16.5 W — still fine on the wall supply, but see §5.3 for why the USB side is a different story.

### 5.2 The one number that must be measured

**The IWR6843LEVM's actual current draw is UNKNOWN.** TI's own supply brick for the EVM family is rated 5V/2.5A, but that is the brick's rating, not the board's draw, and TI's forums state that detailed power characterisation for this part is unpublished. The 2.5 W above is a placeholder — a USB 2.0 high-power device class assumption, nothing more.

This matters because it is the single largest consumer of the USB current budget, and the budget is tight (§5.3).

**Measurement, before any battery decision:** put an inline USB-C power meter between the PSU and the Pi, then an inline USB-A meter on the IWR6843's port. Record idle, streaming, and during an L3 dump transfer at 1,041,667 baud. Ten minutes of work; it converts the entire power section of this document from estimate to fact.

### 5.3 USB current budget — the actual bottleneck

The Pi 5 limits **total** current across all four USB ports:

- **600 mA** by default
- **1.6 A** only when it has negotiated 5V/5A over USB-PD, or `usb_max_current_enable=1` is set in `config.txt`

| Configuration | USB draw | 600 mA budget | 1.6 A budget |
|---|---|---|---|
| Current build (IWR6843 + touch) | ~550 mA | **92% — no margin** | 34% — fine |
| + display powered over USB | ~1,100 mA | **EXCEEDS** | 69% |
| + K-LD7 ×2 back on USB | ~1,050 mA | **EXCEEDS** | 66% |
| Everything at once | ~1,600 mA | **EXCEEDS** | **100% — at the limit** |

**Three conclusions:**

1. **The 5V/5A PSU is not optional, it is load-bearing.** On a 5V/3A supply the current build sits at 92% of the USB budget with the IWR6843's draw still unmeasured. That is the mechanism behind the repo's undervoltage symptoms — `docs/iwr6843/README.md:758`, "Either radar disconnects when both run | Insufficient USB power".
2. **Powering the display over USB is the single worst decision available.** It alone exceeds the default budget.
3. **The repo's "can't power both radars over USB" claim is correct, and now has a number.** OPS243 on USB + IWR6843 + touch would be ~850 mA — over the 600 mA cap, and uncomfortably close to 1.6 A once the K-LD7s are added.

### 5.4 What breaks on battery

You chose wall-now, battery-later. Here is what "later" runs into, so the bank gets bought once:

A typical USB-C power bank advertises 5V/3A (15 W) on its 5V profile, and higher wattage only at 9V/12V/20V. Consequences:

- The Pi negotiates 3 A, not 5 A → **USB budget drops to 600 mA** → the current build lands at 92% of budget with no margin.
- 15 W supply against ~14 W typical draw → **~7% headroom**, and the Pi alone peaks at 12 W. Brownouts under transient load are near-certain.

**A 5V/3A power bank cannot run this system.** Viable paths, in order:

1. **PD bank with a genuine 5V/5A profile.** Rare and must be verified from the spec sheet — many banks claim 25 W+ but only at 9 V or above. This is the only single-source option.
2. **PD bank at 9V/12V + a 5V/5A buck converter.** Adds a part but decouples the bank's profile from the Pi's requirement. Most robust.
3. **Split loads** — bank port A → Pi, port B → display and OPS243 via a separate 5V feed. Removes ~4.3 W from the Pi's rail, but does *not* raise the USB budget, so the IWR6843 constraint is untouched.

Note that split loads solve the *total wattage* problem and not the *USB current* problem. Those are separate limits and only paths 1 and 2 address both.

---

## 6. Required system configuration

None of this is currently in `docs/raspberry-pi-setup.md` or `scripts/setup/setup.sh`.

### `/boot/firmware/config.txt`

```
# UART0 on GPIO14/15 for the OPS243-A (set indirectly by raspi-config today).
enable_uart=1

# I2C-1 on GPIO2/GPIO3 for the BME280 and the tilt accelerometer.  NEW.
dtparam=i2c_arm=on

# Raise the total USB current budget from 600 mA to 1.6 A.
# Only safe with a 5V/5A supply.  The Pi sets this automatically when it
# negotiates 5A over PD, so it is belt-and-braces — but see docs/hardware-integration.md 5.3.
usb_max_current_enable=1

# Secondary UART for the camera-trigger Pico.  FUTURE — overlay name and GPIO
# mapping must be confirmed on-device with `dtoverlay -h uart2-pi5`.
# dtoverlay=uart2-pi5
```

### `raspi-config`

- Interface Options → Serial Port → login shell over serial: **No**, serial hardware: **Yes** (already documented)
- Interface Options → I2C → **Enable** (new)

### Groups

```
sudo usermod -aG dialout,gpio,i2c "$USER"
```

`i2c` is new; `dialout` and `gpio` are already documented in `docs/iwr6843/README.md:235`.

### Verification

```
ls -l /dev/i2c-*                 # expect /dev/i2c-1
i2cdetect -y 1                   # expect 0x76 or 0x77 (BME280) and 0x18 (LIS3DH)
ls -l /dev/ttyAMA0               # OPS243 UART — NOT /dev/serial0 on a Pi 5
vcgencmd get_throttled           # expect 0x0; any bit set means power trouble
```

`vcgencmd get_throttled` appears nowhere in the repo and should be added to `scripts/hardware-test/diagnose.py`. It is the fastest check for exactly the class of failure this document is about.

---

## 7. Bring-up order

Do not wire everything and then power on. Each step below leaves a working system you can diagnose.

1. **Baseline.** Pi + display (GPIO 5V, pin 2 only) + touch USB. Confirm `vcgencmd get_throttled` is `0x0`. Measure total draw at the PSU.
2. **OPS243-A on pin 4.** Confirm `/dev/ttyAMA0`, 230,400 baud negotiation, speed readings. Re-check throttling.
3. **Sound trigger.** Three-way GATE splice (detector → OPS J3 pin 3 → Pi pin 11). Verify with `scripts/hardware-test/test_sound_trigger_gpio.py` — **not** `test_sound_trigger_software.py`, which has the gpiochip bug in §2.5.
4. **IWR6843 on USB. Measure its current here.** This is the §5.2 measurement. Confirm both radars stay enumerated for a full session.
5. **I²C sensors.** Enable the bus, `i2cdetect`, confirm both addresses. Power impact is unmeasurable — if anything changes at this step, something is wired wrong.
6. **Camera + Pico.** Future. Confirm the secondary-UART mapping on-device first.

Steps 1-4 are the existing system and should be re-validated with measurements even though they already work — the point is to have numbers before the battery decision, not to find bugs.

---

## 8. Open items

1. **IWR6843LEVM current draw — UNKNOWN.** Blocks the battery decision. §5.2 says how to get it.
2. **Pi 5 secondary-UART GPIO mapping — UNKNOWN.** Published sources disagree. Verify on-device before the camera work.
3. Display draw is a **class figure**, not this panel's measurement. The ROADOM has dual speakers, so it may run higher. Falls out of step 1 above.
4. `docs/sound-trigger-wiring.md` needs updating to the three-node GATE splice, and `README.md:59` should stop pointing new builders at it.
5. `test_sound_trigger_software.py:160` gpiochip bug.
6. Decide whether the shared I²C helper (`src/openflight/i2c.py`) lands with the weather branch, the leveling branch, or separately first. It is the same dependency and the same bus for both.
