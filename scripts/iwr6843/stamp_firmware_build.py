"""Stamp normalized local firmware sources and explicit feature flags; no binary identity claim."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def stamp(root: Path, output: Path, flags: str) -> dict:
    """Write the clock build ID and a manifest without machine-specific paths."""
    firmware = root / "firmware/iwr6843"
    paths = [root / "firmware/Makefile"]
    paths += sorted(
        path for path in firmware.iterdir() if path.suffix in {".c", ".h", ".cfg", ".cmd"}
    )
    paths.append(firmware / "makefile")
    hashes = {
        path.relative_to(root).as_posix(): hashlib.sha256(
            path.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for path in paths
        if path.name != "clock_probe_build_id.h"
    }
    record = {"schema": "openflight.firmware-source-id.v1", "flags": flags, "sources": hashes}
    fingerprint = hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()
    record["source_fingerprint"] = fingerprint
    output.mkdir(parents=True, exist_ok=True)
    (output / "clock_probe_build_id.h").write_text(
        f'#define L3_USB_CLOCK_BUILD_ID "{fingerprint}"\n', encoding="utf-8"
    )
    (output / "source-manifest.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--flags", required=True)
    args = parser.parse_args()
    print(stamp(args.root.resolve(), args.out, args.flags)["source_fingerprint"])


if __name__ == "__main__":
    main()
