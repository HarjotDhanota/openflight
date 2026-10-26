"""Guarded research-only RAM load; no persistent-flash fallback or erase option."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from firmware.flash_iwr6843 import open_serial  # noqa: E402
from openflight.iwr6843.bootloader import BootloaderError  # noqa: E402
from openflight.iwr6843.device_lock import IWR6843DeviceLock  # noqa: E402
from openflight.iwr6843.ram_clock import IWR6843RamClockLoader, validate_ram_image  # noqa: E402


def main(argv=None):
    """Require a reviewed image hash and physical preparation before opening UART."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--port", required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)
    try:
        image = args.image.read_bytes()
        if (
            not re.fullmatch(r"[0-9a-f]{64}", args.sha256)
            or hashlib.sha256(image).hexdigest() != args.sha256
        ):
            raise ValueError("RAM image does not match the reviewed SHA-256")
        validate_ram_image(image)
        print("Temporary RAM application only; no flash erase/write operation.")
        print(f"Image bytes: {len(image)}; SHA-256: {args.sha256}")
        print("Keep the existing functional-mode switch positions documented.")
        print("Set SOP2/S1.1 ON, with SOP1/S1.2 OFF and SOP0/S1.3 ON.")
        print("Leave other switches unchanged; do not press RESET until UART is open.")
        if input("Type READY to open the Enhanced UART: ").strip() != "READY":
            return 2
        lock = IWR6843DeviceLock(args.port)
        with lock:
            with open_serial(args.port) as handle:
                print("UART is open. Press RESET, wait one second, then type RAM.")
                if input("RAM: ").strip() != "RAM":
                    return 2
                loader = IWR6843RamClockLoader(handle)
                loader.load_ram(image)
        print("ROM acknowledged the RAM transfer; application startup is not yet verified.")
        print("Verify the expected clockProbe build/runtime before any timing experiment.")
        print("After testing, restore functional SOP2/S1.1 OFF and press RESET.")
        return 0
    except (OSError, ValueError, RuntimeError, BootloaderError) as error:
        print(
            f"RAM experiment failed: {type(error).__name__}; no flash fallback attempted.",
            file=sys.stderr,
        )
        print("Restore functional SOP2/S1.1 OFF and RESET to boot saved firmware.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
