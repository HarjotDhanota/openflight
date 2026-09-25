# Static setup range capture

This diagnostic records an IWR6843 range profile before motion-target
filtering. Capture the unchanged setup once with no ball and once after placing
the ball. The pair can then test whether one localized reflector appeared at a
stable range. It does not select a tee range or enable range-dependent fusion.

Stop the kiosk or any other OpenFlight process using the IWR6843 first. The
driver holds an interprocess device lock for the complete serial lifetime, so a
second process exits with an explicit busy error rather than stealing bytes
from a live dump.

Use the fixed-window setup profile. The normal shot profiles deliberately move
their saved range window across phases and cannot produce the one common grid
required for static subtraction.

```bash
mkdir -p ~/openflight_sessions/static-range

uv run python scripts/iwr6843/capture_static_range.py \
  --capture-id empty-001 \
  --kind empty \
  --output-dir ~/openflight_sessions/static-range \
  --config config/iwr6843_static_range_24f3ms_53bin_iq16.cfg \
  --firmware firmware/releases/l3_dump_configurable_capture_20260818.bin \
  --rig-geometry config/enclosure_v3_rig_geometry.json \
  --calibration config/iwr6843_calibration_reference.json
```

Place the ball without moving the enclosure, radar, mat, cables or nearby
reflectors, then repeat with a new identifier and `--kind ball_present`.
Auto-detection remains the default. For the guided tester, prefer the stable
CP2105 Enhanced/UARTA interface (interface `00`) when it is available:

```bash
ls -l /dev/serial/by-id/
bash scripts/start-tester.sh --host 0.0.0.0 \
  --iwr-static-port /dev/serial/by-id/<IWR-CP2105-name>-if00-port0
```

Use the actual path printed by `ls`; the placeholder is not a literal device
name. Do not assume a fixed `/dev/ttyUSB` number. If no `if00` path appears,
check board power, the data-capable USB cable and enumeration before retrying.
The same explicit port is used by **Check the hardware** and the guided static
captures. The command waits one second after configuration, more than thirteen
complete 72 ms rings, before requesting a fresh dump.

Each attempt writes `<capture-id>.l3dump` atomically before parsing it, then
writes `<capture-id>.json`. A short or invalid dump remains on disk and its JSON
record is marked unusable. The record hashes the exact configuration bytes sent
to the radar and the exact declared firmware image, rig geometry and calibration
files. The firmware hash identifies the supplied image file; the current
firmware cannot read the flashed image back from the board, so this is declared
provenance rather than proof of what is installed.

A complete binary payload alone is not a usable capture. The host waits through
the remaining capture timeout for the firmware's trailing `Done`, then requires
an acknowledged `stats` response showing capture active before deriving the
profile. Cleanup uses longer CLI windows than the CP2105's documented stalls and
verifies `active=0` after `sensorStop`. If the dump is complete or partial but
the CLI does not recover, the raw bytes are still saved, the result records
`stage=post_dump_cli_health`, and the host closes the port without sending more
commands into a possibly active dump handler. Press RESET, rerun **Check the
hardware**, and retry only after the CLI probe passes.

Every capture remains explicitly unqualified. Successful transport does not
qualify the profile; a later hardware-validation step must regenerate the
profile from the saved raw bytes and exact hashes before it can contribute even
a noncanonical tee-range candidate.
