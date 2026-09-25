#!/usr/bin/env python3
"""Build or validate a local, consent-bound community contribution package."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from openflight.contribution_package import (
    build_contribution_package,
    validate_contribution_package,
)


def main(argv=None) -> int:
    """Run the local package builder or verifier."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--export", type=Path, required=True)
    build.add_argument("--metadata", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("archive", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
            result = build_contribution_package(args.export, args.output, metadata)
        else:
            result = validate_contribution_package(args.archive)
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"contribution package failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
