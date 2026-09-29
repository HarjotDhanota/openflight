"""Find which way of closing the IWR6843 port avoids the CP2105 purge timeout.

On the Pi every static setup capture ended with ``cp210x ttyUSB0: failed set
request 0x12 status: -110`` in dmesg and a close that took 5.05 s: the kernel's
purge-on-close control request times out (5 s USB control timeout). Idle
open/close cycles were clean, so it follows a real capture.

Each trial runs one real static capture (configure, settle, l3dump, health
check, sensorStop) and then closes the port one of these ways:

* ``plain``           close straight away, as the setup does today
* ``drain``           wait for 0.5 s of input silence and flush both buffers first
* ``no_hupcl``        clear HUPCL so close does not drop the modem lines
* ``drain_no_hupcl``  both

``idle`` trials (open, stats, close) are the control.

Stop the tester server first: it holds the radar. Replug the radar's USB cable
before running, since an earlier timeout can leave the bridge wedged. Then check
``dmesg -T | grep cp210x | tail`` against the printed times.

    uv run python scripts/iwr6843/close_timeout_probe.py --repeats 2
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from openflight.iwr6843.driver import IWR6843Radar  # noqa: E402

DEFAULT_CONFIG = REPO / "config" / "iwr6843_static_range_24f3ms_53bin_iq16.cfg"
VARIANTS = ("idle", "plain", "drain", "no_hupcl", "drain_no_hupcl")
TIMEOUT_SUSPECT_S = 4.0


def _drain(radar: IWR6843Radar, quiet_s: float = 0.5, limit_s: float = 5.0) -> int:
    drained = 0
    quiet_since = time.monotonic()
    deadline = quiet_since + limit_s
    while time.monotonic() < deadline:
        waiting = radar.ser.in_waiting
        if waiting:
            drained += len(radar.ser.read(waiting))
            quiet_since = time.monotonic()
        elif time.monotonic() - quiet_since >= quiet_s:
            break
        else:
            time.sleep(0.02)
    radar.ser.reset_input_buffer()
    radar.ser.reset_output_buffer()
    return drained


def _clear_hupcl(radar: IWR6843Radar) -> None:
    import termios  # Linux only

    fd = radar.ser.fileno()
    attrs = termios.tcgetattr(fd)
    attrs[2] &= ~termios.HUPCL
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def _trial(variant: str, port: str | None, config: Path) -> dict:
    record: dict = {"variant": variant, "started": datetime.now().strftime("%H:%M:%S")}
    radar = IWR6843Radar(port=port)
    try:
        if variant == "idle":
            record["stats"] = radar.stats().strip()[-80:]
        else:
            radar.send_config(str(config))
            time.sleep(1.0)
            record["dump_bytes"] = len(radar.read_dump())
            radar.verify_post_dump_cli()
            radar.stop_sensor()
        if "drain" in variant:
            record["drained_bytes"] = _drain(radar)
        if "no_hupcl" in variant:
            _clear_hupcl(radar)
    except Exception as error:  # pylint: disable=broad-exception-caught
        record["error"] = f"{type(error).__name__}: {error}"
    started = time.monotonic()
    try:
        radar.close()
    except Exception as error:  # pylint: disable=broad-exception-caught
        record["close_error"] = f"{type(error).__name__}: {error}"
    record["close_s"] = round(time.monotonic() - started, 3)
    record["closed_at"] = datetime.now().strftime("%H:%M:%S")
    record["purge_timeout_suspected"] = record["close_s"] >= TIMEOUT_SUSPECT_S
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", default=None, help="CP2105 if00 port (default: auto-detect)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    args = parser.parse_args(argv)
    results = []
    for _ in range(args.repeats):
        for variant in args.variants:
            try:
                record = _trial(variant, args.port, args.config)
            except Exception as error:  # pylint: disable=broad-exception-caught
                record = {"variant": variant, "open_error": f"{type(error).__name__}: {error}"}
                print(json.dumps(record), flush=True)
                print("The radar could not be opened; replug its USB cable and run again.")
                return 1
            results.append(record)
            print(json.dumps(record), flush=True)
            time.sleep(1.0)
    print("\nclose time (s) per variant:")
    for variant in args.variants:
        times = [r["close_s"] for r in results if r["variant"] == variant and "close_s" in r]
        print(f"  {variant:15s} {times}")
    print("\nNow run: dmesg -T | grep cp210x | tail -20")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
