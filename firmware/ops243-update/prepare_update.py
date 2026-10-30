#!/usr/bin/env python3
"""Prepare the pinned OPS243-A v1.3.3 image; never opens a hardware device."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.request import urlopen

BASE = 0x0C000000
SIZE = 0x100000
COMMIT = "efaceefb8fa547071cb925c0d20d2e38363ace67"
NAME = "OPS243_CW_v1.3.3_20261016_1230.hex"
HEX_SHA = "169a6c6c7ae14bcc7da3e7201e31c623149da65e9f3df7598da70e0ae3e9d81c"
URL = f"https://raw.githubusercontent.com/omnipresense/OPS243-A_Firmware/{COMMIT}/{NAME}"
FILES = {
    "ops243-before-update.bin": SIZE,
    "OPS243-v1.3.3.hex": None,
    "ops243-first64k.bin": 0x10000,
    "ops243-app.bin": 0x60000,
    "ops243-expected.bin": SIZE,
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def merge(original, raw):
    if len(original) != SIZE:
        raise ValueError("Backup must be exactly 1,048,576 bytes")
    if sha(raw) != HEX_SHA:
        raise ValueError("Firmware SHA-256 mismatch; do not use this file")
    image = bytearray(original)
    coverage = bytearray(SIZE)
    base = 0
    eof = False
    for line in raw.decode("ascii").splitlines():
        if not line.startswith(":"):
            raise ValueError("Invalid HEX line")
        r = bytes.fromhex(line[1:])
        if len(r) < 5 or len(r) != r[0] + 5 or sum(r) % 256:
            raise ValueError("Invalid HEX record length/checksum")
        n, kind = r[0], r[3]
        data = r[4 : 4 + n]
        if kind == 4 and n == 2:
            base = int.from_bytes(data, "big") << 16
        elif kind == 0:
            offset = base + int.from_bytes(r[1:3], "big") - BASE
            if offset < 0 or offset + n > SIZE:
                raise ValueError("HEX address outside flash")
            if any(coverage[offset : offset + n]):
                raise ValueError("Overlapping HEX records")
            image[offset : offset + n] = data
            coverage[offset : offset + n] = b"\x01" * n
        elif kind == 1 and n == 0:
            eof = True
            break
        elif kind != 5:
            raise ValueError(f"Unsupported HEX record {kind}")
    expected_coverage = bytearray(SIZE)
    expected_coverage[:0x2B4] = b"\x01" * 0x2B4
    expected_coverage[0x20000:0x65D7C] = b"\x01" * (0x65D7C - 0x20000)
    if not eof or coverage != expected_coverage:
        raise ValueError("Unexpected firmware address ranges")
    return bytes(image)


def outputs(original, raw):
    image = merge(original, raw)
    return {
        "OPS243-v1.3.3.hex": raw,
        "ops243-first64k.bin": image[:0x10000],
        "ops243-app.bin": image[0x20000:0x80000],
        "ops243-expected.bin": image,
    }


def check():
    data = {name: Path(name).read_bytes() for name in FILES}
    for name, size in FILES.items():
        if size is not None and len(data[name]) != size:
            raise ValueError(f"Wrong size: {name}")
    manifest = json.loads(Path("prepared-sha256.json").read_text())
    if manifest.get("commit") != COMMIT or manifest.get("firmware") != "1.3.3":
        raise ValueError("Wrong preparation manifest")
    for name, value in data.items():
        if sha(value) != manifest["files"].get(name):
            raise ValueError(f"File changed since preparation: {name}")
    rebuilt = outputs(data["ops243-before-update.bin"], data["OPS243-v1.3.3.hex"])
    for name, value in rebuilt.items():
        if data[name] != value:
            raise ValueError(f"Merged image mismatch: {name}")
    print("Preflight passed: backup, firmware, and merged images match.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Validate prepared files without downloading"
    )
    args = parser.parse_args()
    if args.check:
        check()
        return
    original = Path("ops243-before-update.bin").read_bytes()
    if len(original) != SIZE:
        raise ValueError("Backup must be exactly 1,048,576 bytes")
    raw = urlopen(URL, timeout=60).read()
    generated = outputs(original, raw)
    for name, value in generated.items():
        Path(name).write_bytes(value)
    manifest = {
        "firmware": "1.3.3",
        "commit": COMMIT,
        "files": {
            "ops243-before-update.bin": sha(original),
            **{name: sha(value) for name, value in generated.items()},
        },
    }
    Path("prepared-sha256.json").write_text(json.dumps(manifest, indent=2) + "\n")
    check()
    print("Prepared v1.3.3; original backup retained. No hardware was accessed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        sys.exit(1)
