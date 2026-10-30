# Flashing the OPS243-A with a Debug Probe

Update an OPS243-A to **1.3.3** using a Raspberry Pi Debug Probe, a
10-pin breakout board and OpenOCD. This community procedure was tested on
one **XMC4500 revision AC, 1 MiB** radar using a Pi 5 running Raspberry Pi OS
Trixie and OpenOCD 0.12.0+dev-snapshot (2026-02-16-16:07).

Send `?P` through the radar's existing serial connection to check the processor
before buying parts. This firmware does not support XMC4700 or OPS243-C.
Some D2 and later boards require JTAG; the Pi Debug Probe supports SWD only.
For those boards, use [OmniPreSense's programming procedure](https://omnipresense.com/wp-content/uploads/2025/02/AN-013-F_Code-Update.pdf).

## Parts and soldering

Have these already or buy them before starting:

| Item | What to check |
| --- | --- |
| Official Raspberry Pi Debug Probe | USB data cable and the supplied three-wire debug cable |
| Arm Cortex JTAG/SWD breakout board | 10-pin, 2 × 5, 1.27 mm target connector; a Treedix breakout was used |
| Matching ribbon cable | Female-to-female, 10-pin, 1.27 mm, fitting both the breakout and radar J2 |
| DuPont connectors / header pins | Fit the breakout's solder pads and the probe cable ends; check what your breakout includes |
| Soldering iron and solder | Required to attach the DuPont connectors to the breakout board |
| Raspberry Pi and OPS243-A | Raspberry Pi OS, internet access, terminal/SSH access and stable power |

**Soldering is required for the breakout used here:** solder the DuPont
connectors onto the breakout board so the probe's three leads can plug in.
Do this with everything disconnected and unpowered. Check the joints for
bridges and verify continuity from each connector to the intended ribbon pin.

## 1. Prepare the Pi and probe

From your OpenFlight checkout, copy the supplied scripts into a fresh working
folder. Use a separate folder for each radar; its backup belongs to that sensor.

```bash
cd ~/openflight
cp -R firmware/ops243-update ~/ops243-update
cd ~/ops243-update
sudo apt update
sudo apt install -y openocd python3
```

Inspect the scripts before running them. Firmware is downloaded from a pinned
vendor commit during preparation; no firmware or sensor backups ship here.

If OpenOCD warns about old Debug Probe firmware, disconnect target power and
the debug cable. Download **`debugprobe.uf2`** from the
[official releases](https://github.com/raspberrypi/debugprobe/releases/latest).
Remove the probe cover, hold BOOTSEL while connecting USB, and copy the UF2
onto the RPI-RP2 drive. The probe reboots automatically. See
[Raspberry Pi's update instructions](https://www.raspberrypi.com/documentation/microcontrollers/debug-probe.html#updating-the-firmware-on-the-debug-probe).

## 2. Wire the breakout

Remove Pi and radar power before connecting anything. Use the probe's **D**
(debug) port and wire its cable to the breakout:

| Probe lead | Breakout signal | Cortex ribbon pin |
| --- | --- | --- |
| Yellow / SD | SWIO / SWDIO / TMS | 2 |
| Orange / SC | CLK / SWCLK / TCK | 4 |
| Black / GND | Ordinary GND | 3 or 5 |

Follow signal names and continuity if colors or labels differ. Leave **SWO**,
GNDd, VCC/VTref, RST, TDI and TDO disconnected on the probe side. The probe's
**U** port is UART and is unused here.

Connect the ribbon from the breakout to the radar's **J2 programming connector**,
matching pin 1 and the key. J3 is the power/UART header. Connect the probe's USB
to the Pi running OpenOCD; a desktop can SSH into that Pi.

The tested radar stayed [powered through J3](ops243-uart.md#2-wire-it): Pi
physical pin 4 → J3 pin 9 (5 V), Pi physical pin 14 → J3 pin 10 (GND), with
radar micro-USB unplugged. Keep the probe's black ground connected too. The
probe has 3.3 V I/O and does **not** power the radar; do not feed 5 V into its
debug signals or combine radar power sources. Restore power after wiring.

## 3. Back up the radar

Stop OpenFlight, or close any other application controlling the radar. Skip
the service command if OpenFlight is not installed as a service.

```bash
sudo systemctl stop openflight
cd ~/ops243-update
bash 01_backup.sh
```

Require **XMC4500 Rev AC**, flash size **0x00100000**, all 12 sectors
unprotected, **1048576 bytes** backed up, and **`contents match`**. The script
refuses to overwrite an existing backup. Copy `ops243-before-update.bin` and
`backup-sha256.txt` to another device before proceeding.

## 4. Prepare, then flash

Prepare the image first; this command does not access hardware:

```bash
python3 prepare_update.py
```

Require `Preflight passed: backup, firmware, and merged images match.` It checks
the pinned firmware hash and HEX records, retaining the backup's bytes outside
the supplied firmware. Do not change persistent radar settings between backup
and flashing.

The next command writes flash. Keep power and wiring stable until it finishes:

```bash
bash 03_flash.sh
```

The script checks chip/flash IDs and protection settings, compares the live
flash against the backup, then writes the application and first 64 KiB blocks.
Require this sequence:

```text
contents match
wrote 393216 bytes from file ops243-app.bin
wrote 65536 bytes from file ops243-first64k.bin
contents match
```

The **last** match verifies the complete 1 MiB against the expected image,
including retained bytes. The tested writes took about 3½ minutes. The CPU
is left halted for the power cycle below.

## 5. Power cycle and verify

```bash
sudo poweroff
```

Wait for shutdown, **physically unplug Pi power**, disconnect the probe's D
cable, then restore power. A reboot does not remove GPIO 5 V. Remove any
separate radar power source too before disconnecting wires.

For the tested Pi 5 GPIO UART layout:

```bash
sudo systemctl stop openflight
cd ~/openflight
uv run python ~/ops243-update/check_version_openflight.py
```

Require **`OPS243 firmware: 1.3.3`**. The helper uses `/dev/ttyAMA0`. For another
serial layout, send `?V` through its actual port and configured baud rate.
Keep radar micro-USB unplugged when using the GPIO UART. Restart OpenFlight
with `sudo systemctl start openflight` and check normal readings. Retain the
original backup permanently.

## If a step fails

| Failure | Action |
| --- | --- |
| Wrong IDs, protected sectors, hash or backup mismatch | Stop and investigate; do not remove guards or replace the original backup. |
| Probe missing / halt timeout | Check USB data cable, probe firmware, wiring and 1000 kHz speed. The flash script uses `reset run`, waits one second, then halts; `reset init` and `reset halt` timed out in the tested setup. |
| Erase, write or final comparison fails | Keep power/wiring stable and preserve logs and the backup for diagnosis. Do not rerun after a partial write or use a mass erase. |
| UART busy / wrong version | Stop the application using the port, check the port and confirm a physical power cycle. |

## Firmware and validation

The scripts pin `OPS243_CW_v1.3.3_20261016_1230.hex` from vendor commit
[`efaceefb8fa547071cb925c0d20d2e38363ace67`](https://github.com/omnipresense/OPS243-A_Firmware/tree/efaceefb8fa547071cb925c0d20d2e38363ace67),
SHA-256 `169a6c6c7ae14bcc7da3e7201e31c623149da65e9f3df7598da70e0ae3e9d81c`.
They write complete physical-sector blocks, preserving other bytes from the
backup; see [XMC4500 sector limitations](https://kb.segger.com/Infineon_XMC4xxx)
and [OpenOCD flash commands](https://openocd.org/doc/html/Flash-Commands.html).
Retained settings may still be migrated by the new firmware after boot.

The supplied kit records one user-operated update on 29 October 2026: verified
1 MiB backup, both blocks written, complete flash comparison passed, and a
post-boot 1.3.3 response. The packaged scripts' additional guards were checked
offline; they have not had a separate end-to-end hardware run.
