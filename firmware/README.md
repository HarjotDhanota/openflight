# IWR6843 Firmware

The firmware developer guide now lives in the documentation site:

**→ [docs/development/firmware.md](../docs/development/firmware.md)**
(published at <https://openflight.dev/docs/development/firmware/>)

It covers building the configurable image from source, the Docker toolchain,
the L3 raw-dump format, and the flashing procedure.

You do not need to build from source to use OpenFlight — a validated prebuilt
image ships in [`releases/`](releases/), and the
[IWR6843 Operator Guide](../docs/iwr6843/index.md) walks through flashing it.

## What's in this directory

| Path | Contents |
| --- | --- |
| `iwr6843/` | Firmware sources |
| `releases/` | Prebuilt, validated firmware images |
| `flash_iwr6843.py` | Flashing tool |
| `Dockerfile`, `Makefile` | Build toolchain |

## Experimental build flags

All default off; an image built without them is byte-identical to the
production build. Pass them to `build-native`:

| Make variable | Defines | Adds |
| --- | --- | --- |
| `CLOCK_PROBE_DEFS` | `L3_USB_CLOCK_PROBE` | `clockProbe` (text USB clock probe) |
| `CLOCK_PROBE_DEFS` | `L3_USB_CLOCK_PROBE L3_USB_CLOCK_COMPACT` | `clockProbe`, `cp` (44-byte `OFC2` packet) |

`L3_USB_CLOCK_COMPACT` without `L3_USB_CLOCK_PROBE` is refused at compile
time (`iwr6843/l3_feature_checks.h`). Commands are registered contiguously by
`iwr6843/l3_cli_table.h`, so a disabled flag never hides a later command.

`clockProbe` reports a build id from `iwr6843/clock_probe_build_id.h`: one
SHA-256 per flag set over every firmware source. After changing any file in
`iwr6843/` or the `Makefile`, regenerate it with
`python firmware/build_fingerprint.py` (`--check` verifies it).
