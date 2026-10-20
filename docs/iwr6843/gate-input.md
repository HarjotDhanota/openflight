# Experimental sound-GATE input to the IWR

19 October 2026. This is an opt-in firmware and evidence path. It has not been
built with the TI toolchain, flashed, wired, or tested on the physical unit here.
Host C tests establish software behavior, not electrical timing accuracy.

The existing SEN-14262 edge continues to trigger the OPS243 and Pi. An additional
connection to the IWR lets its GPIO callback latch a cycle-counter tick and
request retention immediately. The radar remains free-running before that edge;
this does not configure hardware-SYNC frame triggering or radar self-detection.

## Candidate pin and prerequisites

The inspected TI IWR6843LEVM schematic package, PROC116A / `001_IWR`, routes
`RADAR_SYNC_IN` through R36 (0 ohms) to J1 pin 22. The chip pad P4 multiplexes
as GPIO28. The SDK symbols are `SOC_XWR68XX_PINP4_PADBB`,
`SOC_XWR68XX_PINP4_PADBB_GPIO_28` and `SOC_XWR68XX_GPIO_28`.

Sources: [TI schematic package](https://www.ti.com/lit/zip/swrr178),
[device pin multiplexing and electrical limits](https://www.ti.com/lit/ds/symlink/iwr6843.pdf).
The TI-authored GPIO and pinmux headers were inspected in an SDK source snapshot;
the installed 3.6.2 SDK build is still a required check.

Before connecting anything, confirm the actual board revision, connector
orientation, R36 population, pad access, I/O supply/voltage compatibility, shared
ground, and loading of the existing GATE fanout. Verify the waveform with the
unit's actual supply. The schematic establishes a candidate route; it does not
establish the condition of the physical board. No additional controller is used.

## Build and configuration

With the TI toolchain already installed, from the repository root:

```sh
make -C firmware build-native GATE_INPUT=1 RELEASE_NAME=l3_dump_gate_20261019.bin
# Or, with the prebuilt SDK container:
make -C firmware docker-build GATE_INPUT=1 RELEASE_NAME=l3_dump_gate_20261019.bin
```

The new GPIO library and `L3_GATE_INPUT` define are included only with
`GATE_INPUT=1`. The configurable HWA/hybrid-evidence build is required. Ordinary
builds keep their existing libraries, CLI and capture formats. Follow the existing
build/flash procedure and retain the known working rollback image; these commands
do not flash the sensor.

In a separate copy of the chosen capture config, add `gateCfg 1` after
`sensorStop` and before `sensorStart`. Keep its geometry and capture plan unchanged.
Use the existing config-path option to select this copy. Production configs are
not changed by this feature.

Even in a GPIO-capable build, the mode defaults to serial freezing at boot. The
mode persists across sensor stop/start. To return to serial operation without
rebooting, explicitly send `sensorStop`, then `gateCfg 0`, before applying the
ordinary config. Simply removing `gateCfg 1` from a config does not reset a mode
already enabled in the current boot. Builds without GPIO support reject `gateCfg`.

## Retention and readback

1. Successful RF startup clears stale freeze completions and pending GPIO status,
   increments the local arm sequence, and enables the rising-edge input.
2. The first eligible callback records the PMU tick, frame-start count and local
   edge sequence. It requests the existing configured post-frame plan. No serial
   I/O or blocking semaphore wait occurs in that callback.
3. Further edges increment an ignored-edge count without overwriting the latch.
   The existing frame-boundary logic completes the capture. IQ8 completion is
   acknowledged only after the final CPU/EDMA pack has completed.
4. The Pi still sends `l3dump` through the existing owner and transport. A completed
   hardware freeze remains readable even though `gCaptureActive` is already zero.
   An unfinished freeze is awaited, without draining its completion or requesting
   another post-frame window.
5. The CLI stops RF, streams the retained capture, then restarts and arms a fresh
   capture. `sensorStop` also releases a hardware-frozen capture without another
   post-frame wait. An RF-stop failure preserves state for a stop retry.

In GATE mode, a missing edge is an error; there is no silent serial-trigger
fallback. An edge older than 250 ms when `l3dump` is handled is also refused. This
is a retention-admission policy, not measured latency or a fusion accuracy bound.
It uses SYS/BIOS ticks rather than the short-wrapping PMU counter, so an old PMU
value cannot appear fresh after one PMU wrap. The RTOS clock is assumed not to
have completed an entire 32-bit wrap between edge and request.

After a missing-edge, stale-edge or freeze-timeout error, stop/start and confirm
readiness before the next attempt. A stale frozen capture remains retained until
stopped; it is not reassigned to a later shot. The HWA stops rearming at the
boundary, but RF is stopped by the CLI, so unattended frozen operation is not an
automatic power-saving mode.

## Evidence contract

Serial mode continues to emit versions 8/9. GATE mode emits fork versions 12
(temperature present) or 13 (absent). Versions 10/11 remain unsupported because
the reviewed Cormac format diverges from this fork's observed-evidence contract.
An older host rejects the new versions instead of misinterpreting the bytes.

After the existing 40-byte `ILE1` capture block, and before the frame records,
GATE versions insert a packed 32-byte record: magic `ILG1` followed by seven
little-endian uint32 fields:

| Field | Meaning |
| --- | --- |
| `arm_seq` | Local arm count; advances on sensor start and successful dump/rearm |
| `edge_seq` | GPIO callback count of the latched edge; ignored callbacks also advance the counter |
| `edge_tick` | PMU reading at GPIO callback entry, before the freeze request |
| `frame_seq` | Frame-start count read while latching that edge |
| `freeze_tick` | PMU reading when the final safe completion is acknowledged |
| `ignored_edges` | Saturating count of rejected callbacks in this arm, before RF stop |
| `flags` | Bit 0: edge latched; bit 1: safe completion observed; both required |

Zero is a valid wrapped tick or sequence value. Local counters restart on device
reboot; they are not globally unique boot identities. Keep the sensor's reset and
host-session provenance when comparing captures. The existing `request_tick`
still means CLI entry, and the existing capture `freeze_tick` still means CLI
resumption; neither is relabelled as an electrical edge or hardware completion.

The host preserves `hardware_gate_evidence` alongside the original Pi
`gate_event` in the capture and session JSONL. The binary dump retains the
per-frame timing and IQ evidence. No IWR-to-Pi map, automatic edge-ID equivalence,
contact time, GPIO latency bound, or calibrated fusion timestamp is inferred.
Both edge matching and the physical meaning of these timestamps need validation.

## Qualification on the unit

Keep the ordinary serial mode as the baseline. Record firmware/source identities,
board revision, Pi boot identity, GPIO backend, capture profile and exposure.
For each attempt retain the original Pi event, complete binary dump, errors and
trace. Test single pulses, ringing, early/late `l3dump`, deliberate missed Pi
edges, missed IWR edges, stop/start, dump cancellation and device resets. Confirm
that invalid attempts never supply another shot's evidence.

Compare baseline and GATE mode on retained pre/post-frame coverage, frame gaps,
request-to-freeze intervals and event loss. Measure electrical edge-to-ISR delay
under load separately. Run the existing LED timing calibration to qualify camera
exposure timing. Freeze thresholds before held-out trials; report bias, p95/p99,
largest observed error and refusals. A narrow software bracket is not proof of
millisecond physical synchronization. Faster full-dump transport and radar
self-triggering are separate work.

Workstation validation: 805 relevant capture, transport, logging and trigger tests
passed, including 27 new cases. The new firmware tests compile production C
functions with GCC in WSL and use driver/semaphore stand-ins; they cover IQ16,
CPU/EDMA IQ8 completion, delayed reads, stale reads, RF-stop retry and the legacy
fixed-ring request. A C-produced record is decoded by the Python layout.
There were 42 skips: 28 existing tests require a native Windows `cc`, seven need
native `make`, six need unavailable historical recordings and one needs Linux
`CLOCK_BOOTTIME`. Ruff passed and backend pylint scored 9.79/10. The SDK build,
physical pin checks, waveform measurements and real-ball qualification remain open.

## Attribution and changes

The event-latch and freeze/readback lifecycle is adapted on 19 October 2026 from
Cormac McGrath's `Cormac131/openflight`, `feat/iwr-calcs`, reviewed at
[`109c259f`](https://github.com/Cormac131/openflight/tree/109c259fbc3af4ed3a17b8f07b41efc0f431e56f).
Relevant originating commits are:

- [`6024a48b`](https://github.com/Cormac131/openflight/commit/6024a48b51df3e0f98deec06c01c1e118bd2e8a2):
  event latch, post-frame freeze request and queued trigger notice.
- [`27daa713`](https://github.com/Cormac131/openflight/commit/27daa7134b9370343ee40e2e0fe73e79453ad6ed):
  recovery from an already-frozen ring before stopping/reconfiguring RF.

This adaptation uses the existing sound GATE instead of radar detection, preserves
this fork's observed frame evidence and safe packing path, and adds independent
GPIO evidence and stale-read refusal. It does not import Cormac's estimator,
calibration constants, binaries, result transport or host trigger notifications.
The project and reviewed source use AGPLv3; existing notices remain intact. Code
and documentation were prepared with Codex assistance and require owner review
and physical qualification before deployment.
