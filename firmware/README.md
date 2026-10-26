# IWR6843 Firmware

The firmware developer guide now lives in the documentation site:

**→ [docs/development/firmware.md](../docs/development/firmware.md)**
(published at <https://openflight.dev/docs/development/firmware/>)

It covers building the configurable image from source, the Docker toolchain,
the L3 raw-dump format, and the flashing procedure.

For controlled UART timing experiments, add `--define=L3_UART_NO_ECHO=1` to
the variant flags. This disables the CLI port's received-character echo while
preserving newline command input and binary output. It is off by default and
does not qualify clock or acquisition timing. Compare separately stamped
echo-on/off RAM images before adopting it; no baud, RF or wire-format change
is required. Existing host readers accept replies with or without command echo.

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
