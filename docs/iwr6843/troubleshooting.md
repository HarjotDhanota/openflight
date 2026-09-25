---
icon: lucide/wrench
---

# IWR6843 Troubleshooting

Start with the symptom shown in the terminal. Avoid changing estimator settings
until power, ports, firmware, config, and geometry are verified.

| Symptom | Likely cause | Action |
|---|---|---|
| `no IWR6843 CLI found` | Wrong USB interface, board still in flash mode, missing functional RESET, stale serial owner, or unstable power | Read the `Probes:` list in the message: `no reply to help` means the port opened but the firmware stayed silent (reset/flash mode/power); `bytes without the CLI help` means something else is streaming (often an abandoned dump — wait, then retry); `could not open` names the OS error (permissions, group membership of the running service, a vanished device). Auto-detection never opens the CP2105 Standard interface (`if01`). Set functional switches, press RESET, verify Enhanced/UARTA interface `00`, stop serial processes, then give the tester its stable `/dev/serial/by-id/...-if00-port0` path with `--iwr-static-port` |
| Static capture reports `stage=post_dump_cli_health` | Raw bytes arrived but the firmware never returned a healthy CLI after `l3dump` | Keep the preserved raw file, press RESET, rerun the tester hardware check on Enhanced/UARTA `if00`, then start a new capture; do not repeatedly send Retry while the CLI check fails |
| `GPIO busy` | Another kiosk, calibration, or shot-test process owns BCM17 | Stop the old process; use `pgrep -af` and `sudo fuser -v /dev/gpiochip*` to locate it |
| `captureFormat` or `phaseCaptureCfg` rejected | Older firmware is flashed | Flash the configurable release, reset in functional mode, and retry either supported profile |
| Bootloader probe returns no response | Wrong CP2105 port or RESET occurred before the script opened UART | Use Enhanced/UARTA, rerun the probe, type `READY`, then RESET only when prompted |
| Flash fails after `Erasing existing SFLASH` | Transfer was interrupted after the old image was erased | Leave the board in flash mode and rerun the complete flash; the ROM bootloader is still available |
| Server starts only after unplugging TI | Board was not reset cleanly, a prior dump was still streaming, or USB/power wedged | Stop the old process, press RESET in functional mode, wait for the port, then reconnect USB only if needed |
| `short IWR6843 dump` | Interrupted UART transfer, process shutdown during dump, or wrong firmware format | Let the active dump finish, restart, and confirm 732,812 bytes for wide or 549,764 bytes for dense |
| Dense `stats` accumulates `hwa_missed` or `iq8_overrun` | The 2 ms processing budget is not being sustained | Stop using the capture for measurements, reset, and return to the wide profile while investigating |
| Clap produces `rejected_by_ball_tracker` | A clap has no moving ball range track | Expected for trigger testing; confirm the dump completed, then hit a ball |
| `rejected_track_quality` | A ball-like track was found but it was too thin, noisy, inconsistent, or net-contaminated | Verify geometry and aim; inspect the debug dump before relaxing acceptance gates |
| `rejected_missing_tdm_sign` | The ball track was usable, but the TX timing evidence did not resolve a trustworthy correction sign | Keep the estimated UI angle, inspect the debug dump, and verify signal quality before changing gates |
| All UI angles are estimated | TI captures are absent, unmatched to OPS, or rejected by LCMF | Run with `--debug`, inspect `iwr6843_capture`, and check the reported rejection reason |
| OPS reports no data | Wrong OPS port, missing power, WiFi OPS connected through unsupported receive-only J3 UART, or non-WiFi UART wired incorrectly | For a WiFi OPS use the externally powered USB hub; otherwise verify `/dev/ttyAMA0`, power, shared ground, and crossed TX/RX |
| Either radar disconnects when both run | Insufficient USB power or unstable cabling | Use OPS GPIO power or a hub with its own external supply; verify the hub supply is connected and sized for both radars |
| Angles are consistently shifted | Tilt, antenna orientation, radar height, ball height, or tee distance is wrong | Re-measure all geometry from the antenna center and common floor reference |
| Dump file is missing from the session | OpenFlight was not launched with `--debug` | Re-run in debug mode when raw capture retention is required |

If the firmware itself must be rebuilt rather than flashed from the checked-in
binary, continue with the [firmware developer guide](../development/firmware.md).
